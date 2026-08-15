"""Offline waypoint-match audit for the Planner's Phase 0 decision gate.

The quest catalog lives here, but the step/coordinate extractor deliberately
stays in wikq2 (``docs/planner.md``).  This module asks wikq2's exact-title API
for each catalogued quest, retains only the planner-facing fields, and measures
how much of the resulting coordinate corpus the EQ2MAP matcher can identify.

Nothing imports this module from the API.  ``tools/planner_phase0.py`` is the
only caller and is hand-run, resumable, and safe to interrupt.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SCHEMA_VERSION = 1
CONFIDENCE_BUCKETS = (
    (0.55, 0.70, "0.55-0.69"),
    (0.70, 0.85, "0.70-0.84"),
    (0.85, 0.95, "0.85-0.94"),
    (0.95, 1.01, "0.95-0.98"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise(value: str) -> str:
    return " ".join((value or "").replace("_", " ").lower().split())


def quest_titles(conn, era: str) -> list[str]:
    return [row[0] for row in conn.execute(
        "SELECT page_title FROM plan_quests WHERE era=? ORDER BY page_title",
        (era,),
    )]


def planner_sync(conn, era: str) -> dict:
    row = conn.execute(
        "SELECT items, sources, sets, quests, edges, pages, synced_ts "
        "FROM plan_syncs WHERE era=?",
        (era,),
    ).fetchone()
    if not row:
        return {}
    keys = ("items", "sources", "sets", "quests", "edges", "pages", "syncedTs")
    return dict(zip(keys, row))


def lookup_quest(title: str, lookup_url: str, timeout: float = 120,
                 retries: int = 3) -> dict:
    """Resolve one known quest title through wikq2, with bounded retries."""
    url = f"{lookup_url}?{urlencode({'q': title, 'kind': 'all', 'exact': '1'})}"
    last_error = "unknown error"
    for attempt in range(retries):
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            results = payload.get("results") or []
            exact = next((row for row in results
                          if _normalise(row.get("title", "")) == _normalise(title)), None)
            result = exact or next((row for row in results
                                    if row.get("kind") == "quest"), None)
            if not result:
                return {"pageTitle": title, "status": "error",
                        "error": "wikq2 returned no quest result"}
            return {
                "pageTitle": title,
                "status": "ok",
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "kind": result.get("kind", ""),
                "stepsAnyOrder": bool(result.get("stepsAnyOrder")),
                "questSteps": result.get("questSteps") or [],
                "coordinates": result.get("coordinates") or [],
            }
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    return {"pageTitle": title, "status": "error", "error": last_error}


def _finite_coordinate(coord: dict) -> bool:
    return all(isinstance(coord.get(key), (int, float))
               and math.isfinite(coord[key]) for key in ("x", "y", "z"))


def _percent(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 2) if denominator else 0.0


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    at = (len(ordered) - 1) * fraction
    lower = math.floor(at)
    upper = math.ceil(at)
    if lower == upper:
        return round(ordered[lower], 3)
    value = ordered[lower] * (upper - at) + ordered[upper] * (at - lower)
    return round(value, 3)


def summarise(quests: list[dict]) -> dict:
    """Return the Phase 0 coverage and quality report for resolved quests."""
    ok = [quest for quest in quests if quest.get("status") == "ok"]
    errors = [quest for quest in quests if quest.get("status") != "ok"]
    coordinates = [coord for quest in ok for coord in quest.get("coordinates", [])]
    numeric = [coord for coord in coordinates if _finite_coordinate(coord)]
    zone_labeled = [coord for coord in numeric if coord.get("zoneTitle")]
    matched = [coord for coord in coordinates if coord.get("mapMatch")]
    confidences = [float(coord["mapMatch"]["confidence"]) for coord in matched]
    distances = [float(coord["mapMatch"]["distance2d"]) for coord in matched]
    same_floor = [coord for coord in matched
                  if float(coord["mapMatch"].get("elevationDelta", math.inf)) <= 4]

    buckets = {label: 0 for _low, _high, label in CONFIDENCE_BUCKETS}
    buckets["below-0.55"] = 0
    for confidence in confidences:
        label = next((label for low, high, label in CONFIDENCE_BUCKETS
                      if low <= confidence < high), "below-0.55")
        buckets[label] += 1

    zones: dict[str, dict[str, int]] = {}
    for coord in numeric:
        zone = coord.get("zoneTitle") or "(no zone)"
        row = zones.setdefault(zone, {"coordinates": 0, "matched": 0})
        row["coordinates"] += 1
        if coord.get("mapMatch"):
            row["matched"] += 1
    by_zone = [{"zone": zone, **counts,
                "matchRate": _percent(counts["matched"], counts["coordinates"])}
               for zone, counts in zones.items()]
    by_zone.sort(key=lambda row: (-row["coordinates"], row["zone"]))

    return {
        "quests": {
            "total": len(quests),
            "resolved": len(ok),
            "errors": len(errors),
            "withCoordinates": sum(bool(quest.get("coordinates")) for quest in ok),
        },
        "coordinates": {
            "extracted": len(coordinates),
            "numeric": len(numeric),
            "zoneLabeled": len(zone_labeled),
            "missingZone": sum(not coord.get("zoneTitle") for coord in numeric),
            "matched": len(matched),
            "matchRateOverall": _percent(len(matched), len(coordinates)),
            "matchRateZoneLabeled": _percent(len(matched), len(zone_labeled)),
            "sameFloor": len(same_floor),
        },
        "confidence": {
            "buckets": buckets,
            "p10": _quantile(confidences, 0.10),
            "p25": _quantile(confidences, 0.25),
            "median": _quantile(confidences, 0.50),
            "p75": _quantile(confidences, 0.75),
            "p90": _quantile(confidences, 0.90),
        },
        "distance2d": {
            "within5": sum(value <= 5 for value in distances),
            "within15": sum(value <= 15 for value in distances),
            "within40": sum(value <= 40 for value in distances),
            "within80": sum(value <= 80 for value in distances),
            "median": _quantile(distances, 0.50),
            "p90": _quantile(distances, 0.90),
        },
        "byZone": by_zone,
        "errors": [{"pageTitle": row.get("pageTitle"), "error": row.get("error")}
                   for row in errors],
    }


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def audit(conn, era: str, lookup_url: str, output: Path, workers: int = 2,
          limit: int | None = None, progress=None, resume: bool = True) -> dict:
    """Resolve the era, checkpointing after every ten quests and on failure."""
    titles = quest_titles(conn, era)
    if limit is not None:
        titles = titles[:limit]

    previous: dict[str, dict] = {}
    if resume and output.exists():
        try:
            saved = json.loads(output.read_text())
            if (saved.get("schemaVersion") == SCHEMA_VERSION
                    and saved.get("era") == era
                    and saved.get("source", {}).get("lookupUrl") == lookup_url):
                previous = {row["pageTitle"]: row for row in saved.get("quests", [])
                            if row.get("status") == "ok"}
        except (OSError, json.JSONDecodeError, KeyError):
            previous = {}

    results = {title: previous[title] for title in titles if title in previous}

    def snapshot() -> dict:
        ordered = [results[title] for title in titles if title in results]
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": utc_now(),
            "era": era,
            "complete": len(ordered) == len(titles),
            "source": {
                "lookupUrl": lookup_url,
                "plannerSync": planner_sync(conn, era),
            },
            "summary": summarise(ordered),
            "quests": ordered,
        }

    pending = [title for title in titles if title not in results]
    if progress:
        progress(len(results), len(titles), "resumed" if results else "starting")
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = {pool.submit(lookup_quest, title, lookup_url): title
               for title in pending}
    try:
        try:
            for future in as_completed(futures):
                title = futures[future]
                try:
                    results[title] = future.result()
                except BaseException as exc:
                    results[title] = {"pageTitle": title, "status": "error",
                                      "error": f"{type(exc).__name__}: {exc}"}
                done = len(results)
                if progress:
                    progress(done, len(titles), title)
                if done % 10 == 0:
                    atomic_write(output, snapshot())
        except BaseException:
            for future in futures:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown()
    finally:
        atomic_write(output, snapshot())
    return snapshot()
