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


def test_icmp_fallback_fills_quality_when_no_sla() -> None:
    from app.collector.icmp_prober import ProbeReading
    from app.collector.telemetry_collector import _fill_quality_from_icmp
    from app.schemas.telemetry import SourceSnapshot

    snap = SourceSnapshot(data_source="REAL", success=True, metrics={})
    probes = [
        ProbeReading("cloudflare", "1.1.1.1", rtt_avg_ms=10.0, rtt_mdev_ms=1.0,
                     packet_loss_pct=0.0, success=True),
        ProbeReading("google", "8.8.8.8", rtt_avg_ms=20.0, rtt_mdev_ms=3.0,
                     packet_loss_pct=0.0, success=True),
    ]
    _fill_quality_from_icmp(snap, probes)
    assert snap.metrics["latency_ms"] == 15.0
    assert snap.metrics["jitter_ms"] == 2.0
    assert snap.metrics["packet_loss_pct"] == 0.0
    assert snap.metrics["sla_status"] == "up"
    assert snap.raw_json["quality_source"] == "icmp_from_collector"


def test_derive_rates_handles_counter_reset() -> None:
    from app.database.models import TelemetryRaw

    prev = TelemetryRaw(timestamp_utc=utc_now() - timedelta(seconds=60), rx_bytes=10_000_000)
    out = _derive_rates({"rx_bytes": 5}, prev, utc_now())
    assert out["rx_bps"] is None  # negative delta => skip, no negative throughput
