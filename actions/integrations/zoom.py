import time

import requests

from ._common import get_credentials, log

_token_cache: dict[str, tuple[str, float]] = {}  # account_id -> (token, expires_at)

TOOL_DECLARATIONS = [
    {
        "name": "zoom_create_meeting",
        "description": "Creates a Zoom meeting and returns the join link.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic":            {"type": "STRING", "description": "Meeting topic/title"},
                "start_time_iso":   {"type": "STRING", "description": "Start time, ISO 8601, e.g. 2026-08-15T14:00:00Z"},
                "duration_minutes": {"type": "INTEGER", "description": "Duration in minutes (default 30)"},
            },
            "required": ["topic", "start_time_iso"],
        },
    },
    {
        "name": "zoom_list_meetings",
        "description": "Lists upcoming scheduled Zoom meetings.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def _get_token(creds: dict) -> str:
    account_id = creds["account_id"]
    cached = _token_cache.get(account_id)
    if cached and cached[1] > time.time() + 30:
        return cached[0]

    r = requests.post(
        "https://zoom.us/oauth/token",
        params={"grant_type": "account_credentials", "account_id": account_id},
        auth=(creds["client_id"], creds["client_secret"]),
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    token = data["access_token"]
    _token_cache[account_id] = (token, time.time() + data.get("expires_in", 3600))
    return token


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("zoom")
    if not creds:
        return "Zoom isn't connected — add your Server-to-Server OAuth app credentials in the Integrations tab."

    try:
        token = _get_token(creds)
        headers = {"Authorization": f"Bearer {token}"}

        if name == "zoom_create_meeting":
            payload = {
                "topic": args["topic"],
                "type": 2,  # scheduled meeting
                "start_time": args["start_time_iso"],
                "duration": args.get("duration_minutes", 30),
            }
            r = requests.post("https://api.zoom.us/v2/users/me/meetings", headers=headers, json=payload, timeout=15)
            r.raise_for_status()
            meeting = r.json()
            return f"Created Zoom meeting '{args['topic']}': {meeting['join_url']}"

        if name == "zoom_list_meetings":
            r = requests.get("https://api.zoom.us/v2/users/me/meetings", headers=headers,
                              params={"type": "upcoming"}, timeout=15)
            r.raise_for_status()
            meetings = r.json().get("meetings", [])
            if not meetings:
                return "No upcoming Zoom meetings."
            lines = [f"{m['topic']} — {m['start_time']}" for m in meetings]
            return "Upcoming meetings:\n" + "\n".join(lines)

    except requests.HTTPError as e:
        msg = f"Zoom API error: {e.response.status_code} {e.response.text[:200]}"
        log("Zoom", msg, player)
        return msg
    except Exception as e:
        msg = f"Zoom error: {e}"
        log("Zoom", msg, player)
        return msg

    return "Unknown Zoom action."
