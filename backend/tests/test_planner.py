"""The Planner: what the wiki's templates mean, and how a declared ORDER ranks.

Fixtures are REAL pages recorded verbatim (`fixtures/wiki/planner_pages.json`),
so these exercise the template shapes the wiki actually uses rather than ones
invented to pass. Nothing here touches the network — the same rule every other
reference-data test follows, and here it also means the crawl itself is
exercised end to end with `fetch` and `members` handed in.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from planner import catalog, ingest, wiki

PAGES = json.loads(
    (Path(__file__).parent / "fixtures" / "wiki" / "planner_pages.json").read_text())

BOOTS = "Mist Covered Boots (Level 80)"


# ---------- layer 1: what the templates say ----------

def test_equip_information_is_a_census_dump_in_template_form():
    """Every stat is a named field and `itemlink` carries the same signed item
    id a raid log writes, which is what makes the join to `items` exact."""
    row = wiki.parse_equip(BOOTS, PAGES[BOOTS])
    assert row["level"] == 80
    assert row["slot"] == "Feet"
    assert row["tier"] == "FABLED"
    assert row["dtype"] == "Chain Armor"
    assert row["stats"]["abmod"] == 98
    assert row["stats"]["potency"] == 3.7
    assert row["stats"]["acspeed"] == 2.1
    # `\aITEM 2117508092 …` — the id the log writes, unsigned
    assert row["census_id"] == 2117508092
    assert row["icon"] == 3645


def test_a_live_era_stat_is_dropped_the_way_items_py_drops_it():
    """The wiki mirrors a 2022 Census scrape, so `critbonus` is on almost every
    RoK item page and belongs to none of them — this server does not have Crit
    Bonus. Showing it would invite comparing two items on a stat neither one
    grants (`items.ERA_HIDDEN`, made again here for the wiki's field names)."""
    row = wiki.parse_equip(BOOTS, PAGES[BOOTS])
    assert "critbonus" in PAGES[BOOTS]          # the page really does carry it
    assert "critbonus" not in row["stats"]


def test_the_turquoise_and_the_set_are_read_separately_from_the_stats():
    """THE ITEM IS NOT THE UNIT OF VALUE: the set bonus rides on an adornment
    that ships inside the armour and can be moved out of it."""
    row = wiki.parse_equip(BOOTS, PAGES[BOOTS])
    assert row["set_name"] == "Mist Covered Set"
    assert row["adorns"] == {"white": 1, "orange": 1, "turquoise": 1}


def test_class_templates_expand_to_subclasses_the_class_tree_knows():
    """`{{AllShamanCats|Equipment|yes}}` is the shaman TIER, and `classtree` is
    the one translation from a tier to the classes it reaches."""
    row = wiki.parse_equip(BOOTS, PAGES[BOOTS])
    assert row["classes"] == ["defiler", "mystic"]


def test_beastlord_and_channeler_are_never_in_an_EoF_or_RoK_class_list():
    """EQ2 gained them in 2011 and 2014. A class list is filtered to what the
    SERVER has, not to what the template mirrored in 2022."""
    everybody = wiki.classes_of("{{AllAdvCats|Equipment|yes}}")
    assert len(everybody) == 24
    assert "beastlord" not in everybody and "channeler" not in everybody
    assert "necromancer" in everybody


def test_a_pattern_page_is_not_equipment():
    """Half of what a mob's `drops` list points at is not a thing you wear —
    an armour pattern, a recipe, a pointer page. Returning None is what keeps
    the catalog to equipment."""
    assert wiki.parse_equip("Blackened Chestguard Pattern",
                            PAGES["Blackened Chestguard Pattern"]) is None


def test_a_named_monster_carries_the_era_and_the_raid_group_solo_split():
    """The two facts no item page has. `diff` is the split for free."""
    mob = wiki.parse_named("Adkar Vyx", PAGES["Adkar Vyx"])
    assert mob["era"] == "Rise of Kunark"
    assert mob["zone"] == "The Protector's Realm"
    assert mob["diff"] == "epic x4" and mob["kind"] == "raid"
    assert "Boots of the Dead-Eye" in mob["drops"]
    assert len(mob["drops"]) == 16
    solo = wiki.parse_named("Admiral Tylix", PAGES["Admiral Tylix"])
    assert solo["kind"] == "solo"


def test_a_quest_gives_up_its_equipment_rewards_and_nothing_else():
    """A gear reward is `{{Equip|Name}}`; `{{Item|…}}`, coin and faction are
    not things you wear and are not in this catalog."""
    q = wiki.parse_quest("Acts of Contrition", PAGES["Acts of Contrition"])
    assert "The Truth of Marr (Mythical)" in q["rewards"]
    plain = wiki.parse_quest("101 Things to Do With a Dead Grindhoof",
                             PAGES["101 Things to Do With a Dead Grindhoof"])
    assert plain["rewards"] == []
    assert plain["level"] == 79 and plain["timeline"] == "Jarsath Wastes"


def test_a_live_update_patch_falls_back_to_the_category_it_was_crawled_from():
    """`patch` is free text and carries live-update numbers as well as
    expansion names — `Acts of Contrition` says `LU42`. An update number places
    no expansion on its own, so the page keeps the era of the category it was
    found in rather than being guessed at or dropped."""
    q = wiki.parse_quest("Acts of Contrition", PAGES["Acts of Contrition"])
    assert q["era"] == "LU42"
    assert wiki.era_of_patch(q["era"]) is None
    assert wiki.era_of_patch("Rise of Kunark") == "rok"


def test_an_adornment_set_page_carries_its_tiers():
    s = wiki.parse_adorn_set("Focused Mind Set (Adornment Set)",
                             PAGES["Focused Mind Set (Adornment Set)"])
    assert s["name"] == "Focused Mind Set"
    assert s["level"] == 78
    assert "Focused Mind Set: Chest" in s["pieces"]
    assert [b["pieces"] for b in s["bonuses"]] == [3, 6]
    assert "Focus: Magi's Shielding" in s["bonuses"][0]["text"]


# ---------- the crawl ----------

def fake_wiki():
    """The crawl driven off recorded pages. `Adkar Vyx` drops the boots; the
    disambiguation is included so the version-following round is exercised."""
    def members(cat):
        if cat.endswith("Named Monsters"):
            return ["Adkar Vyx", "Admiral Tylix"]
        return ["Acts of Contrition"]

    def fetch(titles):
        return {t: PAGES[t] for t in titles if t in PAGES}
    return fetch, members


def fresh_db(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


def test_the_crawl_inverts_mobs_into_items_with_their_source(tmp_path):
    """The catalog is built by inverting mobs and quests, because an item page
    has no era and its `obtain` field is blank more often than not."""
    fetch, members = fake_wiki()
    conn = fresh_db(tmp_path)
    report = ingest.sync(conn, "rok", fetch=fetch, members=members)
    assert report["items"] == 1                 # only the boots page is recorded
    row = conn.execute("SELECT * FROM plan_items").fetchone()
    assert row["page_title"] == BOOTS
    assert row["era"] == "rok"
    src = conn.execute("SELECT * FROM plan_sources").fetchone()
    assert (src["source"], src["kind"], src["era"]) == ("Adkar Vyx", "raid", "rok")
    assert src["zone"] == "The Protector's Realm"


def test_an_item_above_the_era_cap_is_not_in_that_era(tmp_path):
    """A RoK quest page rewritten for a live revamp hands back a level-100
    item. One of those in the catalog becomes the largest value the scorer has
    ever seen, and every real RoK drop then scores about 2 out of 100 against
    something nobody on this server can wear."""
    pages = dict(PAGES)
    pages[BOOTS] = PAGES[BOOTS].replace(" level   = 80", " level   = 100")

    def fetch(titles):
        return {t: pages[t] for t in titles if t in pages}

    _, members = fake_wiki()
    conn = fresh_db(tmp_path)
    report = ingest.sync(conn, "rok", fetch=fetch, members=members)
    assert report["items"] == 0 and report["over_cap"] == 1
    assert conn.execute("SELECT COUNT(*) FROM plan_items").fetchone()[0] == 0


def test_a_resync_removes_what_the_wiki_no_longer_says(tmp_path):
    """The catalog is a cache of a crawl, so a drop removed from a mob page has
    to be able to LEAVE. Reconciling is per-era, so the other era's rows stay."""
    fetch, members = fake_wiki()
    conn = fresh_db(tmp_path)
    ingest.sync(conn, "rok", fetch=fetch, members=members)
    assert conn.execute("SELECT COUNT(*) FROM plan_items").fetchone()[0] == 1

    stripped = dict(PAGES)
    stripped["Adkar Vyx"] = PAGES["Adkar Vyx"].replace(
        f"*[[{BOOTS}|Mist Covered Boots]]", "")
    ingest.sync(conn, "rok", members=members,
                fetch=lambda ts: {t: stripped[t] for t in ts if t in stripped})
    assert conn.execute("SELECT COUNT(*) FROM plan_items").fetchone()[0] == 0


# ---------- the read side ----------

def loaded(tmp_path):
    fetch, members = fake_wiki()
    conn = fresh_db(tmp_path)
    ingest.sync(conn, "rok", fetch=fetch, members=members)
    return conn


def test_the_era_filter_reads_the_SOURCE_not_the_item(tmp_path):
    """An item introduced in EoF that also drops off a RoK named is RoK content
    for somebody planning RoK. Filing it by where it first appeared would hide
    it from the only reader asking."""
    conn = loaded(tmp_path)
    assert catalog.search(conn, eras=["rok"])["total"] == 1
    assert catalog.search(conn, eras=["eof"])["total"] == 0
    assert catalog.search(conn, eras=["rok", "eof"])["total"] == 1


def test_an_unknown_era_falls_back_and_never_widens(tmp_path):
    conn = loaded(tmp_path)
    assert catalog.search(conn, eras=["tso"])["total"] == 1   # -> the default, rok
    assert catalog.search(conn, eras=[])["total"] == 1


def test_potency_and_crit_cannot_be_ranked_by(tmp_path):
    """They are on 80% and 72% of the real catalog, so ordering by them orders
    by NOTHING — every candidate has them and the ranking collapses back into
    "how expensive is this item". The server refuses them whatever a hand-built
    URL asks for, because the answer would be meaningless rather than merely
    untidy. They stay on the card and stay available as columns."""
    assert "potency" not in wiki.PRIORITY_STATS
    assert "crit" not in wiki.PRIORITY_STATS
    assert catalog.weights(["potency", "crit"]) == {}
    conn = loaded(tmp_path)
    out = catalog.search(conn, eras=["rok"], order=["potency"])
    assert out["scored"] is False and out["items"][0]["score"] == 0.0
    # still on the card, which is a different question
    assert any(e["name"] == "Potency" for e in out["items"][0]["card"]["stats"]["effects"])


def test_the_priority_options_are_the_thirteen_that_separate_items(tmp_path):
    """Lindsay's list, from the game. Crit Bonus is absent for a third reason
    again — TLE does not have the stat at all."""
    assert wiki.PRIORITY_STATS == (
        "abmod", "acspeed", "arspeed",
        "aspeed", "dps", "multi", "flurry", "aeauto",
        "bchance", "hategain", "mit", "strike",
        "maxhealth")
    assert [name for name, _ in wiki.STAT_GROUPS] == [
        "Abilities", "Melee", "Tanking", "Also"]
    assert "critbonus" in wiki.ERA_HIDDEN_FIELDS


def test_hate_gain_is_read_and_is_a_percentage():
    """A real `EquipInformation` field the first pass missed entirely. The
    template renders it without a sign, but the values are 0.9 to 2 — which is
    not a flat amount of anything."""
    assert "hategain" in wiki.STAT_LABEL
    assert "hategain" in wiki.STAT_PCT


def test_the_order_is_all_the_scorer_is_told(tmp_path):
    """Rank, not weight. Reversing the order changes the score, and no weight
    ever leaves `catalog`."""
    conn = loaded(tmp_path)
    a = catalog.search(conn, eras=["rok"], order=["abmod"])["items"][0]
    b = catalog.search(conn, eras=["rok"], order=["acspeed", "abmod"])["items"][0]
    assert a["score"] == 100.0          # the only item, so it IS the scale
    assert "weight" not in json.dumps(a)
    assert b["score"] == 100.0


def test_no_order_means_nothing_is_ranked(tmp_path):
    """Absence is a statement: a stat not on the list is not scored, and an
    empty list scores nothing at all rather than falling back to a default."""
    out = catalog.search(conn := loaded(tmp_path), eras=["rok"])
    assert out["scored"] is False
    assert out["items"][0]["score"] == 0.0
    assert conn is not None


def test_stats_are_normalised_so_the_biggest_number_does_not_win(tmp_path):
    """2.1% Casting Speed and 98 Ability Mod are both "a lot". Without a scale
    a weighted sum ranks on nothing but which stat uses bigger numbers."""
    scale = {"acspeed": 4.0, "abmod": 100.0}
    w = catalog.weights(["acspeed", "abmod"])
    top = catalog.score({"acspeed": 4.0, "abmod": 0}, w, scale)
    bottom = catalog.score({"acspeed": 0, "abmod": 100.0}, w, scale)
    assert top > bottom                 # casting speed was listed first
    assert catalog.score({"acspeed": 4.0, "abmod": 100.0}, w, scale) == 100.0


def test_armour_weight_is_lifted_out_of_dtype_and_filters(tmp_path):
    """The one property that rules an item out before any stat on it matters —
    a plate tank cannot wear leather however good the numbers are. The wiki
    keeps it in `dtype` beside weapon and shield types, so it is derived rather
    than stored twice."""
    assert wiki.armor_of("Chain Armor") == "Chain"
    assert wiki.armor_of("One-Handed Crushing") is None
    assert wiki.armor_of("Tower Shield") is None
    assert wiki.armor_of(None) is None
    conn = loaded(tmp_path)
    assert catalog.search(conn, eras=["rok"], armor=["Chain"])["total"] == 1
    assert catalog.search(conn, eras=["rok"], armor=["Plate"])["total"] == 0
    assert catalog.meta(conn, ["rok"])["armor"] == ["Chain"]


def test_a_two_hander_says_so_in_the_slot(tmp_path):
    """The wiki files a greatsword and a dagger under the same
    `slot = Primary`, which invites comparing them as though the other hand
    were still free. 162 of the catalog's primaries take both hands."""
    assert wiki.is_two_handed("Two-Handed Crushing") is True
    assert wiki.is_two_handed("One-Handed Slashing") is False
    assert wiki.is_two_handed("Main Hand Piercing") is False
    assert wiki.is_two_handed("Chain Armor") is False
    assert wiki.is_two_handed(None) is False
    assert wiki.slot_label("Primary", "Two-Handed Crushing") == "Primary/2H"
    assert wiki.slot_label("Primary", "One-Handed Slashing") == "Primary"
    # armour keeps its plain slot; only a weapon can cost you a hand
    assert wiki.slot_label("Feet", "Chain Armor") == "Feet"
    assert wiki.slot_label(None, "Two-Handed Crushing") is None
    row = catalog.search(loaded(tmp_path), eras=["rok"])["items"][0]
    assert row["slot_label"] == "Feet" and row["two_handed"] is False


def test_naming_three_stats_shows_items_with_two_of_them(tmp_path):
    """EQ2 gear in these expansions is FOUR-STAT — potency and crit, which
    everything has, plus two more — so an item can carry at most about two of
    whatever you listed. Without a floor, naming three stats showed every item
    with ONE of them: on the real catalog that is 2,881 rows instead of 538,
    and 45% of the catalog carries no more than one priority stat at all."""
    assert catalog.default_match_min(["abmod", "acspeed", "arspeed"]) == 2
    assert catalog.default_match_min(["abmod", "acspeed"]) == 2
    assert catalog.default_match_min(["abmod"]) == 1
    assert catalog.default_match_min([]) == 0
    # naming five cannot make an item carry five
    assert catalog.default_match_min(list(wiki.PRIORITY_STATS)) == 2


def test_the_floor_drops_a_row_that_carries_only_one_priority(tmp_path):
    """The fixture boots have abmod and casting speed and no reuse speed, so
    they survive a 3-stat order at the default floor and not at a stricter
    one — and the floor is answered back rather than applied silently."""
    conn = loaded(tmp_path)
    three = ["abmod", "acspeed", "arspeed"]
    out = catalog.search(conn, eras=["rok"], order=three)
    assert out["match_min"] == 2 and out["total"] == 1
    assert catalog.search(conn, eras=["rok"], order=three,
                          match_min=3)["total"] == 0
    # one they only half-match: abmod is there, haste and flurry are not
    assert catalog.search(conn, eras=["rok"],
                          order=["abmod", "aspeed", "flurry"])["total"] == 0


def test_the_floor_counts_only_stats_that_actually_rank(tmp_path):
    """Potency does not rank, so it cannot be one of the two an item matches
    on — otherwise the rule a hand-built URL cannot get round would have a way
    round it after all."""
    conn = loaded(tmp_path)
    out = catalog.search(conn, eras=["rok"], order=["potency", "crit", "abmod"])
    assert out["ranked"] == ["abmod"]
    assert out["match_min"] == 1 and out["total"] == 1


def test_a_required_stat_filters_and_does_not_rank(tmp_path):
    """The one control that crosses the line on purpose — no weight can say
    "I will not look at anything without ability mod"."""
    conn = loaded(tmp_path)
    assert catalog.search(conn, eras=["rok"], required=["abmod"])["total"] == 1
    assert catalog.search(conn, eras=["rok"], required=["flurry"])["total"] == 0
    # required is per-stat and absolute; the match floor is about the order as
    # a whole. They are different controls and both apply.
    assert catalog.search(conn, eras=["rok"], required=["abmod"],
                          order=["abmod", "aspeed", "flurry"])["total"] == 0


def test_a_class_filter_uses_the_class_tree(tmp_path):
    conn = loaded(tmp_path)
    assert catalog.search(conn, eras=["rok"], classes=["mystic"])["total"] == 1
    assert catalog.search(conn, eras=["rok"], classes=["necromancer"])["total"] == 0


def test_the_card_is_items_display_shape_so_ItemCard_is_reused(tmp_path):
    """Three ways to meet an item — a chest drop, a link in Auction, a row on
    this page — and all three must open the SAME examine window."""
    row = catalog.search(loaded(tmp_path), eras=["rok"])["items"][0]
    card = row["card"]
    assert card["rarity"] == "Fabled"
    assert {"stats", "effects", "flags", "adornments"} <= set(card["stats"])
    assert card["stats"]["adornments"] == ["white", "orange", "turquoise"]
    # the blue block, in the examine window's own order
    assert [e["name"] for e in card["stats"]["effects"]][:2] == ["Potency", "Crit Chance"]
    assert card["effects"]["set"] == "Mist Covered Set"


def test_meta_reports_an_era_with_no_catalog_rather_than_hiding_it(tmp_path):
    """The page has to be able to say "EoF is not synced yet" instead of
    quietly showing an empty table."""
    m = catalog.meta(loaded(tmp_path), ["rok", "eof"])
    by_key = {e["key"]: e for e in m["eras"]}
    assert by_key["rok"]["items"] == 1 and by_key["rok"]["label"] == "RoK"
    assert by_key["eof"]["items"] == 0 and by_key["eof"]["synced_ts"] is None
    assert m["slots"] == ["Feet"] and m["kinds"] == ["raid"]
