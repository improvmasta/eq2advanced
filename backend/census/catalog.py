"""ability_catalog: which log ability names belong to players vs pet kits, and
which fire on their own (procs) rather than by a deliberate cast.

**A pet or proc label is a CLAIM, and only a curated row may make one.**
Everything else the machine works out is a candidate, kept beside the verdict
and shown only in the review export (`backend/tools/ability_review.py`).

That rule is the fix for the two ways the old catalog invented labels:

- *Pets.* `observe_pet_abilities` wrote `unit='pet'` for anything a pet-KIND
  entity cast, globally and permanently, off a single sighting. One bare name
  mistaken for a dumbfire took its whole spellbook down with it, and because
  `refine_bare_pets` reads this table back to decide what a pet is, the error
  fed itself: 228 names ended up marked pet, 108 of which Census knows as
  scribed player spells — `Ice Comet`, `Harm Touch`, `Raging Blow`. Necromancer
  looked right only because the curated seed already covered it.
- *Procs.* Census's `may cast <X> on ...` grammar names X for every buff that
  references it, which flags a class's OWN combat art the moment anything can
  fire it — `Berserk`, `Dragon Stance`, `Baffle`, `Knockdown`.

So `unit='pet'` and `proc=1` come from `CURATED_PET_ABILITIES` /
`CURATED_PROCS` and nowhere else. Both lists were verified against bobby.txt —
every entry appears there under a pet possessive chain, or as YOUR damage with
zero prepare lines all night. `pet_seen` and `proc_candidate` hold what the
machine noticed, so promoting a name after review is editing one tuple here.

Consumers: fit.spellbook drops pet-kit names from the player join (the
Master's-Strike class of misjoin), raidreport's engagement classifier never
anchors on a proc ability.
"""

# Pet-kit abilities. The three summoner kits below are GROUND TRUTH rather
# than sightings: Lindsay fought one pet per fight on 2026-08-11 (session 127)
# and cast nothing of his own, so each fight holds one kit and nothing else.
# The frontend's PET_ARCHETYPE (BreakdownTable.jsx) splits this flat list into
# those kits; a name may only be there if it is here.
CURATED_PET_ABILITIES = (
    # necromancer mage pet
    "Grim Wave", "Grim Embrace", "Grim Devastation", "Grim Lifetap",
    "Grim Bolt", "Grim Distortion",
    # necromancer scout pet
    "Throat Gash", "Poisoned Spike", "Shadowy Garrote", "Unseen Blade",
    "Shadestrike", "Acidity",
    # necromancer fighter pet. Filed as a CONJUROR's until Lindsay's fighter
    # fight showed the whole Graven kit under a necromancer's pet — and every
    # one of the 21 windows that has it also has Shout and Grisly Feedback,
    # which is the defensive stance, not a conjuror.
    "Graven Strike", "Graven Scream", "Graven Breath", "Graven Frenzy",
    "Graven Assault", "Graven Vanquishing",
    # Cast by whichever pet is out: the pet STANCES (defensive = Shout +
    # Grisly Feedback, offensive = Clawing of the Soul). All three fired for
    # all three of his pets, so they say a pet acted and never which one.
    "Shout", "Grisly Feedback", "Clawing of the Soul",
    # necromancer swarm pets (Blighted Horde / Awaken Grave / Rending Frenzy)
    "Grave Decay", "Rapid Decay",
    # conjuror swarm pet
    "Protofire",
    # Conjuror pet kits, settled the other way round: Lindsay wrote down the
    # conjuror's OWN book and Census confirms every entry of it, so what is
    # left over on a conjuror's line is the pet. Fire and air are an either/or,
    # and the two blocks anti-correlate at <=0.15 over 419 raid windows.
    # conjuror fire (mage) pet
    "Searing Flames", "Shocking Flames", "Igneous Flames", "Wave of Flames",
    "Sphere of Flames", "Storm of Flames",
    # conjuror air (scout) pet
    "Aery Whip", "Wisp Blade", "Storm Surge", "Furystorm", "Galestorm",
    "Thunderous Attack",
    # conjuror earth (fighter) pet — nobody raids it, so this is everything it
    # has ever been seen to cast
    "Telluric Bash", "Telluric Retaliation",
)
# Not claimed, on purpose: "Shadow Step" and "Shockwave". The pet is what
# swings them, but the OWNER presses them (Lindsay, 2026-08-11) — which is why
# they show up across all three archetypes' raid fights and in none of the
# three where he cast nothing himself. A button the player pressed is the
# player's, so they are never labelled pet, never folded into a pet row and
# never counted as pet damage (`PET_COMMANDED`, frontend lib/stats.js).
#
# Dropped 2026-08-11: "Quick Strike". This table is keyed by NAME alone, and
# that name belongs to mobs and players at least as much as to a pet — 454 mob
# rows and 518 player rows against 6 pet-cast ones across the real database —
# so claiming it hung a "pet cast" badge on everyone else's combat art.

# Buff/item procs that print as a player's own ability line. Observed as YOUR
# damage in bobby.txt with zero `You prepare` lines across the whole night and
# no matching scribed spell.
CURATED_PROCS = (
    "Lich's Siphoning",          # Lich (necro buff) drain proc
    "Dissonant Note", "Precise Note",   # bard buff procs
    "Dynamism",
    "Fae Fires",
    "Theurgist's Detonation",
    "Animated Dagger",
    "Smite of Consistency",
    "Overclocked Lifestone",
    "Najena's Empowerment",
    "Arcane Fury",
    "Arcane Storm",
    # Conjuror buff procs, each confirmed by the caster's own Census effect
    # text rather than by where the damage landed:
    #   Fire Seed   "On a combat hit this spell has a 20% chance to cast Seed
    #                of Fire on target of attack" (+ Blooming Flames on death)
    #   Flameshield "When damaged with a melee weapon this spell will cast
    #                Flameshield on target's attacker"
    # Fire Seed and Flameshield go on an ALLY, so the damage line lands under
    # whoever was wearing the buff, not the conjuror who cast it.
    "Seed of Fire", "Blooming Flames", "Flameshield",
    # Deity procs — cast by the PLAYER and available to anyone who worships
    # there, so they belong to no class (Lindsay uses Ro's Flames on a
    # necromancer; Census files it under Ro's Fury with no class at all)
    "Ro's Flames", "Incinerate",
)

# `class` carries two different meanings depending on which statement wrote it,
# and confusing them marks a class's own spell as gear. A spell record names
# the classes that SCRIBE the ability (scribed=1); a "may cast X" effect names
# the classes whose buff FIRES X, which says nothing about who can press it
# (scribed stays 0). Only the first kind can clear a proc flag.
_CENSUS_UPSERT = (
    "INSERT INTO ability_catalog (ability_name, class, unit, proc, scribed, source) "
    "VALUES (?,?,?,?, 1, 'census') ON CONFLICT(ability_name) DO UPDATE SET "
    "class=excluded.class, unit=excluded.unit, proc=excluded.proc, scribed=1, "
    "source='census' WHERE ability_catalog.source IS NOT 'curated'")

# A "may cast X" effect is a CANDIDATE, never the verdict — see the module
# docstring. `proc_class` keeps whose buff fires it, which is a different
# question from `class` (who scribes it) and used to be written over the top of
# it; the review export prints both side by side.
_PROC_UPSERT = (
    "INSERT INTO ability_catalog "
    "(ability_name, class, unit, proc, proc_candidate, proc_class, scribed, source) "
    "VALUES (?, NULL, 'player', 0, 1, ?, 0, 'census') ON CONFLICT(ability_name) "
    "DO UPDATE SET proc_candidate=1, proc_class=excluded.proc_class")


def seed_curated(conn) -> None:
    """Idempotent curated seed; called at startup. Curated rows always win, and
    they are now the ONLY rows that carry a pet or proc verdict.

    A name DROPPED from either tuple is RETIRED here, back to a candidate. The
    seed is the whole claim, so without this an edit to these lists is a no-op
    on any database that has already been seeded — `reset_verdicts` runs first
    but deliberately spares `source='curated'`, and the retired name would have
    kept its badge forever. A human ruling still outranks both (`_PET_NAMES`)."""
    claimed = CURATED_PET_ABILITIES + CURATED_PROCS
    with conn:
        conn.executemany(
            "INSERT INTO ability_catalog "
            "(ability_name, class, unit, proc, source) VALUES (?,NULL,?,?,'curated') "
            "ON CONFLICT(ability_name) DO UPDATE SET "
            "unit=excluded.unit, proc=excluded.proc, source='curated'",
            [(n, "pet", 0) for n in CURATED_PET_ABILITIES]
            + [(n, "player", 1) for n in CURATED_PROCS])
        conn.execute(
            "UPDATE ability_catalog SET "
            "  pet_seen = MAX(pet_seen, CASE WHEN unit='pet' THEN 1 ELSE 0 END), "
            "  proc_candidate = MAX(proc_candidate, proc), "
            "  unit = 'player', proc = 0, source = 'observed' "
            "WHERE source='curated' AND ability_name NOT IN "
            f"({','.join('?' * len(claimed))})", claimed)


def reset_verdicts(conn) -> int:
    """Demote every machine-written pet/proc label to a candidate. Idempotent,
    runs at startup right before `seed_curated` puts the curated ones back.

    This is what actually clears the badges off a database that has already
    learned wrong: the labels live in this table, not in the parsed rows, so
    nothing has to be reparsed for a raid page to stop calling Ice Comet a pet
    ability. The evidence is not thrown away — an `observed` pet row becomes
    `pet_seen`, a census proc becomes `proc_candidate` — so the review export
    still ranks exactly what was demoted. -> rows demoted."""
    with conn:
        cur = conn.execute(
            "UPDATE ability_catalog SET "
            "  pet_seen = MAX(pet_seen, CASE WHEN unit='pet' THEN 1 ELSE 0 END), "
            "  proc_candidate = MAX(proc_candidate, proc), "
            "  proc_class = COALESCE(proc_class, CASE WHEN proc=1 AND scribed=0 "
            "                                        THEN class END), "
            "  unit = 'player', proc = 0 "
            "WHERE source IS NOT 'curated' AND (unit='pet' OR proc=1)")
    return cur.rowcount


def backfill_scribed(conn) -> int:
    """One-time repair for catalogs written before `scribed` existed: a name
    that matches a cached Census spell record IS scribed, and that record's
    class list is the one that means "who can press this". Idempotent, cheap,
    and it only ever adds knowledge — rows with no matching spell stay as they
    are. -> rows repaired."""
    with conn:
        cur = conn.execute(
            "UPDATE ability_catalog SET scribed=1, class=COALESCE(("
            "  SELECT cs.class FROM census_spells cs "
            "  WHERE cs.name = ability_catalog.ability_name "
            "     OR cs.base_name = ability_catalog.ability_name LIMIT 1), class) "
            "WHERE scribed=0 AND source='census' AND EXISTS ("
            "  SELECT 1 FROM census_spells cs "
            "  WHERE cs.name = ability_catalog.ability_name "
            "     OR cs.base_name = ability_catalog.ability_name)")
    return cur.rowcount


def upsert_from_spells(conn, recs: list[dict]) -> None:
    """Catalog rows for freshly cached census spells. Runs inside the caller's
    transaction (sync.py)."""
    from census.effects import parse_effects
    from census.sync import base_name    # deferred: sync imports this module
    spell_rows, proc_rows = [], []
    for rec in recs:
        name = rec.get("name") or ""
        if not name:
            continue
        classes = ",".join(sorted((rec.get("classes") or {}).keys()))
        for n in {name, base_name(name)}:
            spell_rows.append((n, classes, "player", 0))
        for eff in parse_effects(rec.get("effect_list")):
            if eff.get("kind") == "proc" and eff.get("casts"):
                proc_rows.append((eff["casts"], classes))
    conn.executemany(_CENSUS_UPSERT, spell_rows)
    conn.executemany(_PROC_UPSERT, proc_rows)


def observe_pet_abilities(conn, names: set[str], session_id: int) -> None:
    """Learn-back from a parse: record which SESSIONS saw a pet-kind entity
    cast each name. Runs inside the caller's transaction.

    This used to write `unit='pet'` outright, and that is the bug this module's
    docstring is about — the sighting it learns from is only as good as the
    entity classification behind it, and one bare name mistaken for a dumbfire
    brought its whole spellbook in. It is evidence now: no label until a human
    moves the name into `CURATED_PET_ABILITIES`.

    Keyed by session rather than counted, so a reparse re-states the same
    evidence instead of inflating it — "seen in 4 raids" has to keep meaning
    four raids after the PARSE_VERSION sweep runs."""
    if not names:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO ability_pet_sightings (ability_name, session_id) "
        "VALUES (?,?)", [(n, session_id) for n in names])
    # denormalized onto the catalog so every reader (and the export) can rank
    # candidates without a join
    conn.executemany(
        "INSERT INTO ability_catalog "
        "(ability_name, class, unit, proc, pet_seen, source) "
        "VALUES (?, NULL, 'player', 0, 0, 'observed') "
        "ON CONFLICT(ability_name) DO NOTHING", [(n,) for n in names])
    conn.executemany(
        "UPDATE ability_catalog SET pet_seen = "
        "(SELECT COUNT(*) FROM ability_pet_sightings p "
        " WHERE p.ability_name = ability_catalog.ability_name) "
        "WHERE ability_name = ?", [(n,) for n in names])


# The precedence ladder, in SQL, once: a hand-written ruling wins, the curated
# seed answers what has never been ruled on, and nothing else gets a vote. Both
# readers below are the ONLY doors to these two labels — encounters_api calls
# them rather than keeping its own copy, so a ruling reaches the badges, the
# rollup's press counting and the coach in one edit.
_PET_NAMES = (
    "SELECT ability_name FROM ability_rulings WHERE unit='pet' "
    "UNION SELECT ability_name FROM ability_catalog WHERE unit='pet' "
    "AND ability_name NOT IN (SELECT ability_name FROM ability_rulings)")

_PROC_NAMES = (
    "SELECT ability_name FROM ability_rulings WHERE fires='proc' "
    "UNION SELECT ability_name FROM ability_catalog WHERE proc=1 "
    "AND ability_name NOT IN (SELECT ability_name FROM ability_rulings)")


def pet_ability_names(conn) -> set[str]:
    return {r[0] for r in conn.execute(_PET_NAMES)}


def proc_ability_names(conn) -> set[str]:
    return {r[0] for r in conn.execute(_PROC_NAMES)}


def press_inputs(conn) -> tuple[dict[str, float], frozenset[str]]:
    """What the rollup needs to tell a button press from a tick: (ability name
    -> tick period in seconds, ability names that fire themselves).

    Census records the period per spell TIER; the log only ever prints the
    numeral-stripped base name, so the tiers are collapsed on `base_name` and
    the shortest period wins — an upgrade that ticks faster would otherwise
    have its extra ticks read as extra presses."""
    periods: dict[str, float] = {}
    for name, period in conn.execute(
            "SELECT base_name, MIN(dmg_period_s) FROM census_spells "
            "WHERE dmg_period_s IS NOT NULL AND dmg_period_s > 0 GROUP BY base_name"):
        periods[name] = float(period)
    return periods, frozenset(proc_ability_names(conn))


