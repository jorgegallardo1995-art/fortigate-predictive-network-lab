"""Version constants and runtime provenance helpers.

``COLLECTOR_VERSION`` is written into every telemetry row and into the research
metadata, so bump it whenever the acquisition logic changes in a way that could
affect the dataset. ``SCHEMA_VERSION`` tracks the database schema independently.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

COLLECTOR_VERSION = "0.1.0"
"""Semantic version of the collector application."""

SCHEMA_VERSION = "1"
"""Database schema version. Increment on every Alembic migration."""

PROJECT_ROOT = Path(__file__).resolve().parents[2]
"""Absolute path to the repository root (…/fortigate_predictive_collector)."""


def get_git_commit(short: bool = True) -> str | None:
    """Return the current git commit hash, or ``None`` if unavailable.

    Used for reproducibility metadata. Never raises.
    """
    try:
        args = ["git", "-C", str(PROJECT_ROOT), "rev-parse"]
        args += ["--short", "HEAD"] if short else ["HEAD"]
        out = subprocess.run(
            args, capture_output=True, text=True, timeout=5, check=False
        )
        commit = out.stdout.strip()
        return commit or None
    except (OSError, subprocess.SubprocessError):
        return None
