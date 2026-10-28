"""BLE (Nordic UART) client for the stock X4.

Protocol documented through reverse engineering for XTOS V5.6.33. This module
implements the framing and commands with a published contract, but it has **not
been hardware-validated in this project**. Command e=2 is not exposed because it
returns the saved Wi-Fi password in plaintext.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from .errors import DeviceError, ProtocolError, UsageError

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
FRAME_HEADER = b"\x03\x02\x03"
MAX_WRITE_BYTES = 512

_STATUS_LABELS = {
    0: "ok",
    1: "no SD card or error",
    2: "not found or invalid parameter",
    3: "operation failed",
    4: "unsupported file type",
}
_SECRET_KEYS = {"p", "password", "pass", "sta_password"}


class FrameReassembler:
    """Reassemble BLE responses with header [03 02 03 lenHi lenLo].

    Notifications may split the header or the JSON. An unframed JSON response is
    also accepted for diagnostics.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._expected: int | None = None
        self._raw_mode = False

    def reset(self) -> None:
        self._buffer.clear()
        self._expected = None
        self._raw_mode = False

    def _try_raw_json(self) -> bytes | None:
        try:
            payload = bytes(self._buffer)
            json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        self.reset()
        return payload

    def feed(self, data: bytes) -> bytes | None:
        if data:
            self._buffer.extend(data)
        if not self._buffer:
            return None

        if self._raw_mode:
            return self._try_raw_json()

        if self._expected is None:
            if self._buffer[:1] == b"{":
                self._raw_mode = True
                return self._try_raw_json()
            if len(self._buffer) < len(FRAME_HEADER):
                return None
            if bytes(self._buffer[:3]) != FRAME_HEADER:
                raise ProtocolError(
                    "BLE response with unknown header.",
                    hint=(
                        "Expected [03 02 03 lenHi lenLo]. "
                        f"Received: {bytes(self._buffer[:8]).hex(' ')}"
                    ),
                )
            if len(self._buffer) < 5:
                return None
            self._expected = (self._buffer[3] << 8) | self._buffer[4]
            del self._buffer[:5]

        if len(self._buffer) < self._expected:
            return None
        payload = bytes(self._buffer[: self._expected])
        del self._buffer[: self._expected]
        self._expected = None
        return payload


def sanitize_reply(reply: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Remove secrets from a BLE response before displaying it."""

    cleaned: dict[str, Any] = {}
    redacted: list[str] = []
    for key, value in reply.items():
        if key in _SECRET_KEYS:
            redacted.append(key)
            continue
        if isinstance(value, dict):
            nested, nested_redacted = sanitize_reply(value)
            cleaned[key] = nested
            redacted.extend(f"{key}.{item}" for item in nested_redacted)
        else:
            cleaned[key] = value
    return cleaned, redacted


@dataclass(frozen=True)
class BleDevice:
    name: str
    address: str
    rssi: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "address": self.address, "rssi": self.rssi}


def _require_bleak() -> Any:
    try:
        import bleak
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DeviceError(
            "BLE is unavailable: the 'bleak' dependency is missing.",
            hint="Install with: .\\.venv\\Scripts\\python.exe -m pip install -e .",
        ) from exc
    return bleak


async def scan_devices(
    *, timeout: float = 8.0, prefix: str = "XTEPD_BLE_"
) -> list[BleDevice]:
    bleak = _require_bleak()
    try:
        discovered = await bleak.BleakScanner.discover(timeout=timeout)
    except Exception as exc:
        raise DeviceError(
            f"Could not scan BLE: {type(exc).__name__}: {exc}",
            hint=(
                "Check that Windows Bluetooth is enabled and the reader is powered "
                "on. On the X4, enter the transfer screen or a menu that keeps BLE "
                "active."
            ),
        ) from exc

    needle = prefix.casefold()
    devices: list[BleDevice] = []
    for item in discovered:
        name = getattr(item, "name", None) or ""
        if not name.casefold().startswith(needle):
            continue
        address = getattr(item, "address", None) or ""
        rssi = getattr(item, "rssi", None)
        devices.append(BleDevice(name=name, address=address, rssi=rssi))
    devices.sort(key=lambda item: (-(item.rssi or -999), item.name))
    return devices


class BleSession:
    """BLE session against the X4 Nordic UART service."""

    def __init__(
        self,
        address: str,
        *,
        timeout: float = 20.0,
        connect_timeout: float = 20.0,
    ) -> None:
        self.address = address
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self._client: Any = None
        self._reassembler = FrameReassembler()
        self._response: asyncio.Future[bytes] | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "BleSession":
        bleak = _require_bleak()
        try:
            self._client = bleak.BleakClient(
                self.address, timeout=self.connect_timeout
            )
            await self._client.connect()
            services = self._client.services
            if services.get_characteristic(NUS_WRITE_UUID) is None:
                raise ProtocolError(
                    "The BLE device does not expose the NUS write characteristic."
                )
            if services.get_characteristic(NUS_NOTIFY_UUID) is None:
                raise ProtocolError(
                    "The BLE device does not expose the NUS notify characteristic."
                )
            await self._client.start_notify(NUS_NOTIFY_UUID, self._on_notify)
        except ProtocolError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise DeviceError(
                f"Could not connect over BLE to {self.address}: "
                f"{type(exc).__name__}: {exc}",
                hint=(
                    "Move the reader closer to the PC, check that BLE is active, "
                    "and make sure no other app is connected to the X4."
                ),
            ) from exc
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is None:
            return
        client, self._client = self._client, None
        try:
            if getattr(client, "is_connected", False):
                try:
                    await client.stop_notify(NUS_NOTIFY_UUID)
                except Exception:
                    pass
                await client.disconnect()
        except Exception:
            pass

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        try:
            payload = self._reassembler.feed(bytes(data))
        except Exception as exc:
            if self._response is not None and not self._response.done():
                self._response.set_exception(exc)
            return
        if payload is not None and self._response is not None:
            if not self._response.done():
                self._response.set_result(payload)

    async def request(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        if self._client is None or not getattr(self._client, "is_connected", False):
            raise DeviceError("The BLE session is not connected.")
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(raw) > MAX_WRITE_BYTES:
            raise UsageError(
                f"The BLE command is {len(raw)} bytes and exceeds the documented "
                f"maximum of {MAX_WRITE_BYTES}."
            )
        wait_seconds = timeout if timeout is not None else self.timeout
        async with self._lock:
            self._reassembler.reset()
            loop = asyncio.get_running_loop()
            self._response = loop.create_future()
            try:
                await self._client.write_gatt_char(
                    NUS_WRITE_UUID, raw, response=True
                )
                reply_raw = await asyncio.wait_for(
                    self._response, timeout=wait_seconds
                )
            except asyncio.TimeoutError as exc:
                raise DeviceError(
                    f"The X4 did not respond over BLE within {wait_seconds:g} s.",
                    hint=(
                        "It may have disconnected, be busy, or not expose the "
                        "documented protocol on this firmware version."
                    ),
                ) from exc
            except ProtocolError:
                raise
            except Exception as exc:
                raise DeviceError(
                    f"BLE error during the operation: {type(exc).__name__}: {exc}"
                ) from exc
            finally:
                self._response = None

        try:
            reply = json.loads(reply_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(
                "The BLE response is not valid JSON.",
                hint=f"Bytes: {reply_raw[:80].hex(' ')}",
            ) from exc
        if not isinstance(reply, dict):
            raise ProtocolError("The BLE response is not a JSON object.")
        if "error" in reply:
            # Do not echo the content: a malformed response could contain
            # command data (for example, a Wi-Fi password).
            raise DeviceError(
                "The X4 returned a BLE error.",
                hint=(
                    "The command was not accepted. Check the firmware version and "
                    "the command format; the body is hidden for security."
                ),
            )
        return reply

    # ------------------------------------------------------------------
    # Documented commands
    # ------------------------------------------------------------------
    async def scan_wifi(self) -> list[dict[str, Any]]:
        reply = await self.request({"e": 0})
        items = reply.get("l")
        if not isinstance(items, list):
            raise ProtocolError("Wi-Fi scan: the BLE response does not contain 'l'.")
        result: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("s"), str):
                result.append({"ssid": item["s"], "rssi": item.get("r")})
        return result

    async def connect_wifi(self, ssid: str, password: str) -> dict[str, Any]:
        reply = await self.request({"e": 1, "s": ssid, "p": password})
        self._require_status(reply, "Connect to Wi-Fi", ok={0})
        return sanitize_reply(reply)[0]

    async def device_id(self) -> dict[str, Any]:
        reply = await self.request({"e": 3})
        if not isinstance(reply.get("id"), str):
            raise ProtocolError("Identify device: missing 'id'.")
        return sanitize_reply(reply)[0]

    async def status(self) -> dict[str, Any]:
        reply = await self.request({"e": 9})
        cleaned, redacted = sanitize_reply(reply)
        if redacted:
            cleaned["redacted_fields"] = redacted
        return cleaned

    async def open_book(self, path: str) -> dict[str, Any]:
        reply = await self.request({"e": 10, "p": path})
        self._require_status(reply, "Open book over BLE", ok={0})
        return {
            "path": path,
            "status": 0,
            "reboot_expected": True,
            "note": "The X4 validates the book and reboots into the reader.",
        }

    async def reading_times(self) -> list[dict[str, Any]]:
        reply = await self.request({"e": 11})
        self._require_status(reply, "Get reading time", ok={0})
        items = reply.get("l", [])
        if not isinstance(items, list):
            raise ProtocolError("Reading time: 'l' is not a list.")
        return [
            {"name": item.get("n"), "minutes": item.get("t")}
            for item in items
            if isinstance(item, dict)
        ]

    async def set_wifi(self, enabled: bool) -> dict[str, Any]:
        reply = await self.request({"e": 14, "on": enabled})
        self._require_status(reply, "Cambiar Wi-Fi", ok={0})
        cleaned, redacted = sanitize_reply(reply)
        if redacted:
            cleaned["redacted_fields"] = redacted
        return cleaned

    async def set_nickname(self, nickname: str) -> dict[str, Any]:
        reply = await self.request({"e": 15, "n": nickname})
        self._require_status(reply, "Set device name", ok={0})
        return sanitize_reply(reply)[0]

    async def get_nickname(self) -> dict[str, Any]:
        reply = await self.request({"e": 16})
        self._require_status(reply, "Get device name", ok={0})
        return sanitize_reply(reply)[0]

    @staticmethod
    def _require_status(
        reply: dict[str, Any], action: str, *, ok: set[int]
    ) -> None:
        status = reply.get("s")
        if isinstance(status, int) and not isinstance(status, bool) and status in ok:
            return
        if isinstance(status, int) and not isinstance(status, bool):
            label = _STATUS_LABELS.get(status, "unknown code")
            raise DeviceError(
                f"{action}: the X4 returned s={status} ({label}).",
                hint="Check the path, SD card, and firmware version.",
            )
        raise ProtocolError(f"{action}: the BLE response does not contain an 's' status.")


async def wait_for_scan(
    *, timeout: float, prefix: str
) -> list[BleDevice]:
    return await scan_devices(timeout=timeout, prefix=prefix)


def scan_devices_sync(
    *, timeout: float = 8.0, prefix: str = "XTEPD_BLE_"
) -> list[BleDevice]:
    return asyncio.run(scan_devices(timeout=timeout, prefix=prefix))


def run_ble(coro: Any) -> Any:
    """Run a BLE coroutine from the synchronous CLI."""

    return asyncio.run(coro)
