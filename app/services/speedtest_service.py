"""Active throughput tests with the Ookla Speedtest CLI.

Scheduled once per hour (``speedtest.minute``), each test saturates the WAN
for ~10-20 s from the collector host and stores one ``speed_tests`` row -
successful or not, so failed attempts stay visible. The FortiGate itself is
never touched; this is plain outbound traffic from the Pi.

The CLI is the official Ookla binary (``speedtest``), not the Python
``speedtest-cli`` clone, which is CPU-bound and caps far below the link rate on
a Raspberry Pi 3. Running it implies accepting Ookla's license / GDPR terms
(``--accept-license --accept-gdpr``).

In MOCK mode no traffic is generated: a synthetic result tagged
``data_source=MOCK`` is stored instead.
"""

from __future__ import annotations

import json
import random
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
from app.database.models import DATA_SOURCE_MOCK, DATA_SOURCE_REAL, SpeedTest

logger = get_logger("speedtest")

# One test at a time: two concurrent tests would split the link between them.
_lock = threading.Lock()


def run_speedtest(trigger: str = "scheduled") -> dict[str, Any]:
    """Run one test and store it. Returns the stored row as a dict, or
    ``{"skipped": True, ...}`` when another test is already running."""
    from app.services import congestion_service

    if congestion_service.is_running():
        # The ramp owns the link: a test now would both distort it and be distorted.
        logger.info("speedtest skipped: a congestion ramp is running")
        return {"skipped": True, "reason": "congestion-running"}
    if not _lock.acquire(blocking=False):
        logger.info("speedtest skipped: another test is still running")
        return {"skipped": True, "reason": "already-running"}
    try:
        settings = get_settings()
        started = utc_now()
        if settings.mock_mode:
            parsed, error, raw = _mock_result(), None, None
        else:
            parsed, error, raw = _run_cli()
        finished = utc_now()

        row = SpeedTest(
            started_at=started,
            finished_at=finished,
            data_source=DATA_SOURCE_MOCK if settings.mock_mode else DATA_SOURCE_REAL,
            trigger=trigger,
            success=error is None,
            error=error,
            raw_json=raw,
            **parsed,
        )
        with session_scope() as session:
            session.add(row)
            session.flush()
            out = serialize(row)

        if error:
            logger.error("speedtest failed (%s): %s", trigger, error)
        else:
            logger.info(
                "speedtest ok (%s): down=%.1f Mbps up=%.1f Mbps ping=%.1f ms server=%s (%.0fs)",
                trigger, out["download_mbps"] or 0, out["upload_mbps"] or 0,
                out["ping_ms"] or 0, out["server_name"], (finished - started).total_seconds(),
            )
        return out
    finally:
        _lock.release()


def is_running() -> bool:
    return _lock.locked()


def _run_cli() -> tuple[dict[str, Any], str | None, dict | None]:
    cfg = get_config()
    binary = cfg.get("speedtest.binary", "speedtest")
    if shutil.which(binary) is None:
        return {}, f"Ookla Speedtest CLI not found ({binary!r}) - see scripts/install_speedtest.sh", None

    cmd = [binary, "--format=json", "--accept-license", "--accept-gdpr"]
    server_id = cfg.get("speedtest.server_id")
    if server_id:
        cmd += ["--server-id", str(server_id)]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd, capture_output=True, text=True,
            timeout=int(cfg.get("speedtest.timeout_seconds", 120)), check=False,
        )
    except subprocess.TimeoutExpired:
        return {}, "timeout", None

    result = _last_result_json(proc.stdout)
    if result is None:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {}, f"exit {proc.returncode}: {detail[-1] if detail else 'no output'}", None
    return parse_result(result), None, result


def _last_result_json(stdout: str) -> dict | None:
    """The CLI may print log lines before the final ``{"type": "result"}``."""
    for line in reversed(stdout.strip().splitlines()):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return obj
    return None


def _mbps(bandwidth_bytes_per_s: Any) -> float | None:
    return round(bandwidth_bytes_per_s * 8 / 1e6, 3) if bandwidth_bytes_per_s is not None else None


def parse_result(r: dict[str, Any]) -> dict[str, Any]:
    """Map an Ookla ``--format=json`` result onto SpeedTest columns."""
    ping, down, up = r.get("ping") or {}, r.get("download") or {}, r.get("upload") or {}
    server = r.get("server") or {}
    location = ", ".join(p for p in (server.get("location"), server.get("country")) if p)
    return {
        "ping_ms": ping.get("latency"),
        "jitter_ms": ping.get("jitter"),
        "download_mbps": _mbps(down.get("bandwidth")),
        "upload_mbps": _mbps(up.get("bandwidth")),
        "download_latency_ms": (down.get("latency") or {}).get("iqm"),
        "upload_latency_ms": (up.get("latency") or {}).get("iqm"),
        "packet_loss_pct": r.get("packetLoss"),
        "download_bytes": down.get("bytes"),
        "upload_bytes": up.get("bytes"),
        "server_id": server.get("id"),
        "server_name": server.get("name"),
        "server_location": location or None,
        "isp": r.get("isp"),
        "result_url": (r.get("result") or {}).get("url"),
    }


def _mock_result() -> dict[str, Any]:
    down, up = random.uniform(150, 200), random.uniform(80, 100)  # noqa: S311
    ping = random.uniform(5, 9)  # noqa: S311
    return {
        "ping_ms": round(ping, 3), "jitter_ms": round(random.uniform(0.2, 1.5), 3),  # noqa: S311
        "download_mbps": round(down, 3), "upload_mbps": round(up, 3),
        "download_latency_ms": round(ping + random.uniform(5, 40), 3),  # noqa: S311
        "upload_latency_ms": round(ping + random.uniform(5, 60), 3),  # noqa: S311
        "packet_loss_pct": 0.0,
        "download_bytes": int(down * 1e6 / 8 * 10), "upload_bytes": int(up * 1e6 / 8 * 10),
        "server_id": 0, "server_name": "MOCK", "server_location": "MOCK", "isp": "MOCK",
        "result_url": None,
    }


def serialize(t: SpeedTest) -> dict[str, Any]:
    return {
        "id": t.id,
        "started_at": t.started_at.isoformat(),
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "duration_seconds": (
            round((t.finished_at - t.started_at).total_seconds(), 1) if t.finished_at else None
        ),
        "data_source": t.data_source,
        "trigger": t.trigger,
        "success": t.success,
        "error": t.error,
        "ping_ms": t.ping_ms,
        "jitter_ms": t.jitter_ms,
        "download_mbps": t.download_mbps,
        "upload_mbps": t.upload_mbps,
        "download_latency_ms": t.download_latency_ms,
        "upload_latency_ms": t.upload_latency_ms,
        "packet_loss_pct": t.packet_loss_pct,
        "download_bytes": t.download_bytes,
        "upload_bytes": t.upload_bytes,
        "server_id": t.server_id,
        "server_name": t.server_name,
        "server_location": t.server_location,
        "isp": t.isp,
        "result_url": t.result_url,
    }


def list_tests(since: datetime, data_source: str) -> list[dict[str, Any]]:
    """Tests since ``since``, newest first."""
    with session_scope() as session:
        rows = session.scalars(
            select(SpeedTest)
            .where(SpeedTest.started_at >= since, SpeedTest.data_source == data_source)
            .order_by(SpeedTest.started_at.desc())
        ).all()
        return [serialize(t) for t in rows]


def summary(since: datetime, data_source: str) -> dict[str, Any]:
    tests = list_tests(since, data_source)
    ok = [t for t in tests if t["success"]]

    def stats(key: str) -> dict[str, float | None]:
        vals = [t[key] for t in ok if t[key] is not None]
        if not vals:
            return {"avg": None, "min": None, "max": None}
        return {
            "avg": round(sum(vals) / len(vals), 2), "min": round(min(vals), 2),
            "max": round(max(vals), 2),
        }

    cfg = get_config()
    return {
        "total": len(tests),
        "successful": len(ok),
        "failed": len(tests) - len(ok),
        "download_mbps": stats("download_mbps"),
        "upload_mbps": stats("upload_mbps"),
        "ping_ms": stats("ping_ms"),
        "download_latency_ms": stats("download_latency_ms"),
        "last": tests[0] if tests else None,
        "running": is_running(),
        "schedule": {
            "enabled": bool(cfg.get("speedtest.enabled", True)),
            "minute_utc": int(cfg.get("speedtest.minute", 30)),
            "skip_hours_utc": list(cfg.get("speedtest.skip_hours_utc", [3])),
            "server_id": cfg.get("speedtest.server_id"),
        },
    }
