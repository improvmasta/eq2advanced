# eq2advanced — Codex notes

All context lives in `CLAUDE.md` — read that file; it applies to Codex verbatim
(commands, the rules, what the app is, what is open). Design reference:
`ARCHITECTURE.md` is the index of `docs/*.md`.

## Ship log

- 2026-08-14 (codex): Mark admin redesign documentation implemented
- 2026-08-14 (codex): Implement complete admin operations redesign
- 2026-08-14 (codex): Add public in-game chat archive and visitor insights
- 2026-08-13 (claude): Docs pass: tighten CLAUDE/AGENTS/README and the docs/ reference, move the skillissue proposal into docs/
- 2026-08-13 (claude): Crowdsourced AoE timers, account-kept hand marks, and a reflect countdown for Treyloth
- 2026-08-10 (claude): Live meter: Census resolves strangers mid-pull, AoE rows with no timer expire (carries /act end, joust marks, overlay text scale)
- 2026-08-09 (claude): Plugin update copy ships with the build (refdata NOTES), not hardcoded in the page
- 2026-08-09 (claude): Publish ACT plugin 0.2.1 (never skips unsent log on a failed send)
- 2026-08-09 (claude): Loot tab: chest drops, EQ2i-style item cards and roll history (schema v32)
- 2026-08-08 (claude): Live dashboard build-out (mini parse/overlay dock, livebus SSE wakeups, smooth clocks, ParseView), zone eras as reference data, Features page, docs/ split out of ARCHITECTURE
- 2026-08-07 (claude): Replay a recorded fight through the live meter (curator/admin), no writes
- 2026-08-07 (claude): Raid dashboard: the fight in progress (livemeter partials), raid notes by zone/named (v28), stream overlay (v29)
- 2026-08-06 (claude): Docs and repo cleanup: rewrite README, drop shipped plan files, remove dead ShareBar component + CSS
- 2026-08-05 (claude): Pets and procs stop being inferred: ability_rulings + the Abilities console (curator role), EQ2 class tree, and the wiki as reference data (schema v23, PARSE_VERSION 20)
- 2026-08-05 (claude): Sharing page rebuild (Groups + Automatic sharing side by side, guild-tag auto-share UI, settings-list switches)
- 2026-08-04 (claude): Phase 24: one raid, several uploaders — raidmatch clustering (schema v18 roster_json), your parse first, a Parse switch on the list and the raid page
- 2026-08-04 (claude): Import page rebuild: account-scoped pairing (schema v13), drag-drop uploader, no character prompt
- 2026-08-04 (claude): Serve the ACT plugin from the site: download + install steps + auto-sharing on Import, header pill
- 2026-08-04 (claude): Revert phase 17: sharing belongs on the site, the ACT plugin only sends logs (schema v12 drops session_shares + can_share)
- 2026-08-03 (claude): Phase 9+10: editable raid list, import hub, fight rail rebuild, engagement v3, read caches
