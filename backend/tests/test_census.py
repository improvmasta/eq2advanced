"""Phase 4: Census sync from recorded fixtures — no live calls in CI.

Fixtures under fixtures/census/ are trimmed real Census responses for Bobby
(Wuoshi/618, Necromancer 70), captured 2026-08-02: character doc, 7 spell
records (incl. Soulrot VI Apprentice+Master), equipped items."""

import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from census.effects import parse_effect
from census.sync import base_name

FIXTURES = Path(__file__).parent / "fixtures" / "census"


class FakeCensus:
    """Fixture-backed stand-in for census.client.CensusClient."""

    def __init__(self):
        self.chars = {"bobby": json.load(open(FIXTURES / "character_bobby.json"))}
        self.spells = {s["id"]: s for s in json.load(open(FIXTURES / "spells.json"))}
        self.items = {i["id"]: i for i in json.load(open(FIXTURES / "items.json"))}
        self.calls = 0

    def character_by_name(self, name, world_id=618):
        self.calls += 1
        return copy.deepcopy(self.chars.get(name.lower()))

    def spells_by_ids(self, ids):
        return [copy.deepcopy(self.spells[i]) for i in ids if i in self.spells]

    def items_by_ids(self, ids):
        return [copy.deepcopy(self.items[i]) for i in ids if i in self.items]


@pytest.fixture(scope="module")
def fake():
    return FakeCensus()


@pytest.fixture(scope="module")
def client(tmp_path_factory, fake):
    tmp = tmp_path_factory.mktemp("eq2adv-census")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    import census.client as census_client
    mp.setattr(census_client, "_shared", fake)
    import routers.census_api as census_api
    mp.setattr(census_api, "REFRESH_COOLDOWN_S", 0)
    from main import app
    with TestClient(app) as c:
        c.post("/api/auth/register",
               json={"email": "a@x.test", "password": "hunter2hunter2"})
        yield c
    mp.undo()


def add_bobby(client):
    r = client.post("/api/characters", json={"name": "Bobby"})
    assert r.status_code in (200, 409), r.text
    if r.status_code == 200:
        return r.json()["id"]
    return next(c["id"] for c in client.get("/api/characters").json()["characters"]
                if c["name"] == "Bobby")


# ---- effects grammar (pure unit) ----

def test_effects_grammar():
    e = parse_effect("Inflicts 33 - 45 disease damage on target instantly and every second.")
    assert e == {"raw": e["raw"], "kind": "damage", "min": 33, "max": 45,
                 "dtype": "disease", "target": "target", "period_s": 1.0}
    e = parse_effect("Inflicts 90 - 110 disease damage on target instantly and every 4 seconds.")
    assert (e["kind"], e["period_s"]) == ("damage", 4.0)
    e = parse_effect("Inflicts 638 - 1064 disease damage on target encounter.")
    assert (e["kind"], e["target"], e["period_s"]) == ("damage", "target encounter", None)
    e = parse_effect("Inflicts 337 magic damage on target.")
    assert (e["min"], e["max"]) == (337, 337)
    e = parse_effect("Inflicts 3.5% of max health in magic damage on target instantly and every 4 seconds.")
    assert (e["kind"], e["pct_max_health"], e["dtype"]) == ("damage", 3.5, "magic")
    e = parse_effect("Heals target for 100 - 150 instantly and every 2 seconds.")
    assert (e["kind"], e["min"], e["max"], e["period_s"]) == ("heal", 100, 150, 2.0)
    e = parse_effect("Wards target against 500 points of all damage.")
    assert (e["kind"], e["amount"]) == ("ward", 500)
    e = parse_effect("Increases power of caster by 20 instantly and every 4 seconds.")
    assert (e["kind"], e["direction"], e["period_s"]) == ("power", "increases", 4.0)
    e = parse_effect("Increases Max Power of group members (AE) by 502.6.")
    assert (e["kind"], e["stat"], e["amount"]) == ("stat", "Max Power", 502.6)
    e = parse_effect("When damaged with a melee weapon this spell may cast Grisly Protection on target's attacker. ")
    assert (e["kind"], e["casts"]) == ("proc", "Grisly Protection")
    e = parse_effect("This effect cannot be critically applied.")
    assert e["kind"] == "note" and e["no_crit"]
    assert parse_effect("Shapechanges caster into a lich.")["kind"] == "other"


def test_base_name():
    assert base_name("Soulrot VI") == "Soulrot"
    assert base_name("Bloody Ritual III") == "Bloody Ritual"
    assert base_name("Aqueous Soul") == "Aqueous Soul"
    assert base_name("Contrapt") == "Contrapt"


# ---- sync + summary through the API ----

def test_refresh_and_summary(client, fake):
    cid = add_bobby(client)
    r = client.post(f"/api/characters/{cid}/census/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] and body["changed"]
    assert body["spells_fetched"] == 6 and body["items_fetched"] == 3

    s = client.get(f"/api/characters/{cid}/census").json()
    assert s["synced"] is True
    assert s["character"]["class"] == "Necromancer" and s["character"]["level"] == 70
    assert s["guild"] == "Skill Issue"
    stats = {k["label"]: k["value"] for k in s["key_stats"]}
    assert stats["Ability Mod"] == 1442
    assert stats["Base Modifier"] == 68.1
    assert stats["Crit Chance"] == 53.5
    gear = {g["slot"]: g for g in s["gear"]}
    assert gear["Primary"]["name"] == "Wand of Crystallized Plasma"
    assert gear["Primary"]["tier"] == "LEGENDARY"
    scribed = {sp["name"]: sp for sp in s["spells"]["scribed"]}
    assert scribed["Soulrot VI"]["tier_name"] == "Apprentice"
    assert scribed["Soulrot VI"]["base_name"] == "Soulrot"
    # Contrapt (artisan) and Investigating (classless) are not Necromancer spells
    assert s["spells"]["other_count"] == 2


def test_refresh_dedupes_unchanged_snapshot(client, fake):
    cid = add_bobby(client)
    before = fake.calls
    r = client.post(f"/api/characters/{cid}/census/refresh").json()
    assert fake.calls == before + 1
    assert r["found"] and not r["changed"] and r["spells_fetched"] == 0
    snaps = client.get(f"/api/characters/{cid}/census/snapshots").json()["snapshots"]
    assert len(snaps) == 1


def test_snapshot_diff_gear_spell_stats(client, fake):
    cid = add_bobby(client)
    doc = fake.chars["bobby"]
    doc["last_update"] += 1000
    doc["stats"]["combat"]["abilitymod"] = 1500
    doc["equipmentslot_list"][0]["item"]["id"] = 999000111  # new Primary weapon
    doc["spell_list"] = sorted(
        {i for i in doc["spell_list"] if i != 1835735656} | {575873413})  # Soulrot VI App->Master

    r = client.post(f"/api/characters/{cid}/census/refresh").json()
    assert r["changed"] and r["spells_fetched"] == 1 and r["items_fetched"] == 1
    snaps = client.get(f"/api/characters/{cid}/census/snapshots").json()["snapshots"]
    assert len(snaps) == 2

    d = client.get(
        f"/api/characters/{cid}/census/snapshots/{snaps[0]['id']}/diff").json()
    assert {"label": "Ability Mod", "from": 1442, "to": 1500, "pct": False} in d["stats"]
    assert {"slot": "Primary", "from": "Wand of Crystallized Plasma",
            "to": "Test Wand of Diffing"} in d["gear"]
    assert {"name": "Soulrot VI", "from_tier": "Apprentice",
            "to_tier": "Master"} in d["spells"]
    # oldest snapshot has nothing before it
    d0 = client.get(
        f"/api/characters/{cid}/census/snapshots/{snaps[1]['id']}/diff").json()
    assert d0.get("first") is True


def test_spell_detail(client):
    r = client.get("/api/spells/1835735656")
    assert r.status_code == 200
    sp = r.json()["spell"]
    assert sp["name"] == "Soulrot VI" and sp["tier_name"] == "Apprentice"
    assert sp["effects"][0]["kind"] == "damage"
    assert client.get("/api/spells/12345").status_code == 404


def test_character_missing_from_census(client):
    r = client.post("/api/characters", json={"name": "Ghostly"})
    cid = r.json()["id"]
    r = client.post(f"/api/characters/{cid}/census/refresh")
    assert r.status_code == 404
    assert "not found in Census" in r.json()["detail"]
    assert client.get(f"/api/characters/{cid}/census").json()["synced"] is False


def test_census_isolation(client, fake):
    cid = add_bobby(client)
    client.cookies.clear()
    client.post("/api/auth/register",
                json={"email": "b@x.test", "password": "hunter2hunter2"})
    for path in (f"/api/characters/{cid}/census",
                 f"/api/characters/{cid}/census/refresh",
                 f"/api/characters/{cid}/census/snapshots"):
        method = client.post if path.endswith("refresh") else client.get
        assert method(path).status_code == 404
    client.cookies.clear()
    client.post("/api/auth/login",
                json={"email": "a@x.test", "password": "hunter2hunter2"})
