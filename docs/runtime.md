# Runtime, stack and hardening

Index: `ARCHITECTURE.md`.

## Topology

1. Cloudflare DNS for `eq2advanced.com` — **DNS-only** (grey cloud).
2. Zoraxy reverse proxy on `10.1.1.4:8000`.
3. The public hostname currently points at the **dev** box `10.1.1.15:8450` (deliberate).
4. Container target `10.1.1.5:8450`, image `ghcr.io/improvmasta/eq2advanced:main`.

```bash
provision-app.sh route eq2advanced 8450 --target-host 10.1.1.15   # dev
provision-app.sh route eq2advanced 8450 --deploy-server media     # container
```

`route` leaves the Cloudflare proxy setting alone unless told otherwise.

### Why the Cloudflare proxy stays off

- The edge caps request bodies at 100 MB, ahead of the app's own
  `upload_max_bytes` (shipped as 0 = unlimited). Oversized raid backfills get a
  Cloudflare 413 the API never sees. `siteconfig.edge_max_bytes(request)`
  reports the ceiling via `GET /api/uploads/limits` when a request carries
  `CF-Ray` from a trusted peer, so `UploadDrop` can refuse early; off the proxy
  it reports 0. **A 413 is only ours when it carries `X-Parse-Only-Allowed`.**
- ACME HTTP-01 renewal through Zoraxy fails while proxied (Cloudflare answers
  the challenge path). `provision-app.sh --cert` refuses the combination.
- SSE is unaffected — `STREAM_POLL_S` (1.5s) heartbeats stay inside any idle
  timeout.

## Two proxies, three lying request attributes (`siteconfig.py`)

Requests reach uvicorn from Zoraxy over plain HTTP. `siteconfig.py` is the only
place that corrects for it. **Never read `request.client.host` or
`request.base_url` directly.**

- `client_ip(request)` — otherwise every visitor is `10.1.1.4` and `ratelimit`
  holds one bucket for the whole internet (five bad passwords locks out
  everyone). Reads `CF-Connecting-IP` then `X-Forwarded-For`, and **only** from a
  peer in `TRUSTED_PROXIES` (Zoraxy + loopback), so a LAN client cannot invent
  an address.
- `is_secure(request)` — TLS ends at the edge, so the session cookie would never
  be marked `Secure`. Decided from `X-Forwarded-Proto`, trusted peers only.
- `public_base_url()` — anything handed to a third party (group invite links,
  the `eq2advanced://pair` device-token payload) comes from here, not from the
  internal host:port. `PUBLIC_BASE_URL` overrides; the default is the live
  hostname regardless of where the route points today.

## Stack

- **Backend** — FastAPI + SQLite (WAL) in `backend/`; uvicorn on `${PORT:-8450}`.
- **Frontend** — Vite + React in `frontend/`, built to `frontend/dist` and served
  by the API process. UI dev: `npm --prefix frontend run dev` (:5173 proxies
  `/api`).
- **Data** — `DATA_DIR` (`./data`, `/data` in the container): `eq2advanced.db`,
  `uploads/` (gzipped raw logs by sha256), `raw/` (live-ingest chunks),
  `parseshots/`, `noteshots/`, `icons/`.

## Backend layout

- `parser/` — pure streaming parser, no DB. `prefix.py` (epoch prefix, CRLF,
  amount formats), `subjects.py` (the subject model — `docs/parser.md`),
  `classify.py` (ordered regex chain), `events.py`, `flavor/` (prepare-line →
  ability resolution). `parse_lines(lines, logger)` is the single entry point for
  bulk files and live batches, and collapses same-second duplicate prepare lines.
- `pipeline/` — `encounters.py` (segmentation + labels), `statsroll.py`
  (per-encounter rollups; `ACTOR_INSERT`/`actor_rows` owns the actor-stats column
  order), `ingest_writer.py` (the one write path), `prune.py`.
- `routers/` — `uploads_api` (multipart → sha256-deduped gzip → background parse),
  `sessions_api`, `encounters_api`, `auth_api` (first account becomes admin),
  `characters_api`, `groups_api`, `admin_api`, `tokens_api` (one readable API key
  per account; `refresh` revokes all live keys), `plugin_api` (the committed DLL
  as a zip, unauthenticated).
- `auth.py`, `groups.py`, `security.py` — see `docs/sharing.md`.
- `pipeline/live.py` + `routers/ingest_api.py` — `docs/live.md`.
- `census/` + `routers/census_api.py` — `docs/census-abilities.md`.
- `coach/` + `routers/coach_api.py` — `docs/coach.md`.

## Hardening

- **Pruning** (`pipeline/prune.py`, every 6h from `main.py`, `PRUNE_DAYS`
  default 180, 0 disables): ready + unpinned sessions past the cutoff get their
  raid report frozen into `raid_reports`, then their `events` rows deleted and
  `sessions.pruned=1`. Rollups, entities and encounters stay, so the parse pages
  still work; raw gzips remain the reprocessing safety net. A pruned session
  serves the frozen report (`"frozen": true`) and refuses coach regeneration
  (409). Calibration auto-pins, so ground truth is never pruned.
- **Backfill** — the dropzone queues multiple files sequentially; server-side
  sha256 and line dedupe make overlap harmless.
- **Live coach hints** — each SSE fight card carries cheap `hints` computed in
  `sessions_api._card_hints`, not the full engine.

## Verification

```bash
.venv/bin/python -m pytest backend/tests/ -q     # golden fixture = /home/lindsay/bobby.txt
bash restart.sh && curl -s localhost:8450/api/sessions
curl -F "file=@/home/lindsay/bobby.txt" -F "character_name=Bobby" localhost:8450/api/uploads
```
