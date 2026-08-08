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

**People BETRAY, and one pooled answer per name cannot survive it.** EQ2 lets a
character swap to the mirror subclass of their archetype, which splits their
evidence into two spellbooks with a date between them. Pooling produced two
different failures, and the second is the worse one:

- a **tie**, when the halves are level. Klebb cast swashbuckler abilities until
  2026-07-31 and brigand after (17 v 16); Thwart was an illusionist until
  2026-08-02 and a coercer after (12 v 10). Both failed the tie rule and went
  blank in every raid they had ever been in, more firmly with every upload.
- a **confident wrong answer**, when one half is bigger. Zooey's defiler half
  led her mystic half 19 to 16, so the vote called her a defiler at 52% and
  stamped it on all four of her sessions — including the two that are purely
  mystic. She is a Mystic; Census says so too.

So the timeline is read FIRST, before the tally and before Census: if the top
two classes are a real betrayal pair (`BETRAYAL_PAIRS`) and their ability
windows do not overlap, that is not ambiguity, it is a date. `_split_eras`
finds the changeover and infers each side from its own evidence; the eras are
stored together and `class_at` resolves the one that was true at the time of
the FIGHT — a session is a file, and a file can span six weeks.

What this still cannot do is name a class whose spells are not in Census: AA
abilities, gear procs and item effects are absent from the spell books, and
they are roughly half of the ability names a raid log actually contains.
"""

import json
from collections import Counter, defaultdict
from typing import NamedTuple

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

# EQ2's twelve mirrored subclasses — the ONLY moves a class change can make.
# Betrayal swaps you to the other subclass of your own archetype and nothing
# else, so this is a fact about the game, not a threshold to tune, and it is
# what keeps a two-day gap in a proc's evidence from reading as a career
# change: Jabann scored 5 warlock then 2 fury, Kartik 2 shadowknight then 3
# berserker, and neither pairing exists.
BETRAYAL_PAIRS = frozenset(frozenset(p) for p in (
    ("guardian", "berserker"), ("paladin", "shadowknight"), ("monk", "bruiser"),
    ("templar", "inquisitor"), ("warden", "fury"), ("mystic", "defiler"),
    ("wizard", "warlock"), ("conjuror", "necromancer"), ("illusionist", "coercer"),
    ("swashbuckler", "brigand"), ("troubador", "dirge"), ("assassin", "ranger"),
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


class Evidence(NamedTuple):
    """Everything the whole database knows about who cast what, and when.

    `by_name` is the pooled set the vote runs on. `by_session` is the same
    evidence kept apart per session, so a name whose class CHANGED can still be
    answered for each log it appears in. `window` is when each ability was
    cast, which is what tells a class change apart from a genuine tie."""
    by_name: dict[str, set[tuple[str, str | None]]]
    by_session: dict[str, dict[int, set[tuple[str, str | None]]]]
    window: dict[str, dict[str, tuple[int, int]]]     # name -> ability -> (first, last)


def _evidence(conn) -> Evidence:
    """Every ability every player has EVER been seen using, keyed by name.

    One query over the whole database (14k rows on the real one, ~0.4s) — cheap
    enough to redo per parse, and the only way a name that is thin in tonight's
    log gets the benefit of last week's. Grouping by encounter start carries
    the timeline `_split_eras` needs without a second pass."""
    by_name: dict[str, set] = defaultdict(set)
    by_session: dict[str, dict[int, set]] = defaultdict(lambda: defaultdict(set))
    window: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    for r in conn.execute(
            "SELECT e.session_id AS sid, e.name AS name, ab.name AS ability, "
            "       ab.class AS acls, MIN(enc.started_ts) AS t0, MAX(enc.started_ts) AS t1 "
            "FROM encounter_ability_stats s "
            "JOIN entities e ON e.id = s.entity_id "
            "JOIN abilities ab ON ab.id = s.ability_id "
            "JOIN encounters enc ON enc.id = s.encounter_id "
            "WHERE e.kind='player' "
            "GROUP BY e.session_id, e.name, ab.name"):
        name, ability = r["name"], r["ability"]
        by_name[name].add((ability, r["acls"]))
        by_session[name][r["sid"]].add((ability, r["acls"]))
        prev = window[name].get(ability)
        window[name][ability] = (
            min(r["t0"], prev[0]) if prev else r["t0"],
            max(r["t1"], prev[1]) if prev else r["t1"])
    return Evidence(by_name, by_session, window)


def _strong_by_class(used, muted, cls_of) -> dict[str, list[str]]:
    """class -> the abilities in `used` that ONLY that class scribes."""
    out: dict[str, list[str]] = defaultdict(list)
    for name, ability_class in used:
        if name in MELEE_BUCKETS or name in muted:
            continue
        classes = cls_of.get(name) or _classes(ability_class)
        if len(classes) == 1:
            out[next(iter(classes))].append(name)
    return out


def _split_eras(used: set[tuple[str, str | None]], window: dict[str, tuple[int, int]],
                muted: set[str], cls_of: dict[str, set[str]]) -> list[dict] | None:
    """The evidence read as a timeline. -> [earlier guess, later guess] in time
    order, or None when the contenders overlap and there is no changeover.

    Runs BEFORE the pooled vote, not as its fallback: a changeover does not
    need the tally to be close to exist, and when the tally has a winner it is
    picking one side of a real switch and applying it to every raid on both
    sides of it.

    The test is disjointness, not merely "two strong classes": a raider who
    wears a proc off another class's list scores a few stray votes all night
    long, and those windows interleave with the real book. A betrayal does not
    interleave — the last swashbuckler ability lands before the first brigand
    one, because the character stopped being able to cast it."""
    strong = _strong_by_class(used, muted, cls_of)
    ranked = sorted(strong.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    if len(ranked) < 2 or len(ranked[1][1]) < MIN_STRONG:
        return None
    if frozenset((ranked[0][0], ranked[1][0])) not in BETRAYAL_PAIRS:
        return None
    spans = {}
    for cls, abilities in ranked[:2]:
        stamps = [window[a] for a in abilities if a in window]
        if not stamps:
            return None
        spans[cls] = (min(t0 for t0, _ in stamps), max(t1 for _, t1 in stamps))
    (first_cls, first_span), (second_cls, second_span) = sorted(
        spans.items(), key=lambda kv: kv[1][0])
    if second_span[0] <= first_span[1]:
        return None                     # the two books were cast side by side
    cut = (first_span[1] + second_span[0]) / 2
    eras = []
    for before, (cls, span) in ((True, (first_cls, first_span)),
                                (False, (second_cls, second_span))):
        # an ability straddling the cut belongs to neither era: it is exactly
        # the shared proc or item effect the disjointness test is guarding
        # against, and it would vote in both
        era_used = {(a, c) for a, c in used
                    if a in window and ((window[a][1] <= cut) if before
                                        else (window[a][0] > cut))}
        guess = _infer(era_used, muted, cls_of)
        if guess is None or guess["class"] != cls:
            return None
        guess["era"] = [int(span[0]), int(span[1])]
        eras.append(guess)
    return eras


UNIDENTIFIED = json.dumps({"class": None, "source": "unidentified"},
                          separators=(",", ":"))


def guess_session_classes(conn, session_id: int, roster=None) -> int:
    """Write `entities.class_guess` for every player in a session, from
    evidence pooled across every session, and apply the answer to that name's
    rows in the other sessions too. A name whose class CHANGED gets the whole
    timeline written to every row (`_era_guess`); the reader resolves it against
    the fight's own clock with `class_at`.

    `roster` is the set of names this log proves are player characters
    (`refine.roster_prescan`). A bare-named summoned pet is grammatically a
    raider and casts its class's real spells, so inference will happily call it
    a Berserker off three Ruptures; withholding the claim is the only honest
    answer when nothing in the file says a person was ever behind the name.
    Those rows are marked `unidentified` rather than left blank, so the table
    says "we don't know" instead of quietly showing a classless raider — and
    the mark is written for THIS session only, because the same name may be a
    real character in someone else's log.

    Returns the number of rows updated. Runs inside the caller's transaction
    when there is one (the parse path); otherwise autocommits per statement."""
    names = {r["name"] for r in conn.execute(
        "SELECT DISTINCT name FROM entities WHERE session_id=? AND kind='player'",
        (session_id,))}
    if not names:
        return 0

    # Census by name beats every vote below it, and it covers the whole raid,
    # not just the people with an account here (census/roster.py). A claimed
    # character is layered on top because its own sync is the fresher of the
    # two — same source, refreshed on sight rather than on a TTL.
    from census.roster import known_classes
    truth = dict(known_classes(conn))
    for r in conn.execute(
            "SELECT name, class FROM characters WHERE class IS NOT NULL"):
        cls = (r["class"] or "").strip().lower()
        if cls:
            truth[r["name"].lower()] = cls

    muted, cls_of = _catalog(conn)
    ev = _evidence(conn)

    # never overwrite another session's "no player evidence here" finding: the
    # same name can be a raider in one log and a summoned pet in the next
    ALL_ROWS = ("UPDATE entities SET class_guess=? WHERE name=? AND kind='player' "
                "AND (class_guess IS NULL OR class_guess <> ?)")

    updated = 0
    for name in names:
        used = ev.by_name.get(name, set())
        known = truth.get(name.lower())
        if known is None and roster is not None and name not in roster:
            cur = conn.execute(
                "UPDATE entities SET class_guess=? WHERE session_id=? AND name=? "
                "AND kind='player'", (UNIDENTIFIED, session_id, name))
            updated += cur.rowcount
            continue

        # The timeline is checked FIRST, before the pooled vote and before
        # Census — both of them answer with ONE class, and the whole point of a
        # changeover is that one class is not the answer. Consulting it only
        # when the vote deadlocked was not enough: Zooey cast 19 defiler
        # abilities through 2026-07-26 and 16 mystic ones from 2026-07-30, the
        # pooled tally called that a defiler win at 52%, and every session she
        # was in — including the two that are purely mystic — was stamped
        # defiler. A confident wrong answer is worse than the blank was.
        eras = _split_eras(used, ev.window.get(name, {}), muted, cls_of)
        if eras:
            if known:
                # Census reports what they are NOW, which dates it to the last
                # era; the earlier ones are the log's to answer
                eras[-1] = {**eras[-1], "class": known,
                            "confidence": 1.0, "source": "census"}
            guess = _era_guess(eras)
        elif known:
            guess = {"class": known, "confidence": 1.0,
                     "matches": len(used), "source": "census"}
        else:
            guess = _infer(used, muted, cls_of)
            if guess is None:
                continue
        cur = conn.execute(
            ALL_ROWS, (json.dumps(guess, separators=(",", ":")), name, UNIDENTIFIED))
        updated += cur.rowcount
    return updated


def _era_guess(eras: list[dict]) -> dict:
    """The eras folded into one `class_guess` value: the LATEST class at the
    top (so any reader that knows nothing about eras sees who they are now),
    with the whole timeline under `eras` for one that does.

    Answering per session was not enough. A raid log is a file, not a night —
    Vestigial's is 1.2M lines spanning six weeks, so Zooey's defiler half and
    her mystic half are both inside it, and crediting the session to whichever
    book it cast more of got Mistmoore's Inner Sanctum (2026-08-02, two days
    into the mystic era) labelled defiler. The class has to be resolved at the
    time of the FIGHT, which is `class_at`."""
    latest = eras[-1]
    return {**latest, "eras": [{"class": e["class"], "from": e["era"][0],
                                "to": e["era"][1], "source": e["source"]}
                               for e in eras]}


def resolve_class(guess: dict | None, ts: int | None,
                  strong_here: dict[str, int] | None = None) -> dict | None:
    """The class a name held in the fights being READ, not across their career.

    Census answers "what are they now" and the pooled vote answers "what are
    they mostly"; neither is dated, and a raid is. The abilities cast in the
    fights on screen ARE dated, and Census's own spell collection says which
    class scribes each one — so when the log in front of us names a class, it
    outranks both. That is what keeps a raid from the week before a betrayal
    from being relabelled by a Census row written today.

    The catch, and the reason this is not just "infer from these fights": the
    local evidence gets to choose WHEN, never WHAT. It may only pick among the
    classes this name is known to have held (`eras`, plus the stored answer), so
    a coercer whose charmed pet cast three Berserker abilities in one fight
    cannot be promoted to Berserker by that fight. A name that never changed
    class has one candidate and this is a no-op.
    """
    if not guess:
        return guess
    candidates = {e["class"] for e in guess.get("eras", ()) if e.get("class")}
    if guess.get("class"):
        candidates.add(guess["class"])
    if strong_here and len(candidates) > 1:
        scored = {c: strong_here.get(c, 0) for c in candidates}
        best = max(scored, key=lambda c: (scored[c], c))
        if scored[best] >= MIN_STRONG and all(
                v < scored[best] for c, v in scored.items() if c != best):
            here = next((e for e in guess.get("eras", ()) if e["class"] == best), None)
            return {**guess, "class": best,
                    "source": (here or guess).get("source", "inferred")}
    return class_at(guess, ts)


def strong_classes_here(rows, muted: set[str], cls_of: dict[str, set[str]]) -> dict:
    """(actor key, ability name) pairs -> {actor key: {class: single-class
    ability count}}, the same whole-vote evidence `_infer` counts, gathered
    from only the fights being read."""
    out: dict[str, Counter] = defaultdict(Counter)
    for key, ability in rows:
        if ability in MELEE_BUCKETS or ability in muted:
            continue
        classes = cls_of.get(ability)
        if classes and len(classes) == 1:
            out[key][next(iter(classes))] += 1
    return out


def class_at(guess: dict | None, ts: int | None) -> dict | None:
    """The class this name held at `ts`. Identity for everyone who never
    changed. An era's window is when we SAW the book, so the gap between two
    eras is split at the midpoint rather than left unanswered, and anything
    before the first era or after the last takes that end's class."""
    if not guess or not guess.get("eras") or ts is None:
        return guess
    eras = guess["eras"]
    for i, era in enumerate(eras):
        if ts <= era["to"] or i == len(eras) - 1:
            if i and ts < era["from"] and ts <= (eras[i - 1]["to"] + era["from"]) / 2:
                era = eras[i - 1]
            return {**guess, "class": era["class"], "source": era["source"],
                    "confidence": guess.get("confidence") if era is eras[-1] else 1.0}
    return guess


def parse_class_guess(text: str | None) -> dict | None:
    """Read side of `entities.class_guess`. Never raises on junk."""
    if not text:
        return None
    try:
        guess = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(guess, dict):
        return None
    # `unidentified` carries no class but is still an answer: the row is an
    # actor nothing in the log proves is a person. Blank would read as "not
    # guessed yet", which is a different thing.
    if guess.get("source") == "unidentified":
        return guess
    return guess if guess.get("class") else None


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
