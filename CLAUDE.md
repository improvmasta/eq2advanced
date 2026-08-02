# eq2advanced - Claude Context

## Behavior

- Be concise and make focused changes.
- Prefer updating existing files over adding new abstractions.
- Keep secrets out of the repository.
- Use the local helper scripts below for restart and shipping.

## Read also

- `AGENTS.md` — agent instructions and provisioning notes.
- `ARCHITECTURE.md` — how the app is wired and how it deploys.
- `codex.md` — Codex working notes (keep in sync with this file).

## App

- Public URL: https://eq2advanced.jupiterns.org
- Local port: 8450
- Docker image: ghcr.io/improvmasta/eq2advanced:main

## Commands

```bash
bash restart.sh
SHIP_TOOL=claude bash ship.sh "message"   # updates Ship log in CLAUDE.md+codex.md, commits; pushes on main, else offers to merge the branch
docker compose up -d --build
```

`ship.sh` is the generic helper from `/home/lindsay/scripts`. Set `SHIP_TOOL=claude`
(or `codex`) for the matching co-author trailer; the Ship log below is updated
automatically on every ship and condenses itself.

## Host context

CLI tools (`gh`, `rg`, `jq`, `fd`), the `state`/`logs`/`restart`/`ship` session
helpers, provisioning commands, and deploy notes live in `/home/lindsay/CLAUDE.md`
and `/home/lindsay/AGENTS.md` — don't duplicate them here.

## Migration Notes

This repo starts as a minimal static container. When the app grows, migrate the Dockerfile,
restart script, and verification commands to match the actual stack. Use the patterns in
`/home/lindsay/folio` for FastAPI/Vite apps and `/home/lindsay/wikq2` for Next.js apps.

### Next.js HMR caveat

HMR preserves React state across hot reloads — changes to module-level constants that seed
`useState` (e.g. default option objects) only take effect after a full `bash restart.sh`.
Multiple rapid edits can also wedge HMR into a broken state; restart fixes it.

## Ship log

