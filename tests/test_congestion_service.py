"""Congestion ramps: experiment + per-step registration, protocol-defined T_f,
abort handling, mutual exclusion with speed tests and iperf3 JSON parsing."""

from __future__ import annotations

import json

import pytest
from app.database.models import DATA_SOURCE_MOCK, Experiment, LoadStep, NetworkEvent
from app.services import congestion_service, speedtest_service


@pytest.fixture(autouse=True)
def _no_leftover_ramp():
    yield
    congestion_service.stop()


def _run_ramp(**kw) -> str:
    st = congestion_service.start(**kw)
    congestion_service._thread.join(timeout=30)
    assert not congestion_service.is_running()
    return st["experiment_id"]


def test_ramp_registers_experiment_steps_and_tf(session, monkeypatch) -> None:
    monkeypatch.setattr(congestion_service._stop, "wait", lambda _s: False)  # no real sleeping

    exp_id = _run_ramp(direction="download", profile_pct=[0, 50, 100, 120], step_seconds=10)

    exp = session.query(Experiment).filter_by(experiment_id=exp_id).one()
    steps = session.query(LoadStep).filter_by(experiment_id=exp_id).order_by(LoadStep.step_index).all()
    assert exp.scenario == "CONGESTION"
    assert exp.end_timestamp is not None
    assert exp.notes.endswith("completed")
    # 4 profile steps + final 0 % recovery
    assert [s.target_pct for s in steps] == [0, 50, 100, 120, 0]
    assert [s.target_mbps for s in steps] == [0, 125, 250, 300, 0]  # capacity 250 Mbps
    assert all(s.success and s.data_source == DATA_SOURCE_MOCK for s in steps)
    assert steps[0].achieved_mbps is None  # baseline: no load offered
    assert steps[2].achieved_mbps == pytest.approx(232.5)  # mock caps at 93 % of capacity
    # T_f = start of the first step at/above failure_at_pct (100 %)
    assert exp.failure_timestamp == steps[2].started_at
    types = [e.event_type for e in session.query(NetworkEvent).filter_by(experiment_id=exp_id)]
    assert types == ["EXPERIMENT_START", "EXPERIMENT_END"]


def test_abort_before_saturation_has_no_tf(session) -> None:
    congestion_service.start(profile_pct=[0, 20, 100], step_seconds=10)
    congestion_service.stop()

    exp = session.query(Experiment).filter(
        Experiment.experiment_id.like("EXP-CONG-%")).one()
    assert exp.failure_timestamp is None
    assert "aborted" in exp.notes and "no T_f" in exp.notes
    assert exp.end_timestamp is not None


def test_only_one_ramp_and_no_speedtest_meanwhile(session) -> None:
    congestion_service.start(profile_pct=[0], step_seconds=10)
    with pytest.raises(congestion_service.CongestionError, match="already running"):
        congestion_service.start(profile_pct=[0], step_seconds=10)
    assert speedtest_service.run_speedtest() == {"skipped": True, "reason": "congestion-running"}


def test_refuses_while_speedtest_runs(session) -> None:
    speedtest_service._lock.acquire()
    try:
        with pytest.raises(congestion_service.CongestionError, match="speed test"):
            congestion_service.start(profile_pct=[0], step_seconds=10)
    finally:
        speedtest_service._lock.release()


@pytest.mark.parametrize("kw, msg", [
    ({"direction": "sideways"}, "direction"),
    ({"profile_pct": [0, 500]}, "profile_pct"),
    ({"step_seconds": 5}, "step_seconds"),
])
def test_rejects_bad_parameters(session, kw, msg) -> None:
    with pytest.raises(congestion_service.CongestionError, match=msg):
        congestion_service.start(**kw)


def test_real_mode_requires_a_server(session, monkeypatch) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.get_settings(), "mock_mode", False)
    with pytest.raises(congestion_service.CongestionError, match="server_host"):
        congestion_service.start()


def test_close_orphans_after_restart(session) -> None:
    from app.core.timeutils import utc_now

    session.add(Experiment(
        experiment_id="EXP-CONG-20260101T000000Z", name="x", scenario="CONGESTION",
        start_timestamp=utc_now(), created_at=utc_now(), notes="trigger=manual",
    ))
    session.commit()

    assert congestion_service.close_orphans() == 1
    session.expire_all()
    exp = session.query(Experiment).one()
    assert exp.end_timestamp is not None
    assert "aborted: service restart" in exp.notes


def test_parse_iperf3_reverse_result() -> None:
    out = json.dumps({"end": {
        "sum_sent": {"bits_per_second": 235.1e6, "retransmits": 412},
        "sum_received": {"bits_per_second": 232.8e6},
    }})
    assert congestion_service.parse_iperf3(out) == {
        "success": True, "achieved_mbps": 232.8, "retransmits": 412, "error": None,
    }


def test_parse_iperf3_errors() -> None:
    busy = json.dumps({"start": {}, "end": {},
                       "error": "the server is busy running a test. try again later"})
    assert congestion_service.parse_iperf3(busy) == {
        "success": False, "error": "the server is busy running a test. try again later",
    }
    assert congestion_service.parse_iperf3("", "iperf3: error - unable to connect")["error"] == (
        "iperf3: error - unable to connect")


def test_api_runs_and_status(session, monkeypatch) -> None:
    from app.web import routes_api

    monkeypatch.setattr(congestion_service._stop, "wait", lambda _s: False)
    _run_ramp(profile_pct=[0, 100], step_seconds=10)

    runs = routes_api.congestion_runs(hours=1)
    assert len(runs) == 1 and len(runs[0]["steps"]) == 3
    st = routes_api.congestion_status()
    assert st["running"] is False
    assert st["defaults"]["failure_at_pct"] == 100
