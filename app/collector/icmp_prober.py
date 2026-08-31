"""ICMP prober - runs on the collector host (Raspberry Pi) as an independent check.

This measures the path *Pi -> target* (which traverses the FortiGate WAN). It is a
cross-check for the FortiGate Performance SLA, NOT a replacement: the measurement
point is different and that is recorded (``ProbeResult`` is a separate table).

Uses the system ``ping`` binary (available on macOS and Raspberry Pi OS) so no
raw-socket privileges are required.
"""

from __future__ import annotations

import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass

from app.core.config import get_config
from app.core.logging import get_logger

logger = get_logger("collector.icmp")

_RTT_LINE = re.compile(
    r"(?:round-trip|rtt)[^=]*=\s*([\d.]+)/([\d.]+)/([\d.]+)(?:/([\d.]+))?"
)
_LOSS = re.compile(r"([\d.]+)%\s*packet loss")
_TX_RX = re.compile(r"(\d+)\s*packets transmitted,\s*(\d+)\s*(?:packets )?received")


@dataclass(slots=True)
class ProbeReading:
    label: str
    address: str
    packets_sent: int | None = None
    packets_received: int | None = None
    packet_loss_pct: float | None = None
    rtt_min_ms: float | None = None
    rtt_avg_ms: float | None = None
    rtt_max_ms: float | None = None
    rtt_mdev_ms: float | None = None
    success: bool = False


class IcmpProber:
    """Ping a configured list of targets once per acquisition cycle."""

    def __init__(self) -> None:
        cfg = get_config()
        self.enabled: bool = bool(cfg.get("icmp_prober.enabled", True))
        self.count: int = int(cfg.get("icmp_prober.count", 5))
        self.timeout: int = int(cfg.get("icmp_prober.timeout_seconds", 2))
        self.targets: list[dict[str, str]] = list(cfg.get("icmp_prober.targets", []))
        self._ping = shutil.which("ping")
        if self.enabled and not self._ping:
            logger.warning("ping binary not found - ICMP prober disabled")
            self.enabled = False

    def probe_all(self) -> list[ProbeReading]:
        if not self.enabled:
            return []
        return [self._probe_one(t["label"], t["address"]) for t in self.targets]

    def _probe_one(self, label: str, address: str) -> ProbeReading:
        reading = ProbeReading(label=label, address=address)
        cmd = [self._ping, "-n", "-c", str(self.count), "-W", str(self.timeout), address]  # type: ignore[list-item]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.count * (self.timeout + 1) + 5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("ICMP probe to %s (%s) failed: %s", label, address, exc)
            return reading

        out = proc.stdout
        if m := _TX_RX.search(out):
            reading.packets_sent = int(m.group(1))
            reading.packets_received = int(m.group(2))
        if m := _LOSS.search(out):
            reading.packet_loss_pct = float(m.group(1))
        if m := _RTT_LINE.search(out):
            reading.rtt_min_ms = float(m.group(1))
            reading.rtt_avg_ms = float(m.group(2))
            reading.rtt_max_ms = float(m.group(3))
            reading.rtt_mdev_ms = float(m.group(4)) if m.group(4) else None

        # Fallback jitter estimate if the OS did not report mdev.
        if reading.rtt_mdev_ms is None and reading.rtt_min_ms is not None:
            try:
                reading.rtt_mdev_ms = round(
                    statistics.pstdev([reading.rtt_min_ms, reading.rtt_avg_ms, reading.rtt_max_ms]), 3  # type: ignore[list-item]
                )
            except statistics.StatisticsError:
                reading.rtt_mdev_ms = None

        reading.success = proc.returncode == 0 and (reading.packet_loss_pct or 100.0) < 100.0
        return reading
