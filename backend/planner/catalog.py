"""The read side: an era-filtered catalog, ranked against a declared ORDER.

Everything here is a read of rows a hand-run crawl already wrote. No network,
no Census call, no `items.ensure` — a request handler must never do any of
those (`docs/sharing.md`), and the catalog is a few thousand rows, so the whole
page is served out of SQLite and a dict.

**WHICH EXPANSIONS COUNT IS THE READER'S CHOICE.** EoF and RoK, either or both.
The filter reads `plan_sources.era`, not `plan_items.era`: an item introduced in
EoF that also drops off a RoK named IS RoK content for somebody planning RoK,
and filing it by where it first appeared would hide it from the only reader
asking about it.

**The priority list is an ORDER, not a set of numbers, and no weight is ever
surfaced.** You say "ability mod, then reuse, then casting speed"; the ranks
map to a decaying weight in here and that number does not leave this module.
There is deliberately no cap math, no diminishing-returns curve and no set
optimizer: this tool presents ranked options and the reader chooses, and
inventing precision the model does not have would be worse than not having it
(docs/planner.md). A set optimizer is also wrong on its own terms, because the
most valuable part of a piece of armour — the turquoise — detaches and moves.
"""

import json
import random

from planner import wiki

# Rank -> weight. Geometric, so first place is worth about three times third
# and the tail never quite reaches zero: a stat you listed fifth still breaks a
# tie between two items that agree on the first four. The exact base is not
# meaningful and is never shown — what a reader stated is an ORDER, and this is
# only the arithmetic that turns an order into a sort.
DECAY = 0.6

# Set pages mix typed-looking additive bonuses with named effects.  Parse only
# the former, and only when the whole sentence is the number plus a known
# label.  "Applies Focus: ..." remains prose: turning a named spell effect into
# arithmetic would be the false precision the planner otherwise avoids.
_SET_STAT_LABELS = {
    "potency": "potency", "crit chance": "crit",
    "ability modifier": "abmod", "ability mod": "abmod", "all": "abmod",
    "casting speed": "acspeed", "ability doublecast": "abdblcast",
    "flurry": "flurry",
    "wis": "wis", "sta": "sta",
    "in-combat health regeneration": "hregen",
    "health": "health", "power": "power",
}


def normalize_set_bonus_line(text: str) -> str:
    """Use the in-game label for the wiki's legacy ``All`` modifier."""
    import re
    return re.sub(r"(?i)([+-]?\d+(?:\.\d+)?)\s+All\s*$",
                  r"\1 Ability Mod", text or "")


def normalize_set_bonuses(bonuses: list[dict], *, typed: bool = False) -> list[dict]:
    """Copy set tiers with their legacy labels normalized for every surface."""
    out = []
    for source in bonuses:
        bonus = dict(source)
        bonus["text"] = normalize_set_bonus_line(bonus.get("text") or "")
        # Some wiki set pages repeat a bare stat line verbatim. It is one
        # threshold, not two grants; preserve order while refusing the exact
        # duplicate even when Census enrichment is temporarily unavailable.
        bonus["stat_lines"] = list(dict.fromkeys(
            normalize_set_bonus_line(line)
            for line in bonus.get("stat_lines") or []))
        if typed:
            bonus["stats"] = bonus_stats(bonus)
        out.append(bonus)
    return out


def set_bonus_stats(text: str) -> dict[str, float]:
    """Conservatively type one additive set-bonus line.

    A line may name SEVERAL stats — the game draws a tier as "4 Potency, 100
    Ability Mod, 5 Crit Chance" — so a comma list is read when EVERY segment
    types. Fail closed on the whole line if any of them does not: half of a
    sentence typed as arithmetic is worse than none of it, which is the same
    call the single-value form was already making."""
    import re
    body = (text or "").strip().strip("|")
    parts = [p for p in body.split(",") if p.strip()] if "," in body else [body]
    out: dict[str, float] = {}
    for part in parts:
        match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s+(.+?)\s*", part)
        key = _SET_STAT_LABELS.get(
            match.group(2).strip().lower()) if match else None
        if not key:
            return {}
        out[key] = out.get(key, 0.0) + float(match.group(1))
    return out


def bonus_stats(bonus: dict) -> dict[str, float]:
    """Every additive stat one set TIER grants.

    The page writes them one per bare line under the tier (`wiki._BONUS_TIER`),
    so this is the sum over those; the headline is read too, for the tiers that
    put a stat on the `(N)` line itself and for rows stored before the block
    parser existed."""
    lines = [bonus.get("text") or "", *(bonus.get("stat_lines") or [])]
    out: dict[str, float] = {}
    for line in lines:
        for key, value in set_bonus_stats(line).items():
            out[key] = out.get(key, 0.0) + value
    return out


def weights(order: list[str]) -> dict[str, float]:
    """Only `wiki.PRIORITY_STATS` rank, whatever a hand-built URL asks for.

    Potency and Crit Chance are on 80% and 72% of the catalog, so ordering by
    them orders by nothing — and a query string must not be a way around a rule
    that exists because the answer would be meaningless, not because the page
    is being tidy. They are still on the card and still available as columns."""
    return {stat: DECAY ** i for i, stat in enumerate(order)
            if stat in wiki.PRIORITY_STATS}


def _era_clause(eras: list[str]) -> tuple[str, list]:
    keys = [e for e in eras if e in wiki.ERAS] or list(wiki.DEFAULT_ERAS)
    ph = ",".join("?" * len(keys))
    return (f"EXISTS (SELECT 1 FROM plan_sources s WHERE s.page_title = "
            f"i.page_title AND s.era IN ({ph}))", keys)


def _rows(conn, eras: list[str]) -> list[dict]:
    where, params = _era_clause(eras)
    out = []
    for r in conn.execute(f"SELECT * FROM plan_items i WHERE {where}", params):
        row = dict(r)
        # Stored crawls may predate Primary Attributes normalization. Apply it
        # on read as well as ingest so the existing catalog is corrected now,
        # without requiring a destructive re-crawl.
        row["stats"] = wiki.expand_primary_attributes(
            json.loads(row.pop("stats_json") or "{}"))
        row["adorns"] = json.loads(row.pop("adorns_json") or "{}")
        row["classes"] = [c for c in (row["classes"] or "").split(",") if c]
        row["tradeskill_classes"] = [
            c for c in (row.get("tradeskill_classes") or "").split(",") if c]
        # Lifted out of `dtype` rather than stored beside it — one string op
        # per row, and no migration or re-crawl to start filtering on it.
        row["armor"] = wiki.armor_of(row["dtype"])
        # Same treatment for the rarity: the wiki's eleven `icat` spellings
        # collapse to the five-rung ladder a player names (`wiki.tier_bucket`).
        # `tier` stays exactly as crawled — the card and the colour still read
        # it — and the bucket is only what the filter offers.
        row["tier_bucket"] = wiki.tier_bucket(row["tier"])
        row["two_handed"] = wiki.is_two_handed(row["dtype"])
        # The naming decision lives here rather than in the table, so anything
        # else that shows a slot says the same thing about a two-hander.
        row["slot_label"] = wiki.slot_label(row["slot"], row["dtype"])
        out.append(row)
    return out


def _sources(conn, pages: list[str]) -> dict[str, list[dict]]:
    if not pages:
        return {}
    out: dict[str, list[dict]] = {}
    for i in range(0, len(pages), 500):
        chunk = pages[i:i + 500]
        for r in conn.execute(
                "SELECT page_title, source, source_page, kind, era, zone, level, "
                f"detail FROM plan_sources WHERE page_title IN ({','.join('?' * len(chunk))})",
                chunk):
            out.setdefault(r["page_title"], []).append(dict(r))
    # A raid drop and a solo quest reward are both true; the raid one is listed
    # first because it is the harder claim and the one a reader is deciding on.
    # A world drop sorts last: naming the zone is the least that can be said
    # about where something came from, and it is only ever the answer when
    # nothing better exists.
    order = {"raid": 0, "group": 1, "quest": 2, "solo": 3, "unknown": 4,
             "zone": 5}
    for rows in out.values():
        rows.sort(key=lambda s: (order.get(s["kind"], 9), s["source"]))
    return out


def scales(rows: list[dict]) -> dict[str, float]:
    """The largest value each stat reaches in the selected eras.

    Computed over the whole era catalog and NOT over the filtered view, so
    narrowing to one slot does not silently rescore every row. A score is only
    useful if it means the same thing after you press a filter."""
    out: dict[str, float] = {}
    for row in rows:
        for stat, value in row["stats"].items():
            if value > out.get(stat, 0):
                out[stat] = value
    return out


def score(stats: dict[str, float], w: dict[str, float],
          scale: dict[str, float]) -> float:
    """0–100 against the reader's order. Absence is zero, not a penalty.

    Each stat is divided by the biggest that stat gets in this era before it is
    weighted, because 3.7 Potency and 98 Ability Mod are both "a lot" and a raw
    weighted sum would rank on nothing but which stat happens to use bigger
    numbers."""
    if not w:
        return 0.0
    total = sum(w.values())
    got = sum(weight * min(stats.get(stat, 0) / scale[stat], 1.0)
              for stat, weight in w.items() if scale.get(stat))
    return round(100 * got / total, 1)


# HOW MANY OF YOUR PRIORITIES AN ITEM HAS TO ACTUALLY CARRY.
#
# EoF/RoK gear is four-stat: potency and crit, which everything has, plus two
# more. So an item can carry at most about two of whatever you listed, and a
# reader who names three stats is not asking to see everything with one of
# them — measured on the built catalog, 45% of items carry NO more than one
# priority stat, and those are the rows that were burying the list.
#
# Two is therefore the floor and also the ceiling worth asking for: naming a
# fourth and fifth stat cannot make an item carry four. Below two the reader
# named one stat and means it.
FOUR_STAT_FLOOR = 2


def default_match_min(order: list[str]) -> int:
    n = len(weights(order or []))
    return min(FOUR_STAT_FLOOR, n)


def search(conn, *, eras: list[str], order: list[str] | None = None,
           required: list[str] | None = None, classes: list[str] | None = None,
           slots: list[str] | None = None, tiers: list[str] | None = None,
           kinds: list[str] | None = None, armor: list[str] | None = None,
           level_min: int | None = None, level_max: int | None = None,
           q: str | None = None, carries_set: bool = False,
           hosts_turquoise: bool = False, has_proc: bool = False,
           match_min: int | None = None, limit: int = 400,
           sample: int | None = None) -> dict:
    """The item table. Filters are HARD and ranking is separate from them.

    `required` is the one control that crosses the line on purpose: a stat can
    be marked required, which moves it from ranking to filtering. That covers
    "I will not look at anything without ability mod" without pretending a
    weight can express a hard requirement.

    `match_min` is the other, and it is a filter about the ORDER as a whole
    rather than about one stat: how many of your priorities a row has to carry
    before it is worth looking at (`FOUR_STAT_FLOOR`). It is answered back so
    the page can say "2 of 3" rather than silently dropping rows."""
    rows = _rows(conn, eras)
    scale = scales(rows)
    w = weights(order or [])
    floor = default_match_min(order or []) if match_min is None else match_min
    floor = max(0, min(floor, len(w)))

    want_classes = {c for c in (classes or []) if c}
    want_armor = {a.title() for a in (armor or []) if a}
    want_slots = {s.lower() for s in (slots or []) if s}
    # A shield occupies the character window's Secondary position even though
    # EquipInformation calls its item category `Shield`. Clicking that concrete
    # slot must therefore find both off-hand items and shields.
    if "secondary" in want_slots:
        want_slots.add("shield")
    # A tier is asked for by BUCKET (`wiki.TIER_BUCKETS`), and the raw crawled
    # spelling is still accepted so an older link keeps working.
    want_tiers = {t.strip().lower() for t in (tiers or []) if t}
    want_kinds = {k for k in (kinds or []) if k}
    need = [s for s in (required or []) if s in wiki.STAT_LABEL]
    needle = (q or "").strip().lower()

    sources = _sources(conn, [r["page_title"] for r in rows])
    kept = []
    before_priorities = 0
    for row in rows:
        if want_classes and not want_classes & set(row["classes"]):
            continue
        row_slots = {(row["slot"] or "").lower(),
                     (row["slot2"] or "").lower()}
        if row["slot_label"]:
            # Display distinctions are valid facet values too. Keep the raw
            # Primary value so its existing broad meaning still includes both
            # one- and two-handers; Primary/2H is the narrower choice.
            row_slots.add(row["slot_label"].lower())
        if want_slots and not row_slots & want_slots:
            continue
        if want_tiers and not want_tiers & {row["tier_bucket"] or "",
                                            (row["tier"] or "").lower()}:
            continue
        if want_armor and row["armor"] not in want_armor:
            continue
        if level_min is not None and (row["level"] or 0) < level_min:
            continue
        if level_max is not None and (row["level"] or 0) > level_max:
            continue
        if needle and needle not in row["name"].lower():
            continue
        if carries_set and not row["set_name"]:
            continue
        if hosts_turquoise and not row["adorns"].get("turquoise"):
            continue
        if has_proc and not row["effects"]:
            continue
        row["sources"] = sources.get(row["page_title"], [])
        if want_kinds and not want_kinds & {s["kind"] for s in row["sources"]}:
            continue
        # EVERY FILTER BUT THE STAT ONES HAS NOW RUN, and the count is kept:
        # an empty table has two completely different causes and the reader
        # cannot tell them apart from nothing. "No Head item at level 70 in
        # this expansion" and "46 of them, none carrying Reuse Speed" are the
        # same blank table and lead to opposite next moves.
        before_priorities += 1
        if any(not row["stats"].get(s) for s in need):
            continue
        # The four-stat floor. Counted over the stats that actually RANK, so a
        # hand-built order carrying potency does not let a row in on it.
        row["matched"] = sum(1 for s in w if row["stats"].get(s))
        if floor and row["matched"] < floor:
            continue
        row["score"] = score(row["stats"], w, scale)
        kept.append(row)

    # HOW MANY OF YOUR STATS A ROW CARRIES ORDERS THE TABLE BEFORE ITS SCORE
    # DOES. Naming three stats asks for the items that have all three, and
    # there are usually a handful or none — but a two-stat item with large
    # numbers outscores a three-stat item with modest ones, so a pure score
    # sort buried the exact rows the third choice was made to find. Tiering
    # instead of filtering keeps the promise both ways: the complete matches
    # lead, the partial ones follow in score order under them, and nothing is
    # hidden for being one stat short.
    #
    # It also decides which rows survive `limit`, which a client-side sort
    # cannot: a full match ranked 250th would never have been sent.
    kept.sort(key=lambda r: (-r["matched"], -r["score"], -(r["level"] or 0),
                             r["name"]))
    set_names = sorted({r["set_name"] for r in kept if r.get("set_name")})
    set_hover = {}
    if set_names:
        for set_row in conn.execute(
                "SELECT name, pieces_json, bonuses_json FROM plan_sets WHERE name IN "
                f"({','.join('?' * len(set_names))})", set_names):
            set_hover[set_row["name"]] = {
                "bonuses": normalize_set_bonuses(
                    json.loads(set_row["bonuses_json"] or "[]")),
                "total": len(json.loads(set_row["pieces_json"] or "[]")),
            }
    sampled = random.sample(kept, min(sample, len(kept))) if sample else None
    returned = sampled if sampled is not None else kept[:limit]
    for row in returned:
        hovered = set_hover.get(row.get("set_name"), {})
        row["_set_bonuses"] = hovered.get("bonuses", [])
        row["_set_total"] = hovered.get("total")
    return {
        "total": len(kept),
        "items": [_item_out(r) for r in returned],
        "sampled": sampled is not None,
        "scored": bool(w),
        # Answered back rather than assumed: the page shows "2 of 3" beside the
        # table, because a filter that quietly removes half the catalog has to
        # say so.
        "match_min": floor,
        "ranked": list(w),
        # THE CATALOG IS A CRAWL AND IT IS NOT THE GAME. It holds what an
        # expansion's zones, nameds and quests could be walked to; a blank
        # table is a statement about this table and never about EverQuest II,
        # and the page has to be able to say which of its own controls emptied
        # it before it implies the item does not exist.
        "before_priorities": before_priorities,
        "catalog": len(rows),
    }


# The examine window's two blocks, in the order the game draws them. The blue
# block is the throughput stats a raider sorts on; the white block above it is
# attributes, resistances and the defensive numbers. Same split `items.py`
# makes from Census's `type` field — made here from the field name instead,
# because the wiki has no type on a field and the answer is fixed anyway.
#
# **ABILITY MOD IS LAST**, which is where the game puts it — game knowledge,
# from Lindsay, and the same order `items.EFFECT_LAST` keeps so the two examine
# cards agree. It is one of the two numbers a raider compares items on and is
# still not what the window leads with.
_BLUE = ("potency", "crit", "multi", "dps", "aspeed", "acspeed",
         "arspeed", "flurry", "aeauto", "dblcast", "abdblcast", "strike",
         "bchance", "maxhealth", "mitinc", "accuracy", "abmod")

# The green block is read ACROSS the game's two columns: attributes first,
# then resistances, then skills/defences.  Together with the grid in
# `ItemCard.jsx`, `[Primary, Stamina, Resist, Combat Skills]` becomes the same
# two rows the client draws instead of putting both attributes down the left.
_GREEN = ("Primary Attributes", "Stamina", "Elemental Resist", "Arcane Resist",
          "Noxious Resist", "Resistances", "Combat Skills", "Mitigation",
          "Protection")


def card(row: dict) -> dict:
    """A catalog row in `items.display`'s shape, so the EXISTING examine card
    draws it unchanged (`components/ItemCard.jsx`).

    There are now three ways to meet an item — a chest drop, a link somebody
    posted in Auction, and a row on this page — and all three have to produce
    the SAME window. `items.py` owns that shape for the two that come from
    Census; this is the third source speaking the same contract rather than a
    fourth card drawn slightly differently.

    The picture is `items.icon_path`'s, cached by the ingest, and is offered
    only when the file is actually on disk: a card with a broken image in it is
    worse than a card with no image."""
    from items import icon_path
    stats, effects = [], []
    primary = [row["stats"].get(key) for key in wiki.PRIMARY_ATTRIBUTE_KEYS
               if row["stats"].get(key)]
    if primary:
        stats.append({"name": "Primary Attributes", "value": max(primary),
                      "pct": False})
    resist_keys = ("vselemental", "vsarcane", "vsnoxious")
    resist_values = [row["stats"].get(key) for key in resist_keys
                     if row["stats"].get(key)]
    collapse_resists = bool(resist_values) and len(set(resist_values)) == 1
    if collapse_resists:
        # Census and the TLE client call the lone/equal AC field Resistances.
        # The wiki stores Earring of the Solstice's 360 in `vselemental`, but
        # the in-game examine does not call it Elemental Resist.
        stats.append({"name": "Resistances", "value": resist_values[0],
                      "pct": False})
    for key, value in row["stats"].items():
        if key in wiki.PRIMARY_ATTRIBUTE_KEYS or collapse_resists and key in resist_keys:
            continue
        line = {"name": wiki.STAT_LABEL.get(key, key), "value": value,
                "pct": key in wiki.STAT_PCT}
        (effects if key in _BLUE else stats).append(line)
    effects.sort(key=lambda r: _BLUE.index(_key_of(r["name"])))
    stats.sort(key=lambda r: (
        _GREEN.index(r["name"]) if r["name"] in _GREEN else len(_GREEN),
        -float(r["value"]), r["name"]))
    icon = row["icon"] if row["icon"] and icon_path(row["icon"]).exists() else None
    names = wiki.effect_names(row["effects"])
    included = None
    if row.get("set_name"):
        included = {
            "name": row["set_name"], "color": "turquoise", "predicate": None,
            "flags": [], "requires_equip": True,
            "total": row.get("_set_total"),
            # THE TIER LINE IS THE STATS, the way the game draws it — "(6) 4
            # Potency, 100 Ability Mod, 5 Crit Chance" — with the proc and its
            # explanation as the bullets under it. A tier whose own line is
            # empty is still a tier; dropping it on a falsy `text` is what hid
            # the largest bonus on the set.
            "set_bonuses": [{
                "required": bonus.get("pieces"),
                "stat_lines": bonus.get("stat_lines") or [],
                "effect": bonus.get("text", "").replace("|", "").strip() or None,
                "descriptions": [line for line in bonus.get("detail") or [] if line],
            } for bonus in row.get("_set_bonuses", [])
                if bonus.get("text") or bonus.get("stat_lines")],
        }
    return {
        "name": row["name"],
        "rarity": (row["tier"] or "").title() or None,
        "description": row.get("description"),
        "icon": icon,
        "wiki": f"https://eq2.fandom.com/wiki/{row['page_title'].replace(' ', '_')}",
        "type": row.get("dtype") or row.get("wtype"),
        "slot": row.get("slot_label") or row.get("slot"),
        "level": row.get("level"),
        # WHO CAN WEAR IT, which is the one property that rules an item out
        # before any number on it matters. Silence when it is not a restriction
        # — see `wiki.class_restriction`, which the loot card asks too.
        "classes": wiki.class_restriction(row.get("classes")),
        "tradeskill_classes": [c.title() for c in
                               row.get("tradeskill_classes") or []] or None,
        "stats": {
            "stats": stats, "effects": effects,
            "flags": [f.strip().title() for f in (row["flags"] or "").split()
                      if f.strip()],
            "adornments": [c for c in wiki.ADORN_COLORS
                           for _ in range(row["adorns"].get(c) or 0)],
            "included_adornment": included,
        },
        "effects": {"names": names,
                    "desc": wiki.effect_lines(row["effect_desc"]),
                    "set": row["set_name"]},
    }


_LABEL_KEY = {label: key for key, label in wiki.STAT_LABEL.items()}


def _key_of(label: str) -> str:
    return _LABEL_KEY.get(label, label)


def _item_out(row: dict) -> dict:
    return {
        "card": card(row),
        "page_title": row["page_title"], "name": row["name"],
        "census_id": row["census_id"], "era": row["era"],
        "slot": row["slot"], "slot2": row["slot2"], "level": row["level"],
        "slot_label": row["slot_label"], "two_handed": row["two_handed"],
        "tier": row["tier"], "dtype": row["dtype"], "wtype": row["wtype"],
        "classes": row["classes"],
        "tradeskill_classes": row.get("tradeskill_classes", []),
        "flags": row["flags"],
        "armor": row["armor"],
        "adorns": row["adorns"], "set_name": row["set_name"],
        "stats": row["stats"], "description": row.get("description"),
        "effects": row["effects"],
        "effect_desc": row["effect_desc"], "icon": row["icon"],
        "score": row.get("score", 0.0), "matched": row.get("matched", 0),
        "tier_bucket": row.get("tier_bucket"),
        "sources": row.get("sources", []),
    }


def sets(conn, *, eras: list[str], order: list[str] | None = None,
         classes: list[str] | None = None) -> dict:
    """The set-adornment view: rank the SET BONUSES, not the armour.

    A set row answers three things that are different questions:

    - what the bonus IS at each tier, which is prose off the wiki and is shown
      as written — nothing here scores a sentence;
    - which items CARRY a piece (the armour that ships the turquoise), which is
      where you actually get it;
    - which items can HOST one (`turquoiseslot ≥ 1`, `level ≥ the set's`),
      because the turquoise detaches and the host does not have to be the armour
      it came in.

    Shortlisting from this view adds the ADORNMENT, never the armour it came
    in. That distinction is the whole reason this view exists.
    """
    rows = _rows(conn, eras)
    scale = scales(rows)
    w = weights(order or [])
    want_classes = {c for c in (classes or []) if c}

    carriers: dict[str, list[dict]] = {}
    hosts: list[dict] = []
    for row in rows:
        if want_classes and not want_classes & set(row["classes"]):
            continue
        row["score"] = score(row["stats"], w, scale)
        if row["set_name"]:
            carriers.setdefault(row["set_name"], []).append(row)
        if row["adorns"].get("turquoise"):
            hosts.append(row)

    keys = [e for e in eras if e in wiki.ERAS] or list(wiki.DEFAULT_ERAS)
    out = []
    for r in conn.execute(
            "SELECT * FROM plan_sets WHERE era IN "
            f"({','.join('?' * len(keys))}) ORDER BY name", keys):
        mine = sorted(carriers.get(r["name"], []),
                      key=lambda x: (-x["score"], x["name"]))
        if want_classes and not mine:
            continue
        level = r["level"] or 0
        can_host = sorted(
            (h for h in hosts if (h["level"] or 0) >= level),
            key=lambda x: (-x["score"], x["name"]))
        bonuses = normalize_set_bonuses(
            json.loads(r["bonuses_json"] or "[]"), typed=True)
        pieces = json.loads(r["pieces_json"] or "[]")
        # Carrier cards are opened from inside the set view, where the reason
        # to inspect the armour is the detachable turquoise it contains.  The
        # ordinary item search enriches this card before serializing it; do the
        # same here instead of sending an included adornment with an empty
        # ladder.  Copy the row so a carrier's set context cannot leak into the
        # generic host cards below.
        carrier_rows = [
            {**row, "_set_bonuses": bonuses, "_set_total": len(pieces)}
            for row in mine
        ]
        out.append({
            "name": r["name"], "page_title": r["page_title"], "era": r["era"],
            "level": r["level"],
            "pieces": pieces,
            "bonuses": bonuses,
            "carriers": [_piece_out(x) for x in carrier_rows],
            "hosts": [_piece_out(x) for x in can_host[:12]],
            "host_count": len(can_host),
            # The best score among the armour that carries a piece. A real
            # number about real items — the BONUS itself is prose and is not
            # scored, because ranking a sentence against a stat order would be
            # inventing an answer.
            "best_carrier": mine[0]["score"] if mine else 0.0,
        })
    out.sort(key=lambda s: (-s["best_carrier"], -(s["level"] or 0), s["name"]))
    return {"sets": out, "scored": bool(w)}


def epics(conn, class_name: str) -> dict:
    """The RoK Fabled/Mythical weapon pair for one subclass.

    Epic rewards are ordinary catalog items, but this read keeps the defining
    RoK progression reachable even when stat priorities or a result limit
    would bury it. Source detail is the quest timeline and is the conservative
    test: rarity alone does not make a weapon a class epic.
    """
    wanted = (class_name or "").strip().lower()
    if wanted not in wiki.SUBCLASSES:
        return {"items": []}
    rows = _rows(conn, ["rok"])
    sources = _sources(conn, [row["page_title"] for row in rows])
    out = []
    for row in rows:
        if wanted not in row["classes"]:
            continue
        # Enervated weapons are later conversion copies of the original RoK
        # Mythicals. They remain searchable in the ordinary item catalog, but
        # must not replace the original raid reward in this progression pair.
        if "enervated" in row["name"].lower():
            continue
        if row["slot"] not in {"Primary", "Secondary", "Ranged"}:
            continue
        mine = sources.get(row["page_title"], [])
        if not any(source["kind"] == "quest" and
                   "epic weapon" in (source.get("detail") or "").lower()
                   for source in mine):
            continue
        stage = wiki.tier_bucket(row["tier"])
        if stage not in {"fabled", "mythical"}:
            continue
        row["sources"] = mine
        item = _item_out(row)
        item["epic_stage"] = stage
        out.append(item)
    out.sort(key=lambda item: ({"fabled": 0, "mythical": 1}[item["epic_stage"]],
                               item["name"]))
    return {"items": out}


def _piece_out(row: dict) -> dict:
    # Set discovery is still gear discovery. Returning the ordinary item shape
    # gives every carrier/host the same examine card and equip action as the
    # main table instead of rendering inert names the reader cannot inspect.
    return _item_out(row)


def meta(conn, eras: list[str] | None = None) -> dict:
    """What the page needs to draw its controls: the expansions on offer, and
    the facets that actually occur in the ones selected.

    An era with nothing synced still appears, with `items: 0` — the page has to
    be able to say "RoK is not synced yet" rather than quietly showing an empty
    table, and only this can tell it that."""
    selected = [e for e in (eras or []) if e in wiki.ERAS] or list(wiki.DEFAULT_ERAS)
    synced = {r["era"]: dict(r) for r in conn.execute("SELECT * FROM plan_syncs")}
    counts = {r["era"]: r["n"] for r in conn.execute(
        "SELECT era, COUNT(DISTINCT page_title) n FROM plan_sources GROUP BY era")}
    rows = _rows(conn, selected)
    slots, tiers, kinds, armor = {}, {}, {}, {}
    for row in rows:
        for slot in (row["slot"], row["slot2"]):
            if slot:
                slots[slot] = slots.get(slot, 0) + 1
        if row["slot_label"] and row["slot_label"] not in {
                row["slot"], row["slot2"]}:
            # Primary remains the broad equipment position; Primary/2H is an
            # additional filter for the subset that consumes both hands.
            slots[row["slot_label"]] = slots.get(row["slot_label"], 0) + 1
        if row["tier_bucket"]:
            tiers[row["tier_bucket"]] = tiers.get(row["tier_bucket"], 0) + 1
        if row["armor"]:
            armor[row["armor"]] = armor.get(row["armor"], 0) + 1
    for r in conn.execute(
            "SELECT kind, COUNT(DISTINCT page_title) n FROM plan_sources "
            f"WHERE era IN ({','.join('?' * len(selected))}) GROUP BY kind", selected):
        kinds[r["kind"]] = r["n"]
    return {
        "eras": [{
            "key": key, "name": name, "label": wiki.ERA_LABEL[key],
            "items": counts.get(key, 0),
            "synced_ts": (synced.get(key) or {}).get("synced_ts"),
        } for key, name in wiki.ERAS.items()],
        "selected": selected,
        # Every stat, so a column and a card can be labelled — and separately
        # the GROUPS, which are the only things the priority editor offers.
        # The two lists are different on purpose: what an item carries and what
        # is worth ranking by are not the same question.
        "stats": [{"key": key, "label": label, "pct": key in wiki.STAT_PCT}
                  for key, label in wiki.STAT_LABEL.items()],
        "groups": [{"label": name,
                    "stats": [{"key": k, "label": wiki.STAT_LABEL[k],
                               "pct": k in wiki.STAT_PCT} for k in keys]}
                   for name, keys in wiki.STAT_GROUPS],
        "opening_order": list(wiki.OPENING_ORDER),
        "classes": list(wiki.SUBCLASSES),
        "slots": sorted(slots, key=lambda s: (-slots[s], s)),
        # In armour WEIGHT order, light to heavy — not by how many items there
        # are. It is a fixed four-item scale a player already has in their
        # head, and sorting it by frequency would shuffle it every re-sync.
        "armor": [a for a in wiki.ARMOR_TYPES if a in armor],
        # In RARITY order, light to heavy, for the same reason armour weight
        # is: it is a ladder the reader already knows, and sorting it by how
        # many items happen to be on each rung would reshuffle it every
        # re-sync. Labels travel with it so the page does not keep a second
        # copy of the game's vocabulary.
        "tiers": [{"key": key, "label": wiki.TIER_BUCKET_LABEL[key],
                   "items": tiers[key]}
                  for key in wiki.TIER_ORDER if key in tiers],
        "kinds": sorted(kinds, key=lambda k: -kinds[k]),
        "kind_counts": kinds,
        "total": len(rows),
    }
