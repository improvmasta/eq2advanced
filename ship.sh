#!/usr/bin/env bash
# Generic ship script for apps under /home/lindsay.
# Usage: bash ship.sh "commit message"
#
# Updates the assistant context docs (CLAUDE.md + codex.md), stages all changes,
# and commits (ZFS-safe). Pushes directly only when on the repo's main branch;
# on any other branch, shipping just commits and offers to merge into main
# (interactive prompt when run from a terminal, merge hint otherwise).
#
# Co-author trailer is configurable so commits from different assistants stay
# consistent. Override with SHIP_COAUTHOR, or set SHIP_TOOL=claude|codex.
# Ship log self-condenses: capped at SHIP_LOG_MAX entries (default 20); a warning
# is printed when a doc grows past SHIP_DOC_MAX_LINES (default 500).
set -e
[ -z "$1" ] && { echo "usage: bash ship.sh \"message\""; exit 1; }
cd "$(dirname "$0")"

# Pre-push gate: catch type/lint/test failures locally instead of paying for a
# CI round-trip (and an agent re-engagement) to fix them. Runs only the repo
# tooling that exists. Skip with SHIP_SKIP_CHECKS=1.
if [ "${SHIP_SKIP_CHECKS:-0}" != "1" ]; then
  if [ -f package.json ]; then
    npm run --if-present typecheck </dev/null
  fi
  if [ -d tests ]; then
    if [ -x .venv/bin/python ]; then .venv/bin/python -m pytest -q
    elif command -v pytest >/dev/null 2>&1; then python -m pytest -q; fi
  fi
fi

case "${SHIP_TOOL:-codex}" in
  claude) DEFAULT_COAUTHOR="Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>" ;;
  *)      DEFAULT_COAUTHOR="Co-Authored-By: Codex <noreply@openai.com>" ;;
esac
COAUTHOR="${SHIP_COAUTHOR:-$DEFAULT_COAUTHOR}"

MSG="$1

$COAUTHOR"

# Stamp a dated entry from the commit subject into the Ship log of both docs so
# CLAUDE.md and codex.md stay in sync no matter which assistant ships.
SUBJECT="$(printf '%s' "$1" | head -n1)"
DATE="$(date +%F)"
TOOL="${SHIP_TOOL:-codex}"
python3 - "$SUBJECT" "$DATE" "$TOOL" "${SHIP_LOG_MAX:-20}" "${SHIP_DOC_MAX_LINES:-500}" CLAUDE.md codex.md <<'PY'
import sys

subject, date, tool, log_max, doc_max, *files = sys.argv[1:]
log_max, doc_max = int(log_max), int(doc_max)
entry = f"- {date} ({tool}): {subject}\n"
heading = "## Ship log"

for path in files:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        continue
    if heading in text:
        head, rest = text.split(heading, 1)
        nl = rest.find("\n")
        line_end = len(rest) if nl == -1 else nl + 1
        heading_line, body = rest[:line_end], rest[line_end:]
        sep = ""
        if body.startswith("\n"):
            sep, body = "\n", body[1:]
        # The log is the contiguous run of "- " lines after the heading; keep any
        # later content (other sections) untouched as the remainder.
        lines = body.splitlines(keepends=True)
        i = 0
        while i < len(lines) and lines[i].startswith("- "):
            i += 1
        entries = [entry] + lines[:i]            # newest first
        if len(entries) > log_max:               # condense: drop oldest
            entries = entries[:log_max]
        text = head + heading + heading_line + sep + "".join(entries) + "".join(lines[i:])
    else:
        text = text.rstrip("\n") + f"\n\n{heading}\n\n{entry}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    n = text.count("\n") + 1
    if n > doc_max:
        sys.stderr.write(
            f"[ship] WARNING: {path} is {n} lines (> {doc_max}); "
            "consider condensing its hand-written sections.\n"
        )
PY

python3 - "$MSG" <<'PY'
import subprocess
import sys

msg = sys.argv[1]
subprocess.run(["git", "add", "-A"], check=True)
subprocess.run(["git", "commit", "-m", msg], check=True)
PY

# Branch-aware shipping: push directly only on the repo's main branch. On any
# other branch, committing is the whole job — surface a merge prompt instead of
# silently pushing main (which would be a no-op and strand the commit on the
# branch). Resolve the main branch from origin/HEAD, falling back to "main".
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
MAIN="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')"
MAIN="${MAIN:-main}"

if [ "$BRANCH" = "$MAIN" ]; then
  git push origin "$MAIN"
else
  MERGE_HINT="git checkout $MAIN && git merge --ff-only $BRANCH && git push origin $MAIN"
  echo "[ship] Committed on '$BRANCH' (not '$MAIN'); nothing pushed yet."
  if [ -t 0 ]; then
    printf "[ship] Merge '%s' into '%s' and push now? [y/N] " "$BRANCH" "$MAIN"
    read -r ans
    case "$ans" in
      y|Y|yes|Yes)
        git checkout "$MAIN"
        if git merge --ff-only "$BRANCH"; then
          git push origin "$MAIN"
          git checkout "$BRANCH"
          echo "[ship] Merged and pushed '$MAIN'; back on '$BRANCH'."
        else
          git checkout "$BRANCH"
          echo "[ship] Fast-forward merge failed (branches diverged)." >&2
          echo "[ship] Resolve manually: git checkout $MAIN && git merge $BRANCH && git push origin $MAIN" >&2
          exit 1
        fi
        ;;
      *)
        echo "[ship] Left on '$BRANCH'. To ship to '$MAIN' later: $MERGE_HINT"
        ;;
    esac
  else
    echo "[ship] To merge into '$MAIN' and deploy: $MERGE_HINT"
  fi
fi
