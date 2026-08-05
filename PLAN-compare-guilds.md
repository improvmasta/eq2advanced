# eq2advanced: Guild-tagged raids + faceted Compare picker

## Context

The Compare page's add card is the weak point: a flat `<select>` over every visible run plus a separate two-step player search. Lindsay wants one faceted live search — dropdowns for **Zone / Date / Player / Guild** plus a search box where typing "freeth" instantly shows recent matching raid nights (matching zone names like *Freethinker Hideout*, guild names like *Freethinkers*, and player names). Clicking a result fills the dropdowns so you can adjust; raid compares add in **one click** (approved); player compares fill dropdowns first, then you select the player and get them compared to you.

"Group" = **guild**, which doesn't exist in the data yet: Lindsay wants raids tagged by guild by looking roster players up in Census and taking the majority guild, shown as a pill right of the zone name in the feed. So this is two features, guild backend first, Compare UI second (it consumes the tag and must degrade gracefully while `guild` is NULL everywhere).

No `PARSE_VERSION` bump — nothing changes parser/rollup semantics; the tag is a derived annotation recomputed idempotently from cached Census data.

All paths under `/home/lindsay/eq2advanced`.

---

## Part A — Guild tags (backend)

### A1. Schema v20 (`backend/db.py`)

Bump `SCHEMA_VERSION` 19→20 (db.py:19). Shape-guarded ALTERs (idiom at db.py:740-747), plus matching `CREATE TABLE` text updates (roster_classes db.py:198-208, zone_runs db.py:151-164):

- `roster_classes`: add `guild_name TEXT`, `guild_id INTEGER`, `guild_checked INTEGER NOT NULL DEFAULT 0`.
  - `guild_checked` is the tri-state disambiguator: `0` = row predates guild capture → **abstains** from the vote and is the backfill queue; `1` + `guild_name` = known guild; `1` + NULL = known guildless (the Census doc has no `guild` key — a different fact from "never asked").
- `zone_runs`: add `guild TEXT` — majority guild name, NULL until computed or when no majority holds. Single TEXT column: the name is both display string and Compare facet key. No staleness column — retagging is pure SQL (zero Census calls), so every pass just recomputes.

### A2. Capture the guild — `backend/census/roster.py`

In `resolve()` (roster.py:65-111) the full character doc is in hand at :85 and the guild is thrown away. Changes:

1. Extract `g = doc.get("guild") or {}` next to the class. Hit rows gain `g.get("name")`, `g.get("guildid")`, `guild_checked=1`; miss rows write `NULL, NULL, 0` (a failed lookup has no guild fact — it must abstain, never read as guildless). Extend the UPSERT column list.
2. New params `resolve(..., force=False, pace_s=0.0)`:
   - `force=True` skips the `stale_names` TTL filter — how the backfill re-fetches the ~1096 pre-v20 cached rows (their class/`checked_ts` refresh too, a free TTL reset).
   - `pace_s` sleeps between requests so the hourly backfill is polite (matches `sync.py:298`'s `time.sleep(1)` convention); parse path keeps `0.0`.
3. Docstring paragraph in the module's voice: the guild rides along because the doc was already paid for.

Optional win, verify live before adopting: `c:show=name,type,guild,id` passthrough on `client.character_by_name` — full docs are large and roster reads four fields. `c:show` on `character` is un-verified query shape in this codebase; if it misbehaves, ship without it. `sync.py` keeps full docs.

### A3. New module `backend/census/guilds.py`

Why-docstring in house voice ("the raid's guild, voted by its roster"). Import `RAID_MIN_RAIDERS` from `backend/groups.py` (one definition of "what is a raid"); `GUILD_BACKFILL_BUDGET = 120`.

1. **`majority_guild(roster, guild_of) -> str | None`** — pure, exhaustively testable. `guild_of` maps `name_lower → guild_name|None` and contains **only** `found=1 AND guild_checked=1` rows; absent names (the ~18% failed lookups, or not-yet-checked) abstain. Rule:
   - `known` = roster members present in the map. **Abstain if `len(known)*2 < len(roster)`** — a tag on thin data is a wrong tag on somebody's guild.
   - Plurality guild among `known`; **tag only on strict majority: `top*2 > len(known)`** — known-guildless count against (12 guildies + 10 guildless friends tags; 3 + 8 doesn't). Ties can't pass strict majority → abstain for free.
2. **`known_guilds(conn, world_id) -> dict`** — one SELECT, mirrors `known_classes`.
3. **`retag_runs(conn, character_id=None) -> int`** — recompute `zone_runs.guild` for runs with `raider_count >= RAID_MIN_RAIDERS AND roster_json IS NOT NULL` (optionally scoped to one character); world_id via `characters` join falling back to `DEFAULT_WORLD` (matches ingest_writer.py:77-82). Below-threshold runs forced NULL. Only UPDATE changed rows; return count. No transaction inside — callers own it.
4. **`backfill_stale_guilds(conn, client, budget=120, world_id=DEFAULT_WORLD) -> dict`** — names from `roster_classes WHERE found=1 AND guild_checked=0 ORDER BY checked_ts LIMIT budget`, then `roster.resolve(..., force=True, pace_s=0.75)`. Returns resolve report + `remaining`.

### A4. Wiring

- **`backend/pipeline/ingest_writer.py`** `_sync_roster_classes` (:59-87): after the class-guess re-run, `guilds.retag_runs(conn, character_id)` inside the existing try/except, own `with conn:` — a fresh upload sees its pill on first load.
- **`backend/pipeline/zoneruns.py`** end of `rebuild_zone_runs`: `retag_runs(conn, character_id)` — merges/splits/deletes/reparse relinks change rosters, and the tag must follow the roster it was voted from. Two-line why-comment.
- **`backend/main.py`** `_census_refresh_loop` (:20-31): after `refresh_stale`, run `backfill_stale_guilds` then `with conn: retag_runs(conn)` via `asyncio.to_thread`, logging the report when it did work. Already gated by `CENSUS_AUTO_REFRESH` (:133) — conftest's `=0` keeps CI offline with no test changes. 120/tick at 0.75 s pacing drains the 1096-row backlog in ~9 h of uptime.
- **`backend/tools/sync_roster.py`**: add `--guilds` flag — loop `backfill_stale_guilds` until `remaining == 0`, then `retag_runs`, printing reports. The "do it now" path.

### A5. API + feed surface

- `GET /api/zone-runs` does `SELECT z.*` (zoneruns_api.py:60-65) — `guild` flows into list + detail payloads automatically. `raidmatch.alternates` explicit column list: leave alone.
- **`frontend/src/pages/Home.jsx`** zone cell (:307-318): after the `merged` badge — `{r.guild && <span className="badge guild" title="Majority guild of the roster, from Census">{r.guild}</span>}`.
- **`frontend/src/pages/ZoneRun.jsx`** header badges (~:1244-1256): same span — the raid page should agree with the list that brought you there.
- **`frontend/src/styles/base.css`** badges block (:213-234): `.badge.guild { max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle; }` — quiet muted pill, deliberately **not** gold (gold pills mean sharing groups everywhere; a Census fact must not impersonate a sharing control).

---

## Part B — Compare picker rebuild

### B1. Architecture: fully client-side

The page already fetches the whole visible list once (Compare.jsx:462-476); ~300 nights × ~24 names ≈ 100 KB — smaller than one `/encounters/agg` answer. Client-side faceting: zero debounce, zero new search endpoints, instant cross-narrowing; the guild facet is free via A5.

### B2. Backend: `?roster=1` on the list (`backend/routers/zoneruns_api.py`)

`list_zone_runs(scope="all", roster: int = 0, ...)`. Replace the pop at :99-100: pop `roster_json` always; when `roster`, set `r["roster"] = json.loads(raw) if raw else []` (parsed server-side so the client never learns the storage format). Same `VISIBLE_RUN_IDS` predicate — reveals nothing the viewer couldn't already open fight-by-fight.

The `/api/players` endpoints (:168-199) **stay** (tested, public, behind the same predicate); their comment gains a line that Compare now facets client-side. The frontend `playerSearch`/`playerRuns` api.js entries are **removed** (dead code — the v12 lesson).

### B3. `frontend/src/lib/api.js`

`zoneRuns: (scope, { roster } = {}) => req(...&roster=1 when set)`. Delete `playerSearch`/`playerRuns`.

### B4. `AddColumn` rewrite (`frontend/src/pages/Compare.jsx` :451-567)

URL model (`?c`), `parseTokens`/`serialize`, `ParseCol`, `ColHead` — **unchanged**. `Compare()` passes `anchor={tokens[0]?.subject ?? null}`; `playerMode = anchor && anchor !== 'raid'`.

**Data:** one `api.zoneRuns('all', { roster: true })` on mount; dedupe one row per night exactly as today (raid_key / mine / primary, :466-473); keep `roster` arrays; newest first.

**State:** `q` + facets `zone`, `date`, `guild`, `player` (`''` = any), and `picked` (selected night, player flow only). In player mode `player` initializes to `anchor` — "me across nights" is the zero-setup default.

**Matching:** a night passes when every set facet holds (`zone`; `date === dayKey(started_ts)`; `guild`; `player` case-insensitively ∈ roster) and `q` holds: zone/guild **includes** ql, or a roster name **startsWith** ql (names are typed from the front; zones/guilds get typed from the middle — "unrest", "freeth"). So "freeth" surfaces both Freethinker Hideout nights and Freethinkers-guild nights.

**Dropdowns (cross-narrowing):** each dropdown's options come from nights matching all *other* facets + q — never offer a choice that empties the list. Zone A–Z; Date = distinct days newest first (`fmt.date` labels); Guild = distinct non-null, **rendered only if any visible night has a guild** (fresh-backfill degradation = the control simply not existing yet); Player = sorted union of matching rosters, native `<select>` (the app's picker pattern). **The Player dropdown is hidden when `anchor === 'raid'`** — per spec, raid compares work the same without it (a raid column can still be flipped to a player afterwards in `ColHead`).

**Results list:** filtered nights, newest first, capped at 12 with a muted "+N more — narrow it down". Each row one full-width button (`.pickrow`): `fmt.date` · zone · guild `.badge` · `{encounter_count} fights` · muted `{character_name}'s parse`. Empty states: `.err` on load failure; "Nothing matches." + a Clear `.chip` resetting q + facets. `prominent` behavior kept.

**Click behavior:**
- *Raid mode (anchor raid, or empty page with Player facet unset):* one click on a row → `onAdd({runId, sel:'all', subject:'raid'})` (approved). `q` clears, facets persist — stacking three Freethinkers nights is click-click-click.
- *Player flow (playerMode, or empty page with Player facet set):* clicking a row **selects** it (`.pickrow.on`) and fills the zone/date/guild dropdowns from it ("fills in the dropdowns, so then you can adjust"); a confirm strip appears: subject `<select>` over **that night's roster** + "Whole raid", defaulting to the `player` facet if set, else `anchor` if in the roster, else whole raid — and an **Add** button → `onAdd({runId, sel:'all', subject})`. Common case ("me on another night"): click row, click Add — two clicks. Someone else: one select change more. "Whole raid" stays reachable so no mode is a dead end.

Opens with a house-voice why-comment: the picker is a faceted view over the list the page already holds — typing narrows, dropdowns pin, and a raid click is the commitment because the anchor column already said what kind of comparison this is.

### B5. CSS (`base.css` Compare block ~:1071-1100)

Widen `.addcol` to ~300px (results rows need room). New `.pickrow` (flex row, transparent border, hover surface, `.on` gold-dim border), `.confirm` strip, reuse `.picklist`; the existing full-width `select, input` rule covers the facet column.

---

## Part C — Tests

`.venv/bin/python -m pytest backend/tests/ -q` (261 green today; keep it that way; fixtures in `tests/fixtures/census/`, conftest sets `CENSUS_AUTO_REFRESH=0`).

1. **`test_census.py`** — extend `FakeClient` docs with/without a `guild` key (shape per `character_bobby.json`: `{"name": ..., "guildid": 38, ...}`). New: resolve writes guild_name/guild_id/guild_checked=1 on hit; doc without the key → NULL + checked=1 (guildless ≠ unknown); miss → `found=0, guild_checked=0`; `force=True` re-fetches a fresh row `stale_names` would skip.
2. **New `test_guilds.py`** — pure `majority_guild` table: clear majority; exactly-50% boundary → None; tie → None; all-guildless → None; guildless counting against; under-half-roster-resolved → None; failed-lookup abstention. DB level: `retag_runs` tags only `raider_count >= 7`, forces NULL below, scopes by character, returns changed count; `backfill_stale_guilds` drains the queue under budget with a fake client. Migration: `PRAGMA table_info` shows the new columns after `init_db`.
3. **`test_zoneruns_api.py`** — `guild` key in list + detail payloads; `?roster=1` returns parsed `roster` and never `roster_json`; default omits both; second-user visibility (existing :197 pattern) unchanged under `?roster=1`. `/api/players` tests untouched.

Frontend has no test runner (package.json: dev/build/preview only) — manual verification below.

## Part D — Verification

1. Backend tests green, count grows. 2. `npm --prefix frontend run build` clean. 3. `bash restart.sh` (needs real `CENSUS_SERVICE_ID`; `s:example` throttles ~6 req).
4. `.venv/bin/python backend/tools/sync_roster.py --guilds` — watch `remaining` drain, then `sqlite3 data/eq2advanced.db "SELECT zone, raider_count, guild FROM zone_runs ORDER BY started_ts DESC LIMIT 20"`.
5. By hand at http://10.1.1.15:8450: feed pills only on ≥7-raider nights, none where the vote abstained; merged + guild badges coexist without wrapping; ZoneRun header pill; both themes.
6. Compare: empty page → "freeth" → zone *and* guild rows appear → one click adds a raid. Player drilldown → Compare chip → picker preselects that name, results are their nights → click row → confirm defaults to them → Add. Adjust dropdowns after a row click; cross-narrowing never strands an empty list; Clear works.
7. Degradation: `UPDATE zone_runs SET guild=NULL` + reload → no pills, no Guild dropdown, all else identical (hourly loop re-tags — expected).
8. Docs: ARCHITECTURE.md gains the majority-rule reasoning + Compare-picker section update; CLAUDE.md/codex.md schema v18→v20 line, Census bullet gains `--guilds`, Compare paragraph updated (keep the two in sync).

## Ordering

1. db.py v20 → roster.py guild capture → guilds.py (+ tests C1–C2)
2. Wiring: ingest_writer, zoneruns rebuild hook, main.py loop, `sync_roster --guilds`
3. `?roster=1` + payload tests (C3)
4. Home/ZoneRun pills + badge CSS
5. Compare AddColumn rewrite + api.js + picker CSS
6. Manual e2e + docs

## Critical files

`backend/db.py`, `backend/census/roster.py`, `backend/census/guilds.py` (new), `backend/pipeline/ingest_writer.py`, `backend/pipeline/zoneruns.py`, `backend/main.py`, `backend/tools/sync_roster.py`, `backend/routers/zoneruns_api.py`, `frontend/src/pages/Compare.jsx`, `frontend/src/lib/api.js`, `frontend/src/pages/Home.jsx`, `frontend/src/pages/ZoneRun.jsx`, `frontend/src/styles/base.css`
