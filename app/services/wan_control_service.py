"""Controlled WAN faults injected through the FortiGate API (write path).

Two fault types, both registered as experiments and clearly marked as INDUCED
so ml/ can separate them from organic events and keep them out of the
predictive training set:

* **outage** - bring ``wan_control.interface`` administratively down for a
  bounded window (scenario WAN_FAILURE). This is the nightly scheduled outage.
* **bandwidth ramp** - progressively shrink the interface shaping ceiling
  (``inbandwidth``/``outbandwidth``) step by step, optionally saturating each
  step with a speed test, then restore. Registered as a CONGESTION experiment
  so it shows on the Congestión page. Unlike traffic flooding, this needs no
  external server and produces a *gradual* degradation (rising utilisation,
  falling throughput) - the precursor pattern the LSTM needs to predict.

Only one WAN fault runs at a time (outage and ramp share the same lock).

Safety is the whole point of this module:

* Everything is OFF unless ``wan_control.enabled`` is true AND a control token
  is configured. In MOCK mode nothing is sent to the device.
* An outage always has a hard maximum duration and is restored in a ``finally``
  block, even if the thread crashes.
* On process startup :func:`restore_on_startup` forces the interface back to
  the known-good baseline and closes any experiment a restart left open, so a
  crash mid-outage can never leave the link down.
* Restore is deterministic: status **up** + bandwidth set to the configured
  baseline (``wan_control.normal_*_kbps``) - it never depends on state held in
  memory that a restart would lose.
"""

from __future__ import annotations

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

logger = get_logger("wan-control")

EXPERIMENT_PREFIX = "EXP-WANDOWN-"
OPERATOR = "wan-fault-injector"
SEVERITY_CRITICAL = "CRITICAL"


class WanControlError(RuntimeError):
    """The fault cannot be injected (disabled, not configured, already running)."""


_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_status: dict[str, Any] = {}


# --- public API -------------------------------------------------------------
def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def _cfg() -> Any:
    return get_config()


def _interface() -> str:
    return _cfg().get("wan_control.interface", "wan1")


def _control_client():
    from app.api.fortigate_control_client import FortiGateControlClient

    return FortiGateControlClient()


def _require_enabled() -> None:
    cfg = _cfg()
    if not cfg.get("wan_control.enabled", False):
        raise WanControlError("wan_control.enabled is false - fault injection is disabled")
    if not get_settings().mock_mode and not _control_client().configured:
        raise WanControlError("FORTIGATE_CONTROL_TOKEN is not configured")


def start_outage(duration_seconds: int | None = None, *, trigger: str = "manual") -> dict[str, Any]:
    """Bring the WAN down for ``duration_seconds`` in a background thread."""
    global _thread
    cfg = _cfg()
    _require_enabled()
    duration = int(duration_seconds or cfg.get("wan_control.outage_seconds", 10800))
    hard_max = int(cfg.get("wan_control.max_outage_seconds", 10800))
    if not 30 <= duration <= hard_max:
        raise WanControlError(f"duration must be between 30 and {hard_max} seconds")

    with _lock:
        if is_running():
            raise WanControlError("a WAN fault is already active")
        settings = get_settings()
        data_source = DATA_SOURCE_MOCK if settings.mock_mode else DATA_SOURCE_REAL
        now = utc_now()
        exp_id = f"{EXPERIMENT_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}"
        iface = _interface()
        with session_scope() as session:
            session.add(Experiment(
                experiment_id=exp_id,
                name=f"Corte programado de WAN ({iface})",
                scenario="WAN_FAILURE",
                description=(
                    f"INDUCED scheduled outage: {iface} admin-down for {duration}s "
                    f"(trigger={trigger}). T_f = outage start. Exclude from predictive training."
                ),
                start_timestamp=now,
                failure_timestamp=now,  # the outage instant is the ground-truth T_f
                operator=OPERATOR,
                notes=f"trigger={trigger} | induced=WAN_DOWN",
                created_at=now,
            ))
            record_event(session, "EXPERIMENT_START",
                         f"INDUCED outage {exp_id}: {iface} going down for {duration}s",
                         severity=SEVERITY_CRITICAL, experiment_id=exp_id,
                         related_interface=iface, data_source=data_source)

        _stop.clear()
        _status.clear()
        _status.update(experiment_id=exp_id, interface=iface, trigger=trigger,
                       started_at=now.isoformat(), duration_seconds=duration,
                       ends_at=(now.timestamp() + duration))
        _thread = threading.Thread(
            target=_run_outage, args=(exp_id, iface, duration, data_source),
            name="wan-outage", daemon=True,
        )
        _thread.start()
    logger.warning("INDUCED WAN outage %s started (%s, %ds)", exp_id, iface, duration)
    return status()


CONGESTION_PREFIX = "EXP-CONG-"


def start_bandwidth_ramp(
    profile_kbps: list[int] | None = None,
    *,
    step_seconds: int | None = None,
    trigger: str = "manual",
) -> dict[str, Any]:
    """Shrink the shaping ceiling step by step, then restore. Runs in the
    background; registered as a CONGESTION experiment with one load_steps row
    per ceiling."""
    global _thread
    cfg = _cfg()
    _require_enabled()
    source = profile_kbps if profile_kbps is not None else cfg.get("wan_control.ramp_profile_kbps", [])
    profile = [int(k) for k in source]
    if not profile or any(k <= 0 for k in profile):
        raise WanControlError("ramp_profile_kbps must be a non-empty list of positive kbps ceilings")
    step = int(step_seconds or cfg.get("wan_control.ramp_step_seconds", 120))
    if not 20 <= step <= 900:
        raise WanControlError("ramp step_seconds must be between 20 and 900")

    with _lock:
        if is_running():
            raise WanControlError("a WAN fault is already active")
        from app.services import netem_degradation_service

        if netem_degradation_service.is_running():
            raise WanControlError("a netem degradation is active - retry later")
        settings = get_settings()
        data_source = DATA_SOURCE_MOCK if settings.mock_mode else DATA_SOURCE_REAL
        now = utc_now()
        exp_id = f"{CONGESTION_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}"
        iface = _interface()
        baseline = int(cfg.get("wan_control.normal_in_kbps", 0)) or max(profile)
        fail_at = int(cfg.get("wan_control.ramp_failure_at_kbps", 20000))
        with session_scope() as session:
            session.add(Experiment(
                experiment_id=exp_id,
                name=f"Rampa de shaper ({iface})",
                scenario="CONGESTION",
                description=(
                    f"INDUCED bandwidth ramp on {iface}: ceilings "
                    f"{', '.join(f'{k // 1000}M' for k in profile)} kbps x {step}s, "
                    f"restore to {baseline} kbps; T_f = first ceiling <= {fail_at} kbps"
                ),
                start_timestamp=now,
                operator=OPERATOR,
                notes=f"trigger={trigger} | induced=BW_RAMP | method=shaper",
                created_at=now,
            ))
            record_event(session, "EXPERIMENT_START",
                         f"INDUCED bandwidth ramp {exp_id} on {iface}",
                         experiment_id=exp_id, related_interface=iface, data_source=data_source)

        _stop.clear()
        _status.clear()
        _status.update(mode="ramp", experiment_id=exp_id, interface=iface, trigger=trigger,
                       started_at=now.isoformat(), steps_total=len(profile), baseline_kbps=baseline,
                       plan=[{"ceiling_kbps": k} for k in profile], step_index=None)
        _thread = threading.Thread(
            target=_run_ramp,
            args=(exp_id, iface, profile, step, baseline, fail_at, data_source),
            name="wan-bw-ramp", daemon=True,
        )
        _thread.start()
    logger.warning("INDUCED bandwidth ramp %s started (%s, %d steps)", exp_id, iface, len(profile))
    return status()


def _run_ramp(exp_id: str, iface: str, profile: list[int], step: int, baseline: int,
              fail_at: int, data_source: str) -> None:
    from app.database.models import LoadStep

    cfg = _cfg()
    per_step_speedtest = bool(cfg.get("wan_control.ramp_speedtest_per_step", True))
    failure_ts = None
    outcome = "completed"
    try:
        for i, ceiling in enumerate(profile):
            if _stop.is_set():
                outcome = "aborted"
                break
            started = utc_now()
            _status.update(step_index=i, step_started_at=started.isoformat(), current_kbps=ceiling)
            _set_bandwidth(iface, ceiling)
            if failure_ts is None and ceiling <= fail_at:
                failure_ts = started
                with session_scope() as session:
                    exp = session.scalars(select(Experiment).filter_by(experiment_id=exp_id)).one()
                    exp.failure_timestamp = failure_ts

            achieved = None
            if per_step_speedtest and not _stop.is_set():
                achieved = _measure_step()  # saturates this ceiling, returns down Mbps
            _stop.wait(max(0, step - (utc_now() - started).total_seconds()))
            if _stop.is_set():
                outcome = "aborted"

            with session_scope() as session:
                session.add(LoadStep(
                    experiment_id=exp_id, data_source=data_source, step_index=i,
                    direction="shaper", target_pct=round(ceiling / baseline * 100, 1),
                    target_mbps=round(ceiling / 1000, 3), started_at=started,
                    finished_at=utc_now(), achieved_mbps=achieved, retransmits=None,
                    success=outcome != "aborted",
                ))
            logger.info("ramp %s step %d/%d: ceiling %d kbps -> measured %s Mbps",
                        exp_id, i + 1, len(profile), ceiling, achieved)
    except Exception:
        outcome = "failed"
        logger.exception("bandwidth ramp %s crashed", exp_id)
    finally:
        restored = _restore(iface, reason=outcome)
        with session_scope() as session:
            exp = session.scalars(select(Experiment).filter_by(experiment_id=exp_id)).one()
            exp.end_timestamp = utc_now()
            exp.notes = f"{exp.notes or ''} | {outcome}".strip(" |")
            if not restored:
                exp.notes += " | RESTORE FAILED"
            record_event(session, "EXPERIMENT_END",
                         f"Bandwidth ramp {exp_id} {outcome}"
                         + ("" if restored else " - RESTORE FAILED"),
                         severity=SEVERITY_CRITICAL if not restored else "INFO",
                         experiment_id=exp_id, related_interface=iface, data_source=data_source)
        logger.warning("bandwidth ramp %s %s (restored=%s)", exp_id, outcome, restored)


def _set_bandwidth(iface: str, ceiling_kbps: int) -> None:
    if get_settings().mock_mode:
        logger.warning("MOCK: pretend %s ceiling=%d kbps", iface, ceiling_kbps)
        return
    with _control_client() as c:
        c.set_bandwidth(iface, ceiling_kbps, ceiling_kbps)


def _measure_step() -> float | None:
    """Saturate the current ceiling with one speed test; return download Mbps."""
    try:
        from app.services import speedtest_service

        result = speedtest_service.run_speedtest(trigger="ramp")
        return result.get("download_mbps") if result.get("success") else None
    except Exception:
        logger.exception("per-step speed test failed")
        return None


def stop(wait_seconds: float = 15) -> dict[str, Any]:
    """Abort the active outage and restore the link now."""
    _stop.set()
    thread = _thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=wait_seconds)
    else:
        _restore(_interface(), reason="manual-stop")
    return status()


def restore_on_startup() -> bool:
    """Force the link back to baseline and close any outage left open by a
    restart. Called from the app lifespan before the scheduler starts."""
    closed = False
    with session_scope() as session:
        open_exps = session.scalars(
            select(Experiment).where(
                Experiment.experiment_id.like(f"{EXPERIMENT_PREFIX}%"),
                Experiment.end_timestamp.is_(None),
            )
        ).all()
        for e in open_exps:
            e.end_timestamp = utc_now()
            e.notes = f"{e.notes or ''} | restored: service restart".strip(" |")
            record_event(session, "EXPERIMENT_END",
                         f"Outage {e.experiment_id} ended (link restored on startup)",
                         experiment_id=e.experiment_id, related_interface=_interface())
            closed = True
    # Always assert the baseline, even if no experiment was open: cheap and safe.
    cfg = _cfg()
    if cfg.get("wan_control.enabled", False) and not get_settings().mock_mode \
            and _control_client().configured:
        try:
            _restore(_interface(), reason="startup")
        except Exception:
            logger.exception("startup restore failed - CHECK THE WAN LINK MANUALLY")
    if closed:
        logger.warning("closed WAN outage(s) left open by a restart and restored the link")
    return closed


def status() -> dict[str, Any]:
    cfg = _cfg()
    settings = get_settings()
    live = is_running()
    return {
        "running": live,
        **(dict(_status) if live else {}),
        "enabled": bool(cfg.get("wan_control.enabled", False)),
        "configured": settings.mock_mode or _control_client().configured,
        "mock_mode": settings.mock_mode,
        "interface": _interface(),
        "baseline_kbps": {
            "in": int(cfg.get("wan_control.normal_in_kbps", 0)),
            "out": int(cfg.get("wan_control.normal_out_kbps", 0)),
        },
        "schedule": {
            "enabled": bool(cfg.get("wan_control.schedule_enabled", False)),
            "start_local": cfg.get("wan_control.schedule_start_local", "00:00"),
            "outage_seconds": int(cfg.get("wan_control.outage_seconds", 10800)),
            "timezone": settings.timezone,
        },
        "ramp_schedule": {
            "enabled": bool(cfg.get("wan_control.ramp_schedule_enabled", False)),
            "minute_utc": int(cfg.get("wan_control.ramp_schedule_minute", 0)),
            "hours_utc": list(cfg.get("wan_control.ramp_schedule_hours_utc", [])),
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
                "experiment_id": e.experiment_id,
                "interface": e.name,
                "start_timestamp": e.start_timestamp.isoformat(),
                "end_timestamp": e.end_timestamp.isoformat() if e.end_timestamp else None,
                "failure_timestamp": e.failure_timestamp.isoformat() if e.failure_timestamp else None,
                "duration_seconds": (
                    round((e.end_timestamp - e.start_timestamp).total_seconds())
                    if e.end_timestamp else None
                ),
                "notes": e.notes,
            }
            for e in exps
        ]


# --- worker -----------------------------------------------------------------
def _run_outage(exp_id: str, iface: str, duration: int, data_source: str) -> None:
    outcome = "completed"
    try:
        _apply_down(iface)
        if _stop.wait(duration):
            outcome = "aborted"
    except Exception:
        outcome = "failed"
        logger.exception("WAN outage %s failed while going down", exp_id)
    finally:
        restored = _restore(iface, reason=outcome)
        with session_scope() as session:
            exp = session.scalars(select(Experiment).filter_by(experiment_id=exp_id)).one()
            exp.end_timestamp = utc_now()
            exp.notes = f"{exp.notes or ''} | {outcome}".strip(" |")
            if not restored:
                exp.notes += " | RESTORE FAILED"
            record_event(session, "EXPERIMENT_END",
                         f"Outage {exp_id} {outcome}" + ("" if restored else " - RESTORE FAILED"),
                         severity=SEVERITY_CRITICAL if not restored else "INFO",
                         experiment_id=exp_id, related_interface=iface, data_source=data_source)
        logger.warning("WAN outage %s %s (restored=%s)", exp_id, outcome, restored)


def _apply_down(iface: str) -> None:
    if get_settings().mock_mode:
        logger.warning("MOCK: pretend %s is down", iface)
        return
    with _control_client() as c:
        c.set_status(iface, up=False)


def _restore(iface: str, reason: str) -> bool:
    """Bring the interface back up at the baseline bandwidth. Best-effort but
    loud: returns False if it could not be confirmed."""
    if get_settings().mock_mode:
        logger.warning("MOCK: pretend %s is restored (%s)", iface, reason)
        return True
    cfg = _cfg()
    try:
        with _control_client() as c:
            c.set_status(iface, up=True)
            c.set_bandwidth(iface, int(cfg.get("wan_control.normal_in_kbps", 0)),
                            int(cfg.get("wan_control.normal_out_kbps", 0)))
        logger.warning("WAN link %s restored (%s)", iface, reason)
        return True
    except Exception:
        logger.exception("FAILED to restore %s (%s) - MANUAL ACTION MAY BE NEEDED", iface, reason)
        return False
