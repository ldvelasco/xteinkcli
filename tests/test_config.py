from __future__ import annotations

from pathlib import Path

import pytest

from xteinkcli.config import AppConfig, load_config
from xteinkcli.errors import UsageError


def test_load_config_and_env_override(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'ip = "192.168.1.50"\nport = 8080\ntimeout = 12\nupload_timeout = 300\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XTEINK_IP", "192.168.1.60")
    config = load_config(str(config_path))
    assert config.ip == "192.168.1.60"
    assert config.port == 8080
    assert config.timeout == 12
    assert config.upload_timeout == 300
    assert config.ip_source == "XTEINK_IP environment variable"


def test_cli_override_wins():
    config = AppConfig(ip="192.168.1.50", ip_source="config")
    updated = config.with_overrides(ip="192.168.1.99", port=81)
    assert updated.ip == "192.168.1.99"
    assert updated.port == 81
    assert updated.ip_source == "argument --ip"


def test_missing_explicit_config_is_error(tmp_path):
    with pytest.raises(UsageError):
        load_config(str(tmp_path / "no-existe.toml"))


def test_invalid_ip_and_port(tmp_path, monkeypatch):
    invalid_ip = tmp_path / "ip.toml"
    invalid_ip.write_text('ip = "http://192.168.1.50"\n', encoding="utf-8")
    with pytest.raises(UsageError):
        load_config(str(invalid_ip))

    valid = tmp_path / "valid.toml"
    valid.write_text('ip = "192.168.1.50"\n', encoding="utf-8")
    monkeypatch.setenv("XTEINK_PORT", "99999")
    with pytest.raises(UsageError):
        load_config(str(valid))


def test_base_url():
    assert AppConfig(ip="192.168.1.50").base_url == "http://192.168.1.50"
    assert (
        AppConfig(ip="192.168.1.50", port=8080).base_url
        == "http://192.168.1.50:8080"
    )
    with pytest.raises(UsageError):
        _ = AppConfig().base_url


def test_relative_existing_config_is_found(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('ip = "10.0.0.2"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = load_config(None)
    assert config.ip == "10.0.0.2"
    assert config.config_path is not None
    assert config.config_path.name == "config.toml"
    assert config.config_path.is_file()
