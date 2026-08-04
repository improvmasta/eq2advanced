"""Class inference: fractional votes for shared spells, the margin rule, and
evidence pooled across sessions (one player, one answer, everywhere)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3

import pytest

from pipeline.classguess import _infer, guess_session_classes

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
CREATE TABLE ability_catalog (ability_name TEXT PRIMARY KEY, class TEXT,
                              unit TEXT, proc INT DEFAULT 0);
CREATE TABLE characters (name TEXT, class TEXT);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
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
