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
# The version is written beside the DLL because the site has to be able to say
# "you are on 0.1.0, there is a 0.2.0" to somebody whose plugin is old
# (routers/plugin_api.py, and the pill on /import). It cannot be read back out
# of a .NET assembly without a PE parser, and a stale number here would put an
# update pill in front of somebody who is already up to date — so it is taken
# from the plugin source and the script REFUSES rather than guesses.
vdest="$root/backend/refdata/plugin/VERSION"
src="${PLUGIN_SRC:-$HOME/eq2advanced-act}"
version="${PLUGIN_VERSION:-}"
if [ -z "$version" ] && [ -f "$src/EQ2Advanced/Plugin.cs" ]; then
  version="$(sed -n 's/.*public const string Version = "\([^"]*\)".*/\1/p' \
             "$src/EQ2Advanced/Plugin.cs" | head -1)"
fi
[ -n "$version" ] || {
  echo "cannot tell what version this build is. Pass PLUGIN_VERSION=x.y.z, or" >&2
  echo "point PLUGIN_SRC at a checkout of $REPO." >&2
  exit 1
}

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

printf '%s\n' "$version" > "$vdest"

echo "run $run -> backend/refdata/plugin/EQ2Advanced.dll (v$version)"
echo "  was $before"
echo "  now $after"
[ "$before" = "$after" ] && echo "  (unchanged)" || echo "  UPDATED - ship this repo to publish it"
