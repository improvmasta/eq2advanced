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
        # Nobody hit an enemy. If one was hitting US it still has a name, and
        # ACT uses it: its export of the corpse-tick stub after the Freeport
        # pull is titled "Velna T`Kril", not "trash".
        for r in seg_events:
            if (r["type"] == "damage" and r["src_kind"] == "mob"
                    and r["src_entity"] is not None):
                name = name_of(r["src_entity"])
                if name:
                    return name, _is_named_mob(name, logger, known_mobs), None
        # a stray segment (self-damage, an expiry) — nothing to name it after
        return "trash", False, None
    top = max(dmg, key=lambda eid: (dmg[eid], -eid))
    name = name_of(top) or "trash"
    killed = any(r["type"] == "kill" and r["tgt_entity"] == top
                 and r["src_kind"] in _ALLY_KINDS for r in seg_events)
    return name, _is_named_mob(name, logger, known_mobs), (1 if killed else 0)


def split_trailing_corpse(seg: Segment, rows: list[dict]) -> list[Segment]:
    """Drop a dead mob's leftover ticks off the end of a segment.

    A DoT the mob landed before it died keeps ticking on the raid for a few
    seconds after the kill. The silence rule counts those ticks as combat, so
    the fight's clock runs past the kill: ACT read Lindsay's Freeport pull as
    28s and we read 32s — 12% off the EncDPS on a fight that short, plus the
    tick's damage on the mob's row.

    ACT ends the fight at the kill and opens a NEW encounter for the tick (its
    tree shows the 28s pull, then a [00:00] stub 4s later). This reproduces
    that: the clock stops at the last real beat, and a trailing tick that
    carries damage becomes its own segment.

    It is deliberately a SUFFIX operation. Cutting at every point where the
    engaged mobs were all dead splits chain pulls in half — measured against
    ACT's Emerald Halls zone view (61 encounters), mid-fight variants produced
    74 to 149. This one produces 62, and re-times 2 of those 60 fights.

    Nothing is discarded: every event stays in some encounter, so zone totals
    are untouched. Trimming trailing events outright was tried on 2026-08-03
    and regressed cures/EncHPS — see ARCHITECTURE.md.

    `rows[k]` is the resolved row for `seg.event_indices[k]`.
    """
    if not rows:
        return [seg]
    dead = {r["tgt_entity"] for r in rows
            if r["type"] == "kill" and r["tgt_kind"] == "mob"}

    last = None                 # last beat: the group's last action
    for k, r in enumerate(rows):
        if r["type"] == "kill" and r["tgt_kind"] == "mob":
            last = k
        elif r["type"] in ("damage", "avoid") and r["src_kind"] != "mob":
            last = k
    if last is None:
        return [seg]

    # the killing blow is the fight's last beat, so the clock stops there even
    # when the kill line is the final event — ACT's 28s on the Freeport pull is
    # start-to-kill, not start-to-last-damage
    fight_end = rows[last]["ts"]
    if last == len(rows) - 1:
        if fight_end == seg.end_ts:
            return [seg]
        return [Segment(zone=seg.zone, start_ts=seg.start_ts, end_ts=fight_end,
                        event_indices=list(seg.event_indices))]
    # the kill's own second belongs to the fight (a lifetap heal on the killing
    # blow is part of it, and ACT's encounter ends on that second)
    cut = last + 1
    while cut < len(rows) and rows[cut]["ts"] <= fight_end:
        cut += 1
    if cut >= len(rows):
        return [Segment(zone=seg.zone, start_ts=seg.start_ts, end_ts=fight_end,
                        event_indices=list(seg.event_indices))]

    head = Segment(zone=seg.zone, start_ts=seg.start_ts, end_ts=fight_end,
                   event_indices=list(seg.event_indices[:cut]))
    tail_rows = rows[cut:]
    hitters = {r["src_entity"] for r in tail_rows if r["type"] == "damage"}
    if not hitters or not hitters <= dead:
        # Only a CORPSE opens a new encounter. On a wipe the mobs are alive and
        # still swinging at the bodies: ACT keeps that damage in the fight and
        # stops the clock anyway (its knotted guardian wipe is 40s while the
        # hits run 3s longer), so the tail rides along untrimmed.
        head.event_indices = list(seg.event_indices)
        return [head]
    tail_end = seg.start_ts
    for r in tail_rows:
        if r["type"] in ("damage", "avoid"):
            tail_end = r["ts"]
    return [head, Segment(zone=seg.zone, start_ts=tail_rows[0]["ts"],
                          end_ts=max(tail_end, tail_rows[0]["ts"]),
                          event_indices=list(seg.event_indices[cut:]))]


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
