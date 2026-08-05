# eq2advanced

Raid-parsing and coaching site for EverQuest II TLE — https://eq2advanced.com

Upload an EQ2 log (or stream it live from the ACT plugin) and the site parses
it into raid nights: per-fight and per-night damage, healing, defense, incoming
AoEs, deaths, and a coach report that compares what an ability *should* do at
your Census stats against what it actually did. Nights are shared through
groups; several people's parses of the same raid collapse into one entry.

FastAPI + SQLite backend (`backend/`), Vite + React SPA (`frontend/`, built to
`dist/` and served by the API). See `ARCHITECTURE.md`.

## Local

```bash
bash restart.sh                                # http://localhost:8450
.venv/bin/python -m pytest backend/tests/ -q   # tests
npm --prefix frontend run build                # rebuild the SPA
```

## Docker

```bash
docker compose up -d --build
```

The GitHub workflow builds and pushes `ghcr.io/improvmasta/eq2advanced:main`.
The repository is private; the GHCR package is intended to be public, which may
need a one-time settings change at
`https://github.com/users/improvmasta/packages/container/package/eq2advanced/settings`.
