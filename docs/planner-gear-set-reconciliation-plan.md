# Gear Planner saved plans and acquisition reconciliation

Status: **complete — 2026-08-22**

This document is the implementation handoff for the Gear Planner character and
Gear Set lifecycle. It records the decisions already made so a fresh session can
start from implementation instead of repeating the audit.

The existing compact Gear Sets controls remain. This work changes the state
behind them and fixes character identity/navigation; it is not a visual redesign.

Implementation result: canonical public-character identity now spans Census,
Lexicon, Recent searches, browser workspaces, saved-set ownership, and completion
ledgers. Saved sets remain target-only drafts over the live equipped baseline;
exact observed item/adornment identities move targets into a reversible Completed
view without dirtying the saved plan or removing its evidence. Schema v52 adds
the private additive ledger and lossless legacy-key collision recovery. Public
lookup cancellation/generation guards, explicit saved-set deletion, guarded
guest adoption, and workspace-v3 base restoration close the lifecycle races
recorded below. The follow-up Gear Set UX pass removed the competing `+` action:
one Save menu beside the load picker now exposes update, replace, and create as
separate destinations, with naming deferred until Create new is chosen.

Verification on completion: 1,022 backend tests and 33 frontend lifecycle/unit
tests passed, the Vite production bundle rebuilt, `git diff --check` passed,
and the local public-character/API smoke returned the canonical Bobby identity
while both private ledger routes rejected a signed-out request.

## Outcome

The Planner has three distinct layers:

1. **Equipped gear** is a live, floating Census/Lexicon baseline.
2. **A saved plan** is a durable list of gear and set-adornment targets for one
   public character and one reader/account.
3. **Remaining work** is derived by subtracting gear already observed on the
   character from the saved targets.

Obtaining gear must advance the plan. After a target has been observed equipped
in a refreshed character snapshot, it no longer replaces the equipped row, no
longer contributes a projected delta, and no longer appears in the active
Outline. It remains visible in a collapsed Completed section so the progression
is explainable and recoverable.

A plan is therefore not a frozen copy of every equipped slot. Untargeted slots
continue to float with the character's current gear. Do not turn Gear Sets into
full equipment snapshots.

## Decisions that must remain true

- Public character identity is a filing key, never proof of ownership. Different
  readers may keep different plans for the same character.
- Guests retain character-scoped browser persistence. Signed-in readers retain
  account persistence, with browser storage as the offline/failure fallback.
- Keep five renameable saved slots per reader and public character.
- Choosing/loading, saving, renaming, deleting, viewing contents, and resetting
  remain distinct actions in the stable Gear Sets bar.
- Equipped gear floats underneath the plan. A saved plan stores targets and
  acquisition state, not an obsolete copy of untouched equipped gear.
- Exact Census item ids are the primary reconciliation identity. Never infer
  acquisition from similar stats or a fuzzy name match.
- Paired positions are physical alternatives: a planned ring, ear, wrist, or
  charm is satisfied if the exact item is observed in either compatible slot.
- A two-handed Primary continues to consume Secondary in projections.
- Automatic completion is progression, not a user edit. It must not make a clean
  named plan appear dirty.
- The Planner only observes equipped gear today. Automatic completion happens
  after an item has been equipped and a refreshed character snapshot contains
  it; bags and bank contents cannot be claimed as known.
- Preserve compact anchored controls while keeping actions unambiguous: the
  picker loads, one adjacent Save menu updates/replaces/creates, and `Contents`,
  `Rename`, `Delete`, and `Reset to Equipped` remain distinct operations.
- No deployment is part of this implementation.

## Confirmed current behavior and faults

### Character entry

- Signed-out plain `/plan` loads no character.
- Signed-in plain `/plan` reads `eq2adv:plan:character` from localStorage. If it
  is absent or invalid, `/api/characters` supplies the first character in
  alphabetical order.
- `/plan?character=<name>` performs a public lookup and wins over the account
  character after it resolves.
- Public searches do not become the default for a later plain `/plan`; the last
  account character id remains in localStorage.

Faults:

- Removing `?character=` does not clear the currently loaded public character.
- Public lookup requests have no cancellation/generation guard. An older response
  can overwrite a newer URL or account-picker choice.
- While a lookup is pending or after it fails, the old character can remain on
  screen under a URL naming a different character.
- The public API currently returns display names such as `Bobby (Wuoshi)`.
  Recent-character storage reuses that value as a lookup name, but the lookup
  endpoint accepts `Bobby`; the resulting Recent searches shortcut returns 404.

### Character identity

The browser currently derives the plan owner key as Census id when present and
display name otherwise. A Lexicon fallback later replaced by Census can therefore
change the key and split one character's drafts/saved sets between two folders.
Display-name changes can also produce invalid recent lookup values.

### Workspace and named sets

- A character switch clears `activeSavedSetSlot`.
- The character's last local shortlist is then restored automatically.
- The five named saved slots are fetched, but no slot is selected or loaded.
- Loading a named set writes its contents into the same local shortlist.

Consequently, refresh can restore the exact contents of a named set while the
chooser says `Choose set` and the UI calls it unsaved. The data survived, but the
base-set identity did not.

### Acquisition

Planned items currently win over equipped items whenever `active[slot]` names a
shortlisted page. No code compares a plan target's `census_id` with refreshed
equipped `item_id` values. An acquired target therefore remains active, remains
in projected replacement math, and remains in the Outline.

### Saved-set owners and deletion

Deleting a set writes a row with a null payload. The saved-set owner query groups
all rows, including null payloads, and groups by both owner key and owner name.
This can leave empty character folders and duplicate one key under old/new display
names.

## Target state model

### Canonical public character identity

The backend, not React, supplies these separate facts in every Planner character
summary:

```json
{
  "planner_key": "wuoshi:bobby",
  "lookup_name": "Bobby",
  "display_name": "Bobby (Wuoshi)"
}
```

- `planner_key` is stable across Census and Lexicon provenance. Use normalized
  world plus canonical lookup name, not a source-dependent id.
- `lookup_name` is the exact round-trippable input to `/api/plan/character` and
  the value stored in Recent searches.
- `display_name` is presentation only.
- Continue exposing source/provenance and Census id separately. Do not rename a
  Lexicon value to `census_id` merely to stabilize storage.

All shortlist, workspace, saved-set, recent-character, completion, and Outline
state uses `planner_key`.

### Per-character workspace

Replace the bare shortlist localStorage value with a versioned workspace:

```json
{
  "version": 3,
  "owner": {
    "key": "wuoshi:bobby",
    "lookup_name": "Bobby",
    "display_name": "Bobby (Wuoshi)",
    "className": "necromancer",
    "world": "Wuoshi"
  },
  "base": {
    "kind": "equipped | saved | draft",
    "slot": 1,
    "saved_updated_ts": 0
  },
  "shortlist": {
    "items": [],
    "sets": [],
    "active": {},
    "set_slots": {},
    "adorn_slots": {}
  }
}
```

Rules:

- `equipped` means no acquisition targets or planned socket changes.
- `saved` names the saved slot from which the working state was loaded.
- `draft` is browser-persisted work not based on a named slot.
- Restore the base pointer with the shortlist. Once saved slots arrive, validate
  the pointer against slot existence and timestamp/payload. If it still matches,
  show the saved name cleanly. If it differs, retain the contents as a restored
  draft and explain why.
- A v2 bare shortlist migrates to a v3 `draft`; do not guess which saved slot it
  came from. Exact payload equality may be used only to offer/recover an obvious
  association, choosing the newest matching slot when duplicates exist.
- UI state must say one of `Equipped`, `Draft restored`, `<Set name>`, or
  `<Set name> - changes not saved`. Do not label browser-persisted work merely
  `Unsaved` without saying that it is not committed to a named Gear Set.

### Obtained-item ledger

Completion belongs to the reader plus public character, not to one saved slot.
If two plans target the same item, observing it once satisfies it in both.

Use an account-backed table for signed-in readers and character-keyed
localStorage for guests. Suggested table shape:

```sql
CREATE TABLE planner_obtained_items (
  user_id INTEGER NOT NULL REFERENCES users(id),
  owner_key TEXT NOT NULL,
  item_key TEXT NOT NULL,
  item_name TEXT NOT NULL,
  first_seen_ts INTEGER NOT NULL,
  last_seen_ts INTEGER NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (user_id, owner_key, item_key)
);
```

`item_key` is `census:<unsigned id>` when the catalog and equipped record have an
id. A strict canonical page/name fallback may be used only when an id genuinely
does not exist; flag that provenance so it can later be replaced by an exact id.

The ledger is additive: an item once observed equipped remains obtained if later
unequipped. This models acquisition rather than current wear. Keep timestamps and
source so Completed can say why it was satisfied.

Suggested authenticated API:

- `GET /api/plan/obtained-items?owner_key=...`
- `POST /api/plan/obtained-items/reconcile` with canonical owner facts and the
  bounded set of item/adornment identities observed in the current snapshot.

Guest reconciliation reads/writes the equivalent localStorage record. Never mix
guest/account rows across character keys. On sign-in, adopt guest observations
only into the same canonical character folder.

Do not send the complete Census character document to the reconciliation route;
send only bounded canonical identities already present in the Planner response.

### Derived remaining plan

Keep saved targets immutable during automatic reconciliation. Derive:

```text
remaining targets = saved/working targets - obtained identities
completed targets = saved/working targets intersect obtained identities
```

Use `remaining targets` for:

- active planned item selection;
- projected stat deltas;
- changed-slot styling;
- Outline item query parameters;
- the working Contents count.

Use `completed targets` for a collapsed Completed disclosure carrying item name,
planned slot, and first-observed time. Removing a target from the plan is still an
explicit edit; completing it is not.

If the active target for a slot is completed, the gear window naturally falls
back to the refreshed equipped item. Other explicitly checked alternatives remain
independent targets until individually obtained or unchecked. This preserves the
current rule that every checked candidate belongs to the linked Outline.

For paired slots, compare identity across the compatible slot family before
declaring the target remaining. Do not require the item to appear in the exact
left/right position originally chosen.

### Set-adornment reconciliation

Apply the same model to installed adornments after ordinary gear is correct:

- Include exact installed adornment ids in the observed reconciliation payload.
- Match a tracked exact set piece by adornment id first and strict piece name
  only when no id exists.
- A broad tracked set remains a goal for its still-unobtained pieces; do not mark
  the entire set complete because one turquoise piece was observed.
- The Worn Sets count continues to describe what is currently in the equipment
  window. Acquisition completion is a separate fact and must not be inferred
  from reaching a bonus threshold.
- Outline generation must receive only still-needed exact pieces, or accept an
  explicit bounded `obtained_item` list and filter them server-side.

## Implementation phases

### Phase 0 - characterization tests

Add failing tests before changing behavior:

- Plain signed-out, plain signed-in, remembered valid id, invalid id fallback.
- Direct `?character=Bobby`, query removal, back/forward between two names.
- Old lookup resolving after a newer lookup/account selection.
- Public API `lookup_name` round trip when `display_name` contains `(Wuoshi)`.
- Census and Lexicon summaries produce the same `planner_key`.
- Refresh after loading a named set retains the set association.
- A legacy bare shortlist restores as a draft.
- A newly equipped exact target leaves remaining work and enters Completed.
- A planned ring is satisfied in either finger slot.
- An unequipped-but-previously-observed item remains completed.
- Automatic completion does not set saved-set dirty state.
- Completed items are absent from Outline query parameters.
- Deleting the last real set removes the character from saved-set owners.

The current helper-only tests are insufficient; add component/page state tests
around Planner lifecycle effects. Extract pure reducers/selectors where that
makes these cases deterministic, but do not create a second Planner state system.

### Phase 1 - canonical identity and navigation

Backend:

- Add `planner_key`, `lookup_name`, and `display_name` to owned, cached Census,
  owned-snapshot fallback, and Lexicon character summaries.
- Normalize the public lookup input and make returned `lookup_name` round-trip.

Frontend:

- Replace `ownerOf()` inference with the backend-supplied canonical identity.
- Store recent `lookup_name`, never display text.
- Treat `character` query state as authoritative.
- Add an AbortController or monotonically increasing request generation. Query
  removal/account selection must invalidate any public lookup in flight.
- During lookup, show the requested identity as loading; on failure, do not leave
  an unrelated old toon presented under the requested URL.

Migration:

- Migrate numeric/display-derived owner keys to `world:lookup_name` for saved
  sets, local workspaces, recent characters, and guest/account local caches.
- Resolve `(user, new owner key, slot)` collisions deterministically: newest
  non-null payload wins, while displaced non-identical payloads must be retained
  for explicit recovery rather than silently dropped.
- Keep a bounded recovery copy of legacy server rows until the migration has
  been verified.

### Phase 2 - workspace/set identity

- Add v3 workspace helpers and migration in `planSavedSets.js` or a narrowly
  named adjacent module.
- Persist base kind/slot with the shortlist.
- Validate the base after server/local saved slots merge.
- Preserve clean named-set identity across refresh and character switches.
- Keep the existing replacement confirmation when leaving a modified named set
  or draft.
- Make status copy distinguish browser persistence from named/account saves.
- Ensure Reset to Equipped changes only the working equipment/socket layer and
  retains checked acquisition targets, matching the current documented rule.
  `Uncheck all` remains the explicit way to clear targets/Outline.

### Phase 3 - obtained-item persistence and reconciliation

Backend:

- Add the obtained-items table in the next shape-guarded schema revision.
- Add bounded authenticated read/reconcile routes.
- Validate canonical owner keys, item-key format, name length, item count, and
  payload size.
- Account rows remain private to `require_user`.

Frontend:

- Extract observed exact gear identities from the loaded character response.
- Merge observations into guest/account completion state after a successful
  character load; never run reconciliation for a failed/stale lookup response.
- Add pure selectors for remaining/completed targets and effective active slots.
- Feed only remaining active targets into loadout projection and Outline.
- Add the compact Completed disclosure to Gear Set Contents/Outline without
  expanding the stable header controls.
- Show a short status when refresh completes targets, for example
  `Plan updated - 2 equipped targets completed`.

### Phase 4 - set-adornment completion

- Extend observations to exact installed adornment identities.
- Reconcile exact turquoise pieces without conflating current Worn Sets bonus
  counts and permanent acquisition state.
- Filter completed exact pieces from set acquisition sources while preserving
  remaining set pieces and bonus ladder display.
- Cover moved adornments and paired weapon positions.

### Phase 5 - saved-set cleanup and merge hardening

- Add an explicit DELETE operation or make null-payload writes remove the row.
- Make `/plan/saved-set-owners` include only characters with at least one real
  payload and return one latest canonical name per key.
- Make recent-character merging timestamp-aware; an older duplicate must not
  overwrite fresher lookup/class/level facts.
- On saved-set GET failure, merge same-character guest and account-local data
  instead of hiding guest saves while signed in.
- Guard initial GET/adoption against racing a user save. Do not let a stale fetch
  overwrite a newer local/server write.
- Surface colliding guest/account slots for explicit import/replace rather than
  silently making one inaccessible.

### Phase 6 - documentation and verification

- Update `docs/planner.md` only after behavior exists, keeping this plan as the
  historical implementation checklist or replacing its status with Complete.
- Update schema version/reference documentation with the actual revision used.
- Run focused frontend lifecycle tests, all frontend tests, saved-set/character
  backend tests, then the full backend suite in proportion to the touched scope.
- Build `frontend/dist` and verify obsolete status/lookup strings are absent.
- Run `git diff --check` and a local `/plan` smoke covering signed-out, signed-in,
  direct public URL, refresh, character switch, completion, and set reload.
- Do not ship or deploy unless separately requested.

## Primary files

Frontend:

- `frontend/src/pages/Planner.jsx`
- `frontend/src/components/PlanLoadout.jsx`
- `frontend/src/components/PlanOutline.jsx`
- `frontend/src/lib/planSavedSets.js`
- `frontend/src/lib/planAdornments.js`
- `frontend/src/lib/api.js`
- new/focused frontend lifecycle tests

Backend:

- `backend/census/sync.py`
- `backend/planner/saved_sets.py`
- `backend/routers/planner_api.py`
- `backend/db.py`
- `backend/tests/test_census.py`
- `backend/tests/test_planner_saved_sets.py`
- new reconciliation tests

Reference:

- `docs/planner.md`
- `ARCHITECTURE.md` only if its concise Planner index needs updating

## Acceptance scenarios

1. A signed-in reader loads Bobby, opens Raid Set, refreshes the page, and still
   sees `Raid Set` as the clean base rather than an unidentified draft.
2. Bobby equips a targeted ring in the other finger slot. After Census refresh,
   the equipment window uses the live ring, its projected delta disappears, the
   item leaves active Outline work, and it appears under Completed.
3. Bobby later unequips that ring. It remains Completed because it was previously
   observed, while the equipment baseline continues to float.
4. A guest and an account independently plan for the same public Bobby without
   either claiming the character or seeing the other's sets/completion ledger.
5. A first lookup served by Lexicon and a later Census response open the same
   character folder and retain all plans.
6. Selecting `Bobby (Wuoshi)` from Recent searches sends canonical `Bobby`, loads
   successfully, and preserves the display label.
7. Rapid A -> B lookup/navigation can never render A beneath `?character=B`.
8. Deleting the last saved set removes that character from account-backed saved
   folders while leaving an intentionally retained local recent search alone.
9. An obtained turquoise piece stops appearing as needed, but other pieces in
   that set and its full bonus ladder remain available.
10. Automatic completion changes no saved-set dirty marker and never deletes the
    original target evidence silently.

## Historical implementation instruction

This was the implementation order used. Preserve the decisions and acceptance
scenarios above when extending the feature. Inspect the current worktree and
schema before editing because commits and schema numbers may have advanced.
Verification never authorizes shipping or deployment.
