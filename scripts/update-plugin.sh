#!/usr/bin/env bash
# Pull the latest CI-built ACT plugin into this repo, so the site serves it.
#
#   bash scripts/update-plugin.sh [run-id]
#
# The DLL is built by improvmasta/eq2advanced-act on every push. We COMMIT it
# here rather than linking GitHub because that repo is private and Actions
# artifacts need an authenticated session and expire after 90 days — a link to
# one is useless to a raider and eventually useless to everyone. 35 KB in the
# repo buys a download link that just works, and ships inside the container.
#
# Run this after shipping the plugin repo, then ship this one.
set -euo pipefail

REPO="improvmasta/eq2advanced-act"
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/backend/refdata/plugin/EQ2Advanced.dll"

if [ -z "${GH_TOKEN:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
  export GH_TOKEN="$GITHUB_TOKEN"
fi
if [ -z "${GH_TOKEN:-}" ] && [ -f "$HOME/.config/app-provision.env" ]; then
  # shellcheck disable=SC1091
  set -a; . "$HOME/.config/app-provision.env"; set +a
  export GH_TOKEN="${GITHUB_TOKEN:-}"
fi

run="${1:-}"
if [ -z "$run" ]; then
  # Filtered with jq rather than `--status success`: that flag landed after
  # gh 2.23, which is what this box has.
  run="$(gh run list --repo "$REPO" --workflow build.yml --limit 20 \
         --json databaseId,conclusion \
         --jq '[.[] | select(.conclusion == "success")][0].databaseId')"
fi
[ -n "$run" ] || { echo "no successful build run found in $REPO" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
gh run download "$run" --repo "$REPO" -n EQ2Advanced -D "$tmp"
[ -f "$tmp/EQ2Advanced.dll" ] || { echo "artifact had no EQ2Advanced.dll" >&2; exit 1; }

before="$( [ -f "$dest" ] && sha256sum "$dest" | cut -d' ' -f1 || echo none)"
mkdir -p "$(dirname "$dest")"
cp "$tmp/EQ2Advanced.dll" "$dest"
after="$(sha256sum "$dest" | cut -d' ' -f1)"

echo "run $run -> backend/refdata/plugin/EQ2Advanced.dll"
echo "  was $before"
echo "  now $after"
[ "$before" = "$after" ] && echo "  (unchanged)" || echo "  UPDATED - ship this repo to publish it"
