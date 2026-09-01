"""Synthetic telemetry source for development / demo (``MOCK_MODE=true``).

Every observation it produces is tagged ``data_source=MOCK`` and must never enter
a scientific export mixed with REAL data (the export layer enforces this).

The generator is a **pure function of the sample timestamp** - no accumulating
instance state. This matters because the scheduler creates a fresh
``TelemetryCollector`` (and therefore a fresh ``MockSource``) every cycle: any
in-memory counter would jump between cycles and produce nonsensical throughput
deltas. Deriving everything from wall-clock time keeps the cumulative counters
strictly monotonic and the 60-second deltas realistic, and makes the mock series
reproducible.

It models a plausible small-office WAN (diurnal load, drifting latency, rare
blips). It is not meant to resemble any real failure process.
"""

from __future__ import annotations

import math
import random
from typing import Any

from app.collector.icmp_prober import ProbeReading
from app.core.logging import get_logger
from app.core.timeutils import to_local
from app.schemas.telemetry import CycleContext, SlaReading, SourceSnapshot

logger = get_logger("collector.mock")

_MODEL = "FortiGate-50F"
_HOSTNAME = "FGT50F-LAB-MOCK"
_SERIAL = "FGT50FMOCK000001"
_FOS = "v7.4.4,build2662 (MOCK)"

# Fixed epoch (2024-12-01 UTC). Cumulative counters and uptime are measured from
# here, so they only ever increase - across cycles and across process restarts.
_EPOCH = 1_733_011_200
_DAY = 86_400.0
_W = 2 * math.pi / _DAY

# Nominal average WAN throughput for the mock link (bytes/s).
_AVG_RX_BPS = 42_000_000 / 8
_AVG_TX_BPS = 16_000_000 / 8
_AVG_PACKET_BYTES = 750


def _diurnal(epoch: float) -> float:
    """Smooth 0.4..1.3 load factor, peak ~15:00, trough ~03:00 (UTC-based shape)."""
    return 1.0 + 0.45 * math.sin(_W * epoch - math.pi / 2) * 0.5 + 0.35 * math.sin(_W * epoch)


def _cumulative_bytes(t: float, avg_bps: float) -> int:
    """Analytic integral of a strictly-positive diurnal rate -> monotonic bytes."""
    # rate(x) = avg_bps * (1 + 0.3 sin(_W x))  (always >= 0.7 avg_bps > 0)
    integral = avg_bps * (t - (0.3 / _W) * math.cos(_W * t) + 0.3 / _W)
    return max(0, int(integral))


class MockSource:
    """Drop-in replacement for the FortiGate source. Stateless between cycles."""

    def __init__(self, seed: int | None = None) -> None:
        self._extra_seed = seed or 0

    def _rng(self, epoch: float) -> random.Random:
        """Deterministic per-minute RNG so a given timestamp always yields the same sample."""
        return random.Random(int(epoch // 60) + self._extra_seed)

    def fetch(self, ctx: CycleContext) -> SourceSnapshot:
        now = ctx.scheduled_timestamp
        epoch = now.timestamp()
        t = epoch - _EPOCH
        rng = self._rng(epoch)
        local = to_local(now)

        load = _diurnal(epoch)
        noise = rng.gauss(0, 1)
        blip = rng.random() < 0.012  # ~1% of samples get a transient spike

        rx_bytes = _cumulative_bytes(t, _AVG_RX_BPS)
        tx_bytes = _cumulative_bytes(t, _AVG_TX_BPS)
        # small, slowly-growing error/drop counters (monotonic)
        rx_drops = int(t / 3600 * 1.5) + (int(t // 60) % 3)
        tx_drops = int(t / 3600 * 0.8)
        rx_errors = int(t / 7200)
        tx_errors = int(t / 9000)

        latency = round(max(3.0, 13 + 6 * (load - 1) + 2.5 * noise + (55 if blip else 0)), 2)
        jitter = round(max(0.2, 1.8 + 0.9 * abs(noise) + (12 if blip else 0)), 2)
        loss = round(max(0.0, rng.gauss(0.05, 0.1) + (3.5 if blip else 0)), 3)

        cpu = round(min(99.0, 10 + 38 * (load - 0.4) + 4 * abs(noise) + (22 if blip else 0)), 1)
        mem = round(min(99.0, 38 + 10 * (load - 1) + rng.uniform(-2, 2)), 1)
        sessions = int(max(20, 320 * load + rng.gauss(0, 35) + (500 if blip else 0)))

        metrics: dict[str, Any] = {
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "rx_packets": rx_bytes // _AVG_PACKET_BYTES,
            "tx_packets": tx_bytes // _AVG_PACKET_BYTES,
            "rx_errors": rx_errors,
            "tx_errors": tx_errors,
            "rx_drops": rx_drops,
            "tx_drops": tx_drops,
            "cpu_pct": cpu,
            "memory_pct": mem,
            "disk_pct": None,  # 50F typically does not report disk
            "uptime_seconds": int(t),
            "temperature_c": round(46 + 4 * (load - 1) + rng.uniform(-1.5, 1.5), 1),
            "active_sessions": sessions,
            "new_sessions_per_second": round(max(0.0, 12 * load + rng.gauss(0, 3)), 2),
            "tcp_sessions": int(sessions * 0.7),
            "udp_sessions": int(sessions * 0.25),
            "interface_status": "up",
            "interface_speed": "1000full",
            "interface_duplex": "full",
            "interface_mtu": 1500,
            "interface_speed_mbps": float(ctx.wan_speed_down_mbps or 1000.0),
            "link_status": "up",
            "sla_status": "down" if blip and rng.random() > 0.5 else "up",
            "availability": 100.0,
            "default_route_present": True,
            "latency_ms": latency,
            "jitter_ms": jitter,
            "packet_loss_pct": loss,
        }

        targets = ctx.sla_targets or {"HC_Mock": {"label": "mock", "target": "1.1.1.1"}}
        sla = [
            SlaReading(
                health_check_name=name,
                label=meta.get("label", name.lower()),
                target=meta.get("target"),
                latency_ms=round(latency + rng.uniform(-3, 6), 2),
                jitter_ms=round(max(0.1, jitter + rng.uniform(-0.5, 1.5)), 2),
                packet_loss_pct=round(max(0.0, loss + rng.uniform(-0.05, 0.2)), 3),
                sla_state="up" if not blip or rng.random() > 0.5 else "down",
                probe_sessions=rng.randint(1, 5),
            )
            for name, meta in targets.items()
        ]

        return SourceSnapshot(
            data_source="MOCK",
            success=True,
            api_latency_ms=round(rng.uniform(15, 60), 1),
            identification={
                "device_hostname": _HOSTNAME,
                "device_model": _MODEL,
                "serial_number": _SERIAL,
                "fortios_version": _FOS,
                "wan_interface": ctx.wan_interface,
            },
            metrics=metrics,
            sla=sla,
            raw_json={"mock": True, "load_factor": round(load, 3), "blip": blip},
        )

    def probe_icmp(self, ctx: CycleContext) -> list[ProbeReading]:
        """Fake ICMP results consistent with the mock latency."""
        rng = self._rng(ctx.scheduled_timestamp.timestamp())
        base = 12 + 8 * (_diurnal(ctx.scheduled_timestamp.timestamp()) - 1)
        out: list[ProbeReading] = []
        for name, meta in (ctx.sla_targets or {}).items():
            avg = round(max(1.0, base + rng.uniform(-4, 8)), 2)
            out.append(
                ProbeReading(
                    label=meta.get("label", name.lower()),
                    address=meta.get("target", "1.1.1.1"),
                    packets_sent=5,
                    packets_received=5,
                    packet_loss_pct=0.0,
                    rtt_min_ms=round(max(0.5, avg - 2), 2),
                    rtt_avg_ms=avg,
                    rtt_max_ms=round(avg + 3, 2),
                    rtt_mdev_ms=round(rng.uniform(0.3, 2.0), 2),
                    success=True,
                )
            )
        return out
