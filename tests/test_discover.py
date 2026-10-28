from __future__ import annotations

import ipaddress

import pytest

from xteinkcli.discover import parse_private_subnet, scan_subnet
from xteinkcli.errors import UsageError


def test_scan_finds_mock_device(device):
    network = ipaddress.ip_network("127.0.0.0/30")
    found = scan_subnet(network, port=device.port, timeout=0.5, workers=2)
    assert [item["ip"] for item in found] == ["127.0.0.1"]
    assert found[0]["version"] == "XT V5.6.33"


def test_scan_ignores_non_xteink():
    network = ipaddress.ip_network("127.0.0.0/30")
    # Without a server there are no results and no exception is raised.
    assert scan_subnet(network, port=1, timeout=0.15, workers=2) == []


def test_parse_private_subnet_rejects_public():
    with pytest.raises(UsageError):
        parse_private_subnet("8.8.8.0/24")


def test_parse_private_subnet_limits_size():
    with pytest.raises(UsageError):
        parse_private_subnet("10.0.0.0/8")
