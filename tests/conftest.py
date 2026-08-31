"""Test configuration.

Environment is set BEFORE any app import so the cached ``Settings`` and the
SQLAlchemy engine pick up a throw-away database and MOCK mode. No test ever
touches a real FortiGate.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

_TMP = tempfile.mkdtemp(prefix="pnl-test-")
os.environ.update(
    {
        "MOCK_MODE": "true",
        "RUN_SCHEDULER": "false",
        "DATA_DIR": _TMP,
        "DATABASE_URL": "",
        "FORTIGATE_HOST": "fortigate.test",
        "FORTIGATE_API_TOKEN": "unit-test-token-abcdef",
        "TIMEZONE": "UTC",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from app.database.database import SessionLocal, engine, init_db  # noqa: E402
from app.database.models import Base  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def session() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
