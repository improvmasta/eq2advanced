"""The Class tab's contract: the registry, the payload shape, and the two
failure modes a class metric has to survive.

These tests pin the PIPE, not any particular stat: the registry is emptied for
every test here (see `clean_registry`) and refilled with stubs, so the shape
stays covered no matter what real metrics exist. A metric declares columns and
returns rows, a metric that raises is isolated rather than taking the tab down
with it, and an event-reading metric on a pruned selection says so instead of
reporting zeroes. The real ones are tested in test_classmetrics.py."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from pipeline import classstats
from pipeline.classstats import Column, Ctx

BASE_TS = 1754600000
CTIME = "Mon Aug 03 21:00:00 2026"
NAMED = "The Corsolander"


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """Every test gets its own registry — a stub metric must never leak into
    the next test, or into the app."""
    monkeypatch.setattr(classstats, "_REGISTRY", {})


def enc_row(eid=1, started=BASE_TS, duration=100):
    return {"id": eid, "started_ts": started, "ended_ts": started + duration,
            "duration_s": duration, "name": NAMED, "is_named": 1}


def ctx(actors, *, live=(1,), conn=None):
    return Ctx(conn=conn, enc_ids=[1], encs=[enc_row()], live_enc_ids=list(live),
               session_ids=[1], actors=actors)


TROUB = {"name": "Vestigial", "kind": "player", "class": "troubador",
         "key": "Vestigial|player", "damage": 100, "class_source": "census"}
NECRO = {"name": "Bobby", "kind": "player", "class": "necromancer",
         "key": "Bobby|player", "damage": 900, "class_source": "census"}


# ---------------------------------------------------------------- registry ---

def test_a_metric_is_reachable_by_its_class_and_nobody_else():
    @classstats.register(key="stub", cls="troubador", label="Stub",
                         blurb="…", columns=[Column("actor", "Troubador")])
    def _stub(c):
        return []

    assert [m.key for m in classstats.metrics_for("troubador")] == ["stub"]
    assert classstats.metrics_for("dirge") == []
    assert classstats.registered_classes() == {"troubador"}


def test_a_duplicate_key_within_a_class_is_a_startup_error():
    def add():
        @classstats.register(key="dup", cls="troubador", label="Stub",
                             blurb="…", columns=[Column("actor", "Troubador")])
        def _stub(c):
            return []
    add()
    with pytest.raises(ValueError, match="duplicate metric"):
        add()


def test_a_column_unit_outside_the_vocabulary_is_refused():
    """The frontend formats by unit; an unknown one would render as a blank
    cell in production and as an error here."""
    with pytest.raises(ValueError, match="unknown column unit"):
        classstats.register(key="bad", cls="troubador", label="Stub", blurb="…",
                            columns=[Column("uptime", "Uptime", "furlongs")])


# ----------------------------------------------------------------- collect ---

def test_every_class_in_the_raid_gets_a_section_even_with_no_metrics():
    out = classstats.collect(ctx([TROUB, NECRO]))
    assert [s["class"] for s in out["classes"]] == ["troubador", "necromancer"]
    assert all(s["metrics"] == [] for s in out["classes"])
    assert out["classes"][0]["actors"][0]["name"] == "Vestigial"


def test_sections_are_ordered_by_role_not_by_damage():
    """Utility before DPS — the class rail reads like a raid frame, and the
    troubador here did a ninth of the necromancer's damage."""
    out = classstats.collect(ctx([NECRO, TROUB]))
    assert [s["archetype"] for s in out["classes"]] == ["utility", "dps"]


def test_an_unpinned_class_is_listed_apart_rather_than_guessed():
    mystery = {"name": "Rorschach", "kind": "player", "class": None,
               "key": "Rorschach|player"}
    out = classstats.collect(ctx([TROUB, mystery]))
    assert out["unclassified"] == ["Rorschach"]
    assert [s["class"] for s in out["classes"]] == ["troubador"]


def test_an_unidentified_name_is_not_a_raider_of_unknown_class():
    """`unidentified` means nothing in the log proved a person — a summoned
    pet fights and casts exactly like a raider. It belongs in neither list."""
    pet = {"name": "Rorschach", "kind": "player", "class": None,
           "class_source": "unidentified", "key": "Rorschach|player"}
    out = classstats.collect(ctx([TROUB, pet]))
    assert out["unclassified"] == []
    assert [s["class"] for s in out["classes"]] == ["troubador"]


def test_rows_and_a_note_come_back_intact():
    @classstats.register(key="stub", cls="troubador", label="Cap uptime",
                         blurb="what it cannot see",
                         columns=[Column("actor", "Troubador"),
                                  Column("uptime", "Uptime", "pct")])
    def _stub(c):
        return {"rows": [{"actor": a["name"], "uptime": 62.0}
                         for a in c.players("troubador")],
                "note": "one caster was out of range"}

    [section] = classstats.collect(ctx([TROUB]))["classes"]
    [metric] = section["metrics"]
    assert metric["status"] == "ok"
    assert metric["rows"] == [{"actor": "Vestigial", "uptime": 62.0}]
    assert metric["note"] == "one caster was out of range"
    assert [c["unit"] for c in metric["columns"]] == ["text", "pct"]


def test_the_denominator_is_the_selection_not_one_fight():
    c = Ctx(conn=None, enc_ids=[1, 2],
            encs=[enc_row(1, BASE_TS, 100), enc_row(2, BASE_TS + 500, 200)],
            live_enc_ids=[1, 2], session_ids=[1], actors=[TROUB])
    assert c.duration_s == 300


# ------------------------------------------------------------ failure modes ---

def test_one_broken_metric_does_not_take_out_the_tab():
    @classstats.register(key="boom", cls="troubador", label="Broken",
                         blurb="…", columns=[Column("actor", "Troubador")])
    def _boom(c):
        raise ZeroDivisionError("census said 0")

    @classstats.register(key="fine", cls="troubador", label="Fine",
                         blurb="…", columns=[Column("actor", "Troubador")])
    def _fine(c):
        return [{"actor": "Vestigial"}]

    [section] = classstats.collect(ctx([TROUB]))["classes"]
    broken, fine = section["metrics"]
    assert (broken["status"], broken["rows"]) == ("error", [])
    assert broken["note"]
    assert (fine["status"], fine["rows"]) == ("ok", [{"actor": "Vestigial"}])


def test_a_metric_returning_the_wrong_shape_is_reported_not_rendered():
    @classstats.register(key="wrong", cls="troubador", label="Wrong",
                         blurb="…", columns=[Column("actor", "Troubador")])
    def _wrong(c):
        return "62%"

    [section] = classstats.collect(ctx([TROUB]))["classes"]
    assert section["metrics"][0]["status"] == "error"


def test_an_event_reading_metric_on_a_pruned_selection_says_so():
    """Pruning keeps the rollups and drops the events. Zero uptime and no
    events are different answers and must not render the same."""
    @classstats.register(key="needs", cls="troubador", label="Needs events",
                         blurb="…", columns=[Column("actor", "Troubador")],
                         needs_events=True)
    def _needs(c):
        raise AssertionError("must not run without events")

    [section] = classstats.collect(ctx([TROUB], live=()))["classes"]
    metric = section["metrics"][0]
    assert metric["status"] == "pruned"
    assert "pruned" in metric["note"].lower()


# --------------------------------------------------------------------- api ---

def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def log() -> str:
    """One named pull: the logger nuking, a troubador singing at it. Two
    single-class abilities each (classguess.MIN_STRONG), so both raiders
    resolve from the log alone — the tests never reach Census."""
    out = [line(0, "You have entered Veeshan's Peak.")]
    for t in range(0, 60, 12):
        out.append(line(t, f"YOUR Soulrot hits {NAMED} for 900 disease damage."))
        out.append(line(t + 3, f"YOUR Bloodcoil hits {NAMED} for 700 disease damage."))
        out.append(line(t + 6, f"Vestigial's Perfect Shrill hits {NAMED} "
                               f"for 300 mental damage."))
        out.append(line(t + 9, f"Vestigial's Thunderous Overture hits {NAMED} "
                               f"for 200 mental damage."))
    out.append(line(64, f"You have killed {NAMED}."))
    return "".join(out)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-classstats")
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
               json={"username": "classtest", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
        # class inference reads ability_catalog, which is filled from CACHED
        # CENSUS SPELLS in production and is empty in a fresh test database —
        # the curated seed is pet kits and procs, neither of which names a
        # class. Four rows is what a class costs (classguess.MIN_STRONG = 2).
        conn = dbmod.get_db()
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO ability_catalog "
                "(ability_name, class, unit, proc, scribed, source) "
                "VALUES (?,?, 'player', 0, 1, 'census')",
                [("Soulrot", "necromancer"), ("Bloodcoil", "necromancer"),
                 ("Perfect Shrill", "troubador"), ("Thunderous Overture", "troubador")])
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


def test_api_returns_a_section_per_class_in_the_parse(client, selection):
    r = client.get("/api/encounters/class-stats", params={"ids": selection})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pruned"] is False
    classes = {s["class"] for s in body["classes"]}
    assert "necromancer" in classes
    for s in body["classes"]:
        assert s["actors"], "a section with nobody in it should not exist"
        assert s["metrics"] == []          # the registry is stubbed empty here


def test_api_serves_a_registered_metric_end_to_end(client, selection, monkeypatch):
    @classstats.register(key="casts", cls="necromancer", label="Casts seen",
                         blurb="…", columns=[Column("actor", "Necromancer"),
                                             Column("n", "Casts", "num")],
                         needs_events=True)
    def _casts(c):
        rows = c.events(("damage",))
        return [{"actor": a["name"],
                 "n": sum(1 for e in rows if e["src"] == a["name"])}
                for a in c.players("necromancer")]

    body = client.get("/api/encounters/class-stats",
                      params={"ids": selection}).json()
    necro = next(s for s in body["classes"] if s["class"] == "necromancer")
    [metric] = necro["metrics"]
    assert metric["status"] == "ok"
    assert metric["rows"][0]["n"] == 10          # five Soulrot + five Bloodcoil


def test_api_refuses_an_unreadable_selection(client, selection):
    other = TestClient(client.app)
    other.post("/api/auth/register",
               json={"username": "classstranger", "password": "hunter2hunter2"})
    r = other.get("/api/encounters/class-stats", params={"ids": selection})
    assert r.status_code in (403, 404)


def test_api_rejects_junk_ids(client):
    assert client.get("/api/encounters/class-stats",
                      params={"ids": "abc"}).status_code == 422
