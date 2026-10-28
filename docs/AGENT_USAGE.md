# Using xteinkcli from agents

The CLI is designed so an agent can invoke it **without inheriting** an activated
virtual environment from another terminal. Do not assume `xteinkcli` is on `PATH` or
that `.venv` is active.

## Robust invocation

Define the absolute path once:

```powershell
$X = "C:\path\to\xteinkcli\.venv\Scripts\xteinkcli.exe"
```

Then always use it:

```powershell
& $X doctor --json
& $X status --json
& $X list / --json
```

Alternative if the launcher does not exist (for example, after moving the repository):

```powershell
$X = "C:\path\to\xteinkcli\.venv\Scripts\python.exe"
& $X -m xteinkcli status --json
```

Do not use plain `python`: it may be the global interpreter without the dependencies.

## Configuration independent of the current directory

`config.toml` is searched in the current directory and in
`%APPDATA%\xteinkcli\config.toml`. An agent that changes directories should pass
`--config` or `--ip`:

```powershell
$env:XTEINK_IP = "192.168.1.50"
& $X status --json
```

or

```powershell
& $X --config "C:\path\to\xteinkcli\config.toml" status --json
```

`--ip` can appear before or after the subcommand.

## Reading results

`--json` writes **one JSON object to stdout** for both success and error. It does not
mix human messages into stdout. Exit codes:

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 1 | Generic error |
| 2 | Invalid usage/configuration |
| 3 | Conflict, not found, or existing destination |
| 4 | Device/protocol error |
| 5 | Missing confirmation (`--yes`) |
| 6 | Network error/timeout |

Recommended pattern in PowerShell:

```powershell
$raw = & $X status --json
$code = $LASTEXITCODE
$r = $raw | ConvertFrom-Json
if ($code -ne 0 -or -not $r.ok) {
    throw "xteinkcli failed ($code): $($r.error.message) / $($r.error.hint)"
}
$r.data
```

Do not interpret an HTTP 200 from the reader as operation success. The CLI already
verifies, but the agent must check `ok` and, for `send`, `data.verified`.

## Diagnostic flow

```powershell
& $X doctor --json
if ($LASTEXITCODE -ne 0) {
    & $X discover --json
}
```

`discover` only scans private IPv4 networks and queries `/status` (read-only). If it
returns an empty list, the X4 is off, on a different network, or File Transfer is not
active. Do not retry aggressively.

## Sending a book

1. Confirm the local file exists and is not empty.
2. Send with `--json`.
3. Require `ok == true` and `data.verified == true`.

```powershell
$file = "$env:USERPROFILE\Downloads\Book.epub"
$r = (& $X send $file --dest /Books --json) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $r.ok -or -not $r.data.verified) {
    throw "Could not verify the upload: $($r.error.message)"
}
$r.data.remote_path
```

Do not query `/status` again while the upload is running: the stock firmware may stop
responding. Let the CLI wait; its default timeout is 600 s and can be increased:

```powershell
& $X send $file --dest /Books --upload-timeout 1200 --json
```

If the destination exists, the CLI fails with code 3. **Do not add
`--overwrite --yes` automatically**: overwriting requires an explicit user
instruction.

## Destructive operations

`delete`, `--overwrite`, `open`, Wi-Fi changes, and changing the device name require
`--yes` (or interactive confirmation). An agent must not add `--yes` based on an
ambiguous instruction.

Before deleting or moving:

```powershell
& $X list "/Books" --json
& $X search "name" --dir /Books --json
```

After the user confirms the exact path:

```powershell
& $X delete "/Books/Book.epub" --yes --json
```

Do not try to delete `/`, paths containing `..`, or local Windows paths. The CLI
rejects them.

## Wi-Fi passwords

Do not pass the password as an argument: it would remain in history and in the process
list. Use `--password-stdin`:

```powershell
$secure = Read-Host "Wi-Fi password" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $plain | & $X ble wifi-connect "MyNetwork" --password-stdin --yes --json
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    Remove-Variable plain -ErrorAction SilentlyContinue
}
```

The CLI does not store or print the password, and `ble status` removes the `p` field
from the response. The BLE command that reads saved credentials (`e=2`) is not exposed.

## BLE

BLE is experimental and was not hardware-validated. Check that Windows Bluetooth is
enabled first:

```powershell
Get-PnpDevice -Class Bluetooth | Select-Object Status, FriendlyName
```

Commands:

```powershell
& $X ble scan --json
& $X ble status --json
& $X ble wifi-scan --json
& $X ble reading-time --json
& $X ble nickname --json
```

If several `XTEPD_BLE_*` devices are present, select one:

```powershell
& $X ble status --address "AA:BB:CC:DD:EE:FF" --json
& $X ble status --device-name "XTEPD_BLE_A1B2" --json
```

Do not run `ble open` or Wi-Fi changes without explicit confirmation: they reboot or
disconnect the reader.

## Concurrency and timeouts

The stock X4 is an ESP32-C3 with little RAM and a simple HTTP server. **Do not run two
HTTP operations at once** (upload, listing, deletion). Wait for one to finish before
starting another.

- Fast operations: `--timeout` (10 s by default).
- Upload/download: `--upload-timeout` (600 s by default).
- BLE: the CLI waits at least 15 s for a response.

If a timeout occurs, do not blindly repeat an upload: list the directory and check
whether the file is complete.

## Security

- Use only readers you own or are authorized to control.
- Run `discover` only on networks you own or control.
- Do not expose the X4 to the Internet or configure port forwarding.
- Do not send data to the Xteink cloud; the CLI does not.
- Do not flash or modify the firmware.
- Do not log passwords or include them in JSON responses.
- Do not use `--yes` without an explicit user request.
