"""Backups: a failed/interrupted copy must never (a) leave a corrupt file
under the dated name, or (b) block pruning of older backups - both used to
happen when a service restart hit mid-backup, which is exactly what starved
the disk (old backups piled up forever, past keep_days)."""

from __future__ import annotations

import pytest
from app.services import backup_service


@pytest.fixture
def settings_with_db(tmp_path, monkeypatch):
    from app.core import config as config_module

    db_path = tmp_path / "fortigate.db"
    import sqlite3

    sqlite3.connect(db_path).close()

    settings = config_module.get_settings()
    monkeypatch.setattr(type(settings), "database_path", property(lambda self: db_path))
    monkeypatch.setattr(type(settings), "backups_dir", property(lambda self: tmp_path / "backups"))
    monkeypatch.setattr(type(settings), "effective_database_url", property(lambda self: "sqlite:///x"))
    return settings, tmp_path


def test_successful_backup_is_named_atomically(settings_with_db) -> None:
    _settings, tmp_path = settings_with_db
    result = backup_service.run_backup()
    assert result["skipped"] is False
    assert list((tmp_path / "backups").glob("*.tmp")) == []
    files = list((tmp_path / "backups").glob("fortigate_*.db"))
    assert len(files) == 1


def test_failed_backup_still_prunes_old_ones(settings_with_db) -> None:
    settings, tmp_path = settings_with_db
    db_path = settings.database_path
    # Corrupt the source file so sqlite3's online backup API genuinely fails,
    # simulating a service restart / disk error mid-backup.
    db_path.write_bytes(b"not a real sqlite file")

    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    # Simulate 8 pre-existing daily backups (over the default keep_days=7).
    for i in range(8):
        (backups_dir / f"fortigate_2026-01-0{i + 1}.db").write_bytes(b"x")

    result = backup_service.run_backup()

    assert result.get("error") is True
    assert result["pruned"] == 1  # still cleaned up the oldest, despite today's failure
    assert list(backups_dir.glob("*.tmp")) == []
    # no partial/corrupt file left under today's dated name
    assert list(backups_dir.glob(f"fortigate_{__import__('datetime').date.today().isoformat()}.db")) == []
