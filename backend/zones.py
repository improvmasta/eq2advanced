"""What a zone NAME means: which expansion it arrived with, and whether it is a
raid zone.

A log line gives one thing about the place a fight happened — its name, and a
repeat visit gets a number stuck on the end ("Castle Mistmoore 2"). Everything
else about a zone is knowledge from the game, so it is reference data here
rather than something inferred from parses: the same rule the class tree keeps.

The source is the wiki's `IZoneInformation` box, pulled by hand into
`refdata/zone_eras.json` (`tools/sync_zone_eras.py`). The file is COMMITTED and
read once at import — nothing here touches the network, so the wiki being down
is not this app being down, and a zone that has no entry simply has no era
rather than a guessed one.

Its one caller today is the raid notes outline (`routers/notes_api.py`), which
groups the pile by expansion because that is the order a TLE server unlocks
content in — the shape a raider already has in their head.
"""

import json
import re
from pathlib import Path

ZONE_FILE = Path(__file__).resolve().parent / "refdata" / "zone_eras.json"

# The EXPANSIONS, in release order, with the day each one landed. These are the
# era boundaries: a zone added by a live update belongs to whatever expansion
# was current when it shipped, which is how `LU22` becomes Kingdom of Sky (its
# own patch notes say "a dangerous new raid zone in Kingdom of Sky") without
# anybody hand-filing it. `tools/sync_zone_eras.py` resolves them at sync time,
# so nothing here does date arithmetic at runtime.
EXPANSIONS = (
    ("Shattered Lands", "2004-11-08"),
    ("Desert of Flames", "2005-09-13"),
    ("Kingdom of Sky", "2006-02-21"),
    ("Echoes of Faydwer", "2006-11-14"),
    ("Rise of Kunark", "2007-11-13"),
    ("The Shadow Odyssey", "2008-11-18"),
    ("Sentinel's Fate", "2010-02-16"),
    ("Destiny of Velious", "2011-02-22"),
    ("Age of Discovery", "2011-12-06"),
    ("Chains of Eternity", "2012-11-13"),
    ("Tears of Veeshan", "2013-11-12"),
    ("Altar of Malice", "2014-11-11"),
    ("Terrors of Thalumbra", "2015-11-17"),
    ("Kunark Ascending", "2016-11-15"),
    ("Planes of Prophecy", "2017-11-28"),
    ("Chaos Descending", "2018-11-13"),
    ("Blood of Luclin", "2019-12-16"),
    ("Reign of Shadows", "2020-11-17"),
    ("Visions of Vetrovia", "2021-11-16"),
    ("Renewal of Ro", "2022-11-15"),
    ("Ballads of Zimara", "2023-11-14"),
    ("Scars of Destruction", "2024-11-12"),
)

# Display order: the expansions plus the three adventure packs, which keep
# their own headings because a TLE server unlocks them as their own thing.
# Anything the data names that is missing here still groups — it sorts to the
# end (`era_rank`) — so a later expansion costs one line, not a migration.
ERA_ORDER = (
    "Shattered Lands",
    "Bloodline Chronicles",
    "Splitpaw Saga",
    "Desert of Flames",
    "Fallen Dynasty",
    *[name for name, _ in EXPANSIONS[2:]],
)

# What a raider calls it. A sidebar heading has room for "EoF" and not for
# "Echoes of Faydwer", and the short form is what gets said out loud anyway.
ERA_SHORT = {
    "Shattered Lands": "Classic",
    "Bloodline Chronicles": "Bloodlines",
    "Splitpaw Saga": "Splitpaw",
    "Desert of Flames": "DoF",
    "Fallen Dynasty": "Fallen Dynasty",
    "Kingdom of Sky": "KoS",
    "Echoes of Faydwer": "EoF",
    "Rise of Kunark": "RoK",
    "The Shadow Odyssey": "TSO",
    "Sentinel's Fate": "SF",
    "Destiny of Velious": "DoV",
}


def expansion_on(date: str) -> str | None:
    """Which expansion was live on an ISO date. Used at SYNC time to place the
    zones the wiki files under a live update number instead of an expansion."""
    current = None
    for name, start in EXPANSIONS:
        if date >= start:
            current = name
        else:
            break
    return current

_INSTANCE_N = re.compile(r"\s+\d{1,2}$")


def _load() -> dict[str, dict]:
    try:
        raw = json.loads(ZONE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return {r["zone"]: r for r in raw.get("zones", []) if r.get("zone")}


_BY_NAME = _load()


def base_name(zone: str | None) -> str:
    """"Castle Mistmoore 2" -> "Castle Mistmoore".

    The game numbers repeat visits to an instance, and the number is about the
    NIGHT, not the place. Stripping it is why one zone's notes are one pile."""
    return _INSTANCE_N.sub("", (zone or "").strip()).strip()


def info(zone: str | None) -> dict | None:
    """The wiki's row for a zone, by exact name and then by base name.

    Exact first because a couple of real zone names end in a number, and a
    zone that names itself is a better answer than one we edited."""
    name = (zone or "").strip()
    if not name:
        return None
    return _BY_NAME.get(name) or _BY_NAME.get(base_name(name))


def era_of(zone: str | None) -> str | None:
    row = info(zone)
    return row["era"] if row else None


def era_rank(era: str | None) -> int:
    """Sort key. An unknown era goes last rather than first: the outline is
    read top-down and the zones we can place belong at the top."""
    try:
        return ERA_ORDER.index(era)
    except ValueError:
        return len(ERA_ORDER)


def era_label(era: str | None) -> str:
    return ERA_SHORT.get(era, era) if era else "Other"


def is_raid(zone: str | None) -> bool:
    row = info(zone)
    return bool(row and row.get("instance") == "Raid")


def is_public(zone: str | None) -> bool:
    """An OUTDOOR zone — the wiki's own word for it, off the `ZoneBox` pages
    (`gamewiki.parse_zone`). Not the same question as `is_raid`, and the
    difference is the whole point: an instance is booked and named by its
    zone, while a public zone is a place several guilds pass through and a
    contested named in one is an event that happened there. A zone with NO
    entry answers False to both — unknown is not a claim."""
    row = info(zone)
    return bool(row and row.get("instance") == "Public")
