from googleapiclient.discovery import build

from . import google_oauth
from ._common import log

TOOL_DECLARATIONS = [
    {
        "name": "docs_read",
        "description": "Reads the text content of a Google Doc.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"document_id": {"type": "STRING", "description": "The document ID (from its URL)"}},
            "required": ["document_id"],
        },
    },
    {
        "name": "docs_create",
        "description": "Creates a new Google Doc with the given text content.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title":   {"type": "STRING", "description": "Document title"},
                "content": {"type": "STRING", "description": "Body text"},
            },
            "required": ["title"],
        },
    },
]


def _extract_text(doc: dict) -> str:
    out = []
    for el in doc.get("body", {}).get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
        for run in para.get("elements", []):
            text_run = run.get("textRun")
            if text_run:
                out.append(text_run.get("content", ""))
    return "".join(out).strip()


def dispatch(name: str, args: dict, player=None) -> str:
    creds = google_oauth.get_credentials()
    if not creds:
        return "Google Docs isn't connected — connect your Google Account in the Integrations tab first."

    try:
        service = build("docs", "v1", credentials=creds)

        if name == "docs_read":
            doc = service.documents().get(documentId=args["document_id"]).execute()
            text = _extract_text(doc)
            return text if text else "That document appears to be empty."

        if name == "docs_create":
            doc = service.documents().create(body={"title": args["title"]}).execute()
            doc_id = doc["documentId"]
            if args.get("content"):
                service.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": [{"insertText": {"location": {"index": 1}, "text": args["content"]}}]},
                ).execute()
            return f"Created doc '{args['title']}': https://docs.google.com/document/d/{doc_id}/edit"

    except Exception as e:
        msg = f"Google Docs error: {e}"
        log("Google Docs", msg, player)
        return msg

    return "Unknown Google Docs action."
