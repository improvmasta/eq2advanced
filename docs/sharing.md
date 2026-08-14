# Accounts, groups, sharing and redaction

Index: `ARCHITECTURE.md`.

## Identity

Login is `username` + password; **there is no email anywhere**. The only
self-service recovery is a security question chosen at sign-up (one of
`auth.RESET_QUESTIONS`; the answer is normalized by `auth.normalize_answer` then
PBKDF2'd like a password). A reset deletes every `auth_sessions` row for that
user, because a reset exists precisely when someone else may hold the password.
Accounts predating v9 have no question and need an admin reset.

`ratelimit.py` counts failures per identity AND per client address on login, both
reset routes, the two routes that re-check a password before changing a
credential (`/auth/password`, `/auth/security-question` — a live cookie proves
you signed in once, not that you know the password now), and the group-join code.
With no email loop and no 2FA that counter is the only thing between a weak
password and a script. The address half only works because
`siteconfig.client_ip` resolves the real visitor behind the proxies
(`docs/runtime.md`). It is in-process, so it resets on restart, and it is not a
substitute for fail2ban at the edge. Registration is deliberately not
failure-counted; the lever for sign-up abuse is the `registration_open` setting.

Migrations are guarded by table SHAPE, not `user_version` (the dev reloader can
stamp the version mid-edit). The ones that rebuild a table SQLite cannot ALTER
(`_rebuild_users`, `_rebuild_characters`, `_rebuild_sessions`,
`_rebuild_device_tokens`) preserve ids, run with `foreign_keys=OFF` and assert
`foreign_key_check`.

### Claims are not exclusive

`characters` is `UNIQUE(user_id, name, world_id)` with a NOT NULL owner. Anyone
may claim a name; each claim is that user's own row with its own logs, invisible
to the other claimants. `sessions.upload_sha256` lost its global UNIQUE for the
same reason — two raiders who were both there upload the same bytes and get one
content-addressed gzip with a session each (`idx_sessions_upload` is
(sha, character)). The file is unlinked only by the last session pointing at it.
Known duplication: two claimants of one name each drive their own Census sync.

**`/characters` is off the nav and must not be linked** — an upload derives the
character from the FILE NAME. It survives only as a routed URL.

### Admin runs the site, it does not read the site

`role='admin'` is OPERATIONAL and is absent from every visibility decision in
`security.py` — an admin gets 404 on a stranger's run, `/encounters/agg`,
`/timeline`, `/deaths`, coach report and Census snapshot, and `test_auth.py` pins
each one. `admin_api.py` serves only counts, sizes, statuses and settings; every
mutation writes `audit_log`. Support is "ask them to share the raid".

`role` is `user|curator|admin`. `curator` opens the Abilities console
(`docs/census-abilities.md`) and nothing else; none of the three reaches anybody's
parse.

**The admin console** is six tabs (`?tab=`): Overview, Visitors, Accounts,
Content, Feedback, Audit. Two rules. *An alert is something broken* — `receiving` is a
plugin streaming right now, the healthiest state a session has, so Overview lists
only errored sessions and parses stuck past `STUCK_PARSE_S`, and counts the live
ones separately. *The accounts table is searched, sorted and paged on the SERVER*
(`q/sort/dir/limit/offset`, whitelisted sort columns), so it deliberately does not
use `SortableTable`, which would sort one page while claiming to sort the set.
Row actions live in a panel opened by clicking the row. **Feedback** (v25) is a
bug or suggestion filed from the topnav on any page, carrying the reporter's
path; admins triage it open → planned → closed.

## The visibility rule (`groups.py`)

A zone run is visible to you if you own it, OR it is explicitly shared with a
group you are in, OR its character auto-shares with a group you are in and this
run is not hidden from that group, OR its uploader connected the guild tag their
character wears to a group you are in (same conditions), OR it has been
published. **That is one SQL SELECT** (`VISIBLE_RUN_IDS`, parameterised by
`:uid`) and nothing else composes it. `PERSONAL_RUN_IDS` is the same thing minus
the published branch, and `VISIBLE_RUN_IDS` is *derived from it* rather than
repeated.

- **Nothing is materialised.** `rebuild_zone_runs` re-derives run membership on
  every upload, reparse and hand edit, so a share copied onto a run would
  evaporate; read-time evaluation is also what makes leaving a group take effect
  on the next request. When runs collapse into one id the survivor inherits the
  union (`groups.carry_shares`, called from the rebuild before the stale rows are
  deleted), or a merge would silently unshare a night.
- **`hide` beats auto-share, `share` beats everything.** `set_run_shares` must
  count EVERY standing branch when deciding where to write a `hide`: it deletes
  only explicit `share` rows, so a read-time branch missing from that set
  survives the delete and the untick revokes nothing.
- **A standing branch has FOUR query sites**, listed above `_SHARE_REACHES`:
  `PERSONAL_RUN_IDS` (who can see it), `shares_for_runs` (what the owner's Share
  control shows), `shared_via_for_runs` (why the viewer can see it), and
  `set_run_shares`'s `auto` set. Missing the first is a leak; missing the last
  revokes nothing; missing `shares_for_runs` looks cosmetic and is not, because
  ShareDialog seeds its save set from that GET — an unreported group is dropped on
  the next save and the server writes it a `hide`. The reach condition itself is
  written ONCE and aliased per branch.
- **Seeing is not changing.** `owned_zone_run` guards delete/merge/split/edits, so
  a shared raid is read-only to everyone including admins and cannot be re-shared
  onward.
- **Authorization is per ENCOUNTER, not per session** (`visible_encounters`).
  `/encounters/agg|timeline|deaths` used to authorize through the session, so a
  viewer cleared for one shared run would have been cleared for every other fight
  in the same uploaded file. Sessions themselves stay strictly owner-only.
- `memo.py` needs no key change: authorization runs before the memo and the
  payload is a pure function of the already-authorized id set. **Do not memoize an
  authorization decision.**

### A reader dismissing a raid is a THIRD predicate

`run_dismissals` (schema v31) → `DISMISSED_RUN_IDS`, `LISTED_RUN_IDS`, read by
the raid list alone. Auto-share is what makes it necessary: once somebody's raid
week arrives whether or not you were on it, the alternatives were reading past it
every night or leaving the group.

It is deliberately not an access rule — `visible_zone_run` and
`visible_encounters` never consult it, so the link still opens and the reader can
put it back — and it is not in `run_shares`, which is the OWNER's audience and
would read a reader's row as a share at all four standing-branch query sites.
`POST /zone-runs/{id}/dismiss` refuses your own raid (that one is `hide`, which
reaches everybody) and covers every parse of the same night that is not yours,
because the list draws one row per RAID. Dismissals are carried through a rebuild
by `carry_shares`, or an uploader adding a file to the same night would put a
swept raid back. `GET /zone-runs` filters them out and reports `dismissed_count`;
`?dismissed=1` lists them again, flagged.

`drop_shares_for_runs` exists because `carry_shares` COPIES to the survivor,
`foreign_keys=ON`, and the rebuild then deletes the old run — so every reference
to a stale run has to go before that delete, not after it.

## Sharing is a decision for the account, not the uploader

Every branch above is set on the site by someone signed in. The ACT uploader
sends log lines and has no say in who sees the result: a device token cannot read
a parse back and cannot change its audience.

v11 built the opposite — a `session_shares` table, a `device_tokens.can_share`
scope, `share_groups` on every ingest batch, a sharing panel in ACT — and v12
dropped all of it. Written down because the reason is not visible in the code
that remains: the token lives in a config file on a gaming PC, and "who can see
my raids" should not be answerable from there. The two site controls cover the
ground — a character's standing auto-share for "always", a raid's own Share
control for one night.

### Groups

`groups` / `group_members` / `group_invites`. Three ways in, all the same
credential — an invite addressed to a username, the 6-digit join code read aloud
in voice, or an invite **link** (`/join/<code>`, carrying that same code so there
is one thing to rotate). A million codes is small, so `ratelimit` is the actual
security: on joining, and on `GET /api/groups/preview/{code}`, the
unauthenticated route the landing page uses to name a group before the visitor
has an account (deliberately thin — name, description, headcount, "are you
already in it", never the roster). The link works signed out and joins the moment
the account exists. Both rate-limit call sites dedupe their keys, since an
anonymous caller's identity *is* their address.

`GET /groups/new-code` hands the create form a free code so the code and its link
can be shown while the name is still being typed; `POST /groups` claims it,
re-minting only if it was taken in between. Nothing is reserved, so an abandoned
form burns nothing.

Membership is all that is stored; roles are owner/admin/member. Two levers after
a code gets out: **rotate** (`/code/rotate`, optionally `enabled: false`) mints a
new code and kills the old one and every link built from it while members stay
in; **remove** (`DELETE /groups/{id}/members/{uid}`, owner or group admin, never
the owner) drops access on the next request. Leaving or being removed also drops
that user's auto-shares into the group.

`DELETE /groups/{id}` requires `?confirm=<name>` matching byte for byte, case
included, enforced server-side — the delete revokes everyone's access to every
raid that reached them through the group.

**Published runs** (`public_runs`, admin-only, own raids only) are readable
without an account: read routes take `security.optional_user`, and a caller of
None makes every ownership/membership clause compare against NULL, leaving
exactly the published set. Publishing is the one action that removes a privacy
boundary, so it is admin-gated, refused on data merely shared with them, and
audited.

## Auto-share carries raids only (schema v16)

`character_shares.raids_only`. "Share my raids with the guild" is not a request
to broadcast every six-man zone, and the two readings cost differently: opting in
is one tick, noticing you have been leaking is luck. New shares are written with
`raids_only = 1`; the migration gives EXISTING rows 0 — their pre-v16 meaning —
because a migration must never revoke access somebody already has.

The read-time rule is one definition, `groups.AUTO_SHARE_REACHES` (since_ts
window AND size AND no `hide`), interpolated into all four query sites. The trap
is `set_run_shares`'s `auto` set: a share that does NOT reach a run must be
unticked with a plain delete and never a `hide`, or the row lingers and blocks a
later opt-in. `RAID_MIN_RAIDERS = 7` lives in `groups.py` — the same line the raid
list draws in the UI.

`PUT /characters/{id}/shares` takes `group_content` in its `shares` form; the bare
`group_ids` form keeps its pre-v16 meaning and stays a legacy shim.

## Sharing by guild tag (schema v21)

`guild_shares` — the second standing branch, and the one that survives an alt: a
user connects a guild tag one of their characters wears to a group they are in,
and their uploads from any character wearing it flow there.

It is a **per-USER** rule matched on the **uploader's character's** Census guild
(`roster_classes`), and three things about that are load-bearing:

- **Not the run's `guild` tag.** That tag (v20) is a majority vote of the whole
  roster — a derived property of the night, often somebody else's guild. Sharing
  is a decision about your own uploads.
- **Not a group-manager power.** `PUT /groups/{id}/guild-shares` is member-gated,
  not manage-gated: it says "share MY uploads". A group owner never gains a say
  over anybody's raids, and a viewer still cannot re-share.
- **`guild_checked = 1` only.** 0 means nobody has asked Census yet, and a share
  firing on it would leak on the strength of a backfill that has not run — or go
  missing while the queue is long. `test_unchecked_guild_abstains` pins both
  directions.

The reach condition is `AUTO_SHARE_REACHES` with a different alias
(`_SHARE_REACHES('gs')`), so the two standing branches cannot drift, and the
branch is wired into all four query sites. The `COLLATE NOCASE` on the guild-name
join is required, not decorative: SQLite takes the collation of the LEFT operand.

Two accepted consequences: **a connected tag is inert until Census resolves the
character** (the page says "guild not resolved yet" rather than offering a tag
that is not there), and **Census guilds are undated**, so leaving a guild
retroactively unshares the tag-shared back catalogue. That falls straight out of
read-time evaluation and is why `character_shares` stays the "keep sharing
regardless of what I do next" tool.

`set_guild_shares` rewrites ONE group's rules, not the user's whole set.
`since_ts` is pinned to first connection and `prev` is keyed on the lowercased
name, so re-saving a tag as Census now spells it keeps the pin.

## Deleting a group is a soft delete (schema v17)

`groups.deleted_ts`. Nothing is erased: members, invites, the join code, the
auto-shares and the run shares all stay, and an admin restores the group with one
row update (`groups.restore_group`, `GET /admin/groups` +
`POST /admin/groups/{id}/restore`).

That makes "deleted" a READ-TIME condition like every other rule in `groups.py`,
and it has to be said in every branch. The guard is written once as
`groups.LIVE_GROUP(col)` and spliced into `AUTO_SHARE_REACHES` (covering all four
auto-share sites), the `run_shares` branch of `PERSONAL_RUN_IDS`,
`shares_for_runs`, `shared_via_for_runs`, and — because membership rows survive a
delete — `is_member` / `member_role` / `my_groups` / `group_by_code`. **Miss one
and a deleted group keeps leaking.**

The join code stays on the deleted row on purpose: it cannot be joined
(`group_by_code` filters), it stays reserved by the UNIQUE index so it is never
handed to a second group, and a restore is therefore exact.

`POST /admin/users/{id}/username` renames an account. Nothing stores a username
except `users`, so it is a relabel and the account stays signed in. Same rules as
sign-up (`auth.USERNAME_RE`, lower case).

## Log size and retention

`settings.upload_max_bytes` / `storage_max_bytes` (0 = unlimited, **shipped as
0**) with per-user overrides on `users`. The cap is counted as the upload
streams, so an oversized file never finishes landing; the 413 carries
`X-Parse-Only-Allowed` so the UI can offer the deal instead of a refusal.
`retain_raw=0` parses the log and then drops the bytes
(`ingest_writer.drop_raw_if_unwanted`, only when no other session shares that
content address). The cost is enforced: those sessions are skipped by
`POST /sessions/{id}/reparse` and by `_reparse_stale`, so no future parser
improvement can reach them.

## What the server keeps of a log (`pipeline/redact.py`, schema v24)

EQ2 writes one log file for everything — `eq2log_<char>.txt` is the client log,
and combat is a minority of it. In the golden raid, hundreds of lines are speech,
and about half of those are tells, guild chat, officer chat, the named channels
and local `/say`. That, not anyone's DPS, is the sensitive content in an upload.

**Application access control cannot solve this.** `groups.py` decides who may read
a parse, but every check it makes is a check the person holding the disk can
delete. The only durable answer to "who can read my chat" is that the chat is not
there. So redaction happens at INGEST — the upload path filters the byte stream as
it arrives (`StreamRedactor`, holding a partial line between reads) and the live
path filters before writing each chunk. There is no window in which the
unredacted file exists on the server.

**It cannot change a number.** `classify_body` returns None for every chat line,
so chat produces no events. The redactor governs *exactly* that set and never
inspects anything else, so a line the parser reads is a line redaction cannot
touch. The two sets are the same objects: `redact.py` imports `CHAT_PREFIXES` /
`CHAT_RE` from `classify` rather than restating them, because a copy that drifts
is how this would quietly start eating events.
`test_parse_is_identical_with_and_without_chat` pins the invariant.

**Allowlist, not denylist.** Inside the governed set a line is dropped unless it
matches a retained channel (group, raid party, NPC dialogue). A denylist fails
open — a custom channel or a chat format from a client patch would be retained
because nobody wrote a rule against it. The one carve-out is a governed line
carrying no quoted message: no typed text means nothing private, so it stays as
fight flavor.

**`trim_to_fights` is a second pass, after the parse.** Group and raid chat
survives ingest; what survives *this* is the part said about a fight
(±`FIGHT_MARGIN_S`, 90s). It runs post-parse because it needs the encounter
windows, and it takes the UNION of windows across every session sharing those
bytes — trimming to one uploader's fights would cut chat out from under the
others. No parsed session on those bytes means "don't trim", never "trim
everything".

**The content address stays the sha256 of the ORIGINAL bytes.** It is a hash, not
a copy, and it is what makes two raiders' uploads of one night dedupe to a single
file. Hashing the redacted output would couple the dedupe key to the redaction
rules, so a rule change would fork every stored file. `src_bytes` measures what
was sent (it feeds the quota); `raw_bytes` measures what was stored.

`sessions.redacted_lines` counts what went, and the Import page shows it per
file — it turns "we strip your chat" from a claim into a number the uploader can
check. Logs stored before this existed are cleaned by
`backend/tools/redact_existing.py` (one-time, `--dry-run` first, atomic rename
per file, safe to re-run).

**What the Import page does NOT claim.** It says an admin account is not a key.
It does not claim the operator cannot read the database, because that would be
false. What limits the operator is how little is kept, which is the whole reason
the filtering is at import rather than at display.

## Counting readers (`visitors.py`, `visit_days` v37)

The /chat link was publicized, and the question that followed is the only one
this answers: how many people came, and were they strangers or accounts. The
Visitors tab is a **timeline of days written out** — one row per day, newest
first — because a chart was not what was asked for.

**A visitor is a DAY, not a person.** The stored id is `sha256(that day's salt +
address + user agent)`; the salt is minted once per day and deleted after
`SALT_KEEP_DAYS` (2). While the day is live the hash collapses somebody's page
loads into one row. Once the salt is gone the hash anchors to nothing: it cannot
be turned back into an address, and it cannot be lined up against another day's
hash for the same person.

That ceiling is deliberate and load-bearing. This site already refuses to show
who is logged in on /chat, and a visitor table that followed people across days
would be a stronger claim on a reader than the one it declined to make about a
player. The consequence is that **"unique visitors this month" cannot be
computed and must never be faked** — summing the column gives visitor-DAYS, and
the footer says so.

**A visit is somebody arriving.** It is counted in `spa.py`, at the one moment
index.html goes out: an API call never reaches that route, a static file returns
above it, and a raider clicking between tabs inside the SPA never comes back to
the server at all. Bots are filtered on user-agent — bluntly, because the
alternative is a graph made mostly of Googlebot, and a false positive costs one
uncounted visit and nothing else. The **token URLs are not readers** either
(`/overlay/*`, `/ingame/*`): those are capabilities pointed at a meter, and OBS
reloads its browser source on every scene change, so counting them would file a
stream's restarts as visitors. `siteconfig.client_ip` supplies the address,
never `request.client.host`, or the tally would be the reverse proxy's own IP
once a day forever.

**Counting never breaks a page.** `visitors.note` swallows everything and so
does its caller: a visit that cannot be recorded is a visit that did not happen.
`signed_in` and `chat` are flags on the DAY that only ever go up, so somebody who
reads /chat and then signs in is one visitor who did both.

## The public chat box (`/chat`, `pipeline/chatbus.py`)

General, LFG and Auction in three scrolling blocks built to look
like EQ2's own chat window, live at the bottom and **kept** behind a date filter
on each block, with a Stats panel under each.

**It has a door now, and the door is a PLAQUE.** *In-game chat*, between wikQ2
and eq2lexicon in the header, carrying the chat light. It is still not a nav
TAB, and the reason is the same one that kept it out of the nav entirely: the
tabs are the things you do with a log, and this is a window onto the game — the
same errand as the wiki and the lexicon, which is the row it now sits in. It is
the one plaque that points at this site, so it gets no frame and no away-arrow.

The subtitle names the server (**Wuoshi**): the channels look identical on every
server in the game, so a reader who does not already know has no way to tell
from a page of chat.

**No account needed to read it.** It used to require one, and that was the wrong
shape for what this is: the record has no user in it, every line was broadcast to
a whole server by the game, and none of the three routes reaches a parse, a
session or an account. What an account decides is who FILLS it — which is
unchanged, and is the plugin, not a form. There is no POST.

**It was a relay and is now a RECORD (v36).** Until v36 a message sat in a
bounded per-channel deque for a few hours and a restart emptied it. That made
the page a window onto this minute, and the page exists to be a window into the
game — so the three channels are written to `chat_messages` and kept for as long
as there is disk. The box opens on the archive, each block has a date filter, and
anybody signed in sees the whole record. The deque survives as the live TAIL,
which is all the SSE stream reads.

**This still did not widen redaction, and the distinction is what to keep hold
of.** `redact.py` is untouched: those three channels are still dropped before any
byte of *an uploaded log* is written, and `test_chatbus.py`
`::test_keeping_chat_did_not_widen_redaction` pins that by asserting `keep_line`
still refuses every line the box shows. What changed is that the site now keeps
its own record of the three PUBLIC channels — read out of a live batch on its way
through memory (`live.process_batch`, beside the `stored` filter) and written
inside that batch's transaction. `chat_messages` has no `user_id`, no
`character_id` and no `session_id`, on purpose: a public line belongs to the
server, not to whoever's plugin happened to relay it, and **the uploader is not
recorded**. The privacy argument is unchanged in substance — the game itself
broadcasts these three to everyone in the zone — but it is now "what everyone
already saw" rather than "we keep nothing".

The obvious "fix" here is to make redaction agree with the box. **Don't** — the
inconsistency is the design. Tells, guild chat, officer chat and /say still reach
neither, and group/raid chat goes the other way round (kept from a log, never
shown here).

**Storing it makes the channel test load-bearing, not less so.** It is now the
only thing between a private line and a permanent row, so the default-deny below
is the rule to be most careful with in this file.

**The channel test is default-deny twice over.** A line has to match the exact
`tells <Name> (<n>),` shape AND have both its name and its number in
`CHANNELS`. A private tell has no `(n)`, so no player name can impersonate a
channel; Crafting (6), an unknown number on a known name, and a channel EQ2 adds
next patch all relay nothing until somebody adds them here.

**Live only, which now also means "filed under the right night".** Backfill
batches are ignored and so is anything whose log clock is more than `MAX_LAG_S`
from the wall clock, for the same reason `live._publish_snapshot` gates:
importing March's log must not scroll March's General chat past as if it were
happening now — and must not date it tonight either. **The archive therefore
starts at the first line relayed after v36 and cannot be backfilled from
anything on this server**: uploads and raw chunks are both redacted before they
are written, so the chat that would fill it in does not exist here. The only
source is a player's own untouched `eq2log_<Character>.txt`, and reading one is
a separate decision that has not been made.

**One line, several uploaders, one message — and their clocks do not agree.**
The `(1786724295)` a log line opens with is written by each player's own EQ2
client off their own machine clock, so the same sentence arrives stamped
12:18:15 from one uploader and 12:18:16 from the next. **An exact key does not
collapse that**, which is why the dedupe is a WINDOW: same channel, speaker and
text within `DEDUPE_WINDOW_S` (20s) is one message, with
`UNIQUE(ts, ch, who, text)` left as the exact-match backstop. The hole was in
the original relay too and only became visible once the box stopped forgetting.

20s is a compromise with two failure modes and no value that avoids both: wider
eats a genuine repeat (an auction spammer reposting the same WTS is real chat),
narrower double-posts on skew. Skew past the window still double-posts, and
`MAX_LAG_S` bounds that at 120s — a machine minutes out of true is left alone
rather than paid for with a per-uploader clock model.

**A light, never a roster.** `connected` is the count of characters whose plugin
has sent inside `CONTRIB_TTL_S` (90s, recomputed on every read because nothing
publishes "they stopped"). It reaches the reader as ONE BIT and never as a list:
naming who is logged in would put a live roster of players on a page about what
the server said, which is the thing being avoided; a dot saying whether chat is
arriving is not that.

That bit is drawn twice. The header plaque carries it on every page from `GET
/api/chat/status` — one number, polled every 60s, signed out too, and a failed
poll leaves the light where it was rather than reporting red for a hiccup. The
page itself carries it on the `Chat` title and takes it from the stream instead:
green needs the SSE link up AND `connected > 0`, because to a reader "the server
stopped talking to this tab" and "nobody in the game is relaying" are the same
fact — nothing is arriving. Neither dot is colour alone (both carry the state in
a `title`).

**"Disconnected" is a LINE, not an empty state.** It renders where the next
message would have been, in the live view only, under everything already said. A
pinned day is not a feed and cannot disconnect from anything. The distinction
matters because red here is the normal state of a channel at 4am rather than a
fault, and the archive above it is still perfectly good reading — which is the
whole reason the old blank-page "disconnected" was removed.

The reads are `GET /api/chat/recent` (the newest of the archive, the stream
cursor and the span the date pickers may reach over), `GET
/api/chat/history?ch&start&end` (one channel, one window, capped at 31 days),
`GET /api/chat/status` (the light) and `GET /api/chat/stream?since=<seq>` with
the same doorbell shape as `livebus`.

### Recruiting rail

The right rail collects the newest current General-chat pitch from each guild
over the last 3 days. It is a directory made from chat, not a fourth EQ2
channel, so it sits outside the replica and wears the site's tokens. Repeated
copies of one guild's macro collapse to its newest copy; lines from the same
speaker stamped in the same second stay together, because EQ2 gives a pasted
multi-line advert one timestamp.

Guild recognition is deliberately conservative and pinned to the forms seen in
the archive: decorated leading names (`<Super Best Friends>`, `(Ctrl Alt
Defeat)`, `[ Malazan ]`) plus recruiting/joining/invite language, or a bare name
before sentence grammar such as `Revelation Is Looking ... Raid Force` and
`Court of Thorns is a guild ... join`. The `is/are` boundary is case-insensitive:
enthusiastic capitalization does not turn "Is Looking" into part of a guild
name. An uncertain line stays ordinary text. A pitch is folded after 320 visible
characters (about five rail lines) with an explicit `… more` / `less` control;
opening it restores every line and link. Recognized names become
`k='guild'` parts and link to eq2lexicon's `/guild/<name>` profile; the endpoint
is `GET /api/chat/recruiting` and remains public like the messages it groups.

## The Stats panel (`GET /api/chat/stats`, `chatbus.stats`)

Under each box — **outside** the frame, because the window is a replica of EQ2
and the panel is this site talking about what is in it. Outside the replica it
wears the site's own tokens; dressing it in the game's palette would claim EQ2
drew a window it has never had.

**There are no charts in it.** No bars, no columns, no sparkline: leaderboards
are numbered lists and the clock profile is the top three two-hour windows
written out as text. The one thing drawn rather than written is the word cloud,
where size is the count and the only encoding — a cloud that is also a colour
ramp says the same thing twice.

**The summary at the top is a list, not a sentence.** It reads as the first of
the boards rather than as prose above them — the same rules, labels left,
figures right, only without the rank column, because a figure keyed to a label
is not a placing. All-time it is chat lines today, chat lines a day on average
(over every day in the span, silent ones included — dividing by the days that
HAVE chat reports the busy-night rate as the ordinary one), the busiest day and
the one-time chatters. A box pinned to a day cannot answer "today" or "average"
and does not pretend to: it says lines, voices and one-time chatters instead.

What it counts, all of it arithmetic over the four columns the table has: the
summary above; most talkative; biggest **spammers**, ranked on REPEATS (messages minus distinct
texts, ≥ `SPAM_MIN`) rather than on a ratio, so the account reposting the same
WTS all night outranks somebody who said "wb" twice; **fame**, being named by
somebody else, counted once per message and never for naming yourself; the peak
two-hour windows; and the word cloud, which carries no heading at all — the
words say what they are, and a rule is all the separation it wants from the
lists above it.

Three things the counting deliberately does NOT do. A **link label is not a
word** — the cloud is built from plain runs only (`_plain`), or an Auction cloud
would be one long shout of whatever was linked most, which nobody said. A
**speaker name is not a word** either; a person is the fame board. And the
stopword list drops function words ONLY — `wts`, `lfg` and `pst` are what these
channels are for and are the most interesting thing a cloud can report.

**The panel follows the box's window.** A pinned day counts that day; a live box
counts all time, because the live state is a few hundred lines of tail and "who
talked most in the last 400 messages" is not a question anybody has. `hours`
comes back as `[unix hour, count]` pairs and the SERVER bins nothing — folding
those into a time of day or a day is a question about where the reader is
sitting, so `Chat.jsx` does it, which is also the only way the two days a year
that are not 24 hours long come out right.

Nothing redraws on a new message: the panel is a reading of a window, taken when
you opened it, and a leaderboard that reshuffles while you read it is worse than
one a minute old. Server-side the answer is cached against the newest row id —
while that has not moved, no line has landed in any channel and every window's
answer is still exactly right.

**A leaderboard implies completeness this archive does not have**, so the panel
prints the caveat under the numbers: it counts relayed logs, and a quiet stretch
can mean nobody was uploading rather than nobody talking.

**A line is split on the server, drawn on the client.** `_parts` turns the
stored text into text runs, `url` parts and link labels, so the page never
parses a chat line — it only decides how each piece looks. Two of those pieces
are new:

- **An item link keeps its id.** The markup comes off (nobody reads
  `\aITEM 212446172 …`) but the FIRST number is the Census item id written
  signed, exactly as `pipeline/loot.py` reads it off a chest line — so an item
  somebody links in Auction opens **the same examine card a chest drop does**.
  One card component (`components/ItemCard.jsx`, shared with `LootPanel`) and
  one server-side definition of a card's fields (`items.display`); a second
  examine window drawn slightly differently would be worse than no second one.
  The loot row's `mob` line is absent on a chat card, because nobody dropped it.
- **A typed address becomes a link.** EQ2 leaves URLs as plain text, so this is
  the page doing what the game does not. Trailing sentence punctuation is handed
  back to the text (`see eq2advanced.com.` ends in a full stop that is not part
  of the address), the address is shown AS TYPED — nothing shortens or
  prettifies it, because the one thing a reader needs from a link in a public
  channel is to see where it goes — and it carries
  `noopener noreferrer nofollow`.

**Resolving a linked item is the WORKER's job, and asking for its card is a
READ.** `items.ensure` is network-bound, so the ingest thread hands the ids to a
daemon worker with its own connection (`chatbus._items_loop`, the same shape and
reasoning as `live._roster_worker`) and returns; failing costs a chat line its
hover card and nothing else. `GET /api/items/{id}/card` never resolves — an
unlooked-up id answers `null` and the link stays a link. The Loot tab does not
call it at all: it knows its whole table at once and is handed every card with
its rows, whereas a chat line can arrive over the stream long after the page
loaded, so the link fetches its own card on first hover and the browser keeps it.
Items are reference data about the GAME, so an id looked up because somebody
linked it in Auction is looked up for good, and the raid that later drops it asks
nothing.

**The window a date means is the BROWSER's.** The server keeps unix seconds and
has no business guessing which midnight a reader meant, so `Chat.jsx` builds
local-midnight-to-local-midnight bounds (`dayWindow`, via `new Date(y, m, d+1)`
so the two odd days a year still work) and sends them as numbers. The date and
the per-box filter are both per BLOCK, not per page: three boxes are three
different questions. Picking a day pins that block to the record; clearing it
drops back onto the live tail, which kept arriving underneath.

Each block can be collapsed independently from its EQ2-style title strip. On
wide screens a collapsed block becomes a narrow fixed column and the open
blocks divide the reclaimed width; on narrow screens it becomes a short row.
The collapsed channel keys are browser-local preferences, so no account or
server state is involved and the layout survives a refresh.

Auction lines whose message text begins with `WTS` or `WTB` keep those words
and colour only that leading token red/sell or green/buy. General and LFG
are never trade-coloured. The classifier only uses the beginning of the
message, so ordinary Auction conversation mentioning those initialisms is not
recoloured, and the meaning never depends on colour alone.

The block title already says which channel a line came from, so rows render as
`[time] Player: "message"` rather than repeating `tells Channel (number)`.
Only the quoted speech uses the brighter `--eq2-speech`; timestamps, player
names and window controls retain their quieter blues.

The page-level `Spam filter` preference filters powerleveling ads from live tails and
per-channel archived days in the browser. Its deliberately narrow patterns
cover the observed `powerlevel`, `1-70 PL`, `power 1-70 lvl exp`, and `PL group`
forms without treating ordinary mentions of levels or experience as spam. The
choice is browser-local and persists across refreshes.

The window is a REPLICA — literal colours scoped to `.eq2win` in `base.css`
rather than site tokens, one `--eq2-chat` shared by all three blocks. It has a
SECOND set of them for light mode (`:root[data-theme="light"] .eq2win`, at the
end of that section): same window, same tan frame, same blue channel text taken
down onto parchment. Only the ink and the paper swap; a black slab on a light
page read as broken rather than as EQ2. Retune a colour in BOTH places. The
examine card is the exception and stays black in either theme — that is EQ2i's
item box, and EQ2i has no light one.

## The Sharing page

Two cards side by side (`.sharegrid`, stacked under 1180px): *Groups* on the left
(create/join bar plus master–detail: list, members, invite, leave/delete) and
*Automatic sharing* on the right, holding the two standing rules as one ruled
table each — by character, and by the guild tag Census says that character wears
(`GET /api/guild-shares`, `PUT /groups/{id}/guild-shares`, both member-gated).
Both tables draw `ShareRows` from `AutoShare.jsx`: a phone settings list, name
left and switch right, with the share's two choices as indented rows.

Switches throughout — every row asks "is this on", which is not a checkbox's
question. Rule weight carries the structure: heavy under a section head, full
between subject blocks, hairlines within one, and a vertical rule down the
subject column.

**Renaming is an edit of the pane's heading** (`PATCH /api/groups/{id}`,
owner-or-admin). The title turns into a field with Save and cancel rather than
opening a form of its own.

## The manage pages

Import / Sharing / Account / Admin share one pattern: pagehead → cards with a
small-caps h2 and one line of `.note` → `table.data` or ruled rows → `.formcol`
forms, all inside the `.manage` type scope. A group is never a pill there — it is
a `.settingrow`. **Retune in the `.manage` block in `base.css`, not per
component**, and keep the type ladder intact: h1 > card h2 > card h3 > the subject
of a row > the column labels over it. Headings own the heading font; a row's
subject does not.
