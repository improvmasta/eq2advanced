#!/usr/bin/env bash
# Restart the dev server, detached so it survives the parent shell exiting.
# The pkill pattern includes the port: drivetime also runs `uvicorn main:app`
# on this box, so a bare pattern would kill it too.
set -e
cd "$(dirname "$0")"
pkill -f 'uvicorn main:app.*8450' 2>/dev/null || true
pkill -f 'http.server 8450' 2>/dev/null || true   # retired starter placeholder
sleep 1
setsid nohup bash start.sh > eq2advanced.log 2>&1 < /dev/null &
disown
echo "restarted (http://10.1.1.15:8450, logs: eq2advanced.log)"
