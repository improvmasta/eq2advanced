"""What we know about each ability, and how sure we are — the data behind the
Abilities admin page.

Two labels used to be inferred here and both were wrong at scale.
`observe_pet_abilities` wrote `unit='pet'` off one sighting, so a single bare
name mistaken for a dumbfire took its whole spellbook with it (228 names marked
pet, 108 of them Census-scribed player spells — `Ice Comet`, `Harm Touch`,
`Raging Blow`). Census's "may cast X" grammar flagged `Berserk`, `Dragon
Stance` and `Baffle`, which are the class's own buttons. Necromancer looked
right only because the curated seed already covered it.

So nothing here decides anything. It gathers evidence, says how strong it is,
and hands the undecided ones to a person. The precedence that survives is
`ability_rulings` > the curated seed > no label at all.

**Provenance comes from Census, not from a guess.** Every spell record carries
`given_by`, `type`, `alternate_advancement` and `deity`, and a proc's source
spell is findable by its effect text — so `Fae Fires` is not "a gear proc", it
is `Fae Fire`, a level 35 FURY spell that reads "On any combat or spell hit
this spell will cast Fae Fires on target of attack." What Census genuinely
cannot answer today is gear and deity: `census_items` holds 143 rows (we only
fetch an item something already referenced) and exactly 2 spells carry the
deity flag, because the ingest walks class spell pages. An ability no cached
spell casts is therefore "granted by something we haven't ingested" — gear, an
AA or a deity — and saying which is a person's job until those pulls exist.

Nothing here returns a player name, an entity or a row from anybody's parse.
The per-ability counters are site-wide sums and the classes are class names,
which keeps the admin console's promise (`routers/admin_api.py`): it reports on
the site without reading it.
"""

import json
import re
from collections import defaultdict

import classtree
import gamewiki
from census.effects import parse_effect

PET_KINDS = ("own_pet", "swarm_pet", "named_pet")

# Census `given_by` -> what kind of thing granted it. `alternate_advancement`
# and `deity` are separate booleans on the same record and win over this map.
GIVEN_BY_KIND = {
    "spellscroll": "spell",
    "class": "spell",
    "classtraining": "spell",
    "alternateadvancement": "aa",
    "tradeskillclass": "tradeskill",
    "racialtradition": "racial",
    "racialinnate": "racial",
    "race": "racial",
    "focusabilities": "focus",
}

# A pet ability seen under real possessive chains this many times is not an
# accident of one misread name.
PET_DEFINITE_MIN = 3
# Fired this often by the logger with no prepare line and it was never pressed.
PROC_HITS_MIN = 5
# Bare-name "pet" sightings have to be worth this much of the player casts
# before they are evidence of anything. Below it they are the refiner having
# misread one name in one raid.
PET_GUESS_SHARE = 10


def _definite_pet(name: str) -> bool:
    """Is this entity name a pet by GRAMMAR alone, with no inference?

    `<Owner>'s <lowercase remainder>` is the swarm/dumbfire form and nothing
    else produces it (parser/subjects.py). A bare capitalized token (`Viber`)
    is the parser's GUESS and is deliberately not counted — that guess is what
    put a level 70 shadowknight's spellbook in the pet catalog."""
    m = re.search(r"\S+?'s?\s+(?P<rest>.+)$", name)
    return bool(m) and m.group("rest")[:1].islower()


def _spell_kind(rec: dict) -> str:
    """One spell record -> how that spell is granted."""
    if rec.get("deity"):
        return "deity"
    if rec.get("alternate_advancement"):
        return "aa"
    return GIVEN_BY_KIND.get(rec.get("given_by") or "", "spell")


# a spell that is BOTH scroll-bought and class-granted is one ability with two
# tiers, not two answers; the more specific reading wins
_KIND_RANK = ("deity", "aa", "racial", "focus", "tradeskill", "spell")


def proc_sources(conn) -> dict[str, list[dict]]:
    """proc'd ability name -> the spells whose effect text casts it, each with
    its own provenance.

    Re-parsed from the stored `raw` rather than the cached `parsed_effects`,
    because the trigger grammar was widened after those rows were written: it
    used to read only "may cast", dropping every guaranteed "will cast" —
    `Shout`, `Thorns`, `Grisly Feedback`, `Prismatic Shock`, `Thunder Fist`."""
    by_name: dict[str, dict[str, dict]] = defaultdict(dict)
    for name, cls, blob, pe in conn.execute(
            "SELECT name, class, json, parsed_effects FROM census_spells "
            "WHERE parsed_effects IS NOT NULL"):
        try:
            effects = json.loads(pe) or []
            rec = json.loads(blob) if blob else {}
        except (TypeError, ValueError):
            continue
        kind = _spell_kind(rec)
        base = re.sub(r"\s+[IVX]+$", "", name)      # collapse the tier numerals
        for e in effects:
            raw = (e.get("raw") or "").strip()
            if not raw:
                continue
            parsed = parse_effect(raw)
            if parsed.get("kind") != "proc" or not parsed.get("casts"):
                continue
            slot = by_name[parsed["casts"]]
            prev = slot.get(base)
            if prev and _KIND_RANK.index(prev["kind"]) <= _KIND_RANK.index(kind):
                continue
            slot[base] = {
                "source": base, "source_class": cls or "", "kind": kind,
                "trigger": parsed.get("trigger") or "",
                "mode": parsed.get("mode") or "will",
                "per_min": parsed.get("per_min"),
            }
    return {k: sorted(v.values(), key=lambda s: s["source"]) for k, v in by_name.items()}


def grant_summary(srcs: list[dict]) -> dict:
    """The one-line reading of what fires an ability and how, from its sources.

    Empty `kind` means no cached spell casts it — which is a real finding, not
    a blank: it is gear, an AA or a deity blessing, and the ingest that would
    tell them apart does not exist yet."""
    if not srcs:
        return {"grant_kind": "", "grant_name": "", "grant_class": "", "trigger": ""}
    best = min(srcs, key=lambda s: (_KIND_RANK.index(s["kind"]),
                                    not s["per_min"], s["mode"] != "may"))
    classes = sorted({c for s in srcs for c in (s["source_class"] or "").split(",") if c})
    bits = [best["trigger"].lower() or "(no condition)",
            "may fire" if best["mode"] == "may" else "always fires"]
    if best["per_min"]:
        bits.append(f"~{best['per_min']:g}/min")
    return {
        "grant_kind": best["kind"],
        "grant_name": "; ".join(sorted({s["source"] for s in srcs})[:4]),
        "grant_class": ",".join(classes),
        "trigger": ", ".join(bits),
    }


def gather(conn) -> dict[str, dict]:
    """Every ability any parse has ever produced, with its evidence. Keyed by
    ability name."""
    catalog = {r["ability_name"]: dict(r) for r in conn.execute(
        "SELECT ability_name, class, unit, proc, scribed, pet_seen, "
        "proc_candidate, source FROM ability_catalog")}
    scribed = {r[0]: r[1] for r in conn.execute(
        "SELECT ability_name, class FROM ability_catalog "
        "WHERE scribed=1 AND class IS NOT NULL AND class != ''")}
    rulings = {r["ability_name"]: dict(r) for r in conn.execute(
        "SELECT * FROM ability_rulings")}
    procs = proc_sources(conn)
    # The wiki covers what Census was never asked for — AAs above all. Two
    # different questions it answers: what an ability IS (`wiki`), and what
    # CASTS it (`wiki_src`), the latter being the AA equivalent of Census's
    # "may cast X" grammar. Era-filtered, so a level-70 server never reads a
    # Shadow Odyssey ability. See gamewiki.py.
    wiki = gamewiki.by_name(conn)
    wiki_src = gamewiki.sources_by_cast(conn)

    rows: dict[str, dict] = {}

    def row(ability: str) -> dict:
        if ability not in rows:
            cat = catalog.get(ability, {})
            srcs = procs.get(ability, [])
            # Census first — it has real numbers and rates; the wiki fills in
            # only where Census has nothing to say, which for AAs is always
            if not srcs:
                srcs = [{**s, "mode": "may", "per_min": None} for s in wiki_src.get(ability, [])]
            w = wiki.get(ability)
            rows[ability] = {
                "ability": ability,
                "curated_pet": int(cat.get("unit") == "pet"),
                "curated_proc": int(cat.get("proc") or 0),
                "scribed_by": scribed.get(ability, ""),
                "proc_candidate": int(bool(srcs) or bool(cat.get("proc_candidate"))),
                **grant_summary(srcs),
                "pet_sessions": cat.get("pet_seen") or 0,
                "pet_definite": 0, "pet_own": 0, "pet_guess": 0,
                "player_casts": 0, "mob_casts": 0,
                "prepare_lines": 0, "logger_hits": 0, "total_damage": 0,
                "_players": set(), "_classes": set(),
                "ruling": rulings.get(ability),
                # what the ability IS, if the wiki knows it at all
                "wiki_kind": w["kind"] if w else "",
                "wiki_tiers": (w["tiers"] or "") if w else "",
                "wiki_line": (w["line"] or "") if w else "",
                # None = the wiki has never heard of it, which is not the same
                # as "passive"; only a real page can answer pressed-or-not
                "activated": (w["activated"] if w else None),
                "recast_s": (w["recast_s"] if w else None),
                # the wiki has TWO abilities by this name and the log cannot
                # say which it saw — report it, never pick
                "wiki_ambiguous": bool(w and w.get("ambiguous")),
                "wiki_kinds": (w.get("kinds") if w else []) or [],
            }
        return rows[ability]

    for r in conn.execute(
            "SELECT ab.name AS ability, e.name AS entity, e.kind AS kind, "
            "       e.class_guess AS class_guess, c.name AS logger, "
            "       st.casts AS casts, st.hits AS hits, st.total AS total "
            "FROM encounter_ability_stats st "
            "JOIN entities e ON e.id = st.entity_id "
            "JOIN abilities ab ON ab.id = st.ability_id "
            "JOIN sessions s ON s.id = e.session_id "
            "LEFT JOIN characters c ON c.id = s.character_id"):
        name = r["ability"]
        if name.startswith("("):      # (melee)/(multi attack)/… are not abilities
            continue
        d = row(name)
        hits = r["hits"] or 0
        d["total_damage"] += r["total"] or 0
        if r["kind"] in PET_KINDS:
            if _definite_pet(r["entity"]):
                d["pet_definite"] += hits
            elif r["kind"] == "own_pet" and r["entity"] == r["logger"]:
                # the logger's own pet shares their name — certain by the
                # YOU/YOUR rule, since their own actions never print bare
                d["pet_own"] += hits
            else:
                d["pet_guess"] += hits
        elif r["kind"] == "player":
            d["player_casts"] += hits
            d["_players"].add(r["entity"])
            if r["class_guess"]:
                try:
                    g = json.loads(r["class_guess"]) or {}
                    if g.get("class"):
                        d["_classes"].add(g["class"])
                except (TypeError, ValueError):
                    pass
            if r["entity"] == r["logger"]:
                # `You prepare ...` prints for the logger and nobody else, so
                # this is the ONLY seat from which "it fired uncast" is provable
                d["prepare_lines"] += r["casts"] or 0
                d["logger_hits"] += hits
        elif r["kind"] == "mob":
            d["mob_casts"] += hits

    # a curated or ruled name no uploaded log happens to contain still belongs
    # in the review — a wrong hand-entry is the only kind that never self-corrects
    for name in set(catalog) | set(rulings):
        cat = catalog.get(name, {})
        if cat.get("unit") == "pet" or cat.get("proc") or name in rulings:
            row(name)

    for d in rows.values():
        d["distinct_players"] = len(d.pop("_players"))
        d["player_classes"] = sorted(d.pop("_classes"))
        d["suggest"], d["confidence"], d["why"] = suggest(d)
        d["classes"] = classes_for(d)
        d["settled"] = bool(d["ruling"])
    return rows


def classes_for(d: dict) -> list[str]:
    """Every SUBCLASS this ability might belong to, for grouping the admin page.

    Deliberately generous — one ability under three classes until somebody
    rules on it is the point, and an ability with no class at all is the
    interesting pile, not a gap. Order: who scribes it, whose buff fires it,
    who was seen using it, and a ruling's own grant last because it is the
    answer rather than a lead.

    Everything runs through `classtree.expand`, because a grant is not always a
    subclass: AAs are handed out at every tier of EQ2's tree, so a ruling
    against `predator` has to appear under BOTH ranger and assassin, and a
    `scout` AA under all seven. Census only ever writes subclass names — it
    expands groups before we see them — so this matters for the side a person
    fills in."""
    out: list[str] = []
    for src in (d["scribed_by"], d["grant_class"], d.get("wiki_tiers")):
        out += sorted(classtree.expand_all(src))
    out += d["player_classes"]
    if d["ruling"]:
        out += sorted(classtree.expand_all(d["ruling"]["grant_class"]))
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def suggest(d: dict) -> tuple[str, str, str]:
    """(suggest, confidence, why) — the reading and what it rests on.

    Conservative in both directions. `player` is the answer whenever nothing
    argues otherwise, because "no label" is the state this restores: a wrong
    badge is worse than a missing one. Only `high` is treated as settled by
    the admin page; everything else is put in front of a person."""
    if d["ruling"]:
        r = d["ruling"]
        what = "pet" if r["unit"] == "pet" else ("proc" if r["fires"] == "proc" else "player")
        src = (f"{r['grant_name']} ({r['grant_kind']})" if r.get("grant_name")
               else r.get("grant_kind") or "hand-set")
        return what, "ruled", f"decided by hand: {src}"

    scribed = d["scribed_by"]
    definite, guess, players = d["pet_definite"], d["pet_guess"], d["player_casts"]
    own, classes = d["pet_own"], d["player_classes"]
    single_class = len(classes) == 1

    # The curated seed IS a human answer — the same kind the page writes, just
    # older and living in a tuple instead of a table. It leaves the queue for
    # the same reason a ruling does; search still reaches it, which is how a
    # wrong one gets fixed.
    if d["curated_pet"]:
        return "pet", "curated", "curated: verified under pet possessive chains in bobby.txt"
    if d["curated_proc"]:
        if d["grant_kind"]:
            return ("proc", "curated",
                    f"curated, and Census agrees: {d['grant_name']} "
                    f"({d['grant_class'] or 'no class'} {d['grant_kind']}), {d['trigger']}")
        return ("proc", "curated",
                "curated: seen as YOUR damage with zero prepare lines all night, "
                "and no cached spell casts it — gear, an AA or a deity")

    # A handful of bare-name sightings against thousands of player casts is
    # noise, not a pet reading, and letting it speak first was hiding the proc
    # underneath: `Fae Fires` (95 guesses, 14833 player casts) came back
    # "player" on pet grounds while Census was naming Fae Fire the whole time.
    if guess and players and guess * PET_GUESS_SHARE < players:
        guess = 0

    # --- pet ---------------------------------------------------------------
    if definite >= PET_DEFINITE_MIN and not players:
        if scribed:
            return ("unclear", "low",
                    f"{definite} casts under owner's-pet possessives, but Census says "
                    f"{scribed} scribes it — and pets are not in the spell collection, "
                    f"so one of the two is wrong")
        return ("pet", "high",
                f"cast {definite}x under owner's-pet possessives, never by a player, "
                f"and no class scribes it")
    if definite >= PET_DEFINITE_MIN and players and not scribed:
        # a pet whose owner's name it shares lands in the player column BY
        # DESIGN (subjects.py conflates them), so player casts are not counter-
        # evidence here — the class spread is what tells them apart
        if single_class:
            return ("pet", "high",
                    f"{definite} casts under real pet possessives, and its "
                    f"{players} player casts are all {classes[0]} — the conflated-pet "
                    f"form, not {classes[0]}s pressing it")
        return ("unclear", "low",
                f"{definite} pet casts and {players} player casts across "
                f"{len(classes) or '?'} classes — a pet kit and a shared proc look "
                f"the same from here")
    if (own or guess) and not definite and not scribed and not d["logger_hits"] \
            and not d["prepare_lines"] and single_class and players:
        return ("pet", "medium",
                f"no possessive evidence, but every one of its {players} player casts "
                f"is {classes[0]}, the logger never produced it as a player, and "
                f"nobody scribes it — the shape of a named pet's kit")
    if definite and not players and not scribed:
        return ("pet", "low", f"only {definite} pet cast(s) — real evidence, but thin")
    if guess and not definite and not own:
        why = (f"the ONLY pet evidence is {guess} cast(s) by a bare capitalized name, "
               f"which is the parser's guess, not the grammar")
        return ("player", "high" if scribed else "medium",
                why + (f"; Census says {scribed} scribes it" if scribed else ""))

    # --- what the ability IS, when the wiki has a page for it ---------------
    #
    # This has to come BEFORE the prepare-line test, because that test is blind
    # in exactly this spot. `You prepare <X>` prints for spells and combat arts
    # and NOT for AA activations, so an AA you press produces logger hits with
    # no prepare line — the same fingerprint as a gear proc. It read `Lifeburn`
    # (a 5-minute recast the necromancer presses) as one, and 45 rows rested on
    # that same silence. A recast timer is proof of a button, and it exists
    # nowhere in the log.
    # A wiki page is matched by NAME, which is the weakest join there is, so it
    # only speaks when Census does not contradict it. `Tempest` is a fury spell
    # in these logs and Karana's miracle on the wiki; both print the same. A
    # Census spell record is the game saying a class scribes it, and that beats
    # a name that happens to match — so scribed names fall through to the class
    # reading and the wiki stays visible as evidence only. Same for a name the
    # wiki itself holds twice.
    wiki_speaks = not scribed and not d.get("wiki_ambiguous")
    if wiki_speaks and d["activated"] == 1:
        recast = f", {d['recast_s']:g}s recast" if d["recast_s"] else ""
        return ("player", "high",
                f"the wiki has this as an ACTIVATED {(d['wiki_kind'] or 'ability').upper()}"
                f"{recast} — a button, not a proc. The log cannot tell you that: "
                f"EQ2 prints no prepare line for an AA activation, so pressing "
                f"one looks exactly like something firing on its own")
    if wiki_speaks and d["activated"] == 0 and d["wiki_kind"] == "aa":
        return ("proc", "high" if d["logger_hits"] else "medium",
                f"a PASSIVE AA ({d['wiki_tiers'] or 'no tier'}"
                f"{', ' + d['wiki_line'] + ' line' if d['wiki_line'] else ''}) — "
                f"no recast and no cost, so firing on its own is how it works")

    # --- proc --------------------------------------------------------------
    if d["logger_hits"] >= PROC_HITS_MIN and not d["prepare_lines"]:
        if scribed and classes and (set(classes) & {c.strip() for c in scribed.split(",")}):
            return ("player", "high",
                    f"no prepare lines, but {scribed} scribes it and its casters are "
                    f"{'/'.join(classes)} — a pressed ability whose flavor line we "
                    f"don't read")
        why = f"{d['logger_hits']} hits from the logger with zero prepare lines"
        if d["grant_kind"]:
            why += (f"; Census: {d['grant_name']} "
                    f"({d['grant_class'] or 'no class'} {d['grant_kind']}), {d['trigger']}")
            return "proc", ("high" if not scribed else "medium"), why
        return ("proc", "medium",
                why + "; no cached spell casts it — gear, an AA or a deity, "
                      "and the ingest that would say which does not exist yet")
    if d["proc_candidate"] and scribed:
        return ("player", "high",
                f"Census grammar says {d['grant_name'] or 'something'} casts it, but "
                f"{scribed} SCRIBES it — a class's own button a buff can also fire")
    if d["proc_candidate"] and not d["logger_hits"]:
        return ("proc", "low",
                f"Census grammar only ({d['grant_name'] or 'a spell'} "
                f"{d['trigger'] or 'casts it'}), and no logger row to check for "
                f"prepare lines")

    if d["prepare_lines"]:
        return "player", "high", f"pressed {d['prepare_lines']}x (prepare lines)"
    if scribed:
        return "player", "high", f"Census: scribed by {scribed}, nothing suggests otherwise"
    if d["mob_casts"] and not players and not definite:
        return ("player", "high",
                f"only ever cast by mobs ({d['mob_casts']} hits) — no player label applies")
    if players and not d["logger_hits"]:
        # the common shape of the backlog: real player damage under a name no
        # cached spell record covers, and nobody who could prove it uncast has
        # uploaded a log. The log cannot close this one — Census can, once the
        # item and alternateadvancement pulls exist.
        who = "/".join(classes) if classes else "no resolved class"
        return ("unclear", "low",
                f"{players} player casts ({who}) but nothing in Census names it and "
                f"no logger ever produced it, so there are no prepare lines to check "
                f"— an AA, an item or a spell we have not ingested")
    return "unclear", "low", "no evidence either way"
