"""Synthetic telemetry source for development on the Mac (``MOCK_MODE=true``).

Every observation it produces is tagged ``data_source=MOCK`` and must never enter
a scientific export mixed with REAL data (the export layer enforces this).

The generator models a plausible small-office WAN: diurnal traffic, slowly
drifting latency with occasional spikes, correlated CPU/session load, and rare
short "blips". It is only meant to exercise the dashboard, database, scheduler
and feature pipeline - not to resemble any real failure process.
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


class MockSource:
    """Drop-in replacement for the FortiGate source, backed by an RNG."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._rx_bytes = self._rng.randint(10**10, 10**11)
        self._tx_bytes = self._rng.randint(10**10, 10**11)
        self._rx_packets = self._rx_bytes // 800
        self._tx_packets = self._tx_bytes // 800
        self._rx_errors = self._rng.randint(0, 50)
        self._tx_errors = self._rng.randint(0, 50)
        self._rx_drops = self._rng.randint(0, 200)
        self._tx_drops = self._rng.randint(0, 200)
        self._uptime = self._rng.randint(3 * 86400, 40 * 86400)

    def fetch(self, ctx: CycleContext) -> SourceSnapshot:
        now = ctx.scheduled_timestamp
        local = to_local(now)
        # Diurnal factor: peak around 15:00 local, trough around 04:00.
        hour = local.hour + local.minute / 60.0
        diurnal = 0.35 + 0.65 * max(0.0, math.sin((hour - 6) / 24 * 2 * math.pi) * 0.5 + 0.5)
        jitter_noise = self._rng.gauss(0, 1)
        blip = self._rng.random() < 0.01  # ~1% of samples get a transient spike

        rx_mbps = max(0.2, diurnal * 90 * self._rng.uniform(0.8, 1.2))
        tx_mbps = max(0.2, diurnal * 35 * self._rng.uniform(0.8, 1.2))
        self._uptime += 60
        self._rx_bytes += int(rx_mbps * 1e6 / 8 * 60)
        self._tx_bytes += int(tx_mbps * 1e6 / 8 * 60)
        self._rx_packets += int(rx_mbps * 1e6 / 8 * 60 / 750)
        self._tx_packets += int(tx_mbps * 1e6 / 8 * 60 / 750)
        if self._rng.random() < 0.05:
            self._rx_drops += self._rng.randint(0, 3)
        if self._rng.random() < 0.03:
            self._rx_errors += self._rng.randint(0, 2)

        base_latency = 14 + 6 * diurnal + 2.5 * jitter_noise + (55 if blip else 0)
        latency = round(max(3.0, base_latency), 2)
        jitter = round(max(0.2, 1.8 + 0.9 * abs(jitter_noise) + (12 if blip else 0)), 2)
        loss = round(max(0.0, self._rng.gauss(0.05, 0.1) + (3.5 if blip else 0)), 3)

        cpu = round(min(99.0, 8 + 45 * diurnal + 4 * abs(jitter_noise) + (20 if blip else 0)), 1)
        mem = round(min(99.0, 38 + 12 * diurnal + self._rng.uniform(-2, 2)), 1)
        sessions = int(max(20, 300 * diurnal + self._rng.gauss(0, 40) + (500 if blip else 0)))

        metrics: dict[str, Any] = {
            "rx_bytes": self._rx_bytes,
            "tx_bytes": self._tx_bytes,
            "rx_packets": self._rx_packets,
            "tx_packets": self._tx_packets,
            "rx_errors": self._rx_errors,
            "tx_errors": self._tx_errors,
            "rx_drops": self._rx_drops,
            "tx_drops": self._tx_drops,
            "cpu_pct": cpu,
            "memory_pct": mem,
            "disk_pct": None,  # 50F typically does not report disk
            "uptime_seconds": self._uptime,
            "temperature_c": round(self._rng.uniform(38, 52), 1),
            "active_sessions": sessions,
            "new_sessions_per_second": round(max(0.0, self._rng.gauss(12, 4)), 2),
            "tcp_sessions": int(sessions * 0.7),
            "udp_sessions": int(sessions * 0.25),
            "interface_status": "up",
            "interface_speed": "1000full",
            "interface_duplex": "full",
            "interface_mtu": 1500,
            "interface_speed_mbps": 1000.0,
            "link_status": "up",
            "sla_status": "down" if blip and self._rng.random() > 0.5 else "up",
            "availability": 100.0,
            "default_route_present": True,
            "latency_ms": latency,
            "jitter_ms": jitter,
            "packet_loss_pct": loss,
        }

        sla = [
            SlaReading(
                health_check_name=name,
                label=meta.get("label", name.lower()),
                target=meta.get("target"),
                latency_ms=round(latency + self._rng.uniform(-3, 6), 2),
                jitter_ms=round(jitter + self._rng.uniform(-0.5, 1.5), 2),
                packet_loss_pct=round(max(0.0, loss + self._rng.uniform(-0.05, 0.2)), 3),
                sla_state="up" if not blip or self._rng.random() > 0.5 else "down",
                probe_sessions=self._rng.randint(1, 5),
            )
            for name, meta in (ctx.sla_targets or {"HC_Mock": {"label": "mock", "target": "1.1.1.1"}}).items()
        ]

        return SourceSnapshot(
            data_source="MOCK",
            success=True,
            api_latency_ms=round(self._rng.uniform(15, 60), 1),
            identification={
                "device_hostname": _HOSTNAME,
                "device_model": _MODEL,
                "serial_number": _SERIAL,
                "fortios_version": _FOS,
                "wan_interface": ctx.wan_interface,
            },
            metrics=metrics,
            sla=sla,
            raw_json={"mock": True, "diurnal_factor": round(diurnal, 3), "blip": blip},
        )

    def probe_icmp(self, ctx: CycleContext) -> list[ProbeReading]:
        """Fake ICMP results consistent with the mock latency."""
        base = self._rng.uniform(10, 25)
        out: list[ProbeReading] = []
        for name, meta in (ctx.sla_targets or {}).items():
            avg = round(base + self._rng.uniform(-4, 8), 2)
            out.append(
                ProbeReading(
                    label=meta.get("label", name.lower()),
                    address=meta.get("target", "1.1.1.1"),
                    packets_sent=5,
                    packets_received=5,
                    packet_loss_pct=0.0,
                    rtt_min_ms=round(avg - 2, 2),
                    rtt_avg_ms=avg,
                    rtt_max_ms=round(avg + 3, 2),
                    rtt_mdev_ms=round(self._rng.uniform(0.3, 2.0), 2),
                    success=True,
                )
            )
        return out
