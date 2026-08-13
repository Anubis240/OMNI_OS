import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build

from . import google_oauth
from ._common import log

TOOL_DECLARATIONS = [
    {
        "name": "gmail_list_unread",
        "description": "Lists recent unread Gmail messages (sender and subject).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"max_results": {"type": "INTEGER", "description": "Max messages to return (default 10)"}},
        },
    },
    {
        "name": "gmail_send",
        "description": "Sends an email via Gmail.",
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
    creds = google_oauth.get_credentials()
    if not creds:
        return "Gmail isn't connected — connect your Google Account in the Integrations tab first."

    try:
        service = build("gmail", "v1", credentials=creds)

        if name == "gmail_list_unread":
            max_results = args.get("max_results", 10)
            resp = service.users().messages().list(userId="me", labelIds=["UNREAD"], maxResults=max_results).execute()
            msg_ids = resp.get("messages", [])
            if not msg_ids:
                return "No unread messages."
            lines = []
            for m in msg_ids:
                msg = service.users().messages().get(userId="me", id=m["id"], format="metadata",
                                                       metadataHeaders=["From", "Subject"]).execute()
                headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
                lines.append(f"From {headers.get('From', '?')} — {headers.get('Subject', '(no subject)')}")
            return "Unread:\n" + "\n".join(lines)

        if name == "gmail_send":
            mime = MIMEText(args["body"])
            mime["to"] = args["to"]
            mime["subject"] = args["subject"]
            raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return f"Email sent to {args['to']}."

    except Exception as e:
        msg = f"Gmail error: {e}"
        log("Gmail", msg, player)
        return msg

    return "Unknown Gmail action."
