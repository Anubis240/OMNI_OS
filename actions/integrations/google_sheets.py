from googleapiclient.discovery import build

from . import google_oauth
from ._common import log

TOOL_DECLARATIONS = [
    {
        "name": "sheets_read_range",
        "description": "Reads a range of cells from a Google Sheet.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "spreadsheet_id": {"type": "STRING", "description": "The spreadsheet ID (from its URL)"},
                "range_a1":       {"type": "STRING", "description": "A1 notation range, e.g. 'Sheet1!A1:D10'"},
            },
            "required": ["spreadsheet_id", "range_a1"],
        },
    },
    {
        "name": "sheets_append_row",
        "description": "Appends a row of values to a Google Sheet.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "spreadsheet_id": {"type": "STRING", "description": "The spreadsheet ID (from its URL)"},
                "range_a1":       {"type": "STRING", "description": "A1 notation range/sheet name to append after, e.g. 'Sheet1'"},
                "values":         {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Cell values for the new row, in order"},
            },
            "required": ["spreadsheet_id", "range_a1", "values"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = google_oauth.get_credentials()
    if not creds:
        return "Google Sheets isn't connected — connect your Google Account in the Integrations tab first."

    try:
        service = build("sheets", "v4", credentials=creds)

        if name == "sheets_read_range":
            resp = service.spreadsheets().values().get(
                spreadsheetId=args["spreadsheet_id"], range=args["range_a1"],
            ).execute()
            rows = resp.get("values", [])
            if not rows:
                return "That range is empty."
            return "\n".join(" | ".join(row) for row in rows)

        if name == "sheets_append_row":
            service.spreadsheets().values().append(
                spreadsheetId=args["spreadsheet_id"], range=args["range_a1"],
                valueInputOption="USER_ENTERED", body={"values": [args["values"]]},
            ).execute()
            return "Row appended."

    except Exception as e:
        msg = f"Google Sheets error: {e}"
        log("Google Sheets", msg, player)
        return msg

    return "Unknown Google Sheets action."
