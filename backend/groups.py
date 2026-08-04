"""Groups and who can see which raid.

This module owns the visibility rule. Nothing else composes it, because it is
the whole privacy model of the site and it needs to be readable in one place:

    a zone run is visible to you if
      * you own it (its character is on your account), or
      * it is explicitly shared with a group you are in, or
      * its character auto-shares with a group you are in AND this run has not
        been hidden from that group, or
      * an admin has published it to everyone (`public_runs`).

Every one of these is decided ON THE SITE, by someone signed in. The ACT
uploader deliberately has no say in it: a device token sends logs and nothing
else. A `session_shares` branch letting the plugin share a raid as it recorded
it was built and removed in v12 — if it comes up again, the reason it went is
that "who can see my raids" is a decision for the account, not for a config
file on a gaming PC.

Sharing is never copied onto the run. `rebuild_zone_runs` drops and re-derives
run membership on every upload, reparse and hand edit, so anything materialised
there would evaporate; and evaluating at read time is what makes leaving a group
take effect immediately rather than "next rebuild".

`hide` exists because auto-share is the useful default and you still want to
keep one wipe to yourself. Explicit `share` wins over everything, `hide` only
overrides the auto-share of the same group.

Being able to SEE a run never implies being able to change it. Every mutation
(delete, merge, split, run edits, reparse, session delete) checks ownership
separately — see `security.owned_zone_run`.
"""

import secrets
import time

# Every branch except publishing: the runs you'd see if nothing had ever been
# made public. The difference is what the raid list's "Public" switch takes out
# — a run you own or share with a group does not stop being yours just because
# it is also published. Parameterised by :uid, used as a subquery so it composes
# with whatever else a caller is filtering on.
PERSONAL_RUN_IDS = """
    SELECT z.id FROM zone_runs z JOIN characters c ON c.id = z.character_id
      WHERE c.user_id = :uid
    UNION
    SELECT r.zone_run_id FROM run_shares r
      JOIN group_members m ON m.group_id = r.group_id AND m.user_id = :uid
      WHERE r.mode = 'share'
    UNION
    SELECT z.id FROM zone_runs z
      JOIN character_shares cs ON cs.character_id = z.character_id
      JOIN group_members m ON m.group_id = cs.group_id AND m.user_id = :uid
      WHERE NOT EXISTS (SELECT 1 FROM run_shares h WHERE h.zone_run_id = z.id
                          AND h.group_id = cs.group_id AND h.mode = 'hide')
"""

# One SELECT of run ids: the whole predicate. Derived from PERSONAL_RUN_IDS
# rather than repeated, so a new branch cannot be added to one and forgotten in
# the other — that divergence would be a silent leak or a silent hiding.
VISIBLE_RUN_IDS = PERSONAL_RUN_IDS + """
    UNION
    SELECT zone_run_id FROM public_runs
"""

# Runs you can see that are NOT yours — the "shared with me" scope.
SHARED_RUN_IDS = f"""
    SELECT id FROM ({VISIBLE_RUN_IDS})
    EXCEPT
    SELECT z.id FROM zone_runs z JOIN characters c ON c.id = z.character_id
      WHERE c.user_id = :uid
"""

MEMBER_GROUP_IDS = "SELECT group_id FROM group_members WHERE user_id = :uid"


def is_member(conn, group_id: int, user_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
        (group_id, user_id)).fetchone() is not None


def member_role(conn, group_id: int, user_id: int) -> str | None:
    row = conn.execute("SELECT role FROM group_members WHERE group_id=? AND user_id=?",
                       (group_id, user_id)).fetchone()
    return row["role"] if row else None


def can_manage(conn, group_id: int, user_id: int) -> bool:
    return member_role(conn, group_id, user_id) in ("owner", "admin")


def my_groups(conn, user_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT g.id, g.name, g.description, g.owner_user_id, g.created_ts, "
        "m.role AS my_role, "
        "(SELECT COUNT(*) FROM group_members x WHERE x.group_id = g.id) AS member_count "
        "FROM groups g JOIN group_members m ON m.group_id = g.id "
        "WHERE m.user_id = ? ORDER BY g.name", (user_id,))]


def new_join_code(conn) -> str:
    """A free 6-digit code. The space is only a million, so the code is not the
    security — `ratelimit` on the join route is (and the owner can rotate it)."""
    for _ in range(50):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if conn.execute("SELECT 1 FROM groups WHERE join_code=?", (code,)).fetchone() is None:
            return code
    raise RuntimeError("could not find a free join code")


def code_is_live(group_row) -> bool:
    if not group_row["join_code"]:
        return False
    expires = group_row["join_code_expires_ts"]
    return expires is None or expires > int(time.time())


def group_by_code(conn, code: str):
    row = conn.execute("SELECT * FROM groups WHERE join_code=?", (code,)).fetchone()
    return row if row is not None and code_is_live(row) else None


def add_member(conn, group_id: int, user_id: int, role: str = "member") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO group_members (group_id, user_id, role, joined_ts) "
        "VALUES (?,?,?,?)", (group_id, user_id, role, int(time.time())))


def delete_group(conn, group_id: int) -> None:
    """Membership, invites and every share that pointed at this group. Runs
    shared only here go back to being private."""
    for table in ("group_members", "group_invites", "character_shares", "run_shares"):
        conn.execute(f"DELETE FROM {table} WHERE group_id=?", (group_id,))
    conn.execute("DELETE FROM groups WHERE id=?", (group_id,))


def shares_for_runs(conn, run_ids: list[int]) -> dict[int, list[dict]]:
    """{run_id: [{group_id, name, mode, auto}]} — what each run is shared with,
    for the owner's share control. `auto` marks a share that comes from the
    character's auto-share rather than a decision about this run."""
    if not run_ids:
        return {}
    ph = ",".join("?" * len(run_ids))
    out: dict[int, list[dict]] = {r: [] for r in run_ids}
    seen: set[tuple[int, int]] = set()
    for r in conn.execute(
            f"SELECT rs.zone_run_id AS run_id, rs.group_id, rs.mode, g.name "
            f"FROM run_shares rs JOIN groups g ON g.id = rs.group_id "
            f"WHERE rs.zone_run_id IN ({ph})", run_ids):
        if r["mode"] == "share":
            out[r["run_id"]].append({"group_id": r["group_id"], "name": r["name"],
                                     "mode": "share", "auto": False})
        seen.add((r["run_id"], r["group_id"]))
    for r in conn.execute(
            f"SELECT z.id AS run_id, cs.group_id, g.name FROM zone_runs z "
            f"JOIN character_shares cs ON cs.character_id = z.character_id "
            f"JOIN groups g ON g.id = cs.group_id "
            f"WHERE z.id IN ({ph})", run_ids):
        if (r["run_id"], r["group_id"]) not in seen:
            out[r["run_id"]].append({"group_id": r["group_id"], "name": r["name"],
                                     "mode": "share", "auto": True})
    return out


def character_auto_shares(conn, character_id: int) -> list[int]:
    return sorted(r["group_id"] for r in conn.execute(
        "SELECT group_id FROM character_shares WHERE character_id=?", (character_id,)))


def set_character_auto_shares(conn, character_id: int, owner_user_id: int,
                              group_ids: set[int]) -> None:
    """Replace the character's standing auto-share list. Only the owner's OWN
    groups are rewritten — a character can carry a share into a group its owner
    has since left, and silently dropping that on an unrelated save would
    unshare a back catalogue nobody asked about."""
    now = int(time.time())
    mine = {g["id"] for g in my_groups(conn, owner_user_id)}
    if mine:
        ph = ",".join("?" * len(mine))
        conn.execute(
            f"DELETE FROM character_shares WHERE character_id=? AND group_id IN ({ph})",
            (character_id, *sorted(mine)))
    for gid in sorted(group_ids):
        conn.execute("INSERT INTO character_shares (character_id, group_id, created_ts) "
                     "VALUES (?,?,?)", (character_id, gid, now))


def carry_shares(conn, merged_into: dict[int, int]) -> None:
    """Called by `rebuild_zone_runs` when runs collapse into one id: the survivor
    inherits the union of the shares. Without this, merging two nights — or a
    reparse that re-segments them — would silently unshare one of them.

    Call BEFORE the old rows are deleted, then `drop_orphan_shares` after."""
    now = int(time.time())
    for gone, survivor in merged_into.items():
        if gone == survivor:
            continue
        for r in conn.execute("SELECT group_id, mode FROM run_shares WHERE zone_run_id=?",
                              (gone,)).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO run_shares (zone_run_id, group_id, mode, created_ts) "
                "VALUES (?,?,?,?)", (survivor, r["group_id"], r["mode"], now))
        if conn.execute("SELECT 1 FROM public_runs WHERE zone_run_id=?", (gone,)).fetchone():
            conn.execute(
                "INSERT OR IGNORE INTO public_runs (zone_run_id, published_by, created_ts) "
                "SELECT ?, published_by, ? FROM public_runs WHERE zone_run_id=?",
                (survivor, now, gone))


def drop_orphan_shares(conn) -> None:
    for table in ("run_shares", "public_runs"):
        conn.execute(f"DELETE FROM {table} WHERE zone_run_id NOT IN "
                     "(SELECT id FROM zone_runs)")


def audit(conn, actor_user_id: int | None, action: str,
          target: str | None = None, detail: str | None = None) -> None:
    """Admin actions are written down. The point of the metadata-only admin is
    that it can be checked, and a log nobody can read proves nothing — the
    admin console serves this back."""
    conn.execute(
        "INSERT INTO audit_log (ts, actor_user_id, action, target, detail) VALUES (?,?,?,?,?)",
        (int(time.time()), actor_user_id, action, target, detail))
