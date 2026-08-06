"""Class-specific statistics — the registry behind the Class tab.

Every other tab asks a question the whole raid answers: damage, healing, who
died. This one asks the question only one class can answer. "Was Jester's Cap
up on the assassin all fight" is meaningless for a templar, and a column that
is blank for twenty-five of twenty-six classes does not belong in the
combatant table. So each class gets its own panel, and the panel is empty
until somebody writes the metric for it.

A metric is one `@register(...)` decorated function. It declares its columns
and returns rows; nothing else in the app has to change — the endpoint
enumerates the registry and the frontend renders whatever columns come back.
That is the whole point of the shape: adding "Perfection of the Maestro
uptime" should be a function in this package, not a migration, an API change
and a component.

What a metric MUST NOT do is invent certainty. These stats live at the edge of
what a log can prove — a buff with no fade line, a proc nobody logs the source
of — so `blurb` is required. ONE LINE: the stat's limit, next to the number.
The reasoning goes in the metric's module docstring, not on the page.

Isolation is deliberate: a metric that raises is reported as a broken metric
and the rest of the tab still renders. One bad regex should not take out the
Class tab for every class in the raid.
"""

import json
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from coach.descriptive import archetype_for

log = logging.getLogger("classstats")

# The rendering vocabulary. A metric picks a unit per column and the frontend
# formats it; there is no per-metric component to write, and no HTML in here.
#   text  — left-aligned string (a name, a fight, a verdict)
#   num   — integer, thousands-separated
#   pct   — 0..100, rendered "62%" (send the number, not the string)
#   secs  — a span, rendered "1m 30s"
#   clock — an offset into the fight, rendered "2:47"
#   rate  — one decimal (per-second things)
UNITS = ("text", "num", "pct", "secs", "clock", "rate")

# Role order for the class rail — the order a raid frame reads, not alphabetical.
_ROLE_ORDER = {"tank": 0, "healer": 1, "utility": 2, "dps": 3}


def _names(ability: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize the ability filter to a hashable tuple (it is part of the
    per-request cache key)."""
    if ability is None:
        return ()
    if isinstance(ability, str):
        return (ability,)
    return tuple(sorted(set(ability)))


@dataclass(frozen=True)
class Column:
    """One column of a metric's table. `title` becomes the header tooltip."""
    key: str
    label: str
    unit: str = "text"
    title: str | None = None

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "unit": self.unit,
                "title": self.title}


@dataclass(frozen=True)
class Metric:
    key: str                      # unique within the class
    cls: str                      # class slug, lowercase ('troubador')
    label: str                    # panel heading ("Jester's Cap uptime")
    blurb: str                    # ONE line: what it cannot see
    columns: tuple[Column, ...]
    compute: Callable[["Ctx"], object]
    needs_events: bool = False    # true if compute() calls ctx.events()


_REGISTRY: dict[str, list[Metric]] = {}


def register(*, key: str, cls: str, label: str, blurb: str,
             columns: Sequence[Column], needs_events: bool = False):
    """Decorator: attach one metric to one class.

        @register(key="jesters_cap_uptime", cls="troubador",
                  label="Jester's Cap uptime",
                  blurb="A window ends at 30s, a death, or the fight.",
                  columns=[Column("actor", "Troubador"), …],
                  needs_events=True)
        def jesters_cap(ctx):
            return [{"actor": …, "uptime": 62.0}]

    The function returns a list of row dicts keyed by column key, or a dict
    `{"rows": [...], "note": "…"}` when it has something to say about the run
    as a whole (a caster who was out of range, a fight nobody parsed).
    """
    for unit in (c.unit for c in columns):
        if unit not in UNITS:
            raise ValueError(f"unknown column unit {unit!r}; expected one of {UNITS}")

    def deco(fn: Callable[["Ctx"], object]):
        metric = Metric(key=key, cls=cls.lower(), label=label, blurb=blurb,
                        columns=tuple(columns), compute=fn,
                        needs_events=needs_events)
        bucket = _REGISTRY.setdefault(metric.cls, [])
        if any(m.key == metric.key for m in bucket):
            raise ValueError(f"duplicate metric {metric.cls}/{metric.key}")
        bucket.append(metric)
        return fn
    return deco


def metrics_for(cls: str | None) -> list[Metric]:
    return list(_REGISTRY.get((cls or "").lower(), ()))


def registered_classes() -> set[str]:
    return {cls for cls, ms in _REGISTRY.items() if ms}


@dataclass
class Ctx:
    """Everything a metric is allowed to read, and the one expensive thing it
    can ask for.

    `actors` is the aggregate payload's actor list — already class-resolved,
    already rolled up — so a metric never re-answers "what class is this".
    `live_enc_ids` is the subset whose events still exist; a pruned session
    keeps its rollups and loses its events, and an event-reading metric on a
    fully pruned selection reports that instead of returning zeroes.
    """
    conn: object
    enc_ids: list[int]
    encs: list                     # encounter rows, started_ts order
    live_enc_ids: list[int]
    session_ids: list[int]
    actors: list[dict]
    _events: dict = field(default_factory=dict, repr=False)

    @property
    def duration_s(self) -> int:
        """Summed combat time of the selection — the denominator for uptime."""
        return sum(max(e["duration_s"], 1) for e in self.encs)

    @property
    def started_ts(self) -> int:
        return min(e["started_ts"] for e in self.encs)

    @property
    def windows(self) -> list[tuple[int, int]]:
        """(start, end) of each LIVE fight, in order — what a coverage metric
        measures against. Duration is `duration_s`, not `ended_ts - started_ts`:
        a one-second fight is stored with duration 1 and the two would disagree."""
        live = set(self.live_enc_ids)
        return [(e["started_ts"], e["started_ts"] + max(e["duration_s"], 1))
                for e in self.encs if e["id"] in live]

    def census_duration_s(self, ability: str) -> float | None:
        """How long the game says the buff lasts. The log has no fade line for
        any of these, so this is the only thing an uptime can be built on —
        and a metric with no Census row for its ability must say so rather
        than pick a number."""
        row = self.conn.execute(
            "SELECT MAX(duration_s) FROM census_spells WHERE base_name=? "
            "AND duration_s > 0", (ability,)).fetchone()
        return row[0] if row and row[0] else None

    def players(self, cls: str) -> list[dict]:
        """Actor rows for one class, damage order (the agg payload's order)."""
        cls = cls.lower()
        return [a for a in self.actors
                if a.get("kind") == "player" and (a.get("class") or "").lower() == cls]

    def encounter(self, enc_id: int):
        for e in self.encs:
            if e["id"] == enc_id:
                return e
        return None

    def events(self, types: Iterable[str],
               ability: str | Iterable[str] | None = None) -> list[dict]:
        """Stored events of the given types across the live encounters, in log
        order, with entity ids already resolved to names.

        `ability` narrows to named abilities IN SQL, which matters: a metric
        built on one proc must not drag every damage row of a 60-fight night
        through Python to find it.

        Cached per (type-set, ability) for the request: two metrics that both
        want casts pay for one read. Rows are shared — a metric must treat them
        as read-only, which is also why `extra` is decoded lazily.
        """
        names = _names(ability)
        key = ("enc", tuple(sorted(set(types))), names)
        hit = self._events.get(key)
        if hit is not None:
            return hit
        if not self.live_enc_ids or not key[1]:
            self._events[key] = []
            return []
        eph = ",".join("?" * len(self.live_enc_ids))
        tph = ",".join("?" * len(key[1]))
        where = f"e.encounter_id IN ({eph}) AND e.type IN ({tph})"
        params = [*self.live_enc_ids, *key[1]]
        if names:
            where += f" AND ab.name IN ({','.join('?' * len(names))})"
            params += list(names)
        rows = self._read(where, params)
        self._events[key] = rows
        return rows

    def events_around(self, types: Iterable[str], lookback_s: int,
                      ability: str | Iterable[str] | None = None) -> list[dict]:
        """The same, plus the `lookback_s` seconds BEFORE each selected fight.

        A buff applied during the pull covers the opening of the fight, and an
        event in the gap between two pulls belongs to no encounter at all
        (`encounter_id` is NULL for it), so an encounter-keyed read cannot see
        it and every uptime would start the fight at zero.

        The window is bounded to each selected fight's own run-up rather than
        opened to the session, because authorization here is per ENCOUNTER: a
        shared raid must not become a window onto the other fights in the same
        uploaded file.
        """
        names = _names(ability)
        key = ("win", tuple(sorted(set(types))), int(lookback_s), names)
        hit = self._events.get(key)
        if hit is not None:
            return hit
        if not self.live_enc_ids or not key[1]:
            self._events[key] = []
            return []
        live = set(self.live_enc_ids)
        spans = [(e["started_ts"] - lookback_s, e["ended_ts"])
                 for e in self.encs if e["id"] in live]
        sph = ",".join("?" * len(self.session_ids))
        tph = ",".join("?" * len(key[1]))
        where = (f"e.session_id IN ({sph}) AND e.type IN ({tph}) "
                 f"AND e.ts BETWEEN ? AND ?")
        params = [*self.session_ids, *key[1],
                  min(s for s, _ in spans), max(e for _, e in spans)]
        if names:
            where += f" AND ab.name IN ({','.join('?' * len(names))})"
            params += list(names)
        rows = [r for r in self._read(where, params)
            # the range query is one indexed scan; the per-fight windows are
            # then applied here, so a gap between two pulls contributes nothing
            if any(s <= r["ts"] <= e for s, e in spans)]
        self._events[key] = rows
        return rows

    def _read(self, where: str, params: list) -> list[dict]:
        meta = {r["id"]: r for r in self.conn.execute(
            f"SELECT id, name, kind FROM entities WHERE session_id IN "
            f"({','.join('?' * len(self.session_ids))})", self.session_ids)}
        rows = []
        for r in self.conn.execute(
                f"SELECT e.encounter_id, e.ts, e.seq, e.type, e.src_entity, "
                f"e.tgt_entity, e.ability_id, e.amount, e.flags, e.extra, "
                f"ab.name AS ability FROM events e "
                f"LEFT JOIN abilities ab ON ab.id = e.ability_id "
                f"WHERE {where} ORDER BY e.ts, e.seq", params):
            src, tgt = meta.get(r["src_entity"]), meta.get(r["tgt_entity"])
            rows.append({
                "encounter_id": r["encounter_id"], "ts": r["ts"], "seq": r["seq"],
                "type": r["type"], "ability": r["ability"],
                "src": src["name"] if src else None,
                "src_kind": src["kind"] if src else None,
                "tgt": tgt["name"] if tgt else None,
                "tgt_kind": tgt["kind"] if tgt else None,
                "amount": r["amount"], "flags": r["flags"],
                "extra": json.loads(r["extra"]) if r["extra"] else {},
            })
        return rows


def _normalize(result: object, metric: Metric) -> dict:
    """A metric returns rows, or rows plus a note. Everything else is a bug in
    the metric, and says so rather than rendering as an empty table."""
    if isinstance(result, dict):
        rows, note = result.get("rows", []), result.get("note")
    else:
        rows, note = result, None
    if not isinstance(rows, list):
        raise TypeError(f"{metric.cls}/{metric.key} returned {type(rows).__name__}, "
                        "expected a list of row dicts")
    return {"rows": rows, "note": note}


def _run(metric: Metric, ctx: Ctx) -> dict:
    """One metric's panel payload. Never raises — a metric that blows up is
    reported as broken beside the ones that worked."""
    out = {
        "key": metric.key, "label": metric.label, "blurb": metric.blurb,
        "columns": [c.as_dict() for c in metric.columns],
        "rows": [], "note": None, "status": "ok",
    }
    if metric.needs_events and not ctx.live_enc_ids:
        out["status"] = "pruned"
        out["note"] = "Events pruned — this stat reads them."
        return out
    try:
        out.update(_normalize(metric.compute(ctx), metric))
    except Exception:
        log.exception("class metric %s/%s failed", metric.cls, metric.key)
        out["status"] = "error"
        out["rows"] = []
        out["note"] = "Failed to compute."
    return out


def collect(ctx: Ctx) -> dict:
    """The Class tab's payload: one section per class present in the parse.

    Classes with no metrics yet are still sections — the tab's job is to show
    who is in the raid and where their stats will live, and an empty section
    is the honest version of "not written yet". Players whose class the parse
    could not pin are listed once, separately, rather than being filed under a
    guess.
    """
    by_class: dict[str, list[dict]] = {}
    unclassified: list[str] = []
    for a in ctx.actors:
        if a.get("kind") != "player":
            continue
        # `unidentified` is not a class we failed to guess — it is the refine
        # pass saying nothing in the log proved a person was behind the name,
        # which usually means a summoned pet. A class roster is the wrong
        # place to list it, and "unknown class" would be a claim.
        if a.get("class_source") == "unidentified":
            continue
        cls = (a.get("class") or "").lower()
        if not cls:
            unclassified.append(a["name"])
            continue
        by_class.setdefault(cls, []).append(a)

    sections = []
    for cls, actors in by_class.items():
        metrics = [_run(m, ctx) for m in metrics_for(cls)]
        sections.append({
            "class": cls,
            "archetype": archetype_for(cls),
            "actors": [{"name": a["name"], "key": a.get("key"),
                        "class": a.get("class"),
                        "class_source": a.get("class_source"),
                        "class_confidence": a.get("class_confidence"),
                        "damage": a.get("damage"), "heals": a.get("heals")}
                       for a in actors],
            "metrics": metrics,
        })
    sections.sort(key=lambda s: (_ROLE_ORDER.get(s["archetype"], 9), s["class"]))
    return {
        "classes": sections,
        "unclassified": sorted(unclassified),
        "duration_s": ctx.duration_s,
    }
