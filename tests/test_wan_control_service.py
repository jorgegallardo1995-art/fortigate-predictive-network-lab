"""Controlled WAN fault injection: disabled by default, registers an induced
experiment with T_f, always restores (incl. on startup), and never touches the
device in MOCK mode."""

from __future__ import annotations

import httpx
import pytest
import respx
from app.api.fortigate_control_client import FortiGateControlClient
from app.core.timeutils import utc_now
from app.database.models import Experiment, NetworkEvent
from app.services import wan_control_service as wc

BASE = "https://fortigate.test/api/v2"  # test env FORTIGATE_HOST; httpx drops :443


class _Cfg:
    def __init__(self, **over):
        self.over = {
            "wan_control.enabled": True,
            "wan_control.interface": "wan1",
            "wan_control.outage_seconds": 60,
            "wan_control.max_outage_seconds": 10800,
            "wan_control.normal_in_kbps": 250000,
            "wan_control.normal_out_kbps": 250000,
            "wan_control.ramp_profile_kbps": [250000, 100000, 20000, 5000],
            "wan_control.ramp_step_seconds": 30,
            "wan_control.ramp_failure_at_kbps": 20000,
            "wan_control.ramp_speedtest_per_step": False,
            **over,
        }

    def get(self, key, default=None):
        return self.over.get(key, default)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(wc, "_cfg", lambda: _Cfg())


@pytest.fixture(autouse=True)
def _restore_after():
    yield
    wc._stop.set()
    if wc._thread is not None:
        wc._thread.join(timeout=5)


def _run_outage(**kw) -> str:
    st = wc.start_outage(**kw)
    wc._thread.join(timeout=10)
    assert not wc.is_running()
    return st["experiment_id"]


def test_disabled_by_default(session, monkeypatch) -> None:
    monkeypatch.setattr(wc, "_cfg", lambda: _Cfg(**{"wan_control.enabled": False}))
    with pytest.raises(wc.WanControlError, match="disabled"):
        wc.start_outage()


def test_outage_registers_induced_experiment_with_tf(session, enabled, monkeypatch) -> None:
    monkeypatch.setattr(wc._stop, "wait", lambda _s: False)  # no real sleeping
    exp_id = _run_outage(duration_seconds=60)

    exp = session.query(Experiment).filter_by(experiment_id=exp_id).one()
    assert exp.scenario == "WAN_FAILURE"
    assert exp.failure_timestamp == exp.start_timestamp  # T_f = outage start
    assert exp.end_timestamp is not None
    assert "induced=WAN_DOWN" in exp.notes and exp.notes.endswith("completed")
    evs = [e.event_type for e in session.query(NetworkEvent).filter_by(experiment_id=exp_id)]
    assert evs == ["EXPERIMENT_START", "EXPERIMENT_END"]


def test_rejects_duration_over_hard_max(session, enabled) -> None:
    with pytest.raises(wc.WanControlError, match="between 30"):
        wc.start_outage(duration_seconds=99999)


def test_only_one_outage_at_a_time(session, enabled) -> None:
    wc.start_outage(duration_seconds=60)
    with pytest.raises(wc.WanControlError, match="already active"):
        wc.start_outage(duration_seconds=60)


def test_stop_restores_and_closes(session, enabled) -> None:
    wc.start_outage(duration_seconds=600)
    st = wc.stop()
    assert st["running"] is False
    exp = session.query(Experiment).filter(Experiment.experiment_id.like("EXP-WANDOWN-%")).one()
    assert exp.end_timestamp is not None
    assert "aborted" in exp.notes


def test_restore_on_startup_closes_open_outage(session) -> None:
    session.add(Experiment(
        experiment_id="EXP-WANDOWN-20260101T000000Z", name="Corte", scenario="WAN_FAILURE",
        start_timestamp=utc_now(), failure_timestamp=utc_now(), created_at=utc_now(),
        notes="trigger=scheduled | induced=WAN_DOWN",
    ))
    session.commit()

    assert wc.restore_on_startup() is True
    session.expire_all()
    exp = session.query(Experiment).one()
    assert exp.end_timestamp is not None
    assert "restored: service restart" in exp.notes


def test_real_mode_requires_control_token(session, enabled, monkeypatch) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.get_settings(), "mock_mode", False)
    with pytest.raises(wc.WanControlError, match="CONTROL_TOKEN"):
        wc.start_outage()


# --- bandwidth ramp --------------------------------------------------------
def test_ramp_registers_congestion_experiment_steps_and_tf(session, enabled, monkeypatch) -> None:
    from app.database.models import LoadStep

    monkeypatch.setattr(wc._stop, "wait", lambda _s: False)  # no real sleeping
    st = wc.start_bandwidth_ramp()
    exp_id = st["experiment_id"]
    wc._thread.join(timeout=10)
    assert not wc.is_running()

    exp = session.query(Experiment).filter_by(experiment_id=exp_id).one()
    steps = session.query(LoadStep).filter_by(experiment_id=exp_id).order_by(LoadStep.step_index).all()
    assert exp.experiment_id.startswith("EXP-CONG-")  # shows on the Congestión page
    assert exp.scenario == "CONGESTION"
    assert "method=shaper" in exp.notes and exp.notes.endswith("completed")
    assert [s.target_mbps for s in steps] == [250.0, 100.0, 20.0, 5.0]
    assert steps[0].direction == "shaper"
    # T_f = first ceiling at/below ramp_failure_at_kbps (20000) -> step index 2
    assert exp.failure_timestamp == steps[2].started_at


def test_ramp_and_outage_are_mutually_exclusive(session, enabled) -> None:
    wc.start_bandwidth_ramp(step_seconds=30)
    with pytest.raises(wc.WanControlError, match="already active"):
        wc.start_outage(duration_seconds=60)


def test_ramp_rejects_bad_profile(session, enabled) -> None:
    with pytest.raises(wc.WanControlError, match="ramp_profile_kbps"):
        wc.start_bandwidth_ramp(profile_kbps=[])


# --- control client (write path, mocked device) ----------------------------
@respx.mock
def test_control_client_puts_status_down() -> None:
    route = respx.put(f"{BASE}/cmdb/system/interface/wan1").mock(
        return_value=httpx.Response(200, json={"status": "success", "http_status": 200})
    )
    with FortiGateControlClient(token="write-token-xyz") as c:
        c.set_status("wan1", up=False)
    assert route.called
    import json
    assert json.loads(route.calls.last.request.read()) == {"status": "down"}


@respx.mock
def test_control_client_reads_interface() -> None:
    respx.get(f"{BASE}/cmdb/system/interface/wan1").mock(
        return_value=httpx.Response(200, json={"results": [
            {"name": "wan1", "status": "up", "inbandwidth": 250000, "outbandwidth": 250000}]})
    )
    with FortiGateControlClient(token="write-token-xyz") as c:
        iface = c.get_interface("wan1")
    assert iface["status"] == "up" and iface["inbandwidth"] == 250000


def test_control_client_inert_without_token() -> None:
    from app.api.fortigate_client import FortiGateAuthError

    c = FortiGateControlClient(token="")
    assert c.configured is False
    with pytest.raises(FortiGateAuthError):
        c.set_status("wan1", up=False)


def test_api_status_and_runs(session, enabled, monkeypatch) -> None:
    from app.web import routes_api

    monkeypatch.setattr(wc._stop, "wait", lambda _s: False)
    _run_outage(duration_seconds=60)

    st = routes_api.wan_control_status()
    assert st["running"] is False and st["enabled"] is True
    runs = routes_api.wan_control_runs(hours=1)
    assert len(runs) == 1 and runs[0]["failure_timestamp"] is not None
