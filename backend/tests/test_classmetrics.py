"""Jester's Cap uptime — the first real Class-tab metric, end to end.

The stat is only as honest as its edges, so most of these tests are edges: a
refresh is not a second buff, a buff does not survive its target's death, a
cap landed during the pull covers the opening of the fight, and a landing
nobody can be credited with is still uptime for the person who had it.

The parser half lives in test_parser.py; here the log goes in one end and a
percentage comes out the other."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from pipeline.classmetrics.troubador import (ABILITY, PROC, WINDOW_S, _coverage,
                                             _double_covered, _runs, _window_count,
                                             jesters_cap_casts, jesters_cap_uptime,
                                             potm_coverage)
from pipeline.classstats import Ctx

BASE_TS = 1754700000
CTIME = "Mon Aug 03 21:00:00 2026"
NAMED = "The Corsolander"
DUR = 30.0


# ---------------------------------------------------------------- coverage ---

def test_a_perfect_chain_is_full_uptime():
    assert _coverage([0, 30, 60], [(0, 90)], DUR, []) == 90.0


def test_an_early_refresh_is_one_buff_not_two():
    """Recasting at 20s into a 30s buff extends it to 50s — it does not add
    30s of uptime to the 30 already counted."""
    assert _coverage([0, 20], [(0, 90)], DUR, []) == 50.0


def test_a_gap_in_the_chain_is_missing_uptime():
    assert _coverage([0, 45], [(0, 90)], DUR, []) == 60.0


def test_coverage_is_clipped_to_the_fight():
    """A cap landed 20s before the pull covers only the 10s that reach into
    it, and one landed at the end does not run past the kill."""
    assert _coverage([-20], [(0, 90)], DUR, []) == 10.0
    assert _coverage([85], [(0, 90)], DUR, []) == 5.0


def test_a_death_ends_the_buff():
    """Death strips it and a rez does not bring it back — only a fresh
    landing does."""
    assert _coverage([0], [(0, 90)], DUR, [10]) == 10.0
    assert _coverage([0, 40], [(0, 90)], DUR, [10]) == 40.0


def test_a_death_outside_the_window_changes_nothing():
    assert _coverage([0], [(0, 90)], DUR, [50]) == 30.0


def test_uptime_sums_across_the_selected_fights():
    assert _coverage([0, 100], [(0, 30), (100, 130)], DUR, []) == 60.0


def test_a_cast_between_two_pulls_counts_for_neither():
    assert _coverage([50], [(0, 30), (100, 130)], DUR, []) == 0.0


# ------------------------------------------------------------ the metrics ---

class StubCtx(Ctx):
    """A Ctx with the two database doors stubbed — the metrics under test
    read events and one Census number, and nothing else."""

    def __init__(self, rows, windows, duration=DUR):
        encs = [{"id": i + 1, "started_ts": s, "ended_ts": e, "duration_s": e - s,
                 "name": NAMED, "is_named": 1} for i, (s, e) in enumerate(windows)]
        super().__init__(conn=None, enc_ids=[e["id"] for e in encs], encs=encs,
                         live_enc_ids=[e["id"] for e in encs], session_ids=[1],
                         actors=[])
        self._rows = rows
        self._duration = duration

    def events(self, types, ability=None):
        names = {ability} if isinstance(ability, str) else set(ability or ())
        return [r for r in self._rows if r["type"] in set(types)
                and (not names or r["ability"] in names)]

    def events_around(self, types, lookback_s, ability=None):
        return self.events(types, ability)

    def census_duration_s(self, ability):
        return self._duration


def land(ts, tgt, src="Vestigial", ability=ABILITY, kind="player"):
    return {"ts": ts, "type": "buff", "ability": ability, "src": src,
            "src_kind": "player", "tgt": tgt, "tgt_kind": kind}


def cast(ts, src="Vestigial", ability=ABILITY):
    return {"ts": ts, "type": "buff_cast", "ability": ability, "src": src,
            "src_kind": "player", "tgt": None, "tgt_kind": None}


def death(ts, who):
    return {"ts": ts, "type": "death", "ability": None, "src": None,
            "src_kind": None, "tgt": who, "tgt_kind": "player"}


def test_the_uptime_row_names_who_kept_it_up():
    ctx = StubCtx([land(0, "Bobby"), land(30, "Bobby"), land(60, "Bobby", src="Piedpipper")],
                  [(0, 90)])
    out = jesters_cap_uptime(ctx)
    [row] = out["rows"]
    assert (row["target"], row["uptime"], row["applications"]) == ("Bobby", 100.0, 3)
    assert row["casters"] == "Piedpipper, Vestigial"
    assert out["note"] is None


def test_two_targets_rank_by_uptime():
    ctx = StubCtx([land(0, "Bobby"), land(30, "Bobby"), land(0, "Rorschach")], [(0, 60)])
    rows = jesters_cap_uptime(ctx)["rows"]
    assert [r["target"] for r in rows] == ["Bobby", "Rorschach"]
    assert [round(r["uptime"]) for r in rows] == [100, 50]


def test_an_uncredited_landing_is_still_uptime_and_is_flagged():
    """Two troubadors inside one second leaves the landing with no caster.
    The person still had the buff — what is unknown is whose it was."""
    ctx = StubCtx([land(0, "Bobby", src=None)], [(0, 30)])
    out = jesters_cap_uptime(ctx)
    assert out["rows"][0]["uptime"] == 100.0
    assert out["rows"][0]["casters"] == "—"
    assert "uncredited" in out["note"]


def test_a_buff_on_a_mob_is_not_a_raider_stat():
    ctx = StubCtx([land(0, "The Corsolander", kind="mob")], [(0, 30)])
    assert jesters_cap_uptime(ctx)["rows"] == []


def test_another_buffs_landing_is_not_counted():
    ctx = StubCtx([land(0, "Bobby", ability="Aria of Magic")], [(0, 30)])
    assert jesters_cap_uptime(ctx)["rows"] == []


def test_a_missing_census_row_is_said_out_loud():
    ctx = StubCtx([land(0, "Bobby")], [(0, 30)], duration=None)
    out = jesters_cap_uptime(ctx)
    assert out["rows"][0]["uptime"] == 100.0      # the 30s fallback
    assert "No Census row" in out["note"]


def test_the_cast_table_separates_pressing_from_landing():
    ctx = StubCtx([cast(0), cast(30), cast(60, src="Piedpipper"),
                   land(1, "Bobby"), land(31, "Rorschach"),
                   land(61, "Bobby", src="Piedpipper")], [(0, 90)])
    rows = jesters_cap_casts(ctx)
    assert rows[0] == {"actor": "Vestigial", "casts": 2, "landed": 2, "targets": 2}
    assert rows[1] == {"actor": "Piedpipper", "casts": 1, "landed": 1, "targets": 1}


def test_a_troubador_seen_only_by_their_landings_still_gets_a_row():
    """Their cast line was out of chat range; the landing on somebody nearby
    was not."""
    ctx = StubCtx([land(0, "Bobby", src="Moklok")], [(0, 30)])
    assert jesters_cap_casts(ctx) == [
        {"actor": "Moklok", "casts": 0, "landed": 1, "targets": 1}]


# ------------------------------------------------- Perfection of the Maestro ---
#
# No cast line, no landing line — the proc is the only evidence, so every
# number here is a floor and the tests say which floor.

def proc(ts, src, ability=PROC):
    return {"ts": ts, "type": "damage", "ability": ability, "src": src,
            "src_kind": "player", "tgt": NAMED, "tgt_kind": "mob"}


def test_procs_close_together_are_one_covered_stretch():
    """A gap inside a window is the player not casting, not the buff dropping
    — up to the calibrated tolerance. One proc proves one second."""
    assert _runs([10, 12, 13]) == [(10, 14)]
    assert _runs([10]) == [(10, 11)]


def test_a_gap_wider_than_the_tolerance_splits_the_stretch():
    """Past it the log stops proving anything, so the metric stops claiming."""
    assert _runs([10, 20]) == [(10, 11), (20, 21)]


def test_window_count_is_casts_not_choppiness():
    """A caster who pauses twice inside one window has three stretches and one
    buff; a proc a full duration later cannot be the same cast."""
    assert _window_count([0, 5, 10, 20]) == 1
    assert _window_count([0, int(WINDOW_S), int(WINDOW_S) * 2]) == 3
    assert _window_count([]) == 0


def test_a_run_that_fits_one_window_is_not_double_cover():
    assert _double_covered([(0, 30)]) == 0.0
    assert _double_covered([(0, 32)]) == 0.0        # clock + join tolerance


def test_a_run_longer_than_the_buff_proves_a_second_cast():
    """PotM's 90s recast means one troubador cannot chain it, so 40 covered
    seconds took two casts — 60s of buff for 40s of cover."""
    assert _double_covered([(0, 40)]) == 20.0


def test_potm_coverage_reports_a_floor_per_player():
    ctx = StubCtx([proc(t, "Iynk") for t in (0, 2, 4)] + [proc(1, "Bobby")], [(0, 100)])
    rows = potm_coverage(ctx)
    assert [r["player"] for r in rows] == ["Iynk", "Bobby"]
    assert rows[0]["covered"] == 5.0 and rows[0]["windows"] == 1
    assert rows[0]["coverage"] == 5.0               # 5 proven seconds of 100
    assert rows[0]["wasted"] is None


def test_another_ability_is_not_potm_evidence():
    ctx = StubCtx([proc(0, "Iynk", ability="Dissonant Note")], [(0, 100)])
    assert potm_coverage(ctx) == []


def test_a_pets_damage_is_not_its_owners_buff():
    rows = [proc(0, "Grim Sorcerer")]
    rows[0]["src_kind"] = "own_pet"
    assert potm_coverage(StubCtx(rows, [(0, 100)])) == []


def test_double_cover_surfaces_on_the_row():
    """The RoK question asked early: when the buff goes raid-wide, a stretch
    longer than one window is a second troubador's cast thrown away."""
    ctx = StubCtx([proc(t, "Iynk") for t in range(0, 40, 2)], [(0, 100)])
    [row] = potm_coverage(ctx)
    assert row["longest"] == 39
    assert row["wasted"] == 21.0                    # two casts (60s) for 39 covered


# --------------------------------------------------------------------- api ---

def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def log() -> str:
    """One 64s pull with a troubador chaining Jester's Cap onto the logger:
    landings at t=2 and t=32, so 60 of the fight's seconds are covered."""
    out = [
        line(0, "You have entered Veeshan's Peak."),
        # proves Vestigial is a person and not a summoned pet (refine.py)
        line(0, "Vestigial has joined the raid."),
    ]
    for t in range(0, 60, 12):
        out.append(line(t, f"YOUR Soulrot hits {NAMED} for 900 disease damage."))
        out.append(line(t + 3, f"YOUR Bloodcoil hits {NAMED} for 700 disease damage."))
        out.append(line(t + 6, f"Vestigial's Perfect Shrill hits {NAMED} "
                               f"for 300 mental damage."))
        out.append(line(t + 9, f"Vestigial's Thunderous Overture hits {NAMED} "
                               f"for 200 mental damage."))
    for t in (1, 31):
        out.append(line(t, "Vestigial begins to play the song of the Jester."))
        out.append(line(t + 1, "You feel inspired by the Jester."))
    # PotM leaves no cast line at all — its proc IS the evidence. Ten seconds
    # of it, inside the fight so segmentation is unchanged.
    for t in range(5, 16, 2):
        out.append(line(t, f"YOUR Precise Note hits {NAMED} for 300 mental damage."))
    out.append(line(64, f"You have killed {NAMED}."))
    out.sort(key=lambda x: int(x[1:11]))
    return "".join(out)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-classmetrics")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        c.post("/api/auth/register",
               json={"username": "metrictest", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
        conn = dbmod.get_db()
        with conn:
            # ability_catalog comes from cached Census spells in production
            conn.executemany(
                "INSERT OR REPLACE INTO ability_catalog "
                "(ability_name, class, unit, proc, scribed, source) "
                "VALUES (?,?, 'player', 0, 1, 'census')",
                [("Soulrot", "necromancer"), ("Bloodcoil", "necromancer"),
                 ("Perfect Shrill", "troubador"), ("Thunderous Overture", "troubador")])
            # the metric reads the buff's duration from Census, not from a
            # constant — one row is enough to prove it joins
            conn.execute(
                "INSERT OR REPLACE INTO census_spells "
                "(spell_id, name, base_name, class, level, tier, duration_s, recast_s) "
                "VALUES (1, ?, ?, 'troubador', 65, 1, 30.0, 30.0)", (ABILITY, ABILITY))
        yield c
    mp.undo()


@pytest.fixture(scope="module")
def selection(client):
    r = client.post("/api/uploads", files={"file": ("a.txt", log().encode())},
                    data={"character_name": "Bobby"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(300):
        s = client.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] == "ready":
            break
        if s["status"] == "error":
            raise AssertionError(s["error"])
        time.sleep(0.05)
    else:
        raise AssertionError("parse timed out")
    encs = client.get(f"/api/sessions/{sid}").json()["encounters"]
    return ",".join(str(e["id"]) for e in encs)


def test_the_troubador_section_carries_the_cap_stats(client, selection):
    body = client.get("/api/encounters/class-stats",
                      params={"ids": selection}).json()
    troub = next(s for s in body["classes"] if s["class"] == "troubador")
    metrics = {m["key"]: m for m in troub["metrics"]}
    assert set(metrics) == {"jesters_cap_uptime", "jesters_cap_casts",
                            "potm_coverage"}

    uptime = metrics["jesters_cap_uptime"]
    assert uptime["status"] == "ok"
    [row] = uptime["rows"]
    assert row["target"] == "Bobby"          # "You feel inspired…" is the logger
    assert row["applications"] == 2
    assert row["casters"] == "Vestigial"
    # 60 covered seconds of a ~64s fight, and never over 100
    assert 85 <= row["uptime"] <= 100

    casts = metrics["jesters_cap_casts"]
    assert casts["rows"] == [
        {"actor": "Vestigial", "casts": 2, "landed": 2, "targets": 1}]

    potm = metrics["potm_coverage"]
    assert potm["status"] == "ok"
    [row] = potm["rows"]
    assert row["player"] == "Bobby"        # the proc is the logger's own
    assert row["covered"] == 11.0          # procs at 5..15, the last one proving its second
    assert row["windows"] == 1


def test_the_buff_did_not_disturb_the_parse(client, selection):
    """Buff lines are read by this tab and nothing else: they must not open an
    encounter, feed a damage rollup or grow an ability row."""
    detail = client.get("/api/encounters/agg", params={"ids": selection}).json()
    assert len(detail["encounter_ids"]) == 1
    assert not [a for a in detail["abilities"] if a["ability"] == ABILITY]
    vest = next(a for a in detail["actors"] if a["name"] == "Vestigial")
    assert vest["class"] == "troubador"
