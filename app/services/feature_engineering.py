"""Derived-feature computation.

RAW telemetry is never modified. This module reads ``telemetry_raw`` and writes
``telemetry_features``: for every raw sample timestamp ``t`` and every configured
window ``w`` (5/10/15 min) it summarises the samples in ``(t - w, t]``.

Every derived column is documented in :data:`FEATURE_DEFINITIONS` (name, source
variable, window, formula) and surfaced on the ``/dataset`` page and in the data
dictionary export.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.database import repositories as repo
from app.database.database import session_scope
from app.database.models import DATA_SOURCE_MOCK, DATA_SOURCE_REAL, TelemetryFeature

logger = get_logger("features")

# source column -> statistics to compute over the window
_STAT_SPEC: dict[str, tuple[str, ...]] = {
    "latency_ms": ("mean", "min", "max", "std", "delta", "slope"),
    "jitter_ms": ("mean", "max", "std"),
    "packet_loss_pct": ("mean", "max", "delta"),
    "rx_mbps": ("mean", "max", "std", "slope"),
    "tx_mbps": ("mean", "max", "std", "slope"),
    "active_sessions": ("mean", "max", "delta", "pct_change"),
    "cpu_pct": ("mean", "max", "std"),
    "memory_pct": ("mean", "max", "std"),
}
# output column stem per source column
_STEM = {
    "latency_ms": "latency",
    "jitter_ms": "jitter",
    "packet_loss_pct": "packet_loss",
    "rx_mbps": "rx_mbps",
    "tx_mbps": "tx_mbps",
    "active_sessions": "sessions",
    "cpu_pct": "cpu",
    "memory_pct": "memory",
}

FEATURE_DEFINITIONS: list[dict[str, str]] = []
for _src, _stats in _STAT_SPEC.items():
    for _s in _stats:
        _formula = {
            "mean": "arithmetic mean over the window",
            "min": "minimum over the window",
            "max": "maximum over the window",
            "std": "population standard deviation over the window",
            "delta": "last value - first value in the window",
            "slope": "least-squares slope vs time (per minute) over the window",
            "pct_change": "(last - first) / first over the window",
        }[_s]
        FEATURE_DEFINITIONS.append(
            {"name": f"{_STEM[_src]}_{_s}", "source": _src, "window": "5/10/15 min", "formula": _formula}
        )
FEATURE_DEFINITIONS += [
    {"name": "rx_drop_rate", "source": "rx_drops,rx_packets", "window": "instant",
     "formula": "delta(rx_drops) / delta(rx_packets) between the two most recent samples"},
    {"name": "tx_drop_rate", "source": "tx_drops,tx_packets", "window": "instant",
     "formula": "delta(tx_drops) / delta(tx_packets)"},
    {"name": "rx_error_rate", "source": "rx_errors,rx_packets", "window": "instant",
     "formula": "delta(rx_errors) / delta(rx_packets)"},
    {"name": "tx_error_rate", "source": "tx_errors,tx_packets", "window": "instant",
     "formula": "delta(tx_errors) / delta(tx_packets)"},
    {"name": "traffic_growth_rate", "source": "rx_mbps", "window": "window",
     "formula": "slope of rx_mbps per minute over the window"},
    {"name": "rx_tx_ratio", "source": "rx_bps,tx_bps", "window": "instant",
     "formula": "rx_bps / tx_bps at the window end"},
]


def _slope_per_minute(times: np.ndarray, values: np.ndarray) -> float | None:
    mask = ~np.isnan(values)
    if mask.sum() < 2:
        return None
    x = (times[mask] - times[mask][0]) / 60.0  # minutes
    y = values[mask]
    if np.ptp(x) == 0:
        return None
    return float(np.polyfit(x, y, 1)[0])


def compute_features_for_range(
    session: Session, start, end, data_source: str
) -> int:
    """Compute + upsert feature rows for every raw sample in ``[start, end]``."""
    cfg = get_config()
    windows = cfg.feature_windows
    max_window = max(windows)
    rows = repo.raw_history(session, start - timedelta(minutes=max_window), end, data_source)
    if len(rows) < 2:
        return 0

    df = pd.DataFrame(
        [
            {
                "ts": r.timestamp_utc,
                **{c: getattr(r, c) for c in _STAT_SPEC},
                "rx_bps": r.rx_bps,
                "tx_bps": r.tx_bps,
                "rx_drop_rate": r.rx_drop_rate,
                "tx_drop_rate": r.tx_drop_rate,
                "rx_error_rate": r.rx_error_rate,
                "tx_error_rate": r.tx_error_rate,
            }
            for r in rows
        ]
    )
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    epoch = df["ts"].astype("int64").to_numpy() / 1e9

    targets = df[(df["ts"] >= pd.Timestamp(start)) & (df["ts"] <= pd.Timestamp(end))]
    written = 0
    now = utc_now()

    for idx in targets.index:
        t_epoch = epoch[idx]
        for w in windows:
            lo = t_epoch - w * 60
            sel = (epoch > lo) & (epoch <= t_epoch)
            if sel.sum() < 2:
                continue
            wdf = df[sel]
            wt = epoch[sel]
            feat = TelemetryFeature(
                timestamp_utc=df["ts"].iloc[idx].to_pydatetime(),
                window_minutes=w,
                data_source=data_source,
                sample_count=int(sel.sum()),
                computed_at=now,
            )
            for src, stats in _STAT_SPEC.items():
                stem = _STEM[src]
                series = wdf[src].to_numpy(dtype="float64")
                if np.isnan(series).all():
                    continue
                for stat in stats:
                    setattr(feat, f"{stem}_{stat}", _stat_value(stat, series, wt))
            # instantaneous ratios at window end
            last = df.iloc[idx]
            feat.rx_drop_rate = _nn(last["rx_drop_rate"])
            feat.tx_drop_rate = _nn(last["tx_drop_rate"])
            feat.rx_error_rate = _nn(last["rx_error_rate"])
            feat.tx_error_rate = _nn(last["tx_error_rate"])
            feat.traffic_growth_rate = _slope_per_minute(wt, wdf["rx_mbps"].to_numpy("float64"))
            rx_bps, tx_bps = last["rx_bps"], last["tx_bps"]
            feat.rx_tx_ratio = (
                float(rx_bps / tx_bps) if rx_bps and tx_bps and tx_bps != 0 else None
            )
            repo.upsert_feature(session, feat)
            written += 1
    return written


def _stat_value(stat: str, series: np.ndarray, times: np.ndarray) -> float | None:
    clean = series[~np.isnan(series)]
    if clean.size == 0:
        return None
    if stat == "mean":
        return float(np.mean(clean))
    if stat == "min":
        return float(np.min(clean))
    if stat == "max":
        return float(np.max(clean))
    if stat == "std":
        return float(np.std(clean)) if clean.size > 1 else 0.0
    if stat == "delta":
        return float(clean[-1] - clean[0])
    if stat == "pct_change":
        return float((clean[-1] - clean[0]) / clean[0]) if clean[0] != 0 else None
    if stat == "slope":
        return _slope_per_minute(times, series)
    return None


def _nn(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def recompute_recent_features(lookback_minutes: int = 180) -> int:
    """Scheduled entry point: (re)compute features for the recent tail per source."""
    total = 0
    end = utc_now()
    start = end - timedelta(minutes=lookback_minutes)
    with session_scope() as session:
        for ds in (DATA_SOURCE_REAL, DATA_SOURCE_MOCK):
            if repo.count_raw(session, ds) == 0:
                continue
            last = repo.last_feature_timestamp(session, ds)
            window_start = min(start, last) if last else start
            total += compute_features_for_range(session, window_start, end, ds)
    return total
