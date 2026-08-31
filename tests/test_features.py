"""Feature engineering: derived from RAW, RAW untouched, formulas correct."""

from __future__ import annotations

from datetime import timedelta

from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.models import DATA_SOURCE_REAL, TelemetryRaw
from app.services.feature_engineering import compute_features_for_range


def _seed(session, n: int = 20) -> None:
    start = utc_now() - timedelta(minutes=n)
    for i in range(n):
        ts = start + timedelta(minutes=i)
        session.add(
            TelemetryRaw(
                scheduled_timestamp=ts, collection_start_timestamp=ts, collection_end_timestamp=ts,
                collection_duration_ms=100, collection_success=True, data_source=DATA_SOURCE_REAL,
                timestamp_utc=ts, timestamp_local=ts, timezone="UTC",
                collector_version="0.1.0", schema_version="1", created_at=ts,
                latency_ms=10.0 + i, jitter_ms=1.0, packet_loss_pct=0.0,
                rx_mbps=float(i), tx_mbps=float(i) / 2, active_sessions=100 + i,
                cpu_pct=20.0, memory_pct=40.0,
            )
        )
    session.commit()


def test_features_computed_without_touching_raw(session) -> None:
    _seed(session, 20)
    raw_before = [r.latency_ms for r in repo.raw_history(session, utc_now() - timedelta(hours=1))]

    end = utc_now()
    n = compute_features_for_range(session, end - timedelta(minutes=20), end, DATA_SOURCE_REAL)
    session.commit()
    assert n > 0

    raw_after = [r.latency_ms for r in repo.raw_history(session, utc_now() - timedelta(hours=1))]
    assert raw_before == raw_after  # RAW is immutable

    from app.database.models import TelemetryFeature

    rows = session.query(TelemetryFeature).filter_by(window_minutes=5).all()
    assert rows
    f = max(rows, key=lambda r: r.timestamp_utc)
    # latency rises by 1/min => slope ~ 1.0, delta over 5-min window ~ 4-5
    assert f.latency_slope is not None and 0.7 < f.latency_slope < 1.3
    assert f.latency_max >= f.latency_min
    assert f.cpu_std == 0.0  # constant series


def test_windows_configured(session) -> None:
    _seed(session, 20)
    end = utc_now()
    compute_features_for_range(session, end - timedelta(minutes=20), end, DATA_SOURCE_REAL)
    session.commit()
    from app.database.models import TelemetryFeature

    windows = {r.window_minutes for r in session.query(TelemetryFeature).all()}
    assert windows == {5, 10, 15}
