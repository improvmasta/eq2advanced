"""The two hand marks, on the account.

JOUST says the raid leaves for this AoE; MINI says it is worth a slot on the
mini parse. Neither is in any log — running out of a Soul Paralysis and standing
in a Blanket of Eternal Night look identical in one — so both are marked by hand
and both are properties of the ABILITY, keyed by name and outliving the pull
they were made on. `frontend/src/lib/marks.js` is the other half of this and
says why the mark is an ANSWER rather than a membership.

WHY THE SERVER HOLDS THEM AT ALL, having deliberately not held them before.
They started in localStorage, which was the right call while the only other
surface was an OBS source: a mark is a note about how somebody plays, it is
worth nothing to anyone else, and the alternative was a settings table and a
round trip in front of a countdown. The in-game window (overlay v34) is what
made that wrong. EQ2's browser is a DIFFERENT BROWSER, so the window beside
somebody's hotbars inherited nothing they had marked on the dashboard — it
jousted whatever their ACT list happened to list and no more. An account is the
only thing those two screens share.

WHAT THIS IS NOT. It is not a settings service and must not grow into one:
these are two sets of ability names, they mean nothing without a parse to read
them against, and the panels still draw before this has answered — the SPA
hydrates from the browser's own copy first and corrects on the read
(`lib/marks.js`). Nothing here can fail a countdown.

THE ABSENT ROW IS THE THIRD STATE. Yes, no, and nothing-said, where
nothing-said takes the ACT-list default; a row is written for the first two and
DELETED for the third, so "unmark" is not "mark false".
"""

import time

KINDS = ("joust", "mini")

# What one account may hold, per kind. A raid tier is a few dozen abilities and
# a career is a few hundred; this is a runaway-client backstop, not a budget
# anybody can spend. Past it the marks already stored keep working — refusing a
# new one is the honest failure, because silently evicting somebody's oldest
# mark would take a countdown away with no way to find out why.
MAX_PER_KIND = 500
MAX_ABILITY_LEN = 200
# Per request. The SPA sends one toggle at a time; the one bulk send is the
# adoption of what a browser already had, which is bounded by the same cap.
MAX_PATCH = 600


def read(conn, user_id: int) -> dict[str, dict[str, bool]]:
    """Every answer this account has given, as `{kind: {ability: bool}}`.

    Both kinds are always present and may be empty — a caller merging this into
    a client's state needs to be able to tell "no answers" from "not asked"."""
    out: dict[str, dict[str, bool]] = {k: {} for k in KINDS}
    for r in conn.execute(
            "SELECT kind, ability, marked FROM user_marks WHERE user_id=?",
            (user_id,)):
        if r["kind"] in out:
            out[r["kind"]][r["ability"]] = bool(r["marked"])
    return out


def write(conn, user_id: int, patch: dict[str, dict[str, bool | None]]) -> int:
    """Apply a patch and return how many answers it touched.

    MERGE, never replace: the patch names the abilities it has something to say
    about and nothing else is disturbed. That is what lets one endpoint serve
    both a single pill click and a browser handing over the set it had — and it
    is also what keeps two tabs, or a dashboard and a raid page, from undoing
    each other by each PUTting the world as they last saw it.

    `None` DELETES, which is the only way back to nothing-said. Everything a
    UI does today writes true or false; the delete exists because the state
    exists, and a store that cannot express its own third state is one somebody
    will later emulate with a magic value."""
    now = int(time.time())
    touched = 0
    with conn:
        counts = None
        for kind in KINDS:
            for ability, answer in (patch.get(kind) or {}).items():
                ability = (ability or "").strip()[:MAX_ABILITY_LEN]
                if not ability:
                    continue
                if answer is None:
                    conn.execute(
                        "DELETE FROM user_marks WHERE user_id=? AND kind=? "
                        "AND ability=?", (user_id, kind, ability))
                    touched += 1
                    continue
                # The cap is checked against what is STORED, and only when a
                # new ability would be added — re-answering one already held is
                # never refused, or an account at the ceiling could not turn a
                # mark off.
                if counts is None:
                    counts = {r["kind"]: r["n"] for r in conn.execute(
                        "SELECT kind, COUNT(*) AS n FROM user_marks "
                        "WHERE user_id=? GROUP BY kind", (user_id,))}
                cur = conn.execute(
                    "UPDATE user_marks SET marked=?, updated_ts=? WHERE "
                    "user_id=? AND kind=? AND ability=?",
                    (1 if answer else 0, now, user_id, kind, ability))
                if cur.rowcount:
                    touched += 1
                    continue
                if counts.get(kind, 0) >= MAX_PER_KIND:
                    continue
                conn.execute(
                    "INSERT INTO user_marks (user_id, kind, ability, marked, "
                    "updated_ts) VALUES (?,?,?,?,?)",
                    (user_id, kind, ability, 1 if answer else 0, now))
                counts[kind] = counts.get(kind, 0) + 1
                touched += 1
    return touched
