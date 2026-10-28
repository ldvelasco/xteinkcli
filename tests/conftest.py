from __future__ import annotations

import os

import pytest

from mock_device import MockXteink


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch, tmp_path):
    for name in (
        "XTEINK_IP",
        "XTEINK_PORT",
        "XTEINK_TIMEOUT",
        "XTEINK_UPLOAD_TIMEOUT",
        "XTEINK_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def device():
    mock = MockXteink().start()
    try:
        yield mock
    finally:
        mock.stop()


def cli_args(device: MockXteink, *args: str) -> list[str]:
    return [
        *args,
        "--ip",
        "127.0.0.1",
        "--port",
        str(device.port),
        "--json",
    ]
