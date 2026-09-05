#!/usr/bin/env bash
# Watchdog: restart the collector if its HTTP API stops responding.
#
# The acquisition loop and the web server share one process. If that process
# hangs (rather than crashes) systemd's Restart= never fires - so this runs on a
# timer, and after N consecutive unreachable checks it forces a restart.
set -u

PORT="${DASHBOARD_PORT:-8000}"
URL="http://127.0.0.1:${PORT}/api/status"
STATE_FILE="/run/fortigate-collector-health.fail"
MAX_FAILS=3

if curl -fsS --max-time 15 -o /dev/null "$URL"; then
    rm -f "$STATE_FILE"
    exit 0
fi

fails=$(( $(cat "$STATE_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$STATE_FILE"
echo "healthcheck: $URL unreachable ($fails/$MAX_FAILS consecutive)"

if [ "$fails" -ge "$MAX_FAILS" ]; then
    echo "healthcheck: restarting fortigate-collector after $fails failed checks"
    systemctl restart fortigate-collector
    rm -f "$STATE_FILE"
fi
