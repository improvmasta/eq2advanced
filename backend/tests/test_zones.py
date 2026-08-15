"""Zone reference data: the expansion a zone came from, read off the wiki.

These assert the two things the rest of the app leans on — that the instance
number the game sticks on a repeat visit is not part of a zone's identity, and
that eras land in unlock order — plus a handful of real answers, because the
committed file is the point and a lookup that silently returned nothing would
pass every structural test written about it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import gamewiki
import zones


def test_the_instance_number_is_about_the_night_not_the_place():
    assert zones.base_name("Castle Mistmoore 2") == "Castle Mistmoore"
    assert zones.base_name("The City of Freeport 5") == "The City of Freeport"
    assert zones.base_name("The Emerald Halls") == "The Emerald Halls"
    # not a suffix to strip: the number is the name
    assert zones.base_name("Fizzlandia") == "Fizzlandia"


def test_a_numbered_visit_resolves_to_the_same_zone():
    assert zones.era_of("Castle Mistmoore 2") == zones.era_of("Castle Mistmoore")
    assert zones.info("The Emerald Halls 3")["zone"] == "The Emerald Halls"
    assert zones.display_name("Castle Mistmoore 2") == "Castle Mistmoore"


@pytest.mark.parametrize("zone,era,raid", [
    ("The Emerald Halls", "Echoes of Faydwer", True),
    ("Freethinker Hideout", "Echoes of Faydwer", True),
    ("Nizara, City of the Nayad", "Fallen Dynasty", False),
    ("The Bonemire", "Kingdom of Sky", False),
    # filed under LU32 on the wiki, whose own patch notes opened it — the
    # date puts it inside EoF, and nobody had to hand-file it
    ("The Estate of Unrest", "Echoes of Faydwer", False),
    # LU22: "a dangerous new raid zone in Kingdom of Sky"
    ("The Lyceum of Abhorrence", "Kingdom of Sky", True),
])
def test_real_zones_get_their_real_expansion(zone, era, raid):
    assert zones.era_of(zone) == era
    assert zones.is_raid(zone) is raid


def test_a_zone_nobody_has_heard_of_has_no_era_rather_than_a_guessed_one():
    assert zones.info("A Zone Nobody Has Heard Of") is None
    assert zones.era_of("A Zone Nobody Has Heard Of") is None
    assert zones.era_label(None) == "Other"


def test_mixed_zones_only_promote_their_actual_raid_target():
    assert not zones.is_raid_run("Castle Mistmoore 2", ["The Cloaked Dhampyre"])
    assert zones.is_raid_run("Castle Mistmoore", ["Mayong Mistmoore"])
    assert not zones.is_raid_run("Loping Plains", ["a Mistmoore watcher"])
    assert zones.is_raid_run("Loping Plains", ["Pumpkin Headed Horseman"])
    assert zones.is_raid_run("Rivervale", ["Avatar of Mischief"])


def test_eras_sort_in_unlock_order_and_the_unknown_one_sorts_last():
    order = [zones.era_rank(e) for e in
             ("Shattered Lands", "Desert of Flames", "Kingdom of Sky",
              "Echoes of Faydwer")]
    assert order == sorted(order)
    assert zones.era_rank(None) > zones.era_rank("Scars of Destruction")
    assert zones.era_rank("Bristlebane Day") > zones.era_rank("Echoes of Faydwer")


def test_a_live_update_lands_in_whichever_expansion_was_current():
    assert zones.expansion_on("2006-04-13") == "Kingdom of Sky"
    assert zones.expansion_on("2007-06-28") == "Echoes of Faydwer"
    assert zones.expansion_on("2006-11-14") == "Echoes of Faydwer"   # launch day
    assert zones.expansion_on("2004-01-01") is None                  # before the game


def test_the_wiki_dates_are_read_in_all_four_forms_they_were_typed_in():
    """Six years of patch notes by different hands. A parser that only knew one
    form left the zones from the others with no era at all."""
    assert gamewiki.parse_date("April 13, 2006") == "2006-04-13"
    assert gamewiki.parse_date("December 20th 2006") == "2006-12-20"
    assert gamewiki.parse_date("April 17,2011") == "2011-04-17"
    assert gamewiki.parse_date("2/28/2007") == "2007-02-28"
    assert gamewiki.parse_date("soon") is None
