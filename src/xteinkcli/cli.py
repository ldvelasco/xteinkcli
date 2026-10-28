"""Command-line interface for xteinkcli."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import traceback
import unicodedata
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .ble import BleSession, run_ble, scan_devices
from .client import XteinkClient
from .config import (
    DEFAULT_BLE_PREFIX,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DEFAULT_UPLOAD_TIMEOUT,
    AppConfig,
    load_config,
)
from .discover import parse_private_subnet, scan_subnet
from .errors import (
    EXIT_OK,
    EXIT_USAGE,
    ConfirmationRequired,
    ConflictError,
    DeviceError,
    NotFoundError,
    ProtocolError,
    UsageError,
    WifiNotConnected,
    XteinkError,
)
from .remote_path import (
    basename_remote,
    ensure_dir_argument,
    join_remote,
    normalize_remote_path,
    parent_remote,
    validate_remote_name,
)

EPILOG = """\
Examples:
  xteinkcli doctor
  xteinkcli status
  xteinkcli list /
  xteinkcli send "$env:USERPROFILE\\Downloads\\Book.epub"
  xteinkcli send "C:\\Books\\My Book.epub" --dest /Books --json

The IP can be set with --ip, the XTEINK_IP environment variable, or config.toml.
Wi-Fi passwords are never displayed. Destructive operations require --yes.
"""


class CliParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message, hint="Use `xteinkcli --help` for help.")


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ip",
        default=argparse.SUPPRESS,
        help="X4 IP or host (overrides XTEINK_IP and config.toml).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=argparse.SUPPRESS,
        help=f"X4 HTTP port (default {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=argparse.SUPPRESS,
        help=f"Timeout for quick operations in seconds (default {DEFAULT_TIMEOUT:g}).",
    )
    parser.add_argument(
        "--upload-timeout",
        type=float,
        default=argparse.SUPPRESS,
        help=(
            "Upload/download timeout in seconds "
            f"(default {DEFAULT_UPLOAD_TIMEOUT:g})."
        ),
    )
    parser.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="Path to an alternative config.toml.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Structured JSON output (agent-friendly).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Show unexpected error tracebacks.",
    )


def _add_ble_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--address",
        default=None,
        help="X4 BLE address; skips scanning.",
    )
    parser.add_argument(
        "--device-name",
        default=None,
        help="Exact or partial BLE name (for example XTEPD_BLE_A1B2).",
    )
    parser.add_argument(
        "--scan-timeout",
        type=float,
        default=8.0,
        help="BLE scan seconds (default 8).",
    )


def build_parser() -> CliParser:
    parser = CliParser(
        prog="xteinkcli",
        description=(
            "Local control of an Xteink X4 reader running stock firmware. "
            "It does not flash, modify the firmware, or use the Xteink cloud."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(
        ip=None,
        port=None,
        timeout=None,
        upload_timeout=None,
        config=None,
        json=False,
        debug=False,
        command=None,
    )
    parser.add_argument("--version", action="version", version=f"xteinkcli {__version__}")
    _add_common(parser)

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser(
        "doctor",
        help="Read-only diagnostics: Python, configuration, HTTP, and SD card.",
    )
    _add_common(p)

    p = sub.add_parser(
        "discover",
        help="Find the X4 via /status on the local network (read-only).",
    )
    _add_common(p)
    p.add_argument(
        "--subnet",
        default=None,
        help="Subnet to scan, for example 192.168.1.0/24 (default: local /24).",
    )
    p.add_argument("--workers", type=int, default=64, help="Concurrent probes.")
    p.add_argument(
        "--probe-timeout",
        type=float,
        default=0.4,
        help="Per-host timeout in seconds (default 0.4).",
    )

    p = sub.add_parser("status", help="Show status, firmware, and free space.")
    _add_common(p)

    p = sub.add_parser("list", help="List an SD-card directory (default /).")
    _add_common(p)
    p.add_argument("path", nargs="?", default="/", help="Absolute SD-card directory.")

    p = sub.add_parser("wifi-info", help="Show SSID and IP when the X4 is a Wi-Fi client.")
    _add_common(p)

    p = sub.add_parser("send", help="Upload an EPUB and verify the result.")
    _add_common(p)
    p.add_argument("file", help="Local file path (spaces and Unicode supported).")
    p.add_argument("--dest", default="/", help="Destination SD-card directory (default /).")
    p.add_argument("--name", default=None, help="Remote name different from the local name.")
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing file (requires --yes or confirmation).",
    )
    p.add_argument(
        "--allow-extension",
        action="store_true",
        help="Allow extensions other than .epub.",
    )
    p.add_argument("--yes", action="store_true", help="Confirm overwrite without asking.")

    p = sub.add_parser("mkdir", help="Create an SD-card folder.")
    _add_common(p)
    p.add_argument("path", help="Absolute folder to create, for example /Books/Fiction.")

    p = sub.add_parser("rename", help="Rename a file or folder within its directory.")
    _add_common(p)
    p.add_argument("source", help="Absolute source path.")
    p.add_argument("new_name", help="New name, without slashes.")
    p.add_argument("--yes", action="store_true", help="Currently unused; reserved.")

    p = sub.add_parser("move", help="Move a file or folder to another directory.")
    _add_common(p)
    p.add_argument("source", help="Absolute source path.")
    p.add_argument("dest", help="Existing destination directory.")
    p.add_argument("--yes", action="store_true", help="Currently unused; reserved.")

    p = sub.add_parser("download", help="Download a copy of a book to the PC.")
    _add_common(p)
    p.add_argument("path", help="Absolute file path on the SD card.")
    p.add_argument("--out", default=None, help="Local destination file or directory.")
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing the local file (requires --yes or confirmation).",
    )
    p.add_argument("--yes", action="store_true", help="Confirm local overwrite.")

    p = sub.add_parser("delete", help="Delete a file or an empty folder.")
    _add_common(p)
    p.add_argument("path", help="Absolute path to delete.")
    p.add_argument("--yes", action="store_true", help="Confirm deletion without asking.")

    p = sub.add_parser("search", help="Search files or folders by name.")
    _add_common(p)
    p.add_argument("query", help="Text to search (case- and accent-insensitive).")
    p.add_argument("--dir", default="/", help="Directory to start from (default /).")
    p.add_argument("--max-depth", type=int, default=6, help="Maximum depth (0-20).")
    p.add_argument("--limit", type=int, default=200, help="Maximum results (1-1000).")

    p = sub.add_parser(
        "open",
        help="Ask the stock X4 to open a book via /fileOpen (reboots).",
    )
    _add_common(p)
    p.add_argument("path", help="Absolute book path on the SD card.")
    p.add_argument("--yes", action="store_true", help="Confirm opening/reboot.")

    ble = sub.add_parser(
        "ble",
        help="BLE commands from the documented protocol (experimental, not hardware-validated).",
    )
    _add_common(ble)
    ble_sub = ble.add_subparsers(dest="ble_command", metavar="SUBCOMMAND")

    p = ble_sub.add_parser("scan", help="Scan for XTEPD_BLE_* devices.")
    _add_common(p)
    p.add_argument("--scan-timeout", type=float, default=8.0, help="Scan seconds.")

    p = ble_sub.add_parser("id", help="Get the device ID (e=3).")
    _add_common(p)
    _add_ble_target(p)

    p = ble_sub.add_parser("status", help="Get BLE status (e=9), without passwords.")
    _add_common(p)
    _add_ble_target(p)

    p = ble_sub.add_parser("wifi-scan", help="Scan Wi-Fi networks (e=0).")
    _add_common(p)
    _add_ble_target(p)

    p = ble_sub.add_parser("wifi-connect", help="Connect to and save a Wi-Fi network (e=1).")
    _add_common(p)
    _add_ble_target(p)
    p.add_argument("ssid", help="Wi-Fi network name.")
    p.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin (recommended for agents).",
    )
    p.add_argument("--yes", action="store_true", help="Confirm the Wi-Fi change.")

    p = ble_sub.add_parser("wifi-on", help="Enable Wi-Fi on the reader (e=14).")
    _add_common(p)
    _add_ble_target(p)
    p.add_argument("--yes", action="store_true", help="Confirm the change.")

    p = ble_sub.add_parser("wifi-off", help="Disable Wi-Fi on the reader (e=14).")
    _add_common(p)
    _add_ble_target(p)
    p.add_argument("--yes", action="store_true", help="Confirm the change.")

    p = ble_sub.add_parser("nickname", help="Read (e=16) or set (e=15) the reader name.")
    _add_common(p)
    _add_ble_target(p)
    p.add_argument("nickname", nargs="?", default=None, help="New name; omit it to read.")
    p.add_argument("--yes", action="store_true", help="Confirm the name change.")

    p = ble_sub.add_parser("reading-time", help="Get reading times (e=11).")
    _add_common(p)
    _add_ble_target(p)

    p = ble_sub.add_parser("open", help="Open a book over BLE (e=10; reboots).")
    _add_common(p)
    _add_ble_target(p)
    p.add_argument("path", help="Absolute book path on the SD card.")
    p.add_argument("--yes", action="store_true", help="Confirm opening/reboot.")

    return parser


# ----------------------------------------------------------------------
# Output and utilities
# ----------------------------------------------------------------------
def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _emit_success(
    command: str | None, data: dict[str, Any], lines: list[str], *, json_mode: bool
) -> int:
    if json_mode:
        _print_json({"ok": True, "command": command, "data": data})
    else:
        if lines:
            print("\n".join(lines))
    return EXIT_OK


def _report_error(
    exc: XteinkError, *, command: str | None, json_mode: bool
) -> int:
    if json_mode:
        _print_json(
            {
                "ok": False,
                "command": command,
                "error": exc.to_dict(),
            }
        )
    else:
        print(f"Error: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"Hint: {exc.hint}", file=sys.stderr)
    return exc.exit_code


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} B"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _yes_no(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def _confirm(flag: bool, action: str, *, json_mode: bool) -> None:
    if flag:
        return
    if json_mode or not sys.stdin.isatty():
        raise ConfirmationRequired(
            f"Confirmation required for: {action}.",
            hint="Run again with --yes if you are sure.",
        )
    answer = input(f"{action}\nType 'y' to continue: ").strip().casefold()
    if answer not in {"y", "yes"}:
        raise ConfirmationRequired("Operation cancelled by the user.")


def _settings(args: argparse.Namespace) -> AppConfig:
    config = load_config(getattr(args, "config", None))
    return config.with_overrides(
        ip=getattr(args, "ip", None),
        port=getattr(args, "port", None),
        timeout=getattr(args, "timeout", None),
        upload_timeout=getattr(args, "upload_timeout", None),
    )


def _client(config: AppConfig) -> XteinkClient:
    return XteinkClient(
        config.base_url,
        timeout=config.timeout,
        upload_timeout=config.upload_timeout,
    )


def _find_entry(entries: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for entry in entries:
        if entry["name"] == name:
            return entry
    return None


# ----------------------------------------------------------------------
# HTTP commands
# ----------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace, config: AppConfig) -> int:
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 11):
        add("Python", "ok", f"{version} ({sys.executable})")
    else:
        add("Python", "fail", f"{version}; requires >= 3.11")

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        add("Virtual environment", "ok", f"active: {sys.prefix}")
    else:
        add(
            "Virtual environment",
            "warn",
            "no active .venv detected; use .\\.venv\\Scripts\\python.exe",
        )

    if config.config_path:
        config_detail = f"file: {config.config_path}"
    elif config.ip:
        config_detail = f"no config.toml; IP from {config.ip_source}"
    else:
        config_detail = "no config.toml"
    add("Configuration", "ok" if config.ip else "fail", config_detail)

    if not config.ip:
        add(
            "X4 IP",
            "fail",
            "not configured; use --ip, XTEINK_IP, config.toml, or run discover",
        )
        data = {"checks": checks, "ok": False}
        labels = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
        lines = [
            f"[{labels.get(c['status'], c['status'])}] {c['name']}: {c['detail']}"
            for c in checks
        ]
        lines.extend(
            [
                "",
                "Cannot check the X4 without an IP.",
                "Hint: run `xteinkcli discover` or set XTEINK_IP.",
            ]
        )
        if args.json:
            _print_json(
                {
                    "ok": False,
                    "command": "doctor",
                    "error": {
                        "category": "diagnosis",
                        "message": "No X4 IP is configured.",
                        "hint": (
                            "Run `xteinkcli discover` or set XTEINK_IP / "
                            "config.toml."
                        ),
                    },
                    "data": data,
                }
            )
        else:
            print("\n".join(lines))
        return EXIT_USAGE

    add("X4 IP", "ok", f"{config.ip}:{config.port} (source: {config.ip_source})")

    client = _client(config)
    first_error: XteinkError | None = None
    started = time.perf_counter()
    try:
        status = client.get_status()
        elapsed = (time.perf_counter() - started) * 1000
        add(
            "HTTP /status",
            "ok",
            f"{config.ip}:{config.port} in {elapsed:.0f} ms; "
            f"firmware {status.get('version') or 'unknown'}",
        )
        sd = status.get("sd_present")
        if sd:
            add(
                "SD card",
                "ok",
                f"{_format_bytes(status.get('used_bytes'))} used of "
                f"{_format_bytes(status.get('total_bytes'))}; "
                f"free {_format_bytes(status.get('free_bytes'))}",
            )
        else:
            add("SD card", "warn", "firmware does not report it as mounted")
    except XteinkError as exc:
        first_error = first_error or exc
        add("HTTP /status", "fail", exc.message)

    try:
        entries = client.list_dir("/")
        add("HTTP /list", "ok", f"{len(entries)} entries in /")
    except XteinkError as exc:
        first_error = first_error or exc
        add("HTTP /list", "fail", exc.message)

    try:
        info = client.wifi_info()
        add("Wi-Fi STA", "ok", f"SSID {info['ssid']!r}, IP {info['ip']}")
    except WifiNotConnected as exc:
        add("Wi-Fi STA", "warn", exc.message)
    except XteinkError as exc:
        add("Wi-Fi STA", "warn", f"could not query: {exc.message}")
    finally:
        client.close()

    failures = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]
    ok = not failures
    data = {
        "checks": checks,
        "ok": ok,
        "failures": len(failures),
        "warnings": len(warnings),
    }
    exit_code = EXIT_OK
    if not ok:
        exit_code = first_error.exit_code if first_error is not None else EXIT_DEVICE
    if args.json:
        if ok:
            _print_json({"ok": True, "command": "doctor", "data": data})
        else:
            _print_json(
                {
                    "ok": False,
                    "command": "doctor",
                    "error": {
                        "category": "diagnosis",
                        "message": "Diagnostics found failures.",
                        "hint": (
                            "Review the checks with status 'fail' in data.checks. "
                            "Check the IP, File Transfer, and the Wi-Fi network."
                        ),
                    },
                    "data": data,
                }
            )
        return exit_code

    labels = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
    lines = [f"[{labels.get(c['status'], c['status'])}] {c['name']}: {c['detail']}" for c in checks]
    lines.append("")
    if ok:
        lines.append("Diagnostics OK: the X4 responds to the read-only routes.")
    else:
        lines.append("Diagnostics found failures: check the IP, File Transfer, and Wi-Fi.")
    print("\n".join(lines))
    return exit_code


def cmd_discover(args: argparse.Namespace, config: AppConfig) -> int:
    network = parse_private_subnet(args.subnet)
    if not args.json:
        print(
            f"Scanning {network} for the X4 /status "
            f"({network.num_addresses - 2} hosts)...",
            file=sys.stderr,
        )
    found = scan_subnet(
        network,
        port=config.port,
        timeout=args.probe_timeout,
        workers=args.workers,
    )
    data = {"subnet": str(network), "port": config.port, "devices": found}
    if args.json:
        return _emit_success("discover", data, [], json_mode=True)
    if not found:
        print("No X4 with File Transfer active was found on that subnet.")
        print(
            "Check that the reader is powered on, on the same Wi-Fi, and has "
            "File Transfer active."
        )
        return EXIT_OK
    lines = []
    for device in found:
        lines.append(
            f"X4 found at {device['ip']}:{device['port']} — "
            f"firmware {device.get('version') or 'unknown'}, "
            f"id {device.get('id') or 'unknown'}"
        )
    if len(found) == 1:
        lines.append(f"Use: xteinkcli --ip {found[0]['ip']} status")
    print("\n".join(lines))
    return EXIT_OK


def cmd_status(args: argparse.Namespace, config: AppConfig) -> int:
    with _client(config) as client:
        status = client.get_status()
    data = {"device": config.ip, "port": config.port, **status}
    lines = [
        f"X4 at {config.ip}:{config.port}",
        f"Firmware: {status.get('version') or 'unknown'}",
        f"Model: {status.get('device_type') or 'unknown'}",
        f"ID: {status.get('id') or 'unknown'}",
        f"SD mounted: {_yes_no(status.get('sd_present'))}",
        f"Space: {_format_bytes(status.get('used_bytes'))} used of "
        f"{_format_bytes(status.get('total_bytes'))} "
        f"({_format_bytes(status.get('free_bytes'))} free)",
    ]
    return _emit_success("status", data, lines, json_mode=args.json)


def cmd_list(args: argparse.Namespace, config: AppConfig) -> int:
    directory = normalize_remote_path(args.path, label="directory")
    with _client(config) as client:
        entries = client.list_dir(directory)
    for entry in entries:
        entry["path"] = (
            f"/{entry['name']}"
            if directory == "/"
            else f"{directory}/{entry['name']}"
        )
    data = {"directory": directory, "entries": entries}
    if args.json:
        return _emit_success("list", data, [], json_mode=True)
    if not entries:
        print(f"{directory} is empty.")
        return EXIT_OK
    lines = [f"Contents of {directory}:"]
    for entry in entries:
        kind = "DIR " if entry["type"] == "dir" else "FILE"
        size = "" if entry["type"] == "dir" else f"  {_format_bytes(entry['size'])}"
        lines.append(f"{kind}  {entry['name']}{size}")
    return _emit_success("list", data, lines, json_mode=args.json)


def cmd_wifi_info(args: argparse.Namespace, config: AppConfig) -> int:
    with _client(config) as client:
        info = client.wifi_info()
    data = {"device": config.ip, **info}
    lines = [
        f"SSID: {info['ssid']}",
        f"X4 IP: {info['ip']}",
        "(Wi-Fi passwords are never displayed.)",
    ]
    return _emit_success("wifi-info", data, lines, json_mode=args.json)


def _check_space(client: XteinkClient, file_size: int) -> dict[str, Any] | None:
    try:
        status = client.get_status()
    except XteinkError:
        return None
    free = status.get("free_bytes")
    if free is not None and free < file_size:
        raise DeviceError(
            f"Insufficient SD space: {_format_bytes(free)} free and "
            f"the file is {_format_bytes(file_size)}.",
            hint="Free space with `xteinkcli delete` or use another SD card.",
        )
    return {
        "free_bytes": free,
        "total_bytes": status.get("total_bytes"),
    }


def _verify_remote_file(
    client: XteinkClient,
    directory: str,
    name: str,
    expected_size: int,
    *,
    action: str,
) -> dict[str, Any]:
    entries = client.list_dir(directory)
    entry = _find_entry(entries, name)
    if entry is None:
        candidates = [
            item["name"]
            for item in entries
            if item["type"] == "file"
            and item["name"].casefold().startswith(name.rsplit(".", 1)[0].casefold())
        ]
        extra = f" Similar files: {candidates[:5]}" if candidates else ""
        raise ProtocolError(
            f"{action}: the firmware does not show {name} in {directory} after the operation.",
            hint=(
                "Stock firmware may have renamed the file because of a collision. "
                f"Check the directory with `xteinkcli list {directory}`.{extra}"
            ),
        )
    if entry["type"] != "file":
        raise ProtocolError(f"{action}: {name} appears as a directory, not a file.")
    if entry["size"] != expected_size:
        raise DeviceError(
            f"{action}: {name} has {entry['size']} bytes on the X4; expected "
            f"{expected_size}.",
            hint="The transfer may be incomplete. Check the reader.",
        )
    return entry


def cmd_send(args: argparse.Namespace, config: AppConfig) -> int:
    local = Path(args.file).expanduser()
    if not local.exists():
        raise UsageError(f"Local file does not exist: {local}")
    if not local.is_file():
        raise UsageError(f"Local path is not a file: {local}")
    if local.suffix.casefold() != ".epub" and not args.allow_extension:
        raise UsageError(
            f"The extension {local.suffix or '(none)'} is not .epub.",
            hint="Use --allow-extension if you really want to send another file type.",
        )
    local_size = local.stat().st_size
    if local_size <= 0:
        raise UsageError(f"Local file is empty: {local}")

    dest = ensure_dir_argument(args.dest)
    name = validate_remote_name(args.name or local.name, label="remote name")
    target = join_remote(dest, name)

    with _client(config) as client:
        try:
            entries = client.list_dir(dest)
        except NotFoundError as exc:
            raise NotFoundError(
                f"Destination directory does not exist: {dest}.",
                hint=f"Create the folder with `xteinkcli mkdir {dest}`.",
            ) from exc
        existing = _find_entry(entries, name)
        if existing is not None and existing["type"] == "dir":
            raise ConflictError(f"The destination {target} is a directory.")
        if existing is not None and not args.overwrite:
            raise ConflictError(
                f"{target} already exists on the X4.",
                hint=(
                    "Files are not overwritten by default. Repeat with --overwrite --yes "
                    "if you want to replace it."
                ),
            )
        if existing is not None:
            _confirm(
                args.yes,
                f"{target} ({_format_bytes(existing['size'])}) will be replaced "
                f"by {local.name} ({_format_bytes(local_size)}).",
                json_mode=args.json,
            )

        space = _check_space(client, local_size)

        if not args.json:
            print(
                f"Uploading {local.name} ({_format_bytes(local_size)}) to {dest}...",
                file=sys.stderr,
            )

        overwritten = False
        rate = 0
        if existing is None:
            rate = client.upload_file(local, target)
            _verify_remote_file(client, dest, name, local_size, action=f"Upload {target}")
        else:
            stem = name.rsplit(".", 1)[0]
            suffix = local.suffix
            temp_name = f"{stem}.xteinkcli-tmp-{os.urandom(4).hex()}{suffix}"
            temp_target = join_remote(dest, temp_name)
            rate = client.upload_file(local, temp_target)
            _verify_remote_file(
                client, dest, temp_name, local_size, action=f"Upload {temp_target}"
            )
            client.delete(target, is_dir=False)
            try:
                client.rename(temp_target, target)
            except XteinkError as exc:
                raise DeviceError(
                    f"Could not rename temporary file {temp_target} to {target}: "
                    f"{exc.message}",
                    hint=(
                        f"The new book is still at {temp_target}; the original may "
                        "have been deleted. Check the reader before retrying."
                    ),
                ) from exc
            _verify_remote_file(client, dest, name, local_size, action=f"Upload {target}")
            overwritten = True

    data = {
        "local_path": str(local),
        "remote_path": target,
        "bytes": local_size,
        "verified": True,
        "overwritten": overwritten,
        "rate_bytes_s": rate,
        "space": space,
    }
    lines = [
        f"Uploaded and verified: {target}",
        f"Size: {_format_bytes(local_size)}  "
        f"Speed: {_format_bytes(rate)}/s",
    ]
    if overwritten:
        lines.append("The existing file was replaced.")
    return _emit_success("send", data, lines, json_mode=args.json)


def cmd_mkdir(args: argparse.Namespace, config: AppConfig) -> int:
    target = normalize_remote_path(args.path, label="directory", allow_root=False)
    with _client(config) as client:
        client.mkdir(target)
    data = {"path": target, "created": True}
    return _emit_success(
        "mkdir", data, [f"Folder created and verified: {target}"], json_mode=args.json
    )


def cmd_rename(args: argparse.Namespace, config: AppConfig) -> int:
    source = normalize_remote_path(args.source, label="source", allow_root=False)
    new_name = validate_remote_name(args.new_name, label="new name")
    target = join_remote(parent_remote(source), new_name)
    if source == target:
        raise UsageError("The new name is the same as the current name.")
    with _client(config) as client:
        client.rename(source, target)
    data = {"source": source, "target": target, "verified": True}
    return _emit_success(
        "rename", data, [f"Renamed and verified: {source} -> {target}"], json_mode=args.json
    )


def cmd_move(args: argparse.Namespace, config: AppConfig) -> int:
    source = normalize_remote_path(args.source, label="source", allow_root=False)
    dest = ensure_dir_argument(args.dest)
    target = join_remote(dest, basename_remote(source))
    if source == target:
        raise UsageError("The source is already in that directory.")
    with _client(config) as client:
        entries = client.list_dir(dest)
        if _find_entry(entries, basename_remote(source)) is not None:
            raise ConflictError(f"{target} already exists in the destination.")
        client.rename(source, target)
    data = {"source": source, "target": target, "verified": True}
    return _emit_success(
        "move", data, [f"Moved and verified: {source} -> {target}"], json_mode=args.json
    )


def cmd_download(args: argparse.Namespace, config: AppConfig) -> int:
    remote = normalize_remote_path(args.path, label="path", allow_root=False)
    remote_name = basename_remote(remote)
    if args.out:
        out = Path(args.out).expanduser()
        if out.exists() and out.is_dir():
            out = out / remote_name
        elif str(args.out).endswith(("/", "\\")):
            out = out / remote_name
    else:
        out = Path.cwd() / remote_name
    if not out.parent.is_dir():
        raise UsageError(f"Local destination directory does not exist: {out.parent}")

    with _client(config) as client:
        parent = parent_remote(remote)
        entry = _find_entry(client.list_dir(parent), remote_name)
        if entry is None:
            raise NotFoundError(
                f"{remote} does not exist on the X4.",
                hint="Use `xteinkcli list` to see the exact path.",
            )
        if entry["type"] != "file":
            raise UsageError(f"{remote} is a directory, not a file.")
        if out.exists():
            if not args.overwrite:
                raise ConflictError(
                    f"Local file already exists: {out}.",
                    hint="Use --overwrite --yes to replace it.",
                )
            _confirm(
                args.yes,
                f"The local file {out} will be replaced.",
                json_mode=args.json,
            )
        result = client.download_file(
            remote, out, expected_size=entry["size"], overwrite=args.overwrite
        )
    data = {"remote_path": remote, "verified": True, **result}
    lines = [
        f"Downloaded and verified: {remote} -> {out}",
        f"Size: {_format_bytes(result['bytes'])}",
    ]
    return _emit_success("download", data, lines, json_mode=args.json)


def cmd_delete(args: argparse.Namespace, config: AppConfig) -> int:
    target = normalize_remote_path(args.path, label="path", allow_root=False)
    parent = parent_remote(target)
    name = basename_remote(target)
    with _client(config) as client:
        entry = _find_entry(client.list_dir(parent), name)
        if entry is None:
            raise NotFoundError(f"{target} does not exist on the X4.")
        is_dir = entry["type"] == "dir"
        if is_dir:
            children = client.list_dir(target)
            if children:
                raise ConflictError(
                    f"The folder {target} is not empty ({len(children)} entries).",
                    hint="Delete or move its contents first; recursive deletion is not supported.",
                )
        kind = "folder" if is_dir else "file"
        _confirm(
            args.yes,
            f"The {kind} {target} will be deleted from the X4.",
            json_mode=args.json,
        )
        client.delete(target, is_dir=is_dir)
    data = {"path": target, "type": "dir" if is_dir else "file", "deleted": True}
    return _emit_success(
        "delete", data, [f"Deleted and verified: {target}"], json_mode=args.json
    )


def _search_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def cmd_search(args: argparse.Namespace, config: AppConfig) -> int:
    if not args.query.strip():
        raise UsageError("The search query cannot be empty.")
    root = normalize_remote_path(args.dir, label="directory")
    if not 0 <= args.max_depth <= 20:
        raise UsageError("--max-depth must be between 0 and 20.")
    if not 1 <= args.limit <= 1000:
        raise UsageError("--limit must be between 1 and 1000.")
    key = _search_key(args.query.strip())
    matches: list[dict[str, Any]] = []
    warnings: list[str] = []
    scanned = 0

    with _client(config) as client:
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack and len(matches) < args.limit:
            directory, depth = stack.pop()
            try:
                entries = client.list_dir(directory)
                scanned += 1
            except XteinkError as exc:
                warnings.append(f"{directory}: {exc.message}")
                continue
            for entry in entries:
                path = (
                    f"/{entry['name']}"
                    if directory == "/"
                    else f"{directory}/{entry['name']}"
                )
                if key in _search_key(entry["name"]):
                    matches.append(
                        {
                            "path": path,
                            "name": entry["name"],
                            "type": entry["type"],
                            "size": entry["size"],
                        }
                    )
                    if len(matches) >= args.limit:
                        break
                if entry["type"] == "dir" and depth < args.max_depth:
                    stack.append((path, depth + 1))

    data = {
        "query": args.query,
        "directory": root,
        "matches": matches,
        "truncated": len(matches) >= args.limit,
        "scanned_directories": scanned,
        "warnings": warnings,
    }
    if args.json:
        return _emit_success("search", data, [], json_mode=True)
    if not matches:
        print(f"No results for {args.query!r} in {root}.")
        return EXIT_OK
    lines = [f"{len(matches)} result(s) for {args.query!r}:"]
    for item in matches:
        kind = "DIR " if item["type"] == "dir" else "FILE"
        lines.append(f"{kind}  {item['path']}")
    if warnings:
        lines.append(f"Warnings: {len(warnings)} directories could not be read.")
    return _emit_success("search", data, lines, json_mode=args.json)


def cmd_open(args: argparse.Namespace, config: AppConfig) -> int:
    target = normalize_remote_path(args.path, label="path", allow_root=False)
    parent = parent_remote(target)
    name = basename_remote(target)
    with _client(config) as client:
        entry = _find_entry(client.list_dir(parent), name)
        if entry is None:
            raise NotFoundError(f"{target} does not exist on the X4.")
        if entry["type"] != "file":
            raise UsageError(f"{target} is a directory, not a book.")
        _confirm(
            args.yes,
            f"The X4 will try to open {target} and will reboot.",
            json_mode=args.json,
        )
        result = client.open_book(target)
    data = {
        "path": target,
        **result,
        "verified": False,
        "mechanism": "HTTP GET /fileOpen?path=...",
        "note": (
            "Documented through stock-firmware reverse engineering; not verified live "
            "in this project. HTTP 200 only confirms that the command was accepted."
        ),
    }
    lines = [
        f"Open command sent for {target}.",
        "The X4 should reboot and open the book. It cannot be verified over HTTP.",
    ]
    return _emit_success("open", data, lines, json_mode=args.json)


# ----------------------------------------------------------------------
# BLE
# ----------------------------------------------------------------------
def _resolve_ble(args: argparse.Namespace, config: AppConfig) -> str:
    address = getattr(args, "address", None)
    if address:
        return address
    wanted = getattr(args, "device_name", None)
    devices = run_ble(
        scan_devices(
            timeout=args.scan_timeout,
            prefix=config.ble_name_prefix or DEFAULT_BLE_PREFIX,
        )
    )
    if wanted:
        exact = [item for item in devices if item.name.casefold() == wanted.casefold()]
        if exact:
            devices = exact
        else:
            devices = [
                item
                for item in devices
                if wanted.casefold() in item.name.casefold()
            ]
    if not devices:
        raise NotFoundError(
            "No X4 was found over BLE.",
            hint=(
                "Check that Bluetooth is enabled, the reader is powered on, and "
                "BLE is available on its current screen. You can also pass "
                "--address."
            ),
        )
    if len(devices) > 1:
        listing = ", ".join(f"{item.name} ({item.address})" for item in devices)
        raise ConflictError(
            f"Several compatible devices found: {listing}.",
            hint="Choose one with --address or --device-name.",
        )
    return devices[0].address


def _ble_call(
    args: argparse.Namespace,
    config: AppConfig,
    action: Callable[[BleSession], Any],
) -> Any:
    address = _resolve_ble(args, config)

    async def runner() -> Any:
        async with BleSession(
            address,
            timeout=max(config.timeout, 15.0),
            connect_timeout=max(config.timeout, 20.0),
        ) as session:
            return await action(session)

    return run_ble(runner())


def cmd_ble_scan(args: argparse.Namespace, config: AppConfig) -> int:
    devices = run_ble(
        scan_devices(
            timeout=args.scan_timeout,
            prefix=config.ble_name_prefix or DEFAULT_BLE_PREFIX,
        )
    )
    data = {"devices": [item.to_dict() for item in devices]}
    if args.json:
        return _emit_success("ble scan", data, [], json_mode=True)
    if not devices:
        print("No XTEPD_BLE_* devices found.")
        print("The reader must be powered on and have BLE active.")
        return EXIT_OK
    lines = ["BLE devices found:"]
    for item in devices:
        rssi = f" RSSI {item.rssi} dBm" if item.rssi is not None else ""
        lines.append(f"{item.name}  {item.address}{rssi}")
    return _emit_success("ble scan", data, lines, json_mode=True)


def cmd_ble_id(args: argparse.Namespace, config: AppConfig) -> int:
    reply = _ble_call(args, config, lambda session: session.device_id())
    data = {"device_id": reply.get("id")}
    return _emit_success(
        "ble id", data, [f"Device ID: {reply.get('id')}"], json_mode=args.json
    )


def cmd_ble_status(args: argparse.Namespace, config: AppConfig) -> int:
    reply = _ble_call(args, config, lambda session: session.status())
    data = reply
    lines = ["X4 BLE status:"]
    labels = {
        "e": "command",
        "s": "SSID",
        "ui": "UI mode",
        "on": "Wi-Fi enabled",
        "wm": "Wi-Fi mode",
        "ws": "connection state",
        "ok": "connected",
        "ing": "connecting",
        "fail": "failure",
        "redacted_fields": "hidden fields",
    }
    for key, value in reply.items():
        if key == "p":
            continue
        lines.append(f"  {labels.get(key, key)}: {value}")
    lines.append("(Wi-Fi passwords are always removed from the output.)")
    return _emit_success("ble status", data, lines, json_mode=args.json)


def cmd_ble_wifi_scan(args: argparse.Namespace, config: AppConfig) -> int:
    networks = _ble_call(args, config, lambda session: session.scan_wifi())
    networks.sort(key=lambda item: -(item.get("rssi") or -999))
    data = {"networks": networks}
    if args.json:
        return _emit_success("ble wifi-scan", data, [], json_mode=True)
    if not networks:
        print("The BLE scan returned no Wi-Fi networks.")
        return EXIT_OK
    lines = ["Wi-Fi networks seen by the X4:"]
    for network in networks:
        lines.append(f"  {network['ssid']}  ({network.get('rssi', '?')} dBm)")
    return _emit_success("ble wifi-scan", data, lines, json_mode=args.json)


def _read_wifi_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        line = sys.stdin.readline()
        if line == "":
            raise UsageError("No password was received on stdin.")
        password = line.rstrip("\r\n")
    elif sys.stdin.isatty():
        password = getpass.getpass("Wi-Fi password (hidden): ")
    else:
        raise UsageError(
            "No interactive terminal available to request the password.",
            hint=(
                "Use --password-stdin and pipe the password through stdin, for "
                "example: $pw | xteinkcli ble wifi-connect MyNetwork --password-stdin --yes"
            ),
        )
    if "\x00" in password or "\r" in password or "\n" in password:
        raise UsageError("The password contains invalid characters.")
    if len(password.encode("utf-8")) > 64:
        raise UsageError("The Wi-Fi password exceeds 64 bytes.")
    return password


def cmd_ble_wifi_connect(args: argparse.Namespace, config: AppConfig) -> int:
    ssid = args.ssid
    if not ssid or "\x00" in ssid or len(ssid.encode("utf-8")) > 32:
        raise UsageError("The SSID must be between 1 and 32 UTF-8 bytes.")
    _confirm(
        args.yes,
        f"The X4 will connect to and save the Wi-Fi network {ssid!r}.",
        json_mode=args.json,
    )
    password = _read_wifi_password(args)
    reply = _ble_call(
        args, config, lambda session: session.connect_wifi(ssid, password)
    )
    data = {"ssid": ssid, "status": reply.get("s"), "password_saved_on_device": True}
    return _emit_success(
        "ble wifi-connect",
        data,
        [f"Wi-Fi {ssid!r}: the X4 returned s={reply.get('s')} (0 = ok)."],
        json_mode=args.json,
    )


def _cmd_ble_wifi_toggle(args: argparse.Namespace, config: AppConfig, enabled: bool) -> int:
    action = "enable" if enabled else "disable"
    _confirm(args.yes, f"The X4 Wi-Fi will be {action}d.", json_mode=args.json)
    reply = _ble_call(args, config, lambda session: session.set_wifi(enabled))
    data = {"enabled": enabled, **reply}
    lines = [
        f"Wi-Fi {'enabled' if enabled else 'disabled'}: "
        f"s={reply.get('s')}, on={reply.get('on')}"
    ]
    return _emit_success("ble wifi", data, lines, json_mode=args.json)


def cmd_ble_wifi_on(args: argparse.Namespace, config: AppConfig) -> int:
    return _cmd_ble_wifi_toggle(args, config, True)


def cmd_ble_wifi_off(args: argparse.Namespace, config: AppConfig) -> int:
    return _cmd_ble_wifi_toggle(args, config, False)


def cmd_ble_nickname(args: argparse.Namespace, config: AppConfig) -> int:
    if args.nickname is None:
        reply = _ble_call(args, config, lambda session: session.get_nickname())
        data = {"nickname": reply.get("n")}
        return _emit_success(
            "ble nickname",
            data,
            [f"Device name: {reply.get('n')}"],
            json_mode=args.json,
        )
    nickname = args.nickname.strip()
    if not nickname or len(nickname) > 64 or any(ord(ch) < 32 for ch in nickname):
        raise UsageError("The name must have 1-64 printable characters.")
    _confirm(
        args.yes,
        f"The X4 name will be changed to {nickname!r}.",
        json_mode=args.json,
    )
    reply = _ble_call(
        args, config, lambda session: session.set_nickname(nickname)
    )
    data = {"nickname": reply.get("n", nickname), "status": reply.get("s")}
    return _emit_success(
        "ble nickname",
        data,
        [f"Name changed: {data['nickname']!r}"],
        json_mode=args.json,
    )


def cmd_ble_reading_time(args: argparse.Namespace, config: AppConfig) -> int:
    items = _ble_call(args, config, lambda session: session.reading_times())
    data = {"books": items}
    if args.json:
        return _emit_success("ble reading-time", data, [], json_mode=True)
    if not items:
        print("The X4 returned no reading times.")
        return EXIT_OK
    lines = ["Reading time:"]
    for item in items:
        minutes = item.get("minutes")
        lines.append(f"  {item.get('name')}: {minutes} min")
    return _emit_success("ble reading-time", data, lines, json_mode=args.json)


def cmd_ble_open(args: argparse.Namespace, config: AppConfig) -> int:
    target = normalize_remote_path(args.path, label="path", allow_root=False)
    _confirm(
        args.yes,
        f"The X4 will try to open {target} over BLE and will reboot.",
        json_mode=args.json,
    )
    reply = _ble_call(args, config, lambda session: session.open_book(target))
    data = {"path": target, "verified": False, **reply}
    lines = [
        f"BLE open command sent for {target}.",
        "The X4 should reboot and open the book. Cannot be verified from the CLI.",
    ]
    return _emit_success("ble open", data, lines, json_mode=args.json)


def _dispatch_ble(args: argparse.Namespace, config: AppConfig) -> int:
    command = getattr(args, "ble_command", None)
    if not command:
        raise UsageError(
            "Missing a `ble` subcommand.",
            hint="Try: xteinkcli ble scan",
        )
    handlers: dict[str, Callable[[argparse.Namespace, AppConfig], int]] = {
        "scan": cmd_ble_scan,
        "id": cmd_ble_id,
        "status": cmd_ble_status,
        "wifi-scan": cmd_ble_wifi_scan,
        "wifi-connect": cmd_ble_wifi_connect,
        "wifi-on": cmd_ble_wifi_on,
        "wifi-off": cmd_ble_wifi_off,
        "nickname": cmd_ble_nickname,
        "reading-time": cmd_ble_reading_time,
        "open": cmd_ble_open,
    }
    return handlers[command](args, config)


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------
def _configure_stdio() -> None:
    """Avoid UnicodeEncodeError with non-Latin book names on Windows."""

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in raw_argv
    debug_requested = "--debug" in raw_argv
    parser = build_parser()

    try:
        args = parser.parse_args(raw_argv)
    except UsageError as exc:
        return _report_error(exc, command=None, json_mode=json_requested)
    except SystemExit:
        raise

    if not args.command:
        parser.print_help()
        return EXIT_OK

    command = args.command
    if command == "ble":
        command = f"ble {getattr(args, 'ble_command', '') or ''}".strip()

    try:
        config = _settings(args)
        handlers: dict[str, Callable[[argparse.Namespace, AppConfig], int]] = {
            "doctor": cmd_doctor,
            "discover": cmd_discover,
            "status": cmd_status,
            "list": cmd_list,
            "wifi-info": cmd_wifi_info,
            "send": cmd_send,
            "mkdir": cmd_mkdir,
            "rename": cmd_rename,
            "move": cmd_move,
            "download": cmd_download,
            "delete": cmd_delete,
            "search": cmd_search,
            "open": cmd_open,
            "ble": _dispatch_ble,
        }
        return handlers[args.command](args, config)
    except XteinkError as exc:
        return _report_error(exc, command=command, json_mode=args.json)
    except KeyboardInterrupt:
        print("Interrupted by the user.", file=sys.stderr)
        return 130
    except Exception as exc:  # pragma: no cover - safety net
        if args.debug or debug_requested:
            traceback.print_exc()
        error = XteinkError(
            f"Unexpected error ({type(exc).__name__}): {exc}",
            hint="Run again with --debug to see the traceback.",
        )
        return _report_error(error, command=command, json_mode=args.json)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
