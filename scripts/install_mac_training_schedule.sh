#!/usr/bin/env bash
# Install scripts/train_scheduled.sh as a launchd agent on the Mac: 3 training
# runs per day (07:00, 15:00, 23:00 local). A missed run fires on wake.
# Log: ~/fortigate_rpi_archive/training.log. Uninstall:
#   launchctl bootout gui/$(id -u)/com.fortigate-collector.training
#   rm ~/Library/LaunchAgents/com.fortigate-collector.training.plist
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.fortigate-collector.training"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/fortigate_rpi_archive/training.log"

mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$LOG")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$APP_DIR/scripts/train_scheduled.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL -> $PLIST"
echo "runs at 10:00, 12:00, 15:00 local; log: $LOG"
