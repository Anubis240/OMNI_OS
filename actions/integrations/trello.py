import requests

from ._common import get_credentials, log

_API = "https://api.trello.com/1"

TOOL_DECLARATIONS = [
    {
        "name": "trello_list_boards",
        "description": "Lists the user's Trello boards.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "trello_create_card",
        "description": "Creates a card in a Trello list.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "list_id": {"type": "STRING", "description": "The Trello list ID to add the card to"},
                "name":    {"type": "STRING", "description": "Card title"},
                "desc":    {"type": "STRING", "description": "Card description"},
            },
            "required": ["list_id", "name"],
        },
    },
]


def _auth(creds: dict) -> dict:
    return {"key": creds["api_key"], "token": creds["token"]}


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("trello")
    if not creds:
        return "Trello isn't connected — add an API Key and Token in the Integrations tab."

    try:
        if name == "trello_list_boards":
            r = requests.get(f"{_API}/members/me/boards", params={**_auth(creds), "fields": "name,url"}, timeout=15)
            r.raise_for_status()
            boards = r.json()
            if not boards:
                return "No Trello boards found."
            return "Boards:\n" + "\n".join(f"{b['name']} — {b['url']}" for b in boards)

        if name == "trello_create_card":
            payload = {**_auth(creds), "idList": args["list_id"], "name": args["name"], "desc": args.get("desc", "")}
            r = requests.post(f"{_API}/cards", params=payload, timeout=15)
            r.raise_for_status()
            card = r.json()
            return f"Created card '{card['name']}': {card['shortUrl']}"

    except requests.HTTPError as e:
        msg = f"Trello API error: {e.response.status_code} {e.response.text[:200]}"
        log("Trello", msg, player)
        return msg
    except Exception as e:
        msg = f"Trello error: {e}"
        log("Trello", msg, player)
        return msg

    return "Unknown Trello action."
