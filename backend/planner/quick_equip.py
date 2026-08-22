"""Generate a complete gear draft against an ordered stat-target objective.

Quick Equip is deliberately separate from catalog ranking. The equipment table
is discovery; this module is allowed to make a choice, but only from facts the
reader stated: expansion, class, maximum item level, allowed sources, ordered
stats, per-item required stats, and reader-selected targets. There is no game
cap model, proc valuation, adornment valuation, or hidden class template.

The objective is lexicographic over the WHOLE loadout. Each priority is valued
up to its reader-selected target, so after priority 1 reaches its target,
priority 2 can improve without paying for surplus priority-1 points. Target
sliders use rounded scales that bracket feasible minimum/maximum complete-
loadout totals under the same filters. The same item is never suggested twice,
and a two-handed Primary consumes Secondary.
"""

from __future__ import annotations

from collections import defaultdict
from math import ceil, floor, isfinite, log10
from typing import Iterable

from planner import catalog, wiki

ALTERNATIVES = 3
MAX_PRIORITIES = 5
BEAM_WIDTH = 180

# The generated set follows the equipment window. Ammo and Event are omitted:
# neither is part of the era gear catalog, and inventing an empty recommendation
# for a surface with no candidates would not make the result more complete.
SLOTS = (
    {"key": "activate1", "label": "Charm I", "catalog": "Charm", "side": "left"},
    {"key": "cloak", "label": "Cloak", "catalog": "Cloak", "side": "left"},
    {"key": "head", "label": "Head", "catalog": "Head", "side": "left"},
    {"key": "shoulders", "label": "Shoulders", "catalog": "Shoulders", "side": "left"},
    {"key": "chest", "label": "Chest", "catalog": "Chest", "side": "left"},
    {"key": "forearms", "label": "Forearms", "catalog": "Forearms", "side": "left"},
    {"key": "hands", "label": "Hands", "catalog": "Hands", "side": "left"},
    {"key": "legs", "label": "Legs", "catalog": "Legs", "side": "left"},
    {"key": "feet", "label": "Feet", "catalog": "Feet", "side": "left"},
    {"key": "primary", "label": "Primary", "catalog": "Primary", "side": "left"},
    {"key": "secondary", "label": "Secondary", "catalog": "Secondary", "side": "left"},
    {"key": "activate2", "label": "Charm II", "catalog": "Charm", "side": "right"},
    {"key": "ears", "label": "Ear I", "catalog": "Ear", "side": "right"},
    {"key": "ears2", "label": "Ear II", "catalog": "Ear", "side": "right"},
    {"key": "neck", "label": "Neck", "catalog": "Neck", "side": "right"},
    {"key": "left_ring", "label": "Finger I", "catalog": "Finger", "side": "right"},
    {"key": "right_ring", "label": "Finger II", "catalog": "Finger", "side": "right"},
    {"key": "left_wrist", "label": "Wrist I", "catalog": "Wrist", "side": "right"},
    {"key": "right_wrist", "label": "Wrist II", "catalog": "Wrist", "side": "right"},
    {"key": "waist", "label": "Waist", "catalog": "Waist", "side": "right"},
    {"key": "ranged", "label": "Ranged", "catalog": "Ranged", "side": "right"},
)

SOURCE_KINDS = ("raid", "group", "solo", "quest", "crafted", "zone", "unknown")

_ARMOR_RANK = {name: rank for rank, name in enumerate(wiki.ARMOR_TYPES)}
_CLASS_MAX_ARMOR = {
    "monk": "Leather", "bruiser": "Leather",
    "warden": "Leather", "fury": "Leather",
    "mystic": "Chain", "defiler": "Chain",
    "templar": "Plate", "inquisitor": "Plate",
    "guardian": "Plate", "berserker": "Plate", "paladin": "Plate",
    "shadowknight": "Plate",
    "assassin": "Chain", "ranger": "Chain", "brigand": "Chain",
    "swashbuckler": "Chain", "troubador": "Chain", "dirge": "Chain",
}

STAT_GROUPS = (
    ("Abilities", ("abmod", "acspeed", "arspeed", "abdblcast", "dblcast")),
    ("Melee", ("aspeed", "dps", "multi", "flurry", "aeauto", "strike",
               "accuracy")),
    ("Defense", ("bchance", "hategain", "mit", "prot", "mitinc",
                 "maxhealth", "hregen")),
    ("Attributes", ("str", "agi", "sta", "wis", "int")),
    ("Resists & skills", ("comskills", "vselemental", "vsarcane", "vsnoxious")),
    ("Common", ("potency", "crit")),
)
STATS = tuple(key for _, keys in STAT_GROUPS for key in keys)


def wearable_armor(class_name: str) -> list[str]:
    """Armor weights the class may wear, lightest through its maximum."""
    maximum = _CLASS_MAX_ARMOR.get((class_name or "").strip().lower(), "Cloth")
    return [name for name in wiki.ARMOR_TYPES
            if _ARMOR_RANK[name] <= _ARMOR_RANK[maximum]]


def meta() -> dict:
    """The Quick Equip vocabulary, kept beside the rules that enforce it."""
    return {
        "max_priorities": MAX_PRIORITIES,
        "source_kinds": list(SOURCE_KINDS),
        "groups": [{
            "label": label,
            "stats": [{"key": key, "label": wiki.STAT_LABEL[key],
                       "pct": key in wiki.STAT_PCT} for key in keys],
        } for label, keys in STAT_GROUPS],
        "class_armor": {class_name: wearable_armor(class_name)
                        for class_name in wiki.SUBCLASSES},
    }


def _clean_order(order: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(key for key in order if key in STATS))[:MAX_PRIORITIES]


def _vector(row: dict | None, order: list[str]) -> tuple[float, ...]:
    stats = row.get("stats", {}) if row else {}
    return tuple(float(stats.get(key) or 0) for key in order)


def _add(*vectors: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(sum(values) for values in zip(*vectors)) if vectors else ()


def _rank_key(row: dict, order: list[str]) -> tuple:
    return (*(-value for value in _vector(row, order)),
            -(row.get("level") or 0), row.get("name") or "")


def _low_rank_key(row: dict, order: list[str]) -> tuple:
    return (*_vector(row, order), row.get("level") or 0, row.get("name") or "")


def _eligible_catalog_slots(row: dict) -> set[str]:
    if row.get("two_handed"):
        return {"Primary"}
    out = set()
    for raw in (row.get("slot"), row.get("slot2")):
        name = (raw or "").strip().lower()
        if name == "shield":
            out.add("Secondary")
            continue
        for slot in SLOTS:
            if slot["catalog"].lower() == name:
                out.add(slot["catalog"])
                break
    return out


def _best_weapons(primary: list[dict], secondary: list[dict],
                  order: list[str], *, maximize: bool = True
                  ) -> tuple[dict | None, dict | None]:
    """Choose a feasible raw extreme across the complete weapon pair."""
    if not primary:
        return None, secondary[0] if secondary else None
    best: tuple[tuple, dict, dict | None] | None = None
    for main in primary:
        off = None
        if not main.get("two_handed"):
            off = next((row for row in secondary
                        if row["page_title"] != main["page_title"]), None)
        vector = _add(_vector(main, order), _vector(off, order))
        tie = (vector, (main.get("level") or 0) + ((off or {}).get("level") or 0),
               main.get("name") or "", off.get("name") if off else "")
        if best is None or (tie > best[0] if maximize else tie < best[0]):
            best = (tie, main, off)
    return (best[1], best[2]) if best else (None, None)


def _resolve_selected_conflicts(selected: dict[str, dict | None],
                                candidates: dict[str, list[dict]],
                                order: list[str], *, maximize: bool = True) -> None:
    """Remove a multiply selected physical item at the smallest objective cost."""
    for _ in range(len(SLOTS)):
        owners: dict[str, list[str]] = defaultdict(list)
        for key, row in selected.items():
            if row:
                owners[row["page_title"]].append(key)
        conflict = next(((page, keys) for page, keys in owners.items()
                         if len(keys) > 1), None)
        if not conflict:
            return
        page, keys = conflict
        occupied = {row["page_title"] for row in selected.values()
                    if row and row["page_title"] != page}
        replacements = {}
        losses = {}
        for key in keys:
            replacement = next((row for row in candidates.get(key, [])
                                if row["page_title"] not in occupied
                                and row["page_title"] != page
                                and not (key == "primary"
                                         and selected.get("secondary")
                                         and row.get("two_handed"))), None)
            replacements[key] = replacement
            if replacement is None:
                losses[key] = tuple(float("inf") for _ in order)
            else:
                current = _vector(selected[key], order)
                alternate = _vector(replacement, order)
                losses[key] = tuple(
                    (a - b if maximize else b - a)
                    for a, b in zip(current, alternate))
        keep = max(keys, key=lambda key: losses[key])
        for key in keys:
            if key == keep:
                continue
            selected[key] = replacements[key]
            if replacements[key]:
                occupied.add(replacements[key]["page_title"])


def _prepare(conn, *, eras: list[str], class_name: str, max_level: int,
             order: list[str], required: list[str] | None,
             kinds: list[str] | None, armor: list[str] | None) -> dict:
    """Validate one criteria set and materialize its eligible item pools."""
    selected_eras = [era for era in eras if era in wiki.ERAS] or list(wiki.DEFAULT_ERAS)
    class_key = (class_name or "").strip().lower()
    if class_key not in wiki.SUBCLASSES:
        raise ValueError("choose a valid adventure class")
    ranked = _clean_order(order or [])
    if not ranked:
        raise ValueError("choose at least one stat priority")
    need = list(dict.fromkeys(key for key in (required or []) if key in STATS))
    wanted_kinds = {kind for kind in (kinds or []) if kind in SOURCE_KINDS}
    wearable = wearable_armor(class_key)
    asked_armor = [str(name).title() for name in (armor or [])]
    wanted_armor = [name for name in wiki.ARMOR_TYPES
                    if name in asked_armor and name in wearable]
    if asked_armor and not wanted_armor:
        raise ValueError("choose at least one armor type this class can wear")
    if not asked_armor:
        wanted_armor = wearable

    rows = catalog._rows(conn, selected_eras)
    sources = catalog._sources(conn, [row["page_title"] for row in rows])
    candidates = []
    eligible_before_required = 0
    for row in rows:
        row_sources = [source for source in sources.get(row["page_title"], [])
                       if source.get("era") in selected_eras]
        if not row.get("level") or row["level"] > max_level:
            continue
        if class_key not in set(row.get("classes") or []):
            continue
        if row.get("armor") and row["armor"] not in wanted_armor:
            continue
        if wanted_kinds and not wanted_kinds & {source["kind"] for source in row_sources}:
            continue
        if not _eligible_catalog_slots(row):
            continue
        eligible_before_required += 1
        if any(not row["stats"].get(key) for key in need):
            continue
        row["sources"] = row_sources
        candidates.append(row)

    by_catalog: dict[str, list[dict]] = defaultdict(list)
    for row in candidates:
        for slot_name in _eligible_catalog_slots(row):
            by_catalog[slot_name].append(row)
    return {
        "eras": selected_eras, "class": class_key, "max_level": max_level,
        "order": ranked, "required": need, "kinds": wanted_kinds,
        "armor": wanted_armor, "candidates": candidates,
        "eligible_before_required": eligible_before_required,
        "by_catalog": by_catalog,
    }


def _criteria_out(context: dict, targets: dict[str, float] | None = None) -> dict:
    out = {
        "eras": context["eras"], "class": context["class"],
        "max_level": context["max_level"], "order": context["order"],
        "required": context["required"],
        "kinds": [kind for kind in SOURCE_KINDS if kind in context["kinds"]],
        "armor": context["armor"],
    }
    if targets is not None:
        out["targets"] = targets
    return out


def _select_extreme(by_catalog: dict[str, list[dict]], order: list[str],
                    *, maximize: bool) -> dict[str, dict | None]:
    """One feasible high or low loadout for a raw lexicographic objective."""
    sort_key = (lambda row: _rank_key(row, order)) if maximize else (
        lambda row: _low_rank_key(row, order))
    ranked_catalog = {
        name: sorted(rows, key=sort_key) for name, rows in by_catalog.items()
    }
    candidates_by_slot = {
        slot["key"]: ranked_catalog.get(slot["catalog"], []) for slot in SLOTS
    }
    selected: dict[str, dict | None] = {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for slot in SLOTS:
        if slot["catalog"] not in {"Primary", "Secondary"}:
            grouped[slot["catalog"]].append(slot)
    for catalog_name, concrete in grouped.items():
        pool = ranked_catalog.get(catalog_name, [])
        for index, slot in enumerate(concrete):
            selected[slot["key"]] = pool[index] if index < len(pool) else None

    primary, secondary = _best_weapons(
        ranked_catalog.get("Primary", []), ranked_catalog.get("Secondary", []),
        order, maximize=maximize)
    selected["primary"] = primary
    selected["secondary"] = None if primary and primary.get("two_handed") else secondary
    _resolve_selected_conflicts(
        selected, candidates_by_slot, order, maximize=maximize)
    return selected


def _selected_totals(selected: dict[str, dict | None],
                     order: list[str]) -> tuple[float, ...]:
    return tuple(round(sum(float((row.get("stats") or {}).get(key) or 0)
                           for row in selected.values() if row), 2)
                 for key in order)


def _achievable_ranges(context: dict) -> dict[str, dict[str, float]]:
    """Feasible full-loadout endpoints under this exact candidate scope."""
    out = {}
    for stat in context["order"]:
        low = _selected_totals(
            _select_extreme(context["by_catalog"], [stat], maximize=False), [stat])[0]
        high = _selected_totals(
            _select_extreme(context["by_catalog"], [stat], maximize=True), [stat])[0]
        if low > high:
            low, high = high, low
        slider_min, slider_max, step = _nice_scale(low, high)
        out[stat] = {
            "min": low, "max": high,
            "slider_min": slider_min, "slider_max": slider_max,
            "step": step,
        }
    return out


def _nice_scale(low: float, high: float) -> tuple[float, float, float]:
    """Return about twelve memorable target stops bracketing the raw range."""
    span = high - low
    if span <= 0:
        return low, high, 1
    rough = span / 12
    magnitude = 10 ** floor(log10(rough))
    fraction = rough / magnitude
    nice = next(value for value in (1, 2, 2.5, 5, 10) if fraction <= value)
    step = nice * magnitude
    decimals = max(0, -floor(log10(step))) + (1 if nice == 2.5 else 0)
    slider_min = round(floor(low / step) * step, decimals)
    slider_max = round(ceil(high / step) * step, decimals)
    return slider_min, slider_max, round(step, decimals)


def ranges(conn, *, eras: list[str], class_name: str, max_level: int,
           order: list[str], required: list[str] | None = None,
           kinds: list[str] | None = None, armor: list[str] | None = None) -> dict:
    """Return the slider scale for each chosen whole-loadout stat."""
    context = _prepare(
        conn, eras=eras, class_name=class_name, max_level=max_level, order=order,
        required=required, kinds=kinds, armor=armor)
    return {
        "criteria": _criteria_out(context),
        "ranges": _achievable_ranges(context),
        "candidates": len(context["candidates"]),
        "eligible_before_required": context["eligible_before_required"],
    }


def _clean_targets(asked: dict[str, float] | None, ranges_: dict,
                   order: list[str]) -> dict[str, float]:
    out = {}
    for key in order:
        bounds = ranges_[key]
        lower = bounds.get("slider_min", bounds["min"])
        upper = bounds.get("slider_max", bounds["max"])
        value = upper if asked is None or key not in asked else float(asked[key])
        if not isfinite(value):
            value = upper
        out[key] = round(max(lower, min(upper, value)), 2)
    return out


def _shortlist(rows: list[dict], order: list[str], targets: dict[str, float],
               *, limit: int = 80) -> list[dict]:
    """Bound beam fan-out while preserving useful candidate shapes."""
    if len(rows) <= limit:
        return rows
    chosen: dict[str, dict] = {}

    def keep(values: Iterable[dict]):
        for row in values:
            chosen[row["page_title"]] = row

    raw = sorted(rows, key=lambda row: _rank_key(row, order))
    keep(raw[:min(20, limit // 2)])
    slots = max(1, len(SLOTS))
    per_stat = max(4, (limit - len(chosen)) // max(1, len(order)))
    for stat in order:
        by_stat = sorted(rows, key=lambda row: float(row["stats"].get(stat) or 0),
                         reverse=True)
        high = max(2, per_stat // 2)
        low = max(1, per_stat // 6)
        near = max(1, per_stat // 4)
        keep(by_stat[:high])
        keep(by_stat[-low:])
        share = targets[stat] / slots
        keep(sorted(rows, key=lambda row: abs(
            float(row["stats"].get(stat) or 0) - share))[:near])
        quantiles = max(0, per_stat - high - low - near)
        if quantiles:
            keep(by_stat[int(i * (len(by_stat) - 1) / max(1, quantiles - 1))]
                 for i in range(quantiles))
    # Duplicate shapes leave room; fill it in the declared raw order rather
    # than letting whichever stat happened to be listed first consume it.
    keep(row for row in raw if len(chosen) < limit)
    return list(chosen.values())[:limit]


def _capped(values: tuple[float, ...], order: list[str],
            targets: dict[str, float]) -> tuple[float, ...]:
    return tuple(min(value, targets[key]) for key, value in zip(order, values))


def _target_selection(by_catalog: dict[str, list[dict]], order: list[str],
                      targets: dict[str, float]) -> dict[str, dict | None]:
    """Beam-search the coupled, target-capped whole-loadout objective."""
    groups = []
    for slot in SLOTS:
        if slot["catalog"] in {"Primary", "Secondary"}:
            continue
        rows = _shortlist(by_catalog.get(slot["catalog"], []), order, targets)
        options = [(((slot["key"], row),), frozenset({row["page_title"]}),
                    _vector(row, order), 1) for row in rows]
        options.append((((slot["key"], None),), frozenset(),
                        tuple(0.0 for _ in order), 0))
        groups.append(options)

    primaries = _shortlist(by_catalog.get("Primary", []), order, targets, limit=45)
    secondaries = _shortlist(by_catalog.get("Secondary", []), order, targets, limit=45)
    weapon_options = []
    for main in primaries:
        if main.get("two_handed"):
            weapon_options.append(((('primary', main), ('secondary', None)),
                                   frozenset({main["page_title"]}),
                                   _vector(main, order), 2))
            continue
        matching = [off for off in secondaries
                    if off["page_title"] != main["page_title"]]
        if not matching:
            weapon_options.append(((('primary', main), ('secondary', None)),
                                   frozenset({main["page_title"]}),
                                   _vector(main, order), 1))
        for off in matching:
            weapon_options.append(((('primary', main), ('secondary', off)),
                                   frozenset({main["page_title"], off["page_title"]}),
                                   _add(_vector(main, order), _vector(off, order)), 2))
    if not primaries:
        for off in secondaries:
            weapon_options.append(((('primary', None), ('secondary', off)),
                                   frozenset({off["page_title"]}),
                                   _vector(off, order), 1))
    weapon_options.append(((('primary', None), ('secondary', None)), frozenset(),
                           tuple(0.0 for _ in order), 0))
    weapon_options.sort(
        key=lambda option: (_capped(option[2], order, targets), option[3], option[2]),
        reverse=True)
    if len(weapon_options) > 180:
        kept = {}

        def keep_options(options):
            for option in options:
                key = tuple(sorted(option[1]))
                kept[key] = option

        keep_options(weapon_options[:60])
        budget = max(6, (180 - len(kept)) // max(1, len(order)))
        for stat_index, stat in enumerate(order):
            by_stat = sorted(weapon_options, key=lambda option: option[2][stat_index],
                             reverse=True)
            keep_options(by_stat[:max(3, budget * 2 // 3)])
            keep_options(by_stat[-max(1, budget // 8):])
            share = targets[stat] * 2 / len(SLOTS)
            keep_options(sorted(
                weapon_options,
                key=lambda option: abs(option[2][stat_index] - share)
            )[:max(2, budget // 4)])
        keep_options(option for option in weapon_options if len(kept) < 179)
        empty = next(option for option in weapon_options if not option[1])
        pruned = list(kept.values())[:179]
        if empty not in pruned:
            pruned.append(empty)
        weapon_options = pruned
    groups.append(weapon_options)

    # Process tight pools first. Slot names travel with each option, so this
    # changes search efficiency rather than equipment-window placement.
    groups.sort(key=len)
    remaining = [tuple(0.0 for _ in order) for _ in range(len(groups) + 1)]
    for index in range(len(groups) - 1, -1, -1):
        best = tuple(max(option[2][i] for option in groups[index])
                     for i in range(len(order)))
        remaining[index] = _add(best, remaining[index + 1])

    # totals, used physical pages, chosen option indexes, occupied positions
    states = [(tuple(0.0 for _ in order), frozenset(), (), 0)]
    for index, options in enumerate(groups):
        expanded = []
        for totals, used, chosen, filled in states:
            for option_index, (_, pages, vector, option_filled) in enumerate(options):
                if used & pages:
                    continue
                expanded.append((_add(totals, vector), used | pages,
                                 (*chosen, option_index), filled + option_filled))
        optimistic = remaining[index + 1]
        expanded.sort(
            key=lambda state: (
                _capped(_add(state[0], optimistic), order, targets),
                _capped(state[0], order, targets), state[3], state[0]),
            reverse=True)
        states = expanded[:BEAM_WIDTH]
        if not states:
            break
    if not states:
        return {slot["key"]: None for slot in SLOTS}
    _, _, chosen, _ = max(
        states, key=lambda state: (_capped(state[0], order, targets),
                                   state[3], state[0]))
    selected = {slot["key"]: None for slot in SLOTS}
    for options, option_index in zip(groups, chosen):
        for key, row in options[option_index][0]:
            selected[key] = row
    return selected


def generate(conn, *, eras: list[str], class_name: str, max_level: int,
             order: list[str], required: list[str] | None = None,
             kinds: list[str] | None = None, armor: list[str] | None = None,
             targets: dict[str, float] | None = None,
             alternatives: int = ALTERNATIVES) -> dict:
    """Return a complete draft plus three safe choices for every filled slot."""
    context = _prepare(
        conn, eras=eras, class_name=class_name, max_level=max_level, order=order,
        required=required, kinds=kinds, armor=armor)
    ranked = context["order"]
    ranges_ = _achievable_ranges(context)
    target_values = _clean_targets(targets, ranges_, ranked)
    alternatives = max(1, min(int(alternatives or ALTERNATIVES), ALTERNATIVES))
    by_catalog = context["by_catalog"]
    candidates_by_slot = {
        slot["key"]: sorted(by_catalog.get(slot["catalog"], []),
                            key=lambda row: _rank_key(row, ranked))
        for slot in SLOTS
    }
    raw_selection = _select_extreme(by_catalog, ranked, maximize=True)
    if targets is None:
        selected_by_slot = raw_selection
    else:
        targeted = _target_selection(by_catalog, ranked, target_values)

        def objective(selection):
            totals = _selected_totals(selection, ranked)
            return (_capped(totals, ranked, target_values),
                    sum(row is not None for row in selection.values()), totals)

        # The bounded beam is allowed to find a better target tradeoff, but it
        # may never lose to the exact raw lexicographic extreme. This matters
        # when a rounded target sits just above the measured ceiling: that stat
        # must still reach the true maximum rather than the beam's near miss.
        selected_by_slot = max((targeted, raw_selection), key=objective)

    # Allocate each alternative item to one concrete slot. Any combination the
    # reader reaches by cycling therefore remains physically possible.
    options_by_slot: dict[str, list[dict]] = {
        key: [row] if row else [] for key, row in selected_by_slot.items()
    }
    claimed = {row["page_title"] for row in selected_by_slot.values() if row}
    for slot in SLOTS:
        choices = options_by_slot.setdefault(slot["key"], [])
        for row in candidates_by_slot.get(slot["key"], []):
            if len(choices) >= alternatives:
                break
            if row["page_title"] in claimed:
                continue
            choices.append(row)
            claimed.add(row["page_title"])
        choices.sort(key=lambda row: _rank_key(row, ranked))

    output_rows = [row for choices in options_by_slot.values() for row in choices]
    unique_output = list({row["page_title"]: row for row in output_rows}.values())
    catalog.attach_set_details(conn, unique_output)

    slots = []
    selected_rows = []
    for slot in SLOTS:
        choices = options_by_slot.get(slot["key"], [])
        selected_row = selected_by_slot.get(slot["key"])
        selected_page = selected_row["page_title"] if selected_row else None
        selected = next((row for row in choices
                         if row["page_title"] == selected_page), None)
        if selected:
            selected_rows.append(selected)
        slots.append({
            **slot,
            "selected": next((i for i, row in enumerate(choices)
                              if row["page_title"] == selected_page), None),
            "options": [catalog._item_out(row) for row in choices],
        })

    totals = dict(zip(ranked, _selected_totals(selected_by_slot, ranked)))
    two_handed = bool(selected_by_slot.get("primary")
                      and selected_by_slot["primary"].get("two_handed"))
    missing = [slot["label"] for slot in slots if not slot["options"]
               and not (slot["key"] == "secondary" and two_handed)]
    return {
        "criteria": _criteria_out(context, target_values),
        "ranges": ranges_,
        "slots": slots,
        "totals": totals,
        "filled": len(selected_rows),
        "missing": missing,
        "candidates": len(context["candidates"]),
        "eligible_before_required": context["eligible_before_required"],
    }
