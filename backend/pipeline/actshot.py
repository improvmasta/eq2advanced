"""Read an ACT ability-breakdown screenshot back into numbers.

People compare parses by pasting a screenshot of their ACT window into
Discord, so this is the import path for the parses that only ever existed as
an image. It is deliberately NOT a second ground truth: an XML export is what
the parser is validated against (see ARCHITECTURE.md), and a shot read back
off a JPEG is a claim about somebody else's night, kept apart from real
sessions and never folded into anything aggregate.

**The table is read off its own geometry, never off fixed offsets.** ACT's
columns are the reader's — the two fixtures differ (one has `AvgDelay`, the
other doesn't) — so the grid is measured per image: horizontal rules give the
row ladder, and the header band's separator ticks give the columns. A
separator is told apart from header LETTERING by variance down the band rather
than by darkness, because on a rescaled shot the two are equally dark and a
mean-only test reads half the header as columns.

Three properties of a Discord screenshot drive the rest of it:

  * **The pitch is fractional.** Rescaling makes row gaps alternate 17/18px,
    so a fixed pitch walks off the ladder within twenty rows. The rules are
    fitted (best pitch+offset by inlier count, then a least-squares refit),
    which also tolerates the rules that highlighting swallows and the spurious
    ones the pie chart contributes.
  * **The locale is unknown.** `5.612.947` is five million to a German client
    and 5.612 to an American one, and at this font size `.` and `,` differ by
    a couple of pixels. Guessing is not required — see `_pick_locale`.
  * **Some cells cannot be checked.** ACT's table is redundant enough that
    Damage, EncDPS, Average, Hits, Swings and ToHit all cross-check or
    recompute; Median, MinHit, MaxHit and Crit% do not. Unverifiable cells are
    reported as read, and a cell that FAILS a check it was subject to is
    blanked rather than published wrong.

Everything here is a floor on quality, not a guarantee, which is why the
import is labelled wherever it is shown.
"""

from __future__ import annotations

import difflib
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pytesseract
from PIL import Image

# Characters a numeric column may contain. Whitelisting per column is most of
# the accuracy — without it `98.638` comes back as `$3.5338`.
NUM_CHARS = '0123456789,.%-'

# ACT's column headings -> the field we store. The heading is the only thing
# that says what a column IS, so it is read rather than assumed.
COLUMN_ALIASES = {
    'type': 'name', 'name': 'name',
    'damage': 'damage', 'healed': 'healed',
    'encdps': 'dps', 'dps': 'dps', 'enchps': 'hps', 'hps': 'hps',
    'average': 'average', 'median': 'median',
    'minhit': 'min_hit', 'maxhit': 'max_hit',
    'resist': 'resist', 'hits': 'hits', 'swings': 'swings',
    'tohit': 'to_hit', 'avgdelay': 'avg_delay',
    'crit': 'crit_pct', 'crits': 'crit_pct', 'crittypes': 'crit_types',
    'deaths': 'deaths', 'misses': 'misses', 'blocked': 'blocked',
}

# Columns whose value is a whole number: every separator in them is a
# thousands mark, whatever the locale.
INT_FIELDS = {'damage', 'healed', 'median', 'min_hit', 'max_hit',
              'hits', 'swings', 'deaths', 'misses', 'blocked'}
DEC_FIELDS = {'dps', 'hps', 'average', 'to_hit', 'avg_delay', 'crit_pct'}

# The Resist column is a closed vocabulary, so a misread snaps back to it
# rather than reaching the reader as `cisease`.
DAMAGE_TYPES = ('disease', 'poison', 'piercing', 'crushing', 'slashing',
                'heat', 'cold', 'magic', 'mental', 'divine', 'physical',
                'melee', 'all', 'none')


class ShotError(ValueError):
    """The image is not an ACT table we can read."""


@dataclass
class ActShot:
    zone: str | None = None
    encounter: str | None = None
    character: str | None = None
    kind: str | None = None          # 'damage' | 'heal', from the title's view
    duration_s: int | None = None
    when: str | None = None          # as printed; undated shots have none
    title: str = ''
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    total: dict | None = None        # ACT's `All` row, kept out of `rows`
    decimal: str = '.'
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# geometry


def _rules(a):
    """Rows that are a horizontal rule: uniform AND darker than their
    surroundings. Uniformity alone also matches the header's flat background
    bands, and collapsing those together with the rule beneath them threw the
    rule away."""
    H = a.shape[0]
    mean, std = a.mean(axis=(1, 2)), a.std(axis=(1, 2))
    raw = []
    for y in range(H):
        if std[y] >= 16 or not (140 < mean[y] < 252):
            continue
        lo, hi = max(0, y - 10), min(H, y + 11)
        near = sorted(mean[lo:hi])
        if mean[y] < near[len(near) // 2] - 8:
            raw.append(y)
    # a rule is 2px thick once the shot has been rescaled
    return [y for i, y in enumerate(raw) if i == 0 or y - raw[i - 1] > 1]


def _ladder(rules):
    """Fit one evenly-ruled ladder to the rules. Returns (pitch, top, bottom).

    Scored by inlier count over (pitch, offset) rather than chained, because a
    chain breaks on all three things that really happen: a fractional pitch, a
    highlighted row that swallows its rule, and the pie chart below the table
    contributing rules of its own."""
    if len(rules) < 4:
        raise ShotError('no table grid found in this image')
    ys = np.asarray(rules, dtype=float)
    best = (0, None, None)
    for p10 in range(120, 301):
        p = p10 / 10.0
        d = (ys[None, :] - ys[:, None]) / p       # every offset at once
        n_by_off = (np.abs(d - np.round(d)) * p <= 1.5).sum(axis=1)
        i = int(n_by_off.argmax())
        if n_by_off[i] > best[0]:
            best = (int(n_by_off[i]), p, rules[i])
    n, pitch, off = best
    if n < 4:
        raise ShotError('no table grid found in this image')
    inliers = sorted(y for y in rules
                     if abs((y - off) - round((y - off) / pitch) * pitch) <= 1.5)
    # refit on the inliers so the ladder stays true over forty rows
    ks = [round((y - off) / pitch) for y in inliers]
    kbar = sum(ks) / len(ks)
    ybar = sum(inliers) / len(inliers)
    den = sum((k - kbar) ** 2 for k in ks)
    if den:
        pitch = sum((k - kbar) * (y - ybar) for k, y in zip(ks, inliers)) / den
    return pitch, inliers[0], inliers[-1]


def _right_edge(a, top, bottom, W):
    """Trim a vertical scrollbar: a narrow band at the right that is uniform
    all the way down the table."""
    body = a[top:bottom, :, :]
    std = body.std(axis=(0, 2))
    bar = [x for x in range(max(0, W - 30), W) if std[x] < 25]
    return min(bar) if len(bar) > 8 else W - 2


def _header_band(rules, top, pitch):
    """The header sits above the first rule. Its height is NOT the row pitch —
    one fixture's header is 24px against a 17px pitch — so it runs from the
    nearest rule above `top` when there is one."""
    above = [y for y in rules if y < top - 4]
    y0 = above[-1] if above and top - above[-1] < pitch * 2.2 else int(top - pitch)
    return max(0, y0), max(1, top)


def _columns(a, hdr_y0, hdr_y1, right):
    """Column separators, from the header band only.

    A separator is a vertical LINE: darker than the header background and
    near-constant down the band. Header lettering is just as dark but varies
    down the band, which is the whole difference on a rescaled shot."""
    band = a[hdr_y0 + 3:max(hdr_y0 + 6, hdr_y1 - 2), :right, :]
    if band.size == 0:
        raise ShotError('could not locate the table header')
    mean, std = band.mean(axis=(0, 2)), band.std(axis=(0, 2))
    bg = float(np.median(mean))
    seps = [x for x in range(2, right) if std[x] < 3.0 and mean[x] < bg - 12]
    seps = [x for i, x in enumerate(seps) if i == 0 or x - seps[i - 1] > 3]
    if len(seps) < 3:
        raise ShotError('could not find the table columns')
    return seps + [right]


def _flatten(im, a, top, bottom, pitch, right):
    """Greyscale, with any highlighted row rewritten as black-on-white.

    The selected row is white text on blue. Inverting it leaves dark text on
    GREY inside an otherwise white column strip, and one band on a different
    background is enough for tesseract to drop the row entirely."""
    out = np.asarray(im.convert('L')).astype(int).copy()
    hot = []
    for r in range(int(round((bottom - top) / pitch))):
        y0 = top + int(round(r * pitch)) + 1
        y1 = top + int(round((r + 1) * pitch)) - 1
        band = a[y0:y1, :right, :]
        if band.size == 0:
            continue
        if (band.max(axis=2) - band.min(axis=2)).mean() > 40:
            b = out[y0:y1, :right]
            # Text is the BRIGHT minority on a saturated bar, so the cut sits
            # above the mean; splitting at the mean alone kept enough of the
            # highlight to swallow short cells.
            out[y0:y1, :right] = np.where(b > b.mean() + b.std() * 0.6, 0, 255)
            hot.append(r)
    return Image.fromarray(out.astype(np.uint8), 'L'), hot


# --------------------------------------------------------------------------
# ocr


def _ocr(img, charset=None, psm=7):
    cfg = f'--psm {psm}'
    if charset:
        cfg += f' -c tessedit_char_whitelist={charset}'
    return pytesseract.image_to_string(img, config=cfg).strip()


def _up(img, scale):
    return img.resize((img.width * scale, img.height * scale), Image.LANCZOS)


def _read_title(im, hdr_y0):
    """The title bar is the strip above the header. It sits on a window
    gradient, so unlike the body it wants binarizing — that is the difference
    between `8:30:21` and `6:50:21`."""
    # Find the strip by its INK rather than sweeping every offset above the
    # header: the title is the last band of text before the table starts.
    # Sweeping cost ~90 OCR calls, which was most of the runtime.
    a = np.asarray(im.convert('L')).astype(int)[:max(1, hdr_y0), 16:600]
    inked = [y for y in range(a.shape[0]) if a[y].std() > 22]
    bands = []
    for y in inked:
        if bands and y - bands[-1][1] <= 2:
            bands[-1][1] = y
        else:
            bands.append([y, y])
    bands = [b for b in bands if b[1] - b[0] >= 5]
    if not bands:
        return ''
    y_lo, y_hi = bands[-1]

    best, best_score = '', 0
    for pad in (1, 3):
        crop = im.crop((16, max(0, y_lo - pad), min(im.width, 1200),
                        min(hdr_y0, y_hi + pad + 1)))
        if crop.height < 8:
            continue
        g = _up(crop.convert('L'), 5)
        for thr in (140, 155, 172):
            txt = _ocr(g.point(lambda p, t=thr: 255 if p > t else 0))
            txt = re.sub(r'^[^A-Za-z0-9]+', '', txt).strip(' |')
            # Score for title-LIKENESS, never for length: a strip that catches
            # the header lettering as well reads as a long line of noise, and
            # picking the longest candidate chose exactly that.
            score = 0
            if txt.count('|') >= 2:
                score += 2
            if TITLE_TIME.search(txt):
                score += 3
            if re.search(r'\d{1,2}:\d{2}:\d{2}', txt):
                score += 2
            letters = sum(c.isalnum() or c in " :/()[]|-'." for c in txt)
            if txt:
                score += 2 * (letters / len(txt))
            if score > best_score:
                best, best_score = txt, score
    return best if best_score >= 4 else ''


def _read_headers(flat, cols, hdr_y0, hdr_y1):
    out = []
    for i in range(len(cols) - 1):
        crop = flat.crop((cols[i] + 2, hdr_y0 + 1, cols[i + 1] - 1, hdr_y1 - 1))
        out.append(_ocr(_up(crop, 5)) if crop.height > 3 else '')
    return out


def _field_of(label):
    """Map a read heading onto a field. Fuzzy, because the sort arrow prefixes
    the sorted column (`↓EncDPS`) and reads as a letter."""
    key = re.sub(r'[^a-z]', '', label.lower())
    if not key:
        return None
    if key in COLUMN_ALIASES:
        return COLUMN_ALIASES[key]
    for alias in COLUMN_ALIASES:                 # the arrow lands in front
        if key.endswith(alias) and len(alias) >= 4:
            return COLUMN_ALIASES[alias]
    near = difflib.get_close_matches(key, list(COLUMN_ALIASES), n=1, cutoff=0.75)
    return COLUMN_ALIASES[near[0]] if near else None


def _read_cells(flat, cols, fields, top, bottom, pitch, hot):
    """One OCR pass per column strip, so each column can carry its own
    charset; highlighted rows are re-read whole, which they survive far better
    than a binarized band inside a white strip does."""
    cells = defaultdict(str)
    nrows = int(round((bottom - top) / pitch))
    for i in range(len(cols) - 1):
        text_col = fields[i] in (None, 'name', 'resist', 'crit_types')
        strip = flat.crop((cols[i] + 2, top, cols[i + 1] - 1, bottom))
        if strip.width < 3 or strip.height < 3:
            continue
        d = pytesseract.image_to_data(
            _up(strip, 5),
            config='--psm 6' + ('' if text_col else f' -c tessedit_char_whitelist={NUM_CHARS}'),
            output_type=pytesseract.Output.DICT)
        for k, txt in enumerate(d['text']):
            if not txt.strip():
                continue
            r = int(((d['top'][k] + d['height'][k] / 2) / 5) // pitch)
            if r in hot:
                continue                          # re-read below, as a row
            cells[(r, i)] = (cells[(r, i)] + ' ' + txt.strip()).strip()

    # A highlighted row is re-read on its own, and read THREE ways, because no
    # single crop is right: a tight crop clips the leading digit of a
    # right-aligned cell (824 -> 24, 54% -> 5%), a wide one bleeds the
    # neighbour's digits in (1,017.33 -> 64,017.33), and reading the row whole
    # loses column identity where a word box straddles a boundary. The three
    # disagree in different places, so the agreement between them is the
    # answer — see _vote. It is only ever a row or two.
    alts = defaultdict(list)
    for r in hot:
        if not 0 <= r < nrows:
            continue
        y0 = top + int(round(r * pitch)) + 1
        y1 = top + int(round((r + 1) * pitch)) - 1
        for pad_l, pad_r in ((2, -1), (-2, 2)):
            for i in range(len(cols) - 1):
                x0 = max(0, cols[i] + pad_l)
                x1 = min(cols[-1], cols[i + 1] + pad_r)
                crop = flat.crop((x0, y0, x1, y1))
                if crop.width < 3 or crop.height < 3:
                    continue
                text_col = fields[i] in (None, 'name', 'resist', 'crit_types')
                alts[(r, i)].append(_ocr(_up(crop, 5),
                                         None if text_col else NUM_CHARS))
        whole = flat.crop((0, y0, cols[-1], y1))
        if whole.width > 3 and whole.height > 3:
            d = pytesseract.image_to_data(_up(whole, 5), config='--psm 7',
                                          output_type=pytesseract.Output.DICT)
            by_col = defaultdict(list)
            for k, txt in enumerate(d['text']):
                if not txt.strip():
                    continue
                cx = (d['left'][k] + d['width'][k] / 2) / 5
                c = next((j for j in range(len(cols) - 1)
                          if cols[j] <= cx < cols[j + 1]), None)
                if c is not None:
                    by_col[c].append(txt.strip())
            for c, words in by_col.items():
                alts[(r, c)].append(' '.join(words))
    for key, cands in alts.items():
        cells[key] = _vote(cands, fields[key[1]])
    return cells, nrows


def _vote(cands, field):
    """Reconcile several readings of one cell.

    Grouped by DIGITS rather than by string, so `2.57` and `257` count as the
    same reading of the same pixels — they differ only in whether the decimal
    mark survived. Within the winning group a reading that kept its separator
    wins for a column ACT prints with decimals, which is what turns the
    majority's `257` back into AvgDelay 2.57."""
    cands = [c for c in cands if (c or '').strip()]
    if not cands:
        return ''
    if field in (None, 'name', 'resist', 'crit_types'):
        # Majority on the LETTERS, then the shortest — the wide crop and the
        # whole-row pass each pick up the selection's focus rectangle as a
        # stray leading character, and they disagree about which one, so the
        # reading without it is the one they have in common.
        by_letters = defaultdict(list)
        for c in cands:
            by_letters[re.sub(r'[^A-Za-z]', '', c).lower()].append(c)
        key = max(by_letters, key=lambda k: (len(by_letters[k]), -len(k)))
        return min(by_letters[key], key=len)
    groups = defaultdict(list)
    for c in cands:
        groups[re.sub(r'\D', '', c)].append(c)
    digits = max(groups, key=lambda d: (len(groups[d]), len(d)))
    best = groups[digits]
    if field in DEC_FIELDS:
        with_sep = [c for c in best if re.search(r'[.,]\d{1,2}\s*%?$', c)]
        if with_sep:
            return with_sep[0]
    return best[0]


# --------------------------------------------------------------------------
# numbers


def to_number(raw, field, decimal):
    """Read one cell, given the column's type and the shot's decimal mark."""
    s = (raw or '').strip().rstrip('%').strip()
    if not s or not re.search(r'\d', s):
        return None
    if field in INT_FIELDS:
        digits = re.sub(r'\D', '', s)
        return float(digits) if digits else None
    thousands = ',' if decimal == '.' else '.'
    s = s.replace(thousands, '')
    s = s.replace(decimal, '.')
    s = re.sub(r'[^\d.]', '', s)
    if s.count('.') > 1:                       # a stray mark: keep the last
        head, _, tail = s.rpartition('.')
        s = head.replace('.', '') + '.' + tail
    try:
        return float(s)
    except ValueError:
        return None


def _pick_locale(cells, fields, nrows, duration_s):
    """Decide the decimal mark by ARITHMETIC rather than by guessing.

    `Damage / EncDPS` is the encounter duration on every row, so the reading
    that reproduces the duration printed in the title is the right one. With
    no duration to check against, fall back to the shape of the two-decimal
    columns (`98,60` / `100.00`), which is the same evidence a human uses."""
    di = next((i for i, f in enumerate(fields) if f in ('damage', 'healed')), None)
    ri = next((i for i, f in enumerate(fields) if f in ('dps', 'hps')), None)
    if duration_s and di is not None and ri is not None:
        score = {}
        for dec in ('.', ','):
            hits = 0
            for r in range(nrows):
                dmg = to_number(cells.get((r, di)), 'damage', dec)
                rate = to_number(cells.get((r, ri)), 'dps', dec)
                if dmg and rate and abs(dmg / rate - duration_s) <= 1.5:
                    hits += 1
            score[dec] = hits
        if max(score.values()) > 0:
            return max(score, key=score.get)
    tally = {'.': 0, ',': 0}
    for (r, c), v in cells.items():
        if fields[c] in ('to_hit', 'crit_pct', 'dps', 'average'):
            m = re.search(r'([.,])\d{2}$', v or '')
            if m:
                tally[m.group(1)] += 1
    return ',' if tally[','] > tally['.'] else '.'


def _snap_resist(v):
    key = re.sub(r'[^a-z]', '', (v or '').lower())
    if not key:
        return None
    if key in DAMAGE_TYPES:
        return key
    near = difflib.get_close_matches(key, DAMAGE_TYPES, n=1, cutoff=0.7)
    return near[0] if near else (v or None)


def _repair(row, duration_s, notes):
    """Recompute what ACT's own arithmetic determines, and blank what fails.

    Average is Damage/Hits and the rate is Damage/duration, so a dropped
    decimal mark in either repairs itself. A cell that contradicts a check it
    was subject to is dropped: publishing a number we have positive evidence
    is wrong is worse than publishing nothing."""
    dmg = row.get('damage') if row.get('damage') is not None else row.get('healed')
    hits, rate = row.get('hits'), row.get('dps') or row.get('hps')
    rate_key = 'dps' if row.get('dps') is not None else 'hps'

    if dmg and duration_s:
        want = dmg / duration_s
        if rate is None or abs(rate - want) > max(1.0, want * 0.02):
            row[rate_key] = round(want, 2)
            notes.append(f"{row.get('name')}: recomputed {rate_key}")
    if dmg and hits:
        want = dmg / hits
        if row.get('average') is None or abs(row['average'] - want) > max(1.0, want * 0.02):
            row['average'] = round(want, 2)
    if hits and row.get('swings'):
        row['to_hit'] = round(hits / row['swings'] * 100, 2)

    lo, med, hi = row.get('min_hit'), row.get('median'), row.get('max_hit')
    if lo is not None and hi is not None and lo > hi:
        row['min_hit'] = row['max_hit'] = None
        notes.append(f"{row.get('name')}: dropped MinHit/MaxHit (inconsistent)")
    elif med is not None and lo is not None and hi is not None and not (lo <= med <= hi):
        row['median'] = None
        notes.append(f"{row.get('name')}: dropped Median (outside Min..Max)")
    if row.get('crit_pct') is not None and not 0 <= row['crit_pct'] <= 100:
        row['crit_pct'] = None
    return row


# --------------------------------------------------------------------------
# title


TITLE_TIME = re.compile(r'\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]')


# ACT's own window icon sits left of the title and reads as a couple of stray
# characters — `ga |`, `gm)`. Left alone it becomes an extra pipe segment and
# shifts the zone, encounter and character each one place along.
TITLE_ICON = re.compile(r"^[^A-Za-z0-9]*[A-Za-z0-9]{0,3}\s*[)\]}|]\s*")

# The same artifact prefixes a highlighted row's name cell (`S Soulrot`). Only
# a lone capital or punctuation is stripped: `a maven of wisdom` is a real mob
# name that starts with a one-letter lowercase word.
ROW_ARTIFACT = re.compile(r"^(?:[^\w\s]{1,2}|[A-Z])\s+(?=[A-Za-z(])")


def _clean_name(raw, highlighted):
    name = (raw or '').strip().strip('|[]').strip()
    if highlighted:
        name = ROW_ARTIFACT.sub('', name, count=1)
    return name.strip()


def parse_title(title):
    """`Zone - [n] time | Encounter - [mm:ss] (date) time | Character | View`

    Tolerant on purpose: the two fixtures differ in whether a date is printed
    and in 12- vs 24-hour clocks, so anything past the encounter's duration is
    optional. Only the pipe structure is relied on."""
    out = {}
    parts = [p.strip() for p in TITLE_ICON.sub('', title or '', count=1).split('|')]
    if parts:
        out['zone'] = re.sub(r'\s*-\s*\[\d+\].*$', '', parts[0]).strip() or None
    if len(parts) >= 2:
        seg = parts[1]
        out['encounter'] = re.sub(r'\s*-\s*\[.*$', '', seg).strip() or None
        m = TITLE_TIME.search(seg)
        if m:
            h, mm, ss = m.groups()
            out['duration_s'] = (int(h or 0) * 3600) + int(mm) * 60 + int(ss)
        d = re.search(r'\((\d{1,2}/\d{1,2}/\d{2,4})\)', seg)
        if d:
            out['when'] = d.group(1)
    if len(parts) >= 3 and re.fullmatch(r"[A-Za-z][A-Za-z'`-]{1,23}", parts[2]):
        out['character'] = parts[2]
    view = parts[-1].lower() if len(parts) >= 4 else ''
    if 'heal' in view:
        out['kind'] = 'heal'
    elif 'damage' in view:
        out['kind'] = 'damage'
    return out


# --------------------------------------------------------------------------


def extract(data):
    """Read an ACT breakdown screenshot. `data` is bytes or a PIL Image."""
    im = data if isinstance(data, Image.Image) else Image.open(io.BytesIO(data))
    im = im.convert('RGB')
    a = np.asarray(im).astype(int)
    W, H = im.size
    if W < 200 or H < 60:
        raise ShotError('image is too small to be an ACT table')

    rules = _rules(a)
    pitch, top, bottom = _ladder(rules)
    right = _right_edge(a, top, bottom, W)
    hdr_y0, hdr_y1 = _header_band(rules, top, pitch)
    cols = _columns(a, hdr_y0, hdr_y1, right)
    flat, hot = _flatten(im, a, top, bottom, pitch, right)

    title = _read_title(im, hdr_y0)
    meta = parse_title(title)

    labels = _read_headers(flat, cols, hdr_y0, hdr_y1)
    fields = [_field_of(l) for l in labels]
    if 'name' not in fields:
        fields[0] = 'name'                     # the first column always is
    if not any(f in ('damage', 'healed') for f in fields):
        raise ShotError('no Damage or Healed column in this table')

    cells, nrows = _read_cells(flat, cols, fields, top, bottom, pitch, hot)
    decimal = _pick_locale(cells, fields, nrows, meta.get('duration_s'))

    shot = ActShot(title=title, decimal=decimal,
                   columns=[f for f in fields if f],
                   **{k: v for k, v in meta.items()})
    for r in range(nrows):
        name = _clean_name(cells.get((r, 0)), r in hot)
        # the last band is clipped by whatever sits under the table
        if not name or not re.search(r'[A-Za-z]', name):
            continue
        row = {'name': name}
        for i in range(1, len(cols) - 1):
            f = fields[i]
            if not f or f == 'name':
                continue
            raw = cells.get((r, i))
            if f == 'resist':
                row[f] = _snap_resist(raw)
            elif f == 'crit_types':
                continue                        # decorative, and never read cleanly
            else:
                row[f] = to_number(raw, f, decimal)
        _repair(row, shot.duration_s, shot.notes)
        if name.lower() == 'all':
            shot.total = row                    # ACT's totals line is not an ability
        else:
            shot.rows.append(row)

    if not shot.rows:
        raise ShotError('no ability rows could be read from this image')
    return shot
