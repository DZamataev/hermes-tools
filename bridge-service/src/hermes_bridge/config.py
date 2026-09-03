"""Configuration for the Hermes OpenWebUI bridge."""

import os
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    api_server_key: str
    openwebui_api_key: str
    bridge_secret: str
    data_dir: Path
    hermes_base_url: str = "http://host.docker.internal:8642"
    openwebui_base_url: str = "http://open-webui:8080"
    bridge_host_port: int = 8787
    log_level: str = "info"
    sync_interval_seconds: float = 30.0
    connector_heartbeat_timeout_seconds: float = 20.0

    @property
    def database_path(self) -> Path:
        return self.data_dir / "bridge.sqlite3"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env

        def value(name: str, default: str | None = None) -> str:
            raw = values.get(name, default)
            if raw is None:
                raise ValueError(f"{name} is required")
            return raw.strip()

        def optional(name: str, default: str) -> str:
            return value(name, default) or default

        def required(name: str) -> str:
            result = value(name)
            if not result:
                raise ValueError(f"{name} is required")
            return result

        def decimal(name: str, default: str) -> float:
            raw = optional(name, default)
            try:
                result = float(raw)
            except ValueError as error:
                raise ValueError(f"{name} must be a number") from error
            if not isfinite(result) or result <= 0:
                raise ValueError(f"{name} must be greater than zero")
            return result

        bridge_secret = required("HERMES_BRIDGE_SECRET")
        if len(bridge_secret) < 32:
            raise ValueError("HERMES_BRIDGE_SECRET must be at least 32 characters")

        host_port_raw = optional("BRIDGE_HOST_PORT", "8787")
        try:
            bridge_host_port = int(host_port_raw)
        except ValueError as error:
            raise ValueError("BRIDGE_HOST_PORT must be an integer from 1 to 65535") from error
        if not 1 <= bridge_host_port <= 65535:
            raise ValueError("BRIDGE_HOST_PORT must be from 1 to 65535")

        return cls(
            api_server_key=required("API_SERVER_KEY"),
            openwebui_api_key=required("OPENWEBUI_API_KEY"),
            bridge_secret=bridge_secret,
            data_dir=Path(optional("BRIDGE_DATA_DIR", "/data")),
            hermes_base_url=optional("HERMES_BASE_URL", "http://host.docker.internal:8642"),
            openwebui_base_url=optional("OPENWEBUI_BASE_URL", "http://open-webui:8080"),
            bridge_host_port=bridge_host_port,
            log_level=optional("BRIDGE_LOG_LEVEL", "info"),
            sync_interval_seconds=decimal("BRIDGE_SYNC_INTERVAL_SECONDS", "30"),
            connector_heartbeat_timeout_seconds=decimal(
                "CONNECTOR_HEARTBEAT_TIMEOUT_SECONDS", "20"
            ),
        )
