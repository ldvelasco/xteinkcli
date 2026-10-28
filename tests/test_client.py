from __future__ import annotations

from pathlib import Path

import pytest

from xteinkcli.client import XteinkClient
from xteinkcli.errors import (
    ConflictError,
    DeviceError,
    NetworkError,
    NotFoundError,
    ProtocolError,
    WifiNotConnected,
)


def test_client_status_both_shapes(device):
    with XteinkClient(device.base_url, timeout=2) as client:
        status = client.get_status()
        assert status["version"] == "XT V5.6.33"
        assert status["sd_present"] is True
        assert status["free_bytes"] == 32_563_368_240 - 2_333

        device.status_payload = {
            "type": "SD",
            "sd": True,
            "id": "abc",
            "device_type": "ESP32C3",
            "version": "XTOS V5.6.33",
            "totalBytes": "1000",
            "usedBytes": "250",
        }
        status = client.get_status()
        assert status["total_bytes"] == 1000
        assert status["free_bytes"] == 750


def test_client_status_without_known_fields(device):
    device.status_payload = {"foo": "bar"}
    with XteinkClient(device.base_url, timeout=2) as client:
        with pytest.raises(ProtocolError):
            client.get_status()


def test_client_upload_uses_field_data_and_exact_filename(device):
    device.add_dir("/Libros")
    local = Path("ñandú con espacios.epub")
    local.write_bytes(b"0123456789")
    with XteinkClient(device.base_url, timeout=2) as client:
        rate = client.upload_file(local, "/Libros/ñandú con espacios.epub")
    assert rate >= 0
    request = device.upload_requests[-1]
    assert request["field"] == "data"
    assert request["filename"] == "/Libros/ñandú con espacios.epub"
    assert request["size"] == 10
    assert "multipart/form-data" in request["content_type"]
    assert device.files["/Libros/ñandú con espacios.epub"] == b"0123456789"


def test_client_list_unicode(device):
    device.add_dir("/Libros")
    device.add_dir("/Libros/Ficción")
    device.add_file("/Libros/El niño.epub", b"x")
    with XteinkClient(device.base_url, timeout=2) as client:
        entries = client.list_dir("/Libros")
    assert [item["name"] for item in entries] == ["Ficción", "El niño.epub"]
    assert entries[0]["type"] == "dir"
    assert entries[1]["size"] == 1


def test_client_mkdir_rename_delete(device):
    device.add_dir("/Libros")
    with XteinkClient(device.base_url, timeout=2) as client:
        client.mkdir("/Libros/Nueva")
        assert "/Libros/Nueva" in device.dirs
        with pytest.raises(ConflictError):
            client.mkdir("/Libros/Nueva")

        device.add_file("/Libros/a.epub", b"A")
        client.rename("/Libros/a.epub", "/Libros/b.epub")
        assert "/Libros/b.epub" in device.files
        assert "/Libros/a.epub" not in device.files

        with pytest.raises(NotFoundError):
            client.rename("/Libros/no.epub", "/Libros/x.epub")

        client.delete("/Libros/b.epub", is_dir=False)
        assert "/Libros/b.epub" not in device.files


def test_client_delete_non_empty_dir(device):
    device.add_dir("/Libros")
    device.add_file("/Libros/a.epub", b"A")
    with XteinkClient(device.base_url, timeout=2) as client:
        with pytest.raises(DeviceError):
            client.delete("/Libros", is_dir=True)


def test_client_wifi_info_with_spaces(device):
    device.sta_text = "Mi red de casa 10.0.0.7"
    with XteinkClient(device.base_url, timeout=2) as client:
        assert client.wifi_info() == {"ssid": "Mi red de casa", "ip": "10.0.0.7"}


def test_client_wifi_info_failure(device):
    device.sta_text = "Connection failed"
    with XteinkClient(device.base_url, timeout=2) as client:
        with pytest.raises(WifiNotConnected):
            client.wifi_info()


def test_client_download_truncated_fails_and_cleans_part(device, tmp_path):
    device.add_file("/Libro.epub", b"0123456789")
    device.download_truncate = True
    out = tmp_path / "Libro.epub"
    with XteinkClient(device.base_url, timeout=2) as client:
        with pytest.raises(ProtocolError):
            client.download_file("/Libro.epub", out, expected_size=10)
    assert not out.exists()
    assert not (tmp_path / "Libro.epub.part").exists()


def test_client_open_missing(device):
    with XteinkClient(device.base_url, timeout=2) as client:
        with pytest.raises(NotFoundError):
            client.open_book("/No.epub")


def test_client_network_error():
    with XteinkClient("http://127.0.0.1:1", timeout=0.3) as client:
        with pytest.raises(NetworkError):
            client.get_status()


def test_client_upload_missing_parent(device):
    local = Path("a.epub")
    local.write_bytes(b"A")
    with XteinkClient(device.base_url, timeout=2) as client:
        with pytest.raises(DeviceError):
            client.upload_file(local, "/NoExiste/a.epub")
