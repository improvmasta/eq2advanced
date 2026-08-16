"""Phase 4: Census sync from recorded fixtures — no live calls in CI.

Fixtures under fixtures/census/ are trimmed real Census responses for Bobby
(Wuoshi/618, Necromancer 70), captured 2026-08-02: character doc, 7 spell
records (incl. Soulrot VI Apprentice+Master), equipped items."""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from census.effects import parse_effect, parse_effects
from census.sync import (base_name, planner_character_stats,
                         planner_item_stats, typed_fields)

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

    def spell_page(self, cls, max_level, start):
        rows = [copy.deepcopy(s) for s in self.spells.values()
                if cls in (s.get("classes") or {})
                and (s.get("level") or 0) <= max_level]
        return rows[start:start + 2]  # small pages exercise the resume loop

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
               json={"username": "censusa", "password": "hunter2hunter2"})
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


def test_typed_fields():
    spells = {s["id"]: s for s in json.load(open(FIXTURES / "spells.json"))}
    soulrot = spells[1835735656]  # Soulrot VI Apprentice
    t = typed_fields(soulrot, parse_effects(soulrot["effect_list"]))
    assert t["cast_s"] == 2.0 and t["recast_s"] == 3
    assert t["recovery_s"] == 0.5  # _tenths field stores hundredths
    assert t["duration_s"] == 4.0 and t["power_cost"] == 60
    assert (t["dmg_min"], t["dmg_max"], t["dmg_dtype"], t["dmg_period_s"]) == \
        (33, 45, "disease", 1.0)
    aqueous = spells[680981520]  # buff, no damage effect
    t = typed_fields(aqueous, parse_effects(aqueous["effect_list"]))
    assert t["cast_s"] == 3.0 and t["duration_s"] == 900.0
    assert t["dmg_min"] is None and t["dmg_dtype"] is None


def test_base_name():
    assert base_name("Soulrot VI") == "Soulrot"
    assert base_name("Bloody Ritual III") == "Bloody Ritual"
    assert base_name("Aqueous Soul") == "Aqueous Soul"
    assert base_name("Contrapt") == "Contrapt"


def test_planner_stats_translate_census_items_and_character_totals():
    item = {"modifiers": {
        "all": {"value": 98},
        "basemodifier": {"value": 3.7},
        "spelltimecastpct": {"value": 2.1},
        "arcane": {"value": 280},
        "critbonus": {"value": 9},       # live-only and deliberately absent
    }, "typeinfo": {"maxarmorclass": 42}}
    assert planner_item_stats(item) == {
        "abmod": 98.0, "potency": 3.7, "acspeed": 2.1,
        "vsarcane": 280.0, "mit": 42.0,
    }
    doc = json.load(open(FIXTURES / "character_bobby.json"))
    totals = planner_character_stats(doc)
    assert totals["abmod"] == 1442
    assert totals["potency"] == 68.1
    assert totals["crit"] == 53.48
    assert totals["int"] == doc["stats"]["int"]["effective"]


# ---- sync + summary through the API ----

def test_refresh_and_summary(client, fake):
    cid = add_bobby(client)
    r = client.post(f"/api/characters/{cid}/census/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] and body["changed"]
    assert body["spells_fetched"] == 6 and body["items_fetched"] == 4

    s = client.get(f"/api/characters/{cid}/census").json()
    assert s["synced"] is True
    assert s["character"]["class"] == "Necromancer" and s["character"]["level"] == 70
    assert s["character"]["census_id"] == 2654289790664
    assert s["guild"] == "Skill Issue"
    stats = {k["label"]: k["value"] for k in s["key_stats"]}
    assert stats["Ability Mod"] == 1442
    assert stats["Base Modifier"] == 68.1
    assert stats["Crit Chance"] == 53.5
    gear = {g["slot"]: g for g in s["gear"]}
    assert gear["Primary"]["name"] == "Wand of Crystallized Plasma"
    assert gear["Primary"]["tier"] == "LEGENDARY"
    assert gear["Primary"]["key"] == "primary"
    assert gear["Primary"]["planner_stats"] == {}
    adorn = gear["Primary"]["adornments"][0]
    assert adorn["id"] == 274745776 and adorn["name"] == "Swift Casting"
    assert adorn["planner_stats"] == {"acspeed": 2.0}
    assert adorn["stats"]["adornment"]["color"] == "white"
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
                json={"username": "censusb", "password": "hunter2hunter2"})
    for path in (f"/api/characters/{cid}/census",
                 f"/api/characters/{cid}/census/refresh",
                 f"/api/characters/{cid}/census/snapshots"):
        method = client.post if path.endswith("refresh") else client.get
        assert method(path).status_code == 404
    client.cookies.clear()
    client.post("/api/auth/login",
                json={"username": "censusa", "password": "hunter2hunter2"})


# ---- bulk spell ingest (phase 7 groundwork) ----

def test_bulk_ingest_and_backfill(client, fake):
    """ingest_class_spells caches the whole class book with typed columns and
    line markers; backfill_typed_columns repairs pre-migration rows."""
    from census.sync import backfill_typed_columns, ingest_class_spells
    conn = dbmod.get_db()
    res = ingest_class_spells(conn, fake, "necromancer", 70, page_sleep_s=0)
    # fixture necro records: Aqueous Soul, Bloody Ritual III, Unholy Covenant V,
    # Soulrot VI Apprentice + Master
    assert res["spells"] == 5
    assert res["lines"] == conn.execute(
        "SELECT COUNT(DISTINCT crc) FROM census_spells WHERE class LIKE "
        "'%necromancer%' AND crc IS NOT NULL").fetchone()[0]
    row = conn.execute(
        "SELECT * FROM census_spells WHERE spell_id=575873413").fetchone()
    assert row["cast_s"] == 2.0 and row["recovery_s"] == 0.5
    assert (row["dmg_min"], row["dmg_max"], row["dmg_dtype"]) == (56, 76, "disease")
    assert conn.execute("SELECT 1 FROM settings WHERE key=?",
                        (f"spell_line:{row['crc']}",)).fetchone() is not None
    # completion clears the resume offset (pages persist as they land, so an
    # interrupted class picks up mid-book instead of restarting from zero)
    assert conn.execute("SELECT 1 FROM settings WHERE key LIKE "
                        "'ingest_progress:%'").fetchone() is None

    # a pre-migration row (typed columns NULL) gets repaired from its json blob
    with conn:
        conn.execute("UPDATE census_spells SET cast_s=NULL, dmg_min=NULL "
                     "WHERE spell_id=575873413")
    assert backfill_typed_columns(conn) == 1
    row = conn.execute(
        "SELECT cast_s, dmg_min FROM census_spells WHERE spell_id=575873413").fetchone()
    assert row["cast_s"] == 2.0 and row["dmg_min"] == 56


# ---- roster lookups: class AND guild, from the one doc ----

def char_doc(name, cls="mystic", guild=None, level=70):
    """A trimmed character doc in the shape roster.resolve reads. `guild=None`
    means the key is absent, which is how Census says 'in no guild'."""
    doc = {"id": abs(hash(name)) % 10**8, "name": {"first": name},
           "type": {"class": cls, "level": level}}
    if guild:
        doc["guild"] = {"name": guild, "guildid": 38, "rank": 3}
    return doc


class RosterFake:
    """Answers by name; anything not listed is a miss (a mob, a pet, a typo)."""

    def __init__(self, docs=None, boom=()):
        self.docs = docs or {}
        self.boom = set(boom)
        self.asked = []

    def character_by_name(self, name, world_id=618):
        self.asked.append(name)
        if name.lower() in self.boom:
            raise RuntimeError("census is down")
        return copy.deepcopy(self.docs.get(name.lower()))


def test_resolve_captures_guild(client):
    """The doc that answers 'what class' also carries the guild, so both are
    cached from the one request — and 'no guild' is recorded as an ANSWER."""
    from census import roster
    conn = dbmod.get_db()
    fake = RosterFake({"zooey": char_doc("Zooey", "mystic", "Freethinkers"),
                       "solo": char_doc("Solo", "brigand")})
    report = roster.resolve(conn, fake, ["Zooey", "Solo", "Enynti"], 618, now=1000)
    assert (report["found"], report["missing"], report["failed"]) == (2, 1, 0)

    rows = {r["name_lower"]: r for r in conn.execute(
        "SELECT * FROM roster_classes WHERE world_id=618")}
    assert rows["zooey"]["guild_name"] == "Freethinkers"
    assert rows["zooey"]["guild_id"] == 38
    assert rows["zooey"]["guild_checked"] == 1
    # guildless: asked, answered, no guild — a fact, and it votes
    assert rows["solo"]["guild_name"] is None and rows["solo"]["guild_checked"] == 1
    # a name Census does not have has no guild fact at all, so it abstains
    assert rows["enynti"]["found"] == 0 and rows["enynti"]["guild_checked"] == 0


def test_resolve_force_refetches_fresh_rows(client):
    """A row cached before guilds existed is not stale by any clock — it is
    just missing a field, which is what `force` is for."""
    from census import roster
    conn = dbmod.get_db()
    fake = RosterFake({"tarn": char_doc("Tarn", "templar", "Freethinkers")})
    roster.resolve(conn, fake, ["Tarn"], 618, now=2000)
    with conn:   # pretend it predates v20
        conn.execute("UPDATE roster_classes SET guild_name=NULL, guild_checked=0 "
                     "WHERE name_lower='tarn'")

    fake.asked.clear()
    assert roster.resolve(conn, fake, ["Tarn"], 618, now=2001)["asked"] == 0
    assert fake.asked == []          # the TTL says it is fresh, and it is

    assert roster.resolve(conn, fake, ["Tarn"], 618, now=2001, force=True)["found"] == 1
    assert fake.asked == ["Tarn"]
    row = conn.execute("SELECT * FROM roster_classes WHERE name_lower='tarn'").fetchone()
    assert row["guild_name"] == "Freethinkers" and row["guild_checked"] == 1


def test_resolve_network_failure_writes_nothing(client):
    """An outage is not an answer — it must not cache a guildless row over a
    real character."""
    from census import roster
    conn = dbmod.get_db()
    fake = RosterFake(boom=["ghosty"])
    report = roster.resolve(conn, fake, ["Ghosty"], 618, now=3000)
    assert report["failed"] == 1
    assert conn.execute("SELECT 1 FROM roster_classes WHERE name_lower='ghosty'"
                        ).fetchone() is None


# ---- the by-name lookup behind /plan's loadout (no account) ----

def test_a_character_loads_by_name_with_no_account(client, fake):
    """A CENSUS CHARACTER RECORD IS PUBLIC, so trying gear on your own toon is
    not the one part of a signed-out page that needs signing up. The answer is
    the SAME shape an owned character produces — one builder, so the path
    nobody is signed in for cannot quietly lose a field."""
    signed_out = TestClient(client.app)
    r = signed_out.get("/api/plan/character", params={"name": "Bobby"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["character"]["name"].lower().startswith("bobby")
    assert body["character"]["public"] is True
    assert body["character"]["id"] is None          # nothing is owned
    assert body["synced"] is True and body["gear"]
    assert "planner_stats" in body


def test_a_second_lookup_is_answered_from_the_cache(client, fake):
    signed_out = TestClient(client.app)
    signed_out.get("/api/plan/character", params={"name": "Bobby"})
    before = fake.calls
    signed_out.get("/api/plan/character", params={"name": "bobby"})   # case too
    assert fake.calls == before


def test_a_lookup_falls_back_to_the_cache_when_census_is_unreachable(client, fake):
    """CENSUS INTERMITTENCY IS NORMAL AND IS NOT AN OUTAGE. A reader planning
    gear does not care that the record is six hours old; they care that the
    page works."""
    from census import sync as census_sync
    conn = dbmod.get_db()

    class Dead:
        def character_by_name(self, name, world_id=618):
            raise RuntimeError("census down")

    census_sync.lookup_by_name(conn, fake, "Bobby")                  # seed
    out = census_sync.lookup_by_name(conn, Dead(), "Bobby", refresh=True)
    assert out is not None and out["gear"]


def test_a_name_census_does_not_know_is_a_404_and_is_not_re_asked(client, fake):
    signed_out = TestClient(client.app)
    assert signed_out.get("/api/plan/character",
                          params={"name": "Nobodyatall"}).status_code == 404
    before = fake.calls
    assert signed_out.get("/api/plan/character",
                          params={"name": "Nobodyatall"}).status_code == 404
    # The MISS is cached too, so a typo does not re-ask Census every time.
    assert fake.calls == before


def test_the_lookup_cache_is_refreshed_on_a_schedule(client, fake):
    """A LOOKUP CACHE NOBODY REFRESHES GOES STALE IN ONE DIRECTION. `/plan`
    only ever refills a row when a human types that name, so a character
    somebody looked up once keeps that night's gear until somebody types them
    again — and a name typed for the first time DURING an outage answers
    nothing, because there is no row to fall back to. The census probe that
    already runs every 30 minutes refreshes the stalest rows instead.

    Bounded, oldest first, and it stops rather than hammering a Census that
    has gone away mid-run."""
    from census import sync as census_sync
    conn = dbmod.get_db()

    # Earlier tests in this file leave their own cached rows behind, and this
    # one is counting requests.
    conn.execute("DELETE FROM plan_characters")
    census_sync.lookup_by_name(conn, fake, "Bobby")
    conn.execute("UPDATE plan_characters SET fetched_ts = 0")
    conn.commit()

    before = fake.calls
    out = census_sync.refresh_cached_lookups(conn, fake)
    assert out["checked"] == 1 and out["found"] == 1 and out["queued"] == 0
    assert fake.calls > before                     # it really did re-ask

    # A row inside the window is left alone: this is a trickle, not a re-read.
    quiet = fake.calls
    assert census_sync.refresh_cached_lookups(conn, fake)["checked"] == 0
    assert fake.calls == quiet


def test_the_refresh_stops_when_census_goes_away_mid_run(client, fake):
    from census import sync as census_sync
    from census.client import CensusError
    conn = dbmod.get_db()

    conn.execute("DELETE FROM plan_characters")
    census_sync.lookup_by_name(conn, fake, "Bobby")
    conn.execute("UPDATE plan_characters SET fetched_ts = 0")
    conn.commit()

    class Dying:
        def character_by_name(self, name, world_id=618):
            raise CensusError("census unavailable")

    out = census_sync.refresh_cached_lookups(conn, Dying())
    assert out["stopped"] is True and out["checked"] == 0
    # and the cached row is untouched, so the page still answers from it
    assert census_sync.lookup_by_name(conn, Dying(), "Bobby") is not None
