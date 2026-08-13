from googleapiclient.discovery import build

from . import google_oauth
from ._common import log

TOOL_DECLARATIONS = [
    {
        "name": "youtube_search",
        "description": "Searches YouTube videos.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Search text"}},
            "required": ["query"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = google_oauth.get_credentials()
    if not creds:
        return "YouTube isn't connected — connect your Google Account in the Integrations tab first."

    try:
        service = build("youtube", "v3", credentials=creds)

        if name == "youtube_search":
            resp = service.search().list(
                q=args["query"], part="snippet", type="video", maxResults=8,
            ).execute()
            items = resp.get("items", [])
            if not items:
                return f"No YouTube results for '{args['query']}'."
            lines = [
                f"{i['snippet']['title']} — https://youtube.com/watch?v={i['id']['videoId']}"
                for i in items
            ]
            return "Results:\n" + "\n".join(lines)

    except Exception as e:
        msg = f"YouTube error: {e}"
        log("YouTube", msg, player)
        return msg

    return "Unknown YouTube action."
