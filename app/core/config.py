"""Configuration management.

Two layers, merged at startup:

* **Secrets & environment** -> ``.env`` file / real environment variables, parsed
  by :class:`Settings` (pydantic-settings).
* **Behaviour & tunables** -> ``config/config.yaml``, parsed into :class:`YamlConfig`.

Nothing here ever logs the API token. Access the singletons through
:func:`get_settings` and :func:`get_config`.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.version import PROJECT_ROOT


class Settings(BaseSettings):
    """Environment-driven settings (secrets, hosts, mode, paths)."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- FortiGate REST API ---
    fortigate_host: str = "10.254.0.254"
    fortigate_port: int = 443
    fortigate_api_token: str = ""
    # Separate WRITE token for controlled fault injection (app/services/
    # wan_control_service.py). Empty = fault injection disabled. NEVER the
    # read token above.
    fortigate_control_token: str = ""
    fortigate_vdom: str = "root"
    verify_ssl: bool = False
    fortigate_timeout: float = 10.0
    fortigate_max_retries: int = 3

    # --- Mode ---
    mock_mode: bool = True
    # Start the background acquisition scheduler with the web app. Set false to
    # run the dashboard only (e.g. against an existing database) or in tests.
    run_scheduler: bool = True

    # --- Paths ---
    data_dir: Path = Path("data")
    database_url: str = ""

    # --- Web ---
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000

    # --- Research ---
    timezone: str = "America/Santiago"
    anonymize_serial: bool = True

    # --- Logging ---
    log_level: str = "INFO"

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, v: Path) -> Path:
        """Resolve a relative ``DATA_DIR`` against the project root."""
        p = Path(v).expanduser()
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def fortigate_base_url(self) -> str:
        """Base URL of the FortiOS REST API (no trailing slash)."""
        return f"https://{self.fortigate_host}:{self.fortigate_port}/api/v2"

    @property
    def database_path(self) -> Path:
        """Filesystem path of the SQLite database inside ``data_dir``."""
        return self.data_dir / "fortigate.db"

    @property
    def effective_database_url(self) -> str:
        """SQLAlchemy URL. ``DATABASE_URL`` wins; otherwise SQLite in ``data_dir``."""
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.database_path}"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return PROJECT_ROOT / "logs"

    def ensure_directories(self) -> None:
        """Create the runtime directories if they do not exist."""
        for d in (self.data_dir, self.exports_dir, self.backups_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def safe_dict(self) -> dict[str, Any]:
        """Return settings with the token redacted - safe for logs / metadata."""
        data = self.model_dump()
        data["fortigate_api_token"] = "***REDACTED***" if self.fortigate_api_token else ""
        data["fortigate_control_token"] = (
            "***REDACTED***" if self.fortigate_control_token else ""
        )
        data["data_dir"] = str(self.data_dir)
        return data


class YamlConfig:
    """Thin read-only accessor over ``config/config.yaml``.

    Use :meth:`get` with a dotted path, e.g. ``config.get("collector.sampling_interval_seconds")``.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        with path.open("r", encoding="utf-8") as fh:
            self._data: dict[str, Any] = yaml.safe_load(fh) or {}

    def get(self, dotted_key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    # --- typed convenience accessors used across the app ---
    @property
    def sampling_interval(self) -> int:
        return int(self.get("collector.sampling_interval_seconds", 60))

    @property
    def feature_windows(self) -> list[int]:
        return list(self.get("features.windows_minutes", [5, 10, 15]))

    @property
    def sla_targets(self) -> dict[str, dict[str, str]]:
        return dict(self.get("sla_targets", {}))

    @property
    def endpoints(self) -> dict[str, str]:
        return dict(self.get("fortios_endpoints", {}))


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton."""
    return Settings()


@functools.lru_cache(maxsize=1)
def get_config() -> YamlConfig:
    """Return the cached :class:`YamlConfig` singleton.

    Honours ``config/config.local.yaml`` when present (git-ignored override).
    """
    local = PROJECT_ROOT / "config" / "config.local.yaml"
    default = PROJECT_ROOT / "config" / "config.yaml"
    return YamlConfig(local if local.exists() else default)
