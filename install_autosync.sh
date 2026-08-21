#!/bin/sh
# macOS: install a launchd agent that syncs SimpleFIN every 4 hours (and at login),
# so the dashboard stays fresh without the app or a browser being open.
# Usage: ./install_autosync.sh          (from the audit directory)
#        ./install_autosync.sh remove   (to uninstall)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.$(whoami).audit-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$1" = "remove" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

PY="${PYTHON:-$(command -v python3)}"
[ -n "$PY" ] || { echo "python3 not found"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$DIR/data"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array><string>$PY</string><string>$DIR/sync.py</string><string>30</string></array>
    <key>StartInterval</key><integer>14400</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$DIR/data/sync.log</string>
    <key>StandardErrorPath</key><string>$DIR/data/sync.log</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed $LABEL — syncing every 4h; log: data/sync.log"
