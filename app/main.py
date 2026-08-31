"""Application entry point.

One process serves both the dashboard/API and (via the lifespan hook) the
background acquisition scheduler. This is what ``systemd`` starts on the
Raspberry Pi and what ``uvicorn app.main:app --reload`` runs on the Mac.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import get_config, get_settings
from app.core.logging import get_logger
from app.core.version import COLLECTOR_VERSION
from app.database.database import checkpoint_wal, init_db
from app.web.routes_api import router as api_router
from app.web.routes_pages import router as pages_router

logger = get_logger("main")


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "Starting %s v%s (mode=%s, db=%s)",
        get_config().get("app.technical_name"),
        COLLECTOR_VERSION,
        "MOCK" if settings.mock_mode else "REAL",
        settings.effective_database_url.split("://", 1)[0],
    )
    settings.ensure_directories()
    init_db()

    scheduler = None
    if settings.run_scheduler:
        from app.collector.scheduler import scheduler as _scheduler

        scheduler = _scheduler
        scheduler.start()
    else:
        logger.info("RUN_SCHEDULER=false -> dashboard only, no acquisition")

    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown()
        with contextlib.suppress(Exception):
            checkpoint_wal()
        logger.info("Shutdown complete")


def create_app() -> FastAPI:
    cfg = get_config()
    app = FastAPI(
        title=cfg.get("app.technical_name", "FortiGate Predictive Network Collector"),
        description=cfg.get("app.subtitle", ""),
        version=COLLECTOR_VERSION,
        lifespan=lifespan,
    )
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(api_router)
    app.include_router(pages_router)
    return app


app = create_app()


def main() -> None:
    """`python -m app.main` - convenience runner (systemd uses uvicorn directly)."""
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.dashboard_host,
        port=s.dashboard_port,
        reload=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
