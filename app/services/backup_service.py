"""Automatic SQLite backups.

A consistent copy is made with the SQLite online-backup API (not ``cp``) so it is
safe while the collector is writing under WAL. The active database is never
touched. Backups older than ``backup.keep_days`` are pruned. Every backup is
written to the log; the network-event timeline is deliberately not spammed.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_config, get_settings
from app.core.logging import get_logger

logger = get_logger("backup")


def run_backup() -> dict[str, Any]:
    """Create today's backup and prune old ones. Returns a small result dict.

    Writes to a ``.tmp`` file and atomically renames it into place only after
    a full, clean copy - a service restart mid-backup (e.g. the watchdog
    firing) must never leave a partial/corrupt file under the dated name,
    since that both misrepresents that day's backup and (previously) made
    the exception skip pruning for every later run too.
    """
    settings = get_settings()
    cfg = get_config()

    if not settings.effective_database_url.startswith("sqlite"):
        logger.info("backup skipped: non-SQLite database (use pg_dump / managed backups)")
        return {"skipped": True, "reason": "non-sqlite"}

    src = settings.database_path
    if not src.exists():
        logger.warning("backup skipped: database file %s does not exist yet", src)
        return {"skipped": True, "reason": "no-db"}

    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    prefix = cfg.get("backup.filename_prefix", "fortigate_")
    dest = settings.backups_dir / f"{prefix}{date.today().isoformat()}.db"
    tmp_dest = settings.backups_dir / f"{prefix}{date.today().isoformat()}.db.tmp"
    tmp_dest.unlink(missing_ok=True)

    result: dict[str, Any] = {"skipped": False}
    started = datetime.now()
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        target = sqlite3.connect(tmp_dest)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        tmp_dest.replace(dest)  # atomic: dest only ever exists as a complete copy

        size = dest.stat().st_size
        took = (datetime.now() - started).total_seconds()
        logger.info("backup ok: %s (%.1f MB, %.1fs)", dest.name, size / 1e6, took)
        result.update(file=str(dest), size_bytes=size)
    except Exception:
        logger.exception("backup failed - pruning old backups regardless")
        result.update(error=True)
    finally:
        tmp_dest.unlink(missing_ok=True)
        # Always attempt retention, even if today's backup failed - an
        # interrupted backup must not also block cleanup of older ones.
        result["pruned"] = _prune(settings.backups_dir, prefix, int(cfg.get("backup.keep_days", 7)))

    return result


def _prune(directory: Path, prefix: str, keep_days: int) -> int:
    files = sorted(directory.glob(f"{prefix}*.db"))
    if len(files) <= keep_days:
        return 0
    to_remove = files[: len(files) - keep_days]
    for f in to_remove:
        f.unlink(missing_ok=True)
        logger.info("pruned old backup %s", f.name)
    return len(to_remove)


def list_backups() -> list[dict[str, Any]]:
    settings = get_settings()
    cfg = get_config()
    prefix = cfg.get("backup.filename_prefix", "fortigate_")
    out: list[dict[str, Any]] = []
    for f in sorted(settings.backups_dir.glob(f"{prefix}*.db"), reverse=True):
        st = f.stat()
        out.append(
            {
                "file": f.name,
                "size_bytes": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
            }
        )
    return out
