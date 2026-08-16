"""Import wikq2's structured original-class epic timelines."""

import json
import os
import subprocess
import time
from pathlib import Path

EXPECTED = 24


def wikq2_repo() -> Path:
    configured = os.environ.get("WIKQ2_REPO")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "wikq2"


def export(repo: Path | None = None) -> dict:
    root = repo or wikq2_repo()
    command = [str(root / "node_modules" / ".bin" / "tsx"),
               "scripts/export-epic-timelines.ts"]
    result = subprocess.run(command, cwd=root, text=True, capture_output=True,
                            check=True)
    data = json.loads(result.stdout)
    validate(data)
    return data


def validate(data: dict) -> None:
    timelines = data.get("timelines") or []
    if data.get("version") != 1 or len(timelines) != EXPECTED:
        raise ValueError("wikq2 epic export must contain version 1 and 24 timelines")
    titles = set()
    classes = set()
    for timeline in timelines:
        title = timeline.get("title")
        class_name = timeline.get("class_name")
        quests = timeline.get("quests") or []
        if not title or not class_name or not quests or title in titles or class_name in classes:
            raise ValueError(f"invalid or duplicate wikq2 epic timeline: {title!r}")
        quest_titles = [quest.get("title") for quest in quests]
        if None in quest_titles or len(quest_titles) != len(set(quest_titles)):
            raise ValueError(f"invalid quest chain in wikq2 epic timeline: {title}")
        titles.add(title)
        classes.add(class_name)


def store(conn, data: dict) -> int:
    validate(data)
    now = int(time.time())
    rows = [{
        "title": timeline["title"],
        "class_name": timeline["class_name"],
        "quests_json": json.dumps(timeline["quests"], separators=(",", ":")),
        "requirements_json": json.dumps(timeline.get("requirements") or [], separators=(",", ":")),
        "source_url": timeline["source_url"],
        "source_version": data["version"],
        "fetched_ts": now,
    } for timeline in data["timelines"]]
    with conn:
        conn.executemany(
            "INSERT INTO plan_epic_timelines "
            "(title,class_name,quests_json,requirements_json,source_url,source_version,fetched_ts) "
            "VALUES (:title,:class_name,:quests_json,:requirements_json,:source_url,:source_version,:fetched_ts) "
            "ON CONFLICT(title) DO UPDATE SET class_name=excluded.class_name, "
            "quests_json=excluded.quests_json, requirements_json=excluded.requirements_json, "
            "source_url=excluded.source_url, source_version=excluded.source_version, "
            "fetched_ts=excluded.fetched_ts",
            rows)
        conn.execute(
            f"DELETE FROM plan_epic_timelines WHERE title NOT IN ({','.join('?' * len(rows))})",
            [row["title"] for row in rows])
    return len(rows)
