"""Zone-run API: listing/visibility, dup exclusion, cross-session agg merge,
and the run-scoped raid report."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod

BASE_TS = 1754000000
CTIME = "Fri Aug 01 20:00:00 2026"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-zoneruns")
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
               json={"username": "runs", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
        yield c
    mp.undo()


def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def fight(t: int, mob="a training cube", named=None) -> list[str]:
    # the raid damages the thing it kills — encounters are named after the
    # enemy fought, so a log that hits X and kills Y is not a real log
    target = named or mob
    return [
        line(t, f"YOUR Soulrot hits {target} for 250 disease damage."),
        line(t + 5, f"Aros hits {target} for 100 crushing damage."),
        line(t + 10, f"YOUR Soulrot hits {target} for 260 disease damage."),
        line(t + 12, f"You have killed {target}."),
    ]


def log_a() -> str:
    """Zone 1 with two fights, then zone 2 with one."""
    lines = [line(0, "You have entered Castle Mistmoore.")]
    lines += fight(10, named="Traininglord the Unstoppable")
    lines += fight(100)
    lines += [line(200, "You have entered The Estate of Unrest.")]
    lines += fight(210, named="Hagfiend the Vile")
    return "".join(lines)


def log_b() -> str:
    """One more Mistmoore fight BETWEEN log_a's Mistmoore fights and its Unrest
    trip — lands inside the same run, proving cross-file merging."""
    lines = [line(140, "You have entered Castle Mistmoore.")]
    lines += fight(150, named="Interloper the Third")
    return "".join(lines)


def log_a_overlap() -> str:
    """log_a's exact fight lines plus a trailing zone line: different file
    bytes (defeats the whole-file sha256), identical fights (exercises
    content-level dedupe)."""
    return log_a() + line(400, "You have entered Loping Plains.")


def upload(client, content: str, name: str) -> int:
    r = client.post("/api/uploads", files={"file": (name, content.encode())},
                    data={"character_name": "Bobby"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    for _ in range(300):
        s = client.get(f"/api/sessions/{sid}").json()["session"]
        if s["status"] == "ready":
            return sid
        if s["status"] == "error":
            raise AssertionError(s["error"])
        time.sleep(0.05)
    raise AssertionError("parse timed out")


@pytest.fixture(scope="module")
def uploaded(client):
    sid_a = upload(client, log_a(), "a.txt")
    sid_b = upload(client, log_b(), "b.txt")
    sid_dup = upload(client, log_a_overlap(), "a-overlap.txt")
    assert len({sid_a, sid_b, sid_dup}) == 3
    return {"a": sid_a, "b": sid_b, "dup": sid_dup}


def test_run_list_grouping(client, uploaded):
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    by_zone = {}
    for r in runs:
        by_zone.setdefault(r["zone"], []).append(r)
    # log_b's fight interleaves log_a's Mistmoore visit — one run across files
    assert len(by_zone["Castle Mistmoore"]) == 1
    assert by_zone["Castle Mistmoore"][0]["encounter_count"] == 3
    assert by_zone["Castle Mistmoore"][0]["named_count"] == 2
    assert len(by_zone["The Estate of Unrest"]) == 1
    assert runs[0]["character_name"] == "Bobby"


def test_dup_encounters_excluded(client, uploaded):
    """a-again.txt duplicates log_a byte-for-byte: every encounter dup-marked,
    nothing double-counted in runs or run detail."""
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    mist = next(r for r in runs if r["zone"] == "Castle Mistmoore")
    detail = client.get(f"/api/zone-runs/{mist['id']}").json()
    assert len(detail["encounters"]) == 3
    names = [e["name"] for e in detail["encounters"]]
    assert names.count("Traininglord the Unstoppable") == 1
    # logger headline present per encounter
    assert all(e["logger_damage"] for e in detail["encounters"])


def test_cross_session_agg_merges_by_name(client, uploaded):
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    mist = next(r for r in runs if r["zone"] == "Castle Mistmoore")
    enc = client.get(f"/api/zone-runs/{mist['id']}").json()["encounters"]
    ids = ",".join(str(e["id"]) for e in enc)
    agg = client.get(f"/api/encounters/agg?ids={ids}").json()
    assert len(agg["session_ids"]) == 2
    bobby = next(a for a in agg["actors"] if a["name"] == "Bobby")
    # 2 fights in log_a (510*2) + 1 in log_b (510) = 1530, across 2 entity rows
    assert bobby["damage"] == 1530
    assert len(bobby["entity_ids"]) == 2
    assert bobby["key"] == "Bobby|player"
    soulrot = next(r for r in agg["abilities"]
                   if r["ability"] == "Soulrot" and r["source_name"] == "Bobby")
    assert soulrot["total"] == 1530 and soulrot["hits"] == 6
    assert soulrot["median"] == 255
    assert soulrot["rollup_key"] == "Bobby|player"


def test_run_report_rollup_by_name(client, uploaded):
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    mist = next(r for r in runs if r["zone"] == "Castle Mistmoore")
    rep = client.get(f"/api/zone-runs/{mist['id']}/report").json()
    assert rep["zone_run_id"] == mist["id"]
    night = {n["name"]: n for n in rep["night"]}
    assert night["Bobby"]["damage"] == 1530
    assert night["Aros"]["damage"] == 300
    assert len(rep["encounters"]) == 3


def test_visibility_other_user(client, uploaded):
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    c2 = TestClient(client.app)
    c2.post("/api/auth/register",
            json={"username": "other", "password": "hunter2hunter2"})
    assert c2.get("/api/zone-runs").json()["zone_runs"] == []
    assert c2.get(f"/api/zone-runs/{runs[0]['id']}").status_code == 404
    assert c2.get(f"/api/zone-runs/{runs[0]['id']}/report").status_code == 404
    # cross-session agg checks every session
    enc = client.get(f"/api/zone-runs/{runs[0]['id']}").json()["encounters"]
    ids = ",".join(str(e["id"]) for e in enc)
    assert c2.get(f"/api/encounters/agg?ids={ids}").status_code == 404


def test_player_search(client, uploaded):
    """Substring, case-insensitive, over visible rosters; run_count across
    zones (Bobby raided Mistmoore AND Unrest)."""
    players = {p["name"]: p for p in
               client.get("/api/players?q=BO").json()["players"]}
    assert players["Bobby"]["run_count"] == 2
    assert "Aros" not in players
    assert any(p["name"] == "Aros"
               for p in client.get("/api/players?q=ro").json()["players"])
    assert client.get("/api/players?q=b").status_code == 422


def test_player_runs(client, uploaded):
    runs = client.get("/api/players/Aros/runs").json()["runs"]
    assert {r["zone"] for r in runs} == {"Castle Mistmoore", "The Estate of Unrest"}
    assert all(r["character_name"] == "Bobby" for r in runs)
    # names come from the search; an unknown one is empty, not 404
    assert client.get("/api/players/Nobody/runs").json()["runs"] == []


def test_player_search_visibility(client, uploaded):
    """Signed out (and any non-owner), rosters exist only for published runs —
    same predicate as the list."""
    c2 = TestClient(client.app)
    assert c2.get("/api/players?q=bo").json()["players"] == []
    assert c2.get("/api/players/Aros/runs").json()["runs"] == []


def test_guild_key_in_payloads(client, uploaded):
    """The tag is a column, so it rides `z.*` into both payloads. Untagged is
    NULL, and a picker that never sees the key cannot degrade gracefully."""
    runs = client.get("/api/zone-runs").json()["zone_runs"]
    assert all("guild" in r for r in runs)
    detail = client.get(f"/api/zone-runs/{runs[0]['id']}").json()["zone_run"]
    assert "guild" in detail
    # nothing has been voted on here — no Census in tests
    assert detail["guild"] is None


def test_list_roster_opt_in(client, uploaded):
    """`?roster=1` sends the names parsed; the default sends neither them nor
    the column they are stored in."""
    plain = client.get("/api/zone-runs").json()["zone_runs"]
    assert all("roster" not in r and "roster_json" not in r for r in plain)

    withr = client.get("/api/zone-runs?roster=1").json()["zone_runs"]
    assert all("roster_json" not in r for r in withr)
    rosters = {r["id"]: r["roster"] for r in withr}
    assert all(isinstance(v, list) for v in rosters.values())
    assert any("Aros" in v for v in rosters.values())


def test_list_named_opt_in(client, uploaded):
    """`?roster=1` also carries each night's named mobs and the encounter ids
    that are that fight — the Compare picker's named-mob facet, and what lets
    picking one land a column already scoped to the boss."""
    plain = client.get("/api/zone-runs").json()["zone_runs"]
    assert all("named" not in r for r in plain)

    runs = client.get("/api/zone-runs?roster=1").json()["zone_runs"]
    mist = next(r for r in runs if r["zone"] == "Castle Mistmoore")
    assert [n["name"] for n in mist["named"]] == [
        "Traininglord the Unstoppable", "Interloper the Third"]
    # ids are real encounters of that run, and the dup upload adds none
    detail = client.get(f"/api/zone-runs/{mist['id']}").json()["encounters"]
    by_name = {e["name"]: e["id"] for e in detail}
    assert [n["ids"] for n in mist["named"]] == [
        [by_name["Traininglord the Unstoppable"]], [by_name["Interloper the Third"]]]
    # trash keeps its own name and is not a named mob
    assert all(n["name"] != "a training cube" for n in mist["named"])


def test_roster_opt_in_respects_visibility(client, uploaded):
    """Same predicate as the list — asking for rosters is not a way around it."""
    c2 = TestClient(client.app)
    assert c2.get("/api/zone-runs?roster=1").json()["zone_runs"] == []
    c2.post("/api/auth/register",
            json={"username": "rosternosy", "password": "hunter2hunter2"})
    assert c2.get("/api/zone-runs?roster=1").json()["zone_runs"] == []
