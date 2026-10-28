from __future__ import annotations

import pytest

from xteinkcli.errors import UsageError
from xteinkcli.remote_path import (
    basename_remote,
    ensure_dir_argument,
    join_remote,
    normalize_remote_path,
    parent_remote,
    validate_remote_name,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", "/"),
        ("/Books", "/Books"),
        ("/Books/", "/Books"),
        ("/Books/Ficción/El niño.epub", "/Books/Ficción/El niño.epub"),
        ("/Mis libros/Libro con espacios.epub", "/Mis libros/Libro con espacios.epub"),
    ],
)
def test_valid_paths(path, expected):
    assert normalize_remote_path(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "",
        "Books",
        "C:\\Libros\\a.epub",
        "\\\\servidor\\share\\a.epub",
        "/Books/../escape",
        "/Books//a.epub",
        "/Books/./a.epub",
        "/Books/a:b.epub",
        "/Books/ a.epub",
    ],
)
def test_invalid_paths(path):
    with pytest.raises(UsageError):
        normalize_remote_path(path)


def test_root_not_allowed_when_requested():
    with pytest.raises(UsageError):
        normalize_remote_path("/", allow_root=False)


def test_join_and_parent():
    assert join_remote("/", "a.epub") == "/a.epub"
    assert join_remote("/Books", "a.epub") == "/Books/a.epub"
    assert parent_remote("/Books/a.epub") == "/Books"
    assert parent_remote("/a.epub") == "/"
    assert basename_remote("/Books/a.epub") == "a.epub"
    assert ensure_dir_argument("/Books/") == "/Books"


def test_remote_name_validation():
    assert validate_remote_name("Libro ñ.epub") == "Libro ñ.epub"
    for name in ("", ".", "..", "a/b", "a\\b", "a:b", " a.epub", "a.epub "):
        with pytest.raises(UsageError):
            validate_remote_name(name)
