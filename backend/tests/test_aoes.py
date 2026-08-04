"""AoE detection: what counts as a cast, what the observed timer is, and who
was covered.

The measurement's one systematic failure is a cast that never reached enough
of the raid to be seen — the user's own framing: "an AoE might not hit all 4
groups on pull, so we may miss some". A missed cast can only make a gap
LONGER, never shorter, which is why the observed timer is the shortest gap
that repeats. These tests pin that, the tick-vs-recast split, and the fact
that the wait between two pulls is not a cooldown."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from parser.events import F_ZERO
from pipeline import aoes

BASE_TS = 1754500000
CTIME = "Mon Aug 03 21:00:00 2026"
NAMED = "The Corsolander"
RAIDERS = [f"Raider{i}" for i in range(1, 9)]


def ev(ts, tgt, *, enc=1, kind="damage", ability="War Stomp", src="The Corsolander",
       amount=1000, flags=0):
    return {"encounter_id": enc, "ts": ts, "type": kind, "ability": ability,
            "src_name": src, "src_kind": "mob",
            "tgt_key": f"{tgt}|player", "tgt_kind": "player",
            "amount": amount, "flags": flags}


def cast(ts, *, enc=1, hit=RAIDERS, avoided=(), absorbed=(), **kw):
    rows = [ev(ts, p, enc=enc, **kw) for p in hit]
    rows += [ev(ts, p, enc=enc, kind="avoid", amount=None, **kw) for p in avoided]
    rows += [ev(ts, p, enc=enc, amount=0, flags=F_ZERO, **kw) for p in absorbed]
    return rows


# ---------------------------------------------------------------- the timer ---

def test_shortest_repeating_gap_is_the_timer():
    assert aoes.observed_period([30, 30, 31]) == (30.3, 3)


def test_a_missed_cast_lengthens_a_gap_and_is_ignored():
    """60 is two 30s cycles with the middle cast unseen. The 30s that repeats
    wins; the 60 does not drag the answer up."""
    period, agreed = aoes.observed_period([30, 60, 29, 30])
    assert period == pytest.approx(29.7, abs=0.1)
    assert agreed == 3


def test_one_freak_short_gap_is_not_a_timer():
    """An interrupt or a second mob makes one short gap. A timer repeats."""
    assert aoes.observed_period([7, 45, 44, 46])[0] == 45.0


def test_nothing_repeats_says_so():
    assert aoes.observed_period([12, 40, 91]) == (None, 0)


# ------------------------------------------------------------------- casts ---

def test_ticks_inside_a_cast_are_one_cast():
    """A DoT AoE ticking 3s apart is one press, not four."""
    rows = []
    for t in (0, 60, 120):
        rows += cast(BASE_TS + t) + cast(BASE_TS + t + 3) + cast(BASE_TS + t + 6)
    [row] = aoes.detect(rows, {NAMED})
    assert row["casts"] == 3
    assert row["observed_s"] == 60.0


def test_a_missed_cast_is_counted_not_invented():
    """Three casts on a 45s timer with the middle one unseen: two casts, a 90s
    gap, and the row says one cast is missing rather than reporting 90s."""
    rows = cast(BASE_TS) + cast(BASE_TS + 45) + cast(BASE_TS + 90) + cast(BASE_TS + 180)
    [row] = aoes.detect(rows, {NAMED})
    assert row["casts"] == 4
    assert row["observed_s"] == 45.0
    assert row["missed_hint"] == 1


def test_the_wait_between_two_pulls_is_not_a_cooldown():
    """Same AoE, two fights an hour apart — the gap across fights never enters
    the timer."""
    rows = (cast(BASE_TS, enc=1) + cast(BASE_TS + 45, enc=1)
            + cast(BASE_TS + 3600, enc=2) + cast(BASE_TS + 3645, enc=2))
    [row] = aoes.detect(rows, {NAMED})
    assert row["observed_s"] == 45.0
    assert row["fights"] == 2


def test_a_cleave_is_not_an_aoe():
    """Fewer targets than MIN_TARGETS at once is a frontal, not a raid AoE."""
    small = RAIDERS[:aoes.MIN_TARGETS - 1]
    rows = cast(BASE_TS, hit=small) + cast(BASE_TS + 45, hit=small)
    assert aoes.detect(rows, {NAMED}) == []


def test_only_enemies_cast_aoes():
    """A raider's own group heal lands on everyone; it is not an incoming AoE."""
    rows = cast(BASE_TS) + cast(BASE_TS + 45)
    for r in rows:
        r["src_kind"] = "player"
    assert aoes.detect(rows, set()) == []


# ------------------------------------------------------------------ blocks ---

def test_avoided_and_absorbed_count_as_blocked():
    """Bladedance shows up as an avoid, Tortoise Shell as a zero-damage hit —
    both are the raid covering itself, and neither is a hit."""
    rows = (cast(BASE_TS, hit=RAIDERS[:4], avoided=RAIDERS[4:6], absorbed=RAIDERS[6:])
            + cast(BASE_TS + 45))
    [row] = aoes.detect(rows, {NAMED})
    first = row["cast_list"][0]
    assert (first["hit"], first["avoided"], first["absorbed"]) == (4, 2, 2)
    assert first["targets"] == 8
    assert sorted(first["blocked_by"]) == [f"{p}|player" for p in RAIDERS[4:]]
    assert row["blocked"] == 4


def test_a_player_who_ate_it_did_not_also_dodge_it():
    """A second wave landing on someone who parried the first is a hit."""
    rows = (cast(BASE_TS, hit=RAIDERS, avoided=RAIDERS[:2]) + cast(BASE_TS + 45))
    [row] = aoes.detect(rows, {NAMED})
    assert row["cast_list"][0]["avoided"] == 0
    assert row["cast_list"][0]["hit"] == 8


# ------------------------------------------------------- the reported timer ---

def test_the_act_list_loads_and_joins_by_name():
    timers = aoes.reported_timers()
    assert timers, "ACT spell-timer reference is missing"
    assert timers["Blanket of Eternal Night"]["timer_s"] == 60


def test_several_mobs_sharing_a_name_are_flagged_not_believed():
    """Two "a fallen paladin" on a 20s timer look like one on a 10s timer.
    Entities are keyed by name, so the row says so instead of claiming the
    ACT list is wrong."""
    rows = []
    for t in (0, 10, 20, 30, 40):
        rows += cast(BASE_TS + t, ability="Faith Strike", src="a fallen paladin")
    [row] = aoes.detect(rows, set())          # trash: not a named source
    assert row["reported_s"] == 20
    assert row["observed_s"] == 10.0
    assert row["instances_hint"] == 2


def test_a_named_is_never_explained_away_as_two_mobs():
    rows = []
    for t in (0, 30, 60):
        rows += cast(BASE_TS + t, ability="Blanket of Eternal Night",
                     src="Vampire Lord Mayong Mistmoore")
    [row] = aoes.detect(rows, {"Vampire Lord Mayong Mistmoore"})
    assert row["instances_hint"] is None


# --------------------------------------------------------------------- api ---

def line(t: int, body: str) -> str:
    return f"({BASE_TS + t})[{CTIME}] {body}\r\n"


def log() -> str:
    """One named pull with War Stomp landing on eight raiders three times, 45s
    apart, two of them covered."""
    out = [line(0, "You have entered Veeshan's Peak.")]
    for t in range(0, 100, 6):
        out.append(line(t, f"YOUR Soulrot hits {NAMED} for 900 disease damage."))
    for t in (10, 55, 100):
        for p in RAIDERS[:6]:
            out.append(line(t, f"{NAMED}'s War Stomp hits {p} for 1,200 crushing damage."))
        for p in RAIDERS[6:]:
            # the avoid form that NAMES the ability — a bare "tries to crush
            # X, but X parries" is a melee swing, not the AoE
            out.append(line(t, f"{NAMED} tries to crush {p} with War Stomp, "
                               f"but {p} parries."))
    out.append(line(104, f"You have killed {NAMED}."))
    out.sort(key=lambda x: int(x[1:11]))
    return "".join(out)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-aoes")
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
               json={"username": "aoetest", "password": "hunter2hunter2"})
        c.post("/api/characters", json={"name": "Bobby"})
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


def test_api_reports_the_aoe_with_both_timers(client, selection):
    r = client.get("/api/encounters/aoes", params={"ids": selection})
    assert r.status_code == 200, r.text
    rows = r.json()["aoes"]
    stomp = next(a for a in rows if a["ability"] == "War Stomp")
    assert stomp["casts"] == 3
    assert stomp["observed_s"] == 45.0
    assert stomp["reported_s"] == 45           # from the ACT list
    assert stomp["median_targets"] == 8
    assert stomp["blocked"] == 6               # two parries per cast
    assert stomp["cast_list"][0]["avoided"] == 2


def test_api_refuses_an_unreadable_selection(client, selection):
    other = TestClient(client.app)
    other.post("/api/auth/register",
               json={"username": "stranger", "password": "hunter2hunter2"})
    r = other.get("/api/encounters/aoes", params={"ids": selection})
    assert r.status_code in (403, 404)


def test_api_rejects_junk_ids(client):
    assert client.get("/api/encounters/aoes", params={"ids": "abc"}).status_code == 422
