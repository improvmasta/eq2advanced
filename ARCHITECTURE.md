# eq2advanced — Architecture

Raid-coaching web app for EverQuest II TLE (Wuoshi, EoF era). Core idea:
*Census says what an ability should do at your stats; the parse says what it
actually did; coaching lives in the gap.*

This file is the INDEX. The reference lives in `docs/`, split by topic so a
session only reads the part it is working on — read the file for the area you
are touching before changing it, and put new design decisions (and their
evidence) in the matching file rather than here.

| File | Covers |
| --- | --- |
| `docs/runtime.md` | Runtime topology (Cloudflare/Zoraxy/dev vs container), the two-proxy problem (`siteconfig.py`), stack, backend layout, hardening, verification |
| `docs/sharing.md` | Identity, accounts, groups, the ONE visibility predicate (`groups.py`) and its four traps, hiding vs sharing, admin's limits, chat redaction (`pipeline/redact.py`), log retention, auto-share (v16), guild-tag sharing (v21), group soft delete (v17) |
| `docs/live.md` | The frozen ACT-DLL ingest contract, the raid dashboard (`/live`), the live meter (a view, writes nothing), the spell-timer shortlist, the mini parse (dashboard rail AND stream overlay), notes (v28) and the notes outline (grouped by expansion, from `zones.py`), stream overlay (v29), replay — which the overlay can read, and how fast the screen sees a hit (push bus, plugin cadence, the update pill v30) |
| `docs/parser.md` | The subject model (the crux), encounter segmentation, engage rules, class inference (`classguess.py`), ACT parity + the corpse tail, rezzes/intercepts/adjusted delay (v10), attribution and the stats engine |
| `docs/zoneruns.md` | Zone runs (the navigation model), the raid page (fight rail, Deaths tab, read caches, hand edits + hiding v26), the raid list, raidmatch (one raid, several uploaders, v18), the encounters APIs (timeline/deaths/aoes/class-stats/loot), the Class tab, the Loot tab + items as reference data (v32), curated buffs, PotM, frontend layout, the sibling-site links in the top bar |
| `docs/compare-import.md` | The Compare page (`?c=` grammar, Picker, band search) and screenshot import (`pipeline/actshot.py`, v27) |
| `docs/census-abilities.md` | Census sync, guild vote (v20), character level/guild on rows, proc exposure, pets/procs rulings + the Abilities console (curator), provenance, class tree, the wiki as reference data (v23), why the gear-proc wontfix does not cover item lookup |
| `docs/coach.md` | Coach engine (descriptive, fit, replay, calibration), the five correctness rules, ability catalog, raid report |

`CLAUDE.md` / `codex.md` hold the working rules and commands; each rule there
points back into `docs/` for its evidence.
