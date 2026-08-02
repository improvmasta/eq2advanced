# eq2advanced Codex Notes

## Goal

New app served at https://eq2advanced.jupiterns.org.

See also: `AGENTS.md` (agent instructions), `ARCHITECTURE.md` (wiring + deploy),
`CLAUDE.md` (Claude context — keep in sync with this file).

## Current Stack

- Minimal static Python web server
- Docker image published to GHCR
- Zoraxy routes https://eq2advanced.jupiterns.org to 10.1.1.15:8450

## Key Files

- `AGENTS.md`: agent instructions and provisioning notes
- `ARCHITECTURE.md`: runtime wiring and deployment
- `public/index.html`: starter page
- `Dockerfile`: container image
- `docker-compose.yml`: local/runtime compose
- `restart.sh`: detached local server restart
- `ship.sh`: generic ship — updates Ship log in CLAUDE.md+codex.md, commits; pushes on main, else offers to merge the branch
  (`SHIP_TOOL=claude|codex` selects the co-author trailer; default `codex`)
- `.github/workflows/docker.yml`: GHCR build and public package visibility

## Host context

CLI tools, session helpers, provisioning commands, and deploy notes live in
`/home/lindsay/AGENTS.md` — don't duplicate them here.

## Migration

If converting to Next.js, follow `/home/lindsay/wikq2` for Docker, restart, ship, and build checks.
Next.js HMR preserves React state — changes to module-level constants that seed `useState`
only take effect after a full `bash restart.sh`. Rapid sequential edits can also wedge HMR; restart fixes it.
If converting to FastAPI/Vite, follow `/home/lindsay/folio` for Docker and runtime data layout.
Keep the public image name as `ghcr.io/improvmasta/eq2advanced:main` unless the repository is renamed.

## Ship log

