import requests
from requests.auth import HTTPBasicAuth

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "confluence_search",
        "description": "Searches Confluence pages by text.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Search text"}},
            "required": ["query"],
        },
    },
    {
        "name": "confluence_create_page",
        "description": "Creates a Confluence page in a space.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "space_key": {"type": "STRING", "description": "Confluence space key"},
                "title":     {"type": "STRING", "description": "Page title"},
                "content":   {"type": "STRING", "description": "Page body (plain text)"},
            },
            "required": ["space_key", "title"],
        },
    },
]


def _auth(creds: dict) -> HTTPBasicAuth:
    return HTTPBasicAuth(creds["email"], creds["api_token"])


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("confluence")
    if not creds:
        return "Confluence isn't connected — add your site URL, email, and API token in the Integrations tab."
    base = f"https://{creds['site_url'].replace('https://', '').rstrip('/')}"

    try:
        if name == "confluence_search":
            r = requests.get(
                f"{base}/wiki/rest/api/content/search", auth=_auth(creds),
                params={"cql": f'text ~ "{args["query"]}"', "limit": 10}, timeout=15,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return f"No Confluence pages found for '{args['query']}'."
            lines = [f"{p['title']} — {base}/wiki{p['_links']['webui']}" for p in results]
            return "Pages:\n" + "\n".join(lines)

        if name == "confluence_create_page":
            payload = {
                "type": "page",
                "title": args["title"],
                "space": {"key": args["space_key"]},
                "body": {"storage": {"value": f"<p>{args.get('content', '')}</p>", "representation": "storage"}},
            }
            r = requests.post(f"{base}/wiki/rest/api/content", auth=_auth(creds), json=payload, timeout=15)
            r.raise_for_status()
            page = r.json()
            return f"Created Confluence page: {base}/wiki{page['_links']['webui']}"

    except requests.HTTPError as e:
        msg = f"Confluence API error: {e.response.status_code} {e.response.text[:200]}"
        log("Confluence", msg, player)
        return msg
    except Exception as e:
        msg = f"Confluence error: {e}"
        log("Confluence", msg, player)
        return msg

    return "Unknown Confluence action."
