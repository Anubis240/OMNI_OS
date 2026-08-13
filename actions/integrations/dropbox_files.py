import requests

from ._common import get_credentials, log

_API = "https://api.dropboxapi.com/2"

TOOL_DECLARATIONS = [
    {
        "name": "dropbox_search",
        "description": "Searches Dropbox files by name.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Text to search for"}},
            "required": ["query"],
        },
    },
    {
        "name": "dropbox_list_folder",
        "description": "Lists files in a Dropbox folder.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING", "description": "Folder path, empty string for root (default: root)"}},
        },
    },
]


def _headers(creds: dict) -> dict:
    return {"Authorization": f"Bearer {creds['access_token']}", "Content-Type": "application/json"}


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("dropbox")
    if not creds:
        return "Dropbox isn't connected — add an Access Token in the Integrations tab."

    try:
        if name == "dropbox_search":
            r = requests.post(
                f"{_API}/files/search_v2", headers=_headers(creds),
                json={"query": args["query"]}, timeout=15,
            )
            r.raise_for_status()
            matches = r.json().get("matches", [])
            if not matches:
                return f"No Dropbox files found matching '{args['query']}'."
            lines = [m["metadata"]["metadata"]["path_display"] for m in matches[:10]]
            return "Files:\n" + "\n".join(lines)

        if name == "dropbox_list_folder":
            r = requests.post(
                f"{_API}/files/list_folder", headers=_headers(creds),
                json={"path": args.get("path", "")}, timeout=15,
            )
            r.raise_for_status()
            entries = r.json().get("entries", [])
            if not entries:
                return "That folder is empty."
            lines = [f"{'[dir]' if e['.tag'] == 'folder' else '[file]'} {e['name']}" for e in entries]
            return "Contents:\n" + "\n".join(lines)

    except requests.HTTPError as e:
        msg = f"Dropbox API error: {e.response.status_code} {e.response.text[:200]}"
        log("Dropbox", msg, player)
        return msg
    except Exception as e:
        msg = f"Dropbox error: {e}"
        log("Dropbox", msg, player)
        return msg

    return "Unknown Dropbox action."
