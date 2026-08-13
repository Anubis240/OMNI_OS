from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from core import settings_store
from ._common import log

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/photoslibrary.readonly",
]


def _entry() -> dict | None:
    entry = settings_store.load_settings()["integrations"].get("google")
    if not entry or not entry.get("enabled"):
        return None
    return entry


def get_credentials() -> Credentials | None:
    """A ready-to-use, auto-refreshed Credentials object, or None if the
    user hasn't completed the Connect flow yet."""
    entry = _entry()
    if not entry or not entry.get("refresh_token"):
        return None

    creds = Credentials(
        token=entry.get("access_token"),
        refresh_token=entry["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=entry["client_id"],
        client_secret=entry["client_secret"],
        scopes=SCOPES,
    )
    if not creds.valid:
        creds.refresh(Request())
        settings = settings_store.load_settings()
        settings["integrations"]["google"]["access_token"] = creds.token
        settings_store.save_settings(settings)
    return creds


def connect(player=None) -> str:
    """Runs the interactive OAuth consent flow — opens the system browser,
    waits for the user to approve, catches the redirect on a local port.
    Blocking; call from a worker thread, never the UI thread."""
    entry = _entry()
    if not entry or not entry.get("client_id") or not entry.get("client_secret"):
        return "Add your Google OAuth Client ID and Secret first, then click Connect."

    client_config = {
        "installed": {
            "client_id": entry["client_id"],
            "client_secret": entry["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    try:
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
    except Exception as e:
        msg = f"Google connect failed: {e}"
        log("Google", msg, player)
        return msg

    settings = settings_store.load_settings()
    settings["integrations"]["google"]["refresh_token"] = creds.refresh_token
    settings["integrations"]["google"]["access_token"] = creds.token
    settings_store.save_settings(settings)
    log("Google", "Account connected.", player)
    return "Google account connected."
