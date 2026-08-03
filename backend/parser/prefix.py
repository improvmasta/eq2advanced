"""Line-prefix handling. Every log line carries `(epoch)[ctime] body` with CRLF
endings; the epoch is authoritative (the bracket ctime is space-padded local time).
Numbers in bodies are comma-grouped."""

import re

PREFIX_RE = re.compile(r"^\((\d{10})\)\[[^\]]+\] (.*)$")

# EQ2 chat markup: \aITEM <id> <id>[ <n> <n> <n>]:<Item Name>\/a  -> <Item Name>
ITEM_RE = re.compile(r"\\aITEM [-\d ]+:([^\\]+)\\/a")


def split_prefix(line: str) -> tuple[int, str] | None:
    """Return (epoch, body) or None if the line has no prefix (not expected)."""
    m = PREFIX_RE.match(line.rstrip("\r\n"))
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def unescape_items(body: str) -> str:
    return ITEM_RE.sub(r"\1", body)


def to_int(num: str) -> int:
    """Comma-grouped ints, plus the log's abbreviation for 6-figure hits
    ("296.1K" -> 296100). Tolerates a stray decimal ("1.5") — one malformed
    line must never abort a whole session parse."""
    num = num.replace(",", "")
    if num.endswith("K"):
        return int(float(num[:-1]) * 1000)
    try:
        return int(num)
    except ValueError:
        return int(float(num))
