"""Interactive OAuth connect flows, keyed by the catalog's connect_action.
Each is blocking (opens a browser, waits for consent) — the UI must run
these off the Qt thread."""

from . import google_oauth, microsoft_oauth

CONNECT_ACTIONS = {
    "google_connect": google_oauth.connect,
    "microsoft_connect": microsoft_oauth.connect,
}
