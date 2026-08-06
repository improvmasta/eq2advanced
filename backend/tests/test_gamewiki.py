"""The EQ2 wiki as reference data — parsing, era safety, and the one thing it
knows that the log cannot.

Fixtures are REAL pages recorded verbatim (`fixtures/wiki/aa_pages.json`), so
these exercise the template shapes the wiki actually uses rather than ones
invented to pass. Nothing here touches the network, the same rule the Census
tests follow.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gamewiki
from census.abilityreview import suggest

PAGES = json.loads(
    (Path(__file__).parent / "fixtures" / "wiki" / "aa_pages.json").read_text())


def parsed(title, tiers=()):
    return gamewiki.parse_page(title, PAGES[title], "aa", "eof", set(tiers))


def test_an_activated_aa_is_recognised_by_its_recast():
    """The whole reason this source is in the system. `Lifeburn` is a button
    with a five minute recast; the log shows it as damage with no prepare line,
    which is indistinguishable from a gear proc."""
    p = parsed("Lifeburn", {"necromancer"})
    assert p["activated"] == 1
    assert p["recast_s"] == 300.0
    assert p["tiers"] == "necromancer"
    assert p["line"] == "Rotting"


def test_a_passive_aa_has_neither_recast_nor_cost():
    p = parsed("Ancestral Spirits", {"shaman"})
    assert p["activated"] == 0
    assert p["recast_s"] is None


def test_effects_stop_at_the_end_of_the_template():
    """A page whose `effects` field says "see below" has no bullets, and
    without a `}}` boundary the capture swallowed the rest of the article."""
    p = parsed("Ancestral Spirits", {"shaman"})
    assert p["effects"] == "see below"


def test_an_aa_can_be_a_proc_SOURCE():
    """`Avast Ye` is the rogue AA behind `Pirate Stab`, and nothing in Census
    says so — the wiki writes the same trigger grammar Census does, with letter
    placeholders where Census has real numbers."""
    p = parsed("Avast Ye", {"rogue"})
    casts = dict(gamewiki.proc_targets(p["effects"]))
    assert "Pirate Stab" in casts
    assert "melee hit" in casts["Pirate Stab"].lower()


def test_a_placeholder_percentage_does_not_stop_the_parse():
    # "has a X% chance to cast" — the wiki covers every rank at once, so the
    # number is a letter. What is cast still has to come through.
    got = gamewiki.proc_targets("*On a melee hit this spell has a X% chance "
                                "to cast Pirate Stab on target of attack.")
    assert got and got[0][0] == "Pirate Stab"


def test_a_disambiguated_title_keys_on_the_name_a_log_prints():
    assert gamewiki.log_name("Intoxication (AA)") == "Intoxication"
    assert gamewiki.log_name("Lifeburn (Assassin)") == "Lifeburn"
    assert gamewiki.log_name("Lifeburn") == "Lifeburn"


def test_recast_units():
    assert gamewiki._seconds("30.0 seconds") == 30.0
    assert gamewiki._seconds("5 minutes") == 300.0
    assert gamewiki._seconds("") is None


def test_every_era_this_server_will_never_see_is_off_by_default():
    """A level-70 EoF server must not label raids with Shadow Odyssey or
    Dragon abilities. The trees are separate on the wiki and the default only
    takes the one this server has."""
    assert gamewiki.DEFAULT_ERAS == ("eof",)
    for later in ("rok", "tso", "dov"):
        assert later in gamewiki.AA_TREES        # reachable when asked for
        assert later not in gamewiki.DEFAULT_ERAS


def _row(**kw):
    base = {"ruling": None, "curated_pet": 0, "curated_proc": 0, "scribed_by": "",
            "pet_definite": 0, "pet_own": 0, "pet_guess": 0, "player_casts": 0,
            "mob_casts": 0, "prepare_lines": 0, "logger_hits": 0,
            "player_classes": [], "proc_candidate": 0, "grant_kind": "",
            "grant_name": "", "grant_class": "", "trigger": "",
            "activated": None, "wiki_kind": "", "wiki_tiers": "", "wiki_line": "",
            "recast_s": None}
    return {**base, **kw}


def test_an_activated_aa_is_not_called_a_proc():
    """The correction. Without the wiki this row is "logger hits, no prepare
    line" — which the proc test reads as firing on its own."""
    row = _row(logger_hits=3547, prepare_lines=0, activated=1,
               wiki_kind="aa", wiki_tiers="necromancer", recast_s=300.0)
    what, conf, why = suggest(row)
    assert (what, conf) == ("player", "high")
    assert "recast" in why


def test_the_same_row_without_the_wiki_still_reads_as_a_proc():
    """Proof the wiki is what flips it, not something else in the row."""
    row = _row(logger_hits=3547, prepare_lines=0)
    assert suggest(row)[0] == "proc"


def test_a_passive_aa_is_a_proc():
    row = _row(logger_hits=200, activated=0, wiki_kind="aa",
               wiki_tiers="shaman", wiki_line="Wisdom")
    what, conf, why = suggest(row)
    assert what == "proc" and conf == "high"
    assert "PASSIVE" in why


def test_a_pressed_ability_still_wins_over_the_wiki():
    """A prepare line is the log PROVING a press. Nothing should overturn it —
    but it agrees with the wiki here, so check the ordering does not break the
    stronger evidence."""
    row = _row(prepare_lines=40, logger_hits=100, activated=1, wiki_kind="aa")
    assert suggest(row)[0] == "player"


# ---------- name collisions ----------
#
# A name is not a key. One name can be two abilities, and the log prints them
# identically — which is a thing to REPORT, not a race to win.

def test_the_same_aa_on_several_classes_merges_its_tiers():
    """`Enhance: Cure (Mystic)`, `(Templar)` and `(Warden)` are one ability
    granted to three classes. Overwriting kept one and lost two — 66 pages
    were collapsing to 29 names."""
    rows = [gamewiki.parse_page(f"Enhance: Cure ({c.title()})", "|line = Immunities|",
                                "aa", "eof", {c})
            for c in ("mystic", "templar", "warden")]
    merged = gamewiki._merge(rows)
    assert len(merged) == 1
    assert set(merged[0]["tiers"].split(",")) == {"mystic", "templar", "warden"}


def test_a_deity_miracle_does_not_overwrite_an_aa_of_the_same_name():
    """Keyed by (name, kind), so both survive — 37 AAs were being replaced by
    a blessing or miracle that happened to share a name."""
    rows = gamewiki._merge([
        gamewiki.parse_page("Battlerage", "|recast = 1 hour|deity = Rallos Zek|",
                            "deity", "eof", set()),
        gamewiki.parse_page("Battlerage", "|line = Fury|", "aa", "eof", {"berserker"}),
    ])
    assert {r["kind"] for r in rows} == {"aa", "deity"}


def test_a_disambiguation_page_is_not_an_ability():
    """`Tempest` really is a disambiguation page — it is the fury spell AND
    Karana's miracle. Ingesting it would let a god claim a class spell."""
    assert gamewiki.is_disambiguation(PAGES["Tempest"])
    assert not gamewiki.is_disambiguation(PAGES["Rallos' Devastation"])


def test_a_deity_ability_records_its_god():
    p = gamewiki.parse_page("Rallos' Devastation", PAGES["Rallos' Devastation"],
                            "deity", "eof", set())
    assert p["line"] == "Rallos Zek"      # the deity rides in `line`: what grants it
    assert p["tiers"] is None             # a god grants to worshippers, not classes
    assert p["activated"] == 1            # a miracle is pressed (1 hour recast)


def test_census_scribing_a_class_beats_a_wiki_name_match():
    """`Tempest` is a fury spell in these logs and Karana's miracle on the
    wiki. A Census spell record is the game saying a class scribes it; a wiki
    page matched by NAME is the weakest join there is."""
    row = _row(scribed_by="fury", activated=1, wiki_kind="deity", recast_s=3600.0)
    what, conf, why = suggest(row)
    assert (what, conf) == ("player", "high")
    assert "fury" in why and "DEITY" not in why


def test_a_name_the_wiki_holds_twice_never_drives_the_verdict():
    row = _row(logger_hits=200, activated=1, wiki_kind="aa", wiki_ambiguous=True)
    assert "ACTIVATED" not in suggest(row)[2]
