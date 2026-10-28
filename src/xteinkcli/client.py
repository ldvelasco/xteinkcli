"""HTTP client for the local stock-firmware API of the Xteink X4."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .errors import (
    ConflictError,
    DeviceError,
    NetworkError,
    NotFoundError,
    ProtocolError,
    UsageError,
    WifiNotConnected,
)
from .remote_path import (
    basename_remote,
    normalize_remote_path,
    parent_remote,
)

_IP_AT_END_RE = re.compile(
    r"^(?P<ssid>.*?)\s+(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s*$"
)
_SNIPPET_LIMIT = 240


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "ok"}:
            return True
        if lowered in {"false", "0", "no", "fail"}:
            return False
    return None


def _parse_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _snippet(response: httpx.Response) -> str:
    try:
        text = response.text
    except Exception:  # pragma: no cover - guard against unusual binary bodies
        return ""
    text = " ".join(text.split())
    return text[:_SNIPPET_LIMIT]


def _timeout_label(seconds: float) -> str:
    return f"{seconds:g} s"


class XteinkClient:
    """HTTP operations against a stock X4.

    Note: ``/status`` is not queried during an upload. The documented firmware
    stops answering that route while the transfer is in progress.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        upload_timeout: float = 600.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=min(timeout, 10.0),
                read=timeout,
                write=timeout,
                pool=min(timeout, 10.0),
            ),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "xteinkcli/0.1",
                "Accept": "*/*",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "XteinkClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------
    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.ConnectError as exc:
            raise NetworkError(
                f"Could not connect to the X4 at {self.base_url}.",
                hint=(
                    "Check that the reader is powered on, on the same Wi-Fi network, "
                    "and that File Transfer is active. The IP may have changed: "
                    "run `xteinkcli discover` or check the X4 screen."
                ),
            ) from exc
        except httpx.TimeoutException as exc:
            raise NetworkError(
                f"The X4 did not respond within the timeout at {self.base_url}.",
                hint=(
                    "It may be busy, asleep, or at a different IP. "
                    "Do not query /status during an upload. "
                    "Increase --timeout if the network is slow."
                ),
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkError(
                f"Network error while talking to the X4: {type(exc).__name__}.",
                hint="Check the Wi-Fi connection and the reader IP.",
            ) from exc

    def _check(
        self,
        response: httpx.Response,
        *,
        action: str,
        ok: tuple[int, ...] = (200, 201, 204),
    ) -> None:
        if response.status_code in ok:
            return
        detail = _snippet(response)
        suffix = f" Response: {detail!r}." if detail else ""
        if response.status_code == 404:
            raise NotFoundError(
                f"{action}: the firmware returned HTTP 404.{suffix}",
                hint="The route or file may not exist on the SD card.",
            )
        if response.status_code == 500:
            raise DeviceError(
                f"{action}: the firmware returned HTTP 500.{suffix}",
                hint=(
                    "This may be an invalid path, insufficient space, a duplicate "
                    "name, or an operation not allowed by the stock firmware."
                ),
            )
        raise DeviceError(
            f"{action}: the firmware returned HTTP {response.status_code}.{suffix}"
        )

    def _get_json(self, url: str, *, action: str, **kwargs: Any) -> Any:
        response = self._request("GET", url, **kwargs)
        self._check(response, action=action)
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            snippet = _snippet(response)
            raise ProtocolError(
                f"{action}: the response is not valid JSON.",
                hint=f"Received content: {snippet!r}",
            ) from exc

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------
    def get_status(self) -> dict[str, Any]:
        data = self._get_json("/status", action="Get status")
        if not isinstance(data, dict):
            raise ProtocolError(
                "Get status: /status did not return a JSON object."
            )

        total = _parse_int(data.get("totalBytes", data.get("total_bytes")))
        used = _parse_int(data.get("usedBytes", data.get("used_bytes")))
        sd_present = _parse_bool(data.get("sd"))
        if sd_present is None:
            sd_present = _parse_bool(data.get("isOk"))
        if sd_present is None:
            sd_present = data.get("type") == "SD" or bool(total)

        known = {
            "type",
            "sd",
            "isOk",
            "id",
            "device_type",
            "version",
            "totalBytes",
            "usedBytes",
        }
        if not known.intersection(data):
            raise ProtocolError(
                "Get status: /status has no recognizable X4 fields."
            )

        device_id = data.get("id")
        device_type = data.get("device_type")
        version = data.get("version")
        friendly_type = data.get("type")
        free = None
        if total is not None and used is not None:
            free = max(total - used, 0)

        return {
            "type": str(friendly_type) if friendly_type is not None else None,
            "sd_present": sd_present,
            "id": str(device_id) if device_id is not None else None,
            "device_type": str(device_type) if device_type is not None else None,
            "version": str(version) if version is not None else None,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
        }

    def list_dir(self, directory: str = "/") -> list[dict[str, Any]]:
        directory = normalize_remote_path(directory, label="directory")
        data = self._get_json(
            "/list",
            action=f"List {directory}",
            params={"dir": directory},
        )
        if isinstance(data, dict) and "error" in data:
            raise DeviceError(
                f"List {directory}: the firmware returned an error: "
                f"{str(data.get('error'))[:200]}"
            )
        if not isinstance(data, list):
            raise ProtocolError(
                f"List {directory}: expected a JSON list of entries."
            )

        entries: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                raise ProtocolError(
                    f"List {directory}: invalid entry: {item!r}."
                )
            raw_name = item.get("name", item.get("n"))
            raw_type = item.get("type", item.get("t"))
            raw_size = item.get("size", item.get("z", 0))
            if not isinstance(raw_name, str) or not raw_name:
                raise ProtocolError(
                    f"List {directory}: entry without a valid name: {item!r}."
                )
            if any(token in raw_name for token in ("/", "\\", "\x00")) or any(
                ord(ch) < 32 or ord(ch) == 127 for ch in raw_name
            ):
                raise ProtocolError(
                    f"List {directory}: invalid name returned by the X4: "
                    f"{raw_name!r}."
                )
            if raw_name in {".", ".."}:
                continue
            if raw_type not in {"dir", "file", "folder"}:
                raise ProtocolError(
                    f"List {directory}: unknown type {raw_type!r} "
                    f"for {raw_name!r}."
                )
            size = _parse_int(raw_size)
            if size is None:
                size = 0
            entries.append(
                {
                    "name": raw_name,
                    "type": "dir" if raw_type in {"dir", "folder"} else "file",
                    "size": max(size, 0),
                }
            )

        entries.sort(key=lambda item: (item["type"] != "dir", item["name"].casefold()))
        return entries

    def wifi_info(self) -> dict[str, Any]:
        response = self._request("GET", "/Read_staNameIp")
        self._check(response, action="Get Wi-Fi info")
        text = " ".join(response.text.split())
        match = _IP_AT_END_RE.match(text)
        if match:
            try:
                ip = str(ipaddress.ip_address(match.group("ip")))
            except ValueError:
                ip = None
            if ip:
                ssid = match.group("ssid").strip()
                return {"ssid": ssid, "ip": ip}
        snippet = text[:_SNIPPET_LIMIT] or "(empty response)"
        raise WifiNotConnected(
            "The X4 does not appear to be connected to a Wi-Fi network as a client.",
            hint=(
                "Enable File Transfer on the reader or configure Wi-Fi. "
                f"Response received: {snippet!r}"
            ),
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------
    def mkdir(self, path: str) -> None:
        target = normalize_remote_path(path, label="directory", allow_root=False)
        parent = parent_remote(target)
        existing = self._entry_at(parent, basename_remote(target))
        if existing is not None:
            raise ConflictError(f"An entry already exists at {target}.")
        response = self._request("PUT", "/edit", data={"path": target + "/"})
        self._check(response, action=f"Create folder {target}")
        created = self._entry_at(parent, basename_remote(target))
        if created is None or created["type"] != "dir":
            raise ProtocolError(
                f"Create folder {target}: the firmware returned HTTP "
                f"{response.status_code}, but the folder does not appear in /list.",
                hint="Cannot confirm creation. Check the reader.",
            )

    def rename(self, source: str, target: str) -> None:
        src = normalize_remote_path(source, label="source", allow_root=False)
        dst = normalize_remote_path(target, label="destination", allow_root=False)
        if src == dst:
            raise UsageError("Source and destination are the same path.")
        source_entry = self._entry_at(parent_remote(src), basename_remote(src))
        if source_entry is None:
            raise NotFoundError(
                f"Source does not exist: {src}.",
                hint="Use `xteinkcli list` to see the exact path.",
            )
        if self._entry_at(parent_remote(dst), basename_remote(dst)) is not None:
            raise ConflictError(
                f"Destination already exists: {dst}.",
                hint="Choose another name or delete the destination with confirmation.",
            )

        response = self._request("PUT", "/edit", data={"path": dst, "src": src})
        self._check(response, action=f"Move/rename {src}")

        if self._entry_at(parent_remote(dst), basename_remote(dst)) is None:
            raise ProtocolError(
                f"Move/rename {src}: HTTP {response.status_code}, but {dst} "
                "does not appear in /list.",
                hint="Cannot confirm the operation. Check the reader.",
            )
        if self._entry_at(parent_remote(src), basename_remote(src)) is not None:
            raise ProtocolError(
                f"Move/rename {src}: the source still exists after the operation. "
                "The firmware may have copied instead of moved."
            )

    def delete(self, path: str, *, is_dir: bool) -> None:
        target = normalize_remote_path(path, label="path", allow_root=False)
        parent = parent_remote(target)
        name = basename_remote(target)
        entry = self._entry_at(parent, name)
        if entry is None:
            raise NotFoundError(f"Does not exist: {target}.")
        if is_dir and entry["type"] != "dir":
            raise UsageError(f"{target} is a file, not a directory.")
        if not is_dir and entry["type"] != "file":
            raise UsageError(f"{target} is a directory; use a folder operation.")

        request_path = target + "/" if is_dir else target
        # Some documented versions read `path` from the query and others from
        # the urlencoded body. Sending both is harmless and maximizes compatibility.
        response = self._request(
            "DELETE",
            "/edit",
            params={"path": request_path},
            data={"path": request_path},
        )
        self._check(response, action=f"Delete {target}")
        if self._entry_at(parent, name) is not None:
            raise ProtocolError(
                f"Delete {target}: HTTP {response.status_code}, but the entry "
                "still appears in /list.",
                hint="The firmware did not complete the deletion. Check the reader.",
            )

    def upload_file(self, local_path: Path, remote_path: str) -> int:
        target = normalize_remote_path(remote_path, label="destination", allow_root=False)
        try:
            stat = local_path.stat()
        except OSError as exc:
            raise UsageError(f"Cannot read local file {local_path}: {exc}")
        if not local_path.is_file():
            raise UsageError(f"Local path is not a file: {local_path}")
        file_size = stat.st_size
        if file_size <= 0:
            raise UsageError(f"Local file is empty: {local_path}")

        boundary = f"----xteinkcli{secrets.token_hex(12)}"
        content_type = (
            "application/epub+zip"
            if local_path.suffix.lower() == ".epub"
            else "application/octet-stream"
        )
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="data"; '
            f'filename="{target}"\r\n'
            f"Content-Type: {content_type}\r\n"
            f"\r\n"
        ).encode("utf-8")
        footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
        content_length = len(header) + file_size + len(footer)

        def body() -> Iterator[bytes]:
            yield header
            with local_path.open("rb") as handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            yield footer

        timeout = httpx.Timeout(
            connect=min(self.timeout, 10.0),
            read=self.upload_timeout,
            write=self.upload_timeout,
            pool=min(self.timeout, 10.0),
        )
        started = time.monotonic()
        try:
            response = self._client.post(
                "/edit",
                content=body(),
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(content_length),
                },
                timeout=timeout,
            )
        except httpx.ConnectError as exc:
            raise NetworkError(
                f"Could not connect to the X4 at {self.base_url} to upload the book."
            ) from exc
        except httpx.TimeoutException as exc:
            raise NetworkError(
                f"Upload to {target} exceeded the "
                f"{_timeout_label(self.upload_timeout)} timeout.",
                hint=(
                    "The file may be large or Wi-Fi slow. Check the reader and "
                    "run again; /status was not queried during the upload."
                ),
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkError(
                f"Network error during upload: {type(exc).__name__}."
            ) from exc

        self._check(response, action=f"Upload {target}")
        elapsed = time.monotonic() - started
        rate = file_size / elapsed if elapsed > 0 else 0.0
        return round(rate)

    # ------------------------------------------------------------------
    # Download and open
    # ------------------------------------------------------------------
    def download_file(
        self,
        remote_path: str,
        destination: Path,
        *,
        expected_size: int | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        remote = normalize_remote_path(remote_path, label="path", allow_root=False)
        destination = Path(destination)
        if not destination.parent.is_dir():
            raise UsageError(
                f"Local destination directory does not exist: {destination.parent}"
            )
        if destination.exists() and not overwrite:
            raise ConflictError(f"Local file already exists: {destination}.")

        temporary = destination.with_name(destination.name + ".part")
        if temporary.exists():
            temporary.unlink()

        url_path = "/" + quote(remote.lstrip("/"), safe="/")
        written = 0
        content_type = ""
        try:
            with self._client.stream(
                "GET",
                url_path,
                params={"download": "true"},
                timeout=httpx.Timeout(
                    connect=min(self.timeout, 10.0),
                    read=self.upload_timeout,
                    write=self.upload_timeout,
                    pool=min(self.timeout, 10.0),
                ),
            ) as response:
                self._check(response, action=f"Download {remote}")
                content_type = response.headers.get("content-type", "")
                declared = _parse_int(response.headers.get("content-length"))
                if content_type.lower().startswith("text/html") and not remote.lower().endswith(
                    (".html", ".htm")
                ):
                    raise ProtocolError(
                        f"Download {remote}: the X4 returned HTML instead of the file.",
                        hint="The path may not exist or the firmware may have changed.",
                    )
                with temporary.open("wb") as handle:
                    for chunk in response.iter_bytes(64 * 1024):
                        handle.write(chunk)
                        written += len(chunk)
                if declared is not None and declared != written:
                    raise ProtocolError(
                        f"Download {remote}: {declared} bytes were declared but "
                        f"{written} arrived."
                    )
                if expected_size is not None and written != expected_size:
                    raise ProtocolError(
                        f"Download {remote}: /list reports {expected_size} bytes, "
                        f"but the download has {written}."
                    )
            os.replace(temporary, destination)
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

        return {
            "path": remote,
            "destination": str(destination),
            "bytes": written,
            "content_type": content_type or None,
        }

    def open_book(self, path: str) -> dict[str, Any]:
        remote = normalize_remote_path(path, label="path", allow_root=False)
        # The body is not read: the firmware may reboot right after validating
        # and queueing the open. Receiving the headers is enough.
        with self._client.stream(
            "GET",
            "/fileOpen",
            params={"path": remote},
            timeout=httpx.Timeout(
                connect=min(self.timeout, 10.0),
                read=self.timeout,
                write=self.timeout,
                pool=min(self.timeout, 10.0),
            ),
        ) as response:
            self._check(response, action=f"Open {remote}")
            status = response.status_code
        return {
            "path": remote,
            "http_status": status,
            "reboot_expected": True,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _entry_at(self, directory: str, name: str) -> dict[str, Any] | None:
        for entry in self.list_dir(directory):
            if entry["name"] == name:
                return entry
        return None
