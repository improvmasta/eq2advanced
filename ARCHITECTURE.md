# Architecture

App: eq2advanced — https://eq2advanced.jupiterns.org. See `AGENTS.md` for agent instructions and
`codex.md` / `CLAUDE.md` for assistant context.

## Runtime

Requests flow through:

1. Cloudflare DNS for `eq2advanced.jupiterns.org`
2. Zoraxy on `10.1.1.4:8000`
3. App target `10.1.1.15:8450`

## Initial App

The starter container serves `public/` with Python's built-in HTTP server.
Replace this with the real app stack when implementation begins.

## Deployment

The default deployment target is GHCR:

```
ghcr.io/improvmasta/eq2advanced:main
```

`docker-compose.yml` runs the published image and exposes port `8450`.
Remote deploys are driven by `/home/lindsay/scripts/provision-app.sh` (deploy/move/update).
See `/home/lindsay/AGENTS.md` for full lifecycle documentation.
