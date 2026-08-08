#!/usr/bin/env bash
# Run the API + SPA on the host. Loads .env; binds 0.0.0.0 so the dev site is
# reachable at http://10.1.1.15:8450 (LAN). Auto-reloads on backend edits
# (RELOAD=0 to disable). Data lives under DATA_DIR (default ./data) — the same
# layout the container mounts at /data, so moving to Docker keeps all data.
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
export DATA_DIR="${DATA_DIR:-$(pwd)/data}"
# The UI is a Vite+React SPA served from frontend/dist. Build it once if missing
# (BUILD_WEB=1 forces a rebuild). For active UI work, run `npm --prefix frontend
# run dev` instead — Vite HMR on :5173 proxies the API here.
if [ "${BUILD_WEB:-0}" = "1" ] || [ ! -f frontend/dist/index.html ]; then
  echo "eq2advanced: building frontend/dist…"
  ( cd frontend && { [ -d node_modules ] || npm ci; } && npm run build )
fi
RELOAD_ARGS=""
[ "${RELOAD:-1}" = "1" ] && RELOAD_ARGS="--reload --reload-dir backend"
# --timeout-graceful-shutdown is NOT optional here. The overlay and dashboard
# SSE streams are `while True` loops that never end on purpose (an OBS browser
# source is opened once and left for hours), so uvicorn's default shutdown —
# wait for every open connection to close — waits forever. It keeps the listen
# socket bound while it waits, so requests pile into the backlog unanswered and
# the site HANGS rather than failing. One editor save on a backend file took
# the site down that way. The cap makes every restart, reload and deploy
# terminate whether or not anyone is streaming.
exec .venv/bin/python -m uvicorn main:app --app-dir backend \
  --host "${HOST:-0.0.0.0}" --port "${PORT:-8450}" \
  --timeout-graceful-shutdown "${GRACEFUL_S:-10}" $RELOAD_ARGS
