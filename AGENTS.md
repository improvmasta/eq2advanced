# eq2advanced — Agent Instructions

Raid-parsing and coaching site for EverQuest II TLE, at https://eq2advanced.com
(local port 8450, image `ghcr.io/improvmasta/eq2advanced:main`).

## Read first

- `CLAUDE.md` / `codex.md` — the same context for either assistant: commands,
  the rules that must not be relitigated, what the app is, and what's open.
  Keep the two in sync.
- `ARCHITECTURE.md` — how it is wired and why, in detail.

## Working style

- Be concise; make focused changes; keep secrets out of the repo.
- Local server: `bash restart.sh`. Ship: `bash ship.sh "message"`
  (`SHIP_TOOL=claude|codex` selects the co-author trailer; the Ship log in
  `CLAUDE.md`/`codex.md` updates automatically and self-condenses).
- **Never deploy.** The container on 10.1.1.5 is Lindsay's; the public hostname
  currently points at the dev box on purpose.

## Provisioning

Scaffolded by `/home/lindsay/scripts/provision-app.sh` from the shared generics
in `/home/lindsay/scripts`. App lifecycle (create / deploy / move / update /
route) is driven by that script — read `/home/lindsay/AGENTS.md` on the host
before changing hosting, DNS, or container deployment.
