from __future__ import annotations

import json

import pytest

from xteinkcli.ble import (
    FRAME_HEADER,
    BleSession,
    FrameReassembler,
    sanitize_reply,
)
from xteinkcli.errors import DeviceError, ProtocolError


def _frame(payload: dict) -> bytes:
    data = json.dumps(payload, separators=(",", ":")).encode()
    return FRAME_HEADER + len(data).to_bytes(2, "big") + data


def test_frame_reassembly_split_everywhere():
    frame = _frame({"e": 10, "s": 0})
    for split in range(1, len(frame)):
        parser = FrameReassembler()
        assert parser.feed(frame[:split]) is None
        assert parser.feed(frame[split:]) == frame[5:]


def test_frame_reassembly_byte_by_byte():
    frame = _frame({"e": 16, "s": 0, "n": "Mi X4"})
    parser = FrameReassembler()
    result = None
    for byte in frame:
        result = parser.feed(bytes([byte])) or result
    assert result == frame[5:]


def test_frame_reassembly_raw_json_fallback():
    parser = FrameReassembler()
    raw = b'{"s":0,"n":"X4"}'
    assert parser.feed(raw[:4]) is None
    assert parser.feed(raw[4:]) == raw


def test_frame_reassembly_bad_header():
    parser = FrameReassembler()
    with pytest.raises(ProtocolError):
        parser.feed(b"\x01\x02\x03\x00\x02{}")


def test_sanitize_reply_removes_password():
    cleaned, redacted = sanitize_reply(
        {"e": 9, "s": "Red", "p": "test-password", "on": True}
    )
    assert "p" not in cleaned
    assert cleaned["s"] == "Red"
    assert redacted == ["p"]


class FakeBleClient:
    def __init__(self, frames: list[bytes]) -> None:
        self.is_connected = True
        self.frames = frames
        self.sent: list[bytes] = []
        self.session: BleSession | None = None

    async def write_gatt_char(self, uuid, data, response=True):
        self.sent.append(bytes(data))
        assert self.session is not None
        for frame in self.frames:
            # Simulate 7-byte notifications to split header and JSON.
            for index in range(0, len(frame), 7):
                self.session._on_notify(None, bytearray(frame[index : index + 7]))


def test_session_request_writes_json_and_reassembles():
    import asyncio

    fake = FakeBleClient([_frame({"e": 3, "id": "abc"})])
    session = BleSession("AA:BB")
    session._client = fake
    fake.session = session
    reply = asyncio.run(session.request({"e": 3}))
    assert reply == {"e": 3, "id": "abc"}
    assert fake.sent == [b'{"e":3}']


def test_session_status_redacts_password():
    import asyncio

    fake = FakeBleClient(
        [_frame({"e": 9, "s": "MiRed", "p": "test-password", "on": True})]
    )
    session = BleSession("AA:BB")
    session._client = fake
    fake.session = session
    reply = asyncio.run(session.status())
    assert "p" not in reply
    assert reply["redacted_fields"] == ["p"]


def test_session_open_book_status_codes():
    import asyncio

    fake = FakeBleClient([_frame({"e": 10, "s": 4})])
    session = BleSession("AA:BB")
    session._client = fake
    fake.session = session
    with pytest.raises(DeviceError):
        asyncio.run(session.open_book("/Libro.epub"))
