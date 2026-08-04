# eq2advanced — Architecture

Raid-coaching web app for EverQuest II TLE (Wuoshi, EoF era). Core idea:
*Census says what an ability should do at your stats; the parse says what it
actually did; coaching lives in the gap.*

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
  CRLF, comma/`296.1K` amounts), `subjects.py` (subject model — see below),
  `classify.py` (ordered regex chain), `events.py` (dataclasses + flag bits),
  `flavor/` (prepare-line → ability resolution, see "Cast ground truth").
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
  exclusive), `groups_api`, `admin_api`, `tokens_api` (per-character device
  tokens: mint shown-once, QR pair payload, revoke; the ACT plugin
  authenticates with these).
- `auth.py` (PBKDF2 password + security answer + hashed session/device tokens),
  `groups.py` (membership + the one visibility predicate) and `security.py`
  (deps + ownership/visibility helpers). See "Accounts, groups and sharing".
- `pipeline/live.py` + `routers/ingest_api.py` — live ingest (see below).
- `census/` + `routers/census_api.py` — Census sync (see below);
  `census/catalog.py` populates `ability_catalog` (see "Ability catalog").
- `coach/` + `routers/coach_api.py` — coach engine + raid report (see below).

## Accounts, groups and sharing (phase 12 — 2026-08-03)

Phase 2's accounts were a placeholder: email login, one global owner per
character name, and `is_admin()` short-circuiting every visibility check. All
three are gone.

### Identity (schema v9)

Login is `username` + password; **there is no email anywhere**. The only
self-service recovery is a security question chosen at sign-up (one of
`auth.RESET_QUESTIONS`, answer normalized by `auth.normalize_answer` — strip,
collapse whitespace, casefold — then PBKDF2'd like a password). A reset deletes
every `auth_sessions` row for that user, because a reset exists precisely when
someone else may hold the password. Accounts predating v9 have no question and
are told so on the Account page; only an admin reset recovers them.

`ratelimit.py` counts failures per identity AND per client address on login,
both reset routes, the two routes that re-check a password before changing a
credential (`/auth/password`, `/auth/security-question` — a live cookie proves
you signed in once, not that you know the password now, so a borrowed browser
is exactly where someone would sit and guess it), and the group-join code —
with no email loop and no 2FA that counter is the only thing between a weak
password and a script. The address half only means anything because
`siteconfig.client_ip` resolves the real visitor behind the proxies (see
"The app is behind two proxies"); keyed on the raw peer it was worse than
nothing. In-process, so it resets on restart; that's stated in the docstring
and is not a substitute for fail2ban at the edge.

Registration is deliberately NOT failure-counted: nothing about it is guessable,
and the lever for sign-up abuse is the `registration_open` setting.

Migrations are guarded by SHAPE, not `user_version` (the dev reloader can stamp
the version mid-edit), and each rebuilds a table SQLite can't ALTER:
`_rebuild_users` (email → username + sq columns; username = the old local part,
collisions → `user{id}`), `_rebuild_characters`, `_rebuild_sessions`. All three
preserve ids, run with `foreign_keys=OFF` and assert `foreign_key_check`.
Verified against a copy of the real 344 MB database: 2.7M events, 159 runs,
pinned/calibration/parse_version flags all intact, idempotent on a second run.

### Claims are not exclusive

`characters` is `UNIQUE(user_id, name, world_id)` with a NOT NULL owner. Anyone
may claim "Bobby"; each claim is that user's own row with its own logs, and
nothing about it is visible to the other claimants. `sessions.upload_sha256`
lost its global UNIQUE for the same reason — two raiders who were both there
upload the same bytes and get one content-addressed gzip with a session each
(`idx_sessions_upload` is (sha, character)). The file is unlinked only by the
last session pointing at it, which `delete_session` and `drop_raw_if_unwanted`
both check. Known duplication, not yet solved: two claimants of one name each
drive their own Census sync of the same world character.

### Admin runs the site, it does not read the site

`role='admin'` is OPERATIONAL. It is absent from every visibility decision in
`security.py` — an admin gets 404 on a stranger's run, `/encounters/agg`,
`/timeline`, `/deaths`, coach report and Census snapshot, and `test_auth.py`
pins each one. `admin_api.py` serves only counts, sizes, statuses and settings;
there is no route from it into a parse, and every mutation writes `audit_log`,
which the console shows back. Support is "ask them to share the raid".

### The visibility rule (`groups.py`)

A zone run is visible to you if you own it, OR it is explicitly shared with a
group you're in, OR its character auto-shares with a group you're in and this
run isn't hidden from that group, OR **a session it came out of was shared with
a group you're in** and this run isn't hidden from that group, OR it has been
published. That is one SQL SELECT (`VISIBLE_RUN_IDS`, parameterised by `:uid`)
and nothing else composes it. `PERSONAL_RUN_IDS` is the same thing minus the
published branch, and `VISIBLE_RUN_IDS` is now *derived from it* rather than
repeated — a branch added to one and forgotten in the other is either a silent
leak or a silent hiding, and there is no longer a second copy to forget.

- **Nothing is materialised.** `rebuild_zone_runs` re-derives run membership on
  every upload, reparse and hand edit, so a share copied onto a run would
  evaporate; evaluating at read time is also what makes leaving a group take
  effect on the next request. When runs collapse into one id the survivor
  inherits the union (`groups.carry_shares`, called from the rebuild before the
  stale rows are deleted) — otherwise a merge would silently unshare a night.
- **`hide` beats every standing share, an explicit `share` beats everything.**
  Auto-share and the plugin's "share tonight" are useful defaults; one wipe can
  still be pulled back out. `set_run_shares` must count BOTH as standing when it
  decides where to write a `hide` — it deletes only explicit `share` rows, so a
  read-time branch it doesn't know about survives the delete and the untick
  silently revokes nothing. That bug shipped for exactly as long as it took the
  v11 test to catch it.
- **Seeing is not changing.** `owned_zone_run` guards delete/merge/split/edits,
  so a shared raid is read-only to everyone including admins, and cannot be
  re-shared onward into the viewer's own groups.
- **Authorization is per ENCOUNTER, not per session** (`visible_encounters`).
  This is the leak-shaped part: `/encounters/agg|timeline|deaths` used to
  authorize through the session, so a viewer cleared for one shared run would
  have been cleared for every other fight in the same uploaded FILE. Sessions
  themselves stay strictly owner-only — a shared night is derived stats, never
  the log, the sibling fights, or the parse plumbing.
- `memo.py` needs no key change: authorization runs before the memo and the
  payload is a pure function of the already-authorized id set. Do not memoize
  an authorization decision here.

### Sharing from the ACT plugin (phase 17 — schema v11, 2026-08-04)

The uploader (`improvmasta/eq2advanced-act`) decides who sees a raid *before*
the raid, in ACT, rather than on the site afterwards. Two controls, and the
difference between them is the design:

| Control | Covers | Table |
|---|---|---|
| ticked groups | the raid being uploaded **now** | `session_shares` |
| + "keep these for every raid" | every raid the character records, back catalogue included | `character_shares` |

- **Why the session, not the run.** While the log is still being written the
  only thing that exists is the session. Zone runs are derived at parse time and
  re-derived on every reparse, so an intent recorded against a run id would not
  survive the night. `session_shares` is read at query time exactly like
  `character_shares`, so it inherits `hide`, leave-a-group, and
  nothing-is-materialised for free.
- **Why it rides on the batch.** `share_groups` is an optional field on
  `POST /api/ingest/batch`, not its own route: the session it applies to does
  not exist until the first batch opens it, and a raid must never stream into a
  session whose audience was set by a request that might not have landed. It is
  authoritative on every batch, so unticking a group mid-raid takes the night
  back on the next one. **Omitting the field means "don't touch"** — a plugin
  without the scope sends nothing, and sending `[]` instead would read as "share
  with nobody" and silently undo what was set on the site.
- **Scope is fixed at mint.** `device_tokens.can_share` (default 0, and 0 for
  every token that predates v11) gates both writes; without it `GET
  /api/ingest/shares` still answers, read-only, because "who can see this" is
  the question the raider actually has. There is deliberately no route that
  raises the scope: the token sits in a config file on a gaming PC, so widening
  it has to be done by someone signed in to the site. `POST
  /characters/{id}/tokens` takes `can_share`, and the Characters page has the
  checkbox next to the label field.
- Unknown or not-mine group ids are **404, not dropped**. A plugin that shared
  with fewer people than its checkboxes show would be lying about the one thing
  here that matters.
- `shares_for_runs` now returns `source` ('run' | 'character' | 'session') and
  `GET /zone-runs/{id}/shares` passes it through, so the owner's control can say
  where a share came from — and therefore what else unticking it affects.

Covered end to end by `tests/test_ingest_sharing.py`.

Groups: `groups` / `group_members` / `group_invites`. Three ways in, all the
same credential — an invite addressed to a username, the 6-digit join code read
aloud in voice, or an invite **link** (`/join/<code>`, which carries that same
code so there is one thing to rotate). A million codes is small, so
`ratelimit` is the actual security, on joining AND on
`GET /api/groups/preview/{code}` — the unauthenticated route the landing page
uses to name the group before the visitor has an account. Preview is
deliberately thin (name, description, headcount, "are you already in it") and
never the roster. The link works signed out: `pages/JoinGroup.jsx` shows the
invitation with sign-up underneath and joins the moment the account exists,
rather than bouncing to a login page and losing the invitation.

Note both rate-limit call sites dedupe their keys: an anonymous caller's
identity *is* their address, and counting one failure twice would silently
halve the budget.

`GET /groups/new-code` hands the create form a free code so the code AND its
invite link can be shown while the group name is still being typed; `POST
/groups` claims it (re-minting only if it was taken in between, and saying which
code the group actually got). Nothing is reserved, so an abandoned form burns
nothing.

Membership is all that is stored; roles are owner/admin/member. The two levers
that matter after a code gets out: **rotate** (`/code/rotate`, optionally
`enabled: false` to switch code-joining off) mints a new code and kills the old
one and every link built from it, while every current member stays in; and
**remove** (`DELETE /groups/{id}/members/{uid}`, owner or a group admin, never
the owner themselves) drops that person's access on their next request. Leaving
or being removed also drops that user's auto-shares into the group, so rejoining
doesn't silently reopen everything they had pointed at it.

**Published runs** (`public_runs`, admin-only, own raids only) are readable
**without an account** — read routes take `security.optional_user`, and a caller
of None makes every ownership/membership clause compare against NULL, leaving
exactly the published set. Publishing is the one action that removes a privacy
boundary, so it is admin-gated, refused on data merely shared with them, and
audited. The SPA renders signed-out with only the routes that touch your own
data redirecting to `/login`.

### Log size and retention

`settings.upload_max_bytes` / `storage_max_bytes` (0 = unlimited, **shipped as
0**) with per-user overrides on `users`. The cap is counted as the upload
streams, so an oversized file never finishes landing on disk; the 413 carries
`X-Parse-Only-Allowed` so the UI can offer the deal instead of a refusal.
`retain_raw=0` parses the log and then drops the bytes
(`ingest_writer.drop_raw_if_unwanted`, only when no other session shares that
content address). The cost is real and enforced, not hoped for: those sessions
are skipped by `POST /sessions/{id}/reparse` and by the startup
`_reparse_stale` sweep, so no future parser improvement can ever reach them.

## Live ingest (phase 3 — the frozen ACT-DLL contract)

`GET /api/ingest/hello`, `POST /api/ingest/batch`, `POST /api/ingest/backfill/done`,
and (v11) `GET`/`PUT /api/ingest/shares`; auth is
`Authorization: Bearer <device_token>` only. A batch is gzip (or plain) JSON
`{batch_id, mode: live|backfill, lines: [verbatim lines], share_groups?: [ids]}`
→ `{accepted, duplicates, session_id}`. `backend/tools/simulate_live.py` is the
reference client (and feeds the equivalence test's batch cutter);
`improvmasta/eq2advanced-act` is the ACT plugin that implements it for real, and
the two must stay in step.

Design points, in the order they bit:

- **Batch idempotency**: response stored per `(token, batch_id)` in
  `ingest_batches`; a resend replays the stored answer. One batch in flight per
  token (429 + Retry-After).
- **Line dedupe**: `ingest_lines` key = sha256 of `(occurrence-ordinal-within-
  batch, line)`. Identical legit lines in one batch get distinct ordinals (two
  identical hits in the same second both count); a backfill/re-upload overlap
  carries the same ordinals and drops cleanly. Corollary: batches must be cut
  on log-second boundaries so a second never splits across batches — the
  simulator's `--window` does this and the DLL must too.
- **Incremental finalization is a view, not the record.** Per-session in-memory
  tail (`pipeline/live.py`); each batch re-segments the tail and flushes any
  encounter that can no longer change (a later segment exists, or GAP+grace =
  35s of log time passed) to the normal tables — that's what the Live page's
  SSE cards read. At close (`done`, or 30-min staleness) the whole session is
  **rebuilt from its raw chunks through `parse_session`** — the exact bulk
  path — so a finished live session is provably identical to uploading the
  file (guarded by `test_golden_equivalence`: encounters, actor stats, ability
  stats all byte-equal on bobby.txt; encounter ids change at rebuild, which is
  why the Live page refetches on `status: ready`).
- **Restart-safe by construction**: the in-memory tail is disposable; raw
  chunks + `ingest_lines` survive, and the close-time rebuild reparses raw.
- SSE: `GET /api/sessions/{id}/stream` (cookie auth) polls the DB ~1.5s and
  pushes `encounter` cards + `status` heartbeats (incl. uploader-online from
  `device_tokens.last_seen_ts`); closes at ready/error.

## Census sync (phase 4)

`census/client.py` (HTTP, retrying — Census reads regularly stall; service id
from `CENSUS_SERVICE_ID`, default `s:example`), `census/sync.py` (the sync +
display payloads), `census/effects.py` (effect_list grammar → structured
effects), `routers/census_api.py` (summary / refresh / snapshot history /
spell detail). Query shapes verified live 2026-08-02 — see client.py docstring.

- `sync_character()` is the ONE entry point (manual Refresh + hourly loop in
  `main.py`, which syncs owned characters >24h stale; gate with
  `CENSUS_AUTO_REFRESH=0` — tests set it in conftest.py). It fetches the
  character doc by `name.first_lower` + `locationdata.worldid=618`, updates
  `characters.class/level/census_character_id`, snapshots a TRIMMED doc
  (identity/stats/resists/gear/spell_list/AA points — not quests/collections,
  which are huge), and fills the `census_spells` / `census_items` caches for
  any scribed-spell / equipped-item ids not yet cached.
- **Snapshots only when Census's own `last_update` moved** — so history rows
  mean "the character actually changed" and the diff endpoint (stats, gear
  slot swaps, spell tier changes computed against the previous snapshot) stays
  meaningful. Manual refresh has a 60s cooldown.
- **Damage numbers exist only in effect_list text** ("Inflicts 33 - 45 disease
  damage on target instantly and every second."). `effects.py` parses that
  grammar (damage/heal/ward/power/stat/proc, ranges, %-of-health, tick
  period); anything unrecognized is kept verbatim as kind `other` so phase 5
  can only under-use a spell, never misread it. Typed spell fields
  (`cast_secs_hundredths`, `recast_secs`, `duration.*_sec_tenths`) are
  reliable — EXCEPT `recovery_secs_tenths`, which stores HUNDREDTHS despite
  the name (every spell carries 50 = the universal 0.5s recovery; dividing by
  10 gave 5s and clamped idle% to 0 — found on the real raid night, phase 6).
- The character doc's typed stats carry everything coaching needs (verified on
  Bobby: `combat.abilitymod` 1442, `basemodifier` 68.1, `critchance` 53.5,
  `ability.spelltimereusepct`/`spelltimecastpct`) — no text parsing there.
- Tests (`test_census.py`) run entirely from recorded fixtures in
  `tests/fixtures/census/` (trimmed real responses for Bobby) via a fake
  injected as `census.client._shared` — no live Census calls in CI.

### Bulk spell ingest (phase 7 groundwork)

Census spell records are BASE, pre-stat values per tier (App1–Celestial share a
`crc`) — no wiki scrape or manual entry needed; in-game tooltips are these
numbers with the player's stats applied, which is exactly `fit.py`'s damage
model. `tools/ingest_spells.py --all --max-level 70` proactively caches the
full class books via `sync.ingest_class_spells()` →
`client.spells_by_class()` (query `classes.<cls>.level=[<max>`, lowercase
class keys, `[` = Census's <= operator, `c:start` paging — verified live
2026-08-02, wizard <=70 = 1152 records; `sync.ALL_CLASSES` holds the 24
EoF-era names). Marks `spell_line:{crc}` so upgrade advice never refetches.
`census_spells` carries typed columns (`cast_s/recast_s/recovery_s/duration_s/
power_cost/dmg_min/dmg_max/dmg_dtype/dmg_period_s`, schema v4) populated on
insert by `sync.typed_fields()` — the ONE owner of the unit conversions
(including the recovery-hundredths gotcha; `fit.spellbook()` uses it too).
`dmg_*` is the primary damage effect = largest midpoint (DoT initial hit over
its tick). `sync.backfill_typed_columns()` repairs pre-migration rows;
`spell_overrides` remains the manual escape hatch where Census text is wrong.
GOTCHA: the default `s:example` service id gets burst-throttled hard on bulk
pulls ("Missing Service ID" error after ~10 full-record requests — LESS than
one class book; it also silently CLAMPS `c:limit` to 100, so paging advances
by `len(rows)` and stops only on an empty page — a short page proves nothing).
`ingest_class_spells` therefore persists every page as it lands and stores the
offset in settings (`ingest_progress:{cls}:{max_level}`): an interrupted class
RESUMES mid-book instead of restarting from zero (a from-zero fetch could
never outrun the burst budget). Only the empty end-page clears the offset and
writes the `spell_line:{crc}` completeness markers. The tool paces 30s/page on
`s:example`; a registered service id (census.daybreakgames.com signup) in
`.env` as `CENSUS_SERVICE_ID` removes the limit — start.sh and the ingest
tool both load `.env`.

## Coach engine (phase 5)

`coach/` — `descriptive.py` (session currencies: DPS/crit/autoattack share,
cast estimates, idle-GCD estimate, cure latency, rez responsiveness),
`fit.py` (observed vs Census), `replay.py` (what-if stat marginals + tier
upgrades), `advisor.py` (report assembly, persisted to `coach_reports`),
`raidreport.py`. API: `GET|POST /api/sessions/{id}/coach`,
`GET /api/sessions/{id}/raid-report`, `POST /api/sessions/{id}/calibration`.
Pages: Coach, RaidReport, Calibration.

- **Census is a prior, the parse is the evidence.** Damage model (EoF era):
  `expected = base_mid*(1+basemod/100) + min(abilitymod, base_mid/2)`; the fit
  reconciles observed non-crit means into a per-ability coefficient
  (observed/expected — ~2-4.6x on real TLE data, Census drifts a LOT) and all
  what-if math scales through it. Crit multiplier fitted per ability
  (crit mean / non-crit mean), default 1.3. Multi-effect spells: each hit is
  assigned to the nearest expected effect and the biggest cluster is fitted —
  we under-use a spell rather than misread it.
- **Stat marginals are differences of `predicted_damage`**, which is monotone
  in every stat by construction. Reuse only pays on cooldown-locked abilities
  (median inter-cast gap ≤ 1.25× effective recast); cast speed only converts
  to damage when the rotation isn't idle.
- **Tier upgrades cap at Master (tier 9)** — Ancient/Celestial don't exist on
  TLE and Grandmaster is a class choice, not a scroll. Spell lines are cached
  via `ensure_spell_lines`; Census's comma OR-list works for `id=` but
  silently returns NOTHING for `crc=` (verified live 2026-08-02), so
  `spells_by_crcs` is one request per crc, marker-gated in `settings`.
- **Spellbook join**: log base name → the character's highest scribed version
  at or below their level (Bobby has above-level RoK pre-scribes), overrides
  from `spell_overrides` applied on top.
- **Calibration**: a session flagged via `POST /calibration` (auto-pins) is
  per-ability ground truth — its coefficients override every later report for
  that character (confidence `calibrated`). Uses current snapshot stats, so
  recalibrate after big gear changes.
- Report degrades gracefully with no Census snapshot (currencies + findings,
  `no_census` finding); a Census outage only costs the tier-upgrade section.

### Coach correctness (phase 6, 2026-08-02) — what fixed the v1 debt

Lindsay's v1 verdict was "good base, far from correct"; phase 6 attacked that
list in dependency order. What changed:

1. **Cast ground truth** (`parser/flavor/`): `You prepare` lines resolve to
   ability names — generic article-strip ("the Bloodcloud" → Bloodcloud) +
   `to inflict X on <tgt>` + per-class prose maps (`necromancer.py`, verified
   on bobby.txt; every fixture flavor resolves). CRITICAL WRINKLE: only spells
   with a cast bar print prepare lines — instant casts (Lifetap, 344 hits,
   zero prepares) never do. Discriminator in `descriptive.py`: prepared →
   real count; in the spellbook but never prepared → real instant spell,
   initiation-estimated; in neither → buff/item proc, ZERO casts (Lich's
   Siphoning et al. stop polluting idle%). `currencies.cast_source` says
   which mode ran; `casts` also lands on the damage-kind rollup row.
2. **Two-point calibration** (`fit._solve_two_point`): dummy parses at two
   abmod values (≥100 apart; stats are CAPTURED per session at flag time in
   `sessions.calib_stats_json`) solve the TRUE base piecewise
   (uncapped/capped/mixed hypotheses, 10% tolerance). With a solve, the fit
   row swaps Census base for truth (`base_source: calibrated2`) — the abmod
   cap in every marginal becomes real. Until a second point exists the report
   carries a `calibration_second_point` finding.
3. **k-spread = debuff measurement** (`fit.apply_calibration`): the dummy fit
   NEVER overwrites a healthy raid fit — `k_dummy` rides alongside and
   `debuff_uplift = raid_k / dummy_k` per ability, medianed per damage school
   into `report.debuff_uplift`. Dummy k substitutes only when the session
   sample is thin (<5 non-crits).
4. **Ability catalog** (`census/catalog.py`): populated from every cached
   census spell (name + base_name, class, unit=player) and — via the
   effect-grammar `proc` kind ("may cast X on ...") — the proc'd names get
   `proc=1`. Curated rows (pet kits + observed buff/item procs from
   bobby.txt) always win over census rows. Consumers: `fit.spellbook` drops
   pet-kit names from the player join; a **k sanity gate** (0.2–12; Master's
   Strike misjoined at 54.6) marks the rest `suspect_join` → excluded from
   marginals/upgrades, surfaced as a finding.
5. **Healer/utility currencies**: HP-deficit reconstruction in `statsroll`
   (full HP assumed at pull; wards never touch HP) → `overheal_est` +
   `save_count` (heal landing at ≥60% of the target's worst deficit) +
   `ward_bleedthrough`, persisted per actor and rolled into raid report +
   coach findings. Logger-only debuff uptime (`descriptive._debuff_uptime`):
   real cast starts + Census durations vs burn windows (rolling 10s raid
   damage ≥1.5× encounter mean). All flagged as estimates in the UI.
6. **Engagement classifier v2** (`raidreport`): catalog-proc abilities never
   anchor; inside the opening 2s an ability that fires ≤1s after being hit is
   a reactive proc (skipped); the logger's own prepare line is an exact
   high-confidence anchor (`anchor: cast`); remainder keeps the low-confidence
   flag.

Still open (smaller, unchanged from v1 item 7): rez/time-dead next-action
proxies, DoT tier-upgrade tick tail, reuse marginal rotation displacement.
The abmod marginal is only as good as the calibration points backing it —
Lindsay still needs to RUN the two dummy parses.

## Raid Report (phase 5)

`coach/raidreport.py`, computed on demand from stored events (no schema
change). Per encounter + per night, all raiders in the log: damage/share/DPS,
deaths, time dead (death → next own action), **death DPS cost** (alive-DPS ×
time dead), cures delivered, rez delay, heals/wards/power.

**Engagement timing with the proc caveat** (verified on the Zylphax pull —
pre-pull wards/procs flood the log ~1s after the real opener). Engage is the
gap between the pull and a raider's FIRST ACTION, and `engage_anchor` names
which kind of action stopped the clock:

| anchor | line that fired it |
| --- | --- |
| `cast` | the logger's own prepare line — exact, always high confidence |
| `autoattack` / `ability` / `pet` | damage on a non-ally, or positive threat |
| `autoattack` / `ability` | an *attempted* swing the mob avoided (v3) |
| `heal` / `cure` / `rez` | a heal, a `relieves`/`dispels` cure, or a rez (v3) |

Never anchors: ward absorbs (the line prints when the MOB swings), catalog
procs (any type), and an ability inside the opening 2s that fired ≤1s after
the player was hit (reactive damage-shield correlation). Anything else inside
the opening 2s is flagged low confidence — a pre-pull HoT ticks the instant
the pull lands and the line cannot say which it was. Night rollup averages
named-fight delays only and carries `engage_anchors` (kind → count).

**v3 (2026-08-03) fixed two ways of reading a raider as absent**: only hostile
actions counted, so a templar healing from the first second of Sawtooth the
Ancient scored 13s (their first *damage*) instead of 2s, and a wizard whose
opener missed was dated to the next spell that landed. `test_engagement.py`
pins both. The remaining honest limitation: only the uploader's cast STARTS
are logged, so for everyone else a 4s cast is dated when it lands.

## The subject model (the crux — verified against a real raid log)

- `YOU` / `YOUR <Ability>` = the logging player, always.
- **Bare logger-name = the logger's own pet** (pets can share the owner's exact
  name). `<logger>'s <Ability>` = pet ability.
- Other single-token capitalized names = players (their pet conflates into
  them — community parse convention; fixed properly when they upload their own
  logs).
- `<Owner>'s <lowercase pet>['s <Ability>]` = swarm-pet chain.
- Possessive hazards: `Aros' Soulrot` (ability) vs `Aros's blighted horde`
  (pet); never split inside a capitalized remainder (`Autumn's Kiss`,
  `Treyloth D'Kulvith's Bloodcoil`).
- `focus` dtype = self-inflicted (Vampiric Requiem) — excluded from DPS,
  rolled up as ability kind `self`.
- Logger deaths: `<Killer> has killed you.` + `You regain consciousness!`.

Parser truth source: `backend/tests/test_parser.py` (verbatim log lines) and
`backend/tests/test_golden.py` (full /home/lindsay/bobby.txt fixture: 275,822
lines, 10 segments, 0 unmatched damage lines). **Rerun the golden test after
any parser or segmentation change.**

## Encounter segmentation

EQ2 logs have no encounter markers. Segments = combat-silence gap ≥ 7s (ACT's
~6s idle timeout, measured against Lindsay's Emerald Halls zone view: ACT cut
that night into 61 encounters totalling 1:13:12; our old 25s gap merged chain
pulls into 34 and inflated every EncDPS denominator ~8%). Damage AND avoided
swings hold a fight open; hard-cut on zone lines. Corollary: real mid-fight
lulls > 6s split the fight — Garanel's 21s lull yields two segments (the first
labeled by its named add, Garanel's Shade), which is exactly what ACT shows
for the same night.

### Naming: the enemy fought, not the enemy that died (2026-08-03)

`pipeline.encounters.encounter_label` titles a segment after the **mob that
took the most damage in it**, and sets `success` to whether that mob died.

This replaced labeling from `has killed <Named>`, which had a hole big enough
to hide a raid night in. A wipe produces no kill line, so it could never be
named: it became an anonymous "trash" row, and `seg.success = 1` sat on the
same branch as the name, meaning **no code path could ever record a 0**. The
whole database held 181 encounters at `success=1` and 371 at NULL, zero
losses. Emerald Halls read "9/9 named", which is really nine kills out of nine
kills — while two Galiel Spirithoof wipes, a Farstride Unicorn wipe and a
Treah Greenroot wipe sat in the trash list holding most of the night's
time-dead. It now reads 10 nameds engaged, 7 killed.

Most-damaged reproduces ACT's titles on that night row for row, including the
cases where the raid's damage went into an add rather than the boss — ACT
titles the Treah Greenroot wipe "a knotted guardian", and so do we. Naming
needs resolved entities (which target is a mob), so it runs in the write path
(`ingest_writer.parse_session` and `live._flush`) rather than inside the pure
segmenter. `zone_runs.success_count` counts named kills specifically, since
trash now carries a real success flag too.

Known residual: ACT cut that night into 61 encounters and we make 60. ACT
split Galiel's two pulls at a **5s** gap — the only gap ≥3s in our merged 499s
segment — but no single threshold reproduces its set: at 5s we make 63 (two
extra splits ACT also makes, so ACT must additionally drop the two segments
where no enemy was ever damaged), at 6s we make 61 but split the wrong fight.
The plugin itself does not decide this — `ACT_English_Parser.cs` only calls
`SetEncounter(time, attacker, victim)` and its kill-ends-encounter branch is
commented out, so the boundaries are ACT core's inactivity timer.

## Zone runs (the navigation model — 2026-08-03)

Files ("sessions") are the INGEST unit only; the UI navigates **zone runs** —
one contiguous visit to one zone by one character, derived entirely from
encounter rows by `pipeline/zoneruns.py`:

- **Dedupe**: overlapping uploads produce byte-identical encounters
  (segmentation is deterministic per parse_version). `rebuild_zone_runs`
  groups a character's encounters by `(started_ts, ended_ts, zone, name)`;
  the copy from the widest-coverage session is canonical, the rest get
  `encounters.dup_of` and never join a run. Marking, not deleting — every
  parse stays complete and the marks re-derive after any reparse. Sessions
  only dedupe against equal `parse_version` (mid-sweep safety).
- **Segmentation**: canonical encounters in time order split on a zone change
  or an idle gap > `ZONE_RUN_GAP_S` (3600s). NULL-zone encounters (log began
  mid-zone) form their own "Unknown zone" runs.
- **Id stability**: the upsert matches recomputed runs to existing rows by
  zone + overlapping time window, so `/zones/:id` URLs survive reparses and
  backfills; rollup columns recompute every rebuild.
- **Roster** (`raider_count`): see below — the count that decides what the
  Home page calls a raid.
- **Hooks**: end of `parse_session`, live `_flush` (when fights land), and a
  startup relink sweep in `main.py` before AND after `_reparse_stale` — the
  sweep is also the migration for pre-zone_runs databases (schema v6:
  `zone_runs` table + `encounters.zone_run_id/dup_of`).

API: `GET /api/zone-runs` (list, powers the date-grouped Home page),
`GET /api/zone-runs/{id}` (run + canonical encounters with logger headline),
`GET /api/zone-runs/{id}/report` (raid report scoped to the run —
`raidreport.build_for_encounters` handles cross-session sets, pulls pruned
encounters from frozen reports, flags `partial`). `GET /api/encounters/agg`
accepts cross-session id sets: every session is visibility-checked, actors
merge by `name|kind` (`key` + `entity_ids[]` in the payload), abilities by
source key, with `rollup_key` resolving pet credit (players self-credit —
their DB `rollup_to` is NULL).

Frontend: `/` = `Home.jsx` (one sortable table of runs), `/zones/:id` =
`ZoneRun.jsx` (fight rail + tabs Overview/Damage/Healing/Defense/Insights;
`?sel`/`?actor`/`?tab`/`?cmp` all URL state), right-hand `ActorPanel`
(per-actor drilldown) or `ComparePanel` (checkbox multi-select, per-metric
grouped bars from `lib/stats.js`). `/import` = the import hub (live link,
log files, ACT export) and file management, with `/uploads` redirecting to it;
`/sessions/:id` (Workspace) survives as the per-file debug view.

### The roster, and what counts as a raid (2026-08-03)

An encounter is a time slice, not a guest list: it holds every combat line the
log heard while you were fighting. `raider_count` used to be "distinct
`player` entities in the run's busiest fight", which counted all of them, and
the Home page's raids-only filter (>= 7 people, one more than a full group)
turned that into a wrong answer — a six-man Halls of Fate night read as a raid
off two strangers.

`_raider_count` in `pipeline/zoneruns.py` is now the run's ROSTER, three rules
deep, keyed by entity NAME (a run spans files, entities are session-scoped):

- **player** — mobs and the pooled `Unknown` source are not people. A
  single-token capitalized mob name is a player as far as `classify_entity_kind`
  can tell, and the ones `pipeline/refine.py` doesn't catch land in this table
  looking like raiders (`Ishi-Kurrat`, `Axxyk'Tuur`).
- **acted** — damage, heals, wards, cures, power, rezzes or swings. A row with
  nothing but `damage_taken` is a bystander who got clipped by an AE.
- **presence** — they turn up in >= `ROSTER_PRESENCE` (25%) of the run's
  fights, minimum 2; under `SHORT_RUN_FIGHTS` (4) there is no attendance to
  read and one is enough. This is the rule that does the work. Measured over
  Lindsay's 49 runs, a raid sits at 45-100% of its fights and passers-by at
  3-15%, with nothing in between: Castle Mistmoore is four regulars across
  12-18 of 23 fights plus another group that appears in exactly 2.

A cooperation graph (link players by support flowing between them or by damage
into a shared enemy, keep the logger's component) was built and **rejected**:
it changed 0 of 49 runs, because a passing group does hit the mobs you are
hitting. Don't rebuild it without a log where presence demonstrably fails.

Net effect on the real corpus: Emerald Halls 26 -> 24 (the raid was 24), Lord
Vyemm 32 -> 26, Halls of Fate 7 -> 6 (no longer a "raid"), Freethinker Hideout
25 -> 25 and Ascent of the Awakened 12 -> 12 (real raids, untouched).

### The fight rail (2026-08-03)

`components/EncounterTree.jsx` is the zone page's navigation AND its scope
control, so the two gestures are kept distinct: **clicking** a fight (or All, a
zone block, a `Trash ×N` group) makes it the only selection; **ticking** its
checkbox adds or removes it from the current one, which is how several pulls
merge into one set of combined stats. The boxes always show what is currently
counted, so "All" visibly means all sixty fights. Selection stays in `?sel=`
(an id list, or absent for all), so a merged set is a shareable URL; selecting
everything collapses back to `all` rather than a 60-id query string.

Rows are three fixed columns — checkbox, `9:35p` start, name — with the length
right-aligned as `m:ss` in tabular figures. `Trash ×N` groups keep their
twisty, and a group checkbox ticks the whole group (indeterminate when
partial).

**Wipes are counted by default** (2026-08-03, was excluded): ACT counts them,
and a night with two Galiel wipes IS a night with two Galiel wipes. The rail's
switch (`?wipes=0`) takes them out of every total while leaving them listed and
dimmed, and the page head then says how many were left out. Selecting a wipe on
purpose always shows it — the filter can never empty the page.

**Pet rows** are off by default on Overview. A pet's damage is credited to its
owner (`statsroll.actor_key`, ACT does the same), so a pet actor row can only
ever carry what the pet TOOK — `Tragedy's unswerving hammer` is a real paladin
hammer pet with a real DmgTaken figure and nothing else, which reads as a
parse fragment sitting among the raiders. The `Pets` switch brings the rows
back; `NPCs` still governs mob/environment rows.

### Read caches (2026-08-03)

Clicking a zone re-earned the same expensive answer every time: the run report
replays every stored event (~1.5s on the 60-fight Emerald Halls night) and
`/encounters/agg` recomputes medians from events.

- **Server** — `backend/memo.py`, a 12-entry in-process map keyed by
  `(epoch, key)` and used by `/zone-runs/{id}/report` and `/encounters/agg`.
  Authorization happens BEFORE the memo on every request; only the computed
  payload is shared, and callers copy it before adding fields. Every write
  bumps the epoch and clears the map: `rebuild_zone_runs` is the funnel
  (uploads, live closes, reparses, deletes, hand edits) plus `prune_once`,
  which deletes events without touching run membership. A build that races a
  write is discarded instead of stored. `test_memo.py` pins the invalidation.
- **Client** — `lib/api.js` keeps a read-through map of GET payloads for the
  session and every mutation clears it (`clearCache`). `peek(url)` returns a
  cached payload synchronously, which is what lets `ZoneRun` repaint on a
  click instead of blanking; an uncached selection keeps the previous numbers
  on screen dimmed (`.wsmain.stale`) rather than replacing the page with
  "Loading…".

### Hand edits to the raid list (schema v8 — 2026-08-03)

Segmentation is a guess, so the list is editable: delete a raid or a fight,
merge runs the game logged as two visits, unmerge them again. The hard part is
that a reparse DROPS AND RECREATES every encounter row — an edit keyed by
encounter id would silently evaporate on the next backfill. So `run_edits`
keys by **fingerprint** — `<started_ts>|<zone>|<name>`, the dedupe key minus
`ended_ts`, which every duplicate copy of a fight shares — with three kinds:

| kind | meaning | written by |
|------|---------|------------|
| `delete` | hide this fight everywhere | `POST /api/encounters/delete`, `DELETE /api/zone-runs/{id}` |
| `join` | never start a run here (merge) | `POST /api/zone-runs/merge` |
| `break` | always start a run here (unmerge/split) | `POST /api/zone-runs/{id}/split` |

`rebuild_zone_runs` is the only writer of run membership, so every edit is
applied by re-running it: deletes re-stamp `encounters.deleted_ts` (a derived
mark — `run_edits` is the truth) and drop out before dedupe, and `_segment`
consults breaks/joins at each boundary. `POST /api/encounters/restore` removes
delete rows (the Undo on Home), `POST /api/zone-runs/{id}/unmerge` removes the
joins inside one run, and the run list carries `merged` so the UI only offers
Unmerge where there is something to undo.

`DELETE /api/sessions/{id}` is the only thing that destroys data: derived rows,
ingest bookkeeping, frozen reports, and the raw bytes (content-addressed, so
the file goes only with the last session pointing at it). It also drops the
`run_edits` whose fingerprints no longer match any surviving encounter —
otherwise re-uploading the same log would come back with every deleted fight
still hidden and nothing on screen to explain why. Home surfaces this as: all
fights deleted -> "this log has nothing left in it, delete it too?"

## Reading the raid, not just counting it (2026-08-03)

The tables were complete but flat: every number was a per-fight total, nobody
had a class, and "damage" was one bucket regardless of where it came from.
Four backend additions and the UI built on them.

### Class inference (`pipeline/classguess.py`)

The log never states anyone's class, but it states what they cast, and
`ability_catalog.class` knows who can cast what. Per player: take the distinct
ability names they used, drop the autoattack buckets (class-blind), pet-kit
names, and **procs** (gear fires those — they say nothing about the caster),
then let each remaining name vote for its class. A `characters` row with a
Census class overrides the vote outright (`source: "census"`, confidence 1.0).

**Rebuilt 2026-08-03** — the old version answered per FILE, with whole votes
only, and got 198 of 981 player rows named. Three changes:

- **Evidence pools across sessions, keyed by NAME** (`_evidence`, one 0.1s
  query over the whole database). Guessing per file gave the same person a
  class in one raid and nothing in the next, and for 19 players two different
  answers in the same list (Zooey: defiler here, mystic there). The answer is
  written back to EVERY entity row with that name, so it applies to older
  raids without reparsing them.
- **Shared spells vote in fractions.** "conjuror,necromancer" used to be
  discarded; it is now half a vote each — it cannot pick between the two, but
  it is real evidence against the other twenty-two.
- **A margin rule beside the share rule.** A winner needs whole-vote evidence
  (`MIN_STRONG`, 2 single-class spells), `MIN_SCORE` of weight, and either a
  majority of the weight cast or double the runner-up. The margin is what
  names a player whose gear procs are not all flagged: Shaly scores 14.5
  coercer against 7 bruiser and 4 each of three more — 39% of the weight, and
  obviously a coercer.

Measured on the real database: 131 → 147 names resolved, no answer changed,
none lost, and the per-session disagreements gone.

**What it still cannot do, and why it looks worse than it is.** Roughly half
the ability names in a real raid log — 433 of 919 — have no Census row at all:
AAs, gear procs and item effects are not in the spell books, and the bulk
ingest only pulled ~130 spell names per class. Of the 230 names with any
ability evidence, 38 are named pets misfiled as players (Reaper, Viber and
Zarann cast nothing but `Grim *` necro pet spells), and the rest are players
whose visible kit is entirely uncatalogued. Fixing coverage is a Census
ingest job (the `alternateadvancement` collection), not a voting change.

Stored as JSON in the long-dormant `entities.class_guess` column —
`{"class","confidence","matches","source"}`, read back by
`parse_class_guess`. No schema change. Written at parse time and lazily
backfilled by the encounters API (`backfill_session`, guarded by one indexed
lookup plus an attempted-set), so sessions parsed before any of this existed
light up without a reparse.

`/api/encounters/agg` and `/encounters/{id}` carry `class`,
`class_confidence`, `class_source`, and `archetype` on every actor —
`archetype` is NULL when the class is, since `archetype_for` defaults to
"dps" and that would read as a claim we never made. Cross-session merges keep
the highest-confidence guess.

### Proc exposure

`ability_catalog.proc` existed (set from the Census "may cast X on…" grammar)
but never left the backend. Ability rows now carry `proc` and
`ability_class`, which is what lets the UI split a parse into cast / auto /
proc — the difference between a player's rotation and their gear.

**The name alone over-claims** (fixed 2026-08-03). Census flags a name if ANY
item or buff can cast it, so a class's own combat art gets marked as gear the
moment some proc references it. `_proc_flag` (encounters_api) makes the flag
per ROW, requiring the catalog's claim to survive this actor's evidence:

- `casts` counts prepare lines, which procs never print — a cast is proof.
- the ability is in this actor's own class spellbook.

That second test needs a column: `ability_catalog.class` means two different
things depending on which statement wrote it. From a spell record it is the
classes that SCRIBE the ability; from a "may cast X" effect it is the classes
whose buff FIRES it, which says nothing about who can press it. Hence
`ability_catalog.scribed` (schema v8) — only a scribed row can clear a proc
flag, and curated procs carry no class list at all, so they stay flagged for
everyone. `catalog.backfill_scribed` (startup) repairs pre-v8 rows from
`census_spells`; without it every pre-existing catalog row reads as unscribed
and only the cast evidence bites.

Still NOT solved: **buff attribution**. Damage from another player's buff
proccing on you is entirely yours, and sourceless `is hit by <Effect>` lines
still pool under "Unknown". Real contributed-DPS for utility classes needs
buff application/expiry tracked into uptime windows — parser work, not an API
change.

### `GET /api/encounters/timeline?ids=…&bucket=auto`

Per-actor damage / heals / damage-taken bucketed over time. The clock is the
**concatenated** combat clock — the between-fight gaps are removed — so a
multi-fight selection reads continuously and `duration_s` still equals the
summed `duration_s` the tables divide by. `segments[]` carries the fight
boundaries, `markers[]` the deaths. `auto` picks the finest bucket from
`[1,2,5,10,15,30,60]` that stays under 240 columns. Credit follows the same
pet rollup as `statsroll`, and series key by `name|kind` to match `/agg`.
Pruned sessions contribute nothing and are counted in `pruned_encounters`
(`pruned: true` when all of them are) — the tables still work there, the plot
does not.

### `GET /api/encounters/deaths?ids=…&window=12`

One entry per player death with the incoming hits and the healing received in
the `window` seconds before it (`t` relative and negative, far edge
inclusive, clamped 3–60s, capped at 40 entries per list with a `_truncated`
flag). Deaths use the same death/kill rules as `statsroll`, including the
logger's bare-name pet.

### `GET /api/encounters/aoes?ids=…`

Incoming raid AoEs for the selection (`pipeline/aoes.py`), one row per
(enemy source, ability) with every detected cast attached.

The log never says "this was an AoE", so the definition is behavioural: a
second in which ONE enemy ability touched at least `MIN_TARGETS` (5) players
is a **cast**. Everything that ability does for the next few seconds — DoT
ticks, a second wave on a second group — belongs to that same cast; the merge
threshold is `max(6s, 0.4 x the reported timer)`, because a 60s AoE does not
land twice in 24 seconds. Both damage and *ability-named* avoids count: "The
Corsolander tries to crush Brandomar with War Stomp, but Brandomar resists" is
the AoE, while a bare "tries to crush X, but X parries" is a melee swing and
carries no ability, so it never enters.

Two timers sit side by side and the gap between them is the point:

- **reported** — ACT's spell-timer list, shipped as `backend/refdata/
  act_spell_timers.json` (446 entries extracted from Lindsay's ACT config;
  only `<SpellTimers>` name/duration/category, not the chat triggers). Joined
  by ability NAME, which works because ACT keys off the same log string.
- **observed** — the shortest interval between two casts that REPEATS, within
  one fight (the wait between two pulls is a raid taking a break, not a
  cooldown).

Shortest-repeating rather than mean or median because of how the measurement
fails: an AoE that never reached five people is a cast we cannot see, and a
missed cast makes one gap look like two — it can only ever make a gap LONGER.
So the smallest gap that happens more than once is the closest thing to the
real timer, `observed_agree` says how many intervals agreed (two is a guess,
twenty is a measurement), and `missed_hint` counts the gaps that look like
multiples. On the real DB: Blanket of Eternal Night 60.2s observed vs 60
reported (22 agreeing), Ydalian Bolt 47.7 vs 49, War Stomp 47.4 vs 45 —
and Furious Storm reads 52 against a reported 45 from three different
casters, which is the reported timer being wrong for this expansion.

**Coverage** is who was not hit: `avoided` (Bladedance and friends, an avoid
event) plus `absorbed` (Tortoise Shell and friends, a zero-damage hit with
`F_ZERO`), minus anyone the same cast also hit — a second wave landing on
someone who parried the first is a hit, not a block.

GOTCHA: entities are keyed by NAME, so six "a maven of wisdom" pulling the
same AoE read as one mob casting it six times as often. `instances_hint`
flags the giveaway — an observed timer that is a clean fraction of the
reported one, from a source that is not a named — instead of reporting the
ACT list as wrong. Named bosses, the case that matters, are unique.

### Frontend

- `lib/classes.js` owns identity. **Color is assigned by EQ2 archetype
  (fighter/priest/mage/scout), not by class** — the palette validator says
  four hues separate cleanly and twenty-six cannot, so the family color
  carries identity in stripes and legends while the per-class tint is
  decoration on a chip that always spells the class out. Role
  (tank/healer/dps/utility, mirroring `coach.descriptive.ARCHETYPES`) drives
  the filter chips instead. Chart series use their own 8-color validated
  palette in fixed selection order, since two raiders of one class would
  otherwise draw the same line.
- **Selection sums** (`SelectionBar`): checking rows adds them up in a sticky
  footer instead of immediately hijacking the panel — comparing is now a
  deliberate second click. The same bar serves the ability breakdown, so
  "what fraction of my parse is my priority spells" is a few checkboxes.
- **Rank coloring** (`stats.rankClass`): a number is colored against the
  same-role raiders currently on screen, and says nothing at all below four
  peers, which is not a distribution.
- **Decomposition** (`stats.decompose`): DPS split into activity × hit size ×
  crit × alive%, each against the best peer, naming the biggest gap — the
  difference between "you're 20% behind" and "you cast 30% less".
- New tabs/panels: `TimelineChart` (crosshair, fight bands, death markers,
  table view), `DeathRecap`, `CompositionBar`, plus tier upgrades / debuff
  uplift / per-ability fit, which the coach API had always returned and
  nothing rendered.
- `GET /api/zone-runs` gained `spark[]` (raid DPS per fight) for the home
  page sparklines — one grouped query for the whole list.

## ACT parity (diffed 2026-08-02 vs Lindsay's ACT screenshot, Zylphax the Shredder)

The Zylphax encounter now matches ACT **exactly — all 25 players to the point
of damage** (`test_act_parity_zylphax` guards it). Three bugs found and fixed:

1. **Logger's swarm pets didn't roll up** (Bobby −17.3%): in the possessive
   owner slot (`Bobby's blighted horde`) the logger's name means the PLAYER,
   but the bare-name-is-pet rule classified it as his pet → rollup dropped.
2. **`lastrowid` after `ON CONFLICT DO NOTHING` is garbage** (connection-wide
   last-insert id, any table). Harmless on a fresh DB; corrupts ability
   attribution on ANY second session or reparse. Now guarded by `rowcount`.
3. **Non-focus self-hits counted as damage** (Spades +746): ACT excludes all
   self-inflicted damage from Damage, we only excluded `focus` dtype. Self-hits
   now shelve under ability kind `self` like focus does.

### ACT parity round 2 (2026-08-03, Emerald Halls zone view, 25 players)

Diffed the full zone-wide combatant table against Lindsay's ACT screenshot.
The ACT model, now implemented (each verified numerically):

1. **Ward absorbs fold into the hit they mitigated** (`parser/classify.py
   _pair_wards`). The log prints the absorb line BEFORE its hit line; the hit
   line shows only bleed-through ("fails to inflict any damage" when fully
   absorbed). ACT reconstructs the pre-ward hit: absorbed damage counts for
   the attacker AND the target's damage taken; the warder separately keeps
   the full absorb as healing (asymmetric on purpose). Pairing key is the raw
   target string — WRINKLE: absorbs say "to YOU", the logger's mitigated
   self-hit says "hits YOURSELF"; normalize both to YOU. An absorb that never
   pairs (fully-absorbed DoT tick — no line at all) still counts as the
   target's damage taken. This also closed the old "Unknown parity" gap:
   Zylphax's warded Stench of Death pool is 1.18M ≈ ACT's ~1.17M.
2. **Self-inflicted damage is excluded from DamageTaken** too (Vampiric
   Requiem et al.) — previously only excluded from Damage. Bards' wards
   eating their own Requiem ticks was 60-80% of most players' inflation.
3. **Cures = `relieves` + `dispels` lines, any target, credited to the
   caster.** The real cure grammar is "X's Ability relieves Effect from Y" —
   we only parsed `dispels` (buff strips). 25/25 players exact.
4. **Deaths: the logger's bare-name pet death ("… has killed Bobby") counts
   as the player** — ACT can't tell same-name pets apart. 25/25 exact.
5. **PowerReplenish includes self-gains** (Lich, Savant's Intelligence). The
   drain grammar is a verb family (`confounds|zaps|smites|diseases|…
   draining N points of power`) — we only knew `confounds`; Ipax alone had
   2.5M unparsed drain. Both now match ACT (drain 20+/25 exact).
6. **Damage-taken does NOT roll possessive pets into owners** ("Tragedy's
   unswerving hammer" is its own combatant on the taken side; outgoing still
   rolls up). `statsroll.taken_key`.
7. **EncDPS/EncHPS denominator** = Σ encounter durations, same for everyone;
   EncHPS numerator = heals + wards (ACT's Healed column includes wards —
   verified via Lotus: 11.13M ≈ ACT 11.08M).

Result on the Emerald Halls night: damage 21/25 exact (rest within 0.003%),
cures 25/25, deaths 25/25, power drain ≈exact, EncDPS/EncHPS within 0.7%.
Still open, in residual-size order:
- **Combat clock 4421s vs ACT 4392s** (60 vs 61 encounters): boundary-second
  differences (~0.5s/encounter) in ACT's internal open/close rules we can't
  fully reverse-engineer from screenshots — uniform, doesn't reorder anyone.
- **Damage-taken residuals -1..-3%** (Artonk -6%): suspected intercepts
  ("Bobby intercepted some of the damage…" carries no amount) + boundary
  trimming. Trailing-event trimming was tried and REGRESSED cures/EncHPS —
  ACT keeps idle-window heals/power in the encounter; don't re-add it.
- Emericant's ±6,307: ACT files manastone/potion self-power as PowerDrain.

## Rezzes, revives, intercepts and the adjusted delay (2026-08-03, schema v10)

Four things the log says that the parser was not listening for, plus one stat
ACT cannot express. `PARSE_VERSION` 13; the startup sweep rebuilds everything.

### Every rez family counts (`RE_REZ`)

`rez` matched exactly one line — `X petitions the divinities of resurrection.`
That is the CLERIC flavor. Druids "call forth primeval forces of
resurrection", shamans "primal forces", and those 73 casts (of 142 in the raid
logs) were invisible: Ramms and Squigs showed **zero** rezzes on a night they
cast 41 between them. The regex now takes an open verb (`petitions|calls
forth|beseeches|invokes|implores|summons`) and identifies the line by its
trailing `…resurrection.`, keeping the flavor text in `extra` so an unseen
family shows up as data rather than as a gap. `A resurrection spell is cast on
X.` parses as a rez with a target and no caster.

### Revives, and Time dead stops being a permanent zero

The landing side prints for everyone in range — `X is revived!`, `X is
resurrected!`, `You are revived!` — and only the logger's `You regain
consciousness!` was parsed. With all of them, `encounter_actor_stats.
time_dead_s` (a column that existed and was never written, so the aggregate
reported a confident 0) is filled: death → the first of {revive, acting
again, end of fight}, clamped to the encounter. The raid report uses the same
three-way rule, and `test_agg_time_dead_matches_the_report` pins them
together — two places printing different death times for one fight is worse
than either being slightly wrong.

`You lose consciousness!` is the logger's own death when nothing takes kill
credit. It coincides with `<Killer> has killed you.` every time in the raid
logs, so `_dedupe_repeats` collapses it — ACT death parity is unchanged (141
death events before and after).

### Intercepts (`RE_INTERCEPT`, `encounter_actor_stats.intercepts`)

`Bobby intercepted some of the damage intended for you!` — someone eating a
hit meant for someone else. Three limits are structural, not bugs to fix
later: the log carries **no amount** (so this is a count, and the UI tooltip
says so), the victim is only ever named from the logger's seat (`you` / `your
target`), and the two variants are the same event printed twice — 1270 of the
1442 intercept seconds carry both, so `_dedupe_repeats` keys on (type, who,
second). Two intercepts inside one second are indistinguishable in the log;
one is the honest floor. Credit goes through `decompose`, so the logger's
bare name resolves to their pet exactly as everywhere else.

This is also the suspected residual behind ACT-parity damage-taken running
1-3% light (see above): ACT cannot see the moved damage either.

### "AvgDelay adj" — the gap between button PRESSES (`_activations`)

ACT's Avg Delay is swing span ÷ swings, so a DoT ticking six times and an AoE
hitting five mobs read as eleven actions. Asked what it is really wanted for —
"how often did they press something" — the answer needs ACTIVATIONS:

- hits of one ability in the same second are one press (AoE across targets);
- a hit within one tick period of the previous hit **on the same target**
  continues a chain (DoT tick, multi-hit) instead of starting a press;
- autoattack is not a button and `kind='self'` rows are a cost, not an action;
- catalog procs fire themselves, so they are out of the per-actor total.

The tick period comes from Census `dmg_period_s` (via `catalog.press_inputs`,
collapsed onto `base_name`) when it is known — only ~60 base names — and is
otherwise inferred from the ability's own hits. **The discriminator is modal
dominance, not average regularity**, which the real logs settle: Bloodcoil's
same-target gaps are 3s 75% of the time and Grave Decay's are 1s 86% of the
time (EQ2 ticks these every second), while Lifetap, a nuke, spreads across
8-14s with no single gap over 15% and Dynamism's most common gap is 17% of
its gaps. So the modal gap must carry half the chain and appear at least four
times before anything is folded away — a rotation's jitter never clears that
bar, which is the failure that matters (folding real presses away would
understate a player).

Stored per ability (`presses`, `press_delay_s`) and per actor (`presses`,
`press_span_s`, with the per-actor set deduped by second — two abilities in
one second is one moment of activity). `_avg_delay_adj` divides span by
gaps, so it sums across encounters the same way ACT's does. On the Zylphax
fight ACT's AvgDelay reads 0.14-0.39s for the top parsers — a number nobody
can act on — while the adjusted delay reads 1.2-1.65s and separates them:
Spades 1.21s against Bobby 1.65s.

## Phase 7b — attribution overhaul + stats engine v2 + workspace UX (2026-08-02)

**Pet knowledge base** (`parser/petnames.py`, global `pet_names` table): named
pets (`Ellea's Lunar Attendant`) are grammatically identical to abilities with
internal possessives (`Banjeaux's Daro's Dull Blade`), so ONLY names in the
knowledge base decompose as pets (`Subject.unit == "named_pet"`). Sources:
curated seed, plus **learning** — every parse prescans its raw lines for
`Alas, <Owner>'s <Capitalized> has died…` evidence (kill-victim guard rejects
mob adds like `Garanel's Shade`), unions it with the global table, and after
the parse persists new names + every ability actually cast by a pet entity
(`ability_catalog`, `source='observed'`; curated > observed > census).
Knowledge applies **backwards** two ways: the prescan covers the current file
from line 1, and `sessions.parse_version` + the startup reparse sweep
(`main._reparse_stale`; also `POST /api/sessions/{id}/reparse`) re-attribute
old sessions whenever `PARSE_VERSION` bumps. A session stuck at `parsing` on
startup is an orphan (dead worker) and is swept too. Conflated pets (another
player's pet under the owner's own name) can't be split — ability rows whose
name is a known pet ability get `via_pet` at API read time instead (damage
stays with the owner, matching ACT).

**Behavioral mob refinement** (`pipeline/refine.py`): single-token capitalized
names default to "player", but a kill-victim of a player-credited kill line, or
a name that trades damage with confirmed players (≥2 hit / ≥3 hitting) without
ever touching a heal, reclassifies to `mob` — so one-word bosses ("Venekor")
stop appearing as raiders and their kills label the encounter. Target-side
resolution now decomposes possessives exactly like source-side, so damage
taken by `Ellea's blighted horde` lands on the same entity row.

**Stats engine v2** (schema v5, `pipeline/statsroll.py`): per-ability avoid
breakdown (`misses/parries/ripostes/dodges/blocks/reflects/resists` — the
`parries`→"parrie" bug is dead), `zero_hits` (absorbed hits stay inside `hits`
for ACT parity; min/max/median are non-zero), `median`, `avg_delay_s`,
`dtypes` (JSON per-school split incl. dual-type components), autoattack split
into `(melee)/(multi attack)/(aoe attack)/(flurry)` (flags `F_AOE`/`F_FLURRY`),
crits on ward/power/threat, threat split into `threat`/`detaunt` kinds, casts
attached to the ability's busiest row (no phantom damage rows), actor
`damage_taken`/`power_drain`/`cure_count`, and actor rows for mobs + Unknown.
API derives `swings` and `to_hit_pct`. `GET /api/encounters/agg?ids=…` sums
any set of one session's encounters into the same payload shape (single-id
fast-path; medians recomputed from events, null when pruned) — it powers
every tree node below.

Per-ACTOR AvgDelay (schema v7, parse v11): `encounter_actor_stats` stores
`atk_swings` (offensive damage events + avoids, self-hits excluded) and
`atk_span_s` (first→last swing); the API derives `avg_delay_s =
span/(swings-1)`, which aggregates exactly across encounters (sum of spans /
sum of gaps). Surfaced in the zone-page Damage tab, Workspace combatant
table, and ComparePanel.

**Workspace UX**: `/sessions/:id` is now one ACT-style page
(`frontend/src/pages/Workspace.jsx` + the first shared components:
`SortableTable`, `EncounterTree`, `ShareBar`, `useQueryState`): left tree
(session **All** root → zone blocks → fights; consecutive trash collapses to
`Trash ×N`), right pane = sortable combatant table → per-actor ability
drilldown (ACT columns: Swings, ToHit, Median, AvgDelay, damage types, kind
filter chips) + a DPS bar strip. Selection lives in the URL (`?sel=` id-list
or `all`, `&actor=`). SessionDetail/Encounter/RaidReport/Coach **pages** are
deleted (`/encounters/:id` redirects); the raid-report API remains and its
engagement/death-cost/overheal numbers merge into the All node as columns.
The coach engine, calibration, and `coach_api` are intact — no UI surface.
Chain-pull labels cap at 4 nameds (`A + B + C +N more`).

## Hardening (phase 6)

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
.venv/bin/python -m pytest backend/tests/ -q     # 188 tests incl. golden fixture
bash restart.sh && curl -s localhost:8450/api/sessions
curl -F "file=@/home/lindsay/bobby.txt" -F "character_name=Bobby" localhost:8450/api/uploads
```
