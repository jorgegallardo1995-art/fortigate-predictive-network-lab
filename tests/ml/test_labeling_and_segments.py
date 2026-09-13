from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from ml.labeling import FailureEvent, apply_labels, dedupe_failures, labeling_summary
from ml.segments import continuous_segments, segment_stats


def _minutes(n: int, start: str = "2026-01-01T00:00:00Z") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"timestamp_utc": ts, "experiment_id": [None] * n})


def test_apply_labels_matches_spec_table() -> None:
    """Reproduces the exact table from §3.16.7: -20min -> 0, -15/-10/-5/-1 -> 1."""
    df = _minutes(30)
    tf = df["timestamp_utc"].iloc[25]  # arbitrary anchor
    failure = FailureEvent(tf)
    labeled = apply_labels(df, [failure], horizon_minutes=15, lookback_minutes=25)

    def label_at(minutes_before: int) -> int:
        row = labeled[labeled["timestamp_utc"] == tf - timedelta(minutes=minutes_before)]
        return int(row["label"].iloc[0])

    assert label_at(20) == 0
    assert label_at(15) == 1
    assert label_at(10) == 1
    assert label_at(5) == 1
    assert label_at(1) == 1


def test_apply_labels_no_failures_all_zero() -> None:
    df = _minutes(10)
    labeled = apply_labels(df, [], horizon_minutes=15)
    assert labeled["label"].sum() == 0
    s = labeling_summary(labeled)
    assert s["positive_rows"] == 0
    assert s["total_rows"] == 10


def test_closer_failure_wins_when_two_overlap() -> None:
    df = _minutes(40)
    t_far = df["timestamp_utc"].iloc[35]
    t_near = df["timestamp_utc"].iloc[20]
    labeled = apply_labels(
        df, [FailureEvent(t_far), FailureEvent(t_near)], horizon_minutes=15, lookback_minutes=30
    )
    # a row 10 minutes before t_near should report minutes_before_failure ~= 10, not vs t_far
    row = labeled[labeled["timestamp_utc"] == t_near - timedelta(minutes=10)]
    assert pytest.approx(row["minutes_before_failure"].iloc[0], abs=0.01) == 10.0


def test_dedupe_failures_collapses_close_instants() -> None:
    t0 = pd.Timestamp("2026-01-01T00:00:00Z")
    events = [FailureEvent(t0), FailureEvent(t0 + timedelta(minutes=2))]
    deduped = dedupe_failures(events, min_gap_minutes=5)
    assert len(deduped) == 1


def test_continuous_segments_splits_on_gap() -> None:
    df = _minutes(10)
    # introduce a gap: drop rows 5..7 (indices) to force > tolerance spacing
    df2 = pd.concat([df.iloc[:5], df.iloc[8:]]).reset_index(drop=True)
    segments = continuous_segments(df2, tolerance_seconds=90)
    assert len(segments) == 2
    stats = segment_stats(segments)
    assert stats["n_segments"] == 2
    assert stats["total_rows"] == len(df2)


def test_continuous_segments_single_run_when_no_gap() -> None:
    df = _minutes(20)
    segments = continuous_segments(df, tolerance_seconds=90)
    assert len(segments) == 1
    assert len(segments[0]) == 20


def test_continuous_segments_empty_input() -> None:
    assert continuous_segments(pd.DataFrame(columns=["timestamp_utc"]), 90) == []
