"""Robust acquisition scheduler (APScheduler).

Guarantees required by the research protocol:

* Fixed cadence from ``collector.sampling_interval_seconds`` - not ``sleep(60)``.
* ``max_instances=1`` + ``coalesce=True`` => cycles never overlap and a backlog
  after a slow cycle collapses to a single run instead of a burst.
* A cycle that overruns the interval is allowed to finish; the next scheduled
  run that was skipped is recorded as a ``MISSING_INTERVAL`` event.
* Separate jobs for feature recomputation and the daily backup.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_config
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.services import event_service

logger = get_logger("scheduler")


class CollectorScheduler:
    """Wraps a single :class:`BackgroundScheduler` and the project's jobs."""

    def __init__(self) -> None:
        self.cfg = get_config()
        self.interval = self.cfg.sampling_interval
        self._scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": max(5, self.interval // 2),
            },
        )
        self._lock = threading.Lock()
        self._started_at: datetime | None = None
        self._last_run: datetime | None = None
        self._last_success: datetime | None = None
        self._consecutive_failures = 0
        self._running = False

    # --- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._scheduler.add_job(
            self._run_collection_cycle,
            trigger=IntervalTrigger(seconds=self.interval),
            id="collection_cycle",
            name="Telemetry acquisition",
            next_run_time=utc_now(),
            replace_existing=True,
        )
        if self.cfg.get("features.enabled", True):
            self._scheduler.add_job(
                self._run_feature_recompute,
                trigger=IntervalTrigger(
                    seconds=int(self.cfg.get("features.recompute_interval_seconds", 300))
                ),
                id="feature_recompute",
                name="Feature engineering",
                replace_existing=True,
            )
        self._scheduler.add_job(
            self._run_availability_rebuild,
            trigger=IntervalTrigger(
                seconds=int(self.cfg.get("availability.rebuild_interval_seconds", 180))
            ),
            id="availability_rebuild",
            name="Availability timeline",
            next_run_time=utc_now(),
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._run_auto_experiment_detection,
            trigger=IntervalTrigger(
                seconds=int(self.cfg.get("auto_experiments.interval_seconds", 600))
            ),
            id="auto_experiment_detection",
            name="Auto experiment detection",
            next_run_time=utc_now(),
            replace_existing=True,
        )
        if self.cfg.get("backup.enabled", True):
            self._scheduler.add_job(
                self._run_backup,
                trigger=CronTrigger(
                    hour=int(self.cfg.get("backup.schedule_hour", 3)),
                    minute=int(self.cfg.get("backup.schedule_minute", 0)),
                    timezone="UTC",
                ),
                id="daily_backup",
                name="Daily database backup",
                replace_existing=True,
            )
        if self.cfg.get("retention.enabled", True):
            self._scheduler.add_job(
                self._run_raw_json_retention,
                trigger=CronTrigger(
                    hour=int(self.cfg.get("retention.schedule_hour", 3)),
                    minute=int(self.cfg.get("retention.schedule_minute", 30)),
                    timezone="UTC",
                ),
                id="raw_json_retention",
                name="Raw JSON retention",
                replace_existing=True,
            )

        self._scheduler.add_listener(self._on_job_problem, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
        self._scheduler.start()
        self._running = True
        self._started_at = utc_now()
        logger.info("scheduler started (interval=%ss)", self.interval)
        event_service.emit("COLLECTOR_STARTED", f"Collector started, interval={self.interval}s")

    def shutdown(self) -> None:
        if not self._running:
            return
        self._scheduler.shutdown(wait=True)
        self._running = False
        logger.info("scheduler stopped")
        event_service.emit("COLLECTOR_STOPPED", "Collector stopped")

    # --- jobs --------------------------------------------------------
    def _run_collection_cycle(self) -> None:
        scheduled = utc_now()
        if not self._lock.acquire(blocking=False):
            logger.warning("previous cycle still running - skipping this tick")
            event_service.emit(
                "MISSING_INTERVAL",
                "Acquisition tick skipped: previous cycle still running",
                severity="WARNING",
            )
            return
        try:
            # Import here to avoid a heavy import at module load / during tests.
            from app.collector.telemetry_collector import TelemetryCollector

            collector = TelemetryCollector()
            try:
                result = collector.run_cycle(scheduled)
            finally:
                collector.close()
            self._last_run = scheduled
            if result["success"]:
                self._last_success = scheduled
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
                self._maybe_alert_failures()
        except Exception:
            logger.exception("collection cycle raised")
            self._consecutive_failures += 1
            self._maybe_alert_failures()
        finally:
            self._lock.release()

    def _maybe_alert_failures(self) -> None:
        threshold = int(self.cfg.get("collector.failure_event_threshold", 3))
        if self._consecutive_failures == threshold:
            event_service.emit(
                "API_ERROR",
                f"{self._consecutive_failures} consecutive failed acquisition cycles",
                severity="CRITICAL",
            )

    def _run_feature_recompute(self) -> None:
        try:
            from app.services.feature_engineering import recompute_recent_features

            n = recompute_recent_features()
            if n:
                logger.info("recomputed %d feature rows", n)
        except Exception:
            logger.exception("feature recompute failed")

    def _run_availability_rebuild(self) -> None:
        try:
            from app.services.availability_service import rebuild_all

            n = rebuild_all(full=False)
            if n:
                logger.debug("availability timeline: %d slots", n)
        except Exception:
            logger.exception("availability rebuild failed")

    def _run_auto_experiment_detection(self) -> None:
        try:
            from app.services.auto_experiment_detector import detect_and_register

            created = detect_and_register()
            if created:
                logger.info("auto-registered %d experiment(s): %s", len(created), created)
        except Exception:
            logger.exception("auto experiment detection failed")

    def _run_raw_json_retention(self) -> None:
        try:
            from app.services.retention_service import prune_raw_json

            result = prune_raw_json()
            if result["pruned_rows"]:
                logger.info("raw_json retention: %s", result)
        except Exception:
            logger.exception("raw_json retention failed")

    def _run_backup(self) -> None:
        try:
            from app.services.backup_service import run_backup

            run_backup()
        except Exception:
            logger.exception("scheduled backup failed")

    def _on_job_problem(self, event: Any) -> None:
        logger.warning("APScheduler job problem: %s", event)

    # --- introspection --------------------------------------------
    def status(self) -> dict[str, Any]:
        job = self._scheduler.get_job("collection_cycle") if self._running else None
        return {
            "running": self._running,
            "interval_seconds": self.interval,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "last_success": self._last_success.isoformat() if self._last_success else None,
            "consecutive_failures": self._consecutive_failures,
            "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
            "uptime_seconds": (
                (utc_now() - self._started_at).total_seconds() if self._started_at else None
            ),
        }


scheduler = CollectorScheduler()
