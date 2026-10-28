"""Simulated HTTP server that imitates the Xteink X4 stock API.

It does not replace a hardware test: it reproduces the documented contract so
the CLI can be tested without the reader.
"""

from __future__ import annotations

import json
import posixpath
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

DEFAULT_STATUS = {
    "type": "SD",
    "isOk": "true",
    "id": "7544812",
    "device_type": "ESP32C3",
    "version": "XT V5.6.33",
    "totalBytes": 32_563_368_240,
    "usedBytes": 2_333,
}


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        # The client may close /fileOpen without reading the body; not a failure.
        pass


class MockXteink:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/"}
        self.lock = threading.Lock()
        self.status_payload: dict[str, object] = dict(DEFAULT_STATUS)
        self.sta_text = "MiRed 192.168.1.50"
        self.auto_rename = False
        self.force_rename_suffix = False
        self.overwrite_on_collision = True
        self.store_truncated = False
        self.download_truncate = False
        self.fail_upload = False
        self.status_ok = True
        self.list_ok = True
        self.upload_delay = 0.0
        self.uploading = False
        self.status_during_upload = False
        self.status_calls = 0
        self.upload_requests: list[dict[str, object]] = []
        self.put_requests: list[dict[str, str]] = []
        self.delete_requests: list[str] = []
        self.open_requests: list[str] = []
        self.download_requests: list[str] = []

        device = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def _send(
                self,
                status: int,
                body: bytes = b"",
                content_type: str = "text/plain",
                extra_headers: dict[str, str] | None = None,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                if extra_headers:
                    for key, value in extra_headers.items():
                        self.send_header(key, value)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _query(self) -> dict[str, list[str]]:
                return parse_qs(urlsplit(self.path).query, keep_blank_values=True)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                path = unquote(parsed.path)
                query = self._query()
                if path == "/status":
                    with device.lock:
                        device.status_calls += 1
                        if device.uploading:
                            device.status_during_upload = True
                    if not device.status_ok:
                        self._send(500, b"status failed")
                        return
                    self._send(
                        200,
                        json.dumps(device.status_payload).encode(),
                        "application/json",
                    )
                    return
                if path == "/list":
                    if not device.list_ok:
                        self._send(500, b"list failed")
                        return
                    directory = query.get("dir", [""])[0]
                    if not directory:
                        self._send(500, b"missing dir")
                        return
                    directory = directory.rstrip("/") or "/"
                    if directory not in device.dirs:
                        self._send(404, b"no such dir")
                        return
                    entries = []
                    for folder in sorted(device.dirs):
                        if folder == "/":
                            continue
                        if posixpath.dirname(folder) == directory:
                            entries.append(
                                {
                                    "type": "dir",
                                    "size": "0",
                                    "name": posixpath.basename(folder),
                                }
                            )
                    for file_path, data in sorted(device.files.items()):
                        if posixpath.dirname(file_path) == directory:
                            entries.append(
                                {
                                    "type": "file",
                                    "size": str(len(data)),
                                    "name": posixpath.basename(file_path),
                                }
                            )
                    entries.sort(key=lambda item: (item["type"] != "dir", item["name"]))
                    self._send(200, json.dumps(entries).encode(), "text/json")
                    return
                if path == "/Read_staNameIp":
                    self._send(200, device.sta_text.encode(), "text/plain")
                    return
                if path == "/fileOpen":
                    target = query.get("path", [""])[0]
                    with device.lock:
                        device.open_requests.append(target)
                    if target not in device.files:
                        self._send(404, b"not found")
                        return
                    # The real stock firmware usually reboots after responding.
                    self._send(200, b"", "text/plain")
                    return
                if query.get("download", [""])[0] or path.startswith("/download"):
                    target = query.get("path", [path])[0]
                    with device.lock:
                        device.download_requests.append(target)
                    data = device.files.get(target)
                    if data is None:
                        self._send(404, b"not found")
                        return
                    if device.download_truncate:
                        data = data[: max(1, len(data) // 2)]
                    self._send(200, data, "application/octet-stream")
                    return
                self._send(404, b"not found")

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if parsed.path != "/edit":
                    self._send(404, b"not found")
                    return
                length = int(self.headers.get("Content-Length", "0"))
                with device.lock:
                    device.uploading = True
                try:
                    if device.upload_delay:
                        time.sleep(device.upload_delay)
                    body = self.rfile.read(length)
                    content_type = self.headers.get("Content-Type", "")
                    field, filename, file_data = _parse_multipart(content_type, body)
                    with device.lock:
                        device.upload_requests.append(
                            {
                                "filename": filename,
                                "field": field,
                                "size": len(file_data or b""),
                                "content_type": content_type,
                            }
                        )
                    if device.fail_upload:
                        self._send(500, b"upload failed")
                        return
                    if not filename or file_data is None:
                        self._send(500, b"bad multipart")
                        return
                    target = filename if filename.startswith("/") else "/" + filename
                    with device.lock:
                        parent = posixpath.dirname(target) or "/"
                        if parent not in device.dirs:
                            self._send(500, b"missing parent")
                            return
                        if device.force_rename_suffix:
                            parent_name = posixpath.basename(target)
                            stem, dot, suffix = parent_name.rpartition(".")
                            if not dot:
                                stem, suffix = parent_name, ""
                            renamed = f"{stem}_{int(time.time() * 1000)}"
                            if suffix:
                                renamed = f"{renamed}.{suffix}"
                            target = posixpath.join(
                                posixpath.dirname(target) or "/", renamed
                            )
                        elif target in device.files:
                            if device.auto_rename:
                                stem, dot, suffix = posixpath.basename(target).rpartition(".")
                                if not dot:
                                    stem, suffix = target, ""
                                new_name = f"{stem}_{int(time.time() * 1000)}.{suffix}"
                                target = posixpath.join(parent, new_name)
                            elif not device.overwrite_on_collision:
                                self._send(500, b"collision")
                                return
                        stored = file_data
                        if device.store_truncated and stored:
                            stored = stored[: max(1, len(stored) // 2)]
                        device.files[target] = stored
                    self._send(200, b"", "text/plain")
                finally:
                    with device.lock:
                        device.uploading = False

            def do_PUT(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if parsed.path != "/edit":
                    self._send(404, b"not found")
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8", errors="replace")
                form = parse_qs(body, keep_blank_values=True)
                for key, values in self._query().items():
                    form.setdefault(key, values)
                path = form.get("path", [""])[0]
                src = form.get("src", [""])[0]
                with device.lock:
                    device.put_requests.append({"path": path, "src": src})
                    if not path:
                        self._send(500, b"missing path")
                        return
                    if src:
                        if not _exists(device, src):
                            self._send(500, b"missing source")
                            return
                        if _exists(device, path):
                            self._send(500, b"destination exists")
                            return
                        target_parent = posixpath.dirname(path) or "/"
                        if target_parent not in device.dirs and not _dir_exists(
                            device, target_parent
                        ):
                            self._send(500, b"missing destination dir")
                            return
                        _move(device, src, path)
                    else:
                        directory = path.rstrip("/")
                        if not directory or directory in device.dirs:
                            self._send(500, b"mkdir failed")
                            return
                        parent = posixpath.dirname(directory) or "/"
                        if parent not in device.dirs:
                            self._send(500, b"missing parent")
                            return
                        device.dirs.add(directory)
                self._send(200, b"/", "text/plain")

            def do_DELETE(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if parsed.path != "/edit":
                    self._send(404, b"not found")
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8", errors="replace")
                form = parse_qs(body, keep_blank_values=True)
                for key, values in self._query().items():
                    form.setdefault(key, values)
                target = form.get("path", [""])[0].rstrip("/") or "/"
                with device.lock:
                    device.delete_requests.append(target)
                    if target in device.files:
                        del device.files[target]
                    elif target in device.dirs and target != "/":
                        children = [
                            item
                            for item in device.files
                            if posixpath.dirname(item) == target
                        ] + [
                            item
                            for item in device.dirs
                            if item != target and posixpath.dirname(item) == target
                        ]
                        if children:
                            self._send(500, b"dir not empty")
                            return
                        device.dirs.remove(target)
                    else:
                        self._send(500, b"not found")
                        return
                self._send(200, target.encode(), "text/plain")

        self._server = _QuietThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "MockXteink":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def add_file(self, path: str, data: bytes) -> None:
        with self.lock:
            parent = posixpath.dirname(path) or "/"
            if parent not in self.dirs:
                raise ValueError(f"Missing directory {parent}")
            self.files[path] = data

    def add_dir(self, path: str) -> None:
        with self.lock:
            self.dirs.add(path.rstrip("/") or "/")

    def __enter__(self) -> "MockXteink":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def _parse_multipart(
    content_type: str, body: bytes
) -> tuple[str | None, str | None, bytes | None]:
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not match:
        return None, None, None
    boundary = match.group(1).encode()
    header_end = body.find(b"\r\n\r\n")
    if header_end < 0:
        return None, None, None
    header_text = body[:header_end].decode("utf-8", errors="replace")
    field_match = re.search(
        r'Content-Disposition:\s*form-data;\s*name="([^"]*)"',
        header_text,
        re.IGNORECASE,
    )
    filename_match = re.search(
        r'Content-Disposition:\s*form-data;[^\r\n]*filename="([^"]*)"',
        header_text,
        re.IGNORECASE,
    )
    field = field_match.group(1) if field_match else None
    filename = filename_match.group(1) if filename_match else None
    payload_start = header_end + 4
    marker = b"\r\n--" + boundary
    payload_end = body.find(marker, payload_start)
    if payload_end < 0:
        return field, filename, None
    return field, filename, body[payload_start:payload_end]


def _exists(device: MockXteink, path: str) -> bool:
    path = path.rstrip("/") or "/"
    return path in device.files or path in device.dirs


def _dir_exists(device: MockXteink, path: str) -> bool:
    return (path.rstrip("/") or "/") in device.dirs


def _move(device: MockXteink, src: str, dst: str) -> None:
    if src in device.files:
        device.files[dst] = device.files.pop(src)
        return
    if src in device.dirs:
        device.dirs.remove(src)
        device.dirs.add(dst)
        for file_path in list(device.files):
            if file_path.startswith(src + "/"):
                new_path = dst + file_path[len(src) :]
                device.files[new_path] = device.files.pop(file_path)
        for folder in list(device.dirs):
            if folder.startswith(src + "/"):
                new_folder = dst + folder[len(src) :]
                device.dirs.remove(folder)
                device.dirs.add(new_folder)
