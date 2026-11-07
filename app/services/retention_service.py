"""Prune the raw_json blob from old telemetry_raw rows.

The live database is not the archive - full-fidelity raw API payloads live
in the daily backups (data/backups/, see backup_service.py). This only clears
the (large, ~40KB/row) raw_json column on rows older than
``retention.raw_json_keep_days``, leaving every structured/derived column -
the ones the dashboard and ml/ actually read - untouched. raw_json itself is
deferred (never auto-loaded) and not part of ml/data_loading.py's column set,
so this has zero effect on dataset statistics or model training.

Runs daily, after the backup. It never clears a row newer than the start of
the newest valid backup (backup_service.latest_backup_time), so every
payload it removes is already inside a backup - even when backups have been
failing (e.g. disk full). With no valid backup at all it prunes nothing.
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
    from app.services.backup_service import latest_backup_time

    cfg = get_config()
    days = keep_days if keep_days is not None else int(cfg.get("retention.raw_json_keep_days", 3))
    cutoff = utc_now() - timedelta(days=days)
    covered_until = latest_backup_time()
    if covered_until is None:
        logger.warning("retention skipped: no valid backup yet, raw_json kept on every row")
        return {"pruned_rows": 0, "cutoff": None, "keep_days": days, "reason": "no-backup"}
    if covered_until < cutoff:
        logger.warning(
            "retention: newest valid backup is from %s - not clearing raw_json newer than that",
            covered_until.isoformat(),
        )
        cutoff = covered_until
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
