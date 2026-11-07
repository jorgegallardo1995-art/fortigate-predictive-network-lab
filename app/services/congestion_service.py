"""Controlled congestion ramps (CONGESTION experiments) generated with iperf3.

A ramp offers a growing share of the WAN capacity (``device.wan_speed_*``)
from the Pi to an iperf3 server on the Internet, one fixed-length step at a
time: e.g. 0 % (baseline) -> 20 -> 40 -> 60 -> 80 -> 100 -> 120 % -> 0 %
(recovery). The whole ramp is registered as an ``experiments`` row
(``EXP-CONG-<utc>``, scenario CONGESTION) and every step as a ``load_steps``
row with offered vs. achieved throughput, so the telemetry recorded meanwhile
is fully traceable to the load that caused it.

T_f (``failure_timestamp``) is fixed by protocol, not inferred from the
telemetry: the start of the first step at or above ``congestion.failure_at_pct``
(default 100 % = the link is saturated). An aborted ramp that never reaches
that step has no T_f.

iperf3 (plain TCP, no TLS) is used instead of HTTP downloads: it can hold an
exact rate for minutes and barely loads the Pi 3's CPU. It needs a server you
control - public iperf3 servers are single-client and often busy. In MOCK
mode no traffic is generated.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.config import get_config, get_settings
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.database.database import session_scope
from app.database.models import DATA_SOURCE_MOCK, DATA_SOURCE_REAL, Experiment, LoadStep
from app.services.event_service import record_event

logger = get_logger("congestion")

EXPERIMENT_PREFIX = "EXP-CONG-"
OPERATOR = "congestion-generator"
DIRECTIONS = ("download", "upload")


class CongestionError(RuntimeError):
    """The ramp cannot start (busy, not configured, bad parameters)."""


_state_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_proc: subprocess.Popen | None = None
_status: dict[str, Any] = {}


# --- public API -------------------------------------------------------------
def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def start(
    direction: str | None = None,
    *,
    trigger: str = "manual",
    profile_pct: list[float] | None = None,
    step_seconds: int | None = None,
) -> dict[str, Any]:
    """Register the experiment and start the ramp in a background thread."""
    global _thread
    from app.services import speedtest_service

    cfg = get_config()
    settings = get_settings()
    direction = direction or cfg.get("congestion.direction", "download")
    if direction not in DIRECTIONS:
        raise CongestionError(f"direction must be one of {DIRECTIONS}")
    profile = [float(p) for p in (profile_pct or cfg.get("congestion.profile_pct", []))]
    if not profile or any(p < 0 or p > 200 for p in profile):
        raise CongestionError("profile_pct must be a non-empty list of 0-200 (% of link capacity)")
    step = int(step_seconds or cfg.get("congestion.step_seconds", 180))
    if not 10 <= step <= 900:
        raise CongestionError("step_seconds must be between 10 and 900")
    # A custom step length applies to the recovery step too.
    recovery = step if step_seconds else int(cfg.get("congestion.recovery_seconds", step))
    if not settings.mock_mode:
        if not cfg.get("congestion.server_host"):
            raise CongestionError("congestion.server_host is not set - an iperf3 server is required")
        if shutil.which(cfg.get("congestion.iperf3_binary", "iperf3")) is None:
            raise CongestionError("iperf3 not found - sudo apt install iperf3")

    capacity = float(cfg.get(
        "device.wan_speed_down_mbps" if direction == "download" else "device.wan_speed_up_mbps", 0
    ) or 0)
    if capacity <= 0:
        raise CongestionError("device.wan_speed_*_mbps must be set to compute the offered load")
    plan = [(p, step) for p in profile] + ([(0.0, recovery)] if recovery > 0 else [])

    with _state_lock:
        if is_running():
            raise CongestionError("a congestion ramp is already running")
        if speedtest_service.is_running():
            raise CongestionError("a speed test is running - retry in a minute")

        now = utc_now()
        exp_id = f"{EXPERIMENT_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}"
        failure_at = float(cfg.get("congestion.failure_at_pct", 100))
        with session_scope() as session:
            session.add(Experiment(
                experiment_id=exp_id,
                name=f"Rampa de congestión ({direction})",
                scenario="CONGESTION",
                description=(
                    f"iperf3 {direction} ramp, {len(profile)} steps x {step}s "
                    f"({', '.join(f'{p:g}%' for p in profile)} of {capacity:g} Mbps), "
                    f"recovery {recovery}s; T_f = start of first step >= {failure_at:g}%"
                ),
                start_timestamp=now,
                operator=OPERATOR,
                notes=f"trigger={trigger}",
                created_at=now,
            ))
            record_event(session, "EXPERIMENT_START", f"Experiment {exp_id} (CONGESTION, {direction})",
                         experiment_id=exp_id,
                         data_source=DATA_SOURCE_MOCK if settings.mock_mode else DATA_SOURCE_REAL)

        _stop.clear()
        _status.clear()
        _status.update(
            experiment_id=exp_id, direction=direction, capacity_mbps=capacity,
            trigger=trigger, started_at=now.isoformat(), steps_total=len(plan),
            plan=[{"target_pct": p, "seconds": s} for p, s in plan],
            step_index=None, step_started_at=None, current_pct=None,
        )
        _thread = threading.Thread(
            target=_run, args=(exp_id, plan, direction, capacity, failure_at),
            name="congestion-ramp", daemon=True,
        )
        _thread.start()
    logger.info("congestion ramp %s started (%s, %d steps)", exp_id, direction, len(plan))
    return status()


def stop(wait_seconds: float = 10) -> dict[str, Any]:
    """Abort the running ramp (kills the current iperf3 step)."""
    _stop.set()
    proc = _proc
    if proc is not None and proc.poll() is None:
        proc.terminate()
    thread = _thread
    if thread is not None:
        thread.join(timeout=wait_seconds)
    return status()


def status() -> dict[str, Any]:
    cfg = get_config()
    host = cfg.get("congestion.server_host")
    return {
        "running": is_running(),
        **({k: v for k, v in _status.items()} if is_running() else {}),
        "server": f"{host}:{cfg.get('congestion.server_port', 5201)}" if host else None,
        "mock_mode": get_settings().mock_mode,
        "defaults": {
            "direction": cfg.get("congestion.direction", "download"),
            "profile_pct": list(cfg.get("congestion.profile_pct", [])),
            "step_seconds": int(cfg.get("congestion.step_seconds", 180)),
            "recovery_seconds": int(cfg.get("congestion.recovery_seconds", 180)),
            "failure_at_pct": float(cfg.get("congestion.failure_at_pct", 100)),
            "streams": int(cfg.get("congestion.streams", 4)),
            "schedule_hours_utc": list(cfg.get("congestion.schedule_hours_utc", [])),
        },
    }


def list_runs(since: datetime) -> list[dict[str, Any]]:
    """Congestion experiments since ``since`` with their steps, newest first."""
    with session_scope() as session:
        exps = session.scalars(
            select(Experiment)
            .where(Experiment.experiment_id.like(f"{EXPERIMENT_PREFIX}%"),
                   Experiment.start_timestamp >= since)
            .order_by(Experiment.start_timestamp.desc())
        ).all()
        steps = session.scalars(
            select(LoadStep)
            .where(LoadStep.experiment_id.in_([e.experiment_id for e in exps]))
            .order_by(LoadStep.step_index)
        ).all() if exps else []
        by_exp: dict[str, list[dict[str, Any]]] = {}
        for s in steps:
            by_exp.setdefault(s.experiment_id, []).append(_serialize_step(s))
        return [
            {
                "experiment_id": e.experiment_id,
                "name": e.name,
                "description": e.description,
                "start_timestamp": e.start_timestamp.isoformat(),
                "end_timestamp": e.end_timestamp.isoformat() if e.end_timestamp else None,
                "failure_timestamp": e.failure_timestamp.isoformat() if e.failure_timestamp else None,
                "notes": e.notes,
                "steps": by_exp.get(e.experiment_id, []),
            }
            for e in exps
        ]


def close_orphans() -> int:
    """Close ramps left open by a service restart (their thread died with it)."""
    if is_running():
        return 0
    with session_scope() as session:
        open_exps = session.scalars(
            select(Experiment).where(
                Experiment.experiment_id.like(f"{EXPERIMENT_PREFIX}%"),
                Experiment.end_timestamp.is_(None),
            )
        ).all()
        for e in open_exps:
            last = session.scalars(
                select(LoadStep.finished_at)
                .where(LoadStep.experiment_id == e.experiment_id, LoadStep.finished_at.isnot(None))
                .order_by(LoadStep.finished_at.desc())
            ).first()
            e.end_timestamp = last or utc_now()
            e.notes = f"{e.notes or ''} | aborted: service restart".strip(" |")
            record_event(session, "EXPERIMENT_END",
                         f"Experiment {e.experiment_id} aborted by service restart",
                         experiment_id=e.experiment_id)
        if open_exps:
            logger.warning("closed %d congestion ramp(s) left open by a restart", len(open_exps))
        return len(open_exps)


# --- worker -----------------------------------------------------------------
def _run(exp_id: str, plan: list[tuple[float, int]], direction: str, capacity: float,
         failure_at: float) -> None:
    settings = get_settings()
    data_source = DATA_SOURCE_MOCK if settings.mock_mode else DATA_SOURCE_REAL
    failure_ts: datetime | None = None
    outcome = "completed"
    try:
        for i, (pct, seconds) in enumerate(plan):
            if _stop.is_set():
                outcome = "aborted"
                break
            started = utc_now()
            _status.update(step_index=i, step_started_at=started.isoformat(), current_pct=pct,
                           step_seconds=seconds)
            if failure_ts is None and pct >= failure_at:
                failure_ts = started
                with session_scope() as session:
                    exp = session.scalars(select(Experiment).filter_by(experiment_id=exp_id)).one()
                    exp.failure_timestamp = failure_ts
            target = round(capacity * pct / 100, 3)
            if pct == 0:
                _stop.wait(seconds)
                result: dict[str, Any] = {"success": True}
            elif settings.mock_mode:
                _stop.wait(seconds)
                result = {"success": True, "achieved_mbps": round(min(target, capacity * 0.93), 3),
                          "retransmits": 0}
            else:
                result = _run_iperf3(direction, target, seconds)
            if _stop.is_set():
                outcome = "aborted"
            with session_scope() as session:
                session.add(LoadStep(
                    experiment_id=exp_id, data_source=data_source, step_index=i,
                    direction=direction, target_pct=pct, target_mbps=target,
                    started_at=started, finished_at=utc_now(),
                    achieved_mbps=result.get("achieved_mbps"),
                    retransmits=result.get("retransmits"),
                    success=result["success"] and outcome != "aborted",
                    error=result.get("error"),
                ))
            logger.info("ramp %s step %d/%d: %.0f%% (%.1f Mbps offered) -> %s", exp_id, i + 1,
                        len(plan), pct, target,
                        result.get("achieved_mbps", "baseline") if result["success"]
                        else f"ERROR {result.get('error')}")
    except Exception:
        outcome = "failed"
        logger.exception("congestion ramp %s crashed", exp_id)
    finally:
        with session_scope() as session:
            exp = session.scalars(select(Experiment).filter_by(experiment_id=exp_id)).one()
            exp.end_timestamp = utc_now()
            exp.notes = f"{exp.notes or ''} | {outcome}".strip(" |")
            if outcome != "completed" and failure_ts is None:
                exp.notes += " (no T_f: saturation step never reached)"
            record_event(session, "EXPERIMENT_END", f"Experiment {exp_id} {outcome}",
                         experiment_id=exp_id, data_source=data_source)
        logger.info("congestion ramp %s %s", exp_id, outcome)


def _run_iperf3(direction: str, target_mbps: float, seconds: int) -> dict[str, Any]:
    """One iperf3 client run at a fixed total rate, split across N streams."""
    global _proc
    cfg = get_config()
    streams = max(1, int(cfg.get("congestion.streams", 4)))
    per_stream_bps = int(target_mbps * 1e6 / streams)
    cmd = [
        cfg.get("congestion.iperf3_binary", "iperf3"),
        "-c", str(cfg.get("congestion.server_host")),
        "-p", str(cfg.get("congestion.server_port", 5201)),
        "-t", str(seconds), "-P", str(streams), "-b", str(per_stream_bps),
        "--connect-timeout", "5000", "-J",
    ]
    if direction == "download":
        cmd.append("-R")  # server sends, the Pi receives
    try:
        _proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        stdout, stderr = _proc.communicate(timeout=seconds + 30)
    except subprocess.TimeoutExpired:
        _proc.kill()
        _proc.communicate()
        return {"success": False, "error": "iperf3 timeout"}
    finally:
        _proc = None
    return parse_iperf3(stdout, stderr)


def parse_iperf3(stdout: str, stderr: str = "") -> dict[str, Any]:
    try:
        data = json.loads(stdout)
    except ValueError:
        detail = (stderr or stdout or "no output").strip().splitlines()
        return {"success": False, "error": detail[-1] if detail else "no output"}
    end = data.get("end") or {}
    received = end.get("sum_received") or {}
    if data.get("error") and not received:
        return {"success": False, "error": data["error"]}
    bps = received.get("bits_per_second")
    return {
        "success": bps is not None,
        "achieved_mbps": round(bps / 1e6, 3) if bps is not None else None,
        "retransmits": (end.get("sum_sent") or {}).get("retransmits"),
        "error": data.get("error"),  # e.g. interrupted mid-step: partial result kept
    }


def _serialize_step(s: LoadStep) -> dict[str, Any]:
    return {
        "step_index": s.step_index,
        "direction": s.direction,
        "target_pct": s.target_pct,
        "target_mbps": s.target_mbps,
        "started_at": s.started_at.isoformat(),
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        "achieved_mbps": s.achieved_mbps,
        "retransmits": s.retransmits,
        "success": s.success,
        "error": s.error,
    }
