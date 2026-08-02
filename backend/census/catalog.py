"""ability_catalog: which log ability names belong to players vs pet kits, and
which fire on their own (procs) rather than by a deliberate cast.

Two sources, and curated always wins:
- `census`: every cached spell contributes its name + base_name as a player
  ability; every "may cast <X> on ..." effect contributes <X> as a proc — the
  effect grammar is the proc flag the plan called for.
- `curated`: pet-kit ability names (pets aren't in the Census spell
  collection) and buff/item procs observed in real logs. Verified against
  bobby.txt — every entry below appears there under a pet possessive chain or
  as YOUR damage with zero prepare lines all night.

Consumers: fit.spellbook drops pet-kit names from the player join (the
Master's-Strike class of misjoin), raidreport's engagement classifier never
anchors on a proc ability.
"""

# Pet-kit abilities seen under pet possessive chains in bobby.txt
# (necromancer Grim Sorcerer + scout pet + swarm pets, conjuror equivalents).
CURATED_PET_ABILITIES = (
    # necromancer mage pet (Grim Sorcerer)
    "Grim Wave", "Grim Embrace", "Grim Devastation", "Grim Lifetap",
    "Grim Bolt", "Grim Distortion", "Grisly Feedback",
    # necromancer scout pet
    "Throat Gash", "Poisoned Spike", "Shadowy Garrote", "Unseen Blade",
    "Shadow Step", "Shadestrike", "Quick Strike", "Clawing of the Soul",
    "Acidity", "Shockwave", "Shout",
    # necromancer swarm pets (Blighted Horde / Awaken Grave / Rending Frenzy)
    "Grave Decay", "Rapid Decay",
    # conjuror pets seen in the same raid
    "Protofire", "Graven Frenzy", "Graven Vanquishing", "Graven Scream",
)

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
)

_CENSUS_UPSERT = (
    "INSERT INTO ability_catalog (ability_name, class, unit, proc, source) "
    "VALUES (?,?,?,?, 'census') ON CONFLICT(ability_name) DO UPDATE SET "
    "class=excluded.class, unit=excluded.unit, proc=excluded.proc, "
    "source='census' WHERE ability_catalog.source IS NOT 'curated'")

_PROC_UPSERT = (
    "INSERT INTO ability_catalog (ability_name, class, unit, proc, source) "
    "VALUES (?,?, 'player', 1, 'census') ON CONFLICT(ability_name) DO UPDATE "
    "SET proc=1 WHERE ability_catalog.source IS NOT 'curated'")


def seed_curated(conn) -> None:
    """Idempotent curated seed; called at startup. Curated rows always win."""
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO ability_catalog "
            "(ability_name, class, unit, proc, source) VALUES (?,NULL,?,?,'curated')",
            [(n, "pet", 0) for n in CURATED_PET_ABILITIES]
            + [(n, "player", 1) for n in CURATED_PROCS])


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


def pet_ability_names(conn) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT ability_name FROM ability_catalog WHERE unit='pet'")}


def proc_ability_names(conn) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT ability_name FROM ability_catalog WHERE proc=1")}
