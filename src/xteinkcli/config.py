"""Configuration loading and validation (no secrets)."""

from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .errors import UsageError

APP_DIR_NAME = "xteinkcli"
DEFAULT_PORT = 80
DEFAULT_TIMEOUT = 10.0
DEFAULT_UPLOAD_TIMEOUT = 600.0
DEFAULT_BLE_PREFIX = "XTEPD_BLE_"

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class AppConfig:
    ip: str | None = None
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT
    upload_timeout: float = DEFAULT_UPLOAD_TIMEOUT
    ble_name_prefix: str = DEFAULT_BLE_PREFIX
    config_path: Path | None = None
    ip_source: str = "none"

    def with_overrides(
        self,
        *,
        ip: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
        upload_timeout: float | None = None,
    ) -> "AppConfig":
        updates: dict[str, object] = {}
        if ip is not None:
            updates["ip"] = validate_host(ip)
            updates["ip_source"] = "argument --ip"
        if port is not None:
            updates["port"] = validate_port(port)
        if timeout is not None:
            updates["timeout"] = validate_timeout(timeout, "timeout")
        if upload_timeout is not None:
            updates["upload_timeout"] = validate_timeout(
                upload_timeout, "upload_timeout"
            )
        return replace(self, **updates) if updates else self

    @property
    def base_url(self) -> str:
        if not self.ip:
            raise UsageError(
                "No X4 IP is configured.",
                hint=(
                    "Use --ip, set XTEINK_IP, or create config.toml. "
                    "You can also try: xteinkcli discover"
                ),
            )
        host = f"[{self.ip}]" if ":" in self.ip else self.ip
        if self.port == 80:
            return f"http://{host}"
        return f"http://{host}:{self.port}"


def default_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME / "config.toml"
    return Path.home() / ".config" / APP_DIR_NAME / "config.toml"


def resolve_config_path(explicit: str | None) -> Path | None:
    """Return the first existing configuration file.

    Order: --config, XTEINK_CONFIG, ./config.toml, %APPDATA%\\xteinkcli\\config.toml.
    An explicit --config that does not exist is an error, so typos are not ignored.
    """

    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise UsageError(f"Configuration file does not exist: {path}")
        return path

    env_path = os.environ.get("XTEINK_CONFIG")
    if env_path:
        path = Path(env_path).expanduser()
        if not path.is_file():
            raise UsageError(
                f"XTEINK_CONFIG points to a missing file: {path}"
            )
        return path

    local = Path.cwd() / "config.toml"
    if local.is_file():
        return local

    appdata = default_config_path()
    if appdata.is_file():
        return appdata

    return None


def load_config(explicit: str | None = None) -> AppConfig:
    path = resolve_config_path(explicit)
    values: dict[str, object] = {}
    if path is not None:
        try:
            with path.open("rb") as handle:
                loaded = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise UsageError(f"config.toml is not valid TOML: {exc}") from exc
        except OSError as exc:
            raise UsageError(f"Could not read {path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise UsageError("config.toml must contain a TOML table.")
        values = loaded

    ip_value = values.get("ip")
    ip = None
    if ip_value is not None:
        if not isinstance(ip_value, str):
            raise UsageError("The 'ip' key in config.toml must be a string.")
        ip = validate_host(ip_value)
    port = validate_port(values.get("port", DEFAULT_PORT))
    timeout = validate_timeout(values.get("timeout", DEFAULT_TIMEOUT), "timeout")
    upload_timeout = validate_timeout(
        values.get("upload_timeout", DEFAULT_UPLOAD_TIMEOUT), "upload_timeout"
    )
    prefix = values.get("ble_name_prefix", DEFAULT_BLE_PREFIX)
    if not isinstance(prefix, str) or not prefix.strip():
        raise UsageError("'ble_name_prefix' must be a non-empty string.")

    config = AppConfig(
        ip=ip,
        port=port,
        timeout=timeout,
        upload_timeout=upload_timeout,
        ble_name_prefix=prefix.strip(),
        config_path=path,
        ip_source=str(path) if path is not None else "none",
    )

    env_ip = os.environ.get("XTEINK_IP")
    if env_ip:
        config = replace(
            config, ip=validate_host(env_ip), ip_source="XTEINK_IP environment variable"
        )

    env_port = os.environ.get("XTEINK_PORT")
    if env_port:
        config = replace(config, port=validate_port(env_port))

    env_timeout = os.environ.get("XTEINK_TIMEOUT")
    if env_timeout:
        config = replace(
            config, timeout=validate_timeout(env_timeout, "XTEINK_TIMEOUT")
        )

    env_upload = os.environ.get("XTEINK_UPLOAD_TIMEOUT")
    if env_upload:
        config = replace(
            config,
            upload_timeout=validate_timeout(env_upload, "XTEINK_UPLOAD_TIMEOUT"),
        )

    return config


def validate_host(value: object) -> str:
    if not isinstance(value, str):
        raise UsageError("The IP must be a string.")
    host = value.strip()
    if not host:
        raise UsageError("The IP cannot be empty.")
    if any(ch in host for ch in "/\\?#@") or "://" in host:
        raise UsageError(
            f"Invalid IP/host: {host!r}. Provide only the IP or hostname, without http://."
        )
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not _HOSTNAME_RE.fullmatch(host) or ".." in host:
        raise UsageError(f"Invalid IP/host: {host!r}.")
    return host


def validate_port(value: object) -> int:
    try:
        port = int(str(value))
    except (TypeError, ValueError) as exc:
        raise UsageError(f"Invalid port: {value!r}.") from exc
    if not 1 <= port <= 65535:
        raise UsageError(f"Port out of range: {port}.")
    return port


def validate_timeout(value: object, name: str) -> float:
    try:
        timeout = float(str(value))
    except (TypeError, ValueError) as exc:
        raise UsageError(f"Invalid {name}: {value!r}.") from exc
    if not 0.1 <= timeout <= 3600:
        raise UsageError(f"{name} must be between 0.1 and 3600 seconds.")
    return timeout
