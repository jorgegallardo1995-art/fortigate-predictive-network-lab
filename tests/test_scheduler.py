"""Scheduler wiring + the MOCK collection cycle."""

from __future__ import annotations

from datetime import timedelta

from app.collector.scheduler import CollectorScheduler
from app.collector.telemetry_collector import TelemetryCollector, _derive_rates
from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.models import DATA_SOURCE_MOCK


def test_scheduler_config_defaults() -> None:
    s = CollectorScheduler()
    assert s.interval == 60
    defaults = s._scheduler._job_defaults
    assert defaults["max_instances"] == 1
    assert defaults["coalesce"] is True


def test_mock_cycle_persists_row(session) -> None:
    collector = TelemetryCollector()  # MOCK_MODE=true in tests
    result = collector.run_cycle(utc_now())
    collector.close()
    assert result["success"] is True

    last = repo.latest_raw(session, DATA_SOURCE_MOCK)
    assert last is not None
    assert last.data_source == "MOCK"
    assert last.cpu_pct is not None
    assert repo.latest_sla(session, DATA_SOURCE_MOCK)  # SLA rows written


def test_two_cycles_derive_throughput(session) -> None:
    collector = TelemetryCollector()
    t0 = utc_now() - timedelta(seconds=60)
    collector.run_cycle(t0)
    collector.run_cycle(utc_now())
    collector.close()

    rows = repo.raw_history(session, utc_now() - timedelta(minutes=5), None, DATA_SOURCE_MOCK)
    assert len(rows) == 2
    assert rows[1].rx_mbps is not None and rows[1].rx_mbps >= 0


def test_derive_rates_handles_counter_reset() -> None:
    from app.database.models import TelemetryRaw

    prev = TelemetryRaw(timestamp_utc=utc_now() - timedelta(seconds=60), rx_bytes=10_000_000)
    out = _derive_rates({"rx_bytes": 5}, prev, utc_now())
    assert out["rx_bps"] is None  # negative delta => skip, no negative throughput
