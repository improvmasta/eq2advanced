# eq2advanced

EQ2 TLE raid parser: upload logs or stream live from the ACT plugin, get per-fight and per-night stats (damage, healing, defense, AoEs, deaths) and ability coaching (what an ability should do vs. what it did).

https://eq2advanced.com

Logs from multiple people on the same raid collapse into one entry; shared via groups or character/guild auto-shares.

FastAPI + SQLite (`backend/`), Vite + React SPA (`frontend/`). See `ARCHITECTURE.md` for design.

## Dev

```bash
bash restart.sh                     # http://localhost:8450
pytest backend/tests/ -q            # 311 tests
npm --prefix frontend run build     # SPA → frontend/dist
```

## Deploy

```bash
docker compose up -d --build
```

GitHub Actions builds `ghcr.io/improvmasta/eq2advanced:main`.
