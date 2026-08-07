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
  exclusive), `groups_api`, `admin_api`, `tokens_api` (the account's ONE readable
  API key — Sonarr-style: `token_plain` beside the hash, `GET` serves it back
  to the owner, `refresh` revokes every live key and mints the replacement),
  `plugin_api` (the committed DLL, served as a zip, unauthenticated).
- `auth.py` (PBKDF2 password + security answer + hashed session/device tokens),
  `groups.py` (membership + the one visibility predicate) and `security.py`
  (deps + ownership/visibility helpers). See "Accounts, groups and sharing".
- `pipeline/live.py` + `routers/ingest_api.py` — live ingest (see below).
- `census/` + `routers/census_api.py` — Census sync (see below);
  `census/catalog.py` populates `ability_catalog` (see "Ability catalog").
- `coach/` + `routers/coach_api.py` — coach engine + raid report (see below).

## Accounts, groups and sharing

### Identity

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

Migrations are guarded by table SHAPE, not `user_version` (the dev reloader can
stamp the version mid-edit). The ones that rebuild a table SQLite can't ALTER
(`_rebuild_users`, `_rebuild_characters`, `_rebuild_sessions`,
`_rebuild_device_tokens`) preserve ids, run with `foreign_keys=OFF` and assert
`foreign_key_check`; each was verified against a copy of the real database
before it shipped.

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
run isn't hidden from that group, OR its uploader connected the guild tag their
character wears to a group you're in (same conditions), OR it has been
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
- **`hide` beats auto-share, `share` beats everything.** Auto-share is the
  useful default; one wipe can still be pulled back out. `set_run_shares` has to
  count EVERY standing branch when it decides where to write a `hide`: it
  deletes only explicit `share` rows, so a read-time branch missing from that
  set survives the delete and the untick silently revokes nothing. There are two
  standing branches — the character's auto-share and the uploader's connected
  guild tag — and a third would belong there too.
- **A standing branch has FOUR query sites**, not one, and the comment above
  `_SHARE_REACHES` lists them: `PERSONAL_RUN_IDS` (who can see it),
  `shares_for_runs` (what the owner's Share control shows), `shared_via_for_runs`
  (why the viewer can see it) and `set_run_shares`'s `auto` set. Missing the
  first is a leak; missing the last revokes nothing. Missing `shares_for_runs`
  looks cosmetic and is not: ShareDialog seeds its save set from that GET, so an
  unreported group is dropped on the next save and the server writes it a `hide`
  — a raid silently unshared by an edit about something else. The reach
  condition itself is written ONCE and aliased per branch, so the four sites
  cannot disagree about what "in window" means.
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

### Sharing is a decision for the account, not the uploader

Every branch above is set on the site by someone signed in. The ACT uploader
(`improvmasta/eq2advanced-act`) sends log lines and has no say in who sees the
result: a device token cannot read a parse back and cannot change its audience.

v11 built the opposite — a `session_shares` table, a `device_tokens.can_share`
scope, `share_groups` on every ingest batch, a sharing panel in ACT — and v12
dropped all of it. Written down because the design was tempting and the reason
it went is not visible in the code that remains: the token lives in a config
file on a gaming PC, and "who can see my raids" should not be answerable from
there. The two site controls cover the ground — the character's standing
auto-share for "always", a raid's own Share control for one night.

Groups: `groups` / `group_members` / `group_invites`. Three ways in, all the
same credential — an invite addressed to a username, the 6-digit join code read
aloud in voice, or an invite **link** (`/join/<code>`, carrying that same code
so there is one thing to rotate). A million codes is small, so `ratelimit` is
the actual security: on joining, and on `GET /api/groups/preview/{code}`, the
unauthenticated route the landing page uses to name the group before the
visitor has an account (deliberately thin — name, description, headcount, "are
you already in it" — and never the roster). The link works signed out:
`pages/JoinGroup.jsx` shows the invitation with sign-up underneath and joins
the moment the account exists. Both rate-limit call sites dedupe their keys —
an anonymous caller's identity *is* their address, and counting one failure
twice would silently halve the budget.

`GET /groups/new-code` hands the create form a free code so the code AND its
link can be shown while the name is still being typed; `POST /groups` claims it
(re-minting only if it was taken in between, and saying which code it got).
Nothing is reserved, so an abandoned form burns nothing.

Membership is all that is stored; roles are owner/admin/member. The two levers
after a code gets out: **rotate** (`/code/rotate`, optionally `enabled: false`
to switch code-joining off) mints a new code and kills the old one and every
link built from it while every current member stays in; **remove**
(`DELETE /groups/{id}/members/{uid}`, owner or group admin, never the owner
themselves) drops that person's access on their next request. Leaving or being
removed also drops that user's auto-shares into the group, so rejoining doesn't
silently reopen everything they had pointed at it.

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

### What the server keeps of a log (`pipeline/redact.py`, schema v24)

EverQuest II writes one log file for everything. `eq2log_<char>.txt` is not a
combat log — it is the client log, and the combat is a minority of it. In the
golden raid (275,822 lines) 1,132 lines are speech, and the split matters:
519 of them are tells, guild chat, officer chat, the named channels
(LFG/General/Auction/Crafting) and local `/say`. That, not anyone's DPS, is the
sensitive content in an upload, and it used to be stored verbatim.

**Application access control cannot solve this.** `groups.py` decides who may
read a parse and does it well, but every check it makes is a check the person
holding the disk can delete. The only durable answer to "who can read my chat"
is that the chat is not there. So redaction happens at INGEST — the upload path
filters the byte stream as it arrives (`StreamRedactor`, holding a partial line
between reads) and the live path filters before writing each chunk. There is no
window in which the unredacted file exists on the server, which also means there
is nothing to go back and clean up, and no "we delete it after N days" to trust.

**Why it cannot change a number.** `classify_body` returns None for every line
starting `\aPC `/`\aNPC ` and for `You say|tell` — chat produces no events. The
redactor governs *exactly* that set and never inspects anything else, so a line
the parser reads is a line redaction cannot touch. The two sets are the same
objects: `redact.py` imports `CHAT_PREFIXES`/`CHAT_RE` from `classify` rather
than restating them, because a copy that drifts is precisely how this would
quietly start eating events. `test_parse_is_identical_with_and_without_chat`
pins the invariant, and the 434-test golden parse is the backstop.

**Allowlist, not denylist.** Inside the governed set a line is dropped unless it
matches a retained channel (group, raid party, NPC dialogue). A denylist would
fail open: a custom channel, or a chat format from a client patch, would be
retained because nobody wrote a rule against it. Default-deny gets it wrong in
the safe direction. The one carve-out is a governed line carrying no quoted
message (`Ellea blesses Spades …` / `Bob Goes Into a Bloodlust!!.`): no typed
text means nothing private, so it stays as fight flavor.

Finding the boundary was empirical rather than assumed. Two things fell out of
reading the real log that guessing would have missed: NPC speech is 320 lines of
scripted boss dialogue that belongs to the encounter and has no privacy
dimension at all, and `_CHAT_RE` matched `^You (?:say|tell) ` **with a space**,
so `You say, "…"` — the logger's own local chat — slipped past the parser's own
chat test. It classified to None anyway further down, so the fix to `\b` changed
no output; it did decide whether eight lines of Lindsay's `/say` were governed.

**`trim_to_fights` is a second pass, after the parse.** Group and raid chat
survives ingest; what survives *this* is the part of it said about a fight
(±`FIGHT_MARGIN_S`, 90s — pull calls land before the first swing and the
post-mortem right after the wipe). It runs post-parse because it needs the
encounter windows to exist. It takes the UNION of windows across every session
sharing those bytes: an upload file is content-addressed and shared between
people who were on the same raid, so trimming to one uploader's fights would cut
chat out from under the others. No parsed session on those bytes means "don't
trim", never "trim everything".

**The content address stays the sha256 of the ORIGINAL bytes.** It is a hash,
not a copy, and it is what makes two raiders' uploads of one night dedupe to a
single file. Hashing the redacted output instead would still work but would
couple the dedupe key to the redaction rules, so a rule change would fork every
stored file. `src_bytes` likewise measures what was sent (it feeds the quota);
`raw_bytes` measures what was stored.

`sessions.redacted_lines` counts what went, and the Import page shows it per
file. That is deliberate: it turns "we strip your chat" from a claim into a
number the uploader can check against their own log.

Logs stored before any of this existed are cleaned by
`backend/tools/redact_existing.py` (one-time, `--dry-run` first, atomic rename
per file, safe to re-run — a redacted file redacts to itself). Until it has run
the Import page's copy is true of new logs and false of old ones.

**What the Import page does NOT claim.** It says an admin account is not a key —
`role='admin'` has no part in any visibility check, and an administrator opening
an unshared raid gets the same 404 a stranger does. It stops there. It does not
claim the operator cannot read the database, because that would be false, and a
privacy promise that overstates itself is worth less than none. What limits the
operator is how little is kept, which is the whole reason the filtering is at
import rather than at display.

## Live ingest — the frozen ACT-DLL contract

`GET /api/ingest/hello`, `POST /api/ingest/batch`, `POST /api/ingest/backfill/done`;
auth is `Authorization: Bearer <device_token>` only. A batch is gzip (or plain)
JSON `{batch_id, mode: live|backfill, lines: [verbatim lines]}` → `{accepted,
duplicates, session_id}`. That is the whole surface a device token reaches — it
sends logs and does nothing else (see "Sharing is a decision for the account").
`backend/tools/simulate_live.py` is the reference client (and feeds the
equivalence test's batch cutter); `improvmasta/eq2advanced-act` is the ACT plugin
that implements it for real, and the two must stay in step.

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
  encounter that can no longer change (a later segment exists, or
  `CLOSE_S = GAP_S + TRAIL_GRACE_S` = 17s of log time passed) to the normal
  tables — that's what the dashboard's SSE cards read. At close (`done`, or 30-min staleness) the whole session is
  **rebuilt from its raw chunks through `parse_session`** — the exact bulk
  path — so a finished live session is provably identical to uploading the
  file (guarded by `test_golden_equivalence`: encounters, actor stats, ability
  stats all byte-equal on bobby.txt; encounter ids change at rebuild, which is
  why the Live page refetches on `status: ready`).
- **Staleness needs a reaper, not just a check** (`live.reap_idle_live_sessions`,
  driven from `main.lifespan` at startup and every 5 minutes). The 30-minute
  rule used to be evaluated only inside `open_live_session` — i.e. when the
  SAME character's NEXT batch arrived. Quit EQ2 and ACT and that never happens:
  the session sits at `receiving` forever, the raid page keeps saying **Live**,
  and because closing is what rebuilds it from raw, it is also out of reach of
  the `PARSE_VERSION` sweep (which only looks at `ready`/`parsing`), so no
  parser improvement can ever land on the night you just raided. The startup
  pass runs BEFORE `_reparse_stale` so an abandoned session is closed and
  reparsed once, at the current version, instead of twice.
- **Restart-safe by construction**: the in-memory tail is disposable; raw
  chunks + `ingest_lines` survive, and the close-time rebuild reparses raw.
- SSE: `GET /api/sessions/{id}/stream` (cookie auth) polls the DB ~1.5s and
  pushes `encounter` cards + `status` heartbeats (incl. uploader-online from
  `device_tokens.last_seen_ts`) + `partial` views of the open fight (below);
  closes at ready/error.

## The raid dashboard (`/live`) and the fight in progress

Everything above reports fights that are OVER. The dashboard is the second
monitor during a raid, so it needed the other thing — the pull that is
happening — and that is `pipeline/livemeter.py`.

**It is a view, and being a view is the whole design.** `_flush` already
computes the open segment and drops it; the dashboard's snapshot is built from
exactly those events, handed to the SSE stream as a `partial`, and stored
nowhere. No DB writes, no entity resolution, no encounter row. That is what
keeps `test_golden_equivalence` true: the record is still the record, and this
is a photograph of it mid-fight. The consequences are stated in the payload
rather than hidden — the fight's name is `provisional_*` until it closes and
arrives again as an `encounter` card, and credit is by NAME (a pet credits its
owner through the subject it already carries) because resolution is the
expensive half of a flush.

What it measures is deliberately the same statistic the recorded page reports:
self-inflicted damage is excluded exactly as `roll_encounter` excludes it, DPS
divides by the fight's elapsed clock, and overheal is the same HP-deficit
reconstruction. A live meter that used different arithmetic would visibly
disagree with itself thirty seconds later.

Three gates decide whether a snapshot is built at all, and all three are about
not showing a raid that is not happening:

- **Nobody watching, nothing built.** `mark_watched(session_id)` is called by
  each stream poll; without a dashboard open the raid pays nothing.
- **`mode=backfill` is history**, by the plugin's own word for it — an old log
  being caught up must not flash on screen as a pull in progress.
- **`LIVE_LAG_S`** catches the same thing from the other side: log time far
  behind the clock is a replay, which is what `simulate_live.py` does *without*
  `--restamp` (that flag exists so an old log can be replayed as a raid
  happening now, and it is how this is tested by hand).

Reading it from the SSE generator is an **RCU-style pointer swap**: the
producer (a worker thread, under the per-session lock) builds a fresh dict and
assigns the attribute; readers on the event loop take the reference and treat
it as frozen. One atomic store, no reader lock, no copies.

**Live AoE timers** reuse `pipeline/aoes.py`'s definition — a second in which
one enemy ability touched `MIN_TARGETS` players is a cast — importing its
constants rather than restating them, so the live rule and the recorded rule
cannot drift apart. Two differences, both deliberate. Nothing filters on name
grammar, because that would drop exactly the bosses worth a countdown (live,
`Venekor` is indistinguishable from a raider by name; the anchor rule is the
real evidence — a raider's green AE hits mobs, so touching five RAIDERS in one
second is a claim only an enemy ability can make). And a sourceless
`X is hit by <Effect>` counts, pooled under `Unknown` the way the recorded tab
pools it: bobby.txt's `Stench of Death` reaches 17 people on a 30s reported
timer, and dropping it would hide the biggest thing on the screen. Only casts
inside the CURRENT fight feed an observed period — the wait between two pulls
is a raid taking a break — so a boss's first cast counts down only when ACT's
reported-timer list knows the ability, and a single cast with no timer at all
is not shown, because a row that can only say "that happened" is noise.

Cost: one pure-Python pass over the open fight per batch. Measured against the
biggest fight in bobby.txt — 46,521 events over 408 seconds — that is 65ms,
with a `SNAPSHOT_MIN_S` floor so a client sending faster than the stream polls
cannot spin on it.

### Notes are keyed by zone and named, never by encounter (schema v28)

The dashboard's right column files what you write mid-raid. On trash it belongs
to the ZONE (`mob_name IS NULL`), on a named it belongs to that boss, and the
client decides which — it is the thing that knows what is on screen, and a
server reading it back off encounter rows would be reconstructing an answer it
was already told.

The key is `(user_id, zone, mob_name)` because **encounter ids do not survive**:
a live session is rebuilt from raw when it closes and every id in it changes, so
a note that identified itself by one would lose its subject overnight.
`encounter_id`/`zone_run_id` are stored as provenance and are never joined for
identity. Keying on the boss instead means tonight's note lands beside every
other attempt on that boss, which is the pile this exists to grow into — an
outline of the zone, written one pull at a time (`GET /api/notes/outline`).

Notes are private to whoever wrote them, with no group predicate, exactly as
imported parses are: `groups.py` owns the one visibility rule and does not get a
weaker sibling. Screenshots are stored the way parse shots are — re-encoded to
WebP under `NOTESHOTS_DIR`, never the uploaded bytes, served by an owner-checked
endpoint rather than a static mount.

### The stream overlay is a capability in a URL (schema v29)

`/overlay/<token>` is a page for an OBS browser source, and that decides the
design: a browser source carries no cookies and `EventSource` cannot set a
header, so the token has to ride in the path. Taking that seriously means the
token is deliberately narrow — it reaches the in-flight meter for whichever of
that account's characters is streaming right now, and nothing else. No session
ids, no fight cards, no history, no account name; a URL that ends up in a VOD
must not be a way into anybody's parses (`test_overlay_api.py` asserts the
absence of each field).

Two doors, one read surface: the overlay stream and the session stream both
read `live.live_snapshot`, rather than one endpoint branching on how the caller
authenticated — a generator that decides authorization halfway through is one
nobody can audit. The session it points at is re-resolved every tick, so
switching to an alt mid-stream follows on its own, and the stream stays OPEN
with `{"live": false}` when nothing is running: an OBS source is opened once
and left for hours, so a stream that ended between raids would be a scene that
goes blank for good. Revoking is a row update because a URL already out in the
world has to be killable without changing anything else.

The page renders BEFORE the app shell (`App.jsx` branches on the path): nav,
theme toggle and account icon are furniture on somebody's stream. `transparent`
is the default theme and means the document paints nothing at all — html and
body included — because OBS composites the page over the game, and a background
there is not a style choice, it is a rectangle over the raid.

## Census sync

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
  period); anything unrecognized is kept verbatim as kind `other`, so the
  coach can only under-use a spell, never misread it. Typed spell fields
  (`cast_secs_hundredths`, `recast_secs`, `duration.*_sec_tenths`) are
  reliable — EXCEPT `recovery_secs_tenths`, which stores HUNDREDTHS despite
  the name (every spell carries 50 = the universal 0.5s recovery; dividing by
  10 gave 5s and clamped idle% to 0 — found on a real raid night).
- The character doc's typed stats carry everything coaching needs (verified on
  Bobby: `combat.abilitymod` 1442, `basemodifier` 68.1, `critchance` 53.5,
  `ability.spelltimereusepct`/`spelltimecastpct`) — no text parsing there.
- Tests (`test_census.py`) run entirely from recorded fixtures in
  `tests/fixtures/census/` (trimmed real responses for Bobby) via a fake
  injected as `census.client._shared` — no live Census calls in CI.

### Bulk spell ingest

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

## Coach engine

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

### Coach correctness — the five rules v1 was missing

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
6. **Engagement classifier** (`raidreport`): catalog-proc abilities never
   anchor; inside the opening 2s an ability that fires ≤1s after being hit is
   a reactive proc (skipped); the logger's own prepare line is an exact
   high-confidence anchor (`anchor: cast`); the remainder keeps the
   low-confidence flag.

Still open: rez/time-dead next-action proxies, the DoT tier-upgrade tick tail,
reuse-marginal rotation displacement — and the abmod marginal, which is only as
good as its calibration points. Lindsay still needs to RUN the two dummy parses.

## Raid Report

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

### Naming: the enemy fought, not the enemy that died

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

**The rule is only as good as the mob/player split feeding it.** On 2026-08-04
every Wuoshi pull came out titled "Ancient Grovebeast" — the adds — because
Wuoshi was classified a PLAYER and so was invisible to a rule that ranks mobs.
It took 5–9× the adds' damage in all five pulls (72M vs 8M on the kill), so the
title was never a naming question; see the self-heal veto below. Two other
things went wrong with it, and they are the ones to check when a title looks
off: the boss sat in the raider table at #17 damage, and `success` tracked the
ADDS, so a night of four wipes and one kill recorded three kills and two wipes.

### A segment is only a FIGHT if the raid engaged it

`success` is 0/1/**NULL**, and the rail marks only `success === 0`, so NULL
reads exactly like a kill. On 2026-08-04's Emerald Halls that put three
non-fights and two wipes in the list wearing the same face as a boss kill, and
the run header said 11 named pulls where there were 8.

The segmenter cuts on silence, so a raid night also produces segments nobody
fought: the last pull's DoT ticking on three people 13s before the next one, or
a proc pet touching the boss and being one-shot. `encounter_label` now requires
damage into a mob from `_ENGAGE_KINDS` (`player`, `own_pet`, `named_pet`) — a
**swarm pet is a proc, not a decision**. Without it the segment keeps its name
(ACT's stubs are titled `Encounter`; ours stay readable) but is `is_named`
False, `success` NULL, and falls into the Trash group. This is the same set ACT
drops to reach 61 encounters, noted below.

The exception is why `success` cannot just be NULL whenever the raid dealt no
damage: **a wipe where the boss AoEs everyone down before a single hit lands is
an attempt**, and the most emphatic kind of failure. That night had two — 24
and 17 dead to `Nature's Fury`, zero damage dealt — and both rendered clean.
Ally deaths decide it: dead raiders mean the raid was there and lost.

The rail says the outcome in the fight's own name — green killed, red lost,
each with its own mark, because red/green is the one pair a colourblind reader
loses. Trash stays muted whatever happened to it: fifteen green totem rows
drown the two lines that matter. ACT's own colouring is NOT this rule (its
Wuoshi kill and its Wuoshi wipes are the same colour); green-for-killed is
ours, at Lindsay's ask.

Known residual: ACT cut that night into 61 encounters and we make 60. ACT
split Galiel's two pulls at a **5s** gap — the only gap ≥3s in our merged 499s
segment — but no single threshold reproduces its set: at 5s we make 63 (two
extra splits ACT also makes, so ACT must additionally drop the two segments
where no enemy was ever damaged), at 6s we make 61 but split the wrong fight.
The plugin itself does not decide this — `ACT_English_Parser.cs` only calls
`SetEncounter(time, attacker, victim)` and its kill-ends-encounter branch is
commented out, so the boundaries are ACT core's inactivity timer.

## Zone runs — the navigation model

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

Frontend: `/` = `Home.jsx` (one sortable table of raids), `/zones/:id` =
`ZoneRun.jsx` (fight rail + tabs Damage/Healing/Defense/AoEs/Timeline/Insights;
`?sel`/`?actor`/`?tab`/`?cmp` all URL state), right-hand `ActorPanel`
(per-actor drilldown) or `ComparePanel` (checkbox multi-select, per-metric
grouped bars from `lib/stats.js`). `/import` = the import hub (plugin, API key,
uploader, imported logs), with `/uploads` redirecting to it; `/sessions/:id`
(Workspace) survives as the per-file debug view.

### The roster, and what counts as a raid

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

### The fight rail

**The rail's head is the raid page's title block, and the only one.** The page
used to carry a `.pagehead` above the tables — zone name, date, time range,
character, sharing badges, the parse picker, Share and Compare — while the rail
printed the zone name again a few pixels to its left. Worse, that head was
hidden the moment a drilldown or a comparison opened (`!panelOpen`), so the
buttons on it came and went with the panel. The block that survives every view
is the one that gets to be the title: `EncounterTree` takes `sub` (date · time
range · character, one small line, ellipsised, with the long date on hover),
`actions` (badges, parse picker, Share, Compare) and `titled` (render the zone
name as the page's `h1` — `/sessions/:id` keeps its own head, so it passes
`titled` off and the page still has exactly one `h1`). `.wsmain > :first-child`
zeroes its top margin so the stat strip lines up with the rail beside it.

The selected FIGHT no longer gets a title of its own. The rail's active row
already says which one it is, and its footer says how many are counted.

Two things share that head and are not the same kind of thing. The guild rides
on the `sub` line, right of the character whose parse this is (`subTag`, which
keeps its width while the caption ellipsises) — it describes the night. The
`actions` row below carries the sharing badges and the parse picker, and ends
with Compare pushed to the far right (`.endact`), because it is the one control
there that DOES something. Compare also wears `.btn.solid` — a filled gold
button — everywhere it appears (rail, drilldown, the raid list's head-to-head
card): the default button is a gold outline with a gold wash, which is pixel for
pixel what every *on* toggle in the app wears (`.chip.on`, `.chip.toggle.big.on`,
`.listtools .chip.on`), so beside a row of badges it read as a switch somebody
had already flipped.

**The drilldown opens on the tab you were reading.** The page tabs and a parse's
kind tabs are the same question at two scales, so `PANEL_KIND` (`ZoneRun.jsx`)
translates Damage→Damage and Healing→Heals and hands it to `ActorPanel` /
`ComparePanel` as their starting tab; clicking through six raiders on Healing
used to cost a second click each. Only the page tabs with a per-ability view map
— from Defense, Deaths, AoEs, Timeline or Insights the panel keeps whatever it
is on — and the panel's own tabs still win until the page tab moves again.

`components/EncounterTree.jsx` is the zone page's navigation AND its scope
control, so the two gestures are kept distinct: **clicking** a fight (or the
root, a zone block, a `Trash ×N` group) makes it the only selection; **ticking**
its checkbox adds or removes it from the current one, which is how several pulls
merge into one set of combined stats. The boxes always show what is currently
counted, so the root row visibly means all sixty fights. Selection stays in
`?sel=` (an id list, or absent for all), so a merged set is a shareable URL;
selecting everything collapses back to `all` rather than a 60-id query string.

Rows are three fixed columns — checkbox, `9:35p` start, name — with the length
right-aligned as `m:ss` in tabular figures. `Trash ×N` groups keep their
twisty, and a group checkbox ticks the whole group (indeterminate when
partial).

**It is a tree with one root, not a list whose first item is special.** The root
is **Zonewide** on `/zones/:id` and `All fights` on `/sessions/:id` — a zone run
is one zone by construction, a session file can span several, and the label may
not claim more than it covers. Everything below hangs off it down an indented
spine, and the root is sticky so the total stays on screen to read every row
against. Two corollaries the layout depends on: the run's length is on the root
row, in the same column every fight's length is read down (the head carries the
count only), and the footer is always present once the rail is selectable —
appearing on the first tick shoved the list up under the cursor.

**One "on" for the whole panel.** Row checkboxes were the browser's default, the
`All` chip was rarity-blue and the wipes switch was gold — three ways to say
the same thing in a 40px strip. The checkbox is now drawn in the switch's own
track colors, and the strip separates its two KINDS of control: presets on the
left (what is selected), counting rules on the right of a hairline (what the
numbers mean). The `All` chip is gone; it duplicated the root row.

Repeated nameds carry an **attempt number** (`#3`) rendered outside the
ellipsis. Seven pulls at one boss otherwise render as seven rows of
`Vampire Lord Mayong Mist…` — the truncated part is the part that identifies
them, and the number is the only thing on the row that survives.

**Wipes are counted by default** — ACT counts them,
and a night with two Galiel wipes IS a night with two Galiel wipes. The rail's
switch (`?wipes=0`) takes them out of every total while leaving them listed and
dimmed, and the page head then says how many were left out. Selecting a wipe on
purpose always shows it — the filter can never empty the page.

**There is no Nameds switch** (removed 2026-08-06). The switches exist to take
things OUT of the count, and nobody reads a raid night with the bosses removed
— that one was there to be left on. `named` is still a KIND (`KIND_OF`, the row
colouring, the attempt numbers); it just has no switch, which leaves Trash and
Wipes, packed left rather than spread across the rail.

**The head is three blocks, and each answers one question.** It carried four
kinds of thing in one undifferentiated stack until 2026-08-06, and the failure
was legible:
`Skill Issue` (the guild Census voted the roster into) sat one line above
`Skill Issue (Temp)` (a sharing group) as two pills of nearly the same shape,
`＋ Share` was a verb in a row of nouns, and `Done`/`Compare` right-aligned at
the bottom read as a page footer — so the `THIS RAID …` bar that Edit opened
*underneath* them read as a new section rather than as the button's own effect.
You had to already know where to look.

| block | question | contents |
|-------|----------|----------|
| `.railhead` | what was this night? | zone (h1), date · time range, then one line of character + guild + Live + `shared` + parse picker with `N fights · N hidden` right-aligned on it |
| `.railsec.seen` | who can see it? | `SHARING` + filled `.sharepill`s (Public in blue, group names in gold) + a square gold `＋` |
| `.railsec.acts` | what can I do to it? | Compare (left), Edit (right) — no label: a row of buttons says what it is, where pills alone do not |

Two rules come out of that split. **A pill's fill says what kind of claim it
is**: outlined is a fact somebody else established (the guild tag, Live,
`shared`), filled gold is a decision the owner made (a share). That was already
the app's rule everywhere else — `.badge.guild` is deliberately quiet *because*
gold means a sharing group — and the head is where it was being broken. Inside
that row `Public` is filled BLUE rather than shouted in caps: it is a different
kind of reach (people you never named), and colour says so without making one
pill louder than the rest. And
**the count moved off the title's baseline**, where a long zone name wrapped to
two lines and then got crowded by it.

`Click to focus · tick to combine` is **gone**. It taught the two gestures once
and then sat there forever; the space is worth more as the raid's own controls.

**The share controls open where you clicked them.** `＋` is a square gold
button at the end of the pills — square is what makes it read as a control
rather than a pill that lost its label, so it is sized with `aspect-ratio` and
centred with flex rather than by two hand-picked pixel values (they were 26 ×
24, which is exactly the bug you cannot name but can see). It is a toggle — the
same button closes what it opened — and `ShareDialog` renders inside the
`SHARING` section, under the pills it edits, not as a card at the top of
`.wsmain` (a
different part of the screen from the one you were looking at, and often not on
it at all). It is capped at `42vh` with its own scroll, because the rail is a
fixed-height column and somebody in ten groups must not push the fight list off
the bottom of it.

**Edit mode is a state of the rail, not a screen you go to.** `✎ Edit` sits at
the RIGHT end of the `RAID` row with Compare holding the left, owner only.
Pressing it replaces the row with one right-packed cluster (`.editopts`, a
max-width reveal that degrades to a cross-fade) — `⊘ Hide`, `🗑 Delete`,
`✓ Done` — with Done in the spot Edit was, so the button that opened the
options is the button that closes them. The options grow leftward into the gap
because there is nothing to the right of Done in a 300px rail.

**Compare is not in the edit row, and `.editopts` is `nowrap`.** Both are the
same lesson. Four labelled buttons do not fit across this rail; the first
attempt kept Compare and let the row wrap, and what fell off the end was Done,
onto a second line at the far left — which is exactly the button-hunting this
layout exists to stop. So the cluster carries only what editing needs, the
labels lose the redundant "raid" (the section is called `RAID`), and if it ever
has to break it breaks as a UNIT with Done still attached. Compare comes back
with Done.

Edit also tints the whole column (`.rail.editing`, plus an amber rule drawn
*over* the children in a `::before` — the tint is deliberately faint, so an
inset shadow would sit under every panel background) and puts the same two
verbs on every row below. Nothing else moves: the fights, their checkboxes and
the drilldown all keep working, because deciding a pull does not belong is
something you do WHILE reading the parse that told you so.

**The row buttons are drawn at full strength**, not as ghosts that appear on
hover. Edit mode is a state you deliberately entered, and its whole point is
the controls it added; a mode that can delete a night must not make you hunt
for them. (The raid LIST is the other way round — see below — because there
the pencil is on sixty rows of a page people mostly read.)

**Delete confirms in place.** Clicking `🗑` turns that row's controls into
`Yes` / `✕`, and only that row's — one `confirm` key in `EncounterTree`, one
`rowConfirm` id on the raid list, so a second row can never be armed at the
same time. A dialog would have covered the fight it was asking about; the
second click keeps it under the cursor. Hiding needs no confirmation because
hiding is the undo.

**A hidden fight stays in the rail, struck through, with the switch that puts
it back** — this is the only place its owner can reach it, since it is out of
every payload anyone else gets. It is out of everything the rail COUNTS,
though: the head's fight count (which says `+N hidden` beside it), the kind
switches, the group checkboxes, the footer's total, and the selection ZoneRun
hands to `/encounters/agg`. `ZoneRun` keeps the raw payload in `allEncounters`
and derives `encounters` (the parse) from it; the rail is the one component
that sees both.

**Pets and NPCs are two switches in the filter bar**, immediately right of the
role chips, on every parse tab (Damage / Healing / Defense / Deaths) and off by
default. Who may have a row and what a row must CARRY to earn one are separate
questions: `kindAllowed` answers the first, the per-tab predicate the second
(`ZoneRun.jsx`, `rowsFor`). Both off, every tab is the raid.

They lived on Defense alone until 2026-08-06, on the reasoning that a
non-raider row carries nothing but DmgTaken. That is true of an OWNED pet and
false of a mob. A pet's damage is credited to its owner (`statsroll.actor_key`,
ACT does the same), so `Tragedy's unswerving hammer` really is a paladin hammer
pet with a DmgTaken figure and nothing else — but a mob keeps its own credit
(`_OWN_ROW_KINDS`), so the boss row has real damage, real DPS, its self-heals
and everything the raid put into it. Hiding it from the Damage tab hid a parse
people ask for, and clicking the row opens that parse in the panel like any
raider's (mob rows have no checkbox, so it is a plain drilldown — see the
compare-checkbox section).

What a mob does NOT get is a share of a raid denominator (`Dmg %` is a share of
RAID damage) or a rank color (`rankPool` returns null for anything that is not
a player, so a boss can never sit in a tank's peer group). Those cells stay
blank on its row rather than answering a question nobody asked. Deaths are the
one tab where the NPC switch adds nothing and that is honest, not a bug: a
death is credited to a player or their pet only (`rollup` is NULL for a mob),
which is the ACT residual already recorded at the end of CLAUDE.md.

### The Deaths tab is two columns (`TankDeaths.jsx` + `DeathList.jsx`)

Two different questions were sharing one full-width table. *How did the tank
die* is answered by ONE death in detail; *who died tonight* is answered by all
of them in a list. The list was eating the page's whole width to answer the
second question badly and the first one not at all, so it now sits on the
right of a grid with a tank report on its left (`.deathcols.two`, one column
under 1150px or with a drilldown open).

**The page decides the layout, not the grid.** `.two` is added only when
`hasTankDeath` says there is a tank death to put in the left column — a first
grid child that renders `null` would drop the death list into the narrow
column and leave the wide one empty, so the question has to be answered before
the grid is drawn, by the same predicate the component itself filters on.

**The tank report is the two curves, at three resolutions.** A tank's death is
a failure of what was coming in against what was going out to meet it, and the
thing every raid argues about afterwards is whether the heals were there. So
each tank death gets the same question answered three ways: a **fact line**
(took, healed), a **ledger** of one row per second, and a **log** of every
event. Tanks are `roleOf(actor) === 'tank'`, which is all six FIGHTERS
including brawlers.

Both tables lead with the WALL CLOCK (`fmt.clockS`, the same readout without
the AM/PM the card already established), not with a `−5s` countdown: the rows
are consecutive seconds of a real night and the log everyone cross-checks
against is stamped the same way.

**`Net` is the ledger's whole point** — healed minus took, per second, red when
they lost ground. Took and Healed side by side are two columns the reader has
to subtract in their head every row; Net is the answer to the only question
being asked. Every second of the window renders whether or not anything landed
in it, because a ledger that skipped its empty seconds read as a list of events
rather than a countdown, and a two-second hole where nobody healed him — the
thing that killed him — rendered as no row at all.

**FIVE seconds for the tank, THREE for the raid list, ONE request.** A tank
dies to a spike and the spike is over in two or three seconds; twelve seconds
of context buried the moment under the rest of the pull. The fetch asks for the
wider of the two (`TANK_WINDOW_S`) and `DeathList.clip` narrows to
`RAID_WINDOW_S` in the browser, which is EXACT rather than approximate:
`_window_slice` caps each list at `DEATH_MAX_ENTRIES` and keeps the TAIL, so
the last 3s of a 5s window is complete even when the 5s list was truncated —
which is why `clip` only carries the truncation flag over when nothing was
actually cut. Two requests would have fetched the same events twice.

**There are no tenths of a second in an EQ2 log.** Every line is stamped
`(1785630623)[Sat Aug  1 20:30:23 2026]` — whole epoch seconds — so `events.ts`
is an INTEGER and the per-second buckets are the log's own resolution rather
than a resampling of something finer. Within one second, line order survives as
`events.seq`, which is why each `/deaths` list is ordered `(ts, seq)` and the
blow by blow can only claim ordering within a SIDE: the payload carries no
`seq`, so a hit and a heal in the same second cannot be interleaved against
each other. Nothing on this tab may print a tenth it cannot measure — the
recap's old `0.0s` column was formatting precision it never had.

**A class chip may abbreviate where the width is not there.** `classShort`
(`lib/classes.js`) is the name a raider says out loud — SK, Necro, Wiz, Troub,
Illy, Conj, Brig, Swash — and `ActorName`/`ClassChip` take a `short` prop that
the tank picker and the death list pass. `Shadowknight` alone is wider than the
name it captions in a 380px column. Only classes with real in-game shorthand
are in the map; anything else keeps its full name rather than being truncated
into something nobody says, and the chip's tooltip (`classTitle`) always spells
it out in full either way.

**No charts anywhere in this tab.** The recap's per-row bars are gone — they
were a chart of a column of numbers printed directly beside them, and in a
column that now shares the page they cost more width than the amounts they
illustrated. Direction survives as the sign and the row color, which is all
the bar encoded. The recap's four stat tiles became one `.factline` for the
same reason: same numbers, a quarter of the height.

### Every death, by fight and by MOMENT (`components/DeathList.jsx`)

The Deaths tab's lower half was a flat table of every death in the selection,
and it was wrong in three ways that all have the same cause — it rendered the
API's list rather than the thing the list describes.

**Fights are separated.** A night's deaths run together otherwise, and a raid
that wiped twice on one named and lost a healer on trash reads as one
undifferentiated column of names. Each fight gets a `grouphead` band carrying
its name, when its first death landed and how many it had.

**The clock runs to the second** (`fmt.timeS`). An EQ2 log stamps to the
second and a wipe happens well inside one minute, so `9:41p` is exactly the
resolution at which four deaths to one AoE stop looking like four unrelated
events.

**Deaths within `CLUSTER_S` (5s) of each other are one MOMENT.** A wipe used to
spend twenty-four rows saying one thing. The moment row says `6 players`, opens
on a twisty into the individual deaths, and is captioned with what killed them
— `commonBlow` reads the LAST entry of each `incoming` list (ordered by
`(ts, seq)`, and a truncated list keeps its tail, so the killing blow always
survives the cap) and only speaks when the log agrees: one source and one
ability gives `Cataclysmic Slam — Overking Ohrmzz`, one source and several
abilities gives the mob and a count, and neither gives `N sources`. A moment
where some deaths carry no incoming events at all is marked `*`, because the
caption is then a claim about the others. Five seconds is half a cast bar: two
deaths that far apart are one AoE landing, ten seconds apart are two separate
problems and folding those together would hide the second.

**`Took` and `Healed` name their window in the column head** (`.colsub`, "last
12s"). They were `Damage taken` / `Healing` — true numbers over an interval the
header never mentioned, which is not a number anyone can read.

**The recap opens inside the row it belongs to.** It used to render as its own
card at the foot of the page, which on a bad night put it under a thousand
lines of list; `DeathRecap` grew an `inline` dress (no card, no ✕, a gold spine
down the left) that the expanded row hosts in a full-width cell. The standalone
card is unchanged for anything that opens a recap on its own.

What is expanded is indexed into the CURRENT list of deaths, so `DeathList`
carries `key={deaths:${sel}}` — a new fight selection starts it closed instead
of leaving a recap open on whatever death has since moved into that index.

### Read caches

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

### Hand edits to the raid list (schema v8, hiding v26)

Segmentation is a guess, so the list is editable: delete a raid or a fight,
merge runs the game logged as two visits, unmerge them again. The hard part is
that a reparse DROPS AND RECREATES every encounter row — an edit keyed by
encounter id would silently evaporate on the next backfill. So `run_edits`
keys by **fingerprint** — `<started_ts>|<zone>|<name>`, the dedupe key minus
`ended_ts`, which every duplicate copy of a fight shares — with four kinds:

| kind | meaning | written by |
|------|---------|------------|
| `delete` | this fight is gone, for its owner too | `POST /api/encounters/delete`, `DELETE /api/zone-runs/{id}` |
| `hide` | the owner's alone: nobody else's payload, nobody's totals | `POST /api/encounters/hide`, `POST /api/zone-runs/{id}/hide` |
| `join` | never start a run here (merge) | `POST /api/zone-runs/merge` |
| `break` | always start a run here (unmerge/split) | `POST /api/zone-runs/{id}/split` |

`rebuild_zone_runs` is the only writer of run membership, so every edit is
applied by re-running it: deletes re-stamp `encounters.deleted_ts` (a derived
mark — `run_edits` is the truth) and drop out before dedupe, and `_segment`
consults breaks/joins at each boundary. `POST /api/encounters/restore` removes
delete rows (the Undo on Home), `POST /api/zone-runs/{id}/unmerge` removes the
joins inside one run, and the run list carries `merged` so the UI only offers
Unmerge where there is something to undo.

**Hide is not a soft delete.** Delete says the pull never happened; hide says it
is not the raid's business — a wipe on the way out, a guild-bank pull, the hour
after the raid broke up. Three consequences, and each one is a place the code
has to say it:

- **It still segments.** A hidden fight stays in the encounter stream
  `_segment` reads. Dropping it would split a night in two at a forty-minute
  gap that exists only because somebody hid the pull spanning it.
- **It stops counting**, for the owner as much as for anyone else.
  `encounter_count` is the VISIBLE fight count and `hidden_count` carries the
  rest; `named_count`, `success_count` and `combat_s` are taken over the shown
  fights, as are the roster and therefore the majority-vote guild tag. The run's
  WINDOW too — a night with its last two pulls hidden must not still claim to
  have run until midnight. `_spark` filters `hidden_ts` for the same reason its
  denominator did, and `/zone-runs/{id}/report` excludes them outright.
  **The exception is a run with nothing shown at all**, which keeps the whole
  night's window and roster (`described = counted or members`). Those two fields
  say what KIND of night it was, and `raider_count` is what partitions Raids
  from Solo/Group in the list (`lib/raids.js`) — blanking it moved a hidden
  24-man raid across a filter that is on by default, so the raid vanished off
  its OWNER's list and the switch that un-hides it became unreachable. Hiding a
  raid must never make it hard to un-hide.
- **It is a visibility rule, and it lives beside the sharing one rather than
  inside it.** `groups.VISIBLE_UNHIDDEN_RUN_IDS` wraps `VISIBLE_RUN_IDS`;
  keeping the two separate is what leaves that predicate the single auditable
  statement of the sharing rule (see its four traps above). A run whose
  `encounter_count` is 0 with `hidden_count > 0` is a raid hidden whole, and it
  leaves every list, detail and report a non-owner can reach. Per FIGHT, the
  choke point is `security.visible_encounters`: a hidden encounter is refused
  to everyone but the owner, because "not in the payload we sent" is not an
  access rule — the ids are sequential and a viewer can guess a neighbour's.

Hiding is reversible from the same control that set it (`{"hidden": false}`),
which is the whole reason it exists as a separate kind: un-hiding must never be
able to resurrect something deleted.

`DELETE /api/sessions/{id}` is the only thing that destroys data: derived rows,
ingest bookkeeping, frozen reports, and the raw bytes (content-addressed, so
the file goes only with the last session pointing at it). It also drops the
`run_edits` whose fingerprints no longer match any surviving encounter —
otherwise re-uploading the same log would come back with every deleted fight
still hidden and nothing on screen to explain why. Home surfaces this as: all
fights deleted -> "this log has nothing left in it, delete it too?"

## The raid list as a list

- **`raid_dps`**: player damage over the run's `combat_s`, from the same
  grouped query that builds the sparkline (`_spark` returns both). It replaced
  "Peak DPS", which ranked nights by their single best pull. **Named is gone
  from the UI** — `named_count` is still written, but is not trusted enough to
  print.
- **`shared_via`** (`groups.shared_via_for_runs`): the VIEWER's mirror of
  `shares_for_runs` — which of *your* groups reach somebody else's raid, by the
  same three-way rule. Both carry `group_id`, because the list filters by group
  and a name is not a handle. A viewer still learns nothing about who ELSE can
  see a raid: `shared_with` stays owner-only.
- **Grouping follows the sort**: `SortableTable groupBy` takes an ARRAY of defs
  and draws whichever matches the active sort column — nights under a date
  sort, zones under a zone sort, nothing otherwise.
- **Selection lives on the sticky toolbar line** (`.listtools`), not in a card
  above the table and not in a bar pinned to the bottom. A selection with
  nothing you own says "shared with you — read only" rather than showing an
  empty row of buttons.
- **One raid is edited from its own row**, without checking anything first: a
  pencil in the last column, on your rows only, opening Hide and Delete
  SIDEWAYS beside it (`.rowedits`, a max-width reveal that degrades to a
  cross-fade under `prefers-reduced-motion`). Sideways because a menu dropping
  out of a table cell covers the raids underneath, and the row you are editing
  is the line you are pointing at. Delete arms in place, exactly as in the
  rail. The bulk versions on `.listtools` are unchanged — this is the same two
  verbs at the scale people actually use them. A raid hidden whole wears a
  `hidden` badge; nobody else's list can carry one, because for everybody else
  the row is not there. The PENCIL is quiet until its row is hovered and what
  it opens is not (`.rowedit > .ebtn` vs `.rowedits .ebtn` — a child selector,
  deliberately): sixty lit pencils would be noise, but the controls of a row
  already in edit mode are the point.
- **Two comparisons**: `RaidCompare` (list-row numbers, opens beside the table
  in `.cmpcol`; the list drops Timeline/Combat/Raiders to make room) answers
  "which night was bigger" from what the list already knows; its "Compare
  parses" button hands the checked raids to `/compare` (below), which is the
  deep answer. A `RaidParseCompare` MODAL used to be that answer — raid columns
  only, unshareable — and was folded into the page; don't rebuild it.
- **The filters are two questions.** SIZE — `Raids` and `Solo/Group` as
  independent toggles that PARTITION the list, so a third "All" button was a
  synonym for both on. SOURCE — `components/SourceFilter.jsx`, one menu of
  ticks in three sections (your characters, groups, published), OR'd, empty
  meaning everything. Keys are `char:<id>` / `group:<id>` / `public`; the page
  always fetches `scope=all` and narrows in the browser, so flipping a tick
  never refetches.

  Three earlier attempts at SOURCE were worse and are worth not repeating.
  (1) All / Mine / Shared-with-me chips — Mine was a filter that never
  filtered, since your own raids are always listed. (2) A Shared-with-me switch
  beside a group filter: two controls on ONE axis, and the switch silently
  changed what a group pill MEANT, because a group says "I sent it here" on
  your own raid and "it reached me through here" on somebody else's. (3) The
  same sources as always-visible pills — honest, but a toolbar of proper nouns
  competing with the mode chips beside them.

## Auto-share carries raids only (schema v16)

`character_shares.raids_only`. "Share my raids with the guild" is not a request
to broadcast every six-man zone, and the two readings cost differently: opting
in is one tick, noticing you have been leaking is luck. New shares are written
with `raids_only = 1`; the migration gives EXISTING rows 0 — their pre-v16
meaning — because a migration must never revoke access somebody already has.

The read-time rule is one definition, `groups.AUTO_SHARE_REACHES` (since_ts
window AND size AND no `hide`), interpolated into all four query sites:
`PERSONAL_RUN_IDS`, `shares_for_runs`, `shared_via_for_runs`, and
`set_run_shares`' `auto` set. That last one is the trap: a share that does NOT
reach a run must be unticked with a plain delete and never a `hide`, or the row
lingers and blocks a later opt-in. `RAID_MIN_RAIDERS = 7` now lives in
`groups.py` — the same line the raid list draws in the UI.

`PUT /characters/{id}/shares` takes `group_content` in its `shares` form; the
bare `group_ids` form keeps its pre-v16 meaning (everything, past included), so
it stays a true legacy shim. Pinned by
`test_auto_share_carries_raids_only_by_default`, which uploads a real eight-man
roster and a solo zone in one log and checks that exactly one of them arrives.

## Sharing by guild tag (schema v21)

`guild_shares`. The second standing branch, and the one that survives an alt:
a user connects a guild tag one of their characters wears to a group they are
in, and their uploads from any character wearing it flow there. Without it,
every new character is a new rule to remember on a page nobody visits twice.

It is a **per-USER** rule matched on the **uploader's character's** Census guild
(`roster_classes`), and three things about that sentence are load-bearing:

- **Not the run's `guild` tag.** That tag (schema v20) is a majority vote of the
  whole roster — a derived property of the night, and often somebody else's
  guild. Sharing is a decision a person makes about their own uploads, so it is
  matched on who uploaded it, not on who showed up.
- **Not a group-manager power.** `PUT /groups/{id}/guild-shares` is
  member-gated, not manage-gated: it says "share MY uploads", the same trust
  level as a character's auto-share. A group's owner never gains a say over
  anybody's raids, and a viewer still cannot re-share (`owned_zone_run`).
- **`guild_checked = 1` only.** The same tri-state abstention the raid tag
  makes: 0 means nobody has asked Census yet, and a share that fired on it would
  leak on the strength of a backfill that hasn't run — or go missing for as long
  as the queue is long. `test_unchecked_guild_abstains` pins both directions.

The reach condition is `AUTO_SHARE_REACHES` with a different alias
(`_SHARE_REACHES('gs')`), so window/size/`hide` cannot drift between the two
standing branches, and the branch is wired into all four query sites listed
under "The visibility rule". The `COLLATE NOCASE` on
`rc.guild_name = gs.guild_name` is required rather than decorative: SQLite takes
the collation of the LEFT operand, `roster_classes.guild_name` is BINARY, and
the NOCASE declared on the `guild_shares` column would never get a say.

Two consequences, both accepted:

- **A connected tag is inert until Census resolves the character.** Nothing
  fires, and the Sharing page says "guild not resolved yet" rather than offering
  a tag that isn't there.
- **Census guilds are undated**, so leaving a guild retroactively unshares the
  tag-shared back catalogue. That falls straight out of read-time evaluation —
  nothing was ever materialised — and it is why `character_shares` stays the
  "keep sharing regardless of what I do next" tool. The two controls sit side by
  side in the group view for exactly that reason.

`set_guild_shares` rewrites ONE group's rules, not the user's whole set: the
editing surface is a group's page, and a save about one guild must not drop a
rule pointed somewhere else. `since_ts` is pinned to first-connection and `prev`
is keyed on the lowercased name, so re-saving a tag as Census now spells it
keeps the pin instead of quietly withholding the back catalogue again.

## Deleting a group is a soft delete (schema v17)

`groups.deleted_ts`. Nothing is erased: members, invites, the join code, the
auto-shares and the run shares all stay exactly where they were, and an admin
restores the group with one row update (`groups.restore_group`,
`GET /admin/groups` + `POST /admin/groups/{id}/restore`). A group is a roster
somebody spent time building, "delete group" sits one click under the member
list, and the only support answer worth having is putting it back as it was.

That makes "deleted" a READ-TIME condition like every other rule in
`groups.py`, and it has to be said in every branch — a group whose rows are all
still present would otherwise go on sharing raids after it was deleted. The
guard is written once as `groups.LIVE_GROUP(col)` and spliced into
`AUTO_SHARE_REACHES` (which covers all four auto-share sites at a stroke), the
`run_shares` branch of `PERSONAL_RUN_IDS`, `shares_for_runs`,
`shared_via_for_runs`, and — because membership rows survive a delete —
`is_member` / `member_role` / `my_groups` / `group_by_code`. Miss one and a
deleted group keeps leaking; the test deletes a group carrying a shared raid
and checks the member 404s on it, then restores and checks they get it back.

The join code stays on the deleted row on purpose: it can't be joined
(`group_by_code` filters), it stays reserved by the UNIQUE index so it is never
handed to a second group, and a restore is therefore exact.

`DELETE /groups/{id}` now requires `?confirm=<name>` matching the group's name
byte for byte, case included — enforced server-side, not just in the browser.
The delete revokes everyone else's access to every raid that reached them
through the group, so the confirmation is an act of typing rather than an OK
button that muscle memory clears.

`POST /admin/users/{id}/username` renames an account. Nothing stores a username
except `users` — characters, raids, groups and shares all point at the user id
— so it is a relabel and the account stays signed in. Same rules as sign-up
(`auth.USERNAME_RE`, lower case), because login, invites and password reset all
look an account up by exactly that string.

## The same raid, uploaded by several people (schema v18)

Everyone in a raid runs their own ACT. Share a night with a guild group and it
arrives four times over, and the list called that four raids. Within ONE
character's uploads the copies already collapse by content (`_dedupe`, above),
but two people's logs are not the same bytes — different subjects, different
vantage points, different fights heard — so both parses are real and neither is
a copy. Nothing here merges or deletes anything. It answers two questions:
which rows are the same night, and which one to open first.

`backend/raidmatch.py` decides the first, at READ time over the runs a viewer
can already see. Materialising it would be a fact about somebody else's
account, and it would go stale the moment a share was revoked.

- **zone** — equal, NULL-safe. An "Unknown zone" run (the log began mid-zone)
  can still match, but only on the roster; the place is what it cannot state.
- **time** — the windows overlap, ± `CLOCK_SKEW_S` (120s). The epoch prefix is
  authoritative and comes off the raider's own machine, so two clocks in one
  raid agree to within seconds; the slack is not a fuzzy-match knob.
- **roster** — enough of the same people. This is the rule that says NO: two
  guilds in the same instance zone at the same hour pass the first two and
  share nobody. `ROSTER_AGREEMENT` (0.34 of the smaller roster, minimum 2
  names) sits well under a real pair (they run ~1.0) and well over the overlap
  a passing group leaves behind.

That needed the roster itself, not its size, so **schema v18 adds
`zone_runs.roster_json`** — `_raider_count` became `_roster` and
`raider_count` is now its length. Existing rows stay NULL until the startup
relink sweep rewrites them, which it does on every boot; a missing roster costs
the match its cross-check, never a wrong merge (a named zone plus an
overlapping window still stands, an unknown zone does not).

**Precedence is two decisions, in two places, on purpose.**

- `GET /api/zone-runs` stamps `raid_key` (the cluster's lowest run id — a
  handle, meaningless outside the payload), `parses`, and `primary`: the site's
  pick, viewer-independent, so two people discussing a raid are reading the
  same numbers. `_score` is coverage — fights, then combat time, then roster
  size, tie-broken toward the first upload. Someone who zoned in for the last
  two pulls has a real parse of two pulls, and a stranger should not land on it.
- **Your own parse wins**, and that is the browser's to apply (`Home.jsx`
  `chooseParse`), because it depends on who is looking and the payload is
  shared. Your numbers are the ones you can check against what you remember,
  and yours is the one that survives the sharer leaving the group.

The list draws one row per raid with a `Parse` select naming the uploaders
(`character_name`, fight count, "(yours)"); the raid page carries the same
control in its head and switching NAVIGATES, because the fights and the vantage
point are all theirs. `GET /api/zone-runs/{id}` serves `alternates` for it,
filtered through `VISIBLE_RUN_IDS` — the switch re-sorts raids the viewer was
always allowed to open, it is not a directory of who else parsed the night.

Clustering happens AFTER the source/size filters, so narrowing to one group
narrows the menu with it rather than offering parses that are no longer listed.

## The Compare page — any parses, side by side

`/compare` (`frontend/src/pages/Compare.jsx`) puts N parses side by side: a
column is `(zone run, fight selection, subject)` where the subject is the
whole raid or one player. Same player on two nights, two players on one boss,
raid against raid — one surface. It absorbed the old `RaidParseCompare` modal
(raid columns only, unshareable); `ComparePanel` on the raid page stays,
because "these raiders, this run" is already loaded there and needs no picker
— but it renders the same way this page does.

**A column is the ACTUAL parse, not a metric rollup.** How people compare in
practice is a screenshot of their ACT window lined up against somebody
else's, so a player column is their ability breakdown and a raid column is
the zone page's parse list (same columns, same rank coloring). A first
version rendered metrics as rows (DPS, Crit %, … with a ▲ on the leader) and
was replaced — nobody compares "Crit % rows", they compare parses. The
breakdown itself is one component, `BreakdownTable.jsx`, extracted from
`ActorPanel` and shared by the drilldown, the raid page's `ComparePanel` and
this page, so a parse looks identical everywhere it appears; comparison
surfaces pass `defaultHidden` (Share, ToHit, Median, MinHit) and the
`SortableTable` Columns menu brings those back — see "A default-hidden column
is a baseline" below for why that survives the reader touching the menu.
Tables sharing a `prefsKey`
sync layout changes live (an in-module listener set — localStorage's own
event only fires cross-tab), which is what keeps side-by-side columns lined
up while you rearrange them.

**A column carries its own kind tabs**, the same `KIND_FILTERS` set the
drilldown offers (Damage / Heals / Power / Threat / Cures / Self) and only the
ones that parse has rows for (`availKinds`) — a fury's column has no Threat tab
to click. A raid column offers the two the parse list is written for, Damage and
Heals. This replaced a page-wide Damage|Healing tab pair above the columns,
which existed on the argument that comparing one column's damage to another's
heals is not a comparison: true, and still the reader's call rather than the
page's. The tab lives in component state, NOT in `?c` — the token says what the
comparison is OF, a tab is how you are looking at it — and removing a column
takes its tab with it (they are held by position, so a survivor must not
inherit it). A screenshot column has no tabs at all: an image is of one view.
Beyond the tabs, a column is built like the drilldown on purpose — name, class
chip, ✕, controls, tabs, then the composition strip (`CompositionStrip`, shared
with `ActorPanel`) or a raid column's `ParseStrip`, one compact line of headline
numbers standing in for ACT's title bar.

**The URL is the comparison.** One query param `c`, a CSV of
`<runId>:<sel>:<subject>` tokens, where `sel` is `all` or fight ids joined by
`.` — not `+`, which `URLSearchParams` reads as a space — and `subject` is
`raid` or a player name (EQ2 names are single-word alphanumeric, so the
delimiters can't collide). Malformed tokens are dropped, never crashed on.
Everything on the page — add, remove, flip a fight or a subject — rewrites `c`,
so a pasted link reproduces the whole comparison.

**Every number comes from `/encounters/agg`** — per-encounter authorized,
memoized, client-cached — and never from the run report, whose rows are frozen
whole-run and would silently mismatch a per-fight selection. Cross-parse
identity is BY NAME (entity ids are session-scoped). A raid column sums its
raiders — right for the rates too, every raider's rate runs over the same
fight clock — and crit/auto/proc/casts aggregate by summing the per-player
`damageDerived` rollups. Columns fetch and fail INDEPENDENTLY: a run the
viewer can't see renders "not visible to you" in that column, and the rest of
the comparison stands, which is what makes the links safe to share.

**Getting there is one click from any parse**: a Compare chip in the raid
page's title block — the fight rail's head (carrying the page's current fight
selection) — and one in the
player drilldown header (`ActorPanel compareTo`, players only — comparing a
mob across nights isn't a thing). Both land with one column loaded, and the
placeholder slot beside it says where the next one goes.

**The picker is one faceted live search, computed in the browser.** The first
version was a flat `<select>` over every visible run plus a separate two-step
player search — two controls that could not narrow each other, and a dropdown
that grows to three hundred options is not a picker. It is now a search box over
Zone / Named mob / Date / Guild / Player dropdowns: typing `freeth` surfaces
*Freethinker Hideout* nights AND Freethinkers-guild nights, because zones,
guilds and mob names match anywhere in the string (people type them from the
middle) while roster names match from the front (people type those from the
start).

It is client-side because the page **already fetches the whole visible list**,
one row per NIGHT with the same yours-then-primary rule as the raid list.
`?roster=1` (`list_zone_runs`) adds each night's names AND its named mobs with
the encounter ids that are each fight, parsed server-side so the client never
learns the storage format; ~300 nights × ~24 names is about 100 KB, smaller than
one `/encounters/agg` answer the page will fetch anyway. That buys zero debounce,
zero new endpoints, and instant cross-narrowing: **each dropdown's options are
computed from the nights matching every OTHER facet**, so no combination of
choices can strand you on an empty list. The Guild dropdown is not rendered at
all when nothing visible carries a tag — a fresh backfill degrades by the
control not existing yet, not by an empty select. Named mobs follow the fight
rail's hiding rule (`_named_for_runs`): a hidden pull is still its owner's and
is not a boss anyone else can search for.

**The search is a BAND across the top, and one click on a result IS the add.**
The picker used to be a 300px card holding the left edge with the parses stacked
to its right — better than trailing them, which walked the control further right
with every raid added, but still a third of every row spent on something you
have already used. It is now a card across the top of the page — the facets on
one line, the full width underneath for the parses — with a search field
carrying its own magnifier, then Zone / Named / Date / Guild / Player. Each
dropdown is named for what it holds rather than for the rows it would leave
alone ("Zone", not "Any zone": a facet is off when it reads its own name), and
Guild and Player put YOUR guild and YOUR characters at the top marked `(You)`,
read off the `mine` flag the list already carries — hunting for your own name
among three hundred alphabetical ones is the picker failing at its one job.

A result click lands the column already scoped to what the search was about —
the named mob's fights if one is picked (all of them: a raid pulls a boss twice
often enough that both belong), and the person if the Player facet or the anchor
column names one, spelled the way the roster spells them. The old two-step
(select a night, then pick a subject in a confirm strip, then press Add) is
gone: the column's own two dropdowns fix whatever the click got wrong, which is
the same control in the place you are already looking.

**Every dropdown on the page is `Picker`, not `<select>`.** A native select
costs three things this page cannot spend. Its popup is OS chrome —
`color-scheme` gets it dark and that is the end of what this stylesheet may say
about it, so the surface a reader opens most often is the one surface that
looks like nothing else here. An `<option>` is a string, so a raider can be a
name or a class but not both. And a closed select is as wide as its widest
option, which is how one 24-name roster came to set the width of a control
reading `Bobby`, and how a fight label (mob name plus a clock) pushed the
subject picker to the far side of a 380px column. `Picker.jsx` splits those
apart: the BUTTON is sized by the row it sits in and truncates, the PANEL is
sized by its content. Rows carry an icon and a muted hint, so a player row is a
class dot, a name and the class spelled out; sections are optgroups; past ten
rows the panel grows a filter, because a roster is a list you search.

The open panel is rendered into `document.body` and positioned from the
button's rect. That is not a preference — **every `.card` here carries
`backdrop-filter`, which makes it a stacking context AND a containing block for
`position: fixed`**, so a menu written inside a card is sealed into that card's
box and painted under every later sibling however high its z-index goes. The
search band is a card and the parse columns are cards after it, so facet menus
dropped down *behind* the parses. The same trap put the screenshot viewer under
the next column. Leaving the card is the fix; a bigger z-index cannot be one.

**A row's own parts are targets too.** A night found by a mob name is not really
an answer of "this raid" — searching `saw` and getting *The Emerald Halls* means
the pull, not the night — so the matching named mobs sit under the row as chips
and go straight to that fight, as does any raider whose name the query matched.
A chip carries a MARK, not just a tint: a skull for a pull, a head for a person.
They are the one place on the page where the two kinds of target sit in one
strip, and telling them apart by color alone is the mistake the class chips are
careful not to make. The chips hang off a vertical rule descending from their
row, and each result is ruled off from the next — a dozen results in a
three-column grid, each two or three lines with a strip of chips under it, ran
together into a block where one row's chips read as the next row's subtitle.
With no question about mobs asked, the night's named mobs are offered anyway
(capped): going straight to a boss is the common move. Raiders are not offered
that way — twenty-four names under every row is a roster, not a shortlist — and
the chips compose with the facets rather than replacing them, so a mob chip
keeps the player the search is about and a person chip keeps the pull.

**Results appear only once something has been asked, and the empty slot is the
drop box.** Twelve recent raids sitting there on arrival read as the page's
content, when the content is the parses underneath — so what speaks in the
meantime is the last column: a `ShotDrop` styled as a parse column (`.dropslot`,
a + inside a heavy dashed border) captioned *Search or add a screenshot to
compare…*. It says where the next parse lands AND takes one, which is why there
is no second placeholder and no drop target up in the search band: those were
two objects making one statement. It stays for good, walking right as parses
fill in from the left. Removing a column is an explicit ✕ at the end of its
title line (the drilldown's), not a click on the title itself — a heading that
deletes what you are reading is not a heading.

`GET /api/players?q=` / `GET /api/players/{name}/runs` remain in `zoneruns_api.py`
— a `json_each` scan of `zone_runs.roster_json` behind `VISIBLE_RUN_IDS`, the
same predicate as the list — but the picker no longer calls them. `?roster=1`
runs behind that identical predicate, so it reveals nothing a viewer could not
already read fight by fight.

## Importing a parse from a screenshot (`pipeline/actshot.py`, schema v27)

Half of every comparison people actually make lives in Discord as an image.
Somebody posts their ACT window, somebody else wants to know how they measure
up, and there is no log on either side of that exchange — only a JPEG. This
reads one back into numbers.

**It is not a second ground truth.** An ACT XML export is what the parser is
validated against, and that has not changed. A screenshot is a CLAIM about
somebody else's night, read off pixels, and the whole design follows from
taking that seriously rather than from trying to make it look authoritative.

### It is kept out of the parse world entirely

An import writes one row in `imported_parses` and touches nothing else: no
session, no character, no encounter, no zone run, no entity. That is the
containment, and it is structural rather than a matter of remembering to
filter. Nothing that rolls up, ranks, votes on a guild tag or clusters a raid
can reach a shot, because none of them look in that table. The rows live as
JSON in it for the same reason — the moment they share a table with parsed
numbers, something eventually averages the two together.

Visibility is equally deliberate: a shot is private to whoever imported it,
full stop. `groups.py` owns the one visibility predicate for real parses, and
the rule about that predicate is that it does not acquire weaker siblings. A
shot needs no branch in it, so it gets none; if shots ever want sharing they go
through the existing predicate rather than beside it. Ids are sequential, so a
stranger's `GET` answers 404 exactly as a missing one does.

The picture is kept, but never the original file. A re-encoded WebP copy and a
thumbnail go to `PARSESHOTS_DIR`; the uploaded bytes do not. This reverses a
first decision to drop the image entirely, and the reason it reversed is the
table further down: four columns cannot be checked by any arithmetic, so the
screenshot is the only other evidence those numbers have, and a parse you
cannot put beside its source is one you have to take on faith. Re-encoding is
what makes that safe to keep — the file on disk is an image this app wrote, at
a size it chose, carrying nothing the original file carried besides pixels.

The copies are exactly as private as the row. Served by an owner-checked
endpoint rather than a static mount, because a static directory makes the
filename the permission; named with a random token so a stray path is not one
either; and `Cache-Control: private` so no shared cache holds somebody's
screenshot. They are written only AFTER the table reads — a picture of
something that is not an ACT window has no reason to be on this disk — and
deleting the parse deletes them.

The kept copy is deliberately NOT shrunk to a convenient web size. Its purpose
is reading a number off it, and small antialiased digits scaled to fit a
viewport are precisely what cannot be checked; it is bounded at 2200px only so
a 4K capture doesn't sit at full size.

The VIEWER is a separate question from the file, and it answers differently:
it opens **fit to the screen** and zooms on request. Opening at the stored
pitch followed that same reading-a-number argument and got it wrong by one
step — a 2200px capture dropped onto a laptop shows you one corner of a table
with no way to tell which corner. So the first paint is the whole window and
`Full size` scrolls it at its stored pitch, which is the mode for checking a
cell. Two jobs, two modes. It also renders into `document.body`: opened from
inside a compare column it was trapped in that column's card (`backdrop-filter`
again — see the Picker note above) and painted under the column to its right.

**An imported column is NAMED, not labelled with whatever ACT's title bar
said.** That title bar names the VIEW, so a whole-night screenshot comes back
called `All` — true, and no answer at all to "which parse is this" when two
imported columns sit side by side. `shotTitle` joins who, where and which
fight: *Bobby — Halls of Fate — All*, dropping any part the shot doesn't carry.
The screenshot itself sits in the column HEAD, right of that title block rather
than under it: both are about three short lines tall, so side by side they cost
one band of the column where stacked they cost two, and vertical space above
the table is spent by BOTH parses before their rows line up. The ✕ goes past
the picture, in the card's corner — everywhere else it ends the title line,
which is the same statement (the far end of the head).

### Nothing about the table is assumed

ACT's columns are the reader's, and the two committed fixtures prove it: one
has `AvgDelay` and the other has no such column. So the geometry is measured
per image.

*Rows.* Horizontal rules give the ladder, but they are FITTED rather than
walked. Rescaling by Discord makes the pitch fractional — 17.46px, so gaps
alternate 17/18 — and a fixed pitch drifts off the ladder within twenty rows.
Every (pitch, offset) is scored by how many rules land on it and the winner is
refit by least squares. That also absorbs the two things that break a greedy
chain: a highlighted row swallows its own rule, and the pie chart under the
table contributes rules of its own. Getting this wrong is not subtle — the
pie's legend entries are ability names, so a ladder that runs past the table's
bottom edge reads the legend as parse rows.

*Columns.* Only the header band carries separator ticks. A separator is told
apart from header LETTERING by variance down the band, not by darkness: on a
rescaled shot the two are equally dark, and a mean-only test reads half the
header as columns. The heading is then OCR'd and fuzzy-matched to a field,
because the heading is the only thing that says what a column is.

*The selected row.* ACT draws it white-on-blue. Greyscaling loses it entirely,
and inverting leaves dark text on grey inside an otherwise white column strip,
which `psm 6` drops. It is binarized to black-on-white and re-read three ways —
tight crop, wide crop, whole row — because no single crop is right: tight
clips a right-aligned leading digit (`824` → `24`), wide bleeds the
neighbour's digits in (`1,017.33` → `64,017.33`), and whole-row loses column
identity where a word box straddles a boundary. The three disagree in
different places, so their agreement is the answer, grouped by DIGITS so that
`2.57` and `257` count as one reading — they differ only in whether the
decimal mark survived.

### The locale is arithmetic, not a setting

`5.612.947` is five million to a German client and 5.612 to an American one,
and at this font size `.` and `,` are a couple of pixels apart. Nothing asks
the user and nothing guesses: `Damage / EncDPS` is the same number on every
row, so the mark that makes the most rows agree on it wins. That is usually a
tie — reading `9.241,15` under the wrong mark still recovers every digit and
merely shifts the ratio by a factor of 100, the same factor on every row, so
the cluster is exactly as tight — and the tiebreak is the shape of the
two-decimal columns (`98,60` against `100.00`), which is the evidence a human
uses. The Discord fixture is German and is detected as such.

### The fight length comes from the table, not the title bar

The title's `[mm:ss]` is the duration of ONE encounter. On ACT's `All` line it
is not the fight length at all, and a shot of `All` printing `[00:12]` over 654
seconds of parse was read as a 12-second fight: `_repair` recomputed every
EncDPS as damage/12, so the `All` row published 378,596 DPS against ACT's own
6,946.73 and every ability row was wrong by the same factor of 54. Only the
DPS column was wrong, which is the column people import a screenshot to read.

So the duration is fitted from the table (`_duration_from_table`): the mode of
`Damage / EncDPS` across the rows, which is forty readings of one number
against the title's one. It survives the rows whose EncDPS lost a decimal mark,
and it is right about `All` as well as a single pull. The title is used only
when fewer than four rows agree, and where the two disagree the shot carries a
note saying so. On both single-encounter fixtures they agree, and the title's
value is kept untouched.

The same reversal applies to the two-decimal columns. ACT prints `EncDPS`,
`Average`, `ToHit` and `AvgDelay` with two decimals ALWAYS, so a reading
carrying no separator at all lost the mark rather than the digits — AvgDelay
`461` is 4.61. Losing a mark cannot shorten the digit string, which is what
makes that safe to apply without a second reading.

### What can be checked, and what is simply reported

ACT's table is redundant, and that redundancy is the entire warrant for showing
an OCR'd parse at all:

| column | how it is known |
|---|---|
| `Damage` | the `All` row is the sum of the rest |
| `EncDPS` | `Damage / duration` — recomputed, and it is also what FITS the duration |
| `Average` | `Damage / Hits` — recomputed, so a dropped decimal repairs itself |
| `ToHit` | `Hits / Swings` — recomputed while `Hits <= Swings` holds |
| `Hits`, `Swings` | `Hits <= Swings` is an invariant; a Swings cell that breaks it lost a digit (`73` → `7`) and is rebuilt from ToHit, which is a third reading of the same fact. Publishing the pair instead printed a ToHit of 1042.86% |
| `Median`, `MinHit`, `MaxHit` | **unverifiable**, beyond `Min <= Median <= Max` |
| `Crit%` | **unverifiable**, beyond being a percentage — which is enough to drop the selection artifact's leading digit on the highlighted row (`167%` → 67%) |

Unverifiable cells are reported as read. A cell that FAILS a check it was
subject to is blanked rather than published: a number we have positive
evidence is wrong is worse than no number. `Resist` snaps to the closed
vocabulary of damage types, so `cisease` never reaches a reader.

There is deliberately **no review step**. One was designed and dropped on
Lindsay's call, and the reasoning holds: a confirm grid cannot make an
unverifiable number true, and the cells it would have caught are exactly the
ones nobody can check against anything anyway. What survives instead is the
labelling — an imported column says `imported` wherever it appears.

### On the page

`ShotDrop` IS Compare's empty column rather than a page of its own, because a
screenshot is another way of NAMING a parse, not a separate activity — and
because the slot that says "another parse goes here" and the box that takes one
are the same statement. Drop it, paste it (the way an image leaves Discord is
right-click → Copy image, so paste is first-class, not a nicety) or click to
browse; it becomes a column, and the slot slides one place right, still ready
for the next one, exactly as the search is after a hit.

Behind it, dimmed almost to a texture, is a real ACT window
(`frontend/src/assets/act-window.webp` — a 640px crop of the TABLE, not the pie
chart, 33 KB): the box shows what goes in it instead of only saying so. It
lifts on hover and again while a file is over the box, so the slot answers the
pointer. On the parchment theme it is stronger and multiplied, because a pale
screenshot on a pale card is otherwise invisible; either way it stays faint
enough that the caption is the only thing you read. Reading takes seconds rather than milliseconds, so the
endpoint is a plain `def` — FastAPI runs it in the threadpool and one import
does not stall the event loop.

The token grammar keeps three fields, `shot:<id>:parse`, so the CSV, the
ordering and the remove logic never learn which kind a column is; only the
fetch and the table differ. `shot` cannot collide with a run id, which is
always a number. An imported column renders the SAME `BreakdownTable` as a
real parse — that is the point of importing one — and every column it draws is
either carried by the shot or derived the way it is for a real parse. `Crit %`
is the one worth naming: a shot carries the percentage, so the crit COUNT is
reconstructed from it, which the table's own `All` row then re-weights.

**The picture travels with the parse.** An imported column carries the
screenshot as a thumbnail beside its headline numbers (`ShotViewer.jsx`, shared
with the Import page's table), and clicking it opens the stored image at full
size — scrolling rather than scaled down, because the reason to open it is to
read a figure off it. Clicking ANYWHERE closes it: backdrop, picture, caption,
Escape, the Close chip; only the head bar is exempt. Some of the columns cannot
be checked by arithmetic, so the picture is the only other evidence there is —
leaving it behind on the Import page put the claim and its evidence on two
different screens.

A screenshot is of ONE view, so its column has no kind tabs — a Healing shot is
a Healing column, headed as one, next to whatever the columns beside it are
showing. (While a page-wide tab ruled every column, that same fact had to be an
apology: a Healing shot on the Damage tab said to switch tabs instead of drawing
heals under a DPS heading.) And it refuses rather than invents where it must: a
title bar with no `[mm:ss]` means there is no clock, so the column says
per-second numbers cannot be worked out rather than dividing by a guess.

## Reading the raid, not just counting it

### Class inference (`pipeline/classguess.py`)

The log never states anyone's class, but it states what they cast, and
`ability_catalog.class` knows who can cast what. Per player: take the distinct
ability names they used, drop the autoattack buckets (class-blind), pet-kit
names, and **procs** (gear fires those — they say nothing about the caster),
then let each remaining name vote for its class. A `characters` row with a
Census class overrides the vote outright (`source: "census"`, confidence 1.0).

Three rules, each answering a way the first version got it wrong (it voted per
FILE with whole votes only, and named 198 of 981 player rows):

- **Evidence pools across sessions, keyed by NAME** (`_evidence`, one 0.4s
  query over the whole database). Per-file guessing gave the same person a
  class in one raid and nothing in the next, and for 19 players two different
  answers in the same list (Zooey: defiler here, mystic there). The answer is
  written back to EVERY entity row with that name, so it reaches older raids
  without a reparse.
- **Shared spells vote in fractions.** "conjuror,necromancer" used to be
  discarded; it is half a vote each — it cannot pick between the two, but it is
  real evidence against the other twenty-two.
- **A margin rule beside the share rule.** A winner needs whole-vote evidence
  (`MIN_STRONG`, 2 single-class spells), `MIN_SCORE` of weight, and either a
  majority of the weight cast or double the runner-up. The margin is what names
  a player whose gear procs are not all flagged: Shaly scores 14.5 coercer
  against 7 bruiser and 4 each of three more — 39% of the weight, and obviously
  a coercer.

**Census by NAME is the answer; the vote is the approximation**
(`census/roster.py`, `roster_classes`, schema v19). `character/?name.first_lower=
zooey&locationdata.worldid=618` returns `Mystic` — the game answering, in 0.12s,
without caring that half a raid log's ability names have no Census spell row.
`characters` already held this for the handful of people with an account here;
`roster_classes` holds it for every name that has ever appeared in one of their
raids. On Lindsay's database that took the 8+-raider runs from 20% of raiders
resolved by inference alone to **80% from Census, 94% counting inference**, and
it agreed with every inference it overlapped (Klebb Brigand, Thwart Coercer,
Rorschach Assassin) while correcting the one that was wrong (Zooey: the vote
said Defiler, Census says Mystic).

A miss is an answer too and is cached the same way (`found=0`): `Enynti` is not
a character. That is one of the two negatives `refine_bare_pets` needs.

Census is authoritative about NOW, never about the night of the raid, so it is
layered UNDER the timeline, not over it — a Census row written today must not
relabel a raid from before a betrayal. Order of authority per fight, in
`resolve_class`: **what the fights on screen prove > the era the fight falls in
> Census > the pooled vote.** The local evidence chooses WHEN (which of the
classes this name is known to have held was live that night), never WHAT — so a
coercer whose charmed pet cast three Berserker abilities in one fight cannot be
promoted to Berserker by that fight.

Requests are budgeted (`ROSTER_LOOKUP_BUDGET`) and run OUTSIDE the parse's write
transaction; a Census outage costs a retry, never a parse and never a `found=0`
written over a real character. `backend/tools/sync_roster.py` does the bulk
backfill. Needs a real `CENSUS_SERVICE_ID` — `s:example` throttles after about
six requests, which will not get through one raid.

## The raid's guild, voted by its roster (schema v20)

Nothing in an EQ2 log says which guild a raid belongs to. The roster does, one
name at a time — and the character doc that answers "what class" already carries
"what guild", so `census/roster.py` captures both from the one request it was
already paying for. `census/guilds.py` turns that into a tag: `zone_runs.guild`,
a pill right of the zone name on the raid list and the raid page, and the Compare
picker's Guild facet.

**A wrong tag is a public claim about somebody else's guild**, so the vote
abstains twice as readily as it commits:

- **Abstain on thin evidence.** Fewer than half the roster resolved and there is
  no answer here. About 18% of a real roster never resolves at all (pets, mobs,
  names typed before the character existed), and a tag drawn from six of
  twenty-four names is a guess wearing a fact's clothes.
- **Tag only on a strict majority of what IS known, with the known-guildless
  counting against.** Twelve Freethinkers and ten pick-ups is a Freethinkers
  raid; three Freethinkers and eight unguilded friends is a pick-up group that
  happens to carry three guildies. Ties fail the strict test for free.
- Runs under `RAID_MIN_RAIDERS` are forced NULL — imported from `groups.py`,
  because "what is a raid" is one line the whole app draws once.

That needs three states, not two, which is what `roster_classes.guild_checked`
is for. `guild_checked=1` with a `guild_name` is a guild; `1` with NULL is
**known guildless**, and it votes; `0` is *never asked*, and it abstains.
Collapsing the last two would let a backfill in progress strip real tags, or
paint somebody's guild onto a night that was mostly strangers.

The tag is derived, never authored, so it is **recomputed rather than
maintained**. `retag_runs` is pure SQL over already-cached rows — zero Census
calls — and every write path that can change a roster calls it afterwards:
`pipeline/zoneruns.py` at the end of `rebuild_zone_runs` (the funnel every
upload, live close, reparse, merge, split and delete ends in), `ingest_writer`
once more after the parse-path lookups land, and the hourly loop in `main.py`.
There is therefore no staleness column and no `PARSE_VERSION` bump: nothing
about parser or rollup semantics changed, and NULL means "no majority holds"
and "not computed yet" alike — to every reader they are the same thing.

The ~1100 names cached before v20 have a class and no guild answer. They are not
stale by any TTL, just missing a field, which is what `resolve(force=True)` is
for; `backfill_stale_guilds` walks them oldest-first, 120 per hourly tick at
0.75s pacing, and `sync_roster.py --guilds` is the same pass with no budget for
when you want it now.

### The rest of the character doc: level and guild on an actor row

The same cached row carries a `level`, and until the drilldown asked for it
nothing ever read it back. `_census_facts` / `_add_census_facts`
(`routers/encounters_api.py`) hang `level` and `guild` on every PLAYER actor in
`/encounters/agg` and `/encounters/{id}`, so opening someone's parse names a
person — *Abath, Shadowknight, L70, Gin and Jumjum* — instead of a bare string.
One indexed read per selection, zero requests: the facts are already in
`roster_classes` because the class lookup paid for them.

Two limits worth keeping straight. The world comes from the sessions in the
selection (`roster_classes` is keyed by name AND world), so a name is answered
by the server the raid was on. And unlike the class, **these are undated**:
Census reports where somebody is NOW, and nothing here splits them into eras
the way `_split_eras` splits a spellbook. So they caption a name and never feed
a number — the tooltips say as much, and a raider Census never resolved gets
nothing rather than a zero.

There is no gear/AA link to go with them. EQ2U (`u.eq2wire.com`) is the obvious
target and its character routes answer 200 with an empty body for every id and
name tried on 2026-08-05, so it would have been a link to nowhere. Showing gear
would mean ingesting `equipmentslotlist` from the character doc ourselves.

**A class change is a DATE, not a tie** (`_split_eras`, `_write_eras`). EQ2
lets a character betray into the other half of their archetype, and pooling
forever then guarantees a deadlock between two full spellbooks: Klebb cast
swashbuckler abilities until 2026-07-31 and brigand abilities after it (17 v
16), Thwart was an illusionist until 2026-08-02 and a coercer from 2026-08-04
(12 v 10). Both failed the tie rule, both went blank in **every raid they had
ever appeared in**, and every further upload made it more permanent — Klebb was
blank on the Mistmoore's Inner Sanctum page for exactly this reason. So when
the pooled vote deadlocks, the contenders' ability WINDOWS decide: if the last
swashbuckler ability lands before the first brigand one, that is not ambiguity,
because a character who betrayed cannot cast the old book again. `_split_eras`
cuts between the windows, infers each side from its own evidence, and each
session is answered from the era it falls in — which is why these names skip
the blanket write-back above.

Disjointness is the whole test, not "two strong classes": a raider wearing
another class's proc gear scores stray votes all night long and those
interleave. One residual: `entities.class_guess` is per session, so a log that
spans the changeover (Klebb's falls inside one 1.2M-line file) gets the era it
cast more of, and the fights in that one file predating the switch read as the
new class.

**Voting cannot tell you WHETHER the row is a person** (`refine.roster_prescan`,
`guess_session_classes(roster=…)`). EQ2 writes a summoned pet exactly like a
raider — a bare capitalized name casting its class's real spells — and it
receives group buffs and gets warded like one too, so every signal the vote
reads agrees with itself. On 2026-08-04 that produced `Kartik — Berserker`,
`Vaser — Fury`, `Leneker — Coercer 100%`; each acted in ONE of 21 encounters
while every real raider acted in 20 or more, and the log carried no owner
possessive anywhere, which is also why `petnames` (which learns from
`Alas, <Owner>'s <Pet> has died…`) can never reach them.

So the file has to be the tiebreak: `roster_prescan` collects the names that
appear in a line **only a player character can produce** — chat, raid join /
leave, guild login, loot, resurrection. On that night it covered all 26 real
raiders and none of the seven pets. A name with no such evidence is written
`{"class": null, "source": "unidentified"}` and the table says so, rather than
showing a classless raider. Two deliberate asymmetries: the set is
over-inclusive (server-wide chat counts, so a stranger who said hello lands in
it) because a name wrongly INCLUDED only keeps the status quo while one wrongly
excluded strips a real raider's class; and the mark is written for that session
only, and other sessions' inference will not overwrite it, because the same
name can be a raider in one log and a pet in the next.

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

### Pets and procs stop being inferred (schema v22, PARSE_VERSION 20)

Everything above was a per-row softening of a claim that should never have been
made. The claim itself was wrong, in two directions at once, and it had a
feedback loop.

**Pets.** `census/catalog.observe_pet_abilities` wrote `unit='pet'` for
anything a pet-KIND entity cast — globally, permanently, off one sighting. But
"pet-kind" includes `refine_bare_pets`' guess at a bare capitalized name, and
that pass used Census `found=0` as a way IN while never reading `found=1` as a
way out. So `Gululu` (a level 70 shadowknight), `Wudi` (wizard) and `Moklok`
(troubador, guild "Skill Issue") were filed as dumbfires, their spellbooks were
learned as pet kits, and `refine_bare_pets` reads that same table back to
decide what a pet is — which brought more names in on the next parse. It
converged on **228 pet-flagged names, 108 of which Census knows as scribed
player spells**: `Ice Comet`, `Harm Touch`, `Apocalypse`, `Raging Blow`.
Necromancer looked right only because `CURATED_PET_ABILITIES` already covered
it, which is exactly why the bug survived — the class anyone checked was the
one with a human answer.

**Procs.** `may cast X on …` names X for every buff that references it, so a
class's own button gets flagged the moment anything can fire it: `Berserk`
(berserker), `Dragon Stance` (monk), `Baffle` (brigand), `Knockdown`, `Pin`.

The fix is a precedence ladder, not a better guess:

    ability_rulings  >  curated seed  >  no label at all

`census/catalog.pet_ability_names` / `proc_ability_names` are the only doors to
both labels and they encode that ladder in SQL; `encounters_api` calls them
rather than keeping its own copy, so one ruling reaches the badges, the
rollup's press counting and the coach together. `reset_verdicts` (startup, before
`seed_curated`) demotes every machine-written label into the candidate columns
`pet_seen` / `proc_candidate` — a database that already learned wrong loses its
bad badges on the next restart with **no reparse**, because the labels live in
`ability_catalog`, not in the rolled-up rows.

What the machine still does is gather evidence, and the three columns are
deliberately different strengths:

- `pet_definite` — cast under `<Owner>'s <lowercase remainder>`, the swarm form
  nothing else produces. Certain by grammar.
- `pet_own` — under the logger's own bare name, certain by the YOU/YOUR rule.
- `pet_guess` — a bare capitalized name the refiner guessed at. **Every bad
  label came out of this column**, so it is named as the weak one and a handful
  of them against thousands of player casts is discarded outright
  (`PET_GUESS_SHARE`).

`ability_pet_sightings` is keyed by session so the PARSE_VERSION sweep restates
evidence instead of inflating it — "seen in 4 raids" has to keep meaning four
raids.

### Provenance comes from Census, not from a guess

`Fae Fires` is not "a gear proc". Census holds `Fae Fire`, a level 35 **fury**
spell whose effect text reads *"On any combat or spell hit this spell will cast
Fae Fires on target of attack."* Every spell record carries `given_by`, `type`,
`alternate_advancement` and `deity`, and `census/abilityreview.proc_sources`
finds a proc's source spell by that effect text, so the answer to "spell, AA,
gear or deity" is usually already cached.

Two things had to change to read it. `census/effects.py` matched only `may
cast`, dropping every guaranteed `will cast` — half the grammar, and the half
holding `Shout`, `Thorns`, `Grisly Feedback`, `Prismatic Shock`, `Thunder
Fist`. It now keeps the whole clause: `trigger`, `mode` and `per_min`, because
"on a melee hit, ~3/min" is a proc and "on a kill" is a consequence of one, and
they read identically once the condition is thrown away.

What Census genuinely cannot answer yet is **gear and deity**: `census_items`
holds 143 rows (an item is only fetched when something already referenced it)
and exactly 2 spells carry the deity flag, because the ingest walks class spell
pages. So "no cached spell casts it" is a real finding — gear, an AA or a deity
— and splitting those three needs the `item` + `alternateadvancement` pulls
that are still open.

**Self vs granted is a per-ROW question**, not a per-ability one, and that is
why `ability_rulings.grant_class` exists. `Fae Fires` on a fury is their own
buff; on the warlock beside them it is the fury's. Same ability, two answers,
decided against the actor's class — which is the buff-attribution problem
above, now with the data it needs.

### The Abilities console (`/admin/abilities`, role `curator`)

Inference stops at "here is the evidence, here is how sure I am"; a person
answers the rest. `scope=open` is the queue — unruled and under full confidence
— and the class rail is the ergonomics: 565 undecided abilities is a wall, 56
under `assassin` is an afternoon. An ability sits under **every** class that
might own it (who scribes it, whose buff fires it, who was seen using it), so
one name appearing three times is correct until it is ruled on, and `Unclassed`
(160) is where the gear and AA procs collect. The search box reaches every
ability ever tracked, settled ones included — that is how a wrong answer gets
fixed later.

`curator` is a separate role because the two jobs are unrelated: deciding what
`Fae Fires` is needs someone who knows EQ2, and running the site needs someone
with the disk. Granting `admin` to get the first would hand over accounts,
storage limits and the audit log with it. It widens nothing about who can read
a raid — the payload is ability names, site-wide sums and class names, never a
player name, an entity or a row from anybody's parse, which keeps the admin
console's promise intact.

**The lookup button.** Every ability row in a parse carries a ⚙ for a curator
(`BreakdownTable`, hidden until the row is hovered) linking to
`/admin/abilities?q=<ability>`. The place you NOTICE that `Ice Comet` is not a
pet ability is a raid page, not an admin queue, and making someone go find it
by name is how a wrong label survives. `?q=` is the address rather than
component state so the link works from anywhere; `BreakdownTable` reads the
viewer from `lib/session.jsx` because it renders in three places that don't own
a user, and that context carries no authority — everything it gates re-checks
server-side, so a stale value shows a link that 403s, never data.

### A grant is to a TIER, not a class (`classtree.py`)

AAs are granted at every level of EQ2's tree — the Predator line belongs to
rangers **and** assassins, a Scout AA to all seven scouts — so "who gets this"
cannot be one class name. `classtree.expand` is the single translation:
`predator` → {ranger, assassin}, `scout` → all seven, `ranger` → itself. A
ruling stored against `predator` groups under both subclasses on the Abilities
page and will compare against both when self-vs-granted lands, without being
written twice or drifting.

Census does not need this — it only ever writes subclass names, having expanded
groups before we see them (its `class` column runs `assassin,beastlord,brigand,
dirge,ranger,swashbuckler,troubador` where the game says "Scout"). The tree is
for the side Census does not cover: what a person types. `normalize` is
therefore strict — an unrecognized target is REJECTED at the API rather than
dropped, because `predatr` saved silently is a grant that reaches nobody and
looks like a decision.

Note this is EQ2's own tree and NOT the role grouping in `coach/descriptive.py`.
They answer different questions and must not be merged: that all six Fighter
subclasses are TANKS is a fact about role; that Paladin and Shadowknight are
both Crusaders is a fact about the tree, and only the second says who an AA
reaches.

The tier names came from the wiki's own category tree, which files spells and
AAs at every tier the game grants at (`Category:Predator Spells` sits under
`Category:Scout Spells`). That is also where two of them were corrected:
Beastlord's tier-2 is **Animalist** and Channeler's is **Shaper**, not their own
subclass names.

### The wiki as reference data (`gamewiki.py`, schema v23)

Census stays authoritative for SPELLS — 26,082 records with damage ranges,
periods and the effect grammar, more than any wiki page holds. What it was
never asked for is **AAs** (256 incidental rows against the wiki's 1215) and
items, and between them they are most of what a raid log names and nothing
could explain: 479 ability names with no Census row at all.

**`activated` is what earns the table.** `You prepare <X>` prints for spells and
combat arts and **not** for AA activations. So the log's only proof that
something was PRESSED is missing exactly where AAs live, and an activated AA is
indistinguishable from a gear proc: logger hits, no prepare line. That read
`Lifeburn` — a five-minute recast the necromancer presses — as gear, along with
`Mana Flow`, `Counterblade`, `Nullifying Staff` and 8 more confirmed, out of 45
rows resting on the same silence. A recast timer settles it, and it exists
nowhere in the log. `suggest()` therefore checks `activated` BEFORE the
prepare-line test; the ordering is the fix, not an optimization.

**The wiki is also a proc SOURCE.** Its effect bullets use the same trigger
grammar Census does — "On a melee hit this spell has a X% chance to cast Pirate
Stab on target of attack" — so `census.effects` reads them unchanged, with
letter placeholders swapped for a zero because a wiki page covers every rank at
once. That is how `Avast Ye` (rogue AA) is identified as the source of `Pirate
Stab`, which Census cannot do. 42 abilities are sourced this way.

**Era is a hard filter, not a preference.** The wiki separates its AA trees by
expansion and they do not overlap at all — verified 1215 EoF pages against 407
later ones, zero shared. `DEFAULT_ERAS = ("eof",)` because this is a level-70
TLE server; ingesting Heroic (RoK), Shadows (TSO) or Dragon (DoV) would label
raids with content that does not exist here, the same class of mistake as
inferring a pet from one sighting. Adding RoK when the server gets there is one
entry in `AA_TREES` and a re-sync.

Run by hand (`backend/tools/sync_wiki.py`), never on a schedule: AA trees change
once an expansion, and a nightly job against someone else's wiki buys nothing
and costs them bandwidth. Content is CC-BY-SA — fine as internal reference data,
and anything surfaced to a reader should carry attribution. Tests use pages
recorded verbatim in `fixtures/wiki/` and never touch the network.

**Deities are the other half** (`--what deity`): 139 blessings and miracles,
always EoF because deities arrived with it. Each carries the god that grants it
(`Rallos' Devastation` → Rallos Zek) in the `line` column — the same slot an
AA's line uses, answering the same question — and no class tier, because a god
grants to whoever worships it. All 139 are activated: a miracle is a button on
an hour recast.

**A name is not a key**, and getting that wrong cost real data twice:

- `wiki_abilities` is keyed on **(name, kind)**. One name really is two
  abilities — the fury spell `Tempest` and Karana's miracle both print the same
  in a log, and 37 AA names collide with a blessing. The first single-column
  key let the deity sync silently overwrite them.
- The same AA on several classes gets one page each (`Enhance: Cure (Mystic)`,
  `(Templar)`, `(Warden)`), and those **merge their tiers** rather than
  overwriting. 66 pages were collapsing to 29 names, keeping one class and
  losing the rest.
- A wiki row only speaks when **Census does not contradict it**. Matching by
  name is the weakest join available; a Census spell record is the game saying
  a class scribes it. So `scribed_by` wins, and the wiki stays on screen as
  evidence — which is what keeps `Tempest` a fury spell instead of handing it
  to Karana. `by_name` marks a name the wiki itself holds twice as `ambiguous`
  and `suggest` refuses to be confident about it.
- Disambiguation pages are skipped outright. They are pointers, not abilities,
  and ingesting one is exactly how a god claims a class spell.

Result: the open queue fell 560 → 478 and high-confidence rows rose 944 → 1029,
but the number that matters is 11 verdicts that were confidently wrong and are
now right.

**Gear was investigated and dropped** (2026-08-05), and the reasons are worth
keeping so nobody spends the day again. EQ2 has ~212,000 items, so a crawl is
out. `{{EquipmentEffect|<Ability>|}}` on an item page is a template PARAMETER,
not a wiki link, so `list=backlinks` returns nothing and there is no reverse
index to walk. What is left is full-text search with verification — search the
ability name, read the candidate pages, accept only one whose own effect text
casts that exact ability — and measured against the 381 abilities nothing can
explain, sampling the 60 largest by damage, that found **13**. Two were out of
era (`Caustic Poison` → a Level 90 crate item), which item pages do allow
filtering because they carry `level`, dropping the yield to roughly 15%.

~1500 requests for maybe 55 of 381 is a much worse trade than the AA pull, and
unlike AAs it corrects nothing — those rows are already honestly marked
unknown. An unresolved gear proc is a curator's job. Reopen only if Fandom
enables CirrusSearch: `insource:` would turn that structured `effectlist` into
a precise one-call reverse index and change the arithmetic entirely.

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

### `GET /api/encounters/class-stats?ids=…` — the Class tab

The stats only one class can answer. "Was Jester's Cap up on the assassin all
fight" is a troubador question and nobody else's; as a column in the combatant
table it would be blank for twenty-five classes out of twenty-six. So the
selection is split by class instead, and each class owns its panel.

`pipeline/classstats.py` is a **registry**, and that is the whole design: a
metric is one `@register(...)`-decorated function declaring its columns and
returning rows. The endpoint enumerates the registry, the frontend
(`ClassPanel.jsx`) formats by column UNIT (`text|num|pct|secs|clock|rate`), and
nothing else has to change. Adding "Perfection of the Maestro uptime" is a
Python function, not a migration plus an API change plus a component.

Rules the shape enforces:

- **`blurb` is required**, and it carries the LIMIT, not the pitch. These stats
  live at the edge of what a log can prove — a buff with no fade line, a proc
  with no logged source — and the caveat belongs beside the number, in the
  panel, rather than in a doc nobody opens.
- **A class with no metrics is still a section.** The honest state of this tab
  is "we know who was here, we have not written their stats yet", and the
  frontend says exactly that (`Coming soon.`). It is not an error state.
- **Class resolution is not redone here.** The actor list comes from the
  memoized `/agg` payload the Damage tab already fetched, so "what class is
  this raider" has one answer per page — the one `classguess.resolve_class`
  gave, dated to the fight.
- **`class_source == 'unidentified'` is not a class we failed to guess.** It is
  the refine pass saying nothing in the log proved a person was behind the
  name, which usually means a summoned pet. Those names appear in neither the
  class sections nor the unmatched list; filing them under "class unknown"
  would be a claim about a pet.
- **A metric that raises is isolated** (`status: "error"`) and the rest of the
  tab renders. One bad regex must not take the Class tab out for a whole raid.
- **`needs_events=True` on a pruned selection reports that** rather than
  returning zeroes: pruning keeps the rollups and drops the events, and "no
  uptime" and "no events" are different answers.

`Ctx.events(types)` is the one expensive door — stored events for the live
encounters with entity ids resolved to names, cached per type-set for the
request, so two metrics wanting casts pay for one read.

`test_classstats.py` covers the pipe with stub metrics (it empties the registry
for every test, so the shape stays pinned whatever real metrics exist);
`test_classmetrics.py` covers the real ones.

### Curated buff lines (`parser/buffs.py`) — and Jester's Cap uptime

A beneficial buff is nearly invisible in an EQ2 log: no damage, no heal, no
name, no fade line. A handful of abilities do print flavor text, twice, and
**both lines are written for everyone in chat range**:

```
Vestigial begins to play the song of the Jester.     <- the cast, caster named
The Jester inspires Rorschach.                       <- the landing, target named
```

That is the only place in the parser where ANOTHER player's cast is visible at
all (`You prepare …` is the logger's own and nothing else), which is what makes
buff uptime computable from any raider's upload rather than only the buffer's.

**Curated, not generic.** The third-person grammar exists — `<Name> begins
<flavor>.`, 822 `Tasrin begins a phantasmal enchantment.` lines in one raid —
but the flavor names an ability LINE, not an ability ("an augmentation song" is
every troubador group buff), and the first-person form is not even always a
spell: `You begin to breathe normally.`, `You begin to move faster!`, `You
begin to choke!`. A line earns an entry when its flavor identifies ONE ability
and its landing names the target. Each entry carries a `token` substring so the
regexes never run on the lines that cannot match.

Two event types come out: `buff_cast` (src = caster) and `buff` (tgt = who got
it). `_pair_buffs` gives a landing the caster that produced it — the two lines
are written independently, so the only link is time, and Census puts the cast
at 1s. Across a three-troubador log that left **590 of 596 landings with
exactly one candidate caster and none ambiguous**; when two casters do fall in
one window the landing keeps NO source, because picking one would invent
attribution that reads as measured.

**Blast radius is deliberately nil.** `statsroll` ignores both types (the
if/elif chain has no branch for them), and `segment_events` only ever opens or
extends a segment on `damage`/`avoid` — so a troubador chain-casting between
pulls cannot merge two fights, no ability row grows, no class vote changes, and
ACT parity is untouched. The events are read by the Class tab alone.
`PARSE_VERSION` 19 is the bump that backfills them into stored sessions.

**Jester's Cap uptime** (`pipeline/classmetrics/troubador.py`) is the first
metric. Census: troubador 65, single target, +22.5–42% Reuse Speed by tier,
**30s duration on a 30s recast** (25s with the Enhance AA). Duration equals
recast, so ~100% on one target is the ceiling and the number measures chain
discipline rather than timing. Coverage is the UNION of the applications'
windows, not their sum — an early refresh extends the buff, it does not add a
second one — clipped to each fight and cut short by the target's death (a buff
does not survive it and a rez does not bring it back). Applications are read
with a one-duration lookback (`Ctx.events_around`), because a cap landed during
the pull covers the opening of the fight and belongs to no encounter;
the window is bounded to each selected fight's own run-up rather than opened to
the session, since authorization here is per encounter.

On the real 2026-08-03 raid the parser finds 829 casts (Vestigial 781,
Piedpipper 48) and 820 landings across 12 targets, 816 of them credited.

Still open, and stated in the metric's `blurb` rather than in a doc: a cast
out of chat range is not logged at all, so every count is a floor.

### Perfection of the Maestro — a metric with no line at all

PotM prints nothing: no cast line, no landing line, no fade. All three were
looked for. The only `augmentation song` casts in a raid night are the
concentration buffs (77 across a five-week log, **none** within 35s of a PotM
window), and the `An augmentation song affects X` landings name mobs buffing
themselves. What gives it away is its PROC — Census says PotM casts *Precise
Note* on a hostile spell cast, and exactly one spell in the game casts Precise
Note. **A Precise Note is proof its caster had PotM that second**, and it is
the only proof there is. 35,452 of them were already in the events table, so
this metric needed no parser change.

Everything it reports is therefore a floor, and the two constants are measured
rather than assumed:

- **`WINDOW_S` = 30.** Census says 20s, Enhance: PotM adds 10, and the longest
  proven run across the reference raids is 31s — consistent, and it rules out
  reading Census's base row as the answer.
- **`JOIN_GAP_S` = 3.** How far apart two procs can be and still count as one
  covered stretch. Across 35,339 stored procs, 95% of consecutive gaps inside a
  window are ≤3s, and 3s is the largest join that does not start inventing
  coverage: runs longer than the buff can possibly last are 0.5% of runs at a
  3s join and **6.8% at 8s**. A generous tolerance does not find more coverage
  — it manufactures the overlap below, which is how the first cut of this
  metric reported 182 wasted seconds that were not there.

`Windows` counts CASTS, not stretches — a proc more than one duration after the
window opened cannot belong to it, while counting stretches would count how
choppy someone's casting was (a caster who pauses twice inside one window has
three stretches and one buff).

**Double-covered** is the RoK question asked early. One troubador cannot chain
PotM (90s recast, 30s buff), so a covered stretch longer than one window took
more than one cast, and the excess is buff paid for twice. Group-scoped in EoF
that is a rarity — 26s in a ten-fight Vyemm run, nothing at all in most — but
when the buff goes raid-wide next expansion it becomes the direct measure of a
second troubador's cast landing on someone already buffed. The arithmetic is
the same in both eras, so there is no era switch: the column is quiet today
because the waste is not happening yet.

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
  footer instead of immediately hijacking the panel — comparing is a deliberate
  second click. The same bar serves the ability breakdown, so "what fraction of
  my parse is my priority spells" is a few checkboxes.
- **Click reads, tick compares** (`ZoneRun.focusActor` vs `toggleCmp`). A click
  on a raider's row REPLACES what the drilldown column is showing; only the
  checkbox adds a second parse beside the first. They were the same gesture for
  a while — a click ticked the box — and reading down the table three names deep
  left three quarter-width parses side by side when what was wanted was the
  third one. Clicking the raider already open closes the panel, so a click still
  undoes itself, and mob/pet rows (which have no checkbox) stay plain
  drilldowns on `?actor`.
- **A comparison is a ROW, and it scrolls sideways** (`.cmpraiders`, base.css).
  The raider boxes never wrap: past two or three they run off the right-hand
  edge and take a horizontal scrollbar with them. Wrapping the third box under
  the first read as a grid rather than as parses lined up, and it doubled the
  panel column's height — which, with the rail and the raid table stacked in
  the other column of the same grid, silently pushed the raid table an inch
  down the page. `.workspace.withpanel` also pins that to `grid-template-rows:
  auto 1fr` so a tall panel grows the table's row, never the rail's.
- **Rank coloring** (`stats.rankScale`/`rankColor`/`rankTitle`, applied through
  `SortableTable`'s `cellStyle` / `cellTitle` hooks): a row's PLACE among the
  same-role raiders on screen, mixed into the text with `color-mix`, with the
  standing spelled out in the cell's tooltip ("3rd of 7 healers").

  It has been wrong twice. Hard terciles called the bottom third of the raid red
  even when the whole field was within a point of each other — exactly what crit
  becomes in later expansions. Distance-from-median fixed that and introduced a
  worse problem: the size of the gap and the size of the group both moved the
  color, and each row was measured against ITS OWN role's median, so one column
  carried up to four yardsticks at once. On the Minion of Evil fight that put a
  red 9,662 DPS (necromancer, against a 10,630 DPS-peer median) two rows above a
  green 1,868 (defiler, against a 981 healer median), with nothing on screen
  saying why. Position is the one thing a reader can verify against the column
  they are already looking at.

  **A row with no role gets no color**, and neither does a group under
  `MIN_PEERS`. The old fallback — borrow the whole raid's median — is what made
  the mixing invisible: a third of the roster has no class (Census covers about
  half the ability names) and tanks are usually fewer than four, so most of the
  uncomparable rows were quietly on the raid-wide scale beside role-scoped
  neighbours. Better to claim nothing.
- **Decomposition** (`stats.decompose`): DPS split into activity × hit size ×
  crit × alive%, each against the best peer, naming the biggest gap — the
  difference between "you're 20% behind" and "you cast 30% less".
- **A default-hidden column is a BASELINE, not a first guess**
  (`SortableTable`, `localStorage` under `eq2adv:cols:<prefsKey>`). Stored
  prefs are TWO lists — `hidden` (what the reader turned off) and `shown` (what
  they turned back on) — and `defaultHidden` sits underneath both: hidden if
  the caller hides it by default or the reader hid it, unless the reader asked
  for it by name. One list could not express that. It REPLACED `defaultHidden`
  wholesale, so the first time anyone touched the Columns menu on a comparison
  table its whole starting layout evaporated, and any default-hidden column
  added later turned itself on for everyone who had ever dragged a header —
  the exact opposite of default-hidden. The menu's reset says **Reset to
  defaults**, because that is what it does: order and visibility both.
- **Each parse tab offers the OTHER tab's rate, folded away.** `HPS` is a
  default-hidden column on Damage and `DPS` is one on Healing (`tabHidden` in
  `ZoneRun.jsx`), each sitting immediately beside the rate it pairs with. A
  shadowknight who healed 400k while topping the parse is a fact about the
  damage tab, and reading it meant switching tabs and finding the row again.
  The layout is per tab and per browser, keyed `zonerun:<tab>` — not per run —
  so ticking it once is ticking it for every raid you open afterwards.

## ACT parity

The Zylphax the Shredder encounter matches ACT **exactly — all 25 players to
the point of damage** (`test_act_parity_zylphax` guards it). Two traps from
that round that are not parity rules but will bite again:

- **`lastrowid` after `ON CONFLICT DO NOTHING` is garbage** — it is the
  connection-wide last-insert id, from any table. Harmless on a fresh DB;
  corrupts ability attribution on ANY second session or reparse. Guard with
  `rowcount`.
- In the possessive owner slot (`Bobby's blighted horde`) the logger's name
  means the PLAYER, not the bare-name-is-pet rule — otherwise their swarm pets
  never roll up (Bobby read 17.3% light).

### The ACT model (round 2, Emerald Halls zone view, 25 players)

Diffed the full zone-wide combatant table against ACT. Each rule verified
numerically:

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
Residuals: damage-taken runs 1-3% light (Artonk -6%), suspected intercepts —
the log carries no amount for one — plus boundary seconds; and Emericant's
±6,307, which is ACT filing manastone/potion self-power as PowerDrain.
**Trailing-event trimming was tried here and REGRESSED cures/EncHPS** (ACT
keeps idle-window heals and power inside the encounter); don't re-add it. The
combat-clock gap that used to sit at the top of this list was the corpse tail,
below.

### The corpse tail

`pipeline/encounters.split_trailing_corpse` — a mob's DoT keeps ticking for a
few seconds after it dies, and the silence rule counted those ticks as combat
(the Freeport pull: ACT 28s, us 32s, same 28,634 damage, EncDPS 894.8 against
ACT's 1,022.64). ACT ends the fight at the kill and opens a new encounter for
the tick — a `[00:00]` stub 4s later.

**The rule is a SUFFIX operation and that is the whole design.** Walk back from
the end of a gap-cut segment over events that are only a dead mob still
ticking; the clock stops at the last real beat (an ally acting, a kill, or a
live mob swinging), and if the tail carries damage it becomes its own segment.
It cannot cut a chain pull in half. Everything else was measured against ACT's
Emerald Halls zone view (61 encounters, 4392s) and rejected:

| rule | encounters | clock |
| --- | --- | --- |
| ACT | 61 | 4392s |
| `>=7s` silence alone (before) | 60 | 4421s |
| cut whenever every engaged mob is dead | 132 | 4453s |
| cut at `You stop fighting.` | 149 | 4433s |
| both of those together | 120 | 4462s |
| cut at a kill the logger stopped fighting on | 83 | 4392s |
| **the corpse tail (shipped)** | **61** | **4419s** |

`You stop fighting.` is in the log and it is NOT the boundary ACT uses: cutting
on it fragments a raid, because the logger drops combat constantly while 24
other people keep swinging. A shorter silence threshold matches the count (6s
gives 61) but not the clock, and would not have fixed the Freeport pull at all
— that gap was 4s.

**Confirmed against ACT's own source** (`ACT_English_Parser.cs`, the EQ2 parser
plugin): the kill handler's `EndCombat(true)` is COMMENTED OUT, and the string
"fighting" does not appear in the file at all. ACT never ends an encounter on a
kill or on the combat-state line — boundaries come from
`ActGlobals.oFormActMain.SetEncounter(time, attacker, victim)`, i.e. ACT core's
idle timeout. So this is a behavioural match, not ACT's mechanism.

**ACT's XML exports** (`Import/Export` → XML, the ground truth to collect —
one per fight, not screenshots) then showed that the clock and the membership
are DIFFERENT questions. An encounter holds everything the timeout keeps
together, but its duration runs only to the GROUP's last action: the knotted
guardian wipe reads 40s while the mobs go on hitting bodies for 3 more seconds
(their damage still counts, only the clock stops), and the 411s Malkonis
D'Morte kill ends on the killing blow, not on the heals landing 6s later.

So `last` is the last ALLY action (ally damage/avoid, or a mob kill); mob
damage never extends the clock, and heals never did. A corpse tick opens a new
segment; a LIVE mob's swing does not (on a wipe the tail rides along). Per
combatant against those exports: Malkonis is 27 of 30 damage numbers exact with
cures exact, the wipe 24 of 26 with cures and deaths exact.

Still open, with evidence:
- **ACT starts an encounter ~3s before we do** when the pull opens with THREAT
  (Sorengail's 117,800 taunt at 21:16:42 opened ACT's Malkonis encounter; our
  first damage is at 21:16:45). Adding `threat` as a segment anchor was tried
  and moved Emerald Halls the wrong way (clock 4415 -> 4419s against ACT's
  4392), so it needs its own investigation, not a one-line change.
- **The boss's own Damage column is ~10% light** (Malkonis 1,102,897 rolled vs
  1,234,182 in our own events, ACT 1,234,857) — a `statsroll` question, not a
  segmentation one.
- **Deaths**: ACT counts a mob's death on the mob's row (Malkonis 1, a
  bloodgorger 2; we report 0), and does NOT roll another player's pet death
  into its owner (we gave Bobby/Beaux/Aros a death each for pets).

## Rezzes, revives, intercepts and the adjusted delay (schema v10)

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

## Attribution and the stats engine

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

Everything that vetoes a reclassing is a claim that the name is a PERSON, so
each one is a hole if a mob can produce it. Two found so far, both from real
logs: a boss's self-heal (`Wuoshi's Nature's Salve heals Wuoshi`), fixed by
resolving heal edges only between distinct names once `confirmed` is complete;
and **owning a swarm pet**. That one reads like proof — only players summon
dumbfires — but an encounter that holds the raid's pets prints `Enynti's
protoflame` and `Enynti's awaken grave` for the boss, and one such line
promoted Enynti to a confirmed player, which vetoed its own kill-victim
reclassing. It sat in the Mistmoore's Inner Sanctum raider table with 872k
damage and 24 people attacking it, credited with Ultraviolet Beam, Harm Touch
and Chromatic Shower (abilities it was HIT by, pooled into its class vote
across nine classes). The pet-owner rule is now applied only to names the raid
never killed.

`roster_prescan` is threaded in as the player-side authority (`refine_known_mobs
(events, logger, roster)`): a name in it is never a mob, whatever the rest of
the evidence says. It is the only player signal here a mob cannot manufacture,
and it is what still protects a mind-controlled raider — who produces a
player-credited kill line on their own name — now that the softer signals no
longer get to veto on their own.

**Bare-named summoned pets** (`refine_bare_pets`) are the mirror image of the
one-word boss: EQ2 writes a dumbfire with no owner possessive anywhere in the
file, so `petnames` can never reach it and the grammar makes it a raider.
`Viber`, `Knyi`, `Geker`, `Holmes` and `Reaper` sat in raid tables with no
class — the "?" rows. Two independent tells, either sufficient:

- **their KIT** — `Viber` cast Grisly Feedback (a necromancer Grim Sorcerer's),
  `Knyi` cast Confusion and Headache (an illusionist pet's), `Geker` cast Graven
  Vanquishing (a conjuror pet's). `ability_catalog` already knows those are
  `unit='pet'` because real pets under real owners taught it.
- **Census has never heard of them**, and neither has the log. `Holmes` only
  ever melees, so no kit gives it away, but no character by that name exists on
  the server and it never chatted, looted, joined a raid or was resurrected.

`roster` vetoes both, `known_mobs` wins outright (mobs cast pet kits too —
`Enynti` cast Grave Decay), and the row lands as `swarm_pet` with no owner,
which is the honest shape: an unowned dumbfire is not a raider and is not
anybody's damage.

Trap found while building it: `roster_prescan` matched `^<Name> receives ` as
loot, and `Shotar receives a transcendent injury!` is a DEBUFF landing on a
summoned pet. Four dumbfires were promoted to proven raiders by a combat
message, which then vetoed every demotion. Loot carries an `\aITEM` link; the
pattern now requires it.

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

Per-ACTOR AvgDelay: `encounter_actor_stats` stores `atk_swings` (offensive
damage events + avoids, self-hits excluded) and `atk_span_s` (first→last
swing); the API derives `avg_delay_s = span/(swings-1)`, which aggregates
exactly across encounters (sum of spans / sum of gaps). See also "AvgDelay adj"
below, which counts button presses instead of landings.

`/sessions/:id` (`pages/Workspace.jsx`) survives as the ACT-style per-FILE
debug view — left tree, sortable combatant table, per-actor drilldown, URL
selection (`?sel=`/`&actor=`). Everything a reader wants is on the zone-run
pages; this one exists for looking at one upload in isolation. Chain-pull
labels cap at 4 nameds (`A + B + C +N more`).

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
.venv/bin/python -m pytest backend/tests/ -q     # 261 tests incl. golden fixture
bash restart.sh && curl -s localhost:8450/api/sessions
curl -F "file=@/home/lindsay/bobby.txt" -F "character_name=Bobby" localhost:8450/api/uploads
```
