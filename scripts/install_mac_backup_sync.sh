#!/usr/bin/env bash
# Install scripts/sync_backups_to_mac.sh as a launchd agent on the Mac: runs at
# 08:00, 12:00, 16:00 and 20:00 (a missed run fires on wake). Log:
# ~/fortigate_rpi_archive/sync.log. Uninstall:
#   launchctl bootout gui/$(id -u)/com.fortigate-collector.backup-sync
#   rm ~/Library/LaunchAgents/com.fortigate-collector.backup-sync.plist
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.fortigate-collector.backup-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/fortigate_rpi_archive/sync.log"

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
    <string>$APP_DIR/scripts/sync_backups_to_mac.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL -> $PLIST (log: $LOG)"
