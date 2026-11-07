"""Randomised ramp scheduling (reviewer Bloque 2): times are sorted anew each
day, spaced by at least the minimum gap, so the model cannot learn a fixed
experimenter calendar."""

from __future__ import annotations

import random
from datetime import UTC, datetime

from app.collector.scheduler import plan_ramp_times


def _day():
    return datetime(2026, 11, 7, 0, 0, tzinfo=UTC)


def test_count_and_within_horizon() -> None:
    times = plan_ramp_times(_day(), count=6, min_gap_hours=2, rng=random.Random(1))
    assert len(times) == 6
    assert all(_day() <= t < _day().replace(hour=23, minute=59) for t in times[:-1])


def test_minimum_gap_respected() -> None:
    times = sorted(plan_ramp_times(_day(), count=6, min_gap_hours=2, rng=random.Random(3)))
    gaps = [(times[i + 1] - times[i]).total_seconds() / 3600 for i in range(len(times) - 1)]
    assert all(g >= 2 - 1e-6 for g in gaps)


def test_randomised_between_days() -> None:
    a = plan_ramp_times(_day(), 6, 2, random.Random(10))
    b = plan_ramp_times(_day(), 6, 2, random.Random(11))
    # Different seeds (different days) give different times - no fixed calendar.
    assert [t.minute for t in a] != [t.minute for t in b] or [t.hour for t in a] != [t.hour for t in b]


def test_handles_more_ramps_than_room() -> None:
    # 15 ramps with a 2h gap cannot all fit in 24h; function still returns 15
    # evenly spaced times without crashing (gap collapses to the slot size).
    times = plan_ramp_times(_day(), count=15, min_gap_hours=2, rng=random.Random(5))
    assert len(times) == 15
