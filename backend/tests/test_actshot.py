"""Reading an ACT screenshot back into numbers.

The two fixtures are deliberately unalike, and each one is the only evidence
for a whole class of behaviour:

  emerald-halls-bobby      a crisp 1422px shot, US locale, 14 columns
                           (it has AvgDelay), a dated 12-hour title
  freethinker-zylphax-asame  898px off Discord, GERMAN locale (`5.612.947` is
                           five million), 13 columns (no AvgDelay), an
                           undated 24-hour title, and a scrollbar

Between them they pin down that nothing is hardcoded: not the column set, not
the decimal mark, not the row pitch, not the title grammar. Assertions are on
values transcribed from the images by eye.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.actshot import ShotError, extract, parse_title, to_number  # noqa: E402

SHOTS = Path(__file__).parent / 'fixtures' / 'act_shots'
pytesseract = pytest.importorskip('pytesseract')


@pytest.fixture(scope='module')
def bobby():
    return extract((SHOTS / 'emerald-halls-bobby.png').read_bytes())


@pytest.fixture(scope='module')
def asame():
    return extract((SHOTS / 'freethinker-zylphax-asame.png').read_bytes())


# --- the title bar ---------------------------------------------------------

def test_title_dated_12_hour(bobby):
    assert bobby.zone == 'The Emerald Halls'
    assert bobby.encounter == 'Galiel Spirithoof'
    assert bobby.character == 'Bobby'
    assert bobby.duration_s == 391          # [06:31]
    assert bobby.when == '8/4/2026'
    assert bobby.kind == 'damage'


def test_title_undated_24_hour(asame):
    """The Discord shot prints no date and a 24-hour clock, so anything past
    the duration has to be optional rather than a fixed regex."""
    assert asame.zone == 'Freethinker Hideout'
    assert asame.character == 'Asame'
    assert asame.duration_s == 170          # [02:50]
    assert asame.when is None
    assert asame.kind == 'damage'


def test_parse_title_tolerates_missing_tail():
    t = parse_title('Zone Name - [3] 20:53:23 | Some Boss - [01:05] 20:54:49')
    assert t['zone'] == 'Zone Name'
    assert t['encounter'] == 'Some Boss'
    assert t['duration_s'] == 65
    assert 'character' not in t


def test_parse_title_strips_the_window_icon():
    """ACT's icon reads as a stray token and used to shift every field one
    place along, making the zone `ga` and the encounter the zone."""
    t = parse_title('ga | The Emerald Halls - [22] 8:30:21 PM | Boss - [00:30] '
                    '(8/4/2026) 8:40:26 PM | Bobby | Outgoing Damage')
    assert t['zone'] == 'The Emerald Halls'
    assert t['encounter'] == 'Boss'
    assert t['character'] == 'Bobby'


def test_healing_view_is_a_different_kind():
    assert parse_title('Z - [1] 1 | E - [01:00] 1 | Who | Healed')['kind'] == 'heal'


# --- the grid --------------------------------------------------------------

def test_column_set_is_read_not_assumed(bobby, asame):
    """One fixture has AvgDelay and the other doesn't. A fixed column list
    would silently shift every value in the second one."""
    assert 'avg_delay' in bobby.columns
    assert 'avg_delay' not in asame.columns
    for shot in (bobby, asame):
        for f in ('name', 'damage', 'dps', 'hits', 'crit_pct'):
            assert f in shot.columns


def test_the_pie_chart_is_not_read_as_rows(bobby, asame):
    """Both shots carry a pie chart with a legend under the table. Its labels
    are ability names, so a row ladder that runs past the table's bottom edge
    reads them as parse rows."""
    for shot in (bobby, asame):
        assert all(r.get('damage') for r in shot.rows), \
            'a legend entry has no Damage and would come through empty'
    assert len(bobby.rows) == 43
    assert len(asame.rows) == 26


def test_all_row_is_the_total_not_an_ability(bobby):
    assert bobby.total is not None
    assert bobby.total['damage'] == 5386632
    assert not any(r['name'].lower() == 'all' for r in bobby.rows)


def test_highlighted_row_survives(bobby):
    """The selected row is white-on-blue; greyscaling it loses the row
    entirely, and reading it back needs its own pass."""
    soulrot = next(r for r in bobby.rows if r['name'] == 'Soulrot')
    assert soulrot['damage'] == 397775
    assert soulrot['dps'] == pytest.approx(1017.33, abs=0.01)
    assert soulrot['hits'] == 284
    assert soulrot['crit_pct'] == 54
    assert soulrot['avg_delay'] == pytest.approx(2.57, abs=0.01)


def test_scrollbar_is_not_a_column(asame):
    assert asame.columns[-1] in ('crit_pct', 'crit_types', 'to_hit')


# --- locale ----------------------------------------------------------------

def test_locale_is_decided_by_arithmetic(bobby, asame):
    """`5.612.947` is five million in one locale and 5.612 in the other, and
    the two marks are a couple of pixels apart at this size. Damage/EncDPS is
    the duration in the title, so the reading that reproduces it is the right
    one — no guessing and no user setting."""
    assert bobby.decimal == '.'
    assert asame.decimal == ','
    assert asame.total['damage'] == 5612947
    assert asame.total['dps'] == pytest.approx(33017.34, abs=0.01)


def test_to_number_respects_the_column_type():
    # every separator in a whole-number column is a thousands mark
    assert to_number('5.612.947', 'damage', ',') == 5612947
    assert to_number('5,612,947', 'damage', '.') == 5612947
    # in a decimal column the shot's mark decides
    assert to_number('33.017,34', 'dps', ',') == pytest.approx(33017.34)
    assert to_number('13,776.55', 'dps', '.') == pytest.approx(13776.55)
    assert to_number('', 'damage', '.') is None
    assert to_number('NaN', 'avg_delay', '.') is None


# --- what the numbers have to agree with -----------------------------------

def test_every_row_reproduces_the_encounter_duration(bobby, asame):
    """ACT's table is redundant: Damage/EncDPS is the fight length on every
    row. This is the audit that makes an OCR'd parse worth showing at all."""
    for shot in (bobby, asame):
        for row in shot.rows:
            dmg, dps = row.get('damage'), row.get('dps')
            assert dmg and dps
            assert dps == pytest.approx(dmg / shot.duration_s, rel=0.02), row['name']


def test_average_is_repaired_from_damage_and_hits(asame):
    """A dropped decimal mark turns 9.241,15 into 924115; Average is
    Damage/Hits, so it repairs itself."""
    for row in asame.rows:
        if row.get('hits') and row.get('average'):
            assert row['average'] == pytest.approx(row['damage'] / row['hits'], rel=0.02)


def test_min_median_max_are_ordered_or_dropped(bobby, asame):
    """These three are among the columns nothing can verify, so the one check
    available to them is their own ordering — and a cell that fails it is
    blanked rather than published wrong."""
    for shot in (bobby, asame):
        for row in shot.rows:
            lo, med, hi = row.get('min_hit'), row.get('median'), row.get('max_hit')
            if lo is not None and hi is not None:
                assert lo <= hi, row['name']
            if None not in (lo, med, hi):
                assert lo <= med <= hi, row['name']


def test_crit_is_a_percentage(bobby, asame):
    for shot in (bobby, asame):
        for row in shot.rows:
            if row.get('crit_pct') is not None:
                assert 0 <= row['crit_pct'] <= 100


def test_resist_snaps_to_a_damage_type(bobby, asame):
    """A closed vocabulary, so `cisease` goes back to `disease` instead of
    reaching the reader."""
    known = {'disease', 'poison', 'piercing', 'crushing', 'slashing', 'heat',
             'cold', 'magic', 'mental', 'divine', 'physical', 'melee', 'all', 'none'}
    for shot in (bobby, asame):
        seen = {r['resist'] for r in shot.rows if r.get('resist')}
        assert seen and seen <= known, seen


def test_ability_names_read_cleanly(bobby):
    names = {r['name'] for r in bobby.rows}
    for expected in ("blighted horde's Grave Decay", 'Shadowy Garrote',
                     'Lifeburn', 'Poisoned Spike', "Theurgist's Detonation"):
        assert expected in names, sorted(names)


# --- failure ---------------------------------------------------------------

def test_a_non_table_image_is_refused():
    from PIL import Image
    with pytest.raises(ShotError):
        extract(Image.new('RGB', (400, 300), 'white'))


def test_a_tiny_image_is_refused():
    from PIL import Image
    with pytest.raises(ShotError):
        extract(Image.new('RGB', (40, 20), 'white'))
