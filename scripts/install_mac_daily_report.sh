#!/usr/bin/env bash
# Install the daily thesis-progress email as a launchd agent on the Mac.
# Runs at 16:00 local (after the 15:00 training). A missed run fires on wake.
# Needs the secrets file ~/.config/pnl_report.env (SMTP + from/to). Log:
# ~/fortigate_rpi_archive/report.log. Uninstall:
#   launchctl bootout gui/$(id -u)/com.fortigate-collector.daily-report
#   rm ~/Library/LaunchAgents/com.fortigate-collector.daily-report.plist
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.fortigate-collector.daily-report"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/fortigate_rpi_archive/report.log"
PY="$APP_DIR/.venv/bin/python"

mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$LOG")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>scripts.thesis_progress_report</string>
    <string>--send</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL -> $PLIST (runs 16:00 local; log: $LOG)"
