# eq2advanced — Agent Instructions

App served at https://eq2advanced.jupiterns.org (local port 8450, image `ghcr.io/improvmasta/eq2advanced:main`).

## Read first

- `ARCHITECTURE.md` — how the app is wired (DNS → Zoraxy → container) and how it deploys.
- `codex.md` — Codex working notes and current behavior.
- `CLAUDE.md` — Claude context and commands.

These three plus this file are the agent docs for the repo; keep them in sync.

## Working style

- Be concise; make focused changes; keep secrets out of the repo.
- Local server: `bash restart.sh`. Ship: `bash ship.sh "message"`
  (`SHIP_TOOL=claude|codex` selects the co-author trailer; the Ship log in
  `CLAUDE.md`/`codex.md` updates automatically and self-condenses).

## Provisioning

Scaffolded by `/home/lindsay/scripts/provision-app.sh` from the shared generics in
`/home/lindsay/scripts`. App lifecycle (create / deploy / move / update) is driven
by that script — see `/home/lindsay/AGENTS.md` on the host before changing
hosting, DNS, or container deployment.
