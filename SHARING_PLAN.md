# Plan: guild-tag auto-share, Sharing page restructure, two small fixes

Status: PLANNED, not started. Delete this file when everything below has shipped.
Read `CLAUDE.md` and `ARCHITECTURE.md` (Accounts/groups/sharing sections) first.
Verified against the code at schema v20 / 311 tests green (2026-08-05).

## Context

1. **Guild-tag auto-share** — a user connects one of their guild tags to a group
   they belong to: "auto-share all my uploads whose uploader character wears this
   tag with this group." Lindsay's binding decision: **only the uploader ever
   controls sharing of their raids** — this is a per-user rule matched on the
   *uploader's character's* Census guild (`roster_classes`), never the run's
   majority guild, and never a group-manager power. Viewers can't re-share
   (existing `owned_zone_run` already enforces that).
2. **Sharing page full restructure** — `/groups` is flat and incohesive; areas
   lack delineation and a selected group doesn't feel like a place. Lindsay chose
   a full restructure over a restyle.
3. **Small items** — Compare's add-parse search hides solo/group zones by
   default (toggle to show); the ZoneRun Share button gets a + glyph.

**The trap that shapes everything**: a standing-share branch has **four** query
sites, per the comment above `AUTO_SHARE_REACHES` (`backend/groups.py:64–66`):
`PERSONAL_RUN_IDS`, `shares_for_runs` (:207), `shared_via_for_runs` (:235), and
`set_run_shares`'s auto set (`routers/zoneruns_api.py` ~:258). Missing
`shares_for_runs` is a live bug, not cosmetics: ShareDialog seeds its save set
from that GET, so an unreported guild-shared group gets dropped on any unrelated
save and the server then writes a spurious `hide` — silently unsharing the run.

## Phase 1 — Backend: `guild_shares` (land green before any UI)

### 1.1 Schema (`backend/db.py`)

Add to the SCHEMA string after `character_shares` (a new table needs only
`CREATE TABLE IF NOT EXISTS` — v19 precedent, no ALTER); bump
`SCHEMA_VERSION = 21` (line 19) + a `# v21:` comment block in `init_db()`.

```sql
-- A user's standing rule: uploads I own while this character-guild tag is on
-- my uploader go to this group. Matched on the UPLOADER's character's guild
-- (roster_classes, Census-derived), never the run's majority vote — sharing
-- stays a per-user decision about their own uploads.
CREATE TABLE IF NOT EXISTS guild_shares (
  user_id INTEGER NOT NULL REFERENCES users(id),
  group_id INTEGER NOT NULL REFERENCES groups(id),
  guild_name TEXT NOT NULL COLLATE NOCASE,   -- as Census spells it
  created_ts INTEGER NOT NULL,
  since_ts INTEGER,                      -- NULL = back catalogue included
  raids_only INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, group_id, guild_name)
);
```

### 1.2 Predicate (`backend/groups.py`)

Generalize the reach condition so it stays ONE definition:

```python
def _SHARE_REACHES(alias: str) -> str:
    return f"""
    {LIVE_GROUP(f'{alias}.group_id')}
    AND ({alias}.since_ts IS NULL OR z.started_ts >= {alias}.since_ts)
    AND ({alias}.raids_only = 0 OR COALESCE(z.raider_count, 0) >= {RAID_MIN_RAIDERS})
    AND NOT EXISTS (SELECT 1 FROM run_shares h WHERE h.zone_run_id = z.id
                      AND h.group_id = {alias}.group_id AND h.mode = 'hide')
"""

AUTO_SHARE_REACHES = _SHARE_REACHES("cs")
GUILD_SHARE_REACHES = _SHARE_REACHES("gs")

# The uploader's character, resolved by NAME in Census's cache. guild_checked=1
# is the only state that matches: 0 (never asked) abstains, like the raid-tag
# vote — a share must not fire or miss while a backfill is in progress.
GUILD_SHARE_OWNER_MATCH = """
    JOIN characters oc ON oc.id = z.character_id
    JOIN guild_shares gs ON gs.user_id = oc.user_id
    JOIN roster_classes rc ON rc.name_lower = lower(oc.name)
         AND rc.world_id = oc.world_id AND rc.guild_checked = 1
         AND rc.guild_name = gs.guild_name COLLATE NOCASE
"""
```

The explicit `COLLATE NOCASE` on the comparison is REQUIRED — SQLite picks the
left operand's collation and `rc.guild_name` is BINARY; the NOCASE declared on
the `guild_shares` column alone would not make this case-insensitive.

New UNION branch in `PERSONAL_RUN_IDS` (`VISIBLE_RUN_IDS`/`SHARED_RUN_IDS`
inherit it since they derive from it):

```sql
    UNION
    SELECT z.id FROM zone_runs z
      {GUILD_SHARE_OWNER_MATCH}
      JOIN group_members m ON m.group_id = gs.group_id AND m.user_id = :uid
      WHERE {GUILD_SHARE_REACHES}
```

Also in `groups.py`:
- `shares_for_runs` (:207): third loop over `guild_shares` (same shape as the
  `character_shares` loop, `seen`-guarded, reported `mode: "share", auto: True`).
- `shared_via_for_runs` (:235): third UNION arm (viewer-side attribution).
- `set_guild_shares(conn, user_id, group_id, shares)` — replace-set for ONE
  group; pin `since_ts` to first-enabled `created_ts` exactly like
  `set_character_auto_shares` (:266), with `prev` keyed by `guild_name.lower()`
  so a case-variant resave keeps the pin; `raids_only = 0 if opts.get("group_content") else 1`.

### 1.3 Other sites

- `routers/zoneruns_api.py` `set_run_shares`: extend the `auto` set —

  ```python
  auto |= {r["group_id"] for r in conn.execute(
      f"SELECT gs.group_id FROM zone_runs z {groupsmod.GUILD_SHARE_OWNER_MATCH} "
      f"WHERE z.id=? AND {groupsmod.GUILD_SHARE_REACHES}", (run_id,))}
  ```

  and update its docstring ("Today auto-share is the only such branch" → both).
- `routers/groups_api.py` `leave_group` (:289) and `remove_member`:
  `DELETE FROM guild_shares WHERE group_id=? AND user_id=?` alongside the
  existing `character_shares` delete (both call sites; the comment about
  rejoining not silently reopening applies verbatim).
- Optional: `deleted_groups` restore blurb counts `guild_shares` too.
- No character-delete cleanup needed (rows are per user); `drop_orphan_shares`
  untouched (no run ids stored). Group delete needs nothing — soft delete +
  `LIVE_GROUP` covers it.

### 1.4 Endpoints (`routers/groups_api.py`) + `frontend/src/lib/api.js`

- `GET /api/guild-shares` (require_user) → `{guilds, characters, shares}`:
  the caller's characters LEFT JOIN `roster_classes` (per-character
  `guild_name`/`guild_checked` so the UI can say "guild not resolved yet");
  `guilds` = distinct non-NULL tags of the `guild_checked=1` rows; `shares` =
  the user's `guild_shares` rows reported as `history = since_ts IS NULL`,
  `group_content = not raids_only` (same report shape as `characters_api`).
- `PUT /api/groups/{gid}/guild-shares` — **member**-gated (`_group`, NOT
  manage: it's "share MY uploads", the same trust level as `character_shares`).
  Payload `{"shares": [{"guild_name", "history", "group_content"}]}`. 422 for a
  guild_name not worn by any of the caller's resolved characters (free text
  here would be a confusing no-op forever). Calls `set_guild_shares`.
- `api.js` (PUT wrapped in `mutate()` like `setCharacterShares`):

  ```js
  guildShares: () => req('/api/guild-shares'),
  setGroupGuildShares: (groupId, shares) => mutate(req(
    `/api/groups/${groupId}/guild-shares`, { ...json({ shares }), method: 'PUT' })),
  ```

### 1.5 Tests — NEW file `backend/tests/test_guild_shares.py`

Do NOT append to `test_sharing.py` — it's order-sensitive by design (module-
scoped world; its own last test says it must stay last). Same fixture pattern;
seed guilds straight into `roster_classes` (conftest sets
`CENSUS_AUTO_REFRESH=0`, tests never touch live Census). Cover:

1. Visibility branch: owner uploads raid night + solo zone, wears tag, connects
   tag→group → groupmate sees the raid (not the solo zone, `raids_only`
   default) with `shared_via` naming the group; stranger sees nothing.
2. `guild_checked=0` abstains (backfill in progress must not leak).
3. Case-insensitive match (tag `"Gin And Jumjum"` vs connect `"gin and jumjum"`).
4. Owner's `GET /zone-runs/{id}/shares` marks the group `auto: true`.
5. Untick writes `hide`; standing share survives; second night keeps flowing;
   re-tick restores.
6. `history: false` → back catalogue withheld, later upload arrives.
7. Leave / remove-member deletes the rows; rejoin does not reopen.
8. Groupmate `PUT /zone-runs/{id}/shares` on the shared run → 403.
9. `group_content: true` brings the solo zone.
10. Unwearable tag → 422.

### 1.6 Docs (with this phase — the set_run_shares docstring demands it)

- `ARCHITECTURE.md`: new subsection under "Accounts, groups and sharing":
  per-USER rule, uploader's character guild (never the majority vote), the four
  query sites, `guild_checked=1`-only matching, and the accepted consequences:
  a connected tag is inert until Census resolves the character, and Census
  guilds are undated so leaving the guild retroactively unshares the
  tag-shared back catalogue (read-time evaluation, nothing materialised —
  `character_shares` remains the "keep sharing regardless" tool). Update the
  visibility prose + four-site list.
- `CLAUDE.md`: rules bullet (`set_run_shares` trap now counts TWO standing
  branches) + Accounts blurb. **Mirror into `codex.md`.**

## Phase 2 — Sharing page restructure

Two screens in one `/groups` route, `.manage` scope kept.

**Overview** (no group open): pagehead → **invitations banner** (full-width
strip above everything, gold accent border-left, one row per invite with
Accept/Decline chips) → **group card grid** (`--surface-raised`, 1px
`--border`, `--radius-md`, hover via `--row-hover`; each card: name, member
count, your role, one-line sharing summary "auto-shares: Bobby, Zooey · guild
tag Gin and Jumjum" derived from `api.guildShares()` + the character shares the
page already has) → existing create/join `.toolrow` and pending-code
invite-link row, behavior verbatim.

**Group view** (card clicked or `?g=`): full-width, "← All groups" back
control, sections framed as `.gsection` blocks (surface bg, border, padding,
small-caps h3):
1. Head — name, description, "N members · you are owner".
2. Members — existing `table.data` + pending-invite ghost rows + role/remove
   chips, moved verbatim from today's `.mdpane`.
3. Invite people — existing `.inviterow` join-code + rotate/disable + username
   invite, verbatim.
4. **My sharing into this group** (new): one `.togglerow` per character
   (switch + `include past raids` / `include group content` chips — the
   AutoShare idiom), backed by the SAME `GET/PUT /characters/{id}/shares`; the
   component holds each character's FULL share list and flips only this
   group's entry so the replace-set PUT preserves other groups. Plus one
   `.togglerow` per available guild tag → `PUT /groups/{gid}/guild-shares`.
   Characters with unresolved guilds get a muted "guild not resolved yet" note.
5. Danger zone — Leave / typed-name Delete (`.confirmdel` reused), danger
   framing, at the very bottom.

**Axis decision**: the global "Automatic sharing" (character→groups) card is
DROPPED; per-character auto-share lives in the group view (group→my
characters). The guild control has no character axis, one editing surface per
fact avoids drift, and cross-group visibility survives as the overview cards'
summary line. No API changes for the character path.

**Files**:
- `frontend/src/pages/Groups.jsx` → coordinator: fetches (`api.groups()`,
  `api.characters()`, `api.guildShares()`), `?g=` read **and now write**
  (setSearchParams on open/back), banner, grid, toolrow. The
  `run()`/`busy`/refresh plumbing and the don't-auto-open-while-busy guard
  survive as-is.
- NEW `frontend/src/components/GroupPanel.jsx` — sections 1–3 and 5 (logic
  moved largely verbatim).
- NEW `frontend/src/components/GroupSharing.jsx` — section 4; AutoShare's
  switch-plus-two-chips row markup moves here as a local `ShareRow`.
- DELETE `frontend/src/components/AutoShare.jsx` (Groups.jsx was its only
  consumer).
- `frontend/src/styles/base.css` in/next to the `.manage` block (per CLAUDE.md,
  retune there, not per component): `.invitebanner`, `.groupgrid`
  (`repeat(auto-fill, minmax(260px,1fr))`), `.groupcard`, `.grouppane`,
  `.gsection`, `.dangerzone` (reuse `--danger` framing); retire
  `.mdgrid/.mdlist/.mdrow/.mdpane` once unrendered (grep first — Groups-only
  today); mirror the existing 900px responsive rule; light-theme pass.
- `ShareDialog.jsx`: "auto" label wording → "standing share" (covers both
  branches; the `auto` boolean and untick semantics are unchanged).
- Update the CLAUDE.md/codex.md "Manage pages" paragraph to describe the new
  Sharing shape.

**Preserved flows checklist**: `?g=` deep-link, join-by-code, pending-code +
copy-invite-link, invite by username, rotate/disable code, role changes,
typed-name delete, leave, the `location.state?.joined` toast from
`/join/<code>`, refetch when the group set changes.

## Phase 3 — Small items (independent, any time)

### Compare: raids-only by default

- NEW `frontend/src/lib/raids.js`:

  ```js
  // A group is six, so seven raiders is a raid — the same line groups.py draws.
  export const RAID_MIN_RAIDERS = 7
  export const isRaid = (r) => (r.raider_count || 0) >= RAID_MIN_RAIDERS
  ```

  `Home.jsx:28–31` imports these instead of its local copies.
- `Compare.jsx` `AddColumn` (:471–649): state `groupRuns` default `false`,
  lazy-init from localStorage key `eq2advanced-compare-groupruns` + write
  effect; non-skippable clause in `passes()` (:507):
  `if (!groupRuns && !isRaid(r)) return false` — it's a global toggle, not a
  facet. **Add `groupRuns` to ALL FIVE hand-listed useMemo dep arrays**
  (`results`, `zones`, `dates`, `guilds`, `players`, :523–540) or the
  dropdowns go stale. Chip under the search input, the :400 "Combine pets"
  pattern:

  ```jsx
  <label className={`chip toggle big ${groupRuns ? 'on' : ''}`}
         title={`Off, only raids (${RAID_MIN_RAIDERS}+ raiders) are offered`}>
    <input type="checkbox" checked={groupRuns}
           onChange={(e) => setGroupRuns(e.target.checked)} /> Solo/group runs
  </label>
  ```

### ZoneRun share button

`ZoneRun.jsx:1303`: `>Share<` → `>＋ Share<` (fullwidth ＋ U+FF0B, matching the
sibling `⇄ Compare` glyph weight; the app uses inline unicode, no icon system).

## Build order

1. Phase 1.1–1.3 (schema, predicate, four sites, cleanup) + tests 1–8.
2. Phase 1.4 (endpoints + api.js) + tests 9–10. Docs (1.6) land here too.
3. Phase 2 (restructure, consuming the new endpoint).
4. Phase 3 (any time).

## Verification

- `.venv/bin/python -m pytest backend/tests/ -q` — 311 existing green
  (`test_sharing.py`/`test_auth.py` are the canaries for the predicate change)
  + the new file.
- `npm --prefix frontend run build` clean.
- Manual on dev (`bash restart.sh`; **no ship, no deploy** per CLAUDE.md):
  fresh-DB AND existing-DB boot (v20→v21 stamp, table appears); full Sharing
  page flow incl. `?g=`; connect a real guild tag (dev DB has real
  roster_classes guilds), second account in the group sees the tagged upload
  on `/`; untick one night in ShareDialog → hide sticks while other nights
  flow; save an UNRELATED ShareDialog change on a guild-shared run → nothing
  gets hidden (the four-site regression); Compare picker defaults to raids,
  chip restores group runs, persists across reload; `＋ Share` beside
  `⇄ Compare`; light-theme pass over the new `.manage` styles.
