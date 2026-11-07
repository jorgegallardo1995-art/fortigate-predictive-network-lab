"""End-to-end pipeline smoke test via the real CLI entrypoint, synthetic data only.

Kept small (few thousand synthetic minutes, 1 epoch) so it runs in CI-reasonable
time; it exists to catch wiring bugs across the whole train.py flow, not to
validate model quality.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("tensorflow")
pytest.importorskip("sklearn")

from ml.train import main


def test_synthetic_demo_runs_end_to_end(tmp_path) -> None:
    out_dir = tmp_path / "models"
    rc = main([
        "--synthetic-demo",
        "--synthetic-minutes", "4000",
        "--max-epochs", "1",
        "--output-dir", str(out_dir),
    ])
    assert rc == 0

    runs = list(out_dir.glob("demo_*"))
    assert len(runs) == 1
    run_dir = runs[0]

    assert (run_dir / "model.keras").exists()
    assert (run_dir / "scaler.json").exists()
    assert (run_dir / "history.json").exists()

    meta = json.loads((run_dir / "run_metadata.json").read_text())
    assert meta["synthetic"] is True
    assert meta["sequences"]["total"] > 0
    assert meta["config"]["n_features"] == 23

    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert "lstm_test" in metrics
    assert "lead_time" in metrics


def test_aborts_cleanly_with_zero_real_failures(tmp_path, monkeypatch) -> None:
    """Mirrors the platform's actual current state: REAL data, zero labeled
    failures - must abort with guidance, never silently train a degenerate model."""
    import pandas as pd

    from ml import train as train_module

    empty_df = pd.DataFrame({"timestamp_utc": pd.to_datetime([], utc=True)})
    monkeypatch.setattr(train_module, "load_dataset", lambda *a, **k: _fake_no_failure_df())
    monkeypatch.setattr(train_module, "load_experiments", lambda *a, **k: empty_df.assign(
        experiment_id=[], scenario=[], start_timestamp=[], end_timestamp=[], failure_timestamp=[]))
    monkeypatch.setattr(train_module, "load_network_events", lambda *a, **k: empty_df.assign(
        event_type=[], severity=[], description=[], experiment_id=[]))
    monkeypatch.setattr(train_module, "load_speed_tests", lambda *a, **k: empty_df.assign(
        started_at=[], finished_at=[]))

    rc = main(["--db", "unused.db", "--data-source", "REAL", "--output-dir", str(tmp_path)])
    assert rc == 1
    assert list(tmp_path.glob("run_*")) == []  # nothing saved on abort


def _fake_no_failure_df():
    import pandas as pd
    from ml.variables import input_columns

    n = 200
    ts = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    df = pd.DataFrame({c: [0.0] * n for c in input_columns()})
    df["timestamp_utc"] = ts
    df["experiment_id"] = None
    df["link_status"] = "up"
    df["sla_status"] = "up"
    df["availability"] = 100.0
    df["default_route_present"] = True
    return df
