import requests

from . import google_oauth
from ._common import log

_API = "https://photoslibrary.googleapis.com/v1"

TOOL_DECLARATIONS = [
    {
        "name": "google_photos_list_recent",
        "description": "Lists recently added Google Photos items.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"max_results": {"type": "INTEGER", "description": "Max items to return (default 10)"}},
        },
    },
    {
        "name": "google_photos_search_by_category",
        "description": "Searches Google Photos by content category (the API doesn't support free-text search — category is the closest match).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {"type": "STRING", "description": "One of: PEOPLE, ANIMALS, LANDSCAPES, TRAVEL, FOOD, PETS, SELFIES, CITYSCAPES, SPORT, PERFORMANCES, BIRTHDAYS, SCREENSHOTS, DOCUMENTS"},
            },
            "required": ["category"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = google_oauth.get_credentials()
    if not creds:
        return "Google Photos isn't connected — connect your Google Account in the Integrations tab first."
    headers = {"Authorization": f"Bearer {creds.token}"}

    try:
        if name == "google_photos_list_recent":
            r = requests.post(
                f"{_API}/mediaItems:search", headers=headers,
                json={"pageSize": args.get("max_results", 10)}, timeout=15,
            )
            r.raise_for_status()
            items = r.json().get("mediaItems", [])
            if not items:
                return "No photos found."
            lines = [f"{i.get('filename', '(untitled)')} — {i.get('mediaMetadata', {}).get('creationTime', '')}" for i in items]
            return "Recent photos:\n" + "\n".join(lines)

        if name == "google_photos_search_by_category":
            r = requests.post(
                f"{_API}/mediaItems:search", headers=headers,
                json={"pageSize": 10, "filters": {"contentFilter": {"includedContentCategories": [args["category"]]}}},
                timeout=15,
            )
            r.raise_for_status()
            items = r.json().get("mediaItems", [])
            if not items:
                return f"No photos found in category '{args['category']}'."
            lines = [i.get("filename", "(untitled)") for i in items]
            return "Photos:\n" + "\n".join(lines)

    except Exception as e:
        msg = f"Google Photos error: {e}"
        log("Google Photos", msg, player)
        return msg

    return "Unknown Google Photos action."
