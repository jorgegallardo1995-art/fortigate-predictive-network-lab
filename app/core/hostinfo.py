"""Host (collector machine) facts used to classify collection gaps.

Linux-only details degrade to ``None`` on macOS (dev), which is fine - gap
classification only matters on the production Pi.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path


def host_uptime_seconds() -> float | None:
    """Seconds since the host booted, or ``None`` (non-Linux)."""
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def host_boot_time() -> datetime | None:
    """Absolute UTC instant the host last booted, or ``None``.

    Prefers ``/proc/stat`` ``btime`` (a fixed epoch that does not drift); falls
    back to ``now - uptime``.
    """
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return datetime.fromtimestamp(int(line.split()[1]), UTC)
    except (OSError, ValueError, IndexError):
        pass
    up = host_uptime_seconds()
    return datetime.now(UTC) - timedelta(seconds=up) if up is not None else None
