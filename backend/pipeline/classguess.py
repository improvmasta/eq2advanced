"""Per-entity class inference: what class is each player in this log?

The log never says. What it does say is which abilities every player used, and
`ability_catalog` already knows which ability names belong to which class (from
the cached Census spell books). So each distinct ability a player used casts one
vote for its class and the winner takes the row — stored as JSON in
`entities.class_guess`.

Honesty rules baked in:
- **Procs never vote.** A "may cast X on ..." ability comes from GEAR, not the
  class book — a warlock wearing the same fabled cloak as a wizard would vote
  for whoever the catalog filed the proc under. `ability_catalog.proc=1` is out.
- **Pet kits never vote** (`unit='pet'`): a conjuror's pet casting Protofire is
  the pet's ability, and conflated pets hide under their owner's name.
- **Autoattack never votes** — every class swings.
- **Shared abilities never vote.** A catalog `class` of "conjuror,necromancer"
  resolves to no single class, so it is evidence for neither.
- **Census ground truth wins outright.** A `characters` row with a class (from
  the Census sync) is the answer, at confidence 1.0 and source `census`.

Thresholds are deliberately blunt: the winner needs >= MIN_MATCHES abilities and
>= MIN_SHARE of all votes cast, otherwise `class_guess` stays NULL and every
reader shows nothing rather than a guess.
"""

import json
from collections import Counter, defaultdict

# autoattack buckets from statsroll._melee_bucket — class-blind by definition
MELEE_BUCKETS = frozenset(("(melee)", "(multi attack)", "(aoe attack)", "(flurry)"))

MIN_MATCHES = 3         # distinct class-defining abilities behind the winner
MIN_SHARE = 0.4         # winner's share of all votes cast

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


def _single_class(raw: str | None) -> str | None:
    """A catalog/ability class string -> the one class it identifies, or None.
    Census stores every class that can scribe a spell as a comma list; a shared
    spell identifies nobody."""
    if not raw:
        return None
    parts = {p.strip().lower() for p in raw.split(",") if p.strip()} & VALID_CLASSES
    return next(iter(parts)) if len(parts) == 1 else None


def _catalog(conn) -> tuple[set[str], dict[str, str]]:
    """-> (ability names that may never vote, ability name -> class)."""
    muted: set[str] = set()
    cls_of: dict[str, str] = {}
    for name, cls, unit, proc in conn.execute(
            "SELECT ability_name, class, unit, proc FROM ability_catalog"):
        if unit == "pet" or proc:
            muted.add(name)
            continue
        one = _single_class(cls)
        if one:
            cls_of[name] = one
    return muted, cls_of


def _infer(used: set[tuple[str, str | None]], muted: set[str],
           cls_of: dict[str, str]) -> dict | None:
    votes: Counter = Counter()
    for name, ability_class in used:
        if name in MELEE_BUCKETS or name in muted:
            continue
        one = cls_of.get(name) or _single_class(ability_class)
        if one:
            votes[one] += 1
    total = sum(votes.values())
    if not total:
        return None
    # ties break on the class name so the same log always yields the same answer
    top, n = max(sorted(votes.items()), key=lambda kv: kv[1])
    if n < MIN_MATCHES or n / total < MIN_SHARE:
        return None
    return {"class": top, "confidence": round(n / total, 2),
            "matches": total, "source": "inferred"}


def guess_session_classes(conn, session_id: int) -> int:
    """Write `entities.class_guess` for every player entity in a session.
    Returns the number of rows updated. Runs inside the caller's transaction
    when there is one (the parse path); otherwise autocommits per statement."""
    players = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM entities WHERE session_id=? AND kind='player'",
        (session_id,))}
    if not players:
        return 0

    truth = {}
    for r in conn.execute(
            "SELECT name, class FROM characters WHERE class IS NOT NULL"):
        cls = (r["class"] or "").strip().lower()
        if cls:
            truth[r["name"].lower()] = cls

    muted, cls_of = _catalog(conn)
    used: dict[int, set] = defaultdict(set)
    for r in conn.execute(
            "SELECT s.entity_id AS eid, ab.name AS name, ab.class AS acls "
            "FROM encounter_ability_stats s "
            "JOIN encounters e ON e.id = s.encounter_id "
            "JOIN abilities ab ON ab.id = s.ability_id "
            "WHERE e.session_id=?", (session_id,)):
        if r["eid"] in players:
            used[r["eid"]].add((r["name"], r["acls"]))

    rows = []
    for eid, name in players.items():
        known = truth.get(name.lower())
        if known:
            guess = {"class": known, "confidence": 1.0,
                     "matches": len(used.get(eid, ())), "source": "census"}
        else:
            guess = _infer(used.get(eid, set()), muted, cls_of)
        if guess is not None:
            rows.append((json.dumps(guess, separators=(",", ":")), eid))
    if rows:
        conn.executemany("UPDATE entities SET class_guess=? WHERE id=?", rows)
    return len(rows)


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
