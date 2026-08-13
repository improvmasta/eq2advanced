# eq2advanced — agent instructions

Raid-parsing and coaching site for EverQuest II TLE, at <https://eq2advanced.com>
(local port 8450, image `ghcr.io/improvmasta/eq2advanced:main`).

## Read first

- `CLAUDE.md` — commands, the rules that must not be relitigated (one line each,
  pointing into `docs/`), what the app is, and what is open. `codex.md` is a
  pointer to it; context lives in ONE place.
- `ARCHITECTURE.md` — the index of the design reference in `docs/*.md`. Read only
  the topic file for the area you are changing.

## Working style

- Be concise, make focused changes, keep secrets out of the repo.
- Local server: `bash restart.sh`. Ship: `bash ship.sh "message"`
  (`SHIP_TOOL=claude|codex` selects the co-author trailer; the Ship log in
  `CLAUDE.md`/`codex.md` updates automatically and self-condenses).
- **Never deploy.** The container on 10.1.1.5 is Lindsay's; the public hostname
  currently points at the dev box on purpose.

## Provisioning

Scaffolded by `/home/lindsay/scripts/provision-app.sh`. App lifecycle (create /
deploy / move / update / route) is driven by that script — read
`/home/lindsay/AGENTS.md` before changing hosting, DNS, or container deployment.
