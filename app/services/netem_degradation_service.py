"""Injected latency / packet-loss degradation with Linux ``tc netem``.

The FortiGate 50E's LAN ports are a hard-switch, so per-port speed/duplex
throttling is not addressable via the API (it would hit the whole LAN and risk
a duplex mismatch). Instead we inject the degradation at the measurement host
with ``tc netem`` on ``eth0`` - the standard, citable way to emulate latency,
jitter and loss - ramping it up as a precursor before the failure instant.

This gives the reviewer's missing scenarios (LATENCY_DEGRADATION / PACKET_LOSS
preceding a failure, Bloque 2). It is registered as an experiment with a
protocol-defined T_f, and - like the shaper ramp - restores itself in a
``finally`` block and on startup, so a crash never leaves the link degraded.

netem on eth0 degrades ALL of the Pi's egress (its ICMP probes, and its calls
to the FortiGate API), so the injected latency/loss shows up in the probe
variables. In MOCK mode nothing is applied.
"""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.config import get_config, get_settings
from app.core.logging import get_logger
from app.core.timeutils import utc_now
from app.database.database import session_scope
from app.database.models import DATA_SOURCE_MOCK, DATA_SOURCE_REAL, Experiment
from app.services.event_service import record_event

logger = get_logger("netem")

EXPERIMENT_PREFIX = "EXP-NETEM-"
OPERATOR = "netem-degradation"


class NetemError(RuntimeError):
    """The degradation cannot start (disabled, busy, bad parameters)."""


_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_status: dict[str, Any] = {}


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def _iface() -> str:
    return get_config().get("netem.interface", "eth0")


def _tc(*args: str) -> subprocess.CompletedProcess:
    cmd = ["sudo", "-n", "tc", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def clear(iface: str | None = None) -> bool:
    """Remove any netem qdisc (idempotent). Verifies the real state rather than
    the del return code (deleting an already-absent qdisc errors harmlessly)."""
    if get_settings().mock_mode:
        return True
    iface = iface or _iface()
    _tc("qdisc", "del", "dev", iface, "root")  # best effort
    show = _tc("qdisc", "show", "dev", iface)
    return "netem" not in (show.stdout or "")


def _apply(iface: str, delay_ms: float, jitter_ms: float, loss_pct: float) -> None:
    if get_settings().mock_mode:
        logger.warning("MOCK: netem %s delay=%sms loss=%s%%", iface, delay_ms, loss_pct)
        return
    args = ["qdisc", "replace", "dev", iface, "root", "netem"]
    if delay_ms > 0:
        args += ["delay", f"{delay_ms}ms"]
        if jitter_ms > 0:
            args += [f"{jitter_ms}ms"]
    if loss_pct > 0:
        args += ["loss", f"{loss_pct}%"]
    if len(args) == 6:  # nothing to apply (0/0/0) -> clean baseline step
        clear(iface)
        return
    r = _tc(*args)
    if r.returncode != 0:
        raise NetemError(f"tc netem failed: {(r.stderr or r.stdout).strip()[:200]}")


def restore_on_startup() -> None:
    """Clear leftover netem and close any experiment a restart left open."""
    closed = False
    with session_scope() as session:
        for e in session.scalars(select(Experiment).where(
            Experiment.experiment_id.like(f"{EXPERIMENT_PREFIX}%"),
            Experiment.end_timestamp.is_(None),
        )).all():
            e.end_timestamp = utc_now()
            e.notes = f"{e.notes or ''} | restored: service restart".strip(" |")
            record_event(session, "EXPERIMENT_END",
                         f"Netem degradation {e.experiment_id} ended (cleared on startup)",
                         experiment_id=e.experiment_id)
            closed = True
    cfg = get_config()
    if cfg.get("netem.enabled", False) and not get_settings().mock_mode:
        clear()
    if closed:
        logger.warning("cleared netem degradation left open by a restart")


def _require_enabled() -> None:
    if not get_config().get("netem.enabled", False):
        raise NetemError("netem.enabled is false - latency/loss injection is disabled")


def start(
    profile: list[dict[str, float]] | None = None,
    *,
    step_seconds: int | None = None,
    trigger: str = "manual",
) -> dict[str, Any]:
    """Run a latency/loss ramp in the background. ``profile`` is a list of steps
    like ``{"delay_ms": 100, "jitter_ms": 20, "loss_pct": 2}``."""
    global _thread
    cfg = get_config()
    _require_enabled()
    steps = list(profile or cfg.get("netem.profile", []))
    if not steps:
        raise NetemError("netem.profile is empty")
    max_delay = float(cfg.get("netem.max_delay_ms", 500))
    max_loss = float(cfg.get("netem.max_loss_pct", 20))
    for s in steps:
        if float(s.get("delay_ms", 0)) > max_delay or float(s.get("loss_pct", 0)) > max_loss:
            raise NetemError(f"a step exceeds the safety caps ({max_delay} ms / {max_loss}%)")
    step = int(step_seconds or cfg.get("netem.step_seconds", 120))
    if not 10 <= step <= 900:
        raise NetemError("step_seconds must be between 10 and 900")

    with _lock:
        if is_running():
            raise NetemError("a netem degradation is already running")
        from app.services import wan_control_service

        if wan_control_service.is_running():
            raise NetemError("a WAN fault is active - retry later")

        settings = get_settings()
        data_source = DATA_SOURCE_MOCK if settings.mock_mode else DATA_SOURCE_REAL
        now = utc_now()
        exp_id = f"{EXPERIMENT_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}"
        fail_at = float(cfg.get("netem.failure_at_delay_ms", max(float(s.get("delay_ms", 0)) for s in steps)))
        scenario = cfg.get("netem.scenario", "LATENCY_DEGRADATION")
        with session_scope() as session:
            session.add(Experiment(
                experiment_id=exp_id,
                name=f"Degradación inyectada ({scenario})",
                scenario=scenario,
                description=(
                    f"INDUCED {scenario} via tc netem on {_iface()}: "
                    + ", ".join(f"{s.get('delay_ms',0):g}ms/{s.get('loss_pct',0):g}%" for s in steps)
                    + f" x {step}s; T_f = first step with delay >= {fail_at:g} ms"
                ),
                start_timestamp=now, operator=OPERATOR,
                notes=f"trigger={trigger} | induced=NETEM | method=tc-netem",
                created_at=now,
            ))
            record_event(session, "EXPERIMENT_START",
                         f"INDUCED {scenario} {exp_id} (tc netem)",
                         experiment_id=exp_id, data_source=data_source)

        _stop.clear()
        _status.clear()
        _status.update(experiment_id=exp_id, scenario=scenario, trigger=trigger,
                       started_at=now.isoformat(), steps_total=len(steps), step_index=None,
                       plan=steps)
        _thread = threading.Thread(
            target=_run, args=(exp_id, steps, step, fail_at, data_source),
            name="netem-degradation", daemon=True,
        )
        _thread.start()
    logger.warning("INDUCED netem degradation %s started (%d steps)", exp_id, len(steps))
    return status()


def stop(wait_seconds: float = 10) -> dict[str, Any]:
    _stop.set()
    if _thread is not None and _thread.is_alive():
        _thread.join(timeout=wait_seconds)
    else:
        clear()
    return status()


def _run(exp_id: str, steps: list[dict[str, float]], step_s: int, fail_at: float,
         data_source: str) -> None:
    iface = _iface()
    failure_ts = None
    outcome = "completed"
    try:
        for i, s in enumerate(steps):
            if _stop.is_set():
                outcome = "aborted"
                break
            delay = float(s.get("delay_ms", 0))
            started = utc_now()
            _status.update(step_index=i, step_started_at=started.isoformat(), current=s)
            if failure_ts is None and delay >= fail_at:
                failure_ts = started
                with session_scope() as session:
                    exp = session.scalars(select(Experiment).filter_by(experiment_id=exp_id)).one()
                    exp.failure_timestamp = failure_ts
            _apply(iface, delay, float(s.get("jitter_ms", 0)), float(s.get("loss_pct", 0)))
            logger.info("netem %s step %d/%d: delay=%sms jitter=%sms loss=%s%%",
                        exp_id, i + 1, len(steps), delay, s.get("jitter_ms", 0), s.get("loss_pct", 0))
            _stop.wait(step_s)
    except Exception:
        outcome = "failed"
        logger.exception("netem degradation %s crashed", exp_id)
    finally:
        restored = clear(iface)
        with session_scope() as session:
            exp = session.scalars(select(Experiment).filter_by(experiment_id=exp_id)).one()
            exp.end_timestamp = utc_now()
            exp.notes = f"{exp.notes or ''} | {outcome}".strip(" |")
            if not restored:
                exp.notes += " | CLEAR FAILED"
            record_event(session, "EXPERIMENT_END",
                         f"Netem degradation {exp_id} {outcome}"
                         + ("" if restored else " - CLEAR FAILED"),
                         experiment_id=exp_id, data_source=data_source)
        logger.warning("netem degradation %s %s (restored=%s)", exp_id, outcome, restored)


def status() -> dict[str, Any]:
    cfg = get_config()
    live = is_running()
    return {
        "running": live,
        **(dict(_status) if live else {}),
        "enabled": bool(cfg.get("netem.enabled", False)),
        "mock_mode": get_settings().mock_mode,
        "interface": _iface(),
        "defaults": {
            "scenario": cfg.get("netem.scenario", "LATENCY_DEGRADATION"),
            "profile": list(cfg.get("netem.profile", [])),
            "step_seconds": int(cfg.get("netem.step_seconds", 120)),
            "failure_at_delay_ms": cfg.get("netem.failure_at_delay_ms"),
            "schedule_enabled": bool(cfg.get("netem.schedule_enabled", False)),
        },
    }


def list_runs(since: datetime) -> list[dict[str, Any]]:
    with session_scope() as session:
        exps = session.scalars(
            select(Experiment)
            .where(Experiment.experiment_id.like(f"{EXPERIMENT_PREFIX}%"),
                   Experiment.start_timestamp >= since)
            .order_by(Experiment.start_timestamp.desc())
        ).all()
        return [
            {
                "experiment_id": e.experiment_id, "scenario": e.scenario,
                "start_timestamp": e.start_timestamp.isoformat(),
                "end_timestamp": e.end_timestamp.isoformat() if e.end_timestamp else None,
                "failure_timestamp": e.failure_timestamp.isoformat() if e.failure_timestamp else None,
                "notes": e.notes,
            }
            for e in exps
        ]
