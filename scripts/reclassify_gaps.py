#!/usr/bin/env python3
"""One-off: re-label existing collection_gaps using the host's boot history,
then rebuild the availability timeline.

New gaps are classified live by the collector; this back-fills the ones recorded
before that logic existed. Run it on the Pi (needs journalctl + the DB).

    python scripts/reclassify_gaps.py           # dry run
    python scripts/reclassify_gaps.py --apply
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.database.database import session_scope
from app.database.models import CollectionGap, NetworkEvent


def host_boots() -> list[datetime]:
    """Start instant (UTC) of every boot journald still knows about."""
    try:
        out = subprocess.run(
            ["journalctl", "--list-boots", "-o", "json", "--no-pager"],
            capture_output=True, text=True, timeout=15, check=False,
        ).stdout
        data = json.loads(out)
        return sorted(
            datetime.fromtimestamp(b["first_entry"] / 1_000_000, UTC) for b in data
        )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return []


def main() -> int:
    apply = "--apply" in sys.argv
    boots = host_boots()
    print(f"known host boots: {len(boots)}")
    for b in boots:
        print(f"  {b.isoformat()}")
    print("-" * 70)

    changes = 0
    with session_scope() as session:
        gaps = session.execute(select(CollectionGap).order_by(CollectionGap.gap_start)).scalars().all()
        for g in gaps:
            boot_in_gap = next(
                (b for b in boots if g.gap_start <= b <= g.gap_end + timedelta(minutes=2)), None
            )
            if boot_in_gap:
                stopped = session.execute(
                    select(NetworkEvent.id).where(
                        NetworkEvent.event_type == "COLLECTOR_STOPPED",
                        NetworkEvent.timestamp_utc >= g.gap_start - timedelta(seconds=180),
                        NetworkEvent.timestamp_utc <= g.gap_end,
                    ).limit(1)
                ).first()
                new_reason = "SYSTEM_REBOOT" if stopped else "POWER_FAILURE"
                new_rebooted, new_boot = True, boot_in_gap
            else:
                new_reason = "COLLECTOR_FAILURE" if g.duration_seconds > 180 else g.reason
                new_rebooted, new_boot = False, None

            if (g.reason, g.host_rebooted) != (new_reason, new_rebooted):
                print(
                    f"gap {g.gap_start:%Y-%m-%d %H:%M} +{g.duration_seconds/3600:.1f}h  "
                    f"{g.reason:18} -> {new_reason:16} (rebooted={new_rebooted})"
                )
                changes += 1
                if apply:
                    g.reason = new_reason
                    g.host_rebooted = new_rebooted
                    g.host_boot_time = new_boot
        if not apply:
            session.rollback()

    print("-" * 70)
    print(f"{changes} gap(s) {'updated' if apply else 'would change (dry run - pass --apply)'}")

    if apply:
        from app.services.availability_service import rebuild_all

        n = rebuild_all(full=True)
        print(f"availability timeline rebuilt: {n} slots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
