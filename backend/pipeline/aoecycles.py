"""One row per enemy recast we watched, written at rollup.

`aoes.detect` already decides what a cast is and whether a reuse debuff was on
the mob when it happened. This is the same answer, kept: the interval between
two casts of one ability, tagged with the state at the cast that started it.

WHY KEEP THE CYCLES AND NOT THE CONCLUSION. What the timer for a (mob, ability)
really is, and whether a swipe moves it, is a question about every fight anyone
has uploaded rather than about the one being parsed — a single pull gives two
or three intervals and the honest answer needs dozens. Storing the observations
means the conclusion is derived (`aoelearn.py`), which buys three things a
stored conclusion does not: a threshold can be changed without re-reading a
year of logs, a re-parsed fight replaces its own rows instead of double-counting
them, and the evidence behind a number stays inspectable — a learned timer that
looks wrong can be taken apart into the cycles that made it.

The write is per encounter and goes through `clear_derived` like every other
derived row, so a rebuild is a replacement.
"""

from __future__ import annotations

from pipeline import aoes

INSERT = ("INSERT OR REPLACE INTO aoe_cycles (encounter_id, session_id, "
          "source_name, ability, cast_ts, gap_s, swiped, is_named) "
          "VALUES (?,?,?,?,?,?,?,?)")


def cycle_rows(events: list[dict], encounter_id: int, session_id: int,
               source_named: bool, encounter_name: str | None) -> list[tuple]:
    """The cycles in ONE encounter, ready for `INSERT`.

    `events` are the rollup's resolved rows, already shaped the way
    `aoes.detect` reads them. Detect is called rather than re-implemented for
    the reason the live meter calls it too: three copies of "what is a cast"
    is three definitions that drift, and the whole value of these rows is that
    they mean the same thing as the tab a reader compares them against.

    Casts that detect DROPPED are dropped here as well, and that is the point
    of routing through it.

    One thing it flags rather than drops has to be dropped HERE: a damage
    shield is a CONDITION and has no recast to learn (`aoes.SUSTAINED_RUN`).
    The audit tab keeps those rows deliberately — something that reached the
    raid is worth listing whatever it turned out to be — but the clustering
    assembles a shield's unbroken seconds into tidy "casts", and Mayong's
    `Caress Feedback` was arriving here as a 7.5-second timer. The live panel
    already refuses them for the same reason; this is that refusal, applied to
    the thing that would otherwise remember it forever."""
    named = {encounter_name} if (source_named and encounter_name) else set()
    out = []
    for row in aoes.detect(events, named):
        if row["sustained"]:
            continue
        casts = row["cast_list"]
        for a, b in zip(casts, casts[1:]):
            # only intervals inside this encounter are a recast (aoes.detect
            # never crosses one, so this is a restatement, not a second rule)
            out.append((encounter_id, session_id, row["source"], row["ability"],
                        a["ts"], b["ts"] - a["ts"], int(bool(a["swiped"])),
                        int(row["source"] in named)))
    return out


def detect_rows(seg_events: list[dict], name_of, kind_of,
                encounter_id: int) -> list[dict]:
    """The rollup's resolved events in the shape `aoes.detect` reads.

    The rollup holds entity IDs and the audit endpoint holds names, so this is
    where the two meet. Only damage and avoid lines cross — the same two types
    detect looks at — and an event missing either end is dropped rather than
    guessed at, exactly as the endpoint drops it."""
    out = []
    for ev in seg_events:
        if ev["type"] not in ("damage", "avoid") or not ev["ability"]:
            continue
        src, tgt = ev["src_entity"], ev["tgt_entity"]
        if src is None or tgt is None:
            continue
        tgt_name, tgt_kind = name_of(tgt), kind_of(tgt)
        out.append({
            "encounter_id": encounter_id,
            "ts": ev["ts"], "type": ev["type"], "ability": ev["ability"],
            "src_name": name_of(src), "src_kind": kind_of(src),
            "tgt_key": f"{tgt_name}|{tgt_kind}", "tgt_kind": tgt_kind,
            "tgt_name": tgt_name,
            "amount": ev["amount"], "dtype": ev["dtype"], "flags": ev["flags"],
        })
    return out


def record(conn, seg_events: list[dict], name_of, kind_of, encounter_id: int,
           session_id: int, source_named: bool,
           encounter_name: str | None) -> int:
    rows = cycle_rows(detect_rows(seg_events, name_of, kind_of, encounter_id),
                      encounter_id, session_id, source_named, encounter_name)
    if rows:
        conn.executemany(INSERT, rows)
    return len(rows)
