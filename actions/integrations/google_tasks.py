from googleapiclient.discovery import build

from . import google_oauth
from ._common import log

TOOL_DECLARATIONS = [
    {
        "name": "gtasks_list",
        "description": "Lists tasks in the user's default Google Tasks list.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "gtasks_create",
        "description": "Creates a task in Google Tasks.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Task title"},
                "notes": {"type": "STRING", "description": "Task notes"},
                "due":   {"type": "STRING", "description": "Due date, RFC3339 e.g. 2026-08-15T00:00:00Z"},
            },
            "required": ["title"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = google_oauth.get_credentials()
    if not creds:
        return "Google Tasks isn't connected — connect your Google Account in the Integrations tab first."

    try:
        service = build("tasks", "v1", credentials=creds)

        if name == "gtasks_list":
            resp = service.tasks().list(tasklist="@default").execute()
            tasks = resp.get("items", [])
            if not tasks:
                return "No tasks."
            lines = [f"{'[x]' if t.get('status') == 'completed' else '[ ]'} {t['title']}" for t in tasks]
            return "Tasks:\n" + "\n".join(lines)

        if name == "gtasks_create":
            body = {"title": args["title"]}
            if args.get("notes"):
                body["notes"] = args["notes"]
            if args.get("due"):
                body["due"] = args["due"]
            service.tasks().insert(tasklist="@default", body=body).execute()
            return f"Created task '{args['title']}'."

    except Exception as e:
        msg = f"Google Tasks error: {e}"
        log("Google Tasks", msg, player)
        return msg

    return "Unknown Google Tasks action."
