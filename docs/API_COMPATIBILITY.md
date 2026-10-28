# API compatibility table — Xteink X4 stock

Last review: **2026-10-28**. The firmware was not flashed or modified.

## Versions and sources

| Source | Contribution | Observed version | Date | Confidence |
| --- | --- | --- | --- | --- |
| [Companion App Protocol (pablohc/esp32-reader-re-workflow)](https://github.com/pablohc/esp32-reader-re-workflow/blob/main/devices/Xteink-X4-Stock/findings/software/companion_app_protocol.md) | Registered HTTP routes, `/status` shape, multipart upload, `/fileOpen`, full BLE NUS protocol | XTOS V5.6.33 | reviewed 2026-10-28 | High for static reverse engineering; not a live test |
| [XTEINK X4 Stock ROM WebAPI spec](https://note.com/ample_zephyr2723/n/n16e0cc3feee2) | Real HTTP traffic capture: `/status`, `/list`, `/edit`, download, errors, and limitations | XT V5.6.33 (global) | 2026-07-14 | High for the routes tested by the author; does not cover BLE or `/fileOpen` |
| [capKOMO/xtelink](https://github.com/capKOMO/xtelink/blob/main/src/client.py) | Corroborates `GET /list`, `POST /edit` with `data`, `DELETE` with a body, and urlencoded `PUT` with `path`/`src` | community file manager for X4/X3 | reviewed 2026-10-28 | Medium; third-party implementation, not official documentation |
| This repository's simulated server | Reproducible contract for unit tests | n/a | 2026-10-28 | High as a mock only; does not prove real compatibility |

> **Version warning:** routes and fields may change across global/Chinese firmware,
> X3, and X4. The tool tolerates the two documented `/status` variants, but not every
> stock version should be assumed identical. Before a destructive operation, inspect
> `list` and verify the real result.

## Verification status in this project

- Tested with the **simulated HTTP server** and unit tests: yes (69 tests).
- Tested against a **real X4**: **yes for the HTTP operations in phases 1-3**.
  Device at `<lab-ip>`, firmware **XT V5.6.33**, 14.9 GiB SD card. Verified:
  `status`, `list`, `Read_staNameIp`, multipart upload, collision, overwrite, download
  with SHA-256 comparison, `mkdir`, `rename`, `move`, and deletion of a file/empty
  folder.
- **Not hardware-validated:** `GET /fileOpen`, BLE (scan failed with
  `WinError -2147020577`, Bluetooth radio not ready), enabling File Transfer in
  software, and low-space behavior.
- Test data was created in `/xteinkcli-test` and deleted at the end; the existing book
  in `/Books` was not modified.
- **Leaving File Transfer was tested:** the HTTP server stops responding and the host
  is not even reachable by ping; `status` returns exit 6. Re-enter File Transfer to use
  the CLI.

## HTTP (main surface)

Reference version for this table: **XT V5.6.33 / XTOS V5.6.33**. The HTTP routes come
from the note's capture and the reverse engineering; BLE routes come only from the
reverse engineering. Compatibility with other versions is not assumed.

| Operation | Method and route | Request | Expected response | Source | Confidence | Status in `xteinkcli` |
| --- | --- | --- | --- | --- | --- | --- |
| Status and free space | `GET /status` | — | JSON with `type`, `isOk`/`sd`, `id`, `device_type`, `version`, `totalBytes`, `usedBytes` | Note + RE | High; schemas differ | Implemented; **hardware-validated** (XT V5.6.33, `isOk: "true"`, 14.9 GiB) |
| List directory | `GET /list?dir=/Books` | query `dir` absolute | Array of `{type: dir\|file, size: "bytes", name: "..."}` | Note + RE + xtelink | High | Implemented; **hardware-validated** (including Unicode and spaces) |
| Upload file | `POST /edit` | `multipart/form-data`, field `data`; `filename` = destination SD path | HTTP 200, empty body | Note + RE + xtelink | High for the field/name; collision behavior debated | Implemented (`send`); **hardware-validated**: 1796/1887 B, Unicode, collision, overwrite, `/list` verification |
| Create folder | `PUT /edit` | `application/x-www-form-urlencoded`, `path=/path/` (trailing slash) | HTTP 200; sometimes returns the parent path | Note + xtelink | Medium-high | Implemented; **hardware-validated** (`path` with trailing slash and verification) |
| Rename | `PUT /edit` | `path=/path/new`, `src=/path/old` | HTTP 200 | Note + RE + xtelink | Medium-high | Implemented; **hardware-validated**, verifies source and destination |
| Move | `PUT /edit` | `path=/dest/file`, `src=/source/file` | HTTP 200 (internally `rename()`) | Note + RE | Medium-high | Implemented; **hardware-validated** |
| Delete file | `DELETE /edit?path=/path` | `path` in query (note) or body (xtelink) | HTTP 200 | Note + xtelink | Medium-high | Implemented; **hardware-validated** (sends query and body; requires `--yes`) |
| Delete empty folder | `DELETE /edit?path=/path/` | `path` with trailing slash | HTTP 200; HTTP 500 if not empty | Note | Medium-high | Implemented; **hardware-validated** for empty folder; CLI rejects non-empty first |
| Download | `GET /<path>?download=true` | query `download` | `application/octet-stream`, `Content-Length` | Note | High | Implemented; **hardware-validated**, local and remote SHA-256 matched |
| Read file | `GET /<path>` | — | Contents; `Content-Type: text/plain` fixed in the note | Note | High | Not used; `?download=true` is preferred |
| SSID/IP in STA mode | `GET /Read_staNameIp` | — | text `<ssid> <ipv4>`; failure text when disconnected | RE | Medium | Implemented; **hardware-validated** (`<redacted-ssid>`, `<lab-ip>`) |
| Open book | `GET /fileOpen?path=...` | query `path` | Validates, opens, and reboots after responding | **RE only**; not in the note | Medium-low | Implemented (`open`), requires `--yes`, reports `verified: false`; **not tested** |
| Fast status | `GET /statusFast` | — | JSON not documented in detail | RE | Low | Not implemented |
| Provision Wi-Fi over web | `POST /Wifi` | form `ssid` + `password` | text; saves credentials | RE | Medium | Not implemented; BLE is preferred and secrets are not logged |
| Scan Wi-Fi over web | `GET /Read_scanWifi` | — | undocumented body | RE | Low | Not implemented; BLE `e=0` is used |
| Change run mode | `PUT /setRunMode` | fields not confirmed | unknown | RE | Low | Not implemented |
| Restart in AP mode | `PUT /restartAPMode` | — | unknown | RE | Low | Not implemented |
| System/font/wallpaper settings | `GET/PUT /systemSet`, `/setFont`, `/setWallpaper` | fields not confirmed | unknown | RE | Low | Not implemented |
| Management UI | `GET /system/manager.htm` | — | FSBrowser HTML/JS | Note + RE | High | Not needed by the CLI |

### Discrepancies handled by the implementation

1. **`/status`**: the note capture returns `type: "SD"`, `isOk: "true"` (string), and
   `version: "XT V5.6.33"`; the reverse engineering describes `sd: true|false` and
   `version: "XTOS V5.6.33"`. `XteinkClient.get_status()` accepts both and calculates
   `free_bytes`.
2. **Upload collision**: the note says an existing file is overwritten; the reverse
   engineering says stock renames it to `name_<millis>.ext`. The CLI **rejects** an
   existing destination by default. With `--overwrite --yes` it uploads to a temporary
   name, verifies size, deletes the original, and renames. **Hardware-tested:** rejection
   and overwrite worked. Native auto-renaming was not observed because the CLI avoids
   the collision before uploading.
3. **`PUT /edit`**: the note labels it `multipart/form-data`, but the body it shows
   (`path=...&src=...`) is urlencoded and the community manager uses urlencoded. The CLI
   uses `application/x-www-form-urlencoded`; **hardware-tested** for `mkdir`, `rename`,
   and `move`.
4. **`DELETE /edit`**: the note uses query; the community manager uses body. The CLI
   sends `path` in both. **Hardware-tested** for a file and an empty folder; it was not
   isolated which parameter the firmware consumed.
5. **`/fileOpen`**: only present in the reverse engineering. Do not assume it exists in
   every version. The CLI does not claim the book opened: it only reports that the
   firmware accepted the command and a reboot is expected. **Not hardware-tested** to
   avoid rebooting the reader without confirmation.
6. **`/status` during upload**: the note observes it stops responding while `POST
   /edit` is running. The CLI queries space at most **before** uploading and does not
   query `/status` during or after the transfer (it uses `/list` to verify). A slow
   upload was not forced to observe the live firmware behavior.

## BLE (Nordic UART)

Service `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`; write
`6E400002-B5A3-F393-E0A9-E50E24DCCA9E`; notify
`6E400003-B5A3-F393-E0A9-E50E24DCCA9E`. The device advertises as
`XTEPD_BLE_<XXXX>`. There is no pairing or encryption in the documented NUS channel.

Response: a 5-byte header `[0x03, 0x02, 0x03, lenHi, lenLo]` followed by JSON,
delivered in `MTU - 3` byte notifications. The client reassembles the header and JSON.
Request: a JSON object `{"e": <command>, ...}` of up to 512 bytes.

Documented version: **XTOS V5.6.33**. Confidence: high for the static reverse
engineering; **not hardware-validated** in this project (the BLE scan failed before
connecting).

| `e` | Operation | Request | Documented response | Status |
| ---: | --- | --- | --- | --- |
| 0 | Scan Wi-Fi | `{"e":0}` | `{"e":0,"l":[{"s":"ssid","r":rssi},...]}` | Implemented (`ble wifi-scan`) |
| 1 | Connect Wi-Fi and save | `{"e":1,"s":"ssid","p":"password"}` | `{"s":0}` ok / `{"s":1}` fail | Implemented (`ble wifi-connect`); password via stdin/getpass |
| 2 | Read saved credentials | `{"e":2}` | `{"s":"ssid","p":"password"}` in plaintext | **Intentionally not implemented** |
| 3 | Device ID | `{"e":3}` | `{"id":"..."}` | Implemented (`ble id`) |
| 4 | List files | `{"e":4,"d":"/dir"}` | `{"s":0,"d":"...","l":[{"t":"dir\|file","z":n,"n":"..."}]}` | Not implemented over BLE; HTTP `/list` is used |
| 5 | Rename | `{"e":5,"o":"...","n":"..."}` | `{"s":0/1/2/3}` | Not implemented over BLE; HTTP is used |
| 6 | Delete | `{"e":6,"p":"..."}` | `{"s":0/1/2/3}` | Not implemented over BLE; HTTP with `--yes` is used |
| 7 | Create folder | `{"e":7,"p":"..."}` | `{"s":0/1/2}` | Not implemented over BLE; HTTP is used |
| 8 | Move | `{"e":8,"s":"...","d":"..."}` | `{"s":0/1/2/3}` | Not implemented over BLE; HTTP is used |
| 9 | Status | `{"e":9}` | `{"e":9,"s":"ssid","p":"pw","ui":n,"on":bool,"wm":n,"ws":n,"ok":bool,"ing":bool,"fail":bool}` | Implemented (`ble status`); `p` is always removed |
| 10 | Open book | `{"e":10,"p":"..."}` | `{"e":10,"s":0/1/2/3/4}`; reboots after replying | Implemented (`ble open`); requires `--yes`; not validated |
| 11 | Reading time | `{"e":11}` | `{"e":11,"s":0,"l":[{"n":"book","t":minutes},...]}` | Implemented (`ble reading-time`) |
| 12 | Reset reading time | `{"e":12,"t":"...","n":"..."}` | `{"s":0/1/2/3}` | Not implemented; destructive and outside the initial goal |
| 13 | Prepare transfer | `{"e":13}` | `{"s":0}` (sets a flag) | Not implemented; not confirmed to enable the File Transfer UI |
| 14 | Enable/disable Wi-Fi | `{"e":14,"on":bool}` | `{"s":0,"on":bool,"wm":n}` | Implemented (`ble wifi-on`, `ble wifi-off`) |
| 15 | Set name | `{"e":15,"n":"..."}` | `{"s":0,"n":"..."}` | Implemented (`ble nickname NAME`) |
| 16 | Get name | `{"e":16}` | `{"s":0,"n":"..."}` | Implemented (`ble nickname`) |

Common `s` codes: `0` ok, `1` no SD/error, `2` not found or invalid parameter, `3`
operation failed, `4` unsupported file type (for `e=10`). The CLI translates the code
into a message.

### Known BLE limitations

- The protocol is documented for **XTOS V5.6.33**; there is no guarantee for other
  versions or the Chinese firmware.
- The channel is unauthenticated and unencrypted. Any central in range can use it.
  Keep the reader on a trusted LAN and do not expose secrets through it.
- `e=2` returns the saved Wi-Fi password; the CLI **does not expose it**.
- The Bluetooth adapter must be operational. In this project's test, Windows returned
  `WinError -2147020577` (The device is not ready for use), preventing a real scan.
- Opening books over BLE (`e=10`) and over HTTP (`/fileOpen`) could not be verified on
  hardware.

## Firmware features not implemented

Not implemented because there is no confirmed contract or because they are outside the
goal:

- enabling the File Transfer UI in software (only the `e=13` flag is documented);
- Wi-Fi scanning over HTTP (`/Read_scanWifi`), font/wallpaper settings,
  `setRunMode`, `restartAPMode`, `statusFast`;
- reading or resetting the reading-time history (`e=12`);
- any cloud, account, OTA, or firmware modification feature.

## Security references

- [Security and Sensitive Data — pablohc](https://github.com/pablohc/esp32-reader-re-workflow/blob/main/devices/Xteink-X4-Stock/findings/software/security_sensitive_data.md)
- Use the tool only with readers you own or are authorized to control.
- The X4 HTTP server has no documented authentication. Do not publish it on the
  Internet or forward ports to it.
- `xteinkcli` does not send files or credentials to cloud services.
