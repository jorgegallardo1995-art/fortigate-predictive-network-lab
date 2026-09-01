"""Time helpers.

UTC is the primary scientific reference for the whole dataset. Local time is
stored only as a secondary, human-friendly column. Every timestamp produced here
is timezone-aware.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def utc_now() -> datetime:
    """Current instant as a timezone-aware UTC ``datetime``."""
    return datetime.now(UTC)


def local_tz() -> ZoneInfo:
    """The configured local timezone (``TIMEZONE`` env var)."""
    return ZoneInfo(get_settings().timezone)


def to_local(dt: datetime) -> datetime:
    """Convert an aware ``datetime`` to the configured local timezone (tz-aware)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(local_tz())


def to_local_naive(dt: datetime) -> datetime:
    """Local wall-clock time as a NAIVE datetime.

    Used for the secondary ``timestamp_local`` column: the DB layer normalises
    every aware datetime to UTC on write, which would collapse a local timestamp
    back onto UTC. Storing it naive preserves the wall-clock digits; the
    accompanying ``timezone`` column records which zone they belong to.
    """
    return to_local(dt).replace(tzinfo=None)


def isoformat_utc(dt: datetime) -> str:
    """ISO-8601 string in UTC with a trailing ``Z`` (e.g. ``2026-09-01T12:00:00Z``)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def duration_ms(start: datetime, end: datetime) -> int:
    """Elapsed milliseconds between two instants (non-negative)."""
    return max(0, int((end - start).total_seconds() * 1000))
