"""The display record for an item a log named: what it is, what it looks like,
and where to read about it.

Two sources, and the split is not arbitrary:

- **Census answers what the item IS** — name, rarity tier, type, slot, level and
  `iconid`. This is an exact lookup, not a search: the log writes item links as
  `\\aITEM <id> <crc>:<Name>\\/a` and that first id IS the Census item id, written
  signed. Verified against Census's own `gamelink`, which it returns in the
  log's notation — `\\aITEM -1813422462 -590025310:Hoop of War\\/a` off the raid
  log is Census item 2481544834 exactly. So none of the reasons gear PROCS are
  closed as wontfix (`docs/census-abilities.md`: 212k items, no reverse index)
  apply here. There is nothing to search for.

- **The wiki answers what it LOOKS like** — Census hands out an `iconid` and no
  image, and EQ2i has the game's icons uploaded as `File:Item <iconid>.png`.
  One file per ICON, not per item: icons are shared across items, and 503 drops
  in the archive resolve to far fewer pictures.

**The rarity a raider reads comes from Census, not from the log.** The log's
`X looted the Fabled <ITEM>.` line only prints for people near you — 15 of the
43 in the golden fixture — so taking rarity from it would leave two thirds of a
raid's drops blank. The log line still earns its keep as the proof somebody
actually TOOK what they won (`loot_drops.confirmed`); it is just not where the
word "Fabled" comes from.

**Nothing here runs on a page load.** Resolution happens after a parse and in
`tools/backfill_loot.py --resolve`; the API serves what is already known and a
missing icon renders as a name. An item is reference data about the game, so
one row and one file serve every account forever.
"""

import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request

import gamewiki
from db import ICONS_DIR

CENSUS_CHUNK = 100
# EQ2i names its icon uploads after the Census icon id.
ICON_TITLE = "File:Item {}.png"
WIKI_PAGE = "https://eq2.fandom.com/wiki/"
WIKI_BATCH = 40          # titles per query; the API allows 50
PAUSE_S = 0.25           # the same unhurried cadence gamewiki uses


def unsign(item_id: int) -> int:
    """The log's signed 32-bit item id as Census writes it."""
    return item_id + 2**32 if item_id < 0 else item_id


def network_allowed() -> bool:
    """Tests set CENSUS_AUTO_REFRESH=0 (conftest) and nothing in CI may reach
    Census or the wiki. One switch for both, because a half-resolved item is
    worse than an unresolved one."""
    return os.environ.get("CENSUS_AUTO_REFRESH", "1") != "0"


def icon_path(iconid: int):
    return ICONS_DIR / f"{int(iconid)}.png"


# ---------- reading ----------

def cards(conn: sqlite3.Connection, item_ids) -> dict[int, dict]:
    """The known display record for each id. Unknown ids are simply absent —
    the caller already has the item's NAME off the log line, so an unresolved
    item still renders, just without a picture or a link."""
    ids = sorted({int(i) for i in item_ids})
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
                "SELECT item_id, name, iconid, tier, type, slot, level, "
                "       wiki_title, icon_ok, stats_json, effects_json FROM items "
                f"WHERE item_id IN ({','.join('?' * len(chunk))})", chunk):
            out[r["item_id"]] = {
                "item_id": r["item_id"], "name": r["name"], "tier": r["tier"],
                "type": r["type"], "slot": r["slot"], "level": r["level"],
                "stats": json.loads(r["stats_json"]) if r["stats_json"] else None,
                "effects": (json.loads(r["effects_json"])
                            if r["effects_json"] else None),
                "icon": r["iconid"] if r["icon_ok"] else None,
                "wiki": (WIKI_PAGE + urllib.parse.quote(
                    r["wiki_title"].replace(" ", "_"), safe="_(),'!:")
                    if r["wiki_title"] else None),
            }
    return out


def unresolved(conn: sqlite3.Connection, item_ids) -> list[int]:
    """Ids with no row yet, plus rows Census answered but the wiki never was
    asked about — the two sources are fetched independently and either can have
    been the one that failed."""
    ids = sorted({int(i) for i in item_ids})
    if not ids:
        return []
    known = {
        r["item_id"] for r in conn.execute(
            "SELECT item_id FROM items "
            f"WHERE item_id IN ({','.join('?' * len(ids))}) "
            "  AND census_ts IS NOT NULL AND wiki_ts IS NOT NULL", ids)}
    return [i for i in ids if i not in known]


# ---------- resolving ----------

def _slot(row: dict) -> str | None:
    slots = row.get("slot_list") or []
    names = [s.get("name") for s in slots if s.get("name")]
    return ", ".join(names) if names else None


# --- the examine window, as EQ2i draws it -------------------------------------
#
# The hover card is a REPLICA of the EQ2i item box, which is itself a replica
# of the in-game examine window — black, Times, a glowing rarity word, green
# stat lines and light-blue effect lines. Nothing is scraped: EQ2i renders that
# box out of the same Census record we already hold, so the card is built here
# from our own data and painted with a local copy of the wiki's own CSS values
# (`MediaWiki:ExamineWindow.css`, mirrored in base.css under `.ew-*`). That
# keeps third-party HTML out of the page entirely, and it works for the items
# whose wiki page does not exist.
#
# The two blocks are the wiki's, not an invention: everything flat is green
# (`.ew-stats`), everything that modifies a property is light blue and gets a
# line to itself (`.ew-effectlist`).
STAT_TYPES = ("attribute", "ac", "skill")                     # green block
# Ability Mod is `normalizedmod` and it belongs with the blue modifiers, not
# with the flat stats: on this server it is one of the two numbers a raider
# actually compares items on, and it reads as a throughput stat beside Potency
# rather than as a skill.
EFFECT_TYPES = ("modifyproperty", "normalizedmod")            # blue block
# The blue block's leaders, in this order and always. Potency and Crit Chance
# are what a raider checks first; everything else follows in ITS order below.
EFFECT_FIRST = ("Potency", "Crit Chance")
EFFECT_THEN = ("Multi Attack", "DPS", "Ability Mod", "Flurry")
# Most of the blue block is a percentage in the game's own display. The flat
# ones are the exception the wiki also makes: a weapon's DPS rating is a
# number, and so is Ability Mod.
NOT_PERCENT = {"DPS", "Ability Mod"}
# Census abbreviates the attributes the way the game's tooltip does; EQ2i
# spells them out, and `str` in modern EQ2 is the class's primary attribute
# whatever the class. Anything not listed keeps Census's own display name.
#
# `All` is the one that is not a rename but a CORRECTION. Census files Ability
# Modifier under the key `all` with the display name "All", which reads as
# "+68 to all something" and is nothing of the kind. The wiki settles it: Bee
# Sting's `EquipInformation` carries `abmod = +62` and Census's record for the
# same item carries `all: 62`, beside its own separate `strength` and
# `stamina`. Ability Mod is one of the two stats that matter on this server, so
# leaving it labelled "All" hid it in plain sight.
STAT_LABEL = {
    "str": "Primary Attributes", "sta": "Stamina", "agi": "Agility",
    "wis": "Wisdom", "int": "Intelligence",
    "All": "Ability Mod",
}
# Stats the LIVE game itemises that this server does not have yet. Census
# describes the item as it stands on live, so a TLE raider reading a drop is
# shown a number their character cannot use — worse than showing nothing,
# because it invites comparing two items on a stat neither one grants.
#
# Crit Bonus is the whole list today. Fervor is the other one of its kind and
# belongs here the moment an item turns up carrying it. Delete an entry when
# the server gets the stat; nothing else needs to change.
ERA_HIDDEN = {"Crit Bonus"}
# EQ2 spells these out in the examine window; the rest of Census's ~30 flags
# are internal ("nodestroy", "artiface") and mean nothing to a raider.
FLAG_LABEL = {
    "attunable": "Attuneable", "lore": "Lore", "lore-equip": "Lore-Equip",
    "heirloom": "Heirloom", "notrade": "No-Trade", "norent": "No-Rent",
    "nobroker": "No-Broker", "nozone": "No-Zone", "prestige": "Prestige",
    "relic": "Relic", "indestructible": "Indestructible",
    "appearance-only": "Appearance Only", "nomail": "No-Mail",
}
ADORN_COLORS = ("white", "orange", "turquoise", "red", "blue", "yellow",
                "green", "purple", "cyan", "grey")


def tier_of(level: int | None) -> int | None:
    """EQ2's equipment tier — the `(Tier 8)` beside a level 70 item. Tiers are
    ten levels wide and one-based, so 70 is 8 and 65 is 7."""
    return None if not level else int(level) // 10 + 1


def _line(mod: dict, key: str) -> dict:
    raw = mod.get("displayname") or key
    name = STAT_LABEL.get(raw, raw)
    return {
        "name": name,
        "value": mod.get("value"),
        # Checked against the LABEL, not Census's key: `all` becomes Ability
        # Mod and it is the label that says whether it is a percentage.
        "pct": mod.get("type") in EFFECT_TYPES and name not in NOT_PERCENT,
    }


def _effect_rank(row: dict) -> tuple:
    """The blue block's order: Potency, then Crit Chance, then the named
    throughput stats, then whatever is left biggest-first. Fixed rather than
    sorted by value because the question a raider asks of this block is always
    the same one in the same order."""
    name = row["name"]
    if name in EFFECT_FIRST:
        return (0, EFFECT_FIRST.index(name), 0.0, "")
    if name in EFFECT_THEN:
        return (1, EFFECT_THEN.index(name), 0.0, "")
    return (2, 0, -float(row["value"]), name)


def stat_block(row: dict) -> dict | None:
    """Census's item record as the examine window a raider recognises.

    Built ONCE, at resolve time, and stored — a hover card must be a read of a
    row we already have, never a request or a fetch. Returns None when Census
    gave us nothing worth a card (a spell scroll, or a stub row for an id it
    does not know)."""
    attrs, skills, effects = [], [], []
    # Census's `ac` entries are one per resist school, and the game shows them
    # as a single Resistances figure when they agree — which on raid gear they
    # always do. Disagreeing values are listed separately rather than summed.
    resists: list[dict] = []
    for key, mod in (row.get("modifiers") or {}).items():
        if not isinstance(mod, dict):
            continue
        if mod.get("value") in (None, 0):
            continue
        kind = mod.get("type") or ""
        if (mod.get("displayname") or key) in ERA_HIDDEN:
            continue
        if kind == "ac":
            resists.append(_line(mod, key))
        elif kind == "attribute":
            attrs.append(_line(mod, key))
        elif kind in STAT_TYPES:
            skills.append(_line(mod, key))
        elif kind in EFFECT_TYPES:
            effects.append(_line(mod, key))

    if resists:
        values = {r["value"] for r in resists}
        resists = ([{"name": "Resistances", "value": resists[0]["value"],
                     "pct": False}] if len(values) == 1 else resists)

    # Biggest first WITHIN a kind, and the kinds in the window's own order —
    # attributes, then resistances, then skills. Sorting the block as one list
    # put a big "All" above the primary attributes, which is not how the game
    # reads. Sorting at all is also what makes the block deterministic, which
    # a dict's insertion order is not.
    for group in (attrs, resists, skills):
        group.sort(key=lambda r: (-float(r["value"]), r["name"]))
    effects.sort(key=_effect_rank)
    stats = attrs + resists + skills

    flags = [FLAG_LABEL[k] for k, v in (row.get("flags") or {}).items()
             if FLAG_LABEL.get(k) and (v or {}).get("value")]
    adorn = [a.get("color") for a in (row.get("adornmentslot_list") or [])
             if a.get("color") in ADORN_COLORS]
    weapon = _weapon(row.get("typeinfo") or {})
    if not stats and not effects and not flags and not adorn and not weapon:
        return None
    return {"stats": stats, "effects": effects, "weapon": weapon,
            "flags": sorted(flags), "adornments": adorn}


def _weapon(info: dict) -> dict | None:
    """A weapon's Damage and Delay rows.

    EQ2i shows the BASE damage range and the rating, not the mastery range —
    `72 - 216  One-Handed Piercing` over `4.0 seconds  (72.15 Rating)` — so
    that is what this reports. Census carries both ranges; the mastery one is
    what the weapon does with the skill capped, which is a different claim and
    not the one the item box makes."""
    if info.get("name") != "weapon":
        return None
    lo, hi = info.get("minbasedamage"), info.get("maxbasedamage")
    if lo is None or hi is None:
        return None
    rating = info.get("damagerating")
    return {
        "low": lo, "high": hi,
        # "One-Handed" + "Slash" is how the box reads it; either may be absent
        # on an off-hand or a bow, so they are joined rather than assumed.
        "style": " ".join(x for x in (info.get("wieldstyle"),
                                      info.get("damagetype")) if x) or None,
        "delay": info.get("delay"),
        "rating": round(rating, 2) if isinstance(rating, (int, float)) else None,
    }


def fetch_census(conn: sqlite3.Connection, ids: list[int], client=None) -> int:
    """Census's answer for each id, upserted. Ids Census does not know get a row
    anyway (name from the caller is not available here, so they are skipped) —
    an item the log named and Census has never heard of is real (player-made,
    since-removed) and must not be re-fetched on every pass, so it is stamped
    with `census_ts` and a NULL iconid."""
    if not ids or not network_allowed():
        return 0
    from census.client import shared_client
    client = client or shared_client()
    now = int(time.time())
    found = 0
    for i in range(0, len(ids), CENSUS_CHUNK):
        chunk = ids[i:i + CENSUS_CHUNK]
        rows = client.item_cards(chunk)
        by_id = {int(r["id"]): r for r in rows if r.get("id") is not None}
        with conn:
            for item_id in chunk:
                r = by_id.get(item_id)
                if r is None:
                    conn.execute(
                        "INSERT INTO items (item_id, name, census_ts) VALUES (?,?,?) "
                        "ON CONFLICT(item_id) DO UPDATE SET census_ts=excluded.census_ts",
                        (item_id, f"item {item_id}", now))
                    continue
                found += 1
                stats = stat_block(r)
                conn.execute(
                    "INSERT INTO items (item_id, name, iconid, tier, type, slot, "
                    "                   level, stats_json, census_ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(item_id) DO UPDATE SET "
                    "  name=excluded.name, iconid=excluded.iconid, tier=excluded.tier, "
                    "  type=excluded.type, slot=excluded.slot, level=excluded.level, "
                    "  stats_json=excluded.stats_json, census_ts=excluded.census_ts",
                    (item_id, r.get("displayname") or f"item {item_id}",
                     r.get("iconid"), r.get("tier"), r.get("type"),
                     _slot(r), r.get("itemlevel"),
                     json.dumps(stats) if stats else None, now))
    return found


_LINK = re.compile(r"\[\[([^\]|#]+)")
# `effectlist= {{EquipmentEffect|Mind Shatter|VII}}` — the effect an item casts,
# by name and tier.
_EFFECT = re.compile(r"\{\{\s*EquipmentEffect\s*\|([^|}]+)(?:\|([^|}]*))?", re.I)
# `effectdesc=` then asterisk-indented lines, up to the next template field.
_EFFECTDESC = re.compile(
    r"^\s*\|?\s*effectdesc\s*=\s*(.*?)(?=^\s*\|?\s*\w+\s*=|^\s*\|?\}\}|\Z)",
    re.I | re.M | re.S)


def item_effects(wikitext: str) -> dict | None:
    """The item's own effect, off its EQ2i page.

    **This is the forward direction, and it is why the gear-proc wontfix does
    not apply** (`docs/census-abilities.md`): that one is *ability name → which
    item casts it*, a reverse lookup with no index. Here the item's page is
    already in hand — it is the page we fetched to build the wiki LINK — so
    reading `{{EquipmentEffect|Name|Tier}}` and the `effectdesc` bullets off it
    costs nothing extra. Census has no field for either.

    Bullets keep their DEPTH: `*When Equipped:` is the condition and
    `**Increases mental damage…` is what it then does, and flattening the two
    loses which is which."""
    names = [
        " ".join(p.strip() for p in (m.group(1), m.group(2) or "") if p.strip())
        for m in _EFFECT.finditer(wikitext or "")
    ]
    desc = []
    m = _EFFECTDESC.search(wikitext or "")
    if m:
        for raw in m.group(1).splitlines():
            line = raw.strip().strip("|").strip()
            if not line.startswith("*"):
                continue
            text = line.lstrip("*").strip()
            if text:
                desc.append({"depth": len(line) - len(line.lstrip("*")),
                             "text": text})
    if not names and not desc:
        return None
    return {"names": names, "desc": desc}


def _resolve_titles(names: list[str]) -> dict[str, tuple]:
    """Item name -> (the EQ2i page to link at or None, its effect or None).

    Two answers from one fetch on purpose: the page whose wikitext proves it is
    not a disambiguation is the same page that carries the item's effect, and
    asking for it twice would double the wiki traffic for nothing.

    An item page is often a DISAMBIGUATION — `Hoop of War` is two lines
    pointing at `Hoop of War (Version 1)` and `(Version 2)` — and sending a
    reader to a page that only says "did you mean" is worse than showing the
    plain name. So a disambig resolves to the version the wiki lists first.
    Which version is the exact one needs the item id, and the wiki does not
    carry it; first-listed is the honest approximation and it is still the
    right ARTICLE.

    The query is written here rather than through `gamewiki.fetch_wikitext`
    because that one asks for `redirects=1` and then discards the mapping — it
    only ever needed the content. Here the mapping IS the answer: the title to
    link at is the one the redirect landed on, and without it every redirected
    item reads as a page that does not exist."""
    out: dict[str, str | None] = {}
    for i in range(0, len(names), WIKI_BATCH):
        batch = names[i:i + WIKI_BATCH]
        d = gamewiki._get({
            "action": "query", "prop": "revisions", "rvprop": "content",
            "rvslots": "main", "titles": "|".join(batch), "redirects": 1,
        })
        q = d.get("query", {})
        # requested -> normalized -> redirect target, each hop optional
        hop = {r["from"]: r["to"] for r in q.get("normalized", [])}
        hop.update({r["from"]: r["to"] for r in q.get("redirects", [])})
        later: dict[str, str] = {}
        text_of = {}
        for pg in q.get("pages", {}).values():
            try:
                text_of[pg["title"]] = pg["revisions"][0]["slots"]["main"]["*"]
            except (KeyError, IndexError):
                continue
        for name in batch:
            title = name
            for _ in range(3):                 # normalize, then redirect
                if title not in hop:
                    break
                title = hop[title]
            text = text_of.get(title)
            if not text:
                out[name] = (None, None)
            elif gamewiki.is_disambiguation(text):
                links = _LINK.findall(text)
                out[name] = (links[0].strip() if links else None, None)
                if out[name][0]:
                    later[name] = out[name][0]
            else:
                out[name] = (title, item_effects(text))
        # The disambig is the COMMON case — `Hoop of War` is two lines pointing
        # at `(Version 1)` and `(Version 2)` — and the effect lives on the
        # version page, not the pointer. So the pages we resolved TO are
        # fetched once more, batched, rather than left without their proc.
        if later:
            time.sleep(PAUSE_S)
            versions = gamewiki.fetch_wikitext(sorted(set(later.values())))
            for name, title in later.items():
                out[name] = (title, item_effects(versions.get(title, "")))
        time.sleep(PAUSE_S)
    return out


def _icon_urls(iconids: list[int]) -> dict[int, str | None]:
    """`File:Item <iconid>.png` -> its image URL, or None when the wiki has no
    upload for that icon."""
    out: dict[int, str | None] = {}
    for i in range(0, len(iconids), WIKI_BATCH):
        batch = iconids[i:i + WIKI_BATCH]
        d = gamewiki._get({
            "action": "query", "prop": "imageinfo", "iiprop": "url",
            "titles": "|".join(ICON_TITLE.format(n) for n in batch),
            "formatversion": "2",
        })
        by_title = {p.get("title"): p for p in d.get("query", {}).get("pages", [])}
        for n in batch:
            page = by_title.get(ICON_TITLE.format(n)) or {}
            info = (page.get("imageinfo") or [{}])[0]
            out[n] = info.get("url")
        time.sleep(PAUSE_S)
    return out


def _download_icon(iconid: int, url: str) -> bool:
    """Cache one icon as a real PNG.

    `format=original` is not optional. The wikia CDN re-encodes to WebP on the
    way out — a URL ending `.png` answers with RIFF/WebP whatever `Accept`
    asks for — and a file whose bytes disagree with its name is a trap for
    every reader after this one. The parameter turns the optimiser off and
    returns the 42x42 PNG that was uploaded, which is then VERIFIED by its
    magic number rather than trusted."""
    if "format=" not in url:
        url += ("&" if "?" in url else "?") + "format=original"
    req = urllib.request.Request(url, headers={"User-Agent": gamewiki.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except Exception:                                  # noqa: BLE001
        return False
    # An icon is a small PNG; anything else is not the file we asked for.
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 512_000:
        return False
    tmp = icon_path(iconid).with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(icon_path(iconid))
    return True


IMAGE_TYPES = ((b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
               (b"\xff\xd8\xff", ".jpg", "image/jpeg"))


def _download_image(stem, url: str) -> bool:
    """Cache one wiki image beside `stem`, named for what it ACTUALLY is.

    The gems are uploaded as `.png` for some colours and `.jpg` for others, and
    the wikia CDN re-encodes to WebP on top of that unless `format=original`
    says otherwise — so the format is decided by the magic number and the file
    is named to match. A file whose bytes disagree with its name is a trap for
    every reader after this one."""
    if "format=" not in url:
        url += ("&" if "?" in url else "?") + "format=original"
    req = urllib.request.Request(url, headers={"User-Agent": gamewiki.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except Exception:                                  # noqa: BLE001
        return False
    suffix = next((s for magic, s, _ in IMAGE_TYPES if data.startswith(magic)), None)
    if suffix is None or len(data) > 512_000:
        return False
    tmp = stem.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(stem.with_suffix(suffix))
    return True


def adorn_path(color: str):
    """The cached gem for one slot colour, whichever format the wiki had."""
    stem = ICONS_DIR / f"adorn-{color}"
    return next((p for p in (stem.with_suffix(s) for _, s, _ in IMAGE_TYPES)
                 if p.exists()), stem.with_suffix(".png"))


def image_type(path) -> str:
    return next((ct for _, s, ct in IMAGE_TYPES if path.suffix == s), "image/png")


def fetch_adorn_gems() -> int:
    """The ten adornment-slot gems EQ2i draws on an item box, cached once.

    A fixed set, not per item — every white slot on every item is the same
    picture. Uploaded as `.png` for some colours and `.jpg` for others, so both
    titles are asked for and whichever exists answers. Called from the item
    resolve; a miss just means that colour renders as nothing."""
    if not network_allowed():
        return 0
    missing = [c for c in ADORN_COLORS if not adorn_path(c).exists()]
    if not missing:
        return 0
    titles = [f"File:{c.title()} Adorn Slot.{ext}"
              for c in missing for ext in ("png", "jpg")]
    d = gamewiki._get({
        "action": "query", "prop": "imageinfo", "iiprop": "url",
        "titles": "|".join(titles), "formatversion": "2",
    })
    url_of = {p.get("title"): (p.get("imageinfo") or [{}])[0].get("url")
              for p in d.get("query", {}).get("pages", [])}
    got = 0
    for c in missing:
        stem = ICONS_DIR / f"adorn-{c}"
        for ext in ("png", "jpg"):
            url = url_of.get(f"File:{c.title()} Adorn Slot.{ext}")
            if url and _download_image(stem, url):
                got += 1
                break
    return got


def fetch_wiki(conn: sqlite3.Connection, ids: list[int]) -> int:
    """The wiki half: a page to link at, and the icon file cached on disk.
    Stamps `wiki_ts` either way, so a miss is remembered rather than re-asked."""
    if not ids or not network_allowed():
        return 0
    rows = conn.execute(
        "SELECT item_id, name, iconid FROM items "
        f"WHERE item_id IN ({','.join('?' * len(ids))})", ids).fetchall()
    if not rows:
        return 0

    fetch_adorn_gems()
    titles = _resolve_titles(sorted({r["name"] for r in rows if r["name"]}))

    # Only icons we have not already got a file for; one picture serves many
    # items and every raid after tonight's.
    need = sorted({r["iconid"] for r in rows
                   if r["iconid"] and not icon_path(r["iconid"]).exists()})
    urls = _icon_urls(need) if need else {}
    have = {n for n in need if urls.get(n) and _download_icon(n, urls[n])}

    now = int(time.time())
    done = 0
    with conn:
        for r in rows:
            ok = bool(r["iconid"]) and (
                r["iconid"] in have or icon_path(r["iconid"]).exists())
            title, effects = titles.get(r["name"], (None, None))
            conn.execute(
                "UPDATE items SET wiki_title=?, icon_ok=?, effects_json=?, "
                "wiki_ts=? WHERE item_id=?",
                (title, int(ok), json.dumps(effects) if effects else None,
                 now, r["item_id"]))
            done += 1
    return done


def ensure(conn: sqlite3.Connection, item_ids) -> int:
    """Resolve everything not already known. Blocking and network-bound — call
    it from a worker, outside any write transaction, and never from a request
    handler. Returns how many ids it worked on."""
    ids = unresolved(conn, item_ids)
    if not ids:
        return 0
    fetch_census(conn, ids)
    fetch_wiki(conn, ids)
    return len(ids)
