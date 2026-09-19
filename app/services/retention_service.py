"""Prune the raw_json blob from old telemetry_raw rows.

The live database is not the archive - full-fidelity raw API payloads live
in the daily backups (data/backups/, see backup_service.py). This only clears
the (large, ~40KB/row) raw_json column on rows older than
``retention.raw_json_keep_days``, leaving every structured/derived column -
the ones the dashboard and ml/ actually read - untouched. raw_json itself is
deferred (never auto-loaded) and not part of ml/data_loading.py's column set,
so this has zero effect on dataset statistics or model training.

Runs daily, safely after the backup: as long as raw_json_keep_days is less
than backup.keep_days, every row about to be pruned here was already
captured whole by an earlier daily backup.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import update

from app.core.config import get_config
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.database.database import session_scope
from app.database.models import TelemetryRaw

logger = get_logger("retention")


def prune_raw_json(keep_days: int | None = None) -> dict[str, Any]:
    cfg = get_config()
    days = keep_days if keep_days is not None else int(cfg.get("retention.raw_json_keep_days", 3))
    cutoff = utc_now() - timedelta(days=days)
    with session_scope() as session:
        result = session.execute(
            update(TelemetryRaw)
            .where(TelemetryRaw.timestamp_utc < cutoff, TelemetryRaw.raw_json.isnot(None))
            .values(raw_json=None)
        )
        n = result.rowcount or 0
    if n:
        logger.info(
            "retention: cleared raw_json on %d row(s) older than %s (keep_days=%d)",
            n, cutoff.isoformat(), days,
        )
    return {"pruned_rows": n, "cutoff": cutoff.isoformat(), "keep_days": days}
