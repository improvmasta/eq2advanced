"""The Planner — what to chase in an expansion, and what to do to get it.

Reference data about the GAME, per era, with no account, parse or visibility
predicate anywhere near it. One row serves everybody forever, exactly as
`items.py` puts it.

Three modules, and the split is the same one `docs/planner.md` draws:

- `wiki` — the wiki's templates as layer-1 fields. Parsing only; no database.
- `ingest` — the hand-run crawl that fills the tables. Network-bound, offline,
  never scheduled — the same rule the wiki ability ingest keeps.
- `catalog` — the read side. Era-filtered, scored against a declared ORDER,
  and the only part a request handler ever touches.
"""
