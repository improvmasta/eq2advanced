"""The EQ2 wiki as a reference source, for the abilities Census cannot explain.

Census stays authoritative for SPELLS — 26,082 structured records with damage
ranges, periods and the effect grammar, which is more than any wiki page holds.
What Census was never asked for is **AAs** (256 incidental rows against this
source's 1215) and items, and between them they are most of what a raid log
names and nothing can currently account for: 479 ability names with no Census
row at all.

Two things here are not available anywhere else, and both are corrections
rather than gap-filling:

- **`activated`.** `You prepare <X>` prints for spells and combat arts and NOT
  for AA activations, so the log's only proof that something was PRESSED is
  absent exactly where AAs live — and an activated AA reads as a proc. That
  misfiled `Lifeburn` (a 5-minute recast the necromancer presses) as gear, with
  45 more rows resting on the same silence. A recast timer settles it.
- **The class TIER.** The wiki files AAs by the tier the game grants them at —
  `Predator AAs`, `Crusader AAs`, `Enchanter AAs` — which is the shape
  `classtree` already speaks and Census flattens away.

**Era is a hard filter, not a preference.** The wiki separates the AA trees by
expansion and they do not overlap at all (verified: 1215 EoF pages, 407 later,
zero shared). A level-70 EoF server takes Class + Subclass and must never see
Heroic (RoK), Shadows (TSO) or Dragon (DoV) abilities — ingesting those would
label raids with content that does not exist on the server, which is the same
class of mistake as inferring a pet from one sighting.

Nothing fetched here becomes a verdict. It is evidence on the Abilities console
beside the log's own, and the human still rules — see `census/abilityreview.py`.
"""

import json
import re
import time
import urllib.parse
import urllib.request

import classtree

API = "https://eq2.fandom.com/api.php"
# The wiki asks for a real user agent and a contact; a scraper that hides is a
# scraper that gets blocked, and this runs rarely enough to be a good citizen.
USER_AGENT = "eq2advanced/1.0 (+https://eq2advanced.com; lindsay@jupiterns.org)"
PAUSE_S = 0.25          # between calls, deliberately unhurried
BATCH = 40              # titles per query; the API allows 50

# expansion -> the category trees that hold its AAs. The key is stored on every
# row, so adding RoK when the server gets there is one entry and a re-sync
# rather than a migration.
AA_TREES = {
    "eof": ("Category:AAs by Class", "Category:AAs by Subclass"),
    "rok": ("Category:Heroic AAs",),
    "tso": ("Category:Shadows AAs", "Category:Shadows AAs by Subclass"),
    "dov": ("Category:Dragon AAs",),
}
# What this server actually has. Everything else is fetched only if asked for
# explicitly — see tools/sync_wiki.py --era.
DEFAULT_ERAS = ("eof",)

# Deity abilities — blessings and miracles. Small and bounded (139 pages), and
# EoF-era by construction: deities arrived WITH Echoes of Faydwer, so there is
# no later-expansion set to filter out the way there is for AAs.
DEITY_TREES = ("Category:Blessings", "Category:Miracles")



class WikiError(RuntimeError):
    pass


def _get(params: dict) -> dict:
    params = {**params, "format": "json"}
    req = urllib.request.Request(
        f"{API}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as exc:                       # noqa: BLE001 — one failure mode
        raise WikiError(f"wiki request failed: {exc}") from exc


def category_members(cat: str, kind: str = "page") -> list[str]:
    """Every page (or subcategory) in a category, following continuations."""
    out: list[str] = []
    cont: dict = {}
    while True:
        d = _get({"action": "query", "list": "categorymembers", "cmtitle": cat,
                  "cmtype": kind, "cmlimit": "500", **cont})
        out += [m["title"] for m in d.get("query", {}).get("categorymembers", [])]
        if "continue" not in d:
            return out
        cont = d["continue"]
        time.sleep(PAUSE_S)


# ---------- template parsing ----------
#
# Both SpellInformation2 and the AA templates are flat `field = value` lists
# with HTML comments explaining each one, so one reader serves both. The
# comments must go first or `class = |  <!-- name of the class -->` reads its
# own documentation as the value.

_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _field(wikitext: str, name: str) -> str:
    m = re.search(rf"^\s*\|?\s*{name}\s*=\s*(.*?)\s*\|?\s*$", wikitext, re.I | re.M)
    return m.group(1).strip() if m else ""


_DURATION = re.compile(r"([\d.]+)\s*(second|minute|hour)", re.I)


def _seconds(text: str) -> float | None:
    """"30.0 seconds" / "5 minutes" -> seconds. Recast is stated in words and
    the unit changes with the size of the number."""
    m = _DURATION.search(text or "")
    if not m:
        return None
    n = float(m.group(1))
    return n * {"second": 1, "minute": 60, "hour": 3600}[m.group(2).lower()]


# a page title may be disambiguated — "Intoxication (AA)", "Lifeburn (Assassin)"
# — but the LOG prints the bare name, so that is the key the rest of the app
# joins on
_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


def log_name(page_title: str) -> str:
    return _SUFFIX.sub("", page_title).strip()


def is_disambiguation(wikitext: str) -> bool:
    """A page that only points at other pages. Ingesting one lets a deity page
    claim a class spell of the same name."""
    return bool(re.search(r"\{\{\s*disambig", wikitext or "", re.I))


def parse_page(page_title: str, wikitext: str, kind: str, era: str,
               tiers: set[str]) -> dict:
    """One wiki page -> a `wiki_abilities` row.

    `activated` is the load-bearing field: a recast timer or a power cost means
    the player pressed a button, which is the thing the log cannot tell us
    about an AA. Absent both, it is passive and firing on its own is the whole
    way it works."""
    text = _COMMENT.sub("", wikitext)
    recast = _field(text, "recast")
    power = _field(text, "power")
    # the class named on the page is a tier too, and often the only one when a
    # page sits outside the by-class categories
    page_class = _field(text, "class").strip().lower()
    if classtree.is_target(page_class):
        tiers = set(tiers) | {page_class}
    # `effects` is a bullet list, so it runs to the NEXT field or the end of
    # the template — whichever comes first. Both boundaries are needed: a page
    # whose value is "see below" has no bullets at all, and without the `}}`
    # stop the capture swallowed the rest of the article, templates and all.
    effects = ""
    m = re.search(r"^\s*\|?\s*effects\s*=\s*(.*?)"
                  r"(?=^\s*\|?\s*\w+\s*=|^\s*\|?\}\}|\Z)",
                  text, re.I | re.M | re.S)
    if m:
        effects = "\n".join(ln.strip(" |") for ln in m.group(1).splitlines()
                            if ln.strip(" |"))
    # A deity ability belongs to a GOD, not a class, so the deity rides in the
    # `line` column — the same slot an AA's line uses, and the same question:
    # what grants this.
    deity = _field(text, "deity")
    return {
        "name": log_name(page_title),
        "page_title": page_title,
        "kind": kind,
        "era": era,
        "tiers": classtree.normalize(",".join(sorted(tiers))) or None,
        "line": (deity if kind == "deity" else _field(text, "line")) or None,
        "activated": 1 if (recast or power) else 0,
        "recast_s": _seconds(recast),
        "power": power or None,
        "target": _field(text, "target") or None,
        "descr": (_field(text, "desc") or None),
        "effects": effects or None,
        "fetched_ts": int(time.time()),
    }


def fetch_wikitext(titles: list[str]) -> dict[str, str]:
    """{page title -> wikitext} for up to BATCH titles."""
    d = _get({"action": "query", "prop": "revisions", "rvprop": "content",
              "rvslots": "main", "titles": "|".join(titles), "redirects": 1})
    out = {}
    for pg in d.get("query", {}).get("pages", {}).values():
        try:
            out[pg["title"]] = pg["revisions"][0]["slots"]["main"]["*"]
        except (KeyError, IndexError):
            continue          # missing page, or one with no content
    return out


def collect_aa_titles(era: str) -> dict[str, set[str]]:
    """{page title -> class tiers} for one expansion's AA trees.

    A page appears under every tier that grants it, which is exactly the
    generosity the Abilities console wants: one ability under both `predator`
    and `assassin` until somebody says which."""
    if era not in AA_TREES:
        raise WikiError(f"unknown era {era!r}; known: {sorted(AA_TREES)}")
    titles: dict[str, set[str]] = {}
    for tree in AA_TREES[era]:
        for sub in category_members(tree, "subcat"):
            tier = sub.replace("Category:", "").replace(" AAs", "").strip().lower()
            if not classtree.is_target(tier):
                continue      # housekeeping categories ("AA category reviewed")
            for page in category_members(sub):
                titles.setdefault(page, set()).add(tier)
            time.sleep(PAUSE_S)
        time.sleep(PAUSE_S)
    return titles


UPSERT = (
    "INSERT INTO wiki_abilities (name, page_title, kind, era, tiers, line, "
    "activated, recast_s, power, target, descr, effects, fetched_ts) "
    "VALUES (:name,:page_title,:kind,:era,:tiers,:line,:activated,:recast_s,"
    ":power,:target,:descr,:effects,:fetched_ts) "
    # (name, kind) — a name really can be two abilities, so a deity miracle
    # must not overwrite the AA or spell that shares its name
    "ON CONFLICT(name, kind) DO UPDATE SET "
    "page_title=excluded.page_title, era=excluded.era, "
    "tiers=excluded.tiers, line=excluded.line, activated=excluded.activated, "
    "recast_s=excluded.recast_s, power=excluded.power, target=excluded.target, "
    "descr=excluded.descr, effects=excluded.effects, "
    "fetched_ts=excluded.fetched_ts")


def collect_deity_titles() -> dict[str, set[str]]:
    """{page title -> {}} for every blessing and miracle. No class tiers — a
    deity grants to whoever worships it, not to a class."""
    titles: dict[str, set[str]] = {}
    for cat in DEITY_TREES:
        for page in category_members(cat):
            titles.setdefault(page, set())
        for sub in category_members(cat, "subcat"):
            for page in category_members(sub):
                titles.setdefault(page, set())
            time.sleep(PAUSE_S)
        time.sleep(PAUSE_S)
    return titles


# ---------- zones ----------
#
# The other thing the wiki knows that nothing here does: which expansion a zone
# arrived with, and whether it is a raid. A log line says "Castle Mistmoore"
# and nothing else, so grouping notes by era is a question only reference data
# can answer — and answering it from memory is how The Emerald Halls ends up
# filed under Kingdom of Sky. See `backend/zones.py` for the read side.

ZONE_CATEGORY = "Category:Zones"


def collect_zone_titles() -> list[str]:
    """Every zone page on the wiki. One category, no subcategory walk: the
    per-expansion categories hold only the overland zones, so the raid
    instances — which is all this is for — are not in them."""
    return category_members(ZONE_CATEGORY)


def parse_zone(page_title: str, wikitext: str) -> dict | None:
    """The four facts an `IZoneInformation` box carries that we want.

    `introduced` is the expansion, and a page without one is not a zone record
    we can use — better absent than guessed. `instance` says Raid/Group/Solo
    and `zdiff` says x2/x4, which together are how "raid zones only" is decided
    without a second list to maintain."""
    if is_disambiguation(wikitext):
        return None
    text = _COMMENT.sub("", wikitext or "")
    era = _field(text, "introduced")
    if not era:
        return None
    return {
        "zone": log_name(page_title),
        "page_title": page_title,
        "era": era,
        "instance": _field(text, "instance") or None,
        "size": _field(text, "zdiff") or None,
    }


# A zone the wiki files under a live update rather than an expansion — the
# infobox says `introduced = LU22`, and the update's own page carries the date
# that places it. `{{LU|22|date=April 13, 2006}}`.
_LU_ERA = re.compile(r"^LU\s*(\d+)$", re.I)
_LU_DATE = re.compile(r"\{\{\s*LU\s*\|[^|}]*\|\s*date\s*=\s*([^|}]+)", re.I)
_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
# Six years of patch notes typed by different hands: "April 13, 2006",
# "December 20th 2006", "April 17,2011", "2/28/2007". All four are in there.
_WRITTEN = re.compile(r"([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})")
_NUMERIC = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def live_update_number(era: str) -> int | None:
    m = _LU_ERA.match((era or "").strip())
    return int(m.group(1)) if m else None


def parse_date(text: str) -> str | None:
    """A wiki date in any of the forms above -> ISO, or None."""
    m = _NUMERIC.search(text or "")
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = _WRITTEN.search(text or "")
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    return (f"{m.group(3)}-{_MONTHS.index(m.group(1).lower()) + 1:02d}"
            f"-{int(m.group(2)):02d}")


def live_update_dates(numbers) -> dict[str, str]:
    """{"LU22": "2006-04-13"} for the updates asked about.

    ISO because it is compared against expansion launch dates as text, and
    "April 13, 2006" sorts alphabetically, which is not a date order."""
    titles = {f"Update:{n:02d}": n for n in sorted(set(numbers))}
    out: dict[str, str] = {}
    names = list(titles)
    for i in range(0, len(names), BATCH):
        for title, text in fetch_wikitext(names[i:i + BATCH]).items():
            m = _LU_DATE.search(text or "")
            date = parse_date(m.group(1)) if m else None
            if not date:
                continue
            n = titles.get(title)
            if n is None:            # a redirect landed us on another title
                n = int(re.sub(r"\D", "", title) or 0)
            out[f"LU{n:02d}"] = date
        time.sleep(PAUSE_S)
    return out


def _merge(rows: list[dict]) -> list[dict]:
    """Collapse rows that share a (name, kind) by UNIONING their class tiers.

    The wiki gives the same AA one page per class that gets it —
    `Enhance: Cure (Mystic)`, `(Templar)`, `(Warden)` — and all three print the
    same name in a log. That is one ability granted to three classes, which is
    precisely what `tiers` holds, so merging is the answer and overwriting was
    losing two thirds of it (66 pages were collapsing to 29 names). 29 names,
    not an ambiguity: it is the SAME ability."""
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["name"], r["kind"])
        prev = out.get(key)
        if prev is None:
            out[key] = r
            continue
        tiers = {t for t in (prev["tiers"] or "").split(",") if t}
        tiers |= {t for t in (r["tiers"] or "").split(",") if t}
        prev["tiers"] = classtree.normalize(",".join(sorted(tiers))) or None
        # a button on any of its pages is a button
        prev["activated"] = max(prev["activated"], r["activated"])
        prev["recast_s"] = prev["recast_s"] or r["recast_s"]
        prev["line"] = prev["line"] or r["line"]
        prev["effects"] = prev["effects"] or r["effects"]
    return list(out.values())


def _sync(conn, titles: dict[str, set[str]], kind: str, era: str,
          fetch, progress) -> int:
    rows, seen = [], 0
    ordered = sorted(titles)
    for i in range(0, len(ordered), BATCH):
        chunk = ordered[i:i + BATCH]
        for title, text in fetch(chunk).items():
            if is_disambiguation(text):
                continue          # a pointer page is not an ability
            rows.append(parse_page(title, text, kind, era, titles.get(title, set())))
        seen += len(chunk)
        if progress:
            progress(seen, len(ordered))
        if i + BATCH < len(ordered):
            time.sleep(PAUSE_S)
    merged = _merge(rows)
    with conn:
        conn.executemany(UPSERT, merged)
    return len(merged)


def sync_aas(conn, era: str = "eof", fetch=fetch_wikitext,
             collect=collect_aa_titles, progress=None) -> int:
    """Pull one expansion's AAs into `wiki_abilities`. -> rows written.

    `fetch` and `collect` are parameters so tests drive this with recorded
    fixtures and never touch the live wiki — the same rule the Census sync
    follows."""
    return _sync(conn, collect(era), "aa", era, fetch, progress)


def sync_deities(conn, fetch=fetch_wikitext, collect=collect_deity_titles,
                 progress=None) -> int:
    """Pull blessings and miracles. Always EoF — deities arrived with it."""
    return _sync(conn, collect(), "deity", "eof", fetch, progress)


# The wiki writes the SAME trigger grammar Census does — "On a melee hit this
# spell has a X% chance to cast Pirate Stab on target of attack" — so
# `census.effects` reads it unchanged, with one substitution: the wiki uses
# letter placeholders where Census has real numbers, because a wiki page covers
# every rank of the AA at once. Swapping them for a zero lets the parser find
# WHAT is cast (the part we need) while `chance_pct` stays honestly unknown.
_PLACEHOLDER = re.compile(r"(?<![\w.])[X-Z](?=%)")


def proc_targets(effects: str | None) -> list[tuple[str, str]]:
    """Effect bullets -> [(ability this casts, the trigger clause)].

    This is what makes an AA a proc SOURCE rather than just a row: `Avast Ye`
    is the rogue AA behind `Pirate Stab`, and nothing in Census says so."""
    from census.effects import parse_effect          # deferred: effects is heavy
    out = []
    for line in (effects or "").splitlines():
        text = _PLACEHOLDER.sub("0", line.lstrip("* ").strip())
        if not text:
            continue
        if not text.endswith("."):
            text += "."          # bullets often drop the stop the grammar needs
        p = parse_effect(text)
        if p.get("kind") == "proc" and p.get("casts"):
            out.append((p["casts"].strip(" '\""), p.get("trigger") or ""))
    return out


def sources_by_cast(conn, eras: tuple[str, ...] = DEFAULT_ERAS) -> dict[str, list[dict]]:
    """{ability name -> the wiki abilities that cast it}. The AA equivalent of
    `census.abilityreview.proc_sources`."""
    out: dict[str, list[dict]] = {}
    ph = ",".join("?" * len(eras))
    for r in conn.execute(
            f"SELECT name, kind, tiers, line, effects FROM wiki_abilities "
            f"WHERE effects IS NOT NULL AND era IN ({ph})", list(eras)):
        for cast, trigger in proc_targets(r["effects"]):
            if cast == r["name"]:
                continue        # a spell re-applying itself is not a proc source
            out.setdefault(cast, []).append({
                "source": r["name"], "kind": r["kind"],
                "source_class": r["tiers"] or "", "line": r["line"],
                "trigger": trigger,
            })
    return out


def by_name(conn, eras: tuple[str, ...] = DEFAULT_ERAS) -> dict[str, dict]:
    """Everything known, keyed by the name a log would print. Era-filtered, so
    a server that will never see The Shadow Odyssey never reads its abilities.

    A name can be two abilities — the fury spell `Tempest` and Karana's miracle
    both print as "Tempest", and 37 AA names collide with a blessing or miracle.
    The row carries `ambiguous` and the OTHER kinds when that happens, because
    the log genuinely cannot say which one it saw and a caller that silently
    picked would be inventing the answer. `suggest` refuses to be confident
    about an ambiguous name for exactly that reason."""
    ph = ",".join("?" * len(eras))
    rows: dict[str, list[dict]] = {}
    for r in conn.execute(
            f"SELECT * FROM wiki_abilities WHERE era IN ({ph}) ORDER BY kind",
            list(eras)):
        rows.setdefault(r["name"], []).append(dict(r))
    out = {}
    for name, group in rows.items():
        first = dict(group[0])
        first["ambiguous"] = len(group) > 1
        first["kinds"] = sorted({g["kind"] for g in group})
        out[name] = first
    return out
