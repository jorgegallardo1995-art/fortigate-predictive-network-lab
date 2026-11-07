"""Robust acquisition scheduler (APScheduler).

Guarantees required by the research protocol:

* Fixed cadence from ``collector.sampling_interval_seconds`` - not ``sleep(60)``.
* ``max_instances=1`` + ``coalesce=True`` => cycles never overlap and a backlog
  after a slow cycle collapses to a single run instead of a burst.
* A cycle that overruns the interval is allowed to finish; the next scheduled
  run that was skipped is recorded as a ``MISSING_INTERVAL`` event.
* Separate jobs for feature recomputation, the daily backup and the hourly
  speed test.
"""

from __future__ import annotations

import random as _random
import threading
from datetime import datetime, timedelta
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_config, get_settings
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.services import event_service

logger = get_logger("scheduler")


def plan_ramp_times(
    day_start: datetime,
    count: int,
    min_gap_hours: float,
    rng: _random.Random,
    horizon_hours: float = 24.0,
) -> list[datetime]:
    """Pick ``count`` randomised ramp times within [day_start, +horizon), each at
    least ``min_gap_hours`` apart. One random time per equal slot, jittered only
    within the room the min-gap leaves, so spacing is guaranteed (reviewer
    Bloque 2: events at sorted, wide intervals, not a fixed calendar)."""
    count = max(1, int(count))
    slot = horizon_hours / count
    jitter = max(0.0, slot - float(min_gap_hours))
    times: list[datetime] = []
    for i in range(count):
        offset_h = i * slot + rng.uniform(0.0, jitter)
        times.append(day_start + timedelta(hours=offset_h))
    return times


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

        if self.cfg.get("speedtest.enabled", True):
            self._scheduler.add_job(
                self._run_speedtest,
                trigger=CronTrigger(
                    minute=int(self.cfg.get("speedtest.minute", 30)), timezone="UTC"
                ),
                id="hourly_speedtest",
                name="Hourly speed test",
                # A test can take ~40 s on the Pi; a late start is still worth running.
                misfire_grace_time=300,
                replace_existing=True,
            )

        congestion_hours = self.cfg.get("congestion.schedule_hours_utc", []) or []
        if congestion_hours:
            self._scheduler.add_job(
                self._run_congestion_ramp,
                trigger=CronTrigger(
                    hour=",".join(str(int(h)) for h in congestion_hours),
                    minute=int(self.cfg.get("congestion.schedule_minute", 0)),
                    timezone="UTC",
                ),
                id="scheduled_congestion_ramp",
                name="Scheduled congestion ramp",
                misfire_grace_time=300,
                replace_existing=True,
            )

        if self.cfg.get("wan_control.enabled", False) and self.cfg.get(
            "wan_control.schedule_enabled", False
        ):
            hh, _, mm = str(self.cfg.get("wan_control.schedule_start_local", "00:00")).partition(":")
            self._scheduler.add_job(
                self._run_scheduled_outage,
                trigger=CronTrigger(
                    hour=int(hh), minute=int(mm or 0),
                    timezone=get_settings().timezone,
                ),
                id="scheduled_wan_outage",
                name="Scheduled WAN outage",
                misfire_grace_time=300,
                replace_existing=True,
            )

        if self.cfg.get("wan_control.enabled", False) and self.cfg.get(
            "wan_control.ramp_schedule_enabled", False
        ):
            if str(self.cfg.get("wan_control.ramp_schedule_mode", "random")) == "random":
                # Plan randomised ramp times once a day (and once now) so the
                # model can never learn the experimenter's calendar (Bloque 2).
                self._scheduler.add_job(
                    self._plan_random_ramps,
                    trigger=CronTrigger(hour=0, minute=5, timezone="UTC"),
                    id="plan_random_ramps", name="Plan randomised ramps",
                    misfire_grace_time=600, replace_existing=True,
                )
                self._plan_random_ramps()
            else:
                ramp_hours = self.cfg.get("wan_control.ramp_schedule_hours_utc", []) or []
                self._scheduler.add_job(
                    self._run_scheduled_ramp,
                    trigger=CronTrigger(
                        hour=",".join(str(int(h)) for h in ramp_hours) if ramp_hours else None,
                        minute=int(self.cfg.get("wan_control.ramp_schedule_minute", 0)),
                        timezone="UTC",
                    ),
                    id="scheduled_shaper_ramp", name="Scheduled shaper ramp",
                    misfire_grace_time=300, replace_existing=True,
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
        from app.services import congestion_service, wan_control_service

        congestion_service.stop()
        wan_control_service.stop()
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

    def _run_speedtest(self) -> None:
        try:
            from app.services.speedtest_service import run_speedtest

            # Never compete with the daily backup for disk/CPU on the Pi.
            if utc_now().hour in self.cfg.get("speedtest.skip_hours_utc", [3]):
                return
            run_speedtest(trigger="scheduled")
        except Exception:
            logger.exception("scheduled speedtest failed")

    def _run_congestion_ramp(self) -> None:
        try:
            from app.services import congestion_service

            congestion_service.start(trigger="scheduled")
        except Exception:
            logger.exception("scheduled congestion ramp could not start")

    def _run_scheduled_outage(self) -> None:
        try:
            from app.services import wan_control_service

            wan_control_service.start_outage(trigger="scheduled")
        except Exception:
            logger.exception("scheduled WAN outage could not start")

    def _run_scheduled_ramp(self) -> None:
        try:
            from app.services import wan_control_service

            wan_control_service.start_bandwidth_ramp(trigger="scheduled")
        except Exception:
            # Expected to no-op when a fault is already active (e.g. nightly outage).
            logger.info("scheduled shaper ramp skipped: %s", "another WAN fault active or disabled")

    def _plan_random_ramps(self) -> None:
        """Schedule today's remaining ramps at randomised times (Bloque 2)."""
        try:
            now = utc_now()
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            count = int(self.cfg.get("wan_control.ramp_random_count", 6))
            gap = float(self.cfg.get("wan_control.ramp_random_min_gap_hours", 2))
            times = plan_ramp_times(day_start, count, gap, _random.Random(now.timetuple().tm_yday))
            scheduled = 0
            for t in times:
                if t <= now + timedelta(seconds=30):
                    continue  # already past for today
                self._scheduler.add_job(
                    self._run_scheduled_ramp,
                    trigger=DateTrigger(run_date=t),
                    id=f"ramp_rand_{t:%Y%m%dT%H%M}",
                    name="Randomised shaper ramp",
                    misfire_grace_time=600, replace_existing=True,
                )
                scheduled += 1
            logger.info("planned %d randomised ramp(s) for today (%d total times)", scheduled, len(times))
        except Exception:
            logger.exception("could not plan randomised ramps")

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
