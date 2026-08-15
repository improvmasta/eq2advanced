# eq2advanced

EverQuest II TLE raid parser and coaching site — <https://eq2advanced.com>

Upload logs or stream live from the ACT plugin, and get per-fight and per-night
stats (damage, healing, defense, AoEs, deaths, loot), a live raid dashboard with
measured AoE countdowns, and ability coaching (what an ability should do at your
stats vs. what it did).

Logs from several people on the same raid collapse into one entry; sharing is by
group, or automatic by character or guild tag.

FastAPI + SQLite (`backend/`), Vite + React SPA (`frontend/`).
See `ARCHITECTURE.md` for the design reference.

## Dev

```bash
bash restart.sh                            # http://localhost:8450
.venv/bin/python -m pytest backend/tests/ -q  # long phases print a 15s heartbeat
npm --prefix frontend run build            # SPA → frontend/dist
```

## Deploy

```bash
docker compose up -d --build
```

GitHub Actions builds `ghcr.io/improvmasta/eq2advanced:main`.
