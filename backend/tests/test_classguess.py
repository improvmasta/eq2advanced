"""Class inference: fractional votes for shared spells, the margin rule, and
evidence pooled across sessions (one player, one answer, everywhere)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3

import pytest

from pipeline.classguess import _infer, class_at, guess_session_classes

MUTED = {"Fabled Cloak Proc"}


def used(*pairs):
    """(ability, catalog class string) evidence, as the roller records it."""
    return set(pairs)


def cls_of(mapping):
    return {k: set(v) for k, v in mapping.items()}


def test_shared_spells_alone_never_carry_a_class():
    """"conjuror,necromancer" is half a vote each: real evidence against the
    other 22 classes, never enough to pick between the two."""
    ev = used(("Grim Wave", None), ("Grave Decay", None), ("Fear", None))
    catalog = cls_of({"Grim Wave": ["conjuror", "necromancer"],
                      "Grave Decay": ["conjuror", "necromancer"],
                      "Fear": ["conjuror", "necromancer"]})
    assert _infer(ev, set(), catalog) is None


def test_one_single_class_spell_breaks_a_shared_tie():
    ev = used(("Grim Wave", None), ("Grave Decay", None),
              ("Soulrot", None), ("Lifeburn", None))
    catalog = cls_of({"Grim Wave": ["conjuror", "necromancer"],
                      "Grave Decay": ["conjuror", "necromancer"],
                      "Soulrot": ["necromancer"], "Lifeburn": ["necromancer"]})
    guess = _infer(ev, set(), catalog)
    assert guess["class"] == "necromancer"
    assert guess["source"] == "inferred"


def test_margin_carries_a_class_that_gear_procs_outnumber():
    """A real one: Shaly scores 14 coercer against 7 bruiser and 4 each of
    three more — a minority of the weight cast, but double the runner-up. The
    old share-only rule refused to name a class anyone could have read off the
    ability list."""
    ev = used(*[(f"Coercer {i}", None) for i in range(14)],
              *[(f"Bruiser {i}", None) for i in range(7)],
              *[(f"Ranger {i}", None) for i in range(4)],
              *[(f"Fury {i}", None) for i in range(4)])
    catalog = cls_of({
        **{f"Coercer {i}": ["coercer"] for i in range(14)},
        **{f"Bruiser {i}": ["bruiser"] for i in range(7)},
        **{f"Ranger {i}": ["ranger"] for i in range(4)},
        **{f"Fury {i}": ["fury"] for i in range(4)},
    })
    assert _infer(ev, set(), catalog)["class"] == "coercer"


def test_a_dead_heat_stays_unanswered():
    ev = used(("A", None), ("B", None), ("C", None), ("D", None))
    catalog = cls_of({"A": ["wizard"], "B": ["wizard"],
                      "C": ["warlock"], "D": ["warlock"]})
    assert _infer(ev, set(), catalog) is None


def test_procs_and_pet_kits_never_vote():
    ev = used(("Fabled Cloak Proc", None), ("Soulrot", None), ("Lifeburn", None))
    catalog = cls_of({"Fabled Cloak Proc": ["wizard"],
                      "Soulrot": ["necromancer"], "Lifeburn": ["necromancer"]})
    assert _infer(ev, MUTED, catalog)["class"] == "necromancer"


# ---- pooling across sessions ----

SCHEMA = """
CREATE TABLE entities (id INTEGER PRIMARY KEY, session_id INT, name TEXT,
                       kind TEXT, class_guess TEXT);
CREATE TABLE abilities (id INTEGER PRIMARY KEY, name TEXT, class TEXT);
CREATE TABLE encounter_ability_stats (encounter_id INT, entity_id INT, ability_id INT);
CREATE TABLE encounters (id INTEGER PRIMARY KEY, started_ts INT);
CREATE TABLE ability_catalog (ability_name TEXT PRIMARY KEY, class TEXT,
                              unit TEXT, proc INT DEFAULT 0);
CREATE TABLE characters (name TEXT, class TEXT);
CREATE TABLE roster_classes (name_lower TEXT, world_id INT, name TEXT, class TEXT,
                             level INT, census_character_id INT, found INT, checked_ts INT);
"""


DAY = 86400
NIGHT_ONE = 1785200000


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    # one raid night, which every test that does not care about the timeline
    # hangs its ability rows on (they all write encounter_id 1)
    c.execute("INSERT INTO encounters (id, started_ts) VALUES (1, ?)", (NIGHT_ONE,))
    return c


def test_evidence_pools_across_sessions_and_writes_back(conn):
    """Zooey shows two spells in tonight's raid and two more in last week's.
    Neither file can answer alone; together they can — and the answer lands on
    BOTH entity rows, so the older raid stops saying "unknown"."""
    for i, (name, cls) in enumerate([
            ("Nightmare", "defiler"), ("Bane of Warding", "defiler"),
            ("Spirit of the Wolf", "defiler,mystic"), ("Umbral Trap", "defiler")], 1):
        conn.execute("INSERT INTO abilities (id, name, class) VALUES (?,?,?)", (i, name, cls))
        conn.execute(
            "INSERT INTO ability_catalog (ability_name, class, unit, proc) "
            "VALUES (?,?, 'player', 0)", (name, cls))
    # entity 1 = last week's session, entity 2 = tonight's
    conn.execute("INSERT INTO entities (id, session_id, name, kind) VALUES (1, 7, 'Zooey', 'player')")
    conn.execute("INSERT INTO entities (id, session_id, name, kind) VALUES (2, 8, 'Zooey', 'player')")
    for eid, abilities in ((1, (1, 3)), (2, (2, 4))):
        for aid in abilities:
            conn.execute("INSERT INTO encounter_ability_stats VALUES (1, ?, ?)", (eid, aid))

    assert guess_session_classes(conn, 8) == 2      # both rows written
    rows = {r["id"]: json.loads(r["class_guess"]) for r in
            conn.execute("SELECT id, class_guess FROM entities")}
    assert rows[1]["class"] == rows[2]["class"] == "defiler"
    assert rows[1]["source"] == "inferred"


def test_census_truth_beats_the_vote(conn):
    conn.execute("INSERT INTO abilities (id, name, class) VALUES (1, 'Soulrot', 'necromancer')")
    conn.execute("INSERT INTO ability_catalog VALUES ('Soulrot', 'necromancer', 'player', 0)")
    conn.execute("INSERT INTO entities (id, session_id, name, kind) VALUES (1, 1, 'Bobby', 'player')")
    conn.execute("INSERT INTO encounter_ability_stats VALUES (1, 1, 1)")
    conn.execute("INSERT INTO characters (name, class) VALUES ('Bobby', 'Warlock')")
    guess_session_classes(conn, 1)
    guess = json.loads(conn.execute("SELECT class_guess FROM entities").fetchone()[0])
    assert guess == {"class": "warlock", "confidence": 1.0, "matches": 1,
                     "source": "census"}


def test_an_actor_with_no_player_evidence_gets_no_class(conn):
    """Lindsay's 2026-08-04 Emerald Halls night: `Kober` fought one pull with
    real Berserker abilities and was called a Berserker at 100% confidence. A
    bare-named summoned pet is grammatically a raider and casts its class's
    actual spells, so the vote can only ever agree with itself — the log has to
    be the tiebreak, and it never said a person was behind the name.

    Both actors here show the SAME two abilities, so the roster is the only
    thing telling them apart."""
    for i, name in enumerate(("Rupture", "Raging Blow"), 1):
        conn.execute("INSERT INTO abilities (id, name, class) VALUES (?,?, 'berserker')", (i, name))
        conn.execute("INSERT INTO ability_catalog VALUES (?, 'berserker', 'player', 0)", (name,))
    for eid, name in ((1, "Kober"), (2, "Spades")):
        conn.execute("INSERT INTO entities (id, session_id, name, kind) VALUES (?, 1, ?, 'player')",
                     (eid, name))
        for aid in (1, 2):
            conn.execute("INSERT INTO encounter_ability_stats VALUES (1, ?, ?)", (eid, aid))

    guess_session_classes(conn, 1, roster=frozenset({"Spades"}))
    got = {r["name"]: json.loads(r["class_guess"]) for r in
           conn.execute("SELECT name, class_guess FROM entities")}
    assert got["Kober"] == {"class": None, "source": "unidentified"}
    assert got["Spades"]["class"] == "berserker"      # same evidence, evidenced name


def test_unidentified_survives_another_session_guessing_the_name(conn):
    """The same name can be a raider in one log and a summoned pet in the next,
    so a later session's inference must not reach back and overwrite the
    finding that THIS log had no evidence for it."""
    for i, name in enumerate(("Rupture", "Raging Blow"), 1):
        conn.execute("INSERT INTO abilities (id, name, class) VALUES (?,?, 'berserker')", (i, name))
        conn.execute("INSERT INTO ability_catalog VALUES (?, 'berserker', 'player', 0)", (name,))
    for eid, sess in ((1, 1), (2, 2)):
        conn.execute(
            "INSERT INTO entities (id, session_id, name, kind) VALUES (?, ?, 'Kober', 'player')",
            (eid, sess))
        for aid in (1, 2):
            conn.execute("INSERT INTO encounter_ability_stats VALUES (1, ?, ?)", (eid, aid))

    guess_session_classes(conn, 1, roster=frozenset())           # no evidence in this log
    guess_session_classes(conn, 2, roster=frozenset({"Kober"}))  # a real Kober in that one
    got = {r["id"]: json.loads(r["class_guess"]) for r in
           conn.execute("SELECT id, class_guess FROM entities")}
    assert got[1]["source"] == "unidentified"
    assert got[2]["class"] == "berserker"


# ---- a class that CHANGED ----

def _betrayal(conn, name, early, late):
    """`name` cast `early`'s book on night one and `late`'s on night two.
    Both books are the same size, which is what used to deadlock the vote."""
    conn.execute("INSERT INTO encounters (id, started_ts) VALUES (2, ?)", (NIGHT_ONE + 9 * DAY,))
    aid = 0
    for enc, (cls, sess) in ((1, (early, 1)), (2, (late, 2))):
        conn.execute(
            "INSERT INTO entities (id, session_id, name, kind) VALUES (?,?,?, 'player')",
            (enc, sess, name))
        for i in range(3):
            aid += 1
            ability = f"{cls} {i}"
            conn.execute("INSERT INTO abilities (id, name, class) VALUES (?,?,?)",
                         (aid, ability, cls))
            conn.execute("INSERT INTO ability_catalog VALUES (?,?, 'player', 0)", (ability, cls))
            conn.execute("INSERT INTO encounter_ability_stats VALUES (?,?,?)", (enc, enc, aid))


def test_a_betrayal_is_a_date_not_a_tie(conn):
    """Klebb cast swashbuckler abilities until 2026-07-31 and brigand abilities
    after it, 17 against 16 — a dead heat the tie rule refused, which blanked
    the class in every raid he had ever been in. The two books never overlap,
    and that is the whole answer: each session gets the class that was current
    when it was logged."""
    _betrayal(conn, "Klebb", "swashbuckler", "brigand")
    assert guess_session_classes(conn, 2) == 2      # the timeline lands on both rows
    rows = {r["session_id"]: json.loads(r["class_guess"]) for r in
            conn.execute("SELECT session_id, class_guess FROM entities")}
    # both rows carry the same timeline; the FIGHT's clock picks the class
    assert rows[1] == rows[2]
    assert rows[2]["class"] == "brigand"            # eras-blind readers see today
    assert class_at(rows[1], NIGHT_ONE)["class"] == "swashbuckler"
    assert class_at(rows[1], NIGHT_ONE + 9 * DAY)["class"] == "brigand"


def test_two_books_cast_side_by_side_are_still_a_tie(conn):
    """The split is disjointness, not "two strong classes". A raider carrying
    another class's proc gear scores stray votes all night long, and those
    interleave — so this must stay blank rather than invent a changeover."""
    _betrayal(conn, "Shaly", "coercer", "bruiser")
    # one bruiser ability on night one puts the two windows back on top of
    # each other; nobody unlearns a class and casts it again a week earlier
    conn.execute("INSERT INTO encounter_ability_stats VALUES (1, 1, 4)")
    assert guess_session_classes(conn, 2) == 0
    assert all(r["class_guess"] is None for r in
               conn.execute("SELECT class_guess FROM entities"))


def test_a_changeover_outranks_a_pooled_winner(conn):
    """Zooey's defiler half was one ability bigger than her mystic half, so the
    pooled vote called it a defiler win at 52% and stamped defiler on all four
    of her sessions — the two purely-mystic ones included. She is a Mystic
    (confirmed against Census). The timeline has to be read before the tally,
    not after it fails."""
    _betrayal(conn, "Zooey", "defiler", "mystic")
    conn.execute("INSERT INTO abilities (id, name, class) VALUES (7, 'Putrefy', 'defiler')")
    conn.execute("INSERT INTO ability_catalog VALUES ('Putrefy', 'defiler', 'player', 0)")
    conn.execute("INSERT INTO encounter_ability_stats VALUES (1, 1, 7)")   # 4 defiler v 3 mystic
    assert _infer({(f"defiler {i}", None) for i in range(3)} | {("Putrefy", None)}
                  | {(f"mystic {i}", None) for i in range(3)},
                  set(), cls_of({**{f"defiler {i}": ["defiler"] for i in range(3)},
                                 "Putrefy": ["defiler"],
                                 **{f"mystic {i}": ["mystic"] for i in range(3)}}))["class"] \
        == "defiler"                                   # what the pooled vote would say
    guess_session_classes(conn, 2)
    got = json.loads(conn.execute("SELECT class_guess FROM entities").fetchone()[0])
    assert class_at(got, NIGHT_ONE)["class"] == "defiler"
    assert class_at(got, NIGHT_ONE + 9 * DAY)["class"] == "mystic"


def test_census_dates_to_the_latest_era(conn):
    """A `characters` row says what someone is NOW. For a name that changed
    class that is a fact about the last era only — applying it to every row
    would rewrite the raids from before the switch."""
    _betrayal(conn, "Zooey", "defiler", "mystic")
    conn.execute("INSERT INTO characters (name, class) VALUES ('Zooey', 'Mystic')")
    guess_session_classes(conn, 2)
    got = json.loads(conn.execute("SELECT class_guess FROM entities").fetchone()[0])
    early, late = class_at(got, NIGHT_ONE), class_at(got, NIGHT_ONE + 9 * DAY)
    assert (early["class"], early["source"]) == ("defiler", "inferred")
    assert (late["class"], late["source"]) == ("mystic", "census")
