# xteink-agent

Local tool to control a **Xteink X4 running the original (stock) firmware** from
Windows 10/11. The public command is **`xteinkcli`**.

It does not install CrossPoint, flash the device, modify the firmware, or talk to the
Xteink cloud. All traffic goes to the reader's local HTTP server or its Nordic UART
BLE channel, inside the home LAN.

> **Repository location:** clone or place the project in any directory. Examples use
> `C:\path\to\xteinkcli`. The Python distribution is named `xteink-agent`; the
> installed executable is `xteinkcli`.

## Current status

| Phase | Status |
| --- | --- |
| 1. Diagnostics and read-only operations (`doctor`, `status`, `list`, `wifi-info`, `discover`) | **Validated on a real X4** (XT V5.6.33) |
| 2. Transfer (`send`) with `/list` verification | **Validated on a real X4**, including overwrite and Unicode |
| 3. Library management (`mkdir`, `rename`, `move`, `download`, `delete`, `search`) | **Validated on a real X4** |
| 4. Book opening and BLE (`open`, `ble ...`) | Implemented from documentation; **not hardware-validated** |
| 5. Stable CLI, `--json`, exit codes, and agent documentation | Implemented |

Real tests on **2026-10-28** against a device at `<lab-ip>`, firmware **XT V5.6.33**:
Unicode and space-containing upload, collision rejection, overwrite, download with
matching SHA-256, create/rename/move/search, and deletion of the test artifacts. The
temporary folder was removed and the user's book remained untouched. `open` and BLE
data commands are still pending; the BLE scan failed with `WinError -2147020577`
(Bluetooth radio not ready). See [docs/VALIDATION.md](docs/VALIDATION.md) for the
detailed log.

**Outside File Transfer, the stock HTTP server stops.** This was tested on the real
device: after leaving the mode, `status` exited with code 6, ping failed, and TCP port
80 was closed. Re-enter File Transfer to use the HTTP commands again.

## Requirements

- Windows 10/11.
- Python 3.11 or compatible (3.12/3.13 should also work).
- X4 powered on, on the same Wi-Fi/LAN as the PC, and with **File Transfer** active
  for HTTP operations.
- Windows Bluetooth enabled for BLE commands.
- No Xteink account or Internet connection is required.

## Installation (PowerShell)

```powershell
cd C:\path\to\xteinkcli

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

xteinkcli --help
```

`-e ".[dev]"` installs the project in editable mode and adds `pytest`. For normal use
without tests, `python -m pip install -e .` is enough.

If the PowerShell execution policy blocks `Activate.ps1`, **do not change the system
policy**. Use the virtual environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m xteinkcli --help
```

The installed launcher is:

```text
C:\path\to\xteinkcli\.venv\Scripts\xteinkcli.exe
```

### Update

```powershell
cd C:\path\to\xteinkcli
.\.venv\Scripts\python.exe -m pip install --upgrade -e ".[dev]"
```

Do not install project dependencies with the global Python interpreter.

## Configuration

The X4 IP can be set, in order of precedence:

1. `--ip` on the command line.
2. `XTEINK_IP` environment variable.
3. `config.toml` in the current directory.
4. `%APPDATA%\xteinkcli\config.toml`.

Create the local configuration from the example:

```powershell
Copy-Item config.example.toml config.toml
notepad config.toml
```

`config.toml` is excluded by `.gitignore`. It contains no passwords: the tool does not
store them. If you want the configuration to work from any directory, copy it to
`%APPDATA%\xteinkcli\config.toml`.

You can also avoid the file:

```powershell
$env:XTEINK_IP = "192.168.1.50"
xteinkcli status
```

Or pass the IP on every invocation:

```powershell
xteinkcli --ip 192.168.1.50 status
xteinkcli status --ip 192.168.1.50
```

### Find the IP

On the X4, enter **File Transfer** and read the IP on screen. If you cannot see it, the
CLI can search the local subnet (read-only, private networks only):

```powershell
xteinkcli discover
xteinkcli discover --subnet 192.168.1.0/24 --json
```

The scan requests `GET /status` from each host and only accepts responses containing
several X4-specific fields.

## Usage

So an agent does not depend on an activated virtual environment, always invoke the
executable by its absolute path:

```powershell
$x = "C:\path\to\xteinkcli\.venv\Scripts\xteinkcli.exe"
& $x doctor
```

If the virtual environment is activated, `xteinkcli` works the same way.

### Diagnostics

```powershell
xteinkcli doctor
xteinkcli status
xteinkcli list /
xteinkcli list /Books
xteinkcli wifi-info
```

`doctor` checks Python, the virtual environment, configuration, `/status`, `/list`,
and, as a non-fatal warning, `/Read_staNameIp`. It performs no destructive operations.

### Send a book

```powershell
xteinkcli send "$env:USERPROFILE\Downloads\Book.epub"
xteinkcli send "C:\Books\One Hundred Years of Solitude.epub" --dest /Books
xteinkcli send "C:\Books\My Book.epub" --dest /Books --name "Another Name.epub"
```

Local names may contain spaces, accents, and Unicode characters. Only `.epub` files
are accepted by default; use `--allow-extension` to send another file type.

The flow:

1. Check that the file exists and that the server responds.
2. Check the destination directory and free space.
3. Upload with `POST /edit` (field `data`, `filename` = destination path).
4. **Do not trust HTTP 200**: list the directory again and compare the size.
5. Do not query `/status` during the upload.

If the destination already exists, the command fails with code 3. To replace it
explicitly:

```powershell
xteinkcli send "C:\Books\Book.epub" --dest /Books --overwrite --yes
```

Overwrite first uploads to a temporary name, verifies the size, deletes the original,
and renames. If the upload fails before the deletion, the original is preserved. The
replacement is not atomic in the stock firmware: an interruption between deletion and
rename would leave the new book in the temporary file. The CLI reports that path if it
happens.

### Library management

```powershell
xteinkcli mkdir /Books/Fiction
xteinkcli rename /Books/old.epub new.epub
xteinkcli move /Books/new.epub /Archive
xteinkcli download /Books/new.epub --out "$env:USERPROFILE\Downloads"
xteinkcli search "prince"
xteinkcli search "years" --dir /Books --max-depth 4
```

Deletion requires explicit confirmation. Without `--yes` on an interactive terminal,
the CLI asks for confirmation; in non-interactive/JSON mode it refuses:

```powershell
xteinkcli delete /Books/old.epub --yes
xteinkcli delete /Books/EmptyFolder --yes
```

Non-empty folders are not deleted and there is no recursive deletion.

### Open a book

```powershell
xteinkcli open /Books/Book.epub --yes
```

Uses `GET /fileOpen?path=...`, documented only through reverse engineering. The reader
should reboot and open the book. The CLI only confirms that the command was accepted;
it cannot verify the opening over HTTP and therefore returns `"verified": false`.

### BLE (experimental)

Requires Bluetooth enabled in Windows. The protocol is documented for
**XTOS V5.6.33** and has not been hardware-validated in this project.

```powershell
xteinkcli ble scan
xteinkcli ble status
xteinkcli ble id
xteinkcli ble nickname
xteinkcli ble nickname "My X4" --yes
xteinkcli ble wifi-scan
xteinkcli ble reading-time
xteinkcli ble open /Books/Book.epub --yes
```

Connect to a Wi-Fi network (the password is requested securely and is not stored by
the CLI; the reader stores it in its NVS):

```powershell
xteinkcli ble wifi-connect "MyNetwork" --yes
```

For agents, without typing the password on the command line:

```powershell
"the-password" | xteinkcli ble wifi-connect "MyNetwork" --password-stdin --yes
```

Do not use `--password` (it does not exist) or pass the password as an argument: it
would remain in PowerShell history. `ble status` always removes the `p` field
(password) from the response. BLE command `e=2`, which returns saved credentials, is
**not exposed**.

Enable/disable Wi-Fi:

```powershell
xteinkcli ble wifi-on --yes
xteinkcli ble wifi-off --yes
```

Select a specific device without scanning:

```powershell
xteinkcli ble status --address "AA:BB:CC:DD:EE:FF"
```

### JSON mode for agents

Add `--json` (before or after the subcommand):

```powershell
xteinkcli status --json
xteinkcli list /Books --json
xteinkcli send "C:\Books\Book.epub" --dest /Books --json
```

Success format:

```json
{
  "ok": true,
  "command": "status",
  "data": { "version": "XT V5.6.33", "free_bytes": 123, "...": "..." }
}
```

Error format (also on stdout, with a non-zero exit code):

```json
{
  "ok": false,
  "command": "send",
  "error": {
    "category": "conflict",
    "message": "Human-readable error message",
    "hint": "Actionable hint (when available)"
  }
}
```

> The CLI help, human-readable messages, JSON keys, error categories, and exit codes
> are all in English.

Exit codes:

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 1 | Unhandled generic error |
| 2 | Invalid usage, configuration, or argument |
| 3 | Conflict, not found, or existing destination |
| 4 | Device or protocol error |
| 5 | Explicit confirmation required (`--yes`) |
| 6 | Network error / timeout |
| 130 | Interrupted with Ctrl+C |

An agent must check both `ok` and the exit code, not just process completion. For
`send`, it must also check `data.verified == true`.

More patterns in [docs/AGENT_USAGE.md](docs/AGENT_USAGE.md).

## Security

- Use this tool only with readers you own or are authorized to control. The stock
  HTTP and BLE interfaces have no authentication.
- Only run `discover` on networks you own or control.
- Keep the X4 and the PC on the home LAN. **Do not** set up port forwarding or expose
  the reader's server to the Internet.
- The stock HTTP server has no documented authentication.
- Destructive operations (`delete`, `--overwrite`, `open`, Wi-Fi changes) require
  `--yes` or interactive confirmation.
- Wi-Fi passwords are never printed. `ble status` removes the `p` field; `e=2` is not
  implemented.
- Files, credentials, and data are not sent to the Xteink cloud.
- The firmware is never flashed or modified.
- SD-card paths are validated against traversal (`..`), Windows paths, and unsupported
  characters.

## Tests

Automated tests use the `.venv` Python and a simulated HTTP server; they do not need
the reader. The real phase 1-3 tests were performed separately against the X4 and are
documented in [docs/VALIDATION.md](docs/VALIDATION.md):

```powershell
cd C:\path\to\xteinkcli
.\.venv\Scripts\python.exe -m pytest -q
```

Current result: **69 tests passed**. The mock reproduces `/status` (both schemas),
`/list`, multipart upload, collisions, `PUT`/`DELETE /edit`, download,
`/Read_staNameIp`, `/fileOpen`, and HTTP 500 errors. BLE framing and password
redaction are also tested.

## Publishing

Publish through git so the ignored local files are not included:

```powershell
git add --dry-run .
git status --ignored
```

Do not upload a ZIP of the whole working directory: it would contain `config.toml`,
`.venv/`, caches, and generated metadata. Use `git archive` or explicitly exclude
those paths. The repository contains no credentials; `config.toml` is local-only and
must not be included in a release archive.

## Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `Could not connect to the X4` | File Transfer is not active, IP changed, different network, reader asleep | Power on the X4, enable File Transfer, check the IP on screen, run `xteinkcli discover` |
| PC on Ethernet and X4 on Wi-Fi | Router may isolate clients or use different subnets | Connect the PC to the same Wi-Fi or check the router's client isolation |
| Timeout in `status` | Wrong IP, firewall, slow Wi-Fi | Try `ping`, check the IP, increase `--timeout` |
| `HTTP 500` while uploading | No space, missing path, invalid name, or collision | Check `status`, `list`, and use `--overwrite --yes` only when appropriate |
| `HTTP 500` while deleting a folder | The folder is not empty | Empty it first; there is no recursive deletion |
| `status` does not respond during `send` | The stock firmware stops answering `/status` during transfer | Expected; the CLI does not query it then |
| Everything fails with timeout/network | File Transfer was exited | Re-enter File Transfer; the HTTP server and network stop when leaving |
| No X4 found over BLE | Bluetooth off, reader too far away, or BLE inactive | Enable Bluetooth, move the reader closer, check its screen |
| `The device is not ready for use` (BLE) | Windows Bluetooth adapter/radio is not operational | Enable Bluetooth in Windows and retry; this happened during this project's test |
| Garbled accents in the console | Windows PowerShell 5 code page | Use Windows Terminal/PowerShell 7 or `chcp 65001`; `--json` avoids the issue |

The X4 IP may change after reconnecting. Set a DHCP reservation on the router if you
want a stable address.

## Structure

```text
xteinkcli/
├── .venv/                     # virtual environment (git-ignored)
├── .gitignore
├── .gitattributes
├── LICENSE                    # MIT license
├── pyproject.toml             # xteink-agent package and xteinkcli script
├── config.example.toml        # example configuration, no secrets
├── src/xteinkcli/
│   ├── cli.py                 # commands, human/JSON output, exit codes
│   ├── client.py              # stock HTTP API client
│   ├── ble.py                 # NUS, framing, and BLE commands
│   ├── discover.py            # X4 discovery on the LAN
│   ├── remote_path.py         # SD-card path validation
│   ├── config.py              # configuration and environment variables
│   └── errors.py              # errors and exit codes
├── tests/
│   ├── mock_device.py         # simulated HTTP server
│   └── test_*.py
└── docs/
    ├── API_COMPATIBILITY.md   # API table, sources, and confidence
    ├── AGENT_USAGE.md         # agent patterns
    └── VALIDATION.md          # test log and pending items
```

## Documentation

- [API compatibility table and sources](docs/API_COMPATIBILITY.md)
- [Agent usage](docs/AGENT_USAGE.md)
- [Validation log and pending items](docs/VALIDATION.md)

## License and disclaimer

This project is released under the MIT License. See [LICENSE](LICENSE).

Xteink and X4 are trademarks of their respective owners. This project is not
affiliated with or endorsed by Xteink. The stock HTTP and BLE interfaces are
documented through public reverse engineering and may change without notice; the
software is provided without warranty. Review the vendor's terms before commercial
use. This is not legal advice.

## Out of scope

- Alternative firmware, flashing, OTA, or modifying the reader.
- The Xteink cloud/account.
- Recursive deletion.
- Full control of the stock UI (for example, enabling File Transfer in software)
  without a verified contract.
- A local MCP interface: not required for the CLI; possible future work.
