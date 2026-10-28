"""xteinkcli domain errors and exit codes."""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_DEVICE = 4
EXIT_CONFIRM = 5
EXIT_NETWORK = 6


class XteinkError(Exception):
    """Controlled error the CLI can present without a traceback."""

    exit_code = EXIT_ERROR
    category = "error"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "category": self.category,
            "message": self.message,
        }
        if self.hint:
            data["hint"] = self.hint
        return data


class UsageError(XteinkError):
    exit_code = EXIT_USAGE
    category = "usage"


class ConflictError(XteinkError):
    exit_code = EXIT_CONFLICT
    category = "conflict"


class NotFoundError(XteinkError):
    exit_code = EXIT_CONFLICT
    category = "not_found"


class DeviceError(XteinkError):
    exit_code = EXIT_DEVICE
    category = "device"


class ProtocolError(DeviceError):
    category = "protocol"


class ConfirmationRequired(XteinkError):
    exit_code = EXIT_CONFIRM
    category = "confirmation"


class NetworkError(XteinkError):
    exit_code = EXIT_NETWORK
    category = "network"


class WifiNotConnected(DeviceError):
    category = "wifi_not_connected"
