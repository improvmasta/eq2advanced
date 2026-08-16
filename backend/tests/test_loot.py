"""Chest loot: what is recorded, what is deliberately not, and which fight it
belongs to.

The load-bearing claim is the first one — **a corpse is not a chest**. The log
writes both with the same verbs and only the source clause tells them apart, so
the tests that matter most are the ones asserting an absence.

Nothing here touches Census or the wiki: `items.network_allowed()` is off in CI
(conftest sets CENSUS_AUTO_REFRESH=0), which is also the case the API has to
survive — an unresolved item still renders as the name the log wrote.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import db as dbmod
import items
from pipeline import loot

BASE_TS = 1785631000
CTIME = "Fri Aug 01 20:40:00 2026"
LOGGER = "Bobby"

# A real link off the raid log; the first id is signed and Census answers to
# its unsigned twin. See items.unsign.
HOOP = r"\aITEM -1813422462 -590025310:Hoop of War\/a"
HOOP_ID = 2481544834
SASH = r"\aITEM 1788430006 -1066324666:Dreamer's Sash\/a"
SHARD = r"\aITEM 111 222:a fractured shard\/a"


def line(off, body):
    return f"({BASE_TS + off})[{CTIME}] {body}\r\n"


# ------------------------------------------------------------------ the scan ---

def scan(*bodies):
    return loot.scan([line(i, b) for i, b in enumerate(bodies)], LOGGER)


def test_a_chest_win_is_recorded():
    [d] = scan(f"Buls wins the lotto for {HOOP} from the Exquisite Chest of "
               f"Zylphax the Shredder.")
    assert d["looter"] == "Buls"
    assert d["item_id"] == HOOP_ID
    assert d["item_name"] == "Hoop of War"
    assert d["chest"] == "Exquisite Chest"
    assert d["mob"] == "Zylphax the Shredder"
    assert d["method"] == "lotto"
    assert d["qty"] == 1


def test_a_corpse_drop_is_not_loot():
    """The whole point of the feature. Same verb, same item link, different
    source clause — and a night's corpse drops would bury its chest."""
    assert scan(f"Buls wins the lotto for {SHARD} from the corpse of a doomed "
                f"visitant.") == []
    assert scan(f"Buls loots {SHARD} from the corpse of a bloodgorger.") == []


def test_a_win_with_no_source_is_not_evidence_of_a_chest():
    """`wins the lotto for a <ITEM>.` names nothing. Probably a chest is not a
    chest — 45 lines in the archive would have been guesses."""
    assert scan(f"Buls wins the lotto for a {HOOP}.") == []


def test_you_is_the_logger():
    [d] = scan(f"You win the lotto for {HOOP} from the Ornate Chest of Ishka-Urz.")
    assert d["looter"] == LOGGER


def test_quantity_is_one_row_not_four():
    [d] = scan(f"You win the lotto for 4 {SHARD} from the Small Chest of "
               f"a Mistmoore bloodoath.")
    assert d["qty"] == 4


def test_taking_it_outright_needs_no_confirmation():
    [d] = scan(f"Spades loots {HOOP} from the Treasure Chest of Enynti.")
    assert d["method"] == "loot"
    assert d["confirmed"] == 1


def test_the_looted_line_confirms_a_win_and_carries_the_rarity():
    [d] = scan(f"Buls wins the lotto for {HOOP} from the Exquisite Chest of Zylphax.",
               f"Buls looted the Fabled {HOOP}.")
    assert d["confirmed"] == 1
    assert d["rarity"] == "Fabled"


def test_a_win_nobody_claimed_is_kept_unconfirmed():
    """Winning the roll and then declining happens, and the raid remembers the
    roll either way — so the drop stays, flagged."""
    [d] = scan(f"Hene wins the lotto for {HOOP} from the Exquisite Chest of Zylphax.")
    assert d["confirmed"] == 0
    assert d["rarity"] is None


def test_a_looted_line_alone_creates_nothing():
    """It names no chest, so it cannot prove one. Most of them are corpse
    drops, quest rewards and trades."""
    assert scan(f"Buls looted the Fabled {HOOP}.") == []


def test_the_confirmation_belongs_to_the_person_who_won_it():
    drops = scan(
        f"Buls wins the lotto for {HOOP} from the Exquisite Chest of Zylphax.",
        f"Hene wins the lotto for {SASH} from the Exquisite Chest of Zylphax.",
        f"Hene looted the Legendary {SASH}.")
    by = {d["looter"]: d for d in drops}
    assert by["Hene"]["confirmed"] == 1 and by["Hene"]["rarity"] == "Legendary"
    assert by["Buls"]["confirmed"] == 0


def test_every_chest_the_server_has_is_read():
    for chest in loot.CHESTS:
        [d] = scan(f"Buls loots {HOOP} from the {chest} of Enynti.")
        assert d["chest"] == chest


# ------------------------------------------------------------------- rolls ---

def test_the_lotto_block_is_the_whole_contest():
    [d] = scan(
        f"Now rolling on {HOOP}...",
        "- Khael chooses GREED and rolls 43.",
        "- Sadenx chooses GREED and rolls 79.",
        "- Erin chooses GREED and rolls 2.",
        f"Sadenx wins the lotto for {HOOP} from the Exquisite Chest of Bonesnapper.")
    assert [(r["choice"], r["value"], r["who"]) for r in d["rolls"]["rolls"]] == [
        ("GREED", 79, "Sadenx"), ("GREED", 43, "Khael"), ("GREED", 2, "Erin")]
    # the winner is the top line, which is the check that the sort is right
    assert d["rolls"]["rolls"][0]["who"] == d["looter"]


def test_need_beats_greed_whatever_the_numbers_say():
    """That is how the game resolves it, so a NEED of 12 sorts above a GREED
    of 98 — and the winner has to stay the top line."""
    [d] = scan(
        f"Now rolling on {HOOP}...",
        "- Sadenx chooses GREED and rolls 98.",
        "- Erin chooses NEED and rolls 12.",
        f"Erin wins the lotto for {HOOP} from the Exquisite Chest of Bonesnapper.")
    assert [r["who"] for r in d["rolls"]["rolls"]] == ["Erin", "Sadenx"]
    assert d["rolls"]["rolls"][0]["who"] == d["looter"]


def test_a_choice_with_no_number_is_kept_not_dropped():
    """3,919 lines in the archive are `chose GREED.` with no die shown. They
    wanted it, and that is most of what a loot list is for."""
    [d] = scan(
        f"Now rolling on {HOOP}...",
        "- Sadenx chooses NEED and rolls 40.",
        "- Beaux chose GREED.",
        f"Sadenx wins the lotto for {HOOP} from the Exquisite Chest of Bonesnapper.")
    assert [(r["who"], r["value"]) for r in d["rolls"]["rolls"]] == [
        ("Sadenx", 40), ("Beaux", None)]


def test_you_roll_as_the_logger():
    [d] = scan(
        f"Now rolling on {HOOP}...",
        "- You choose NEED and roll 55.",
        f"{LOGGER} wins the lotto for {HOOP} from the Exquisite Chest of Enynti.")
    assert d["rolls"]["rolls"][0]["who"] == LOGGER


def test_two_chests_rolling_at_once_do_not_share_a_roll_list():
    """Blocks interleave — the rolls are keyed by ITEM, not by "the last block
    we saw"."""
    drops = scan(
        f"Now rolling on {HOOP}...",
        f"Now rolling on {SASH}...",
        "- Khael chooses GREED and rolls 43.",
        f"Khael wins the lotto for {SASH} from the Exquisite Chest of Enynti.",
        f"Erin wins the lotto for {HOOP} from the Exquisite Chest of Enynti.")
    by = {d["item_name"]: d for d in drops}
    assert [r["who"] for r in by["Dreamer's Sash"]["rolls"]["rolls"]] == ["Khael"]
    assert by["Hoop of War"]["rolls"] is None


def test_random_dice_stand_in_when_the_lotto_said_nothing():
    [d] = scan(
        r"\aPC -1 Lendrom:Lendrom\/a rolls from 1 to 100 on the magic dice...and scores a 71!",
        r"\aPC -1 Vestigial:Vestigial\/a rolls from 1 to 100 on the magic dice...and scores a 88!",
        f"Vestigial loots {HOOP} from the Exquisite Chest of Enynti.")
    assert [(r["who"], r["value"]) for r in d["rolls"]["rolls"]] == [
        ("Vestigial", 88), ("Lendrom", 71)]
    assert d["rolls"]["rolls"][0]["range"] == [1, 100]


def test_dice_rolled_AFTER_the_loot_line_still_count():
    """The common real shape, and the one a forward-only window missed
    entirely: the chest is opened, the raid rolls, the item is traded. On a
    real MMIS night 12 of 39 drops had their nearest burst after them."""
    [d] = scan(
        f"Vestigial loots {HOOP} from the Exquisite Chest of Enynti.",
        r"\aPC -1 Lendrom:Lendrom\/a rolls from 1 to 100 on the magic dice...and scores a 71!",
        r"\aPC -1 Vestigial:Vestigial\/a rolls from 1 to 100 on the magic dice...and scores a 88!")
    assert [r["who"] for r in d["rolls"]["rolls"]] == ["Vestigial", "Lendrom"]


def test_a_far_away_burst_is_not_this_drop_s_contest():
    drops = scan(f"Vestigial loots {HOOP} from the Exquisite Chest of Enynti.")
    assert drops[0]["rolls"] is None


def test_an_announcement_ties_the_dice_to_the_item_it_named():
    """The manual method: somebody links the item, everyone /randoms, the
    winner is looted to. The link is the ONE thing that names what is being
    rolled for, so where it exists it beats proximity."""
    [d] = scan(
        r'Rorschach says to the raid party, "' + HOOP + '"',
        r"\aPC -1 Lendrom:Lendrom\/a rolls from 1 to 100 on the magic dice...and scores a 71!",
        r"\aPC -1 Buls:Buls\/a rolls from 1 to 100 on the magic dice...and scores a 88!",
        f"Buls loots {HOOP} from the Exquisite Chest of Enynti.")
    assert d["rolls"]["source"] == "announced"
    assert d["rolls"]["rolls"][0]["who"] == "Buls"


def test_dice_with_nothing_naming_them_are_marked_as_a_guess():
    """Most of the time nobody links it — the call goes out in Discord. Five
    of 38 drops on the raid this was built against had a link. Proximity is
    the answer then, and it has to LOOK like one."""
    [d] = scan(
        r"\aPC -1 Buls:Buls\/a rolls from 1 to 100 on the magic dice...and scores a 88!",
        f"Buls loots {HOOP} from the Exquisite Chest of Enynti.")
    assert d["rolls"]["source"] == "nearby"


def test_the_lotto_is_never_downgraded_to_a_guess():
    [d] = scan(
        f"Now rolling on {HOOP}...",
        "- Sadenx chooses GREED and rolls 12.",
        f"Sadenx wins the lotto for {HOOP} from the Exquisite Chest of Enynti.")
    assert d["rolls"]["source"] == "lotto"


def test_dice_never_mix_in_beside_a_real_lotto_block():
    """Nothing in a `/random` line says which item it was for. Putting one
    beside a lotto roll for a different item and calling it a contest is the
    failure this guards."""
    [d] = scan(
        r"\aPC -1 Lendrom:Lendrom\/a rolls from 1 to 100 on the magic dice...and scores a 99!",
        f"Now rolling on {HOOP}...",
        "- Sadenx chooses GREED and rolls 12.",
        f"Sadenx wins the lotto for {HOOP} from the Exquisite Chest of Enynti.")
    assert [r["who"] for r in d["rolls"]["rolls"]] == ["Sadenx"]


# ------------------------------------------------------------------ the API ---

def log() -> str:
    """One raid: two named fights, and a chest after each. The second chest
    names a mob the fight was NOT named for, which is the case rung 2 of the
    attribution ladder exists for."""
    out = [line(0, "You have entered Castle Mistmoore.")]
    for t in range(1, 12):
        out.append(line(t, f"Bobby hits Zylphax the Shredder for {900 + t} slashing "
                           f"damage."))
    out.append(line(13, "Bobby has killed Zylphax the Shredder."))
    # the chest, well after the fight closed (GAP_S) but named for its mob
    out.append(line(78, f"Now rolling on {HOOP}..."))
    out.append(line(79, "- Hene chooses GREED and rolls 91."))
    out.append(line(79, "- Buls chooses NEED and rolls 14."))
    out.append(line(80, f"Buls wins the lotto for {HOOP} from the Exquisite Chest "
                        f"of Zylphax the Shredder."))
    out.append(line(80, f"Buls looted the Fabled {HOOP}."))
    out.append(line(85, f"Buls loots {SHARD} from the corpse of a doomed visitant."))

    # a chain pull labelled for the big mob; the chest belongs to the small one
    for t in range(200, 212):
        out.append(line(t, f"Bobby hits Enynti for {800 + t} slashing damage."))
        out.append(line(t, f"Bobby hits a Mistmoore bloodoath for {100} slashing "
                           f"damage."))
    out.append(line(213, "Bobby has killed Enynti."))
    out.append(line(280, f"You win the lotto for 2 {SASH} from the Small Chest of "
                         f"a Mistmoore bloodoath."))
    return "".join(out)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("eq2adv-loot")
    mp = pytest.MonkeyPatch()
    mp.setattr(dbmod, "DATA_DIR", tmp)
    mp.setattr(dbmod, "DB_PATH", tmp / "test.db")
    mp.setattr(dbmod, "UPLOADS_DIR", tmp / "uploads")
    mp.setattr(dbmod, "RAW_DIR", tmp / "raw")
    mp.setattr(dbmod, "ICONS_DIR", tmp / "icons")
    mp.setattr(items, "ICONS_DIR", tmp / "icons")
    # `uploads_api` binds UPLOADS_DIR at IMPORT time, so in a whole-suite run
    # the first test module to import `main` owns it for every module after —
    # uploads land in its directory while `session_raw_paths` (which re-reads
    # db.UPLOADS_DIR per call) looks in ours, and the reparse test below sees
    # "no stored raw log". Patch the name the writer actually uses too.
    from routers import uploads_api
    mp.setattr(uploads_api, "UPLOADS_DIR", tmp / "uploads")
    if getattr(dbmod._local, "conn", None) is not None:
        dbmod._local.conn = None
    from main import app
    with TestClient(app) as c:
        c.post("/api/auth/register",
               json={"username": "loottest", "password": "hunter2hunter2"})
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


def rows(client, selection):
    r = client.get("/api/encounters/loot", params={"ids": selection})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_parse_records_chest_loot_and_only_chest_loot(client, selection):
    got = rows(client, selection)["loot"]
    assert sorted(d["name"] for d in got) == ["Dreamer's Sash", "Hoop of War"]


def test_a_chest_is_attributed_to_the_fight_it_was_named_for(client, selection):
    hoop = next(d for d in rows(client, selection)["loot"]
                if d["name"] == "Hoop of War")
    assert hoop["fight"] == "Zylphax the Shredder"
    assert hoop["attribution"] == "name"
    assert hoop["looter"] == "Buls"
    assert hoop["confirmed"] is True


def test_a_chest_named_for_a_mob_inside_the_fight_still_lands(client, selection):
    """The pull is labelled Enynti; the chest belongs to a bloodoath that died
    in it. Rung 2 of the ladder — the events say which fight it was in."""
    sash = next(d for d in rows(client, selection)["loot"]
                if d["name"] == "Dreamer's Sash")
    assert sash["mob"] == "a Mistmoore bloodoath"
    assert sash["fight"] == "Enynti"
    assert sash["attribution"] == "entity"
    assert sash["qty"] == 2
    assert sash["looter"] == "Bobby"          # 'You'


def test_the_api_carries_the_roll_list_in_resolution_order(client, selection):
    """NEED 14 beat GREED 91, which is the game's rule and has to survive the
    round trip through the database."""
    hoop = next(d for d in rows(client, selection)["loot"]
                if d["name"] == "Hoop of War")
    assert [(r["choice"], r["value"], r["who"]) for r in hoop["rolls"]["rolls"]] == [
        ("NEED", 14, "Buls"), ("GREED", 91, "Hene")]
    assert hoop["rolls"]["rolls"][0]["who"] == hoop["looter"]


def test_a_drop_nobody_rolled_for_has_no_roll_list(client, selection):
    sash = next(d for d in rows(client, selection)["loot"]
                if d["name"] == "Dreamer's Sash")
    assert sash["rolls"] is None


def test_an_unresolved_item_still_answers_with_the_log_s_name(client, selection):
    """CI never reaches Census, so nothing has a picture or a wiki page. The
    row is still complete enough to read."""
    data = rows(client, selection)
    assert data["unresolved"] == 2
    for d in data["loot"]:
        assert d["name"] and d["icon"] is None and d["wiki"] is None


def test_loot_is_not_an_event_and_no_looter_became_an_entity(client, selection):
    """The reason loot lives beside the parse rather than in it: `Buls` only
    ever appears on a loot line, and must not turn up in the raid."""
    agg = client.get("/api/encounters/agg", params={"ids": selection}).json()
    names = {a["name"] for a in agg["actors"]}
    assert "Bobby" in names                     # the table is not empty
    assert "Buls" not in names


def test_a_reparse_keeps_the_loot(client, selection):
    """`clear_derived` drops loot with the encounters it points at, and the
    parse writes it back — otherwise a reparse would silently empty the tab."""
    sid = client.get("/api/sessions").json()["sessions"][0]["id"]
    before = rows(client, selection)["loot"]
    r = client.post(f"/api/sessions/{sid}/reparse")
    assert r.status_code == 200, r.text
    for _ in range(300):
        if client.get(f"/api/sessions/{sid}").json()["session"]["status"] == "ready":
            break
        time.sleep(0.05)
    encs = client.get(f"/api/sessions/{sid}").json()["encounters"]
    after = rows(client, ",".join(str(e["id"]) for e in encs))["loot"]
    assert [d["name"] for d in after] == [d["name"] for d in before]
    assert [d["fight"] for d in after] == [d["fight"] for d in before]


def test_another_account_cannot_read_it(client, selection):
    """Loot goes through the same per-encounter gate as every other ?ids=
    endpoint; nothing about an item makes a private raid readable."""
    client.post("/api/auth/logout")
    try:
        assert client.get("/api/encounters/loot",
                          params={"ids": selection}).status_code == 404
    finally:
        client.post("/api/auth/login",
                    json={"username": "loottest", "password": "hunter2hunter2"})


def test_the_icon_route_404s_for_an_icon_nobody_cached(client):
    assert client.get("/api/items/icon/999999.png").status_code == 404


# --------------------------------------------------------- the examine card ---

CENSUS_ITEM = {
    "id": 2481544834,
    "displayname": "Hoop of War",
    "modifiers": {
        "strength": {"displayname": "str", "type": "attribute", "value": 39},
        "stamina": {"displayname": "sta", "type": "attribute", "value": 39},
        "arcane": {"displayname": "arcane", "type": "ac", "value": 280},
        "combatskills": {"displayname": "Combat Skills", "type": "skill", "value": 15},
        "basemodifier": {"displayname": "Potency", "type": "modifyproperty",
                         "value": 2.6},
        "critchance": {"displayname": "Crit Chance", "type": "modifyproperty",
                       "value": 1.8},
        # zeroes are what Census writes for "this item does not have it"
        "flurry": {"displayname": "Flurry", "type": "modifyproperty", "value": 0},
        # a type the card has no place for must be dropped, not guessed at
        "mystery": {"displayname": "Mystery", "type": "somethingnew", "value": 9},
    },
    "flags": {"attunable": {"value": 1}, "heirloom": {"value": 1},
              "nodestroy": {"value": 0}, "artiface": {"value": 0}},
    "adornmentslot_list": [{"color": "white"}, {"color": "orange"}],
}


def test_the_green_block_reads_in_the_window_s_own_order():
    """Attributes, then resistances, then skills — EQ2i's order, not by size.
    Sorting the block as one list put a big `All` above the primary
    attributes, which is not how the game reads."""
    s = items.stat_block(CENSUS_ITEM)
    assert [r["name"] for r in s["stats"]] == [
        "Primary Attributes", "Stamina", "Resistances", "Combat Skills"]


def test_censuss_all_is_ability_mod_and_belongs_with_the_modifiers():
    """Census files Ability Mod under `all` / "All", which reads as "+62 to
    all something" and is nothing of the kind. The wiki settles it: Bee Sting
    carries `abmod = +62` and Census's record for the same item carries
    `all: 62` beside its own separate strength and stamina.

    It goes in the BLUE block, not the green one: on this server it is a
    throughput stat a raider compares items on, beside Potency — and it is a
    flat number, not a percentage."""
    s = items.stat_block({**CENSUS_ITEM, "modifiers": {
        "all": {"displayname": "All", "type": "normalizedmod", "value": 62}}})
    assert not s["stats"]
    assert [(r["name"], r["value"], r["pct"]) for r in s["effects"]] == [
        ("Ability Mod", 62, False)]


def test_the_blue_block_always_leads_with_potency_then_crit_chance():
    """Fixed, not sorted by value: the question a raider asks of this block is
    always the same one in the same order — and **Ability Mod goes last**,
    which is where the game puts it, however much it matters."""
    s = items.stat_block({**CENSUS_ITEM, "modifiers": {
        "attackspeed": {"displayname": "Haste", "type": "modifyproperty", "value": 4.0},
        "all": {"displayname": "All", "type": "normalizedmod", "value": 62},
        "dps": {"displayname": "DPS", "type": "modifyproperty", "value": 8.1},
        "critchance": {"displayname": "Crit Chance", "type": "modifyproperty", "value": 1.8},
        "basemodifier": {"displayname": "Potency", "type": "modifyproperty", "value": 2.6},
    }})
    assert [r["name"] for r in s["effects"]] == [
        "Potency", "Crit Chance", "DPS", "Haste", "Ability Mod"]
    # DPS and Ability Mod are figures; everything else in here is a percentage
    pct = {r["name"]: r["pct"] for r in s["effects"]}
    assert pct == {"Potency": True, "Crit Chance": True, "DPS": False,
                   "Ability Mod": False, "Haste": True}


def test_a_stat_this_server_does_not_have_yet_is_not_shown():
    """Census describes the item as it stands on LIVE. Showing a TLE raider a
    Crit Bonus their character cannot use invites comparing two items on a
    stat neither one grants."""
    s = items.stat_block(CENSUS_ITEM)
    assert not any(r["name"] == "Crit Bonus" for r in s["stats"] + s["effects"])
    assert "Crit Bonus" in items.ERA_HIDDEN      # delete it when TLE gets it


def test_the_blue_block_is_the_property_modifiers_as_percentages():
    s = items.stat_block(CENSUS_ITEM)
    # biggest first, percentages marked, and Census's zero is gone
    assert [(r["name"], r["value"], r["pct"]) for r in s["effects"]] == [
        ("Potency", 2.6, True), ("Crit Chance", 1.8, True)]


def test_a_modifier_type_the_card_has_no_place_for_is_dropped():
    s = items.stat_block(CENSUS_ITEM)
    assert not any(r["name"] == "Mystery" for r in s["stats"] + s["effects"])


def test_matching_resists_read_as_one_line_the_way_the_game_shows_them():
    two = {**CENSUS_ITEM, "modifiers": {
        "arcane": {"displayname": "arcane", "type": "ac", "value": 280},
        "noxious": {"displayname": "noxious", "type": "ac", "value": 280}}}
    assert [(r["name"], r["value"]) for r in items.stat_block(two)["stats"]] \
        == [("Resistances", 280)]
    # ...and disagreeing ones are listed, never summed into a wrong number
    split = {**CENSUS_ITEM, "modifiers": {
        "arcane": {"displayname": "arcane", "type": "ac", "value": 280},
        "noxious": {"displayname": "noxious", "type": "ac", "value": 100}}}
    assert [r["value"] for r in items.stat_block(split)["stats"]] == [280, 100]


def test_only_the_flags_a_raider_reads_survive():
    s = items.stat_block(CENSUS_ITEM)
    assert s["flags"] == ["Attuneable", "Heirloom"]
    assert s["adornments"] == ["white", "orange"]


def test_the_tier_beside_the_level_is_the_game_s():
    assert items.tier_of(70) == 8
    assert items.tier_of(65) == 7
    assert items.tier_of(None) is None


def test_a_weapon_reports_its_base_damage_delay_and_rating():
    """EQ2i's item box shows the BASE range and the rating, not the mastery
    range — the mastery figure is what the weapon does with the skill capped,
    which is a different claim from the one the box makes."""
    w = items.stat_block({**CENSUS_ITEM, "typeinfo": {
        "name": "weapon", "minbasedamage": 97, "maxbasedamage": 290,
        "mindamage": 292, "maxdamage": 877, "delay": 6.0,
        "damagerating": 64.554672, "damagetype": "Slash",
        "wieldstyle": "One-Handed"}})["weapon"]
    assert (w["low"], w["high"]) == (97, 290)
    assert w["style"] == "One-Handed Slash"
    assert (w["delay"], w["rating"]) == (6.0, 64.55)


def test_armour_has_no_weapon_block():
    assert items.stat_block(CENSUS_ITEM)["weapon"] is None


# --- the item's own effect, off its wiki page ---------------------------------

WIKITEXT = """{{EquipInformation|
 str = +39|
 effectlist= {{EquipmentEffect|Mind Shatter|VII}}| <!-- names only -->
 effectdesc=
*When Equipped:
**Increases mental damage done by spells by up to 35.
| <!-- the complete description -->
 slot = Primary|
}}"""


def test_the_item_effect_is_read_off_the_page_we_already_fetched():
    """The forward direction — item page → effect — which is why the gear-proc
    wontfix does not apply. The page is already in hand for the wiki LINK."""
    fx = items.item_effects(WIKITEXT)
    assert fx["names"] == ["Mind Shatter VII"]
    assert fx["desc"] == [
        {"depth": 1, "text": "When Equipped:"},
        {"depth": 2, "text": "Increases mental damage done by spells by up to 35."},
    ]


def test_the_description_keeps_its_indent():
    """`*When Equipped:` is the condition and `**Increases…` is what it then
    does; flattening the two loses which is which."""
    assert {d["depth"] for d in items.item_effects(WIKITEXT)["desc"]} == {1, 2}


def test_a_page_with_no_effect_has_none():
    assert items.item_effects("{{EquipInformation|\n str = +39|\n}}") is None


def test_an_item_with_nothing_to_show_has_no_card():
    """A spell scroll has no equipment stats. Better no card than an empty
    one pretending to be an examine window."""
    assert items.stat_block({"id": 1, "displayname": "Rampage II (Master)"}) is None


def test_a_turquoise_adornment_keeps_its_slot_predicate_and_set_bonus():
    s = items.stat_block({
        "typeinfo": {"name": "adornment", "color": "turquoise",
                     "slot_list": [{"displayname": "Ear"}]},
        "flags": {"novalue": {"value": 1}},
        "setbonus_list": [{"requireditems": 2,
                           "effect": "Applies Abomination Anihiliation.",
                           "descriptiontag_1": "On any combat or spell hit...",
                           "descriptiontag_2": "Inflicts 2,861 divine damage."}],
    })
    assert s["flags"] == ["No-Value"]
    assert s["adornment"] == {
        "color": "turquoise", "slots": ["Ear"], "requires_equip": True,
        "predicate": "In Rise of Kunark or previous expansion zones",
        "set_bonuses": [{"required": 2,
                         "effect": "Applies Abomination Anihiliation.",
                         "descriptions": ["On any combat or spell hit...",
                                          "Inflicts 2,861 divine damage."]}],
    }


def test_an_adornment_page_supplies_its_set_name():
    fx = items.item_effects("{{AdornInformation2|\n set = Arcanist Abomination Anihiliation|\n}}")
    # `classes` rides along on the same read — an adornment page names none,
    # and an unrestricted item is quiet rather than listing every class.
    assert fx == {"names": [], "desc": [], "classes": None,
                  "set": "Arcanist Abomination Anihiliation"}


def test_an_equipment_page_says_who_can_wear_it_and_only_when_it_restricts():
    """The one property that rules an item out before any number on it matters.
    Read off the page already in hand, the same way the proc is."""
    fx = items.item_effects(
        "{{EquipInformation|\n classes = {{AllShamanCats|Equipment|yes}}|\n}}")
    assert fx and "mystic" in fx["classes"] and "wizard" not in fx["classes"]
    # The tier expands to the SUBCLASSES that can equip it, never to its own
    # name — a grant is to a tier, and what wears the item is a class.
    assert items.display({"effects": fx})["classes"] == ["Defiler", "Mystic"]
    # Everybody can wear it -> no line at all, the way the game shows it.
    everyone = items.item_effects(
        "{{EquipInformation|\n classes = {{AllAdvCats|Equipment|yes}}|\n}}")
    assert items.display({"effects": everyone})["classes"] is None


def test_the_adorn_gem_route_refuses_a_colour_the_game_has_no_slot_for(client):
    assert client.get("/api/items/adorn/mauve.png").status_code == 404
