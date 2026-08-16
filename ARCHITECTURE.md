# eq2advanced — architecture index

Raid-parsing and coaching site for EverQuest II TLE (level-70, EoF era). Core
idea: *Census says what an ability should do at your stats; the parse says what it
actually did; coaching lives in the gap.*

**This file is the index. The reference lives in `docs/`, split by topic so a
session only reads the part it is working on.** Read the file for the area you are
touching before changing it, and put new design decisions and their evidence in
the matching file rather than here.

| File | Covers |
| --- | --- |
| `docs/runtime.md` | Runtime topology, the two-proxy problem (`siteconfig.py`), stack, backend layout, hardening, verification |
| `docs/sharing.md` | Identity, accounts, groups, the one visibility predicate (`groups.py`) and its four traps, hiding vs sharing vs dismissal, the admin console, chat redaction (`pipeline/redact.py`), the public chat box (`pipeline/chatbus.py`), log retention |
| `docs/live.md` | The frozen ACT-DLL ingest contract, the raid dashboard (`/live`), the live meter, AoE detection and the learned timer table, the mini parse, notes, the OBS and in-game overlays, replay |
| `docs/parser.md` | The subject model, encounter segmentation, engage rules, class inference (`classguess.py`), ACT parity and the corpse tail, rezzes/intercepts/adjusted delay, attribution and the stats engine |
| `docs/zoneruns.md` | Zone runs (the navigation model), the raid page and its tabs, the raid list, raidmatch, the encounter APIs, the Class tab, the Loot tab and items as reference data, curated buffs, frontend conventions, the sibling-site links |
| `docs/compare-import.md` | The Compare page (`?c=` grammar, Picker, band search) and screenshot import (`pipeline/actshot.py`) |
| `docs/census-abilities.md` | Census sync, guild vote, proc exposure, pet/proc rulings and the Abilities console, provenance, the class tree, the wiki as reference data, why gear procs are wontfix |
| `docs/coach.md` | Coach engine (descriptive, fit, replay, calibration), the five correctness rules, ability catalog, raid report |
| `docs/admin-redesign.md` | Implemented Admin operations workspace: dashboard, incidents, support workflows, ability review, and database-backed AoE timer curation |
| `docs/planner.md` | The Planner (`/plan`, off the nav) — reader-chosen expansions (EoF and/or RoK), Census-backed current gear, slot-aware candidate cycling, projected stats, dynamic set adornments, gear search, and the stable prerequisite outline. **Phases 0-2 COMPLETE** (`backend/planner/`, schema v42): Phase 0 measured 2,584 matched coordinates across 899 RoK quests; co-location tags / multi-class epic planning remain planned |

`docs/skillissue-proposal.md` is an outbound proposal to another project, not
reference material.

`CLAUDE.md` / `codex.md` hold the working rules and commands; each rule there
points back into `docs/` for its evidence.
