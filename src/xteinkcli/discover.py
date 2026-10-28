"""Discover the X4 on the local LAN (read-only)."""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .errors import UsageError

MAX_SCAN_HOSTS = 1024


def infer_local_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect() sends no packets; it only selects the interface.
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])
    except OSError as exc:
        raise UsageError(
            "Could not infer the local network.",
            hint="Specify the subnet manually: xteinkcli discover --subnet 192.168.1.0/24",
        ) from exc
    finally:
        sock.close()


def parse_private_subnet(value: str | None) -> ipaddress.IPv4Network:
    if value is None:
        value = f"{infer_local_ipv4()}/24"
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise UsageError(f"Invalid subnet: {value!r}.") from exc
    if network.version != 4:
        raise UsageError("Discovery only supports IPv4.")
    if not network.is_private:
        raise UsageError(
            f"For safety, only private networks are scanned, not {network}."
        )
    if network.num_addresses > MAX_SCAN_HOSTS:
        raise UsageError(
            f"Subnet {network} has {network.num_addresses} addresses; "
            f"the maximum is {MAX_SCAN_HOSTS}. Use a smaller subnet."
        )
    return network


def _probe(ip: str, *, port: int, timeout: float) -> dict[str, Any] | None:
    url = f"http://{ip}" + ("" if port == 80 else f":{port}") + "/status"
    try:
        response = httpx.get(
            url,
            timeout=httpx.Timeout(
                connect=timeout, read=timeout, write=timeout, pool=timeout
            ),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "xteinkcli/0.1", "Accept": "application/json"},
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # Avoid false positives: a stock X4 must expose several known fields.
    markers = sum(
        key in data
        for key in ("version", "device_type", "totalBytes", "usedBytes", "id")
    )
    if markers < 2:
        return None
    total = data.get("totalBytes")
    used = data.get("usedBytes")
    sd_present = data.get("sd")
    if sd_present is None:
        sd_present = data.get("isOk")
    return {
        "ip": ip,
        "port": port,
        "version": data.get("version"),
        "device_type": data.get("device_type"),
        "id": data.get("id"),
        "sd_present": sd_present,
        "total_bytes": total,
        "used_bytes": used,
    }


def scan_subnet(
    network: ipaddress.IPv4Network,
    *,
    port: int = 80,
    timeout: float = 0.4,
    workers: int = 64,
) -> list[dict[str, Any]]:
    workers = max(1, min(workers, 128))
    hosts = [str(host) for host in network.hosts()]
    found: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_probe, host, port=port, timeout=timeout): host
            for host in hosts
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                found.append(result)
    found.sort(key=lambda item: ipaddress.ip_address(item["ip"]))
    return found
