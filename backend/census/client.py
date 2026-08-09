"""Daybreak Census HTTP client (EQ2 namespace).

Query shapes verified live 2026-08-02:
  * character: name.first_lower + locationdata.worldid (Wuoshi = 618); the doc's
    typed fields carry everything coaching needs (stats.combat.abilitymod /
    basemodifier / critchance, stats.ability.spelltimereusepct / spelltimecastpct,
    weapon delays) — no effect-text parsing on the character side.
  * spell: one record per version x tier; character spell_list ints are these ids.
    cast_secs_hundredths / recast_secs / recovery_secs_tenths / duration.*_sec_tenths
    are typed; damage numbers live only in effect_list description text.
  * item: equipment slots carry item ids only — displayname/tier come from here.

Tests never construct a real client — sync.py takes the client as a parameter and
CI uses recorded fixtures (tests/fixtures/census/).
"""

import os
import time

import httpx

BASE = "https://census.daybreakgames.com/{sid}/get/eq2"
ID_CHUNK = 100  # ids per request; verified batch id-queries work with c:limit
RETRIES = 3     # full character docs are large and Census reads regularly stall


class CensusError(Exception):
    pass


class CensusClient:
    def __init__(self, service_id: str | None = None, timeout: float = 60.0):
        sid = service_id or os.environ.get("CENSUS_SERVICE_ID", "s:example")
        self._base = BASE.format(sid=sid)
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self):
        self._http.close()

    def _get(self, path: str, list_key: str) -> list[dict]:
        url = f"{self._base}/{path}"
        last = None
        for attempt in range(RETRIES):
            if attempt:
                time.sleep(2 * attempt)
            try:
                res = self._http.get(url)
                res.raise_for_status()
                doc = res.json()
                break
            except (httpx.HTTPError, ValueError) as e:
                last = e
        else:
            raise CensusError(f"census request failed: {last}") from last
        if "error" in doc:
            raise CensusError(f"census error: {doc['error']}")
        return doc.get(list_key, [])

    def character_by_name(self, name: str, world_id: int = 618) -> dict | None:
        rows = self._get(
            f"character/?name.first_lower={name.lower()}&locationdata.worldid={world_id}"
            "&c:limit=1", "character_list")
        return rows[0] if rows else None

    def _by_ids(self, collection: str, list_key: str, ids: list[int]) -> list[dict]:
        out = []
        for i in range(0, len(ids), ID_CHUNK):
            chunk = ids[i:i + ID_CHUNK]
            out += self._get(
                f"{collection}/?id={','.join(map(str, chunk))}&c:limit={len(chunk)}",
                list_key)
        return out

    def spells_by_ids(self, ids: list[int]) -> list[dict]:
        return self._by_ids("spell", "spell_list", ids)

    def spells_by_crcs(self, crcs: list[int]) -> list[dict]:
        """Every tier of the given spell lines (crc is shared across the tiers
        of one spell version) — tier-upgrade advice needs tiers the character
        has not scribed. ONE request per crc: Census's comma OR-list works for
        id= but silently returns nothing for crc= (verified live 2026-08-02)."""
        out = []
        for crc in crcs:
            out += self._get(f"spell/?crc={crc}&c:limit=20", "spell_list")
        return out

    PAGE = 1000

    def spell_page(self, cls: str, max_level: int, start: int) -> list[dict]:
        """One page of the spell records (all tiers) a class can scribe at or
        below max_level. classes{} keys are lowercase ('wizard',
        'shadowknight', 'troubador'); '[' is Census's <= operator; c:start
        paging verified live 2026-08-02 (wizard <=70 = 1152 records). Census
        silently CLAMPS c:limit (s:example throttles to 100), so callers must
        advance by len(result) and treat only an EMPTY page as the end —
        sync.ingest_class_spells owns that loop and resumes it across the
        s:example burst throttle."""
        return self._get(
            f"spell/?classes.{cls}.level=%5B{max_level}"
            f"&c:limit={self.PAGE}&c:start={start}", "spell_list")

    def items_by_ids(self, ids: list[int]) -> list[dict]:
        return self._by_ids("item", "item_list", ids)

    # The card an item needs to be DISPLAYED, which is a tiny slice of a record
    # that is mostly stat block and discovery history. `c:show` matters here:
    # a raid night's chest loot is a few hundred ids and the full documents run
    # to megabytes. See backend/items.py.
    # `typeinfo` carries a weapon's damage range, delay and rating. It also
    # carries a class list Census will not let `c:show` narrow into, which is
    # why the batch is 100 and not 1000.
    CARD_SHOW = ("id,displayname,iconid,tier,type,itemlevel,slot_list,"
                 "modifiers,flags,adornmentslot_list,typeinfo")

    def item_cards(self, ids: list[int]) -> list[dict]:
        out = []
        for i in range(0, len(ids), ID_CHUNK):
            chunk = ids[i:i + ID_CHUNK]
            out += self._get(
                f"item/?id={','.join(map(str, chunk))}"
                f"&c:limit={len(chunk)}&c:show={self.CARD_SHOW}", "item_list")
        return out


_shared: CensusClient | None = None


def shared_client() -> CensusClient:
    """Process-wide client. Tests set census.client._shared to a fixture fake
    before any request so nothing here ever goes to the network in CI."""
    global _shared
    if _shared is None:
        _shared = CensusClient()
    return _shared
