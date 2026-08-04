"""Phase 5: coach engine (fit round-trip, replay monotonicity, advisor
snapshot, calibration override) + raid report, all through the API on a
synthetic log with a KNOWN coefficient.

The synthetic Bobby log casts Soulrot (scribed Soulrot VI Apprentice: 33-45,
mid 39). At Bobby's fixture stats (abilitymod 1442, basemodifier 68.1) the
model expects 39*1.681 + min(1442, 19.5) = 85.06 per non-crit; hits are
written at ~3x that, so the fit must recover k ~= 3.0.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from test_census import FakeCensus

BASE_TS = 1754000000
CTIME = "Fri Aug 01 20:00:00 2026"
TRUE_K = 3.0
EXPECTED_NONCRIT = 39 * (1 + 68.100006 / 100) + min(1442.0, 39 / 2)


class FakeCensusWithLines(FakeCensus):
    def spells_by_crcs(self, crcs):
        import copy
        return [copy.deepcopy(s) for s in self.spells.values()
                if s.get("crc") in set(crcs)]


@pytest.fixture(scope="module")
def fake():
    return FakeCensusWithLines()


@pytest.fixture(scope="module")
def client(tmp_path_factory, fake):
    tmp = tmp_path_factory.mktemp("eq2adv-coach")
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
               json={"username": "coach", "password": "hunter2hunter2"})
        cid = c.post("/api/characters", json={"name": "Bobby"}).json()["id"]
        assert c.post(f"/api/characters/{cid}/census/refresh").status_code == 200
        yield c
    mp.undo()


def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


NAMED = "Traininglord the Unstoppable"


def soulrot(t: int, amount: int, crit: bool = False, tgt: str = "a training cube") -> str:
    c = "a critical of " if crit else ""
    return line(t, f"YOUR Soulrot hits {tgt} for {c}{amount} disease damage.")


def synthetic_log() -> str:
    """Encounter 1 (named, 90s): 12 non-crit Soulrots (250/260 alternating),
    6 crits (357), casts 5s apart (the ACT-parity cutter closes on >=7s of
    combat silence); Aros engages at +6s with autoattacks and dies at +26s.
    Encounter 2 after a long gap (trash): 4 more non-crits."""
    lines = []
    amts = [250, 260] * 6
    # encounter 1 fights the named it kills — an encounter is titled after the
    # enemy engaged, so damaging one mob and killing another is not a real log
    for i in range(12):
        lines.append(soulrot(i * 5, amts[i], tgt=NAMED))
    for i in range(6):
        lines.append(soulrot(60 + i * 5, 357, crit=True, tgt=NAMED))
    lines.append(line(6, f"Aros hits {NAMED} for 500 crushing damage."))
    lines.append(line(16, f"Aros hits {NAMED} for 500 crushing damage."))
    lines.append(line(26, f"{NAMED} has killed Aros."))
    # multi-word named: a single capitalized token would read as a PLAYER victim
    # (community convention — the player-named-mob limitation)
    lines.append(line(90, f"You have killed {NAMED}."))
    for i, a in enumerate((250, 260, 250, 260)):
        lines.append(soulrot(240 + i * 5, a))
    lines.append(line(260, "You have killed a training cube."))
    # keep epoch order — the parser trusts the prefix
    lines.sort(key=lambda l: int(l[1:11]))
    return "".join(lines)


def upload(client, content: str, name="synthetic.txt") -> int:
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
def session_id(client):
    return upload(client, synthetic_log())


@pytest.fixture(scope="module")
def report(client, session_id):
    r = client.post(f"/api/sessions/{session_id}/coach")
    assert r.status_code == 200, r.text
    return r.json()["report"]


# ---- fit round-trip ----

def test_fit_recovers_known_coefficient(report):
    fits = {f["ability"]: f for f in report["fit"]}
    f = fits["Soulrot"]
    assert f["spell_name"] == "Soulrot VI" and f["tier_name"] == "Apprentice"
    assert f["noncrit_n"] == 16 and f["crit_n"] == 6
    assert f["observed_mean"] == 255.0
    assert abs(f["expected"] - EXPECTED_NONCRIT) < 0.1
    assert abs(f["coefficient"] - TRUE_K) < 0.05
    assert f["crit_mult_fitted"] and abs(f["crit_mult"] - 357 / 255) < 0.01
    assert f["confidence"] == "medium"  # 16 non-crit hits


# ---- replay monotonicity ----

def test_marginals_nonnegative_and_monotone(report):
    pris = {p["stat"]: p for p in report["stat_priorities"]}
    assert set(pris) == {"abilitymod", "basemodifier", "critchance",
                         "reusepct", "castpct"}
    for p in report["stat_priorities"]:
        assert p["damage_gain"] >= 0 and p["dps_gain"] >= 0
    gains = [p["dps_gain"] for p in report["stat_priorities"]]
    assert gains == sorted(gains, reverse=True)
    # abmod is hard-capped at half of Soulrot's tiny TLE base — no headroom
    assert pris["abilitymod"]["damage_gain"] == 0
    assert pris["basemodifier"]["damage_gain"] > 0
    assert pris["critchance"]["damage_gain"] > 0


def test_predicted_damage_monotone_in_stats():
    from coach.replay import predicted_damage
    fits = [{"coefficient": 2.0, "base_mid": 100, "noncrit_n": 50, "crit_n": 10,
             "crit_mult": 1.35}]
    base = {"abilitymod": 20, "basemodifier": 50, "critchance": 30,
            "castpct": 0, "reusepct": 0, "recoverypct": 0}
    ref = predicted_damage(fits, base)
    for stat, step in (("abilitymod", 25), ("basemodifier", 5), ("critchance", 5)):
        up = {**base, "_base_critchance": base["critchance"], stat: base[stat] + step}
        assert predicted_damage(fits, up) >= ref, stat


def test_reuse_marginal_needs_cooldown_lock():
    from coach.replay import reuse_marginal
    stats = {"abilitymod": 0, "basemodifier": 0, "critchance": 0,
             "castpct": 0, "reusepct": 0, "recoverypct": 0}
    book = {"X": {"recast_s": 3.0, "cast_s": 1.0, "recovery_s": 0.5, "effects": []}}
    locked = {"X": {"damage": 1000, "casts": 10, "gaps": [3, 3, 3, 3]}}
    lazy = {"X": {"damage": 1000, "casts": 10, "gaps": [30, 30, 30, 30]}}
    gain, names = reuse_marginal(locked, book, stats)
    assert gain > 0 and names == ["X"]
    gain, names = reuse_marginal(lazy, book, stats)
    assert gain == 0 and names == []


# ---- advisor snapshot ----

def test_report_shape_and_persistence(client, session_id, report):
    assert report["engine_version"] == "coach-1"
    assert report["archetype"] == "dps"
    assert report["character"]["name"] == "Bobby"
    for key in ("stats", "currencies", "stat_priorities", "tier_upgrades",
                "fit", "findings", "caveats"):
        assert key in report, key
    cur = report["currencies"]
    assert cur["encounter_count"] == 2 and cur["named_count"] == 1
    assert cur["damage"] == 16 * 255 + 6 * 357  # every Soulrot hit, both encounters
    # GET returns the persisted report unchanged
    got = client.get(f"/api/sessions/{session_id}/coach").json()["report"]
    assert got["generated_ts"] == report["generated_ts"]


def test_tier_upgrade_advice(report):
    ups = report["tier_upgrades"]
    assert ups and ups[0]["spell_name"] == "Soulrot VI"
    assert ups[0]["from_tier"] == "Apprentice" and ups[0]["to_tier"] == "Master"
    assert ups[0]["damage_gain"] > 0
    assert any(f["code"] == "tier_upgrade" for f in report["findings"])


# ---- raid report ----

def test_raid_report(client, session_id):
    r = client.get(f"/api/sessions/{session_id}/raid-report")
    assert r.status_code == 200, r.text
    rep = r.json()
    night = {n["name"]: n for n in rep["night"]}
    assert set(night) == {"Bobby", "Aros"}
    assert night["Bobby"]["damage"] > night["Aros"]["damage"]
    assert abs(sum(n["damage_share_pct"] for n in rep["night"]) - 100) < 0.5

    enc1 = rep["encounters"][0]
    assert enc1["encounter"]["name"] == "Traininglord the Unstoppable"
    p = {x["name"]: x for x in enc1["players"]}
    # Bobby opens with an ability on the pull — flagged as a possible proc
    assert p["Bobby"]["engage_delay_s"] == 0
    assert p["Bobby"]["engage_confidence"] == "low"
    # Aros trickles in at +6s on autoattack — deliberate, high confidence
    assert p["Aros"]["engage_delay_s"] == 6
    assert p["Aros"]["engage_anchor"] == "autoattack"
    assert p["Aros"]["engage_confidence"] == "high"
    # Aros died at +26 and never acted again
    assert p["Aros"]["deaths"] == 1
    assert p["Aros"]["time_dead_s"] > 0
    assert p["Aros"]["death_dps_lost"] > 0


# ---- calibration ----

def test_calibration_keeps_both_coefficients(client, session_id):
    """The dummy fit never overwrites a healthy session fit — the spread IS
    the raid-debuff measurement (debuff_uplift). Dummy k substitutes only when
    the session's own sample is too thin."""
    # a second session whose hits imply a DIFFERENT coefficient (~300/85 = 3.53)
    lines = [soulrot(i * 5, 300) for i in range(6)]
    lines.append(line(30, "You have killed a training cube."))
    sid2 = upload(client, "".join(lines), name="dummy.txt")

    # no calibration yet: session 2 fits its own hits
    rep = client.post(f"/api/sessions/{sid2}/coach").json()["report"]
    f = next(x for x in rep["fit"] if x["ability"] == "Soulrot")
    session_k = 300 / EXPECTED_NONCRIT
    assert abs(f["coefficient"] - session_k) < 0.05

    # flag session 1 as the dummy-parse ground truth (captures current stats)
    r = client.post(f"/api/sessions/{session_id}/calibration",
                    json={"calibration": True})
    assert r.status_code == 200
    assert r.json()["captured_stats"]["abilitymod"] == 1442.0
    sess = client.get(f"/api/sessions/{session_id}").json()["session"]
    assert sess["calibration"] == 1 and sess["pinned"] == 1

    rep = client.post(f"/api/sessions/{sid2}/coach").json()["report"]
    f = next(x for x in rep["fit"] if x["ability"] == "Soulrot")
    # session fit kept (6 hits = medium confidence); dummy k rides alongside
    assert abs(f["coefficient"] - session_k) < 0.05
    assert abs(f["k_dummy"] - TRUE_K) < 0.05
    assert abs(f["debuff_uplift"] - session_k / TRUE_K) < 0.02
    assert rep["calibration"]["single_point"] == ["Soulrot"]
    assert any(fi["code"] == "calibration_second_point" for fi in rep["findings"])
    assert any(d["dtype"] == "disease" for d in rep["debuff_uplift"])

    # a THIN session (3 non-crits = low confidence) falls back to dummy k
    lines = [soulrot(i * 10, 300) for i in range(3)]
    lines.append(line(40, "You have killed a training cube."))
    sid3 = upload(client, "".join(lines), name="thin.txt")
    rep = client.post(f"/api/sessions/{sid3}/coach").json()["report"]
    f = next(x for x in rep["fit"] if x["ability"] == "Soulrot")
    assert f["confidence"] == "calibrated"
    assert abs(f["coefficient"] - TRUE_K) < 0.05

    client.post(f"/api/sessions/{session_id}/calibration",
                json={"calibration": False})


def test_two_point_cap_solver():
    from coach.fit import _solve_two_point

    # truth B=400 (cap 200), bm=50: abmod 100 uncapped -> 700; 500 capped -> 800
    base, hyp = _solve_two_point([
        {"abmod": 100, "basemodifier": 50, "observed_mean": 700, "n": 20},
        {"abmod": 500, "basemodifier": 50, "observed_mean": 800, "n": 20}])
    assert hyp == "mixed" and abs(base - 400) < 1

    # truth B=2000: both points far under the cap, slope exactly 1 in abmod
    base, hyp = _solve_two_point([
        {"abmod": 100, "basemodifier": 50, "observed_mean": 3100, "n": 20},
        {"abmod": 400, "basemodifier": 50, "observed_mean": 3400, "n": 20}])
    assert hyp == "uncapped" and abs(base - 2000) < 50

    # abmod points too close together — cannot decide
    assert _solve_two_point([
        {"abmod": 100, "basemodifier": 50, "observed_mean": 700, "n": 20},
        {"abmod": 150, "basemodifier": 50, "observed_mean": 750, "n": 20},
    ]) == (None, None)


def test_two_point_calibration_end_to_end(client, fake, session_id):
    """Dummy parses at two abmod values solve the TRUE base: the fit swaps the
    Census base for it, the abmod cap becomes real, uplift measures vs truth."""
    bmf = 1 + 68.100006 / 100
    # truth B=400 (cap 200): dummy A at abmod 1442 (capped) -> 400*bmf+200
    lines = [soulrot(i * 10, round(400 * bmf + 200)) for i in range(6)]
    lines.append(line(70, "You have killed a training cube."))
    sid_a = upload(client, "".join(lines), name="dummy-a.txt")
    assert client.post(f"/api/sessions/{sid_a}/calibration",
                       json={"calibration": True}).status_code == 200

    # swap gear: abmod down to 100 (uncapped), new snapshot, dummy B
    doc = fake.chars["bobby"]
    doc["stats"]["combat"]["abilitymod"] = 100.0
    doc["last_update"] += 1
    cid = client.get("/api/characters").json()["characters"][0]["id"]
    assert client.post(f"/api/characters/{cid}/census/refresh").status_code == 200
    lines = [soulrot(i * 10, round(400 * bmf + 100)) for i in range(6)]
    lines.append(line(70, "You have killed a training cube."))
    sid_b = upload(client, "".join(lines), name="dummy-b.txt")
    r = client.post(f"/api/sessions/{sid_b}/calibration", json={"calibration": True})
    assert r.json()["captured_stats"]["abilitymod"] == 100.0

    # restore current stats before generating the raid report
    doc["stats"]["combat"]["abilitymod"] = 1442.0
    doc["last_update"] += 1
    assert client.post(f"/api/characters/{cid}/census/refresh").status_code == 200

    rep = client.post(f"/api/sessions/{session_id}/coach").json()["report"]
    f = next(x for x in rep["fit"] if x["ability"] == "Soulrot")
    assert f["base_source"] == "calibrated2"
    assert abs(f["base_mid"] - 400) < 1          # solved truth, not Census's 39
    assert abs(f["abmod_cap"] - 200) < 1
    assert f["confidence"] == "calibrated"
    # session mean 255 vs truth-expected 400*bmf + min(1442, 200) = 872.4
    assert abs(f["coefficient"] - 255 / (400 * bmf + 200)) < 0.01
    assert "Soulrot" in rep["calibration"]["two_point"]
    assert f["debuff_uplift"] is not None

    for sid in (sid_a, sid_b):
        client.post(f"/api/sessions/{sid}/calibration", json={"calibration": False})


def test_healer_estimates_in_raid_report(client):
    """Overheal/saves from HP-deficit reconstruction + ward bleedthrough."""
    lines = [
        line(0, "a training cube hits YOU for 1,000 crushing damage."),
        # deficit 1000 (the worst this fight) -> this heal is a save
        line(2, "Aros heals YOU for 600 hit points."),
        # deficit 400 -> 100 of this heal is overheal, and not a save
        line(4, "Aros heals YOU for 500 hit points."),
        soulrot(5, 250),
        line(6, "Aros's Divine Ward absorbs 300 points of damage from being "
                "done to YOU with 200 points of damage bleeding through. "
                "(0 points remaining)"),
        soulrot(10, 250),
        line(12, "You have killed a training cube."),
    ]
    sid = upload(client, "".join(lines), name="healer.txt")
    rep = client.get(f"/api/sessions/{sid}/raid-report").json()
    aros = next(n for n in rep["night"] if n["name"] == "Aros")
    assert aros["heals"] == 1100
    assert aros["overheal_est"] == 100
    assert aros["saves"] == 1
    assert aros["wards_absorbed"] == 300
    assert aros["ward_bleedthrough"] == 200
    assert aros["overheal_pct"] == pytest.approx(100 * 100 / 1200, abs=0.1)


def test_prune_freezes_raid_report(client):
    """Pruning deletes events but freezes the raid report; rollup pages keep
    working; pinned sessions survive untouched."""
    from db import get_db
    from pipeline.prune import prune_once

    lines = [soulrot(i * 10, 250) for i in range(6)]
    lines.append(line(70, "You have killed a training cube."))
    sid = upload(client, "".join(lines), name="prunable.txt")
    pinned_sid = upload(client, "".join(lines).replace("250", "260"),
                        name="pinned.txt")
    client.post(f"/api/sessions/{pinned_sid}/calibration", json={"calibration": True})

    conn = get_db()
    assert prune_once(conn, days=0) >= 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?",
                        (sid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?",
                        (pinned_sid,)).fetchone()[0] > 0    # pinned survives

    # frozen raid report still serves; regeneration is refused
    rep = client.get(f"/api/sessions/{sid}/raid-report").json()
    assert rep["frozen"] and any(n["name"] == "Bobby" for n in rep["night"])
    assert client.post(f"/api/sessions/{sid}/coach").status_code == 409
    # rollup-backed encounter detail is unaffected
    encs = client.get(f"/api/sessions/{sid}").json()["encounters"]
    assert encs and client.get(
        f"/api/encounters/{encs[0]['id']}").status_code == 200

    client.post(f"/api/sessions/{pinned_sid}/calibration", json={"calibration": False})


def test_coach_isolation(client, session_id):
    client.cookies.clear()
    client.post("/api/auth/register",
                json={"username": "other", "password": "hunter2hunter2"})
    for path, method in ((f"/api/sessions/{session_id}/coach", client.get),
                         (f"/api/sessions/{session_id}/coach", client.post),
                         (f"/api/sessions/{session_id}/raid-report", client.get)):
        assert method(path).status_code == 404
    client.cookies.clear()
    client.post("/api/auth/login",
                json={"username": "coach", "password": "hunter2hunter2"})
