# eq2advanced — Accounts, groups, sharing and redaction

Part of the architecture reference. Index: `ARCHITECTURE.md`.

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

**`/characters` is off the nav and must not be linked** — an upload derives
the character from the FILE NAME, so there is nothing a user needs the page
for; it survives only as a routed URL.

### Admin runs the site, it does not read the site

`role='admin'` is OPERATIONAL. It is absent from every visibility decision in
`security.py` — an admin gets 404 on a stranger's run, `/encounters/agg`,
`/timeline`, `/deaths`, coach report and Census snapshot, and `test_auth.py`
pins each one. `admin_api.py` serves only counts, sizes, statuses and settings;
there is no route from it into a parse, and every mutation writes `audit_log`,
which the console shows back. Support is "ask them to share the raid".

`role` now has three values (`user|curator|admin`): `curator` opens the
Abilities console and nothing else (see `docs/census-abilities.md`), and none
of the three reaches anybody's parse (`security.py`).

**The admin console** is five tabs (`?tab=`): Overview, Accounts, Content,
Feedback, Audit — each fetching its own data. Two rules it keeps. *An alert is
something broken*: `receiving` is a plugin streaming RIGHT NOW, the healthiest
state a session has, so Overview lists only errored sessions and parses stuck
past `STUCK_PARSE_S`, each with the owner and an age, and counts the live ones
separately. The old "jobs needing attention" listed every non-final session,
which made a 24-raider night read as 24 failures. *The accounts table is
searched, sorted and paged on the SERVER* (`q/sort/dir/limit/offset`,
whitelisted sort columns, grouped joins rather than five correlated subqueries
per row) — so it deliberately does NOT use `SortableTable`, which sorts in the
browser and would sort one page while claiming to sort the set. Row actions
live in a panel you open by clicking the row, not as four controls on every
row. **Feedback** (schema v25) is a bug or suggestion filed from the topnav
button on any page, carrying the path the reporter was on; admins triage it
open → planned → closed.

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
- **A reader can take a shared raid off their own list, and that is a third
  predicate rather than a branch of the rule** (`run_dismissals`, schema v31 →
  `DISMISSED_RUN_IDS`, `LISTED_RUN_IDS`). Auto-share is what makes it necessary:
  once somebody's raid week arrives whether or not you were on it, the answers
  were "read past it every night" or "leave the group". It is deliberately not
  an access rule — `visible_zone_run` and `visible_encounters` never consult it,
  so a link to a dismissed raid still opens and the reader can put it back —
  and it is not in `run_shares`, which is the OWNER's audience and would read a
  reader's row as a share at all four standing-branch query sites. `POST
  /zone-runs/{id}/dismiss` refuses your own raid (that one is `hide`, which
  reaches everybody) and covers every parse of the same night that isn't yours,
  because the list draws one row per RAID and sweeping one uploader's parse
  would leave the row standing behind somebody else's. Dismissals are carried
  through a rebuild by `carry_shares` like every other run reference, or an
  uploader adding a file to the same night would put a swept raid back with no
  edit of the reader's own. `GET /zone-runs` filters them out and reports
  `dismissed_count`; `?dismissed=1` lists them again, flagged.
  - The reason `drop_shares_for_runs` exists: `carry_shares` COPIES to the
    survivor, `foreign_keys=ON`, and the rebuild then deletes the old run — so
    every reference to a stale run has to go before that delete, not after it
    in `drop_orphan_shares`.
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
pins the invariant, and the golden-parse suite is the backstop.

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


## The Sharing page

Two cards SIDE BY SIDE (`.sharegrid`, stacked under 1180px): *Groups* on the
left — the create/join bar plus the master–detail (list, members, invite,
leave/delete under a rule), with the join code in a field-shaped box rather
than big gold type — and *Automatic sharing* on the right, holding the two
standing rules as one ruled table each: by character, and by the guild tag
Census says that character wears (`GET /api/guild-shares`,
`PUT /groups/{id}/guild-shares`, both member-gated). Both tables draw
`ShareRows` from `AutoShare.jsx`: a phone settings list, name left and switch
right, with the share's two choices as indented rows of the same shape.
Switches throughout — every row asks "is this on", which is not a checkbox's
question. Rule weight carries the structure: heavy under a section head, full
between subject blocks (six alts must read as six blocks), hairlines within
one, and a vertical rule down the subject column.

**Renaming is an edit of the pane's heading** (`PATCH /api/groups/{id}`, which
already existed and is owner-or-admin like every other manage action). The
title turns into a field with Save and cancel rather than opening a form of its
own: a group has exactly one editable field, and nothing about who is in it or
what it can see changes when the name does. Everyone in the group sees the new
name on their next read — the name lives in one row, and the raid list's group
pills read it there.

## The manage pages

Manage pages (Import / Sharing / Account / Admin) share one pattern: pagehead
→ cards with a small-caps h2 and one line of `.note` → `table.data` or ruled
rows → `.formcol` forms, all inside the `.manage` type scope. A group is never
a pill there — it is a `.settingrow`. Retune in the `.manage` block in
`base.css`, not per component, and keep the type ladder intact: h1 > card h2 >
card h3 > the subject of a row > the column labels over it. Headings own the
heading font; a row's subject does not (Cinzel names set larger than the head
above them turned the page into a stack of headlines).
