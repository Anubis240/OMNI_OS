import requests

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "slack_send_message",
        "description": "Sends a message to a Slack channel via the connected Slack bot.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "channel": {"type": "STRING", "description": "Channel name (e.g. '#general') or channel ID"},
                "text":    {"type": "STRING", "description": "Message text"},
            },
            "required": ["channel", "text"],
        },
    },
    {
        "name": "slack_list_channels",
        "description": "Lists Slack channels the bot can see.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def _headers(creds: dict) -> dict:
    return {"Authorization": f"Bearer {creds['bot_token']}", "Content-Type": "application/json; charset=utf-8"}


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("slack")
    if not creds:
        return "Slack isn't connected — add a Bot User OAuth Token in the Integrations tab."

    try:
        if name == "slack_send_message":
            r = requests.post(
                "https://slack.com/api/chat.postMessage", headers=_headers(creds),
                json={"channel": args["channel"], "text": args["text"]}, timeout=15,
            )
            data = r.json()
            if not data.get("ok"):
                return f"Slack error: {data.get('error', 'unknown')}"
            return f"Message sent to {args['channel']} on Slack."

        if name == "slack_list_channels":
            r = requests.get(
                "https://slack.com/api/conversations.list", headers=_headers(creds),
                params={"limit": 50}, timeout=15,
            )
            data = r.json()
            if not data.get("ok"):
                return f"Slack error: {data.get('error', 'unknown')}"
            names = [f"#{c['name']}" for c in data.get("channels", [])]
            return "Channels: " + ", ".join(names) if names else "No channels visible to this bot."

    except Exception as e:
        msg = f"Slack error: {e}"
        log("Slack", msg, player)
        return msg

    return "Unknown Slack action."
