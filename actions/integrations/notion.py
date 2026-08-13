import requests

from ._common import get_credentials, log

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"

TOOL_DECLARATIONS = [
    {
        "name": "notion_search",
        "description": "Searches Notion pages and databases that have been shared with the Omni-OS integration.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Search text"}},
            "required": ["query"],
        },
    },
    {
        "name": "notion_create_page",
        "description": "Creates a new Notion page inside a parent page that's been shared with the integration.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "parent_page_id": {"type": "STRING", "description": "The parent Notion page ID"},
                "title":          {"type": "STRING", "description": "Title of the new page"},
                "content":        {"type": "STRING", "description": "Plain-text body content"},
            },
            "required": ["parent_page_id", "title"],
        },
    },
]


def _headers(creds: dict) -> dict:
    return {
        "Authorization": f"Bearer {creds['token']}",
        "Notion-Version": _VERSION,
        "Content-Type": "application/json",
    }


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("notion")
    if not creds:
        return "Notion isn't connected — add an Internal Integration Token in the Integrations tab."

    try:
        if name == "notion_search":
            r = requests.post(
                f"{_API}/search", headers=_headers(creds),
                json={"query": args["query"], "page_size": 8}, timeout=15,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return f"No Notion pages found for '{args['query']}'. Remember pages must be shared with the integration first."
            lines = []
            for item in results:
                props = item.get("properties", {})
                title = "Untitled"
                for prop in props.values():
                    if prop.get("type") == "title" and prop.get("title"):
                        title = "".join(t.get("plain_text", "") for t in prop["title"])
                        break
                lines.append(f"{title} — {item.get('url', '')}")
            return "Notion results:\n" + "\n".join(lines)

        if name == "notion_create_page":
            payload = {
                "parent": {"page_id": args["parent_page_id"]},
                "properties": {"title": {"title": [{"text": {"content": args["title"]}}]}},
            }
            if args.get("content"):
                payload["children"] = [{
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": args["content"]}}]},
                }]
            r = requests.post(f"{_API}/pages", headers=_headers(creds), json=payload, timeout=15)
            r.raise_for_status()
            page = r.json()
            return f"Created Notion page: {page.get('url', args['title'])}"

    except requests.HTTPError as e:
        msg = f"Notion API error: {e.response.status_code} {e.response.text[:200]}"
        log("Notion", msg, player)
        return msg
    except Exception as e:
        msg = f"Notion error: {e}"
        log("Notion", msg, player)
        return msg

    return "Unknown Notion action."
