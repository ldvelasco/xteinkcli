"""Strict path validation for the reader's SD card.

Device paths are absolute POSIX paths. They must never be confused with local
Windows paths, and traversal (``..``) must never be allowed.
"""

from __future__ import annotations

import posixpath
import re

from .errors import UsageError

MAX_PATH_LEN = 512
MAX_SEGMENT_LEN = 255
_FORBIDDEN_NAME_CHARS = set('/<>:"|?*\\')
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def normalize_remote_path(
    path: str,
    *,
    label: str = "path",
    allow_root: bool = True,
) -> str:
    if not isinstance(path, str):
        raise UsageError(f"The {label} must be a string.")
    if not path:
        raise UsageError(f"The {label} cannot be empty.")
    if _WINDOWS_DRIVE_RE.match(path) or path.startswith("\\\\"):
        raise UsageError(
            f"This looks like a local Windows path: {path!r}. "
            f"Use an absolute SD path, for example /Books/Book.epub."
        )
    if "\\" in path:
        raise UsageError(
            f"The {label} must use '/' (not '\\\\'): {path!r}."
        )
    if not path.startswith("/"):
        raise UsageError(
            f"The {label} must be absolute and start with '/': {path!r}."
        )
    if _CONTROL_RE.search(path):
        raise UsageError(f"The {label} contains control characters.")
    if path == "/":
        if not allow_root:
            raise UsageError("The root '/' is not valid for this operation.")
        return "/"
    if path.endswith("/"):
        path = path.rstrip("/")
        if not path:
            return "/"

    segments = path.split("/")[1:]
    for segment in segments:
        _validate_segment(segment, label=label)

    normalized = "/" + "/".join(segments)
    if len(normalized) > MAX_PATH_LEN:
        raise UsageError(
            f"The {label} exceeds {MAX_PATH_LEN} characters: {normalized!r}."
        )
    return normalized


def validate_remote_name(name: str, *, label: str = "name") -> str:
    if not isinstance(name, str) or not name:
        raise UsageError(f"The {label} cannot be empty.")
    if name in {".", ".."}:
        raise UsageError(f"The {label} cannot be '.' or '..'.")
    _validate_segment(name, label=label)
    return name


def _validate_segment(segment: str, *, label: str) -> None:
    if segment in {"", ".", ".."}:
        raise UsageError(
            f"The {label} contains a disallowed segment ({segment!r})."
        )
    if len(segment) > MAX_SEGMENT_LEN:
        raise UsageError(
            f"A {label} component exceeds {MAX_SEGMENT_LEN} characters."
        )
    if segment != segment.strip():
        raise UsageError(
            f"A {label} component starts or ends with spaces."
        )
    forbidden = sorted(set(segment) & _FORBIDDEN_NAME_CHARS)
    if forbidden:
        raise UsageError(
            f"The {label} contains unsupported characters: {''.join(forbidden)!r}."
        )
    if _CONTROL_RE.search(segment):
        raise UsageError(f"The {label} contains control characters.")
    if segment.endswith("."):
        raise UsageError(
            f"A {label} component cannot end with a period: {segment!r}."
        )


def join_remote(directory: str, name: str) -> str:
    directory = normalize_remote_path(directory, label="directory")
    name = validate_remote_name(name, label="file name")
    if directory == "/":
        return f"/{name}"
    return f"{directory}/{name}"


def parent_remote(path: str) -> str:
    path = normalize_remote_path(path, label="path", allow_root=False)
    parent = posixpath.dirname(path)
    return parent or "/"


def basename_remote(path: str) -> str:
    path = normalize_remote_path(path, label="path", allow_root=False)
    return posixpath.basename(path)


def ensure_dir_argument(path: str) -> str:
    return normalize_remote_path(path, label="directory")
