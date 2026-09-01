#!/usr/bin/env python3
"""ETAPA 6 - discover which FortiOS REST API endpoints your device supports.

Runs GET against every catalogued endpoint (plus a few extra candidates),
reports HTTP status + a preview of the response, and saves the full JSON of the
successful ones to ``data/exports/fortios_probe_<timestamp>/`` so the endpoint
paths in ``config/config.yaml`` can be adjusted with evidence.

This script only ever issues GET. It never changes the FortiGate.

Usage:
    python scripts/probe_fortios_endpoints.py
Requires a populated ``.env`` (FORTIGATE_HOST + FORTIGATE_API_TOKEN).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.endpoints import ENDPOINT_NOTES, resolve_endpoints
from app.api.fortigate_client import (
    FortiGateAuthError,
    FortiGateClient,
    FortiGateEndpointError,
    FortiGateError,
    FortiGateUnavailableError,
)
from app.core.config import get_settings

# FortiOS throttles bursts of admin/API requests (HTTP 429) and locks the source
# IP after repeated auth failures - pace the probe and bail out early on 429/401.
_REQUEST_SPACING_S = 1.0

# Extra candidate paths worth checking on 7.x (not in the default catalogue).
EXTRA_CANDIDATES: dict[str, str] = {
    "system_vdom_resource": "monitor/system/vdom-resource",
    "system_performance_status": "monitor/system/performance/status",
    "firewall_session_stats": "monitor/firewall/session",
    "system_available_certificates": "monitor/system/available-certificates",
    "system_dhcp": "monitor/system/dhcp",
    "system_ha_status": "monitor/system/ha-statistics",
    "network_arp": "monitor/network/arp",
    "system_link_monitor": "monitor/system/link-monitor",
}


def _preview(obj: object, limit: int = 400) -> str:
    text = json.dumps(obj, default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + " ...(truncated)"


def main() -> int:
    settings = get_settings()
    if not settings.fortigate_api_token:
        print("ERROR: FORTIGATE_API_TOKEN is empty. Fill in .env first.")
        return 2

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = settings.exports_dir / f"fortios_probe_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"FortiGate : {settings.fortigate_host}:{settings.fortigate_port}  vdom={settings.fortigate_vdom}")
    print(f"Output    : {out_dir}")
    print("-" * 78)

    summary: list[dict[str, object]] = []
    with FortiGateClient() as client:
        conn = client.test_connection()
        print(f"Connection: {_preview(conn)}")
        print("-" * 78)
        if not conn.get("reachable"):
            print("Device unreachable - aborting probe.")
            return 1
        if conn.get("error", "").startswith("auth"):
            print(
                "\nAuthentication failed (HTTP 401). Fix this on the FortiGate before probing:\n"
                "  * REST API admin -> Trusted Hosts must include this collector's IP (/32),\n"
                "    or set 0.0.0.0/0 temporarily for testing.\n"
                "  * The API key is shown only once at creation - regenerate it if unsure.\n"
                "  * Check the admin account is enabled and not locked out.\n"
                "Not probing further (repeated 401s trigger a source-IP lockout)."
            )
            return 1

        catalogue = {**resolve_endpoints(), **EXTRA_CANDIDATES}
        for key, path in catalogue.items():
            note = ENDPOINT_NOTES.get(key, "")
            time.sleep(_REQUEST_SPACING_S)
            try:
                resp = client.get_path(path)
                keys = list(resp.results.keys()) if isinstance(resp.results, dict) else "(list)"
                status = "OK"
                detail = f"{resp.elapsed_ms:5.0f} ms  keys={keys}"
                (out_dir / f"{key}.json").write_text(
                    json.dumps(resp.raw, indent=2, default=str), encoding="utf-8"
                )
            except FortiGateEndpointError:
                status, detail = "NOT_FOUND", "HTTP 404 - not supported on this FortiOS"
            except FortiGateAuthError as exc:
                status, detail = "AUTH", str(exc)
            except FortiGateUnavailableError as exc:
                status, detail = "UNAVAILABLE", str(exc)
            except FortiGateError as exc:
                status, detail = "ERROR", str(exc)

            print(f"[{status:11}] {key:28} {path}")
            if note:
                print(f"{'':14}{note}")
            print(f"{'':14}{detail}")
            summary.append({"key": key, "path": path, "status": status, "detail": str(detail)})

            if "429" in str(detail):
                print("\nHTTP 429 (rate limited) - stopping. Wait a few minutes and retry.")
                break

    (out_dir / "_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    ok = sum(1 for s in summary if s["status"] == "OK")
    print("-" * 78)
    print(f"{ok}/{len(summary)} endpoints responded. Full payloads saved under {out_dir}")
    print("Next: adjust config/config.yaml -> fortios_endpoints with the working paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
