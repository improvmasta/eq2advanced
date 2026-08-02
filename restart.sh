#!/usr/bin/env bash
# Restart the local starter web server, detached so it survives the parent shell exiting.
# Starter apps serve public/ via Python; migrate this to the real stack as the app grows.
set -e
cd "$(dirname "$0")"
pkill -f 'http.server 8450' 2>/dev/null || true
sleep 1
setsid nohup python3 -m http.server 8450 --bind 0.0.0.0 --directory public > eq2advanced.log 2>&1 < /dev/null &
disown
echo "restarted (http://localhost:8450, logs: eq2advanced.log)"
