"""Register the small global command set for the user-installed Discord app.

Run explicitly after the Discord Developer application is configured. Startup
never mutates Discord: a typo in local configuration must not rewrite the live
app's commands merely because uvicorn reloaded.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord_alerts


def command(name: str, description: str, options=None) -> dict:
    out = {
        "name": name,
        "description": description,
        "type": 1,
        # User install only; private DM with the app only. There is no guild
        # installation path hiding behind this registration.
        "integration_types": [1],
        "contexts": [1],
    }
    if options:
        out["options"] = options
    return out


COMMANDS = [
    command("link", "Connect private EQ2Advanced chat alerts", [{
        "type": 3, "name": "code", "description": "Code shown on EQ2Advanced",
        "required": True, "min_length": 8, "max_length": 8,
    }]),
    command("status", "Show whether chat alerts are connected"),
    command("pause", "Pause private chat alerts"),
    command("resume", "Resume private chat alerts"),
    command("unlink", "Disconnect this Discord account from EQ2Advanced"),
]


def main() -> int:
    app_id = discord_alerts.application_id()
    token = discord_alerts.bot_token()
    if not app_id or not token:
        print("Set DISCORD_APPLICATION_ID and DISCORD_BOT_TOKEN first.", file=sys.stderr)
        return 2
    response = httpx.put(
        f"https://discord.com/api/v10/applications/{app_id}/commands",
        headers={"Authorization": f"Bot {token}"}, json=COMMANDS, timeout=20)
    if response.status_code // 100 != 2:
        print(f"Discord returned HTTP {response.status_code}: {response.text}", file=sys.stderr)
        return 1
    print(f"Registered {len(response.json())} user-install DM commands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
