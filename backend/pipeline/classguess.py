"""Per-player class inference: what class is each player in this log?

The log never says. What it does say is which abilities every player used, and
`ability_catalog` already knows which ability names belong to which class (from
the cached Census spell books). So the abilities a player used vote for a class
and the winner takes the row — stored as JSON in `entities.class_guess`.

Evidence is gathered per NAME across every session, not per session: one raid
night shows a healer casting three spells, and the same healer in last week's
log shows six more. Guessing per file gave the same person a class in one raid,
nothing in the next, and (for Zooey: defiler here, mystic there) two different
answers in the same list. The answer is written back to EVERY entity row with
that name, so a name resolved tonight is resolved in older raids too.

Honesty rules baked in:
- **Procs never vote.** A "may cast X on ..." ability comes from GEAR, not the
  class book — a warlock wearing the same fabled cloak as a wizard would vote
  for whoever the catalog filed the proc under. `ability_catalog.proc=1` is out.
- **Pet kits never vote** (`unit='pet'`): a conjuror's pet casting Protofire is
  the pet's ability, and conflated pets hide under their owner's name.
- **Autoattack never votes** — every class swings.
- **Shared abilities vote in fractions.** A catalog `class` of
  "conjuror,necromancer" is half a vote each: it cannot pick between the two,
  but it is real evidence against the other twenty-two. Only a whole vote (a
  spell exactly one class scribes) can carry a winner, which is what MIN_STRONG
  enforces.
- **Census ground truth wins outright.** A `characters` row with a class (from
  the Census sync) is the answer, at confidence 1.0 and source `census`.

A winner needs whole-vote evidence (MIN_STRONG), enough total weight
(MIN_SCORE), and either a majority of the weight cast or a clear MARGIN over
the runner-up — the margin is what carries a real class whose player wore a lot
of unflagged proc gear. Otherwise `class_guess` stays NULL and every reader
shows nothing rather than a guess.

What this still cannot do is name a class whose spells are not in Census: AA
abilities, gear procs and item effects are absent from the spell books, and
they are roughly half of the ability names a raid log actually contains.
"""

import json
from collections import Counter, defaultdict

# autoattack buckets from statsroll._melee_bucket — class-blind by definition
MELEE_BUCKETS = frozenset(("(melee)", "(multi attack)", "(aoe attack)", "(flurry)"))

MIN_STRONG = 2          # single-class abilities behind the winner
MIN_SCORE = 2.0         # winner's weight, shared abilities counted in fractions
MIN_SHARE = 0.5         # ...of all weight cast, unless the margin carries it
MARGIN = 2.0            # winner over runner-up when it is not a majority

# the 24 EoF-era adventure classes (census.sync.ALL_CLASSES) plus the two that
# only exist on live — Census class keys are lowercase, so this doubles as the
# filter that keeps tradeskill keys ("artisan", "alchemist") out of the vote
VALID_CLASSES = frozenset((
    "assassin", "berserker", "brigand", "bruiser", "coercer", "conjuror",
    "defiler", "dirge", "fury", "guardian", "illusionist", "inquisitor",
    "monk", "mystic", "necromancer", "paladin", "ranger", "shadowknight",
    "swashbuckler", "templar", "troubador", "warden", "warlock", "wizard",
    "beastlord", "channeler",
))

# sessions the lazy API backfill has already tried; a session with no resolvable
# player would otherwise re-run the guess on every request
_ATTEMPTED: set[int] = set()


def _classes(raw: str | None) -> set[str]:
    """A catalog/ability class string -> the classes it identifies. Census
    stores every class that can scribe a spell as a comma list."""
    if not raw:
        return set()
    return {p.strip().lower() for p in raw.split(",") if p.strip()} & VALID_CLASSES


def _single_class(raw: str | None) -> str | None:
    """The ONE class a class string identifies, or None if it names several."""
    parts = _classes(raw)
    return next(iter(parts)) if len(parts) == 1 else None


def _catalog(conn) -> tuple[set[str], dict[str, set[str]]]:
    """-> (ability names that may never vote, ability name -> its classes)."""
    muted: set[str] = set()
    cls_of: dict[str, set[str]] = {}
    for name, cls, unit, proc in conn.execute(
            "SELECT ability_name, class, unit, proc FROM ability_catalog"):
        if unit == "pet" or proc:
            muted.add(name)
            continue
        classes = _classes(cls)
        if classes:
            cls_of[name] = classes
    return muted, cls_of


def _infer(used: set[tuple[str, str | None]], muted: set[str],
           cls_of: dict[str, set[str]]) -> dict | None:
    score: Counter = Counter()      # weight, shared abilities split k ways
    strong: Counter = Counter()     # abilities exactly one class scribes
    for name, ability_class in used:
        if name in MELEE_BUCKETS or name in muted:
            continue
        classes = cls_of.get(name) or _classes(ability_class)
        if not classes:
            continue
        for cls in classes:
            score[cls] += 1 / len(classes)
        if len(classes) == 1:
            strong[next(iter(classes))] += 1
    total = sum(score.values())
    if not total:
        return None
    # ties break on the class name so the same evidence always yields the same
    # answer, whatever order the rows came back in
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    top, weight = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    share = weight / total
    # a tie is never an answer, however much weight is behind it
    if (strong[top] < MIN_STRONG or weight < MIN_SCORE or weight <= runner_up
            or (share < MIN_SHARE and weight < MARGIN * runner_up)):
        return None
    return {"class": top, "confidence": round(share, 2),
            "matches": strong[top], "source": "inferred"}


def _evidence(conn) -> dict[str, set[tuple[str, str | None]]]:
    """Every ability every player has EVER been seen using, keyed by name.

    One query over the whole database (4.8k distinct rows on the real one,
    ~0.1s) — cheap enough to redo per parse, and the only way a name that is
    thin in tonight's log gets the benefit of last week's."""
    used: dict[str, set] = defaultdict(set)
    for r in conn.execute(
            "SELECT DISTINCT e.name AS name, ab.name AS ability, ab.class AS acls "
            "FROM encounter_ability_stats s "
            "JOIN entities e ON e.id = s.entity_id "
            "JOIN abilities ab ON ab.id = s.ability_id "
            "WHERE e.kind='player'"):
        used[r["name"]].add((r["ability"], r["acls"]))
    return used


def guess_session_classes(conn, session_id: int) -> int:
    """Write `entities.class_guess` for every player in a session, from
    evidence pooled across every session, and apply the answer to that name's
    rows in the other sessions too.

    Returns the number of rows updated. Runs inside the caller's transaction
    when there is one (the parse path); otherwise autocommits per statement."""
    names = {r["name"] for r in conn.execute(
        "SELECT DISTINCT name FROM entities WHERE session_id=? AND kind='player'",
        (session_id,))}
    if not names:
        return 0

    truth = {}
    for r in conn.execute(
            "SELECT name, class FROM characters WHERE class IS NOT NULL"):
        cls = (r["class"] or "").strip().lower()
        if cls:
            truth[r["name"].lower()] = cls

    muted, cls_of = _catalog(conn)
    used = _evidence(conn)

    updated = 0
    for name in names:
        known = truth.get(name.lower())
        if known:
            guess = {"class": known, "confidence": 1.0,
                     "matches": len(used.get(name, ())), "source": "census"}
        else:
            guess = _infer(used.get(name, set()), muted, cls_of)
        if guess is None:
            continue
        cur = conn.execute(
            "UPDATE entities SET class_guess=? WHERE name=? AND kind='player'",
            (json.dumps(guess, separators=(",", ":")), name))
        updated += cur.rowcount
    return updated


def parse_class_guess(text: str | None) -> dict | None:
    """Read side of `entities.class_guess`. Never raises on junk."""
    if not text:
        return None
    try:
        guess = json.loads(text)
    except (TypeError, ValueError):
        return None
    return guess if isinstance(guess, dict) and guess.get("class") else None


def backfill_session(conn, session_id: int, cache: bool = True) -> int:
    """Lazy fill for data parsed before class inference existed. The common
    case is ONE indexed lookup (the session's entity rows) that finds nothing to
    do; `cache` suppresses even that for finished sessions we have already
    tried."""
    if cache and session_id in _ATTEMPTED:
        return 0
    todo = conn.execute(
        "SELECT 1 FROM entities WHERE session_id=? AND kind='player' "
        "AND class_guess IS NULL LIMIT 1", (session_id,)).fetchone()
    if todo is None:
        if cache:
            _ATTEMPTED.add(session_id)
        return 0
    with conn:
        n = guess_session_classes(conn, session_id)
    if cache:
        _ATTEMPTED.add(session_id)
    return n
