"""Backups: a failed/interrupted copy must never (a) leave a corrupt file
under the dated name, or (b) block pruning of older backups - both used to
happen when a service restart hit mid-backup, which is exactly what starved
the disk (old backups piled up forever, past keep_days). Later, a full disk
made every backup fail while 4 half-written legacy files still counted
towards keep_days, so nothing was ever pruned again."""

from __future__ import annotations

import gzip
import shutil
import sqlite3
from collections import namedtuple
from datetime import date

import pytest
from app.services import backup_service

TODAY = date.today().isoformat()


@pytest.fixture
def settings_with_db(tmp_path, monkeypatch):
    from app.core import config as config_module

    db_path = tmp_path / "fortigate.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit()
    con.close()

    settings = config_module.get_settings()
    monkeypatch.setattr(type(settings), "database_path", property(lambda self: db_path))
    monkeypatch.setattr(type(settings), "backups_dir", property(lambda self: tmp_path / "backups"))
    monkeypatch.setattr(type(settings), "effective_database_url", property(lambda self: "sqlite:///x"))
    return settings, tmp_path


def _valid_legacy(path) -> None:
    path.write_bytes(backup_service.SQLITE_HEADER + b"\x00" * 100)


def test_successful_backup_is_compressed_verified_and_atomic(settings_with_db) -> None:
    _settings, tmp_path = settings_with_db
    backups_dir = tmp_path / "backups"
    result = backup_service.run_backup()

    assert result["skipped"] is False
    assert "error" not in result
    assert [f.name for f in backups_dir.iterdir()] == [f"fortigate_{TODAY}.db.gz"]

    restored = tmp_path / "restored.db"
    with gzip.open(backups_dir / f"fortigate_{TODAY}.db.gz", "rb") as fin, restored.open("wb") as fout:
        shutil.copyfileobj(fin, fout)
    con = sqlite3.connect(restored)
    assert con.execute("SELECT x FROM t").fetchone() == (42,)
    con.close()


def test_failed_backup_still_prunes_old_ones(settings_with_db) -> None:
    settings, tmp_path = settings_with_db
    # Corrupt the source file so sqlite3's online backup API genuinely fails,
    # simulating a service restart / disk error mid-backup.
    settings.database_path.write_bytes(b"not a real sqlite file")

    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    # Simulate 8 pre-existing daily backups (over the default keep_days=7).
    for i in range(8):
        _valid_legacy(backups_dir / f"fortigate_2026-01-0{i + 1}.db")

    result = backup_service.run_backup()

    assert result.get("error") is True
    assert result["pruned"] == 1  # still cleaned up the oldest, despite today's failure
    assert not (backups_dir / "fortigate_2026-01-01.db").exists()
    assert [f for f in backups_dir.iterdir() if ".tmp" in f.name] == []
    # no partial/corrupt file left under today's dated name
    assert list(backups_dir.glob(f"fortigate_{TODAY}.db*")) == []


def test_corrupt_backups_do_not_count_towards_keep_days(settings_with_db) -> None:
    _settings, tmp_path = settings_with_db
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, 5):  # half-written legacy copies: zeroed header
        (backups_dir / f"fortigate_2026-01-0{i}.db").write_bytes(b"\x00" * 100)
        (backups_dir / f"fortigate_2026-01-0{i}.db-journal").write_bytes(b"j")
    for i in range(5, 8):
        _valid_legacy(backups_dir / f"fortigate_2026-01-0{i}.db")
    (backups_dir / "fortigate_2026-01-08.db.tmp-journal").write_bytes(b"j")

    backup_service.run_backup()

    assert sorted(f.name for f in backups_dir.iterdir()) == [
        "fortigate_2026-01-05.db",
        "fortigate_2026-01-06.db",
        "fortigate_2026-01-07.db",
        f"fortigate_{TODAY}.db.gz",
    ]


def test_skips_backup_when_disk_is_too_full(settings_with_db, monkeypatch) -> None:
    _settings, tmp_path = settings_with_db
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(backup_service.shutil, "disk_usage", lambda _p: Usage(100, 99, 1))

    result = backup_service.run_backup()

    assert result["error"] is True
    assert result["reason"] == "disk-space"
    assert list((tmp_path / "backups").iterdir()) == []


def test_latest_backup_time_is_backup_start(settings_with_db) -> None:
    from app.core.timeutils import utc_now

    assert backup_service.latest_backup_time() is None
    before = utc_now()
    backup_service.run_backup()
    latest = backup_service.latest_backup_time()
    assert latest is not None
    assert before.timestamp() - 1 <= latest.timestamp() <= utc_now().timestamp()
