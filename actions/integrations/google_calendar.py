from datetime import datetime, timezone

from googleapiclient.discovery import build

from . import google_oauth
from ._common import log

TOOL_DECLARATIONS = [
    {
        "name": "calendar_list_events",
        "description": "Lists upcoming Google Calendar events.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"max_results": {"type": "INTEGER", "description": "Max events to return (default 10)"}},
        },
    },
    {
        "name": "calendar_create_event",
        "description": "Creates a Google Calendar event.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "summary":     {"type": "STRING", "description": "Event title"},
                "start_iso":   {"type": "STRING", "description": "Start time, ISO 8601, e.g. 2026-08-12T14:00:00"},
                "end_iso":     {"type": "STRING", "description": "End time, ISO 8601"},
                "description": {"type": "STRING", "description": "Event description"},
            },
            "required": ["summary", "start_iso", "end_iso"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = google_oauth.get_credentials()
    if not creds:
        return "Google Calendar isn't connected — connect your Google Account in the Integrations tab first."

    try:
        service = build("calendar", "v3", credentials=creds)

        if name == "calendar_list_events":
            now = datetime.now(timezone.utc).isoformat()
            resp = service.events().list(
                calendarId="primary", timeMin=now, maxResults=args.get("max_results", 10),
                singleEvents=True, orderBy="startTime",
            ).execute()
            events = resp.get("items", [])
            if not events:
                return "No upcoming events."
            lines = []
            for e in events:
                start = e["start"].get("dateTime", e["start"].get("date"))
                lines.append(f"{start} — {e.get('summary', '(no title)')}")
            return "Upcoming events:\n" + "\n".join(lines)

        if name == "calendar_create_event":
            body = {
                "summary": args["summary"],
                "description": args.get("description", ""),
                "start": {"dateTime": args["start_iso"]},
                "end": {"dateTime": args["end_iso"]},
            }
            event = service.events().insert(calendarId="primary", body=body).execute()
            return f"Created event '{args['summary']}': {event.get('htmlLink', '')}"

    except Exception as e:
        msg = f"Google Calendar error: {e}"
        log("Google Calendar", msg, player)
        return msg

    return "Unknown Google Calendar action."
