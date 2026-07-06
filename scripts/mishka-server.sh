#!/bin/zsh
# Toggle the Mishka Hub API server (the com.mishka-hub.api LaunchAgent) on
# and off — built to be wrapped in a macOS Shortcut and added to Control
# Center as a one-tap power toggle (docs/DEPLOYMENT.md §"Control Center
# toggle").
#
# Deliberately does NOT touch the cloudflared tunnel: that's a root
# LaunchDaemon (would need a password prompt on every tap) and its idle
# power draw is negligible — the process worth turning off to save power is
# the Python/uvicorn ML server this controls. While the API is off, the
# public site still loads (GitHub Pages) but shows "Server offline".
#
# Usage: mishka-server.sh [on|off|toggle|status] [-n]
#   -n  post a macOS notification with the result (nice from a Shortcut)

set -u

LABEL="com.mishka-hub.api"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TARGET="gui/$(id -u)/$LABEL"

notify=false
action="${1:-toggle}"
[[ "${2:-}" == "-n" || "$action" == "-n" ]] && notify=true
[[ "$action" == "-n" ]] && action="toggle"

is_running() {
  launchctl print "$TARGET" >/dev/null 2>&1
}

post() { # $1 = message
  echo "$1"
  if $notify; then
    osascript -e "display notification \"$1\" with title \"Mishka Hub\"" >/dev/null 2>&1
  fi
}

case "$action" in
  status)
    if is_running; then post "Server is ON"; else post "Server is OFF"; fi
    ;;
  on)
    if is_running; then
      post "Server already ON"
    else
      launchctl bootstrap "gui/$(id -u)" "$PLIST" && post "Server ON 🐱" || post "Failed to start server"
    fi
    ;;
  off)
    if is_running; then
      launchctl bootout "$TARGET" && post "Server OFF 💤" || post "Failed to stop server"
    else
      post "Server already OFF"
    fi
    ;;
  toggle)
    if is_running; then
      launchctl bootout "$TARGET" && post "Server OFF 💤" || post "Failed to stop server"
    else
      launchctl bootstrap "gui/$(id -u)" "$PLIST" && post "Server ON 🐱" || post "Failed to start server"
    fi
    ;;
  *)
    echo "usage: $(basename "$0") [on|off|toggle|status] [-n]" >&2
    exit 64
    ;;
esac
