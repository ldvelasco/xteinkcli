# Validation log — 2026-10-28

This file distinguishes automated mock tests, tests of the installed executable, and
real hardware tests. HTTP phases 1-3 **were validated on the real reader**; book
opening and BLE remain pending.

## Environment

- Windows 10/11, PowerShell.
- `.venv` with Python **3.11.9**.
- Dependencies: `httpx 0.28.1`, `bleak 1.1.1`, `pytest 9.2.0`.
- Executable: `.venv\Scripts\xteinkcli.exe`.
- Real X4 at **<lab-ip>**, firmware **XT V5.6.33**, ESP32-C3, 14.9 GiB SD card.
- PC on Ethernet `<lab-pc-ip>`; PC Wi-Fi was disconnected, but the X4 was reachable
  over the LAN.
- Local `config.toml` (git-ignored) with `ip = "<lab-ip>"`; `xteinkcli doctor`
  works from the project root without `--ip`.

## Automated tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Result: **69 tests passed**.

Mock coverage:

- `/status` with both documented schemas;
- `/list` with Unicode files and folders;
- multipart upload with field `data` and `filename` = SD path;
- collision rejection and overwrite with temporary file + rename;
- verification failure after HTTP 200 (renamed file or wrong size);
- `PUT /edit` for mkdir/rename/move; `DELETE /edit` for file and empty folder;
- download size verification and `.part` cleanup;
- `/Read_staNameIp`, `/fileOpen`, HTTP 500 errors, and network timeouts;
- BLE framing (split header, 7-byte notifications, unframed JSON fallback);
- BLE password redaction;
- CLI exit codes and JSON output.

## Installed executable tests (mock)

| Command | Result |
| --- | --- |
| `--help` | exit 0; English help |
| `status --json` | exit 0; status and space |
| `list / --json` | exit 0; entries |
| `send "Cien años de soledad.epub" --dest /Books --json` | exit 0; `verified: true`; correct bytes |
| `download "/Libro ñ.epub" --out copia.epub --json` | exit 0; correct bytes |
| `search anos --json` | exit 0; accent-insensitive search |
| `doctor --json` | exit 0; all checks OK |
| `download` to an existing local file without `--overwrite` | exit 3; expected conflict |

## Real X4 tests

All commands used `--ip <lab-ip>`. Test artifacts were created in
`/xteinkcli-test` and deleted at the end. **The only book in `/Books` was not
modified.**

| Command | Actual result |
| --- | --- |
| `doctor` (no `--ip`, reading `config.toml`) | exit 0; `/status` in 48 ms, firmware `XT V5.6.33`, 14.9 GiB SD, 3 `/list` entries, Wi-Fi `<redacted-ssid>` |
| `doctor --ip <lab-ip> --timeout 5` | exit 0; `/status` in 37 ms |
| `status --json` | exit 0; id `<redacted-id>`, `total_bytes=16006235750`, `used_bytes=2333` |
| `list / --json` | exit 0; `Books`, `System Volume Information`, `XTCache` |
| `list /Books --json` | exit 0; one user EPUB, untouched |
| `discover --json` | exit 0; found `<lab-ip>` with `XT V5.6.33` |
| `mkdir /xteinkcli-test --json` | exit 0; folder created and verified |
| `send "Prueba ñandú.epub" --dest /xteinkcli-test --json` | exit 0; 1796 B, `verified: true`, Unicode path with spaces |
| Same `send` without `--overwrite` | exit 3; conflict, existing file unchanged |
| `send ... --overwrite --yes --json` (1887 B version) | exit 0; `overwritten: true`, `verified: true` |
| `download ... --json` | exit 0; 1887 B; local and remote SHA-256 `612A7608…E6A81` |
| `mkdir /xteinkcli-test/sub --json` | exit 0 |
| `rename ... "Prueba renombrada.epub" --json` | exit 0; source and destination verified |
| `move ... /xteinkcli-test/sub --json` | exit 0; verified |
| `search "renombrada" --dir /xteinkcli-test --json` | exit 0; found `/xteinkcli-test/sub/Prueba renombrada.epub` |
| `delete /xteinkcli-test --yes` with contents | exit 3; CLI refused to delete a non-empty folder |
| Delete file, `sub`, and `/xteinkcli-test` | exit 0 in all three cases; verified with `/list` |
| Final `list / --json` | only `Books`, `System Volume Information`, `XTCache` |
| Final `status --json` | `used_bytes=2333`, same as before the tests |

Real firmware observations:

- `/status` shape: `type: "SD"`, `isOk: "true"` (string), `version: "XT V5.6.33"`.
- `/list` returns directories with size `32768`.
- `PUT /edit` with `application/x-www-form-urlencoded` worked for create, rename, and
  move.
- `DELETE /edit` worked with `path` in both query and body; it was not isolated which
  parameter the firmware used.
- Unicode and space-containing uploads worked; `/list` verification and the download
  SHA-256 matched.
- Native collision auto-renaming was not observed because the CLI rejects an existing
  destination before uploading. Explicit overwrite with temporary file, deletion, and
  rename worked.

## Leaving File Transfer

With the reader outside File Transfer, `xteinkcli status --timeout 5` returned exit 6
(network timeout). Additional checks:

- `ping <lab-ip>`: destination host unreachable / request timed out.
- TCP port 80: `tcp80_connected=False`.

Conclusion: on this unit, leaving File Transfer **stops the HTTP server and network
connectivity**; the CLI cannot operate until the mode is entered again. Nothing on the
reader was modified during this check.

## Real BLE tests

`xteinkcli ble scan --json` was run several times and failed with exit 4:

```text
OSError: [WinError -2147020577] The device is not ready for use.
```

The `bthserv` service is running and the Intel adapter appears OK, but Windows/WinRT
does not allow scanning. No BLE command was sent to the reader.

## Pending hardware validation

1. `GET /fileOpen` and BLE `e=10` (both reboot the reader; not done without explicit
   confirmation).
2. BLE scan, status, Wi-Fi, nickname, and reading time.
3. Enabling the File Transfer UI in software (`e=13` only sets a flag; not confirmed).
4. Low-space, large-file, and interrupted-transfer behavior.
5. Real `/Read_staNameIp` text when Wi-Fi is unavailable.
6. Native auto-renaming on a collision that the pre-check misses.
7. Other firmware versions/regions.

## Conclusion

The HTTP diagnostics, transfer, and library operations are **validated on the real X4**
running `XT V5.6.33`. Book opening and the BLE channel are implemented from
documentation and remain pending real testing.
