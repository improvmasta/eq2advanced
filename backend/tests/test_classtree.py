"""EQ2's class tree, and what a grant at any tier of it reaches.

The point of the module is AAs: they are handed out at every tier, so a ruling
against `predator` has to mean ranger AND assassin everywhere — grouping,
storage and the self-vs-granted comparison — without being written twice.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classtree as t
from census.abilityreview import classes_for


def test_a_group_reaches_its_subclasses():
    assert t.expand("predator") == frozenset({"ranger", "assassin"})
    assert t.expand("bard") == frozenset({"troubador", "dirge"})
    assert t.expand("crusader") == frozenset({"paladin", "shadowknight"})
    # an archetype reaches every subclass under it, through both tiers
    assert t.expand("scout") == frozenset({
        "ranger", "assassin", "troubador", "dirge",
        "swashbuckler", "brigand", "beastlord"})
    assert len(t.expand("fighter")) == 6      # all six fighters are tanks
    # a subclass is just itself
    assert t.expand("ranger") == frozenset({"ranger"})


def test_the_tree_covers_every_class_once():
    assert len(t.SUBCLASSES) == 26
    seen = [s for classes in t.TREE.values() for subs in classes.values() for s in subs]
    assert len(seen) == len(set(seen)), "a subclass cannot sit under two classes"
    # the standalone late additions are under the right archetype
    assert "channeler" in t.expand("priest")
    assert "beastlord" in t.expand("scout")


def test_an_unknown_target_reaches_nobody():
    """A typo must not quietly become a class. `expand` returning the input
    would file the ability under a class nobody can ever find."""
    assert t.expand("predatr") == frozenset()
    assert t.expand("") == frozenset()
    assert t.expand(None) == frozenset()
    assert t.normalize("predatr") == ""


def test_normalize_is_stable_and_drops_junk():
    assert t.normalize(" Predator , RANGER , junk ") == "predator,ranger"
    # same set, same string, whatever order it was typed in — the stored value
    # has to compare equal to itself
    assert t.normalize("ranger,predator") == t.normalize("predator,ranger")


def test_expand_all_reads_a_comma_list():
    assert t.expand_all("predator,templar") == frozenset({"ranger", "assassin", "templar"})
    # Census's own class column is the same shape, so one reader serves both
    assert t.expand_all("paladin,shadowknight") == t.expand("crusader")


def test_a_label_says_who_a_group_covers():
    assert t.label("predator") == "predator (assassin, ranger)"
    assert t.label("ranger") == "ranger"     # nothing to spell out


def _row(**kw):
    base = {"scribed_by": "", "grant_class": "", "player_classes": [], "ruling": None}
    return {**base, **kw}


def test_a_predator_grant_groups_under_both_subclasses():
    """The whole reason the tree exists: an AA ruled against `predator` has to
    appear under ranger and assassin on the Abilities page."""
    cols = classes_for(_row(ruling={"grant_class": "predator"}))
    assert set(cols) == {"ranger", "assassin"}


def test_a_scout_grant_reaches_all_seven():
    cols = classes_for(_row(grant_class="scout"))
    assert set(cols) == t.expand("scout")


def test_grouping_keeps_every_lead_and_dedupes():
    cols = classes_for(_row(
        scribed_by="ranger", grant_class="predator", player_classes=["assassin"]))
    assert cols == ["ranger", "assassin"]       # scribed first, no repeats
