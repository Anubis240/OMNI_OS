from googleapiclient.discovery import build

from . import google_oauth
from ._common import log

TOOL_DECLARATIONS = [
    {
        "name": "drive_search",
        "description": "Searches Google Drive files by name.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Text to search for in file names"}},
            "required": ["query"],
        },
    },
    {
        "name": "drive_list_recent",
        "description": "Lists recently modified Google Drive files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"max_results": {"type": "INTEGER", "description": "Max files to return (default 10)"}},
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = google_oauth.get_credentials()
    if not creds:
        return "Google Drive isn't connected — connect your Google Account in the Integrations tab first."

    try:
        service = build("drive", "v3", credentials=creds)

        if name == "drive_search":
            resp = service.files().list(
                q=f"name contains '{args['query']}' and trashed = false",
                pageSize=10, fields="files(id, name, webViewLink)",
            ).execute()
            files = resp.get("files", [])
            if not files:
                return f"No Drive files found matching '{args['query']}'."
            return "Files:\n" + "\n".join(f"{f['name']} — {f['webViewLink']}" for f in files)

        if name == "drive_list_recent":
            resp = service.files().list(
                pageSize=args.get("max_results", 10), orderBy="modifiedTime desc",
                fields="files(id, name, webViewLink, modifiedTime)",
            ).execute()
            files = resp.get("files", [])
            if not files:
                return "No files found."
            return "Recent files:\n" + "\n".join(f"{f['name']} — {f['webViewLink']}" for f in files)

    except Exception as e:
        msg = f"Google Drive error: {e}"
        log("Google Drive", msg, player)
        return msg

    return "Unknown Google Drive action."
