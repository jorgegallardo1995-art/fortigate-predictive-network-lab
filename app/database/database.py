"""Database engine, session factory and SQLite tuning.

The rest of the app only ever imports :func:`session_scope` (write path) or
:func:`get_session` (FastAPI dependency, read path) and :func:`init_db`.

Switching to PostgreSQL later = set ``DATABASE_URL`` in ``.env``; nothing in this
module or the models needs to change (the SQLite PRAGMAs are applied only for
SQLite URLs).
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.models import Base

logger = get_logger("database")

_settings = get_settings()
_is_sqlite = _settings.effective_database_url.startswith("sqlite")

engine: Engine = create_engine(
    _settings.effective_database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        """Enable WAL + sane durability/perf settings on every SQLite connection."""
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.close()


def init_db() -> None:
    """Create the data directory and all tables if they do not exist.

    For a fresh deployment this is enough. Once Alembic migrations exist, this
    becomes ``alembic upgrade head`` in the install script; ``create_all`` stays
    as the zero-migration bootstrap and is a no-op on an existing schema.
    """
    _settings.ensure_directories()
    Base.metadata.create_all(bind=engine)
    logger.info(
        "Database ready: %s (%s)",
        _settings.effective_database_url.split("://", 1)[0],
        _settings.database_path if _is_sqlite else "external",
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for the collector / services (commit or rollback)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency - read-only request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def checkpoint_wal() -> None:
    """Flush the WAL back into the main DB file (call on graceful shutdown)."""
    if not _is_sqlite:
        return
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    logger.info("SQLite WAL checkpoint completed")
