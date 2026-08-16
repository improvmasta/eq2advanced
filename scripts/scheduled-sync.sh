#!/usr/bin/env bash
#
# The two syncs that are worth running unattended, and nothing else.
#
#   scripts/scheduled-sync.sh census    — retry the Census-dependent backfills
#   scripts/scheduled-sync.sh planner   — re-crawl the wiki catalog for /plan
#
# **THIS OVERRIDES "HAND-RUN, NEVER ON A SCHEDULE"** for `sync_planner.py`
# (Lindsay's call, 2026-08-16). That rule existed because a crawl that runs
# itself is a crawl nobody is watching, and `ingest.store` RECONCILES — it
# deletes what the wiki no longer says. The rule's protection is now in the
# code instead of in the habit: `ingest.CrawlCollapsed` refuses to write a
# crawl that came back far smaller than the last one, so an hour of Fandom
# being unhappy can no longer empty the catalog. Everything still lands in a
# log a person can read, and the cadence is monthly rather than nightly,
# because an expansion's itemization changes when the expansion changes.
#
# `sync_wiki.py` (the ABILITY ingest) is deliberately NOT here. It stays
# hand-run and era-filtered — see CLAUDE.md.
#
# Every run is safe to interrupt and safe to repeat: each underlying tool
# resumes from what is already cached.

set -uo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$APP/.venv/bin/python"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/eq2advanced"
mkdir -p "$STATE"

log() { printf '%s %s\n' "$(date -Is)" "$*"; }

# The same file `start.sh` sources. `CENSUS_SERVICE_ID` lives here and the
# tools below are useless without a real one.
[ -f "$APP/.env" ] && set -a && . "$APP/.env" && set +a

census_up() {
  # A cheap real query rather than a HEAD: Census answers 200 with an `error`
  # body when it is unavailable, so the status code proves nothing.
  local body
  body="$(curl -sS --max-time 30 \
    "https://census.daybreakgames.com/${CENSUS_SERVICE_ID:-s:example}/json/get/eq2/item/?c:limit=1" \
    2>/dev/null)" || return 1
  case "$body" in
    *'"error"'*|'') return 1 ;;
    *) return 0 ;;
  esac
}

case "${1:-}" in
census)
  # CENSUS INTERMITTENCY IS NORMAL AND IS NOT AN OUTAGE (docs/planner.md), so a
  # down probe is a quiet no-op and never an alert. The point of running often
  # is to be there in the window when it comes back: items that failed to
  # resolve kept a NULL `census_ts` and are still queued, and this is what
  # finally answers them.
  if ! census_up; then
    log "census down — nothing to do"
    exit 0
  fi
  log "census up — finishing the backfills it owes"
  # Items first: a loot card with no picture and no link is what a reader
  # actually sees. Resolved rows are skipped, so this is cheap once it is done.
  "$PY" "$APP/backend/tools/backfill_loot.py" --resolve-only 2>&1 | sed 's/^/  items: /'
  # Then the roster — classes and guild tags for the people in the raids.
  "$PY" "$APP/backend/tools/sync_roster.py" 2>&1 | sed 's/^/  roster: /'
  log "census backfills done"
  ;;

planner)
  log "re-crawling the /plan catalog (eof, rok)"
  if "$PY" "$APP/backend/tools/sync_planner.py" --era eof --era rok 2>&1 \
      | tr '\r' '\n' | grep -vE '^\s*$' | sed 's/^/  /'; then
    log "planner crawl done"
  else
    # Exit 2 is `CrawlCollapsed` — the guard did its job and the catalog is
    # untouched. Anything else is the crawl failing outright. Either way the
    # previous catalog is still being served.
    log "planner crawl FAILED (catalog left as it was) — read the log above"
    exit 1
  fi
  ;;

*)
  echo "usage: ${0##*/} census|planner" >&2
  exit 64
  ;;
esac
