"""Automatic SQLite backups.

A consistent copy is made with the SQLite online-backup API (not ``cp``) so it is
safe while the collector is writing under WAL. The active database is never
touched. Each copy is verified with ``PRAGMA quick_check`` and then gzipped
(the raw_json payloads compress ~15x), so a backup is ``fortigate_<date>.db.gz``.
Backups older than ``backup.keep_days`` are pruned. Every backup is written to
the log; the network-event timeline is deliberately not spammed.

The newest valid backup is what makes raw_json retention safe (see
retention_service.py): its mtime is set to the moment the copy *started*, so
every row older than that is guaranteed to be inside it.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_config, get_settings
from app.core.logging import get_logger

logger = get_logger("backup")

SQLITE_HEADER = b"SQLite format 3\x00"
GZIP_MAGIC = b"\x1f\x8b"
# Headroom kept free on top of the estimated backup size, so a backup never
# fills the disk the collector itself is writing to.
_DISK_MARGIN_BYTES = 256 * 1024 * 1024
# Leftovers of an interrupted backup: temp copies and SQLite side files.
_ORPHAN_SUFFIXES = (".tmp", "-journal", "-wal", "-shm")


def run_backup() -> dict[str, Any]:
    """Create today's backup and prune old ones. Returns a small result dict.

    Works on ``.tmp`` files and atomically renames into place only after a
    full copy has passed ``quick_check`` and been compressed - a service
    restart mid-backup (e.g. the watchdog firing) or a full disk must never
    leave a partial/corrupt file under the dated name. Skips (with an error)
    instead of starting when the disk cannot hold the temporary copy.
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

    backups_dir = settings.backups_dir
    backups_dir.mkdir(parents=True, exist_ok=True)
    prefix = cfg.get("backup.filename_prefix", "fortigate_")
    keep_days = int(cfg.get("backup.keep_days", 7))
    stem = f"{prefix}{date.today().isoformat()}"
    dest = backups_dir / f"{stem}.db.gz"
    tmp_db = backups_dir / f"{stem}.db.tmp"
    tmp_gz = backups_dir / f"{stem}.db.gz.tmp"
    _remove_tmp_files(tmp_db, tmp_gz)

    result: dict[str, Any] = {"skipped": False}
    needed = _required_free_bytes(src)
    free = shutil.disk_usage(backups_dir).free
    if free < needed:
        logger.error(
            "backup skipped: not enough disk space (%.0f MB free, %.0f MB needed)",
            free / 1e6, needed / 1e6,
        )
        result.update(error=True, reason="disk-space", free_bytes=free, needed_bytes=needed)
        result["pruned"] = _prune(backups_dir, prefix, keep_days)
        return result

    started = datetime.now(UTC)
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        target = sqlite3.connect(tmp_db)
        try:
            source.backup(target)
            check = target.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            target.close()
            source.close()
        if check != "ok":
            raise RuntimeError(f"quick_check failed on backup copy: {check}")

        level = int(cfg.get("backup.compress_level", 6))
        with tmp_db.open("rb") as fin, gzip.open(tmp_gz, "wb", compresslevel=level) as fout:
            shutil.copyfileobj(fin, fout, length=4 * 1024 * 1024)
        with tmp_gz.open("rb") as fh:
            os.fsync(fh.fileno())
        # mtime = copy start: every row older than this is inside the backup.
        os.utime(tmp_gz, (started.timestamp(), started.timestamp()))
        tmp_gz.replace(dest)  # atomic: dest only ever exists as a complete copy

        size = dest.stat().st_size
        took = (datetime.now(UTC) - started).total_seconds()
        logger.info("backup ok: %s (%.1f MB, %.1fs)", dest.name, size / 1e6, took)
        result.update(file=str(dest), size_bytes=size)
    except Exception:
        logger.exception("backup failed - pruning old backups regardless")
        result.update(error=True)
    finally:
        _remove_tmp_files(tmp_db, tmp_gz)
        # Always attempt retention, even if today's backup failed - an
        # interrupted backup must not also block cleanup of older ones.
        result["pruned"] = _prune(backups_dir, prefix, keep_days)

    return result


def _required_free_bytes(src: Path) -> int:
    """Upper bound for the uncompressed temp copy plus its compressed output."""
    wal = src.with_name(src.name + "-wal")
    db_bytes = src.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
    return int(db_bytes * 1.25) + _DISK_MARGIN_BYTES


def _remove_tmp_files(*paths: Path) -> None:
    for p in paths:
        for f in (p, p.with_name(p.name + "-journal")):
            f.unlink(missing_ok=True)


def _name_pattern(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefix)}(\d{{4}}-\d{{2}}-\d{{2}})\.db(\.gz)?$")


def _is_valid_backup(path: Path) -> bool:
    """Cheap validity test by magic bytes. ``.db.gz`` files only ever appear
    after a verified copy (atomic rename); plain ``.db`` files are legacy
    backups, some of which were left half-written with a zeroed header."""
    magic = GZIP_MAGIC if path.name.endswith(".gz") else SQLITE_HEADER
    try:
        with path.open("rb") as fh:
            return fh.read(len(magic)) == magic
    except OSError:
        return False


def _valid_backups(directory: Path, prefix: str) -> list[tuple[str, Path]]:
    """(date, path) of every valid backup, oldest first."""
    pattern = _name_pattern(prefix)
    found = []
    for f in directory.glob(f"{prefix}*"):
        m = pattern.match(f.name)
        if m and _is_valid_backup(f):
            found.append((m.group(1), f))
    return sorted(found)


def _prune(directory: Path, prefix: str, keep_days: int) -> int:
    """Delete corrupt backups and orphaned side files, then keep only the
    newest ``keep_days`` valid backups. Corrupt files never count towards
    keep_days - otherwise they can pin the set and stop cleanup for good."""
    removed = 0
    pattern = _name_pattern(prefix)
    for f in directory.glob(f"{prefix}*"):
        if pattern.match(f.name):
            if not _is_valid_backup(f):
                f.unlink(missing_ok=True)
                logger.warning("removed corrupt backup %s", f.name)
                removed += 1
        elif f.name.endswith(_ORPHAN_SUFFIXES):
            f.unlink(missing_ok=True)
            logger.info("removed leftover backup file %s", f.name)

    valid = _valid_backups(directory, prefix)
    for _day, f in valid[: max(len(valid) - keep_days, 0)]:
        f.unlink(missing_ok=True)
        logger.info("pruned old backup %s", f.name)
        removed += 1
    return removed


def latest_backup_time() -> datetime | None:
    """Start time (UTC) of the newest valid backup, or None if there is none.

    Every row older than this is inside that backup."""
    settings = get_settings()
    prefix = get_config().get("backup.filename_prefix", "fortigate_")
    if not settings.backups_dir.exists():
        return None
    valid = _valid_backups(settings.backups_dir, prefix)
    if not valid:
        return None
    return datetime.fromtimestamp(max(f.stat().st_mtime for _d, f in valid), UTC)


def list_backups() -> list[dict[str, Any]]:
    settings = get_settings()
    cfg = get_config()
    prefix = cfg.get("backup.filename_prefix", "fortigate_")
    out: list[dict[str, Any]] = []
    if not settings.backups_dir.exists():
        return out
    for _day, f in reversed(_valid_backups(settings.backups_dir, prefix)):
        st = f.stat()
        out.append(
            {
                "file": f.name,
                "size_bytes": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
            }
        )
    return out
