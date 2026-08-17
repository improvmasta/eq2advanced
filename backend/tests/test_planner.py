"""The Planner: what the wiki's templates mean, and how a declared ORDER ranks.

Fixtures are REAL pages recorded verbatim (`fixtures/wiki/planner_pages.json`),
so these exercise the template shapes the wiki actually uses rather than ones
invented to pass. Nothing here touches the network — the same rule every other
reference-data test follows, and here it also means the crawl itself is
exercised end to end with `fetch` and `members` handed in.
"""

import json
import pytest
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from planner import adornments, catalog, epic_timelines, ingest, outline, wiki

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
    # The page's legacy AGI field is the grouped TLE Primary Attributes rating.
    assert {row["stats"][key] for key in ("str", "agi", "wis", "int")} == {46}
    assert row["stats"]["sta"] == 46
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


def test_plain_linked_epic_rewards_enter_the_item_crawl():
    text = """{{QuestInformation|
 level = 80 |
 timeline = Warden Epic Weapon |
}}
== Reward ==
* [[Bite of the Wolf (Fabled)]]
* [[Epic Aspect Choice]]
"""
    row = wiki.parse_quest("Lessons of the Fallen", text)
    assert row["rewards"] == ["Bite of the Wolf (Fabled)"]


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


def test_only_plain_additive_set_bonuses_become_projectable_stats():
    assert catalog.set_bonus_stats("100 Ability Modifier") == {"abmod": 100.0}
    assert catalog.set_bonus_stats("2 Crit Chance|") == {"crit": 2.0}
    assert catalog.set_bonus_stats("65 Health|") == {"health": 65.0}
    assert catalog.set_bonus_stats("Applies Focus: Rift.") == {}
    assert catalog.set_bonus_stats("More damage sometimes") == {}
    # The game draws a tier as a comma list. Every segment has to type or the
    # line is prose: half a sentence read as arithmetic is worse than none.
    assert catalog.set_bonus_stats("4 Potency, 100 Ability Modifier, 5 Crit Chance") == {
        "potency": 4.0, "abmod": 100.0, "crit": 5.0}
    assert catalog.set_bonus_stats("3 Potency, and sometimes more") == {}


SPIRIT_SIPHONING = """{{AdornmentSet|Spirit Siphoning Set|
*{{Equip|Spirit Siphoning Set: Head}}
----
*(2) Applies '''''Focus: Lifetap IV.'''''
**Reduces power cost of Lifetap IV by 200.
3 Potency
*(6)
4 Potency
100 Ability Modifier
5 Crit Chance|
 level   =70|
}}"""


def test_a_set_tier_is_a_BLOCK_and_its_stats_are_the_bare_lines_under_it():
    """A TIER IS NOT ONE LINE. The page writes the proc on the `(N)` line, its
    explanation in sub-bullets, and the tier's flat stats as bare lines after
    those — which the game draws back ON the tier line ("(6) 4 Potency, 100
    Ability Mod, 5 Crit Chance"). Reading only the `(N)` line lost the Potency
    off every tier that had a proc, kept one stat of three where a tier had no
    proc, and dropped that tier entirely when its own line was empty."""
    s = wiki.parse_adorn_set("Spirit Siphoning Set (Adornment Set)",
                             SPIRIT_SIPHONING)
    two, six = s["bonuses"]
    assert two["text"] == "Applies Focus: Lifetap IV."
    assert two["stat_lines"] == ["3 Potency"]
    assert two["detail"] == ["Reduces power cost of Lifetap IV by 200."]
    assert catalog.bonus_stats(two) == {"potency": 3.0}
    # A tier whose own line is empty is still a tier, and it is the big one.
    assert six["pieces"] == 6 and six["text"] == ""
    assert catalog.bonus_stats(six) == {
        "potency": 4.0, "abmod": 100.0, "crit": 5.0}
    # ...and the block stops at the template's own fields rather than reading
    # `level =70` and `}}` as two more stats.
    assert six["stat_lines"] == ["4 Potency", "100 Ability Modifier",
                                 "5 Crit Chance"]


def test_a_set_tier_reaches_the_examine_card_the_way_the_game_draws_it(tmp_path):
    """Stats on the tier line, proc and explanation as the bullets under it."""
    conn = loaded(tmp_path)
    parsed = wiki.parse_adorn_set("Spirit Siphoning Set (Adornment Set)",
                                  SPIRIT_SIPHONING)
    row = dict(conn.execute("SELECT * FROM plan_items").fetchone())
    row.update(stats={}, adorns={}, icon=None, effects=None, effect_desc=None,
               classes=[], set_name="Spirit Siphoning Set",
               _set_bonuses=parsed["bonuses"])
    tiers = catalog.card(row)["stats"]["included_adornment"]["set_bonuses"]
    assert [t["required"] for t in tiers] == [2, 6]
    assert tiers[0]["effect"] == "3 Potency"
    assert tiers[0]["descriptions"] == [
        "Applies Focus: Lifetap IV.",
        "Reduces power cost of Lifetap IV by 200."]
    assert tiers[1]["effect"] == "4 Potency, 100 Ability Modifier, 5 Crit Chance"


# ---------- Phase 2: quest chains ----------

def quest_page(*, level="70", diff="Solo", prereq="", next_="",
               prelist="", nextlist="", zone="Test Zone"):
    """Small QuestInformation page for graph-shape tests.

    The broad parser tests above stay on recorded pages. These cases isolate
    punctuation and list syntax that need several otherwise-identical pages to
    exercise reconciliation and ordering without touching the network.
    """
    return f"""{{{{QuestInformation|
| timeline = Test Timeline
| jcat = Test Journal
| level = {level}
| diff = {diff}
| szone = {zone}
| patch = Rise of Kunark
| prereq = {prereq}
| prelist = {prelist}
| next = {next_}
| nextlist = {nextlist}
| altname =
}}}}
==Rewards==
"""


def test_a_comma_in_a_plain_quest_reference_is_part_of_the_title():
    q = wiki.parse_quest("Current", quest_page(
        prereq="Warm Skins, Fat Bellies", next_="One Fish, Two Fish"))
    assert q["prereq"] == [["Warm Skins, Fat Bellies"]]
    assert q["next"] == [["One Fish, Two Fish"]]


def test_list_references_preserve_comma_titles_and_or_groups():
    q = wiki.parse_quest("Current", quest_page(
        prelist="[[Warm Skins, Fat Bellies]] / {{Quest|Either Way}}, "
                "[[Both of These]]<br>[[And This]]"))
    assert q["prereq"] == [
        ["Warm Skins, Fat Bellies", "Either Way"],
        ["Both of These"],
        ["And This"],
    ]


def test_junk_references_are_rejected_and_scaling_level_is_kept_as_text():
    q = wiki.parse_quest("Current", quest_page(
        level="Scales", diff="Heroic", prereq="}}", next_="| >"))
    assert q["prereq"] == [] and q["next"] == []
    assert q["level"] is None and q["level_text"] == "Scales"
    assert q["diff_kind"] == "group"


# ---------- the crawl ----------

def fake_wiki(drops=()):
    """The crawl driven off recorded pages. `Adkar Vyx` drops the boots; the
    disambiguation is included so the version-following round is exercised.

    THREE category shapes are asked for now, not two — the crawl walks a
    `Named Monsters` category per zone as well as the expansion's, and a
    `Dropped Items` one per zone — so the fake answers each by name rather than
    letting anything unrecognised fall through to the quest list."""
    def members(cat):
        if cat == wiki.EPIC_WEAPONS_CATEGORY:
            return []
        if cat.endswith(wiki.NAMED_SUFFIX):
            return ["Adkar Vyx", "Admiral Tylix"]
        if cat.endswith(wiki.DROPS_SUFFIX):
            return list(drops)
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


def test_named_monsters_are_asked_for_by_zone_and_not_only_by_expansion():
    """THE EXPANSION CATEGORY IS NOT THE WHOLE EXPANSION.

    The wiki files a monster added between expansions under its live update and
    its tier and nothing else — `Kza'Bok` carries `LU39`, `Tier 8` and
    `Shard of Fear`, never `Echoes of Faydwer`. So asking the expansion alone
    left every zone it added mid-cycle out of the catalog, along with the
    level-70 gear those zones drop. Which expansion a ZONE belongs to is
    already reference data here, and it is the question that has an answer."""
    cats = wiki.named_categories("eof")
    assert cats[0] == "Category:Echoes of Faydwer Named Monsters"
    assert "Category:Shard of Fear Named Monsters" in cats
    assert "Category:Shard of Fear Dropped Items" in [
        cat for cat, _ in wiki.drop_categories("eof")]


def test_a_world_drop_enters_on_its_zone_when_no_named_links_it(tmp_path):
    """The two inversions can only find what a page LINKS, and nothing links
    what a trash mob drops — which is most of what a broker search returns.
    The zone's own drop list is the only index of it."""
    conn = fresh_db(tmp_path)
    orphan = dict(PAGES)
    orphan["Adkar Vyx"] = PAGES["Adkar Vyx"].replace(
        f"*[[{BOOTS}|Mist Covered Boots]]", "")
    _, members = fake_wiki(drops=[BOOTS])
    report = ingest.sync(conn, "rok", members=members,
                         fetch=lambda ts: {t: orphan[t] for t in ts if t in orphan})
    assert report["zone_drops"] == 1
    rows = conn.execute("SELECT * FROM plan_sources WHERE page_title=?",
                        (BOOTS,)).fetchall()
    assert [r["kind"] for r in rows] == ["zone"]
    assert rows[0]["zone"] in {z["zone"] for z in wiki.era_zones("rok")}
    # The place is the source, and that is the whole of the honest claim: no
    # monster, and no level invented for a zone that has none.
    assert rows[0]["source"] == rows[0]["zone"] and rows[0]["level"] is None


CRATE = "Faydwer Cloth Pattern: Head"
# The real shape, cut to one item. `{{!}}` is how a piped display name is
# escaped inside a template parameter — a bare pipe would end the parameter.
CRATE_PAGE = ("{{ItemInformation|\n type      =Crate|\n icat      = LEGENDARY|\n"
              " contains  ={{CItemList|\n"
              " item1 = " + BOOTS + "{{!}}Mist Covered Boots|\n"
              " iconnum1 = 528|\n }}|\n"
              " obtain    = {{DroppedItem|The Priest of Fear||"
              "The Estate of Unrest|Ornate}}|\n}}")


def test_a_crate_names_the_armour_and_the_armour_inherits_its_source(tmp_path):
    """**A SET PIECE IS BEHIND A CRATE, AND THE CRATE IS WHAT DROPS.** The mob
    hands you `Faydwer Cloth Pattern: Head` and you unpack one of three hoods
    out of it, only one of which carries Reuse Speed. A crate is an
    `ItemInformation` page, so the equipment parser rightly refuses it — and
    until the crawl looked INSIDE it, every set piece in both expansions was
    reachable from nothing at all."""
    assert wiki.crate_contents(CRATE_PAGE) == [BOOTS]
    assert wiki.crate_contents(PAGES[BOOTS]) == []      # not asked of equipment

    conn = fresh_db(tmp_path)
    pages = dict(PAGES, **{CRATE: CRATE_PAGE})
    pages["Adkar Vyx"] = PAGES["Adkar Vyx"].replace(
        f"*[[{BOOTS}|Mist Covered Boots]]", "")        # nothing links the boots
    _, members = fake_wiki(drops=[CRATE])
    ingest.sync(conn, "rok", members=members,
                fetch=lambda ts: {t: pages[t] for t in ts if t in pages})
    row = conn.execute("SELECT * FROM plan_items WHERE page_title=?",
                       (BOOTS,)).fetchone()
    assert row is not None
    # The crate is not equipment and never becomes a row of its own.
    assert conn.execute("SELECT COUNT(*) FROM plan_items WHERE page_title=?",
                        (CRATE,)).fetchone()[0] == 0
    # What you equip inherits where the box came from.
    src = conn.execute("SELECT * FROM plan_sources WHERE page_title=?",
                       (BOOTS,)).fetchone()
    assert src["kind"] == "zone"


def test_a_named_drop_does_not_also_become_a_world_drop(tmp_path):
    """A monster that names the item answers better than the zone it stands in,
    and a second row saying less is not more information."""
    conn = fresh_db(tmp_path)
    fetch, members = fake_wiki(drops=[BOOTS])
    report = ingest.sync(conn, "rok", fetch=fetch, members=members)
    assert report["zone_drops"] == 0
    kinds = [r["kind"] for r in conn.execute(
        "SELECT kind FROM plan_sources WHERE page_title=?", (BOOTS,))]
    assert kinds == ["raid"]


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


def test_a_crawl_that_collapses_is_refused_rather_than_reconciled(tmp_path):
    """`store` DELETES, which is what lets a correction land and is safe only
    while a person is watching. On a schedule nobody is, and a rate limit or an
    hour of Fandom being unhappy comes back as "the wiki no longer says any of
    this". A real itemization change never halves an expansion; a broken fetch
    always does."""
    conn = fresh_db(tmp_path)
    fetch, members = fake_wiki()
    ingest.sync(conn, "rok", fetch=fetch, members=members)
    assert conn.execute("SELECT COUNT(*) FROM plan_items").fetchone()[0] == 1

    def nothing(_titles):
        return {}
    with pytest.raises(ingest.CrawlCollapsed):
        ingest.sync(conn, "rok", fetch=nothing, members=members)
    # ...and the catalog is still there, which is the whole point.
    assert conn.execute("SELECT COUNT(*) FROM plan_items").fetchone()[0] == 1
    # An operator who knows the drop is real can still say so.
    ingest.sync(conn, "rok", fetch=nothing, members=members, force=True)
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
    # `force` because this fixture is a ONE-item era, and emptying a one-item
    # era is indistinguishable from a broken fetch — which is the whole reason
    # `CrawlCollapsed` exists. The guard is about SCALE and this test is about
    # reconciliation semantics; the guard has its own test above.
    ingest.sync(conn, "rok", members=members, force=True,
                fetch=lambda ts: {t: stripped[t] for t in ts if t in stripped})
    assert conn.execute("SELECT COUNT(*) FROM plan_items").fetchone()[0] == 0


def quest_wiki(pages):
    def members(cat):
        if cat == wiki.EPIC_WEAPONS_CATEGORY:
            return []
        if cat.endswith((wiki.NAMED_SUFFIX, wiki.DROPS_SUFFIX)):
            return []
        return list(pages)

    def fetch(titles):
        return {t: pages[t] for t in titles if t in pages}
    return fetch, members


def test_quest_edges_drop_dangling_titles_and_reconcile_on_resync(tmp_path):
    pages = {
        "First": quest_page(prereq="Missing"),
        "Second": quest_page(prereq="First"),
    }
    conn = fresh_db(tmp_path)
    fetch, members = quest_wiki(pages)
    report = ingest.sync(conn, "rok", fetch=fetch, members=members)
    assert report["quests"] == 2
    assert report["edges"] == 1 and report["dangling"] == 1
    assert [tuple(r) for r in conn.execute(
        "SELECT from_page, to_page FROM plan_quest_edges")] == [("First", "Second")]

    # A crawl is a snapshot, not an append. Removing the dependant removes its
    # quest row and the edge that the previous snapshot supplied.
    pages.pop("Second")
    report = ingest.sync(conn, "rok", fetch=fetch, members=members)
    assert report["quests"] == 1 and report["edges"] == 0
    assert conn.execute("SELECT COUNT(*) FROM plan_quest_edges").fetchone()[0] == 0


def test_an_edge_can_resolve_to_a_quest_from_another_era(tmp_path):
    conn = fresh_db(tmp_path)
    eof = {"An Older Quest": quest_page(level="65")}
    fetch, members = quest_wiki(eof)
    ingest.sync(conn, "eof", fetch=fetch, members=members)

    rok = {"A Kunark Quest": quest_page(level="70", prereq="An Older Quest")}
    fetch, members = quest_wiki(rok)
    report = ingest.sync(conn, "rok", fetch=fetch, members=members)
    assert report["edges"] == 1 and report["dangling"] == 0
    edge = conn.execute(
        "SELECT from_page, to_page, era FROM plan_quest_edges").fetchone()
    assert tuple(edge) == ("An Older Quest", "A Kunark Quest", "rok")


def test_a_second_edge_pass_closes_cross_era_links_stored_in_either_order(tmp_path):
    conn = fresh_db(tmp_path)
    rok = ingest.crawl("rok", *quest_wiki({
        "A Kunark Quest": quest_page(level="70", prereq="An Older Quest"),
    }))
    eof = ingest.crawl("eof", *quest_wiki({
        "An Older Quest": quest_page(level="65"),
    }))
    first = ingest.store(conn, rok)
    assert first["edges"] == 0 and first["dangling"] == 1
    ingest.store(conn, eof)

    report = ingest.reconcile_edges(conn, "rok", rok["edges"])
    assert report == {"edges": 1, "dangling": 0}
    assert tuple(conn.execute(
        "SELECT from_page, to_page FROM plan_quest_edges").fetchone()) == (
            "An Older Quest", "A Kunark Quest")


def test_prerequisites_beat_level_in_the_outline_order():
    rows = [
        {"key": "later", "name": "Later", "level": 70},
        {"key": "first", "name": "First", "level": 80},
        {"key": "free", "name": "Free", "level": 75},
    ]
    ordered = outline._order(rows, [("first", "later")])
    keys = [r["key"] for r in ordered]
    assert keys == ["free", "first", "later"]


def test_a_prerequisite_closure_is_one_questline_unit():
    ordered = [
        {"key": "first", "name": "First", "epic": False, "timeline": "A"},
        {"key": "middle", "name": "Middle", "epic": False, "timeline": "B"},
        {"key": "reward", "name": "Reward", "epic": False, "timeline": "B",
         "gets": [{"page_title": "Hammer", "name": "Worker Sledgemallet",
                   "via_set": None}]},
    ]
    lines = outline._questlines(
        ordered, {"reward"}, {"first": {"reward"}, "middle": {"reward"}})
    assert lines == [{
        "key": "questline:reward",
        "pages": ["first", "middle", "reward"],
        "goals": ["reward"],
        "targets": [{"page_title": "Hammer", "name": "Worker Sledgemallet",
                     "via_set": None}],
        "timeline": None,
        "count": 3,
    }]


def test_item_and_set_picks_are_reported_when_they_cannot_be_placed(tmp_path):
    out = outline.outline(
        fresh_db(tmp_path), eras=["rok"], items=["Missing Item"],
        sets=["Missing Set"])
    assert out["unplaced"] == ["Missing Item", "Missing Set"]


def test_outline_rejects_items_and_set_carriers_for_another_class(tmp_path):
    conn = fresh_db(tmp_path)
    with conn:
        conn.executemany(
            "INSERT INTO plan_items (page_title,name,era,slot,level,tier,classes,"
            "set_name,stats_json,adorns_json,fetched_ts) VALUES "
            "(?,?, 'rok','Primary',80,'FABLED',?,?, '{}','{}',0)", [
                ("Necro Epic", "Necro Epic", "necromancer", None),
                ("Wizard Epic", "Wizard Epic", "wizard", None),
                ("Wizard Set Hat", "Wizard Set Hat", "wizard", "Arcane Set"),
            ])
        conn.executemany(
            "INSERT INTO plan_sources (page_title,source_page,source,kind,era) "
            "VALUES (?,? ,?,'quest','rok')", [
                ("Necro Epic", "Necro Quest", "Necro Quest"),
                ("Wizard Epic", "Wizard Quest", "Wizard Quest"),
                ("Wizard Set Hat", "Wizard Set Quest", "Wizard Set Quest"),
            ])
        conn.executemany(
            "INSERT INTO plan_quests (page_title,name,era,kind,fetched_ts) "
            "VALUES (?,?,'rok','solo',0)", [
                ("Necro Quest", "Necro Quest"),
                ("Wizard Quest", "Wizard Quest"),
                ("Wizard Set Quest", "Wizard Set Quest"),
            ])

    result = outline.outline(
        conn, eras=["rok"], items=["Necro Epic", "Wizard Epic"],
        sets=["Arcane Set"], class_name="necromancer")
    assert [row["name"] for row in result["rows"]] == ["Necro Quest"]
    assert result["ineligible"] == ["Arcane Set", "Wizard Epic"]


def test_outline_tracks_one_exact_set_piece_and_returns_real_carrier_cards(tmp_path):
    conn = fresh_db(tmp_path)
    with conn:
        conn.execute(
            "INSERT INTO plan_sets VALUES (?,?,?,?,?,?,?)",
            ("Arcane Set", "Arcane Set (Adornment Set)", "rok", 70,
             json.dumps(["Arcane Set: Head", "Arcane Set: Feet"]),
             json.dumps([{"pieces": 2, "text": "+20 Ability Modifier"}]), 1))
        conn.executemany(
            "INSERT INTO plan_items (page_title,name,era,slot,level,tier,classes,"
            "set_name,stats_json,adorns_json,fetched_ts) VALUES "
            "(?,?, 'rok',?,70,'FABLED','necromancer','Arcane Set','{}','{}',0)", [
                ("Arcane Hood", "Arcane Hood", "Head"),
                ("Arcane Slippers", "Arcane Slippers", "Feet"),
            ])
        conn.executemany(
            "INSERT INTO plan_sources (page_title,source_page,source,kind,era) "
            "VALUES (?,? ,?,'quest','rok')", [
                ("Arcane Hood", "Hood Quest", "Hood Quest"),
                ("Arcane Slippers", "Feet Quest", "Feet Quest"),
            ])
        conn.executemany(
            "INSERT INTO plan_quests (page_title,name,era,kind,fetched_ts) "
            "VALUES (?,?,'rok','solo',0)", [
                ("Hood Quest", "Hood Quest"),
                ("Feet Quest", "Feet Quest"),
            ])

    result = outline.outline(
        conn, eras=["rok"], sets=["Arcane Set: Head"],
        class_name="necromancer")
    assert [row["name"] for row in result["rows"]] == ["Hood Quest"]
    got = result["rows"][0]["gets"][0]
    assert got["name"] == "Arcane Hood"
    assert got["via_set"] == "Arcane Set"
    assert got["via_set_piece"] == "Arcane Set: Head"
    assert got["card"]["name"] == "Arcane Hood"
    assert result["unplaced"] == []


def test_epic_timeline_rejects_another_class_when_item_classes_are_unknown(tmp_path):
    conn = fresh_db(tmp_path)
    with conn:
        conn.execute(
            "INSERT INTO plan_items (page_title,name,era,slot,classes,stats_json,"
            "adorns_json,fetched_ts) VALUES "
            "('Old Wizard Epic','Old Wizard Epic','rok','Primary','', '{}','{}',0)")
        conn.execute(
            "INSERT INTO plan_sources (page_title,source_page,source,kind,era) "
            "VALUES ('Old Wizard Epic','Wizard Start','Wizard Start','quest','rok')")
        conn.execute(
            "INSERT INTO plan_quests (page_title,name,era,timeline,kind,fetched_ts) "
            "VALUES ('Wizard Start','Wizard Start','rok','Wizard Epic Weapon','solo',0)")
        conn.execute(
            "INSERT INTO plan_epic_timelines "
            "(title,class_name,quests_json,requirements_json,source_url,source_version,fetched_ts) "
            "VALUES ('Wizard Epic Weapon Timeline','wizard','[]','[]','https://wik',1,0)")

    result = outline.outline(
        conn, eras=["rok"], items=["Old Wizard Epic"], class_name="necromancer")
    assert result["rows"] == []
    assert result["ineligible"] == ["Old Wizard Epic"]


def test_wikq2_epic_timeline_supplies_requirements_and_replaces_the_loop(tmp_path):
    conn = fresh_db(tmp_path)
    quests = [
        ("An Ayonic Journey", "solo"),
        ("Feeding the Flame of Yore", "solo"),
        ("Son'Nia's Song", "raid"),
    ]
    with conn:
        conn.execute(
            "INSERT INTO plan_items (page_title,name,era,slot,level,tier,classes,"
            "stats_json,adorns_json,fetched_ts) VALUES "
            "('Ayonic Axe (Fabled)','Ayonic Axe','rok','Primary',80,'FABLED',"
            "'troubador','{}','{}',0)")
        conn.execute(
            "INSERT INTO plan_sources (page_title,source_page,source,kind,era,detail) "
            "VALUES ('Ayonic Axe (Fabled)','Feeding the Flame of Yore','Feeding the Flame of Yore',"
            "'quest','rok','Troubador Epic Weapon')")
        conn.executemany(
            "INSERT INTO plan_quests (page_title,name,era,timeline,kind,fetched_ts) "
            "VALUES (?,?, 'rok','Troubador Epic Weapon',?,0)",
            [(title, title, kind) for title, kind in quests])
        conn.executemany(
            "INSERT INTO plan_quest_edges (from_page,to_page,era,kind,or_group) "
            "VALUES (?,?,'rok','hard',0)", [
                ("Feeding the Flame of Yore", "An Ayonic Journey"),
                ("An Ayonic Journey", "Feeding the Flame of Yore"),
            ])
        conn.execute(
            "INSERT INTO plan_epic_timelines "
            "(title,class_name,quests_json,requirements_json,source_url,source_version,fetched_ts) "
            "VALUES (?,?,?,?,?,1,0)", (
                "Troubador Epic Weapon Timeline", "troubador",
                json.dumps([{"title": title, "url": f"https://wik/{title}"}
                            for title, _ in quests]),
                json.dumps([
                    {"text": "Uruvanian from Words of Air", "quests": [
                        {"title": "Words of Air", "url": "https://wik/Words_of_Air"}]},
                    {"text": "Complete The Poets Palace Access", "quests": []},
                ]), "https://wik/Troubador_Epic_Weapon_Timeline"))

    result = outline.outline(conn, eras=["rok"], items=["Ayonic Axe (Fabled)"])
    assert [row["name"] for row in result["rows"]] == [
        "Complete The Poets Palace Access", "Words of Air",
        "An Ayonic Journey", "Feeding the Flame of Yore"]
    assert "Son'Nia's Song" not in [row["name"] for row in result["rows"]]
    assert [row["requirement"] for row in result["rows"]] == [True, True, False, False]
    epic_quests = sorted(
        (row for row in result["rows"] if row["kind"] == "quest"
         and not row["requirement"]),
        key=lambda row: row["epic_order"])
    assert [row["name"] for row in epic_quests] == [
        "An Ayonic Journey", "Feeding the Flame of Yore"]
    assert all(row["epic"] for row in result["rows"])
    assert all("start_waypoint" in row for row in epic_quests)


def test_epic_snapshot_keeps_a_canonical_step_missing_from_the_quest_crawl(tmp_path):
    conn = fresh_db(tmp_path)
    with conn:
        conn.execute(
            "INSERT INTO plan_items (page_title,name,era,slot,tier,classes,stats_json,"
            "adorns_json,fetched_ts) VALUES "
            "('Test Epic','Test Epic','rok','Primary','FABLED','wizard','{}','{}',0)")
        conn.execute(
            "INSERT INTO plan_sources (page_title,source_page,source,kind,era,detail) "
            "VALUES ('Test Epic','Reward Step','Reward Step','quest','rok','Wizard Epic Weapon')")
        conn.execute(
            "INSERT INTO plan_quests (page_title,name,era,timeline,kind,fetched_ts) "
            "VALUES ('Reward Step','Reward Step','rok','Wizard Epic Weapon','group',0)")
        conn.execute(
            "INSERT INTO plan_epic_timelines "
            "(title,class_name,quests_json,requirements_json,source_url,source_version,fetched_ts) "
            "VALUES (?,?,?,?,?,1,0)", (
                "Wizard Epic Weapon Timeline", "wizard",
                json.dumps([
                    {"title": "Uncrawled Step", "url": "https://wik/Uncrawled_Step"},
                    {"title": "Reward Step", "url": "https://wik/Reward_Step"},
                    {"title": "Later Mythical Work", "url": "https://wik/Later"},
                ]), "[]", "https://wik/Wizard_Epic_Weapon_Timeline"))

    result = outline.outline(
        conn, eras=["rok"], items=["Test Epic"], class_name="wizard")
    quests = sorted((row for row in result["rows"] if not row["requirement"]),
                    key=lambda row: row["epic_order"])
    assert [row["name"] for row in quests] == ["Uncrawled Step", "Reward Step"]
    assert quests[0]["zone"] is None and quests[0]["difficulty"] == "unknown"


def test_wikq2_epic_export_requires_all_24_original_classes():
    with pytest.raises(ValueError, match="24 timelines"):
        epic_timelines.validate({"version": 1, "timelines": []})


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


def test_class_epics_have_a_dedicated_fabled_then_mythical_read(tmp_path):
    conn = loaded(tmp_path)
    with conn:
        conn.execute("UPDATE plan_items SET classes='mystic', slot='Primary', tier='FABLED'")
        conn.execute("UPDATE plan_sources SET kind='quest', detail='Mystic Epic Weapon'")
        conn.execute(
            "INSERT INTO plan_items (page_title, name, era, slot, level, tier, "
            "classes, stats_json, adorns_json, fetched_ts) VALUES "
            "('Mystic Mythical', 'The Real Mythical', 'rok', 'Primary', 80, "
            "'MYTHICAL', 'mystic', '{}', '{}', 0), "
            "('Enervated Mystic', 'Enervated The Real Mythical', 'rok', "
            "'Primary', 80, 'MYTHICAL', 'mystic', '{}', '{}', 0)")
        conn.executemany(
            "INSERT INTO plan_sources (page_title, source_page, source, kind, detail, era) "
            "VALUES (?, 'Final Quest', 'Final Quest', 'quest', 'Mystic Epic Weapon', 'rok')",
            [("Mystic Mythical",), ("Enervated Mystic",)])
    out = catalog.epics(conn, "mystic")
    assert [(item["name"], item["epic_stage"]) for item in out["items"]] == [
        ("Mist Covered Boots", "fabled"), ("The Real Mythical", "mythical")]
    assert catalog.epics(conn, "necromancer") == {"items": []}


def test_the_four_epic_category_omissions_are_explicitly_crawled():
    assert set(wiki.EPIC_WEAPON_EXTRA_PAGES) == {
        "Dream Scorcher (Mythical)", "Mirage Star (Mythical)",
        "Revitalized Vel'Arek", "Sedition, Sword of the Bloodmoon",
    }
    assert set(wiki.EPIC_WEAPON_EXTRA_QUESTS) == {
        "A Bloodmoon Rising!", "Revitalizing Vel'Arek",
        "The Dream Scorcher, Part Two", "The Mirage Star",
    }


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


def test_the_card_says_who_can_wear_it_and_stays_quiet_when_everyone_can(tmp_path):
    """The one property that rules an item out before any number on it matters.

    Silence is the answer for an unrestricted item, because a list of every
    class on the server is not a restriction and the game does not print one
    either."""
    conn = loaded(tmp_path)
    card = catalog.search(conn, eras=["rok"])["items"][0]["card"]
    assert "Mystic" in card["classes"] and "Necromancer" not in card["classes"]
    everyone = dict(conn.execute("SELECT * FROM plan_items").fetchone())
    everyone.update(stats={}, adorns={}, icon=None, effects=None,
                    effect_desc=None, classes=list(wiki.SUBCLASSES))
    assert catalog.card(everyone)["classes"] is None


def test_an_empty_table_says_which_control_emptied_it(tmp_path):
    """"Nothing matches" reads as "no such item exists", which is a claim about
    the GAME that a crawl of somebody else's wiki cannot make. Answering how
    many rows survived everything but the stat controls separates "the stats
    found nothing among rows that do exist" from "there were no rows"."""
    conn = loaded(tmp_path)
    out = catalog.search(conn, eras=["rok"], required=["flurry"])
    assert out["total"] == 0 and out["before_priorities"] == 1
    assert out["catalog"] == 1
    out = catalog.search(conn, eras=["rok"], slots=["head"])
    assert out["total"] == 0 and out["before_priorities"] == 0


def test_item_level_range_filters_both_edges(tmp_path):
    conn = loaded(tmp_path)
    assert catalog.search(conn, eras=["rok"], level_min=80, level_max=80)["total"] == 1
    assert catalog.search(conn, eras=["rok"], level_min=81)["total"] == 0
    assert catalog.search(conn, eras=["rok"], level_max=79)["total"] == 0


def test_white_adornment_catalog_has_real_tier_values_and_slot_rules():
    rows = adornments.white_catalog()
    casting = next(row for row in rows
                   if row["name"] == "Scintillating Adornment of Swift Casting (Superior)")
    assert casting["level"] == 60 and casting["tier"] == 7
    assert casting["summary"] == "+3.8% Casting Speed"
    assert casting["stats"] == {"acspeed": 3.8}
    assert casting["prefix"] == "Scintillating"
    assert casting["family"] == "Swift Casting"
    assert "Wrist" in casting["slots"] and "Head" not in casting["slots"]
    assert not any(row["effect"] in {"Crit Chance", "Crit Bonus"} for row in rows)


def test_legacy_all_set_bonus_is_ability_mod_in_display_and_math(tmp_path):
    conn = loaded(tmp_path)
    conn.execute(
        "INSERT INTO plan_sets VALUES (?,?,?,?,?,?,?)",
        ("Haunted Visions", "Haunted Visions (Adornment Set)", "rok", 70,
         "[]", json.dumps([{"pieces": 3, "text": "+35 All"}]), 1))
    conn.execute("UPDATE plan_items SET set_name='Haunted Visions'")
    conn.commit()
    bonus = catalog.sets(conn, eras=["rok"])["sets"][0]["bonuses"][0]
    assert bonus["text"] == "+35 Ability Mod"
    assert bonus["stats"] == {"abmod": 35.0}


def test_the_card_is_items_display_shape_so_ItemCard_is_reused(tmp_path):
    """Three ways to meet an item — a chest drop, a link in Auction, a row on
    this page — and all three must open the SAME examine window."""
    conn = loaded(tmp_path)
    conn.execute(
        "INSERT INTO plan_sets VALUES (?,?,?,?,?,?,?)",
        ("Mist Covered Set", "Mist Covered Set (Adornment Set)", "rok", 80,
         "[]", json.dumps([{"pieces": 2, "text": "100 Ability Modifier"},
                            {"pieces": 4, "text": "2 Crit Chance"}]), 1))
    row = catalog.search(conn, eras=["rok"])["items"][0]
    card = row["card"]
    assert card["rarity"] == "Fabled"
    assert card["level"] == 80 and card["slot"] == "Feet"
    assert {"stats", "effects", "flags", "adornments"} <= set(card["stats"])
    assert card["stats"]["adornments"] == ["white", "orange", "turquoise"]
    primary = [s for s in card["stats"]["stats"]
               if s["name"] == "Primary Attributes"]
    assert primary == [{"name": "Primary Attributes", "value": 46.0, "pct": False}]
    assert not {"Strength", "Agility", "Wisdom", "Intelligence"} & {
        s["name"] for s in card["stats"]["stats"]}
    included = card["stats"]["included_adornment"]
    assert included["name"] == "Mist Covered Set" and included["color"] == "turquoise"
    assert [b["required"] for b in included["set_bonuses"]] == [2, 4]
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


def _extra_item(conn, title, stats, tier="FABLED"):
    """A second catalog row, so ordering has something to order."""
    conn.execute(
        "INSERT INTO plan_items (page_title, name, era, slot, level, tier, "
        "stats_json, adorns_json, fetched_ts) VALUES (?,?,?,?,?,?,?,?,0)",
        (title, title, "rok", "Feet", 80, tier, json.dumps(stats), "{}"))
    conn.execute(
        "INSERT INTO plan_sources (page_title, source_page, source, kind, era) "
        "VALUES (?,?,?,?,?)", (title, f"{title} source", "A named", "raid", "rok"))
    conn.commit()


def test_the_rows_carrying_all_your_stats_lead_the_table(tmp_path):
    """NAMING A THIRD STAT IS ASKING FOR THE ITEMS THAT HAVE ALL THREE, and in
    four-stat expansions there are a handful of them or none. Sorted on score
    alone a two-stat item with large numbers outranks a three-stat item with
    modest ones, so the rows the third choice was made to find were buried
    under the rows it was made to demote.

    So the count of priorities a row carries orders the table before its score
    does — a tier, not a filter: nothing is hidden for being one stat short, it
    just follows. Because the sort decides which rows survive `limit`, this
    cannot be left to the browser."""
    conn = loaded(tmp_path)
    _extra_item(conn, "Big Two", {"abmod": 200, "acspeed": 9})
    _extra_item(conn, "Small Three", {"abmod": 3, "acspeed": 0.2, "arspeed": 0.2})
    out = catalog.search(conn, eras=["rok"], order=["abmod", "acspeed", "arspeed"])
    assert [i["name"] for i in out["items"]][0] == "Small Three"
    assert [i["matched"] for i in out["items"]] == [3, 2, 2]
    # and the complete match is genuinely the WORSE score, which is the case
    # that made this necessary
    scores = {i["name"]: i["score"] for i in out["items"]}
    assert scores["Small Three"] < scores["Big Two"]


def test_a_rarity_is_asked_for_by_the_word_a_player_uses(tmp_path):
    """The wiki's `icat` holds eleven spellings across the real catalog and a
    player has five words. How a piece was MADE is not a rarity: mastercrafted
    armour is Legendary and a mastercrafted fabled piece is Fabled, so both
    fold into the tier they actually are. A value nothing recognizes stays
    bucketless rather than being assigned a rarity the wiki never claimed."""
    assert wiki.tier_bucket("MASTERCRAFTED LEGENDARY") == "legendary"
    assert wiki.tier_bucket("MASTERCRAFTED FABLED") == "fabled"
    assert wiki.tier_bucket("FABLED, GREATER RELIC") == "fabled"
    assert wiki.tier_bucket("MYTHICAL") == "mythical"
    assert wiki.tier_bucket("UNCOMMON") is None
    assert wiki.tier_bucket(None) is None

    conn = loaded(tmp_path)
    _extra_item(conn, "Made By Hand", {"abmod": 5}, tier="MASTERCRAFTED FABLED")
    assert catalog.search(conn, eras=["rok"], tiers=["fabled"])["total"] == 2
    assert catalog.search(conn, eras=["rok"], tiers=["legendary"])["total"] == 0
    # the raw crawled spelling still answers, so an older link keeps working
    assert catalog.search(conn, eras=["rok"], tiers=["FABLED"])["total"] == 2
    # and the facet offers the buckets, ascending, with their labels
    assert catalog.meta(conn, ["rok"])["tiers"] == [
        {"key": "fabled", "label": "Fabled", "items": 2}]


def test_several_sources_can_be_asked_for_at_once(tmp_path):
    """"Group or raid" is a normal thing to want and was two searches while the
    facet was a dropdown."""
    conn = loaded(tmp_path)
    _extra_item(conn, "Off A Quest", {"abmod": 5})
    conn.execute("UPDATE plan_sources SET kind='quest' WHERE page_title=?",
                 ("Off A Quest",))
    conn.commit()
    assert catalog.search(conn, eras=["rok"], kinds=["quest"])["total"] == 1
    assert catalog.search(conn, eras=["rok"], kinds=["raid"])["total"] == 1
    assert catalog.search(conn, eras=["rok"], kinds=["raid", "quest"])["total"] == 2
