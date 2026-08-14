# Admin redesign

Status: implemented on 2026-08-14 in commit `131ae8e`. This document is the
product and implementation reference for the current admin experience.

The shipped implementation includes:

- the route-based admin workspace and operational dashboard;
- actionable parse incidents with retry, restart, acknowledgement and support
  bundles;
- account, deleted-group, feedback and structured activity workspaces;
- the master-detail ability review queue with provenance, advancement and undo;
- the AoE timer evidence workbench, ACT export and live preview;
- database-backed timer overrides, exclusions, split-mob decisions, reuse
  debuffs and reflect windows, all validated, audited and reversible;
- schema v38 and focused API tests covering incident concurrency, feedback
  workflow and the timer authority ladder.

The acceptance criteria and delivery phases below are retained as the design
rationale and regression contract. They describe shipped behavior rather than
future work.

## The outcome

Admin should answer three questions quickly:

1. Is the site healthy?
2. Is there anything I need to do?
3. Where do I go to do a specific job?

The current page does not fail because any one panel is terrible. It fails
because six tabs, an external Abilities console, settings, support tools,
statistics and unresolved failures all sit at the same level. It grew by adding
a destination whenever a feature needed somewhere to live. The redesign must
not be a prettier version of that structure.

The new admin is an operations workspace with a dashboard at its front and
three durable areas behind it:

- **Operations** — incidents, feedback and system activity.
- **People** — accounts and groups.
- **Game data** — abilities and AoE timers.

Settings belong beside the system they change, not in a miscellaneous settings
tab. Statistics appear where they help make a decision; they are not a separate
pile of interesting numbers.

## Design rules

### Every alert leads to an action

“Needs attention” is not a category. An item may appear in the action queue only
when the page can say:

- what failed, in plain language;
- when it started and whether it is still happening;
- what is affected;
- what the admin can do next;
- whether that action succeeded.

An item with no admin action is either informational health telemetry or an
engineering diagnostic. It must not be presented as an admin task.

### Separate state, action and history

- **State** says what is true now: healthy, degraded or unavailable.
- **Action** is the queue of work that remains.
- **History** says what happened and who changed it.

The current Overview, Feedback and Audit views blur these together. The new
dashboard summarizes state and open work; Operations contains the full queues;
Activity remains an immutable history.

### Summary first, detail on demand

The dashboard is not a wall of tables. It shows a small number of signals, each
linked to a filtered destination. Dense tables and technical evidence belong in
their workspaces and drawers.

### Preserve the privacy boundary

Admin remains operational, not omniscient. Nothing in this plan grants access to
another user's parse, roster, chat identity or log contents. Incident payloads
may expose session id, account, character, source, timestamps, status and a
sanitized error reason—the metadata already returned by `admin_api.py`—but not
combat data. Support for parse content still begins with the user sharing it.

### Make dangerous actions deliberate

Disabling an account, changing a role, resetting a password, overriding a timer
and discarding data require clear consequences and confirmation proportional to
their risk. Every mutation writes the audit log. Successful mutations update the
screen immediately and remain undoable when the underlying operation permits it.

## Information architecture

Use a persistent admin rail on wide screens and a single section menu on narrow
screens. Do not render a horizontal strip of seven or eight tabs.

```text
Admin
├── Dashboard
├── Operations
│   ├── Incidents
│   ├── Feedback
│   └── Activity
├── People
│   ├── Accounts
│   └── Deleted groups
└── Game data
    ├── Abilities
    └── AoE timers
```

The URL carries the location and useful filters, for example:

- `/admin`
- `/admin/incidents?state=open&type=parse_error`
- `/admin/accounts?q=lindsay`
- `/admin/abilities?status=unreviewed&class=shadowknight`
- `/admin/timers?mob=Mayong+Mistmoore`

This makes each job linkable and keeps browser history useful. Curators land
directly in `/admin/abilities` or `/admin/timers`; they do not see the admin-only
rail entries they cannot use.

## Dashboard

The dashboard is a daily control surface, not a report. It has four sections.

### 1. Site status

A compact status band answers whether the main systems are operating:

- ingest: live streams connected, batches received recently;
- parsing: queued/in progress, oldest job age, failures in the last 24 hours;
- storage: used space, growth and configured cap;
- reference data: last Census/wiki refresh and failures where available.

Each tile has a state word, a useful number and a link to detail. Green tiles
stay visually quiet. A dashboard that is healthy should feel nearly empty.

### 2. Action queue

Show at most the five highest-priority open incidents and feedback items. Each
row includes severity, concise cause, age and its primary action. Examples:

- `Parse failed · session 1842 · malformed ACT timestamp` — **Retry parse**
- `Parse stuck for 18m · session 1839` — **Restart parse**
- `Bug report · Live meter does not load` — **Review feedback**
- `12 abilities ready for human review` — **Review next**
- `3 timer suggestions disagree with ACT by >15%` — **Review timers**

“View all” opens the corresponding filtered workspace. Healthy live streams and
ordinary parsing are status, never queue items.

### 3. Usage and growth

Keep statistics, but choose a fixed handful that explain whether the site is
being used and whether capacity is changing:

- visitors and signed-in visitor-days over 30 days;
- uploads and completed raids over 30 days;
- active accounts over 30 days;
- storage total and 30-day growth.

Use small trends rather than a grid of lifetime totals. Lifetime counts can sit
in a secondary “All-time totals” disclosure. Every metric states its unit;
visitor-days must never be relabeled as unique people.

### 4. Recent changes

Show the latest five meaningful admin or curator mutations in human-readable
form: “Lindsay changed Soul Paralysis to 44s,” not a serialized payload. Link to
the full Activity view.

## Operations

### Incidents

Replace the current alert rows with a first-class incident projection. Initially
this can be derived from sessions rather than requiring a new database table.

Each incident has:

- stable id and type (`parse_error`, `parse_stuck`, later `reference_sync` etc.);
- severity and current state;
- detected and last-seen timestamps;
- sanitized summary plus expandable technical details;
- affected metadata;
- available actions and the result of the last action.

The detail drawer for a parse failure should show the full sanitized exception,
not only the final line, with a copy button for a support bundle containing the
session id, source, timestamps, status and error. It still contains no log text.

Required first actions:

- **Retry parse** for an errored session, using the existing stored upload when
  one exists.
- **Restart parse** for a stuck session after safely returning it to a runnable
  state.
- **Acknowledge** only for failures that cannot be repaired in Admin. This hides
  the notification, not the underlying state, and must retain actor, note and
  timestamp.

Do not offer “dismiss” as a way to make a real error disappear. Retrying should
return progress and then resolve the incident automatically when the session
finishes. If an upload was parsed-and-deleted or otherwise cannot be retried,
say so and provide the exact support instruction to give the account owner.

Filters: open/resolved, severity, type and age. Default to open. Resolved items
are history, not clutter in the active queue.

### Feedback

Feedback is an inbox, not a general admin tab. Preserve open → planned → closed,
then add:

- counts by state in the section header;
- filters and text search in the URL;
- an assignee or “unassigned” state if more than one admin begins triaging;
- admin notes separate from the reporter's original text;
- direct links when the submitted path still exists;
- a clear distinction between destructive deletion and closing.

The dashboard shows only new/open feedback. Planned and closed work lives here.

### Activity

Keep the audit log, but render known actions with labels and structured details.
Support server-side search/filter/pagination rather than fetching a growing
prefix and filtering it in the browser. Filters should include actor, action
family and date. Raw detail remains available in a disclosure for debugging.

## People

### Accounts

The existing server-side search, sort and pagination are sound. Keep the
click-row detail panel, but reorganize it into:

- identity and status;
- usage and limits;
- access role;
- recovery and security;
- dangerous actions.

Add a short activity summary and explicit effective limits (“site default:
unlimited”), so blank, zero and inherited values are never ambiguous. Password
reset should explain that all sessions will be signed out before confirmation.
Role changes should explain exactly what curator and admin permit.

Account error counts link to a filtered Incidents view instead of being red text
with nowhere to go. Admin still cannot open the account's raids.

### Deleted groups

This is a recovery tool, not “Content.” Give it its own small view under People.
Show deletion time, owner, member count and what restore will recover. Restore
is the primary action and should disappear after success.

Published raids do not need to share this page. If moderation of public raids is
not possible and not desired, keep their count on the dashboard and remove the
list. If a real operational need appears, build a narrowly authorized
“Published content” view with an explicit action; do not preserve a read-only
table merely because one exists now.

## Game data

Abilities and timers are important enough to be peers, sharing a consistent
workbench shell. Both edit site-wide game knowledge, both are available to the
curator role, and neither exposes private raid content.

### Abilities workbench

The current class rail reduces one giant list but still makes the curator hunt,
open, decide, save and reorient for every item. Reframe the page as a review
queue.

The header shows:

- remaining unreviewed count;
- reviewed today;
- confidence breakdown;
- search across all tracked abilities.

The queue supports combinable filters:

- status: unreviewed, ruled, curated, all;
- suggestion: pet/player and cast/proc;
- confidence;
- class/tier, including Unclassed;
- evidence flags: conflicting, no Census/wiki match, has prepare lines, etc.;
- sort: most evidence, least evidence, highest damage, alphabetical, newest
  first-seen when that timestamp becomes available.

Use a master-detail workbench: a compact result list on the left, the selected
ability and its evidence/form on the right. Saving advances to the next result
without collapsing the curator's filters. The class rail becomes a filter, not
the page's navigation model.

Improve the decision form:

- phrase the answer as one readable sentence before saving;
- distinguish “unknown” from an incomplete form;
- show conflicts first and explain why evidence supports each side;
- show current ruling, provenance, author and changed time;
- offer undo immediately after save;
- add keyboard actions for next/previous/save only after the controls are fully
  accessible without them.

Bulk actions should be deliberately narrow. Safe examples are “mark selected as
reviewed from curated seed” or applying an identical known grant to tiers of the
same named ability. Never bulk-accept machine pet/proc guesses.

### AoE timer workbench

Timer administration must make the site's three kinds of knowledge explicit:

1. **Reported** — the timer imported from ACT's spell-timer list.
2. **Measured** — the site's derived conclusion from `aoe_cycles`.
3. **Curated override** — a human ruling, including exclusions and special
   mechanics.

The list is one row per `(mob, ability)`, searchable by either name. Columns:

- mob and ability;
- effective timer and its source;
- ACT-reported timer;
- measured clean timer, agreeing intervals and distinct pulls;
- swipe verdict/factor and evidence on both sides;
- state: healthy, learning, disagreement, excluded, overridden;
- last observation.

Default the queue to rows needing judgment:

- a measured timer materially disagrees with ACT;
- enough evidence almost meets adoption thresholds;
- the multiple-instances signature blocks learning;
- a curator override conflicts with new evidence;
- a row has no usable timer despite repeated observations.

Selecting a row opens evidence: distribution of clean and swiped intervals,
pull count, current thresholds, which source wins, and the exact reason a timer
was or was not adopted. This is operational game evidence only; do not expose
player, character, session or encounter identities.

Curator actions:

- set or clear an effective timer override with a required note;
- accept a measured suggestion;
- exclude a `(mob, ability)` from learning/countdown with a reason such as
  multiple bodies or sustained damage shield;
- mark/clear a split-mob rule;
- edit curated reflect windows and reuse-debuff definitions in dedicated
  advanced drawers;
- export or copy a proposed ACT timer entry.

Do not edit JSON files directly from a request handler. Move mutable curator
decisions into database tables with actor, note and timestamps, then combine
them with shipped reference data in one explicit authority ladder. Static
reference files can remain the seed/fallback. Every change is validated,
audited and reversible.

A timer preview should show exactly what the live panel would display for the
current effective rule. This is more useful than exposing raw configuration
alone and catches unit/source mistakes before they reach a raid.

## Settings

Settings appear in context:

- registration and account defaults: People → Accounts;
- upload/storage limits and retention: Dashboard → Storage detail;
- timer thresholds and curated mechanics: Game data → AoE timers → Advanced;
- future integration health and refresh controls: the relevant status detail.

Use a sticky unsaved-changes bar for multi-field forms. Show the current
effective value, default, units, consequence and last changer. Avoid `0 =
unlimited` as the only explanation; render “Unlimited” as an explicit choice.

## Backend work

The UI should not invent actions the backend cannot perform. Add capabilities
in this order:

1. Extend overview with time-windowed health and usage summaries.
2. Add incident detail and safe retry/restart endpoints with tests for state
   transitions, missing source data and concurrent retries.
3. Add server-side audit filters and human-readable structured detail.
4. Add ability queue filters, ruling provenance and review timestamps.
5. Add read-only timer review endpoints derived from `aoe_cycles` and shipped
   reference data.
6. Add timer ruling tables and audited curator mutations only after the
   read-only evidence view proves the model.

Prefer separate focused endpoints over one enormous `/admin/overview` payload.
The dashboard may request its small summary in parallel; workspaces fetch their
own paged data.

## Delivery plan

### Phase 1 — make the current admin honest

- Replace “Needs attention” rows with useful error detail and links.
- Add retry/restart actions where the source data permits them.
- Link account error counts to incidents.
- Rename/rehome Content, Feedback and Audit under the new rail.
- Preserve existing account, visitor and group behavior.

This phase fixes the most frustrating failure without waiting for the full
redesign.

### Phase 2 — dashboard and navigation

- Introduce the route-based admin shell.
- Build the status, action queue, usage trends and recent-changes summaries.
- Move settings into their relevant detail views.
- Remove redundant lifetime metric tiles and the horizontal tab strip.

### Phase 3 — abilities workbench

- Add the queue/filter API and master-detail layout.
- Add ruling provenance, next-item flow and undo.
- Verify the curator-only experience independently of admin.

### Phase 4 — timer visibility

- Build the read-only timer queue and evidence detail first.
- Compare it against known fights and the live panel's effective timers.
- Add ACT export/copy support.

### Phase 5 — curated timer control

- Add database-backed overrides, exclusions and special-mechanic rulings.
- Add validation, preview, audit and rollback.
- Retire duplicated mutable JSON entries only after migration tests prove the
  authority ladder.

## Acceptance criteria

The redesign is successful when:

- a healthy dashboard can be understood in under ten seconds;
- every open action-queue item has a diagnosis and a next step;
- an admin can retry a recoverable failed parse and see its outcome without a
  shell;
- no admin route reveals another user's parse contents;
- account errors, feedback and game-data review queues are directly linkable;
- an ability curator can process consecutive decisions without losing filters
  or returning to a class rail;
- a curator can explain exactly why an AoE timer has its effective value;
- timer changes are previewable, audited and reversible;
- the primary navigation has four destinations, not a new tab for every
  feature;
- dashboard metrics show trends and units rather than unexplained lifetime
  counts.

## Explicit non-goals

- General-purpose database administration.
- Reading private raids for support.
- Raw log browsing.
- Editing arbitrary reference files from the browser.
- Turning every statistic into a chart.
- Building a notification system before the on-page action queue is useful.
