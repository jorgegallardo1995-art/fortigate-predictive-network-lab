"""Structured, rotating logging.

A :class:`SecretFilter` scrubs anything that looks like the API token or an
``Authorization`` header from every record, so a stray ``logger.debug(response)``
can never leak credentials into ``logs/collector.log``.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from app.core.config import get_config, get_settings

_CONFIGURED = False

_TOKEN_PATTERNS = [
    re.compile(r"(Authorization\s*[:=]\s*Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(access_token\s*[:=]\s*)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?token\s*[:=]\s*)\S+", re.IGNORECASE),
]


class SecretFilter(logging.Filter):
    """Redact the configured API token and common credential patterns."""

    def __init__(self) -> None:
        super().__init__()
        token = get_settings().fortigate_api_token
        self._token = token if len(token) >= 6 else None

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if self._token and self._token in msg:
            msg = msg.replace(self._token, "***REDACTED***")
        for pat in _TOKEN_PATTERNS:
            msg = pat.sub(r"\1***REDACTED***", msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging() -> None:
    """Configure root logging once (console + rotating file)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = get_settings()
    cfg = get_config()
    settings.ensure_directories()

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    secret_filter = SecretFilter()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(secret_filter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        settings.logs_dir / cfg.get("logging.filename", "collector.log"),
        maxBytes=int(cfg.get("logging.max_bytes", 10 * 1024 * 1024)),
        backupCount=int(cfg.get("logging.backup_count", 10)),
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(secret_filter)
    root.addHandler(file_handler)

    # httpx/uvicorn access logs are noisy at INFO; keep them at WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, configuring logging on first use."""
    setup_logging()
    return logging.getLogger(name)
