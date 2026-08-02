"""Golden test over the real raid night (/home/lindsay/bobby.txt). Skipped when
the fixture isn't present (e.g. CI). Rerun after ANY parser/segmentation change."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser import parse_lines
from parser.events import F_SELF_FOCUS
from pipeline.encounters import segment_events

GOLDEN = Path("/home/lindsay/bobby.txt")

pytestmark = pytest.mark.skipif(not GOLDEN.exists(), reason="golden fixture not present")


@pytest.fixture(scope="module")
def parsed():
    with GOLDEN.open(encoding="utf-8", errors="replace") as fh:
        events = list(parse_lines(fh, "Bobby"))
    return events, segment_events(events, "Bobby")


def test_event_volume(parsed):
    events, _ = parsed
    assert len(events) > 190_000


def test_no_unmatched_damage_lines(parsed):
    # every body ending " damage." must classify (parse_lines drops unknowns,
    # so recheck raw here)
    from parser.classify import classify_body
    from parser.prefix import split_prefix
    unmatched = 0
    with GOLDEN.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            p = split_prefix(line)
            if not p:
                continue
            ts, body = p
            if body.endswith(" damage.") and classify_body(ts, body, "Bobby") is None:
                unmatched += 1
    assert unmatched == 0


def test_zones(parsed):
    events, _ = parsed
    zones = [e.extra["zone"] for e in events if e.type == "zone"]
    assert "Freethinker Hideout" in zones
    assert "The Estate of Unrest" in zones


def test_segments_and_named_fights(parsed):
    _, segs = parsed
    assert len(segs) == 10
    names = " | ".join(s.name for s in segs if s.is_named)
    for boss in ("Zylphax the Shredder", "Othysis Muravian", "Treyloth D'Kulvith",
                 "Malkonis D'Morte", "The Festering Hag", "The Hemogoblin",
                 "Garanel Rucksif, the Cursed"):
        assert boss in names
    # players' pets never label an encounter
    assert "Lunar Attendant" not in names
    assert "unswerving hammer" not in names


def test_bobby_attribution(parsed):
    events, _ = parsed
    player = pet = swarm = focus = 0
    for e in events:
        if e.type == "damage" and e.src and e.src.name == "Bobby":
            amt = e.amount or 0
            if e.src.unit == "player":
                player += amt
                if e.flags & F_SELF_FOCUS:
                    focus += amt
            elif e.src.unit == "own_pet":
                pet += amt
            elif e.src.unit == "swarm_pet":
                swarm += amt
    assert player > 15_000_000        # Bobby the Necromancer did real damage
    assert pet > 5_000_000            # the Grim Sorcerer worked too
    assert swarm > 2_000_000          # hordes contributed
    assert 0 < focus < player * 0.15  # Vampiric Requiem self-damage exists but is bounded


def test_prepare_count(parsed):
    # 918 raw prepare lines; 234 are the client's same-second exact-duplicate
    # log artifact, collapsed by parse_lines — 684 real cast starts
    events, _ = parsed
    flavors = [e for e in events if e.type == "cast_flavor"]
    assert len(flavors) == 684
    # every necromancer flavor in the fixture resolves to an ability
    assert all(e.ability for e in flavors)
    assert sum(1 for e in flavors if e.ability == "Soulrot") == 312


def test_deaths_and_revives(parsed):
    events, _ = parsed
    assert sum(1 for e in events if e.type == "revive") == 3
    assert sum(1 for e in events if e.type == "death") >= 5


def test_act_parity_zylphax(parsed):
    """Anchor against ACT's parse of the same night (Lindsay's screenshot,
    2026-08-02): the Zylphax the Shredder encounter, per-player damage. Bobby's
    number is the regression guard for logger-swarm-pet rollup — ACT rolls his
    hordes into him, and so must we."""
    import sqlite3 as _sqlite3

    from db import SCHEMA
    from pipeline.ingest_writer import EntityResolver, _resolve_events
    from pipeline.statsroll import roll_encounter

    events, segments = parsed
    conn = _sqlite3.connect(":memory:")
    conn.row_factory = _sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO characters (id, name, world_id) VALUES (1, 'Bobby', 618)")
    conn.execute(
        "INSERT INTO sessions (id, character_id, source, status, created_ts) "
        "VALUES (1, 1, 'upload', 'ready', 0)")
    res = EntityResolver(conn, 1, "Bobby")
    resolved = _resolve_events(events, res)
    seg = next(s for s in segments if s.name == "Zylphax the Shredder")
    actor_stats, _ = roll_encounter(
        [resolved[i] for i in seg.event_indices], max(seg.end_ts - seg.start_ts, 1))

    def damage(name):
        eid = conn.execute(
            "SELECT id FROM entities WHERE name=? AND kind='player'", (name,)).fetchone()[0]
        return actor_stats[eid]["damage"]

    assert damage("Bobby") == 3_750_904    # direct + Grim Sorcerer + all three hordes
    assert damage("Spades") == 5_391_907   # guards the self-hit exclusion
    assert damage("Beaux") == 3_567_018
    assert damage("Aros") == 3_395_213
    assert damage("Silkey") == 2_708_054
    assert damage("Thinkbigsti") == 18_559
