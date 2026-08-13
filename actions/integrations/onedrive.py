import requests

from . import microsoft_oauth
from ._common import log

_GRAPH = "https://graph.microsoft.com/v1.0"

TOOL_DECLARATIONS = [
    {
        "name": "onedrive_search",
        "description": "Searches OneDrive files by name.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Text to search for"}},
            "required": ["query"],
        },
    },
    {
        "name": "onedrive_list_recent",
        "description": "Lists recently modified OneDrive files.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    token = microsoft_oauth.get_access_token()
    if not token:
        return "OneDrive isn't connected — connect your Microsoft Account in the Integrations tab first."
    headers = {"Authorization": f"Bearer {token}"}

    try:
        if name == "onedrive_search":
            r = requests.get(f"{_GRAPH}/me/drive/root/search(q='{args['query']}')", headers=headers, timeout=15)
            r.raise_for_status()
            items = r.json().get("value", [])
            if not items:
                return f"No OneDrive files found matching '{args['query']}'."
            lines = [f"{i['name']} — {i.get('webUrl', '')}" for i in items]
            return "Files:\n" + "\n".join(lines)

        if name == "onedrive_list_recent":
            r = requests.get(f"{_GRAPH}/me/drive/recent", headers=headers, timeout=15)
            r.raise_for_status()
            items = r.json().get("value", [])
            if not items:
                return "No recent files."
            lines = [f"{i['name']} — {i.get('webUrl', '')}" for i in items]
            return "Recent files:\n" + "\n".join(lines)

    except requests.HTTPError as e:
        msg = f"OneDrive API error: {e.response.status_code} {e.response.text[:200]}"
        log("OneDrive", msg, player)
        return msg
    except Exception as e:
        msg = f"OneDrive error: {e}"
        log("OneDrive", msg, player)
        return msg

    return "Unknown OneDrive action."
