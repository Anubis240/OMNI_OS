import msal

from core import settings_store
from ._common import log

SCOPES = ["Mail.ReadWrite", "Mail.Send", "Files.ReadWrite", "Calendars.ReadWrite"]


def _entry() -> dict | None:
    entry = settings_store.load_settings()["integrations"].get("microsoft")
    if not entry or not entry.get("enabled"):
        return None
    return entry


def _app(entry: dict) -> msal.PublicClientApplication:
    authority = f"https://login.microsoftonline.com/{entry.get('tenant_id') or 'common'}"
    cache = msal.SerializableTokenCache()
    if entry.get("token_cache"):
        cache.deserialize(entry["token_cache"])
    app = msal.PublicClientApplication(entry["client_id"], authority=authority, token_cache=cache)
    return app


def _persist_cache(app: msal.PublicClientApplication) -> None:
    if not app.token_cache.has_state_changed:
        return
    settings = settings_store.load_settings()
    settings["integrations"]["microsoft"]["token_cache"] = app.token_cache.serialize()
    settings_store.save_settings(settings)


def get_access_token() -> str | None:
    """A ready-to-use Graph API bearer token, or None if not connected yet."""
    entry = _entry()
    if not entry or not entry.get("token_cache"):
        return None
    app = _app(entry)
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _persist_cache(app)
    if not result or "access_token" not in result:
        return None
    return result["access_token"]


def connect(player=None) -> str:
    """Runs the interactive consent flow — opens the system browser, waits
    for approval. Blocking; call from a worker thread, never the UI thread."""
    entry = _entry()
    if not entry or not entry.get("client_id"):
        return "Add your Azure App (Client) ID first, then click Connect."

    app = _app(entry)
    try:
        result = app.acquire_token_interactive(scopes=SCOPES)
    except Exception as e:
        msg = f"Microsoft connect failed: {e}"
        log("Microsoft", msg, player)
        return msg

    _persist_cache(app)
    if "access_token" not in result:
        msg = f"Microsoft connect failed: {result.get('error_description', result.get('error', 'unknown error'))}"
        log("Microsoft", msg, player)
        return msg

    log("Microsoft", "Account connected.", player)
    return "Microsoft account connected."
