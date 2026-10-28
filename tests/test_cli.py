from __future__ import annotations

import json
from pathlib import Path

from conftest import cli_args
from mock_device import MockXteink

from xteinkcli.cli import main


def _json(capsys) -> dict:
    captured = capsys.readouterr()
    return json.loads(captured.out)


def _seed_library(device: MockXteink) -> None:
    device.add_dir("/Books")
    device.add_dir("/Books/Ficción")
    device.add_file("/Books/El principito.epub", b"EPUB-1")
    device.add_file("/Books/Ficción/Cien años.epub", b"EPUB-2")


def test_status_reads_note_shape(device, capsys):
    code = main(cli_args(device, "status"))
    payload = _json(capsys)
    assert code == 0
    assert payload["ok"] is True
    assert payload["data"]["version"] == "XT V5.6.33"
    assert payload["data"]["sd_present"] is True
    assert payload["data"]["free_bytes"] == 32_563_368_240 - 2_333
    assert device.status_calls == 1


def test_status_reads_re_shape(device, capsys):
    device.status_payload = {
        "type": "SD",
        "sd": True,
        "id": "abc",
        "device_type": "ESP32C3",
        "version": "XTOS V5.6.33",
        "totalBytes": 1000,
        "usedBytes": 250,
    }
    code = main(cli_args(device, "status"))
    payload = _json(capsys)
    assert code == 0
    assert payload["data"]["version"] == "XTOS V5.6.33"
    assert payload["data"]["free_bytes"] == 750


def test_list_unicode_and_directories_first(device, capsys):
    _seed_library(device)
    code = main(cli_args(device, "list", "/Books"))
    payload = _json(capsys)
    assert code == 0
    entries = payload["data"]["entries"]
    assert entries[0]["type"] == "dir"
    assert entries[0]["path"] == "/Books/Ficción"
    assert any(item["name"] == "El principito.epub" for item in entries)


def test_list_human_output_is_not_json(device, capsys):
    _seed_library(device)
    code = main(
        [
            "list",
            "/Books",
            "--ip",
            "127.0.0.1",
            "--port",
            str(device.port),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Contents of /Books" in captured.out
    assert "El principito.epub" in captured.out
    assert not captured.out.lstrip().startswith("{")


def test_wifi_info_ok(device, capsys):
    code = main(cli_args(device, "wifi-info"))
    payload = _json(capsys)
    assert code == 0
    assert payload["data"] == {
        "device": "127.0.0.1",
        "ssid": "MiRed",
        "ip": "192.168.1.50",
    }


def test_wifi_info_not_connected(device, capsys):
    device.sta_text = "Connection failed"
    code = main(cli_args(device, "wifi-info"))
    payload = _json(capsys)
    assert code == 4
    assert payload["ok"] is False
    assert payload["error"]["category"] == "wifi_not_connected"


def test_send_unicode_and_spaces(device, capsys):
    device.add_dir("/Books")
    local = Path("Mi libro ñandú.epub")
    local.write_bytes(b"EPUB-CONTENT-123")

    code = main(cli_args(device, "send", str(local), "--dest", "/Books"))
    payload = _json(capsys)
    assert code == 0, payload
    assert payload["data"]["verified"] is True
    assert payload["data"]["remote_path"] == "/Books/Mi libro ñandú.epub"
    assert device.files["/Books/Mi libro ñandú.epub"] == b"EPUB-CONTENT-123"
    request = device.upload_requests[-1]
    assert request["field"] == "data"
    assert request["filename"] == "/Books/Mi libro ñandú.epub"
    assert request["size"] == len(b"EPUB-CONTENT-123")
    assert device.status_calls == 1
    assert device.status_during_upload is False


def test_send_rejects_existing_without_overwrite(device, capsys):
    device.add_dir("/Books")
    device.add_file("/Books/Libro.epub", b"ORIGINAL")
    local = Path("Libro.epub")
    local.write_bytes(b"NUEVO")

    code = main(cli_args(device, "send", str(local), "--dest", "/Books"))
    payload = _json(capsys)
    assert code == 3
    assert payload["error"]["category"] == "conflict"
    assert device.files["/Books/Libro.epub"] == b"ORIGINAL"


def test_send_overwrite_uses_temp_and_verifies(device, capsys):
    device.add_dir("/Books")
    device.add_file("/Books/Libro.epub", b"ORIGINAL")
    local = Path("Libro.epub")
    local.write_bytes(b"NEW CONTENT")

    code = main(
        cli_args(
            device,
            "send",
            str(local),
            "--dest",
            "/Books",
            "--overwrite",
            "--yes",
        )
    )
    payload = _json(capsys)
    assert code == 0, payload
    assert payload["data"]["overwritten"] is True
    assert device.files["/Books/Libro.epub"] == b"NEW CONTENT"
    assert not any("xteinkcli-tmp" in path for path in device.files)


def test_send_overwrite_keeps_original_if_upload_verification_fails(device, capsys):
    device.add_dir("/Books")
    device.add_file("/Books/Libro.epub", b"ORIGINAL")
    device.store_truncated = True
    local = Path("Libro.epub")
    local.write_bytes(b"NEW CONTENT")

    code = main(
        cli_args(
            device,
            "send",
            str(local),
            "--dest",
            "/Books",
            "--overwrite",
            "--yes",
        )
    )
    payload = _json(capsys)
    assert code == 4
    assert payload["error"]["category"] == "device"
    assert device.files["/Books/Libro.epub"] == b"ORIGINAL"
    assert any("xteinkcli-tmp" in path for path in device.files)


def test_send_http_200_but_missing_file_is_failure(device, capsys):
    device.add_dir("/Books")
    # The simulated firmware returns 200 but stores under another name, like the
    # collision auto-rename documented for some stock versions.
    device.force_rename_suffix = True
    local = Path("Libro.epub")
    local.write_bytes(b"DATA")

    code = main(cli_args(device, "send", str(local), "--dest", "/Books"))
    payload = _json(capsys)
    assert code == 4, payload
    assert payload["error"]["category"] == "protocol"
    assert "/Books/Libro.epub" not in device.files
    assert any(path.startswith("/Books/Libro_") for path in device.files)


def test_mkdir_rename_move_delete(device, capsys):
    device.add_dir("/Books")
    device.add_file("/Books/a.epub", b"A")

    assert main(cli_args(device, "mkdir", "/Books/Nueva")) == 0
    capsys.readouterr()
    assert "/Books/Nueva" in device.dirs
    assert device.put_requests[-1]["path"] == "/Books/Nueva/"

    assert main(cli_args(device, "rename", "/Books/a.epub", "b.epub")) == 0
    capsys.readouterr()
    assert "/Books/b.epub" in device.files
    assert "/Books/a.epub" not in device.files

    device.add_dir("/Archivo")
    assert main(cli_args(device, "move", "/Books/b.epub", "/Archivo")) == 0
    capsys.readouterr()
    assert "/Archivo/b.epub" in device.files

    assert main(cli_args(device, "delete", "/Archivo/b.epub", "--yes")) == 0
    capsys.readouterr()
    assert "/Archivo/b.epub" not in device.files

    assert main(cli_args(device, "delete", "/Books/Nueva", "--yes")) == 0
    capsys.readouterr()
    assert "/Books/Nueva" not in device.dirs


def test_delete_requires_yes(device, capsys):
    device.add_dir("/Books")
    device.add_file("/Books/a.epub", b"A")
    code = main(cli_args(device, "delete", "/Books/a.epub"))
    payload = _json(capsys)
    assert code == 5
    assert payload["error"]["category"] == "confirmation"
    assert "/Books/a.epub" in device.files


def test_delete_non_empty_directory_is_refused(device, capsys):
    device.add_dir("/Books")
    device.add_file("/Books/a.epub", b"A")
    code = main(cli_args(device, "delete", "/Books", "--yes"))
    payload = _json(capsys)
    assert code == 3
    assert payload["error"]["category"] == "conflict"
    assert "/Books" in device.dirs


def test_download_and_overwrite(device, capsys):
    device.add_file("/Libro ñ.epub", b"CONTENT")
    out = Path("copia.epub")

    code = main(cli_args(device, "download", "/Libro ñ.epub", "--out", str(out)))
    payload = _json(capsys)
    assert code == 0, payload
    assert out.read_bytes() == b"CONTENT"
    assert device.download_requests == ["/Libro ñ.epub"]

    code = main(cli_args(device, "download", "/Libro ñ.epub", "--out", str(out)))
    assert code == 3
    capsys.readouterr()

    out.write_bytes(b"LOCAL")
    code = main(
        cli_args(
            device,
            "download",
            "/Libro ñ.epub",
            "--out",
            str(out),
            "--overwrite",
            "--yes",
        )
    )
    payload = _json(capsys)
    assert code == 0, payload
    assert out.read_bytes() == b"CONTENT"


def test_open_requires_confirmation_and_does_not_overclaim(device, capsys):
    device.add_file("/Libro.epub", b"DATA")
    code = main(cli_args(device, "open", "/Libro.epub"))
    payload = _json(capsys)
    assert code == 5
    assert payload["error"]["category"] == "confirmation"
    assert device.open_requests == []

    code = main(cli_args(device, "open", "/Libro.epub", "--yes"))
    payload = _json(capsys)
    assert code == 0, payload
    assert payload["data"]["verified"] is False
    assert payload["data"]["reboot_expected"] is True
    assert device.open_requests == ["/Libro.epub"]


def test_search_is_accent_insensitive(device, capsys):
    _seed_library(device)
    code = main(cli_args(device, "search", "ficcion", "--dir", "/Books"))
    payload = _json(capsys)
    assert code == 0
    paths = {item["path"] for item in payload["data"]["matches"]}
    assert "/Books/Ficción" in paths


def test_doctor_read_only(device, capsys):
    _seed_library(device)
    code = main(cli_args(device, "doctor"))
    payload = _json(capsys)
    assert code == 0, payload
    checks = {item["name"]: item for item in payload["data"]["checks"]}
    assert checks["HTTP /status"]["status"] == "ok"
    assert checks["HTTP /list"]["status"] == "ok"
    assert checks["Wi-Fi STA"]["status"] == "ok"
    assert device.upload_requests == []
    assert device.delete_requests == []
    assert device.put_requests == []


def test_doctor_failure_has_nonzero_exit(device, capsys):
    device.status_ok = False
    code = main(cli_args(device, "doctor"))
    payload = _json(capsys)
    assert code == 4
    assert payload["ok"] is False
    assert payload["data"]["ok"] is False


def test_ble_password_is_never_printed(monkeypatch, capsys):
    import io
    import sys

    import xteinkcli.cli as cli_module

    def fake_ble_call(args, config, action):
        return {"s": 0}

    monkeypatch.setattr(cli_module, "_ble_call", fake_ble_call)
    monkeypatch.setattr(sys, "stdin", io.StringIO("test-only-password\n"))
    code = main(
        [
            "ble",
            "wifi-connect",
            "MiRed",
            "--password-stdin",
            "--yes",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "test-only-password" not in captured.out
    assert "test-only-password" not in captured.err
    assert '"status": 0' in captured.out


def test_json_usage_error_is_structured(device, capsys):
    code = main(cli_args(device, "send", "no-existe.epub"))
    payload = _json(capsys)
    assert code == 2
    assert payload["ok"] is False
    assert payload["error"]["category"] == "usage"


def test_help_and_version(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "xteinkcli" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
