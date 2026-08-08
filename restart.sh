#!/usr/bin/env bash
# Restart the dev server, detached so it survives the parent shell exiting.
# The pkill pattern includes the port: drivetime also runs `uvicorn main:app`
# on this box, so a bare pattern would kill it too.
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8450}"
pkill -f "uvicorn main:app.*$PORT" 2>/dev/null || true
pkill -f "http.server $PORT" 2>/dev/null || true   # retired starter placeholder

# Do not start until the port is actually FREE. A server already mid-shutdown
# ignores SIGTERM (it is waiting on connections that never close), so the old
# process kept the socket, the new one died on "Address already in use", and
# this script printed "restarted" over a site that was still hung. Wait, then
# escalate to SIGKILL, then verify — a restart that lies is worse than one that
# fails.
for i in $(seq 1 10); do
  ss -ltn "sport = :$PORT" 2>/dev/null | grep -q ":$PORT" || break
  [ "$i" = 5 ] && pkill -9 -f "uvicorn main:app.*$PORT" 2>/dev/null || true
  sleep 1
done
if ss -ltn "sport = :$PORT" 2>/dev/null | grep -q ":$PORT"; then
  echo "restart FAILED: port $PORT still held by:" >&2
  ss -ltnp "sport = :$PORT" 2>/dev/null >&2
  exit 1
fi

setsid nohup bash start.sh > eq2advanced.log 2>&1 < /dev/null &
disown

for i in $(seq 1 15); do
  sleep 1
  if curl -fsS -o /dev/null -m 2 "http://127.0.0.1:$PORT/"; then
    echo "restarted (http://10.1.1.15:$PORT, logs: eq2advanced.log)"
    exit 0
  fi
done
echo "started but not answering on :$PORT after 15s — check eq2advanced.log" >&2
exit 1
