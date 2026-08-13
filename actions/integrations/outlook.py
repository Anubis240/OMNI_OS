import requests

from . import microsoft_oauth
from ._common import log

_GRAPH = "https://graph.microsoft.com/v1.0"

TOOL_DECLARATIONS = [
    {
        "name": "outlook_list_unread",
        "description": "Lists recent unread Outlook messages (sender and subject).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"max_results": {"type": "INTEGER", "description": "Max messages to return (default 10)"}},
        },
    },
    {
        "name": "outlook_send",
        "description": "Sends an email via Outlook.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "to":      {"type": "STRING", "description": "Recipient email address"},
                "subject": {"type": "STRING", "description": "Email subject"},
                "body":    {"type": "STRING", "description": "Email body (plain text)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    token = microsoft_oauth.get_access_token()
    if not token:
        return "Outlook isn't connected — connect your Microsoft Account in the Integrations tab first."
    headers = {"Authorization": f"Bearer {token}"}

    try:
        if name == "outlook_list_unread":
            r = requests.get(
                f"{_GRAPH}/me/mailFolders/inbox/messages",
                headers=headers,
                params={"$filter": "isRead eq false", "$top": args.get("max_results", 10),
                        "$select": "from,subject"},
                timeout=15,
            )
            r.raise_for_status()
            msgs = r.json().get("value", [])
            if not msgs:
                return "No unread messages."
            lines = [f"From {m['from']['emailAddress']['address']} — {m['subject']}" for m in msgs]
            return "Unread:\n" + "\n".join(lines)

        if name == "outlook_send":
            payload = {
                "message": {
                    "subject": args["subject"],
                    "body": {"contentType": "Text", "content": args["body"]},
                    "toRecipients": [{"emailAddress": {"address": args["to"]}}],
                }
            }
            r = requests.post(f"{_GRAPH}/me/sendMail", headers=headers, json=payload, timeout=15)
            r.raise_for_status()
            return f"Email sent to {args['to']}."

    except requests.HTTPError as e:
        msg = f"Outlook API error: {e.response.status_code} {e.response.text[:200]}"
        log("Outlook", msg, player)
        return msg
    except Exception as e:
        msg = f"Outlook error: {e}"
        log("Outlook", msg, player)
        return msg

    return "Unknown Outlook action."
