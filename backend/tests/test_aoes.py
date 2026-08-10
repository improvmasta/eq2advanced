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
# `War Stomp` is IN the shipped ACT list at 45s, which is what makes it the
# default here — most of these tests are about an ability the raid was told to
# expect. Anything testing the reach rule has to name something the list has
# never heard of, or it is testing the other branch by accident.
UNLISTED = "Cleaving Swipe"


def ev(ts, tgt, *, enc=1, kind="damage", ability="War Stomp", src="The Corsolander",
       amount=1000, flags=0, dtype=None, tgt_kind="player"):
    return {"encounter_id": enc, "ts": ts, "type": kind, "ability": ability,
            "src_name": src, "src_kind": "mob",
            "tgt_key": f"{tgt}|{tgt_kind}", "tgt_kind": tgt_kind,
            "amount": amount, "dtype": dtype, "flags": flags}


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
    """Fewer targets than MIN_TARGETS at once is a frontal, not a raid AoE —
    for an ability the reported-timer list has never heard of. `UNLISTED` is
    load-bearing: with a reported timer this is a cast, which is the next
    test."""
    small = RAIDERS[:aoes.MIN_TARGETS - 1]
    rows = (cast(BASE_TS, hit=small, ability=UNLISTED)
            + cast(BASE_TS + 45, hit=small, ability=UNLISTED))
    assert aoes.detect(rows, {NAMED}) == []


# ------------------------------------------------- the list decides, not reach ---

def test_a_reported_ability_is_a_cast_however_few_it_found():
    """The raid was told to expect this one BY NAME, so reach has nothing left
    to prove. Mayong's Soul Paralysis reached one group in a 16-minute kill and
    a five-target anchor saw three of its eleven casts."""
    rows = [ev(BASE_TS + t, "Raider1") for t in (0, 45, 90)]
    [row] = aoes.detect(rows, {NAMED})
    assert row["casts"] == 3
    assert row["median_targets"] == 1
    assert row["observed_s"] == 45.0


def test_a_pet_eating_it_proves_the_cast_without_joining_the_raid():
    """A pet is evidence and is never coverage: it anchors the cast, and the
    reach numbers stay a statement about RAIDERS."""
    rows = [ev(BASE_TS, "Bobby's pet", tgt_kind="swarm_pet"),
            ev(BASE_TS + 45, "Bobby's pet", tgt_kind="swarm_pet")]
    [row] = aoes.detect(rows, {NAMED})
    assert row["casts"] == 2
    assert row["median_targets"] == 0        # nobody in the raid took it
    assert row["cast_list"][0]["hit"] == 0


def test_one_cast_counts_when_the_list_knows_it_and_not_otherwise():
    """A first pull is exactly when a countdown is worth the most, and the
    reported timer is the only one available on it."""
    [row] = aoes.detect(cast(BASE_TS), {NAMED})
    assert row["casts"] == 1 and row["reported_s"] == 45
    assert aoes.detect(cast(BASE_TS, ability=UNLISTED), {NAMED}) == []


def test_a_ticking_tail_never_splits_a_cast_into_two():
    """A DoT tail is not a second cast however long it runs — see `_cluster`.
    Blanket of Eternal Night ticks for 76s on a ~60s cycle, and a span bound
    short enough to help elsewhere turned its tail into casts that never
    happened."""
    rows = []
    for start in (0, 120):
        rows += cast(BASE_TS + start)                       # the cast itself
        for tick in range(6, 100, 6):                       # 94s of tail
            rows.append(ev(BASE_TS + start + tick, "Raider1"))
    [row] = aoes.detect(rows, {NAMED})
    assert row["casts"] == 2
    assert row["observed_s"] is None      # one gap, and it is not claimed


# ---------------------------------------------------------- damage schools ---

def test_the_row_says_what_it_lands_as():
    rows = (cast(BASE_TS, dtype="cold") + cast(BASE_TS + 45, dtype="cold")
            + [ev(BASE_TS, "Raider1", dtype="disease", amount=10)])
    [row] = aoes.detect(rows, {NAMED})
    assert row["dtype"] == "cold"                    # biggest school leads
    assert set(row["dtypes"]) == {"cold", "disease"}
    assert sum(row["dtypes"].values()) == row["damage"]


def test_damage_with_no_school_is_left_out_rather_than_guessed():
    """A ward-folded hit can carry damage and name no school (`_pair_wards`),
    so the breakdown is shares of the damage and not a reconciliation of it —
    on the real TNT Soul Paralysis row, 486,629 of 534,171."""
    rows = (cast(BASE_TS, dtype="cold") + cast(BASE_TS + 45, dtype="cold")
            + [ev(BASE_TS, "Raider1", amount=5000)])
    [row] = aoes.detect(rows, {NAMED})
    assert row["dtypes"] == {"cold": row["damage"] - 5000}
    assert sum(row["dtypes"].values()) < row["damage"]


# ------------------------------------------------------ suggesting a timer ---

def test_a_timer_this_log_disagrees_with_is_offered_not_applied():
    """Three agreeing gaps at 60s against a reported 45s is an ACT config
    somebody should go and fix."""
    rows = []
    for t in (0, 60, 120, 180):
        rows += cast(BASE_TS + t)
    [row] = aoes.detect(rows, {NAMED})
    assert row["reported_s"] == 45                   # unchanged: it is theirs
    assert row["observed_s"] == 60.0
    assert row["suggested_s"] == 60.0


def test_close_enough_is_not_a_suggestion():
    """Second-resolution stamps and a stunned mob both move a gap a little."""
    rows = []
    for t in (0, 47, 94, 141):
        rows += cast(BASE_TS + t)
    [row] = aoes.detect(rows, {NAMED})
    assert row["observed_s"] == 47.0 and row["suggested_s"] is None


def test_two_agreeing_gaps_is_a_guess_not_a_config_change():
    rows = cast(BASE_TS) + cast(BASE_TS + 70) + cast(BASE_TS + 140)
    [row] = aoes.detect(rows, {NAMED})
    assert row["observed_s"] == 70.0
    assert row["observed_agree"] == 2
    assert row["suggested_s"] is None


def test_several_mobs_of_one_name_are_never_a_timer_suggestion():
    """The observed period is a fraction of the reported one because there are
    two mobs, and editing the ACT config for that would be wrong twice."""
    rows = []
    for t in (0, 10, 20, 30, 40):
        rows += cast(BASE_TS + t, ability="Faith Strike", src="a fallen paladin")
    [row] = aoes.detect(rows, set())
    assert row["instances_hint"] == 2 and row["suggested_s"] is None


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
