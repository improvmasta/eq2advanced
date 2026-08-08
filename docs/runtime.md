# eq2advanced — Runtime, stack and hardening

Part of the architecture reference. Index: `ARCHITECTURE.md`.

## Runtime

1. Cloudflare DNS for `eq2advanced.com`, **DNS-only** (grey cloud).
   It was proxied for part of 2026-08-03; the 100 MB body cap below is why it
   is not anymore
2. Zoraxy on `10.1.1.4:8000`
3. **Currently** the public hostname points at the **dev** box,
   `10.1.1.15:8450` (`restart.sh`) — a deliberate, temporary state
4. Container target: `10.1.1.5:8450` (Lindsay deploys it —
   `ghcr.io/improvmasta/eq2advanced:main`, built on push to main)

Where the hostname lands is one command, and it flips back the same way:

```bash
/home/lindsay/scripts/provision-app.sh route eq2advanced 8450 --target-host 10.1.1.15   # dev (now)
/home/lindsay/scripts/provision-app.sh route eq2advanced 8450 --deploy-server media     # container
```

With no `--cloudflare-*` flag `route` leaves the proxy setting alone; it only
moves the route.

### Consequences of the Cloudflare proxy (why it is off)

- **Uploads are capped at 100 MB by Cloudflare**, before the app's own
  `upload_max_bytes` is consulted. A log over that gets a Cloudflare 413 the
  API never sees — no request line in the uvicorn log, nothing on disk. The
  app's cap ships as 0 (unlimited), so while the proxy was on Cloudflare was
  the *only* upload limit, and it turned a remote raider's backfill away with
  an HTML error page. That is what took the proxy back off on 2026-08-03.
  `siteconfig.edge_max_bytes(request)` is what the app can still do about it:
  a request carrying `CF-Ray` (from a trusted peer) is told the ceiling in
  `GET /api/uploads/limits`, and `UploadDrop` refuses an oversized file with a
  sentence instead of spending the upload to learn it. Off the proxy it
  reports 0 and the dropzone says nothing. **A 413 is only ours when it
  carries `X-Parse-Only-Allowed`** — that header is the parse-only offer, and
  the client must not take an edge 413 as a reason to stop keeping raw logs.
- **ACME HTTP-01 renewal through Zoraxy will fail** while the record is
  proxied (Cloudflare answers the challenge path). `provision-app.sh --cert`
  refuses the combination for this reason. Edge TLS keeps working regardless;
  it is the origin certificate that stops renewing.
- SSE (`/api/sessions/{id}/stream`) is fine: it emits a `status` heartbeat
  every `STREAM_POLL_S` (1.5s), far inside any idle timeout.

### The app is behind two proxies, and knows it (`siteconfig.py`)

Every public request now reaches uvicorn from Zoraxy over plain HTTP, which
makes three request attributes lie. `backend/siteconfig.py` is the one place
that corrects them, and each correction is load-bearing:

- `client_ip(request)` — `request.client.host` is `10.1.1.4` for the entire
  internet, so `ratelimit`'s per-address bucket held ONE counter for everyone:
  five wrong passwords by anybody locked every user out of login for fifteen
  minutes. The safety net was a one-line denial of service. Forwarding headers
  (`CF-Connecting-IP`, then `X-Forwarded-For`) are read **only** when the peer
  is a trusted proxy (`TRUSTED_PROXIES`, default Zoraxy + loopback) so a direct
  LAN client cannot invent an address for itself.
- `is_secure(request)` — TLS ends at the edge, so `request.url.scheme` is
  `http` and the session cookie was never marked `Secure`. Decided from
  `X-Forwarded-Proto`, again only from a trusted peer.
- `public_base_url()` — `request.base_url` is the internal `host:port`, which
  is not a URL anyone else can open. Anything we hand to a third party comes
  from here instead: the group **invite link** (`GET /api/groups` returns
  `invite_base`) and the device-token **pair payload** (`eq2advanced://pair`).
  `PUBLIC_BASE_URL` overrides it; the default is the live hostname. It is
  deliberately independent of wherever the route points today — the hostname is
  the product, the box behind it is an implementation detail that moves.

## Stack

- **Backend**: FastAPI + SQLite (WAL) in `backend/`; uvicorn on `${PORT:-8450}`,
  binds `${HOST:-0.0.0.0}`.
- **Frontend**: Vite + React SPA in `frontend/`, built to `frontend/dist`,
  served by the API process. UI dev: `npm --prefix frontend run dev` (:5173
  proxies `/api` to :8450). Theme: eq2lexicon-sibling dark + warm-parchment
  light, toggle in the top nav.
- **Data**: `DATA_DIR` (default `./data`; container mounts `/data`) —
  `eq2advanced.db`, `uploads/` (gzipped raw logs by sha256), `raw/` (reserved
  for live ingest batches).

## Backend layout

- `parser/` — pure streaming log parser, no DB. `prefix.py` (epoch prefix,
  CRLF, comma/`296.1K` amounts), `subjects.py` (subject model — see `docs/parser.md`),
  `classify.py` (ordered regex chain), `events.py` (dataclasses + flag bits),
  `flavor/` (prepare-line → ability resolution, see "Cast ground truth" in `docs/coach.md`).
  `parse_lines(lines, logger)` is the single entry point for bulk files AND
  live batches; it also collapses the client's same-second duplicate prepare
  lines (234 of 918 in bobby.txt are exact dupes).
- `pipeline/` — `encounters.py` (gap segmentation + named labels),
  `statsroll.py` (per-encounter actor/ability rollups + HP-deficit healer
  estimates; `ACTOR_INSERT`/`actor_rows` is the one place the actor-stats
  column order lives), `ingest_writer.py` (the one write path: entity/ability
  resolution, events + rollups in one transaction, session status),
  `prune.py` (events retention, see "Pruning").
- `routers/` — `uploads_api` (multipart → sha256-deduped gzip → background
  parse thread), `sessions_api`, `encounters_api`, `auth_api` (username +
  password sign-up, cookie login, security-question reset; the FIRST registered
  account becomes admin), `characters_api` (CRUD + auto-share; claims are not
  exclusive), `groups_api`, `admin_api`, `tokens_api` (the account's ONE readable
  API key — Sonarr-style: `token_plain` beside the hash, `GET` serves it back
  to the owner, `refresh` revokes every live key and mints the replacement),
  `plugin_api` (the committed DLL, served as a zip, unauthenticated).
- `auth.py` (PBKDF2 password + security answer + hashed session/device tokens),
  `groups.py` (membership + the one visibility predicate) and `security.py`
  (deps + ownership/visibility helpers). See `docs/sharing.md`.
- `pipeline/live.py` + `routers/ingest_api.py` — live ingest (see `docs/live.md`).
- `census/` + `routers/census_api.py` — Census sync (see `docs/census-abilities.md`);
  `census/catalog.py` populates `ability_catalog` (see "Ability catalog" in `docs/coach.md`).
- `coach/` + `routers/coach_api.py` — coach engine + raid report (see `docs/coach.md`).


## Hardening

- **Pruning** (`pipeline/prune.py`, loop in `main.py` every 6h, `PRUNE_DAYS`
  env, default 180, 0 disables): ready+unpinned sessions older than the
  cutoff get their raid report FROZEN into `raid_reports`, then their
  `events` rows deleted and `sessions.pruned=1`. Rollups/entities/encounters
  stay (Encounter + SessionDetail pages are rollup-backed and unaffected);
  raw gzips remain the reprocessing safety net. A pruned session serves the
  frozen raid report (`"frozen": true`), refuses coach regeneration (409),
  and GET /coach still returns the last persisted report. Calibration
  auto-pins, so ground truth is never pruned.
- **Backfill UX**: the Sessions dropzone takes multiple files (sequential
  queue, per-file errors); server-side sha256 + line dedupe make overlap
  harmless.
- **Live coach hints**: each SSE fight card carries `hints` (died N×, mostly
  overheal, wards punched through) computed from the logger's rollup row in
  `sessions_api._card_hints` — cheap flags, not the full engine.

## Verification

```bash
.venv/bin/python -m pytest backend/tests/ -q     # golden fixture = /home/lindsay/bobby.txt
bash restart.sh && curl -s localhost:8450/api/sessions
curl -F "file=@/home/lindsay/bobby.txt" -F "character_name=Bobby" localhost:8450/api/uploads
```
