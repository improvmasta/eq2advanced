"""What the site LEARNS about a mob's AoE timers across every raid on it.

Two claims are under test and they are different in kind. The base timer is a
measurement that replaces a number the raid uploaded — so the tests are about
when it is allowed to, and the answer is "several agreeing intervals across
several fights, none of them under a reuse debuff". The swipe verdict is a
comparison of two populations from the same mob — so the tests are about the
band in the middle, where the honest answer is that we do not know yet and the
countdown says so rather than guessing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import db as dbmod
from pipeline import aoelearn


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dbmod._local, "conn", None, raising=False)
    dbmod.init_db()
    c = dbmod.get_db()
    yield c
    c.close()


def cycles(conn, *, mob="Mayong Mistmoore", ability="Soul Paralysis",
           gaps=(), swiped=0, fight=1, named=1, ts=1000):
    """`gaps` intervals of one ability in one fight.

    The session and encounter are real rows because the cycle table points at
    them — a cycle belongs to a fight, and a rebuild of that fight has to be
    able to take it away again (`clear_derived`)."""
    conn.execute("INSERT OR IGNORE INTO users (id, username, pw_hash, salt, "
                 "created_ts) VALUES (1,'t',x'00',x'00',0)")
    conn.execute("INSERT OR IGNORE INTO characters (id, user_id, name) "
                 "VALUES (1,1,'Bobby')")
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, character_id, source, status, "
        "pinned, pruned, calibration, retain_raw, redacted_lines, created_ts) "
        "VALUES (1,1,'upload','done',0,0,0,1,0,0)")
    conn.execute(
        "INSERT OR IGNORE INTO encounters (id, session_id, name, is_named, "
        "started_ts, ended_ts, duration_s) VALUES (?,1,?,?,?,?,?)",
        (fight, mob, named, ts, ts + 600, 600))
    conn.executemany(
        "INSERT OR REPLACE INTO aoe_cycles (encounter_id, session_id, "
        "source_name, ability, cast_ts, gap_s, swiped, is_named) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(fight, 1, mob, ability, ts + i * 1000, g, swiped, named)
         for i, g in enumerate(gaps)])
    conn.commit()


def other_raider(conn, *, mob="Mayong Mistmoore", ability="Soul Paralysis",
                 gaps=(), fight=99, ts=1000, span=600, char=2, session=2):
    """The SAME pull, logged by a second raider — their own character, their
    own session, their own encounter row. Nothing marks these as related: the
    zone-run dedupe is one character's overlapping files and must stay that
    way, since each parse is that player's own observation."""
    conn.execute("INSERT OR IGNORE INTO characters (id, user_id, name) "
                 "VALUES (?,1,?)", (char, f"Raider{char}"))
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, character_id, source, status, "
        "pinned, pruned, calibration, retain_raw, redacted_lines, created_ts) "
        "VALUES (?,?,'upload','done',0,0,0,1,0,0)", (session, char))
    conn.execute(
        "INSERT OR IGNORE INTO encounters (id, session_id, name, is_named, "
        "started_ts, ended_ts, duration_s) VALUES (?,?,?,1,?,?,?)",
        (fight, session, mob, ts, ts + span, span))
    conn.executemany(
        "INSERT OR REPLACE INTO aoe_cycles (encounter_id, session_id, "
        "source_name, ability, cast_ts, gap_s, swiped, is_named) "
        "VALUES (?,?,?,?,?,?,0,1)",
        [(fight, session, mob, ability, ts + i * 1000, g)
         for i, g in enumerate(gaps)])
    conn.commit()


# --- one pull is one fight, however many people logged it ------------------

def test_two_raiders_logging_one_pull_is_one_fight(conn):
    """The bug this exists to stop. `MIN_FIGHTS` is there so a timer needs more
    than one raid night behind it — "two fights is what makes it a timer
    instead of an anecdote" — and counting each uploader's copy separately let a
    SINGLE pull satisfy it. Measured on the real corpus: 5,034 named encounters
    are 4,773 real pulls."""
    cycles(conn, gaps=(44, 44, 43, 44), fight=1, ts=1000)
    other_raider(conn, gaps=(43, 44, 44, 43), fight=99, ts=1000)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]
    assert row["base_fights"] == 1
    assert row["base_s"] is None          # one pull is still an anecdote


def test_two_real_pulls_still_adopt(conn):
    cycles(conn, gaps=(44, 44, 43, 44), fight=1, ts=1000)
    other_raider(conn, gaps=(43, 44, 44, 43), fight=99, ts=90000)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]
    assert row["base_fights"] == 2
    assert row["base_s"] == pytest.approx(43.5, abs=0.2)


def test_a_raider_who_engaged_late_is_still_the_same_pull(conn):
    """Why identity is OVERLAP and not start time. A 15s start-delta rule gets
    92% of the corpus and misses 19 pairs whose overlap is 100% — a short
    encounter sitting entirely inside somebody else's, which is exactly what a
    late engage looks like and exactly what a start delta cannot see."""
    cycles(conn, gaps=(44, 44, 43, 44), fight=1, ts=1000)      # ts 1000-1600
    other_raider(conn, gaps=(43, 44, 44, 43), fight=99, ts=1300, span=200)
    assert aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]["base_fights"] == 1


def test_back_to_back_pulls_of_the_same_trash_stay_two_pulls(conn):
    """The other population, and the reason the threshold is where it is: same
    name, adjacent in time, no overlap. 1,424 such pairs in the corpus against
    247 genuine same-pull ones, with nothing in between."""
    cycles(conn, gaps=(44, 44, 43, 44), fight=1, ts=1000)      # ends 1600
    other_raider(conn, gaps=(43, 44, 44, 43), fight=99, ts=1700, span=600)
    assert aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]["base_fights"] == 2


def test_three_logs_of_one_pull_collapse_to_one(conn):
    cycles(conn, gaps=(44, 44, 43, 44), fight=1, ts=1000)
    other_raider(conn, gaps=(43, 44), fight=98, ts=1010, char=2, session=2)
    other_raider(conn, gaps=(44, 43), fight=97, ts=1020, char=3, session=3)
    assert aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]["fights"] == 1


def test_a_different_mob_at_the_same_moment_is_a_different_pull(conn):
    cycles(conn, gaps=(44, 44, 43, 44), fight=1, ts=1000)
    other_raider(conn, mob="Chel'Drak", gaps=(44, 44, 43, 44), fight=99, ts=1000)
    got = aoelearn.learn(conn)
    assert got[("Mayong Mistmoore", "Soul Paralysis")]["fights"] == 1
    assert got[("Chel'Drak", "Soul Paralysis")]["fights"] == 1


def test_narrowing_to_one_mob_gives_the_same_pull_identity(conn):
    """`learn(sources=...)` narrows the encounter read too, and grouping is
    per name, so a subset must answer identically for the names it covers."""
    cycles(conn, gaps=(44, 44, 43, 44), fight=1, ts=1000)
    other_raider(conn, gaps=(43, 44, 44, 43), fight=99, ts=1000)
    full = aoelearn.pull_keys(conn)
    one = aoelearn.pull_keys(conn, {"Mayong Mistmoore"})
    assert one and all(full[k] == v for k, v in one.items())


def test_no_cycles_is_no_opinion(conn):
    """An empty table has to read exactly like the behaviour before it existed:
    nothing learned, so the countdown falls back to ACT's list."""
    assert aoelearn.learn(conn) == {}
    assert aoelearn.timer_for({}, "X", "Y", 45) == (45.0, "reported")
    assert aoelearn.timer_for({}, "X", "Y", None) == (None, "none")


def test_one_fight_is_an_anecdote(conn):
    """Everything that makes a single pull unrepresentative — a stun, an add
    wearing the boss's name, a wipe at 40% — is a property of that pull."""
    cycles(conn, gaps=(44, 44, 43, 44, 44, 43, 44), fight=1)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]
    assert row["clean_s"] is not None          # measured
    assert row["base_s"] is None               # but not adopted
    assert row["base_fights"] == 1


def test_enough_agreeing_intervals_across_fights_replaces_the_act_number(conn):
    cycles(conn, gaps=(44, 44, 43, 44), fight=1)
    cycles(conn, gaps=(43, 44, 44, 43), fight=2, ts=90000)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]
    assert row["base_s"] == pytest.approx(43.5, abs=0.2)
    assert row["base_fights"] == 2
    # and it is what the countdown counts with, over ACT's 37
    got, src = aoelearn.timer_for(aoelearn.learn(conn),
                                  "Mayong Mistmoore", "Soul Paralysis", 37)
    assert got == row["base_s"] and src == "learned"


def test_curator_override_beats_learned_and_reported(conn):
    cycles(conn, gaps=(44, 44, 43, 44), fight=1)
    cycles(conn, gaps=(43, 44, 44, 43), fight=2, ts=90000)
    conn.execute("INSERT INTO timer_rulings(source_name,ability,override_s,note,"
                 "decided_ts) VALUES('Mayong Mistmoore','Soul Paralysis',46,'verified',1)")
    rows = aoelearn.learn(conn)
    assert aoelearn.timer_for(rows, "Mayong Mistmoore", "Soul Paralysis", 37) == (46.0, "curated")


def test_curator_exclusion_turns_countdown_off(conn):
    cycles(conn, gaps=(44, 44, 43, 44), fight=1)
    conn.execute("INSERT INTO timer_rulings(source_name,ability,excluded,note,"
                 "decided_ts) VALUES('Mayong Mistmoore','Soul Paralysis',1,'shield',1)")
    rows = aoelearn.learn(conn)
    assert aoelearn.timer_for(rows, "Mayong Mistmoore", "Soul Paralysis", 37) == (None, "excluded")


def test_a_swiped_cycle_never_teaches_a_base_timer(conn):
    """A fight everybody swiped through says nothing about the mob's own
    recast, however many intervals agree."""
    cycles(conn, gaps=(58, 58, 57, 58, 58, 57, 58, 58), swiped=1, fight=1)
    cycles(conn, gaps=(58, 58, 57, 58), swiped=1, fight=2, ts=90000)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]
    assert row["base_s"] is None and row["clean_s"] is None
    assert row["swiped_s"] is not None


def test_a_measured_stretch_is_a_verdict(conn):
    cycles(conn, gaps=(44, 44, 43, 44), fight=1)
    cycles(conn, gaps=(58, 58, 57, 58), swiped=1, fight=1, ts=50000)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]
    assert row["swipe_verdict"] == "affected"
    assert row["swipe_factor"] == pytest.approx(1.32, abs=0.03)


def test_an_ability_that_does_not_move_is_recorded_as_immune(conn):
    """`Whirling Bladestorm` under a debuff two brigands held for 98% of a
    fight ran 53s against its own 50 — which is not a stretch, it is noise, and
    a countdown must not be adjusted for it."""
    cycles(conn, ability="Whirling Bladestorm", gaps=(50, 50, 51, 50), fight=1)
    cycles(conn, ability="Whirling Bladestorm", gaps=(50, 51, 50, 50),
           swiped=1, fight=1, ts=50000)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Whirling Bladestorm")]
    assert row["swipe_verdict"] == "immune"


def test_the_band_in_the_middle_stays_unknown(conn):
    """Between "clearly moved" and "clearly did not" the answer is that nobody
    knows yet — which is the state the panel draws as `swiped?` and settles by
    watching one more cast, rather than a coin flip either way."""
    cycles(conn, gaps=(50, 50, 51, 50), fight=1)
    cycles(conn, gaps=(56, 56, 57, 56), swiped=1, fight=1, ts=50000)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]
    assert 1.10 < row["swipe_factor"] < 1.15
    assert row["swipe_verdict"] is None


def test_one_side_of_the_comparison_is_not_a_comparison(conn):
    cycles(conn, gaps=(44, 44, 43, 44), fight=1)
    cycles(conn, gaps=(58,), swiped=1, fight=1, ts=50000)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Soul Paralysis")]
    assert row["swipe_verdict"] is None


def test_several_mobs_sharing_a_name_never_teach_a_timer(conn):
    """Six mobs of one name cast on six timers and read as one mob casting six
    times as often. More fights cannot fix it — every one of them counts the
    same six mobs — so the guard is the ratio, not the sample size."""
    # `Flame Claw` is on the ACT list at 22s; two mobs read as 11s
    cycles(conn, mob="Nizari'ishi denizen", ability="Flame Claw", named=0,
           gaps=(11, 11, 11, 11), fight=1)
    cycles(conn, mob="Nizari'ishi denizen", ability="Flame Claw", named=0,
           gaps=(11, 11, 11, 11), fight=2, ts=90000)
    row = aoelearn.learn(conn)[("Nizari'ishi denizen", "Flame Claw")]
    assert row["clean_s"] == 11.0 and row["base_s"] is None


def test_a_named_is_one_mob_and_is_never_explained_away(conn):
    """18s against ACT's 37 is exactly the shape that disqualifies a trash
    name, and a boss is one body — so this one is a measurement, not an
    ambiguity, and it is adopted."""
    cycles(conn, ability="Mayong's Touch", gaps=(18, 18, 19, 18), fight=1)
    cycles(conn, ability="Mayong's Touch", gaps=(18, 19, 18, 18), fight=2,
           ts=90000)
    row = aoelearn.learn(conn)[("Mayong Mistmoore", "Mayong's Touch")]
    assert row["base_s"] is not None
    assert row["several_bodies"] is None


def test_two_mobs_casting_one_ability_learn_two_timers(conn):
    """Timers are keyed by (MOB, ability) all the way down — the cycle rows,
    the derived answer and `timer_for`. Only ACT's list is keyed by ability
    alone, because that is ACT's file; a shared entry is where BOTH of these
    start, and neither is where either ends."""
    for f, ts in ((1, 1000), (2, 90000)):
        cycles(conn, mob="a fairy honorguard", ability="Faith Strike",
               named=0, gaps=(24, 25, 24, 25), fight=f, ts=ts)
        cycles(conn, mob="a fallen paladin", ability="Faith Strike",
               named=0, gaps=(31, 30, 31, 30), fight=f + 10, ts=ts + 40000)
    rows = aoelearn.learn(conn)
    assert rows[("a fairy honorguard", "Faith Strike")]["base_s"] == \
        pytest.approx(24.5, abs=0.2)
    assert rows[("a fallen paladin", "Faith Strike")]["base_s"] == \
        pytest.approx(30.5, abs=0.2)
    # and the countdown asks per mob, so the two never see each other's number
    assert aoelearn.timer_for(rows, "a fairy honorguard", "Faith Strike", 20)[0] \
        != aoelearn.timer_for(rows, "a fallen paladin", "Faith Strike", 20)[0]


def test_a_mob_that_splits_never_teaches_a_timer_however_much_it_agrees(conn):
    """The Emerald Halls rumbler, and the reason more evidence cannot fix this
    one: every fight counts the same two halves, so 21 agreeing intervals
    across 4 uploads are 21 pieces of evidence for a number that is wrong. The
    site had adopted 28.7s against ACT's 50 before this guard existed."""
    for f, ts in ((1, 1000), (2, 90000), (3, 180000), (4, 270000)):
        cycles(conn, mob="A Bisected Rumbler", ability="Rumbling of Earth",
               named=0, gaps=(28, 29, 28, 29, 28, 29), fight=f, ts=ts)
    row = aoelearn.learn(conn)[("A Bisected Rumbler", "Rumbling of Earth")]
    assert row["base_agree"] >= aoelearn.MIN_AGREE       # the gates it passes
    assert row["base_fights"] >= aoelearn.MIN_FIGHTS
    assert row["base_s"] is None                         # and the one it does not
    assert row["several_bodies"] == "splits"
    # so the countdown is still ACT's number, which is one half's real recast
    assert aoelearn.timer_for(aoelearn.learn(conn), "A Bisected Rumbler",
                              "Rumbling of Earth", 50) == (50.0, "reported")


def test_the_typical_factor_comes_from_confirmed_rows_not_the_tooltip(conn):
    """The ability reads -50% reuse speed. What it measures is ~x1.3, and the
    estimate an unconfirmed row runs on toward has to be the second one."""
    assert aoelearn.typical_factor({}) == aoelearn.FALLBACK_FACTOR
    rows = {("m", "a"): {"swipe_verdict": "affected", "swipe_factor": 1.29},
            ("m", "b"): {"swipe_verdict": "affected", "swipe_factor": 1.31},
            ("m", "c"): {"swipe_verdict": "affected", "swipe_factor": 1.33},
            ("m", "d"): {"swipe_verdict": None, "swipe_factor": 9.0}}
    assert aoelearn.typical_factor(rows) == 1.31


def test_the_read_is_cached_until_the_cycle_table_changes(conn):
    cycles(conn, gaps=(44, 44, 43, 44), fight=1)
    first = aoelearn.learned(conn)
    assert aoelearn.learned(conn) is first          # same object, not recomputed
    cycles(conn, gaps=(43, 44, 44, 43), fight=2, ts=90000)
    assert aoelearn.learned(conn) is not first      # a parse landed; re-read
