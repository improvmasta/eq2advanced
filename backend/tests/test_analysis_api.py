"""Class inference, proc exposure, and the two analysis endpoints (timeline +
death recap) that the zone page's Timeline and Defense tabs run on.

One small two-fight log with a real death is enough to pin all of it: the
concatenated combat clock, the per-bucket series, the death window, and the
ability-name vote that names a class the log never states."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

BASE_TS = 1754200000
CTIME = "Fri Aug 01 20:00:00 2026"

# Bobby's class-defining kit, plus one proc that must be kept OUT of the vote
# (gear fires it, so it says nothing about what he is).
CATALOG = [
    ("Soulrot", "necromancer", "player", 0),
    ("Grave Sacrament", "necromancer", "player", 0),
    ("Nightfall Curse", "necromancer", "player", 0),
    ("Lich's Siphoning", "necromancer", "player", 1),
]


def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def log() -> str:
    """Two fights in one zone, 160s apart, with Aros dying in the first."""
    out = [line(0, "You have entered Castle Mistmoore.")]
    # fight 1 — Aros takes two hits, gets one heal, dies
    out += [
        line(10, "YOUR Soulrot hits a training cube for 250 disease damage."),
        line(12, "Aros hits a training cube for 100 crushing damage."),
        line(13, "Tanky hits a training cube for 50 crushing damage."),
        line(16, "a training cube hits Aros for 400 crushing damage."),
        line(18, "Healbot heals Aros for 120 hit points."),
        line(20, "a training cube hits Aros for 900 crushing damage."),
        line(21, "a training cube has killed Aros."),
        line(26, "YOUR Soulrot hits a training cube for 260 disease damage."),
        line(28, "You have killed a training cube."),
    ]
    # fight 2 — the rest of Bobby's kit, including the proc
    out += [
        line(200, "YOUR Soulrot hits a training cube for 300 disease damage."),
        # Tanky survives fight 1 and dies in fight 2 — the merge order that
        # exposes a counter the aggregate forgets to sum
        line(202, "Tanky hits a training cube for 50 crushing damage."),
        line(204, "a training cube hits Tanky for 999 crushing damage."),
        line(205, "YOUR Grave Sacrament hits a training cube for 150 disease damage."),
        line(206, "a training cube has killed Tanky."),
        line(210, "YOUR Nightfall Curse hits a training cube for 90 disease damage."),
        line(215, "YOUR Lich's Siphoning hits a training cube for 40 disease damage."),
        line(218, "Healbot heals Bobby for 200 hit points."),
        line(220, "You have killed a training cube."),
    ]
    return "".join(out)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-analysis")
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
               json={"username": "analysis", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
        yield c
    mp.undo()


@pytest.fixture(scope="module")
def run(client):
    """Upload, then seed the ability catalog and let the API's lazy backfill
    name the classes — the same path a session parsed before Census ran takes."""
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

    from pipeline import classguess
    conn = dbmod.get_db()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO ability_catalog "
            "(ability_name, class, unit, proc, source) VALUES (?,?,?,?,'curated')",
            CATALOG)
    classguess._ATTEMPTED.clear()   # the parse already tried, on an empty catalog

    runs = client.get("/api/zone-runs").json()["zone_runs"]
    run = next(r for r in runs if r["zone"] == "Castle Mistmoore")
    encs = client.get(f"/api/zone-runs/{run['id']}").json()["encounters"]
    assert len(encs) == 2
    return {"run": run, "ids": ",".join(str(e["id"]) for e in encs)}


# ---------------------------------------------------------------- classes ---

def test_class_inferred_from_abilities(client, run):
    agg = client.get(f"/api/encounters/agg?ids={run['ids']}").json()
    bobby = next(a for a in agg["actors"] if a["name"] == "Bobby")
    assert bobby["class"] == "necromancer"
    assert bobby["class_source"] == "inferred"
    assert bobby["archetype"] == "dps"
    # three distinct class abilities voted, all for the same class
    assert bobby["class_confidence"] == 1.0


def test_class_unknown_stays_null(client, run):
    """Aros only ever autoattacks — nothing to vote with, and a guess would be
    worse than an honest blank."""
    agg = client.get(f"/api/encounters/agg?ids={run['ids']}").json()
    aros = next(a for a in agg["actors"] if a["name"] == "Aros")
    assert aros["class"] is None
    assert aros["archetype"] is None
    assert aros["class_confidence"] is None


def test_proc_flag_on_ability_rows(client, run):
    agg = client.get(f"/api/encounters/agg?ids={run['ids']}").json()
    by_name = {r["ability"]: r for r in agg["abilities"]
               if r["source_name"] == "Bobby"}
    assert by_name["Lich's Siphoning"]["proc"] is True
    assert by_name["Soulrot"]["proc"] is False


def test_proc_flag_needs_more_than_the_name():
    """Census flags a name as a proc if ANY item or buff can cast it, which
    over-claims: the classes that scribe that spell press it themselves."""
    from routers.encounters_api import _proc_flag

    procs = {"Shout"}
    classes = {"Shout": "berserker,guardian"}
    row = {"ability": "Shout", "casts": 0}

    # nothing known about the caster — the catalog is all there is
    assert _proc_flag(row, procs, classes, None) is True
    # it is in this player's own spellbook
    assert _proc_flag(row, procs, classes, "berserker") is False
    # someone else's spell going off under their name really is a proc
    assert _proc_flag(row, procs, classes, "wizard") is True
    # a prepare line was printed, so they cast it on purpose
    assert _proc_flag({"ability": "Shout", "casts": 3}, procs, classes, "wizard") is False
    # curated procs carry no class list and stay flagged for everyone
    assert _proc_flag({"ability": "Dynamism", "casts": 0}, {"Dynamism"}, {},
                      "illusionist") is True


# --------------------------------------------------------------- timeline ---

def test_timeline_concatenates_fights(client, run):
    """The 160s of downtime between the fights is not combat and must not
    appear on the axis — the clock has to agree with the summed durations the
    tables use."""
    agg = client.get(f"/api/encounters/agg?ids={run['ids']}").json()
    tl = client.get(f"/api/encounters/timeline?ids={run['ids']}").json()

    assert tl["duration_s"] == agg["encounter"]["duration_s"]
    assert tl["duration_s"] < 100          # not the 220s wall-clock span
    assert len(tl["segments"]) == 2
    assert tl["segments"][0]["start_bucket"] == 0
    assert tl["segments"][1]["start_bucket"] > 0
    assert tl["pruned"] is False


def test_timeline_series_sum_to_totals(client, run):
    agg = client.get(f"/api/encounters/agg?ids={run['ids']}").json()
    tl = client.get(f"/api/encounters/timeline?ids={run['ids']}").json()
    totals = {a["name"]: a for a in agg["actors"]}

    bobby = next(s for s in tl["series"] if s["name"] == "Bobby")
    assert len(bobby["damage"]) == tl["bucket_count"]
    assert sum(bobby["damage"]) == totals["Bobby"]["damage"]

    aros = next(s for s in tl["series"] if s["name"] == "Aros")
    assert sum(aros["taken"]) == totals["Aros"]["damage_taken"]


def test_timeline_marks_deaths(client, run):
    tl = client.get(f"/api/encounters/timeline?ids={run['ids']}").json()
    deaths = [m for m in tl["markers"] if m["type"] == "death"]
    assert sorted(m["name"] for m in deaths) == ["Aros", "Tanky"]
    assert all(0 <= m["bucket"] < tl["bucket_count"] for m in deaths)
    # Tanky dies in the second fight, so his marker sits past its start
    aros = next(m for m in deaths if m["name"] == "Aros")
    tanky = next(m for m in deaths if m["name"] == "Tanky")
    assert aros["bucket"] < tl["segments"][1]["start_bucket"] <= tanky["bucket"]


def test_agg_time_dead_matches_the_report(client, run):
    """`encounter_actor_stats.time_dead_s` used to be a column the roller never
    wrote, so the aggregate reported a confident, permanent zero. It is written
    now, and it has to agree with the raid report — the two must not be able to
    print different numbers for the same fight."""
    merged = client.get(f"/api/encounters/agg?ids={run['ids']}").json()
    tanky = next(a for a in merged["actors"] if a["name"] == "Tanky")
    assert tanky["deaths"] == 1              # he definitely died
    assert tanky["time_dead_s"] > 0

    runs = client.get("/api/zone-runs").json()["zone_runs"]
    mist = next(r for r in runs if r["zone"] == "Castle Mistmoore")
    report = client.get(f"/api/zone-runs/{mist['id']}/report").json()
    dead = sum(p["time_dead_s"] for e in report["encounters"]
               for p in e["players"] if p["name"] == "Tanky")
    assert dead == tanky["time_dead_s"]


def test_timeline_bucket_override(client, run):
    tl = client.get(f"/api/encounters/timeline?ids={run['ids']}&bucket=5").json()
    assert tl["bucket_s"] == 5
    assert tl["bucket_count"] == len(tl["series"][0]["damage"])


# ----------------------------------------------------------------- deaths ---

def test_death_recap_window(client, run):
    d = client.get(f"/api/encounters/deaths?ids={run['ids']}&window=12").json()
    assert d["window_s"] == 12
    assert [x["name"] for x in d["deaths"]] == ["Aros", "Tanky"]   # sorted by ts
    death = d["deaths"][0]
    assert death["incoming_total"] == 1300        # the 400 and the 900
    assert death["healing_total"] == 120
    assert [e["amount"] for e in death["incoming"]] == [400, 900]
    assert all(e["t"] <= 0 for e in death["incoming"])
    assert death["healing"][0]["source"] == "Healbot"


def test_death_recap_window_edges(client, run):
    """Narrowing the window drops the older events. The far edge is inclusive:
    the heal landed exactly 3s before the death and a 3s window keeps it."""
    at4 = client.get(f"/api/encounters/deaths?ids={run['ids']}&window=4").json()
    death = at4["deaths"][0]
    assert [e["amount"] for e in death["incoming"]] == [900]   # the 400 was 5s out
    assert death["incoming_total"] == 900
    assert [e["t"] for e in death["healing"]] == [-3.0]

    at3 = client.get(f"/api/encounters/deaths?ids={run['ids']}&window=3").json()
    assert [e["t"] for e in at3["deaths"][0]["healing"]] == [-3.0]


def test_death_recap_window_clamped(client, run):
    """A one-second window is not a useful question; the route clamps rather
    than returning an empty recap."""
    d = client.get(f"/api/encounters/deaths?ids={run['ids']}&window=1").json()
    assert d["window_s"] == 3
    assert client.get(
        f"/api/encounters/deaths?ids={run['ids']}&window=999").json()["window_s"] == 60


# ------------------------------------------------------------------ spark ---

def test_run_list_carries_sparkline(client, run):
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    mist = next(r for r in runs if r["zone"] == "Castle Mistmoore")
    assert len(mist["spark"]) == 2                # one raid-DPS point per fight
    assert all(isinstance(v, int) and v >= 0 for v in mist["spark"])
