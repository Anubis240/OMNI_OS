import requests

from ._common import get_credentials, log

_API = "https://api.calendly.com"

TOOL_DECLARATIONS = [
    {
        "name": "calendly_get_scheduling_link",
        "description": "Gets the user's Calendly scheduling page link.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "calendly_list_upcoming_events",
        "description": "Lists upcoming scheduled Calendly events.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def _me(headers: dict) -> dict:
    r = requests.get(f"{_API}/users/me", headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()["resource"]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("calendly")
    if not creds:
        return "Calendly isn't connected — add a Personal Access Token in the Integrations tab."
    headers = {"Authorization": f"Bearer {creds['token']}"}

    try:
        user = _me(headers)

        if name == "calendly_get_scheduling_link":
            return f"Scheduling link: {user['scheduling_url']}"

        if name == "calendly_list_upcoming_events":
            r = requests.get(
                f"{_API}/scheduled_events",
                headers=headers,
                params={"user": user["uri"], "status": "active", "sort": "start_time:asc"},
                timeout=15,
            )
            r.raise_for_status()
            events = r.json().get("collection", [])
            if not events:
                return "No upcoming Calendly events."
            lines = [f"{e['name']} — {e['start_time']}" for e in events]
            return "Upcoming events:\n" + "\n".join(lines)

    except requests.HTTPError as e:
        msg = f"Calendly API error: {e.response.status_code} {e.response.text[:200]}"
        log("Calendly", msg, player)
        return msg
    except Exception as e:
        msg = f"Calendly error: {e}"
        log("Calendly", msg, player)
        return msg

    return "Unknown Calendly action."
