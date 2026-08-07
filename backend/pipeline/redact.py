"""Ingest-time redaction: private conversation never reaches disk.

The rule this module enforces is that a stored log keeps the fight and the raid's
own talk about the fight, and nothing else. Tells, guild and officer chat, the
public channels (LFG/General/Auction/…) and local /say are dropped before the
bytes are written, so there is no window in which the full client log exists on
the server and nothing to go back and clean up later.

WHY THIS CANNOT CHANGE A NUMBER. `classify.classify_body` returns None for every
line beginning `\\aPC `/`\\aNPC ` and for `You say|tell` — chat contributes nothing
to any parsed event. This module governs EXACTLY that set and never inspects
anything else, so a line the parser reads is a line redaction cannot touch. The
two sets are the SAME OBJECTS, imported from classify rather than restated here,
because a copy that drifts is how redaction would quietly start eating events.

DEFAULT-DENY. Within the governed set a line is dropped unless it matches
`_RETAIN`. A channel nobody thought of — a future custom channel, a chat format
from a client patch — is dropped rather than kept, which is the direction an
error has to fail in. The one carve-out is a governed line carrying no quoted
message at all (`Bob Goes Into a Bloodlust!!.`): no typed text means nothing
private to leak, so it stays as fight flavor.

Fight-window scoping is NOT done here. Retained group/raid chat is trimmed to
encounter windows afterwards by `trim_to_fights`, which needs the parse to know
where the fights are.
"""

import re

from parser.classify import CHAT_PREFIXES as GOVERNED_PREFIXES, CHAT_RE as GOVERNED_RE
from parser.prefix import split_prefix

# \aPC 58070227 Aros:Aros\/a says to the group, "…"  ->  kind=PC, rest=says to…
_SPEAKER_RE = re.compile(r"^\\a(?P<kind>PC|NPC) (?P<id>-?\d+) [^\\]*\\/a (?P<rest>.*)$")

# a typed message is present — the thing worth protecting
_QUOTED_RE = re.compile(r', "')

# the retained channels, keyed by the label used in the per-session counts
_PC_RETAIN = {
    "group": "says to the group,",
    "raid": "says to the raid party,",
}
_SELF_RETAIN = {
    "group": "You say to the group,",
    "raid": "You say to the raid party,",
}


def channel_of(body: str) -> str | None:
    """The channel a governed line belongs to, or None if the line is not
    governed (i.e. it is combat/system text that redaction must leave alone).

    Returns one of: 'group', 'raid', 'npc', 'flavor' (governed, no typed
    message), or 'private' for everything else in the governed set.
    """
    if not (body.startswith(GOVERNED_PREFIXES) or GOVERNED_RE.match(body)):
        return None
    if not _QUOTED_RE.search(body):
        # a governed line with no quoted message carries no typed text
        return "flavor"

    m = _SPEAKER_RE.match(body)
    if m:
        rest = m.group("rest")
        if m.group("kind") == "NPC":
            # scripted boss dialogue, including "says in Thexian" — game content,
            # not anybody's conversation
            return "npc" if rest.startswith("says") else "private"
        for label, prefix in _PC_RETAIN.items():
            if rest.startswith(prefix):
                return label
        return "private"

    for label, prefix in _SELF_RETAIN.items():
        if body.startswith(prefix):
            return label
    return "private"


RETAINED = frozenset({"group", "raid", "npc", "flavor"})


def keep_line(line: str) -> bool:
    """True if `line` may be stored. Anything without a parseable prefix is kept:
    it is not chat, and dropping unparseable lines would silently eat log damage."""
    split = split_prefix(line)
    if split is None:
        return True
    channel = channel_of(split[1])
    return channel is None or channel in RETAINED


class Redactor:
    """Stateful line filter that also counts what it removed, so a session can
    report the redaction rather than performing it silently."""

    def __init__(self) -> None:
        self.kept = 0
        self.dropped = 0

    def __call__(self, line: str) -> bool:
        if keep_line(line):
            self.kept += 1
            return True
        self.dropped += 1
        return False

    def filter(self, lines):
        for line in lines:
            if self(line):
                yield line


# a single line this long without a newline is not a log line; decide on what we
# have rather than buffering an upload into memory waiting for a terminator
_MAX_LINE = 1 << 20


class StreamRedactor(Redactor):
    """Byte-stream form for the upload path, which must filter as it streams:
    the whole point is that the unredacted file never lands on disk, so there is
    no "write it then clean it" option. Holds a partial trailing line between
    chunks. Bytes round-trip through surrogateescape, so a retained line is
    stored byte-identical to what was uploaded."""

    def __init__(self) -> None:
        super().__init__()
        self._buf = b""

    def feed(self, chunk: bytes) -> bytes:
        self._buf += chunk
        out = bytearray()
        if b"\n" in self._buf:
            *lines, self._buf = self._buf.split(b"\n")
            for raw in lines:
                self._emit(raw + b"\n", out)
        if len(self._buf) > _MAX_LINE:
            self._emit(self._buf, out)
            self._buf = b""
        return bytes(out)

    def finish(self) -> bytes:
        out = bytearray()
        if self._buf:
            self._emit(self._buf, out)
            self._buf = b""
        return bytes(out)

    def _emit(self, raw: bytes, out: bytearray) -> None:
        if self(raw.decode("utf-8", "surrogateescape")):
            out += raw


# Pull calls land before the first swing and the post-mortem right after the
# wipe, so the window a fight's talk occupies is wider than the fight.
FIGHT_MARGIN_S = 90


def _windows(conn, session_id: int) -> list[tuple[int, int]] | None:
    """Merged [start, end] windows covering every fight in every session that
    shares this session's stored bytes.

    The union matters because an upload file is content-addressed and shared
    between people who were on the same raid: trimming to one uploader's fights
    would cut chat out from under the others. Returns None when no session on
    those bytes has parsed yet, which means "don't trim" rather than "trim
    everything" — the caller must not read an empty list as an empty night."""
    row = conn.execute("SELECT source, upload_sha256 FROM sessions WHERE id=?",
                       (session_id,)).fetchone()
    if row is None:
        return None
    if row["source"] == "upload" and row["upload_sha256"]:
        rows = conn.execute(
            "SELECT e.started_ts, e.ended_ts FROM encounters e "
            "JOIN sessions s ON s.id = e.session_id WHERE s.upload_sha256=?",
            (row["upload_sha256"],)).fetchall()
    else:
        rows = conn.execute(
            "SELECT started_ts, ended_ts FROM encounters WHERE session_id=?",
            (session_id,)).fetchall()
    if not rows:
        return None
    spans = sorted((r["started_ts"] - FIGHT_MARGIN_S, r["ended_ts"] + FIGHT_MARGIN_S)
                   for r in rows)
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def trim_to_fights(conn, session_id: int) -> int:
    """Second pass, after the parse: drop retained PLAYER chat that falls outside
    any fight. What survives ingest is the group and raid channels; what survives
    this is the part of them that was said about a fight.

    NPC dialogue and unquoted flavor are left alone — they are game content with
    no privacy dimension, and boss lines are worth keeping wherever they fall.

    Returns the number of lines removed. Safe to run twice; a no-op when nothing
    has parsed yet."""
    import gzip

    windows = _windows(conn, session_id)
    if windows is None:
        return 0

    def in_fight(ts: int) -> bool:
        return any(start <= ts <= end for start, end in windows)

    from pipeline.ingest_writer import session_raw_paths

    removed = 0
    for path in session_raw_paths(conn, session_id):
        tmp = path.with_suffix(path.suffix + ".trimming")
        dropped_here = 0
        try:
            with gzip.open(path, "rb") as src, gzip.open(tmp, "wb") as out:
                for raw in src:
                    line = raw.decode("utf-8", "surrogateescape")
                    split = split_prefix(line)
                    if split is not None and channel_of(split[1]) in ("group", "raid") \
                            and not in_fight(split[0]):
                        dropped_here += 1
                        continue
                    out.write(raw)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        if dropped_here:
            tmp.replace(path)
            removed += dropped_here
        else:
            tmp.unlink(missing_ok=True)
    return removed
