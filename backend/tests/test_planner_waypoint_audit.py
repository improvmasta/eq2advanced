import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner import waypoint_audit


def _coord(zone="Kylong Plains", confidence=None, distance=3,
           elevation=1):
    row = {"x": 1, "y": 2, "z": 3, "zoneTitle": zone}
    if confidence is not None:
        row["mapMatch"] = {
            "confidence": confidence,
            "distance2d": distance,
            "elevationDelta": elevation,
        }
    return row


def test_summarise_separates_overall_and_zone_labeled_coverage():
    summary = waypoint_audit.summarise([
        {"pageTitle": "One", "status": "ok", "coordinates": [
            _coord(confidence=.98),
            _coord(confidence=.84, distance=18, elevation=9),
            _coord(zone=""),
            {"raw": "/waypoint nope", "zoneTitle": "Kylong Plains"},
        ]},
        {"pageTitle": "Two", "status": "ok", "coordinates": []},
        {"pageTitle": "Broken", "status": "error", "error": "timeout"},
    ])

    assert summary["quests"] == {
        "total": 3, "resolved": 2, "errors": 1, "withCoordinates": 1,
    }
    assert summary["coordinates"] == {
        "extracted": 4,
        "numeric": 3,
        "zoneLabeled": 2,
        "missingZone": 1,
        "matched": 2,
        "matchRateOverall": 50.0,
        "matchRateZoneLabeled": 100.0,
        "sameFloor": 1,
    }
    assert summary["confidence"]["buckets"]["0.70-0.84"] == 1
    assert summary["confidence"]["buckets"]["0.95-0.98"] == 1
    assert summary["distance2d"]["within5"] == 1
    assert summary["errors"] == [{"pageTitle": "Broken", "error": "timeout"}]


def test_audit_resumes_successes_and_retries_errors(tmp_path, monkeypatch):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE plan_quests (page_title TEXT, era TEXT);
        CREATE TABLE plan_syncs (
          era TEXT, items INT, sources INT, sets INT, quests INT,
          edges INT, pages INT, synced_ts INT
        );
        INSERT INTO plan_quests VALUES ('Alpha', 'rok'), ('Beta', 'rok');
        INSERT INTO plan_syncs VALUES ('rok', 1, 2, 3, 2, 1, 10, 123);
    """)
    output = tmp_path / "audit.json"
    output.write_text(json.dumps({
        "schemaVersion": 1,
        "era": "rok",
        "source": {"lookupUrl": "http://wikq2/api/lookup"},
        "quests": [
            {"pageTitle": "Alpha", "status": "ok", "coordinates": []},
            {"pageTitle": "Beta", "status": "error", "error": "old"},
        ],
    }))
    called = []

    def fake_lookup(title, _url):
        called.append(title)
        return {"pageTitle": title, "status": "ok", "coordinates": []}

    monkeypatch.setattr(waypoint_audit, "lookup_quest", fake_lookup)
    report = waypoint_audit.audit(
        conn, "rok", "http://wikq2/api/lookup", output, workers=1,
    )

    assert called == ["Beta"]
    assert report["complete"] is True
    assert report["source"]["plannerSync"]["syncedTs"] == 123
    assert report["summary"]["quests"]["resolved"] == 2
