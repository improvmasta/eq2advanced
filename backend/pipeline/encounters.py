"""Encounter segmentation. The log has NO encounter markers — segments are
defined by damage-line gaps (>= GAP_S seconds) and hard-cut on zone changes.

`segment_events` is pure over parsed events and has no DB. `encounter_label`
needs resolved entities (it has to know which target is a mob), so callers in
the write path hand it already-resolved rows.
"""

from dataclasses import dataclass, field

GAP_S = 7           # combat silence that closes an encounter. ACT parity: its
                    # idle timeout is ~6s (gap >= 7 closes) — measured against
                    # Lindsay's Emerald Halls zone view, which ACT cut into 61
                    # encounters totalling 1:13:12; 25s merged chain pulls into
                    # 34 segments and inflated EncDPS denominators by ~8%.
                    # Anchors are damage AND avoided swings (a parried pull
                    # still holds the fight open, as in ACT).
TRAIL_GRACE_S = 10  # kills/deaths landing just after the last combat action still belong


@dataclass
class Segment:
    zone: str | None
    start_ts: int
    end_ts: int
    event_indices: list[int] = field(default_factory=list)
    name: str | None = None
    is_named: bool = False
    success: int | None = None


_TRAIL_TYPES = {"kill", "death", "pet_death", "rez", "revive"}


def _killer_is_player(killer: str | None, logger: str) -> bool:
    """Boss kills are credited to players; pets/allies die TO mobs. A killer
    that is articled/multi-word is a mob — its victim is a casualty, not a win."""
    if not killer:
        return False
    if killer == logger:
        return True
    if killer.lower().startswith(("a ", "an ", "the ")):
        return False
    return " " not in killer and killer[:1].isupper()


def _is_named_mob(victim: str, logger: str,
                  known_mobs: frozenset[str] = frozenset()) -> bool:
    v = victim.lower()
    if v.startswith(("a ", "an ")):
        return False
    if victim == logger:            # bare logger name = the pet
        return False
    if victim in known_mobs:        # behavioral refinement: one-word boss ("Venekor")
        return True
    if " " not in victim and victim[:1].isupper():
        return False                # single-token capitalized = player (mind control etc.)
    # pets as victims are not boss kills: any possessive whose remainder is
    # lowercase is a pet ("Oktavia's unswerving hammer", "Treyloth D'Kulvith's
    # blighted horde"). A capitalized remainder stays named ("Garanel's Shade",
    # "Birch's Defiled Soul" — a real named add).
    tokens = victim.split(" ")
    for i, tok in enumerate(tokens[:-1]):
        if tok.endswith("'s") or (tok.endswith("'") and len(tok) > 1):
            rest = tokens[i + 1]
            if rest and rest[0].islower():
                return False
            break
    return True


_ALLY_KINDS = frozenset(("player", "own_pet", "swarm_pet", "named_pet"))


def encounter_label(seg_events: list[dict], name_of, logger: str,
                    known_mobs: frozenset[str] = frozenset()
                    ) -> tuple[str, bool, int | None]:
    """ACT-style label for one segment: -> (name, is_named, success).

    An encounter is named after the enemy the raid FOUGHT, not the enemy that
    died. Naming from `has killed` (what we did until 2026-08-03) means a wipe
    produces no kill line, so it can never be named — every wipe collapsed into
    an anonymous "trash" row and `success` had no code path that could ever be
    0. Emerald Halls reported 9/9 named as a result, which is really "9 kills
    out of 9 kills": the Galiel Spirithoof and Farstride Unicorn wipes were
    sitting in the trash list.

    The enemy is the mob that took the most damage in the segment, which
    reproduces ACT's titles on Lindsay's Emerald Halls night (including the
    cases where the raid's damage went mostly into an add: ACT calls the Treah
    Greenroot wipe "a knotted guardian", and so do we). `success` is then real:
    1 if that enemy died, 0 if the raid engaged it and it did not."""
    dmg: dict[int, int] = {}
    for r in seg_events:
        if r["type"] != "damage" or r["tgt_kind"] != "mob" or r["tgt_entity"] is None:
            continue
        dmg[r["tgt_entity"]] = dmg.get(r["tgt_entity"], 0) + abs(r["amount"] or 0)
    if not dmg:
        # no enemy was ever hit — a stray segment (self-damage, a lone DoT tick)
        return "trash", False, None
    top = max(dmg, key=lambda eid: (dmg[eid], -eid))
    name = name_of(top) or "trash"
    killed = any(r["type"] == "kill" and r["tgt_entity"] == top
                 and r["src_kind"] in _ALLY_KINDS for r in seg_events)
    return name, _is_named_mob(name, logger, known_mobs), (1 if killed else 0)


def segment_events(events: list, logger: str, initial_zone: str | None = None,
                   known_mobs: frozenset[str] = frozenset()) -> list[Segment]:
    """Assign each event to a segment (or none). Events must be time-ordered.
    `initial_zone` seeds the zone for the live path, where earlier zone events
    have already been flushed to the DB. `known_mobs` comes from the behavioral
    refinement pass (pipeline.refine) so one-word bosses label their kills."""
    segments: list[Segment] = []
    current: Segment | None = None
    zone: str | None = initial_zone
    last_damage_ts: int | None = None

    def finalize(seg: Segment | None):
        if seg is None or not seg.event_indices:
            return
        # heals/wards/power inside the idle window stay in the encounter —
        # ACT keeps them too (its encounter is open until the timeout fires);
        # trimming them was tried 2026-08-03 and moved cures/EncHPS AWAY from
        # ACT's Emerald Halls numbers
        # label from named kills inside the segment; chain pulls can put more
        # than one named in a segment — list them all
        named: list[str] = []
        for i in seg.event_indices:
            ev = events[i]
            if (
                ev.type == "kill" and ev.tgt
                and _killer_is_player(ev.src.name if ev.src else None, logger)
                and _is_named_mob(ev.tgt, logger, known_mobs)
            ):
                if ev.tgt not in named:
                    named.append(ev.tgt)
        if named:
            # chain pulls of named-heavy trash (New Tunaria) can put a dozen
            # nameds in one segment — cap the label, keep the count. Real
            # multi-boss pulls (Unrest's 4-named wing) stay fully spelled out.
            if len(named) > 4:
                seg.name = " + ".join(named[:3]) + f" +{len(named) - 3} more"
            else:
                seg.name = " + ".join(named)
            seg.is_named = True
            seg.success = 1
        else:
            seg.name = "trash"
        segments.append(seg)

    for i, ev in enumerate(events):
        if ev.type == "zone":
            finalize(current)
            current = None
            last_damage_ts = None
            zone = ev.extra.get("zone")
            continue

        if ev.type in ("damage", "avoid"):
            if current is not None and last_damage_ts is not None and ev.ts - last_damage_ts >= GAP_S:
                finalize(current)
                current = None
            if current is None:
                current = Segment(zone=zone, start_ts=ev.ts, end_ts=ev.ts)
            current.event_indices.append(i)
            current.end_ts = ev.ts
            last_damage_ts = ev.ts
            continue

        if current is not None:
            if last_damage_ts is not None and ev.ts - last_damage_ts >= GAP_S:
                # damage silence exceeded: close, but let trailing kill/death
                # lines inside the grace window join the closed segment
                if ev.type in _TRAIL_TYPES and ev.ts - last_damage_ts <= GAP_S + TRAIL_GRACE_S:
                    current.event_indices.append(i)
                    continue
                finalize(current)
                current = None
                last_damage_ts = None
            else:
                current.event_indices.append(i)

    finalize(current)
    return segments
