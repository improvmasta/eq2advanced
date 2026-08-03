"""Encounter segmentation. The log has NO encounter markers — segments are
defined by damage-line gaps (>= GAP_S seconds), hard-cut on zone changes, and
labeled from `has killed <Named>` events inside the segment.

Pure functions over parsed events; no DB. Used by both the bulk path and
(later) the incremental live path.
"""

from dataclasses import dataclass, field

GAP_S = 25          # damage silence that closes an encounter. 15s splits real
                    # fights: bobby.txt has 16-26s lulls INSIDE Estate of Unrest
                    # bosses (e.g. a 21s gap mid-Garanel); post-fight gaps in the
                    # fixture are all >=30s, so 25 merges lulls without merging fights
TRAIL_GRACE_S = 10  # kills/deaths landing just after the last damage still belong


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

        if ev.type == "damage":
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
