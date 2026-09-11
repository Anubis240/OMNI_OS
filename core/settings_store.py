"""User-configurable settings: custom MCP servers, general-purpose API keys
(for those servers/skills to use — Omni's own core LLM stays Gemini),
skill prompt add-ons, and optional Claude Code CLI delegation.

Separate from config/api_keys.json (Gemini key + system settings) since
that file predates this feature and other code already reads it directly —
no reason to touch a working format. This one is purely additive.
"""

from __future__ import annotations

import json
import re
from core.app_paths import get_data_dir
import uuid
from pathlib import Path


def _base_dir() -> Path:
    return get_data_dir()


SETTINGS_PATH = _base_dir() / "config" / "settings.json"

# Single source of truth for the hardcoded Gemini Live model fallback — main.py
# uses it as LIVE_MODEL, settings_panel.py reads it to show the actually-
# resolved model in Settings (a tester asked to verify Omni's own self-report
# independently rather than trust it — see the [YOUR CURRENT MODEL] prompt fix).
DEFAULT_LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"

DEFAULT_SETTINGS = {
    "mcp_servers": [],   # [{id, name, url, apiKey}]
    "api_keys": {"openai": "", "anthropic": ""},
    "custom_api_keys": [],  # [{id, name, value}] — a plain named-key vault for
                            # whatever a hand-written skill or custom MCP server
                            # needs that isn't one of the fixed fields above.
    "skills": [],        # [{id, name, content, enabled}]
    "claude_agent": {"enabled": False, "cliPath": "", "vaultDir": "", "extraDir": ""},
    "codex_agent": {"enabled": False, "cliPath": "", "vaultDir": ""},  # OpenAI Codex CLI —
                    # same shape as claude_agent minus extraDir (no confirmed CLI-level
                    # equivalent to add_dirs; codex's --sandbox mode governs write access
                    # to the --cd'd workspace instead). See actions/codex_companion.py.
    # Four more CLI-delegation backends, same {enabled, cliPath, vaultDir} shape as
    # codex_agent — added 2026-09-09 after reviewing the Hermes Agent skill catalog's
    # "delegate coding to CLI X" family. Each backend's own module documents exactly
    # how confidently its flags were verified (opencode_companion.py and
    # openhands_companion.py against each project's official docs; grok_companion.py
    # and blackbox_companion.py against community sources only — check `--help`
    # before relying on either in production).
    "opencode_agent": {"enabled": False, "cliPath": "", "vaultDir": ""},
    "openhands_agent": {"enabled": False, "cliPath": "", "vaultDir": ""},
    "grok_agent": {"enabled": False, "cliPath": "", "vaultDir": ""},
    "blackbox_agent": {"enabled": False, "cliPath": "", "vaultDir": ""},
    "trader": {"enabled": False},  # optional crypto trading panel add-on, off by default
    "live_model": "",   # Gemini Live model override for the default "Omni" identity
                        # (a companion's own "model" field still wins over this — see
                        # main.py::_build_config()); empty means use the hardcoded
                        # LIVE_MODEL fallback in main.py. Settings → Companions.
    "dashboard_port": 8000,  # Remote Dashboard listen port — configurable because the
                             # original Seraph Guardian app this was forked from defaults
                             # to the same port, so running both at once conflicts.
    "companions": [],    # [{id, name, backend, model, system_prompt, avatar,
                         #   voice, memory_namespace, mcp_server_ids, enabled, specialty}]
                         # backend: "gemini_live" (realtime voice) | "claude_agent" |
                         # "codex_agent" | "opencode_agent" | "openhands_agent" |
                         # "grok_agent" | "blackbox_agent" (all turn-based text/tools,
                         # different CLI each — main.py's _AGENT_BACKENDS/
                         # _agent_send_fn() dispatch on this).
                         # system_prompt only needs to define the persona/identity — the
                         # shared tool-routing/safety rules (core/shared_rules.txt) are
                         # always appended automatically by main.py::_build_config().
                         # Empty by default: _build_config() falls back to the original
                         # single-companion behavior (core/prompt.txt, unnamespaced memory,
                         # self.ui.voice) whenever there's no active companion configured.
                         # specialty: short one-line description of what a claude_agent
                         # companion is good at (e.g. "coding, debugging") — shown in the
                         # World view and given to the lead companion so its
                         # delegate_to_agent tool call can pick the right sub-agent.
                         # Not meaningful for gemini_live companions (nothing routes to
                         # those — see main.py::_build_config()'s sub-agent directory).
    "active_companion_id": "",
    "integrations": {},  # {catalog_id: {field_key: value, ..., "enabled": bool}}
                         # Keyed by INTEGRATION_CATALOG id. Only present once a
                         # user has actually filled in credentials for that
                         # service — absence means "not connected".
}

BUILTIN_COMPANIONS = []  # no built-in personas — Omni (core/prompt.txt) is the
                          # sole default identity; the companion registry stays
                          # in place for anyone who wants to add their own.

# Drives both the Integrations tab grid and the credential-entry form —
# single source of truth for "which integrations exist and what they need."
# auth_type:
#   "credentials" — one or more fields the user pastes in directly (API key,
#                    token, key+secret pair...); no network round-trip needed
#                    to "connect", just save-and-use.
#   "local"       — no credentials at all, just a reachable base URL (Ollama).
#   "oauth"       — needs a real browser-based consent flow. Cataloged now for
#                    the grid's visual completeness; connect_id.action modules
#                    and the actual flow land in a later batch (see
#                    `implemented`).
# implemented: whether actions/integrations/<id>.py exists and is wired into
# main.py's tool registration yet. The UI only offers "Connect" on entries
# where this is True — everything else shows as "Coming soon" rather than
# accepting credentials nothing will ever use.
INTEGRATION_CATALOG = [
    # --- Batch 1: token/local auth, implemented ---
    {"id": "github", "name": "GitHub", "category": "Developer Tools", "auth_type": "credentials",
     "fields": [{"key": "token", "label": "Personal Access Token", "secret": True}],
     "help": "github.com/settings/personal-access-tokens/new — a fine-grained token scoped to just "
             "the repo(s) you want Omni to touch is safer than a classic 'repo'-scope token (full "
             "read/write on every repo, public and private); use classic only if you need broader access.",
     "implemented": True},
    {"id": "notion", "name": "Notion", "category": "Productivity", "auth_type": "credentials",
     "fields": [{"key": "token", "label": "Internal Integration Token", "secret": True}],
     "help": "notion.so/my-integrations — then share each page/database with the integration.", "implemented": True},
    {"id": "obsidian", "name": "Obsidian", "category": "Note-Taking", "auth_type": "local",
     "fields": [{"key": "vault_path", "label": "Vault folder path", "secret": False}],
     "help": "Local files only — no account, no network call. Point this at your vault's root folder "
             "(the one containing your .md notes and its own .obsidian/ subfolder).", "implemented": True},
    {"id": "slack", "name": "Slack", "category": "Communication", "auth_type": "credentials",
     "fields": [{"key": "bot_token", "label": "Bot User OAuth Token", "secret": True}],
     "help": "api.slack.com/apps — create an app in your workspace, install it, copy the xoxb- token.", "implemented": True},
    {"id": "trello", "name": "Trello", "category": "Project Management", "auth_type": "credentials",
     "fields": [{"key": "api_key", "label": "API Key", "secret": False}, {"key": "token", "label": "Token", "secret": True}],
     "help": "trello.com/app-key — grab the key, then generate a token from the link on that page.", "implemented": True},
    {"id": "discord", "name": "Discord", "category": "Communication", "auth_type": "credentials",
     "fields": [{"key": "bot_token", "label": "Bot Token", "secret": True}],
     "help": "discord.com/developers/applications — create an app, add a bot, copy its token, invite it to your server.", "implemented": True},
    {"id": "elevenlabs", "name": "ElevenLabs", "category": "AI Models", "auth_type": "credentials",
     "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
     "help": "elevenlabs.io — Profile → API Keys.", "implemented": True},
    {"id": "openai", "name": "OpenAI", "category": "AI Models", "auth_type": "credentials",
     "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
     "help": "platform.openai.com/api-keys", "implemented": True},
    {"id": "deepseek", "name": "DeepSeek", "category": "AI Models", "auth_type": "credentials",
     "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
     "help": "platform.deepseek.com/api_keys", "implemented": True},
    {"id": "huggingface", "name": "Hugging Face", "category": "AI Models", "auth_type": "credentials",
     "fields": [{"key": "api_key", "label": "Access Token", "secret": True}],
     "help": "huggingface.co/settings/tokens", "implemented": True},
    {"id": "ollama", "name": "Ollama", "category": "AI Models", "auth_type": "local",
     "fields": [{"key": "base_url", "label": "Base URL", "secret": False, "default": "http://localhost:11434"}],
     "help": "Runs on your machine — no account needed. Leave default unless you changed the port.", "implemented": True},
    {"id": "cloudflare", "name": "Cloudflare", "category": "Developer Tools", "auth_type": "credentials",
     "fields": [{"key": "api_token", "label": "API Token", "secret": True}],
     "help": "dash.cloudflare.com/profile/api-tokens", "implemented": True},
    {"id": "digitalocean", "name": "DigitalOcean", "category": "Developer Tools", "auth_type": "credentials",
     "fields": [{"key": "api_token", "label": "Personal Access Token", "secret": True}],
     "help": "cloud.digitalocean.com/account/api/tokens", "implemented": True},

    # --- Batch 2: shared-login families (Google / Microsoft / Atlassian) ---
    # The "google"/"microsoft" entries below are the actual connect points —
    # one OAuth "installed app" consent flow, credentials stored once under
    # that family id. The individual service entries (family: "google"/
    # "microsoft") just reuse it; auth_type "oauth" there means "handled by
    # the family entry", not a separate login. Real per-service coverage
    # varies — see `implemented` on each; the family flow being connected
    # doesn't by itself mean every service under it has a working wrapper.
    {"id": "google", "name": "Google Account", "category": "Account", "auth_type": "credentials",
     "fields": [{"key": "client_id", "label": "OAuth Client ID", "secret": False},
                {"key": "client_secret", "label": "OAuth Client Secret", "secret": True}],
     "help": ("console.cloud.google.com → new project → APIs & Services → Credentials → "
              "Create Credentials → OAuth client ID → Desktop app. Enable the Gmail/Calendar/"
              "Drive/Sheets/Docs/Tasks APIs you want under 'Enabled APIs'."),
     "connect_action": "google_connect", "implemented": True},
    {"id": "gmail", "name": "Gmail", "category": "Email", "auth_type": "oauth", "family": "google", "implemented": True},
    {"id": "google_calendar", "name": "Google Calendar", "category": "Scheduling", "auth_type": "oauth", "family": "google", "implemented": True},
    {"id": "google_drive", "name": "Google Drive", "category": "File Storage", "auth_type": "oauth", "family": "google", "implemented": True},
    {"id": "google_sheets", "name": "Google Sheets", "category": "Productivity", "auth_type": "oauth", "family": "google", "implemented": True},
    {"id": "google_docs", "name": "Google Docs", "category": "Productivity", "auth_type": "oauth", "family": "google", "implemented": True},
    {"id": "google_tasks", "name": "Google Tasks", "category": "Productivity", "auth_type": "oauth", "family": "google", "implemented": True},
    {"id": "google_photos", "name": "Google Photos", "category": "Media", "auth_type": "oauth", "family": "google", "implemented": True},
    {"id": "google_maps", "name": "Google Maps", "category": "Maps & Location", "auth_type": "credentials",
     "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
     "help": "console.cloud.google.com/google/maps-apis — Maps uses a plain API key, not OAuth.", "implemented": True},
    {"id": "youtube", "name": "YouTube", "category": "Media", "auth_type": "oauth", "family": "google", "implemented": True},
    {"id": "microsoft", "name": "Microsoft Account", "category": "Account", "auth_type": "credentials",
     "fields": [{"key": "client_id", "label": "Azure App (Client) ID", "secret": False},
                {"key": "tenant_id", "label": "Tenant ID (use 'common' for personal accounts)", "secret": False, "default": "common"}],
     "help": ("portal.azure.com → App registrations → New registration → Authentication → "
              "add a platform → 'Mobile and desktop applications' → check http://localhost as "
              "the redirect URI → API permissions → add Mail.ReadWrite, Mail.Send, "
              "Files.ReadWrite, Calendars.ReadWrite (no client secret needed for this app type)."),
     "connect_action": "microsoft_connect", "implemented": True},
    {"id": "outlook", "name": "Outlook", "category": "Email", "auth_type": "oauth", "family": "microsoft", "implemented": True},
    {"id": "onedrive", "name": "OneDrive", "category": "File Storage", "auth_type": "oauth", "family": "microsoft", "implemented": True},
    {"id": "jira", "name": "Jira", "category": "Project Management", "auth_type": "credentials",
     "fields": [{"key": "site_url", "label": "Site URL (yoursite.atlassian.net)", "secret": False},
                {"key": "email", "label": "Account Email", "secret": False},
                {"key": "api_token", "label": "API Token", "secret": True}],
     "help": "id.atlassian.com/manage-profile/security/api-tokens", "implemented": True},
    {"id": "confluence", "name": "Confluence", "category": "Docs & Wiki", "auth_type": "credentials",
     "fields": [{"key": "site_url", "label": "Site URL (yoursite.atlassian.net)", "secret": False},
                {"key": "email", "label": "Account Email", "secret": False},
                {"key": "api_token", "label": "API Token", "secret": True}],
     "help": "Same API token as Jira — Atlassian accounts share one.", "implemented": True},

    # --- Batch 3: remaining standalone services, cataloged, not yet built ---
    {"id": "zoom", "name": "Zoom", "category": "Video Meetings", "auth_type": "credentials",
     "fields": [{"key": "account_id", "label": "Account ID", "secret": False},
                {"key": "client_id", "label": "Client ID", "secret": False},
                {"key": "client_secret", "label": "Client Secret", "secret": True}],
     "help": "marketplace.zoom.us — create a Server-to-Server OAuth app.", "implemented": True},
    {"id": "calendly", "name": "Calendly", "category": "Scheduling", "auth_type": "credentials",
     "fields": [{"key": "token", "label": "Personal Access Token", "secret": True}],
     "help": "calendly.com/integrations/api_webhooks", "implemented": True},
    {"id": "dropbox", "name": "Dropbox", "category": "File Storage", "auth_type": "credentials",
     "fields": [{"key": "access_token", "label": "Access Token", "secret": True}],
     "help": "dropbox.com/developers/apps — generate an access token for your own account.", "implemented": True},
]


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except Exception:
        # GEMZ4US 2026-09-10: reported GitHub's saved token (and possibly
        # other settings — not fully checked) missing after updating to
        # v1.11.1, with no explicit disconnect action taken. Nothing found
        # in the installer or this file's own merge logic that would explain
        # that (config\* is excluded from the installer's [Files] on
        # purpose, specifically to survive updates — see installer.iss).
        # But this except clause WAS a real, separate risk regardless of
        # whether it explains that report: a settings.json that fails to
        # parse for any reason (an interrupted write, a disk hiccup, two
        # processes writing at once) silently fell back to `data = {}` —
        # every companion, skill, integration, and key gone with no warning
        # and no way back, only discovered whenever the user next noticed
        # something missing. Back the unreadable file up before falling back
        # to defaults, so a bad settings.json is a recoverable inconvenience,
        # not a silent, permanent data loss.
        try:
            import shutil
            from datetime import datetime
            backup = SETTINGS_PATH.with_suffix(f".corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
            shutil.copy2(SETTINGS_PATH, backup)
        except Exception:
            pass
        data = {}
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    for key in DEFAULT_SETTINGS:
        if key in data:
            if isinstance(DEFAULT_SETTINGS[key], dict) and isinstance(data[key], dict):
                merged[key].update(data[key])
            else:
                merged[key] = data[key]
    if "companions" not in data:
        # Fresh install (or a pre-companion-registry settings.json) — seed the
        # built-in personas so companion switching has something to show on
        # first launch. Once anything is saved, this — including these
        # entries — becomes the on-disk source of truth; a user who deletes
        # a built-in companion via Settings won't see it come back.
        merged["companions"] = json.loads(json.dumps(BUILTIN_COMPANIONS))
    return merged


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def resolve_key_placeholders(text: str, settings: dict | None = None) -> str:
    """Substitutes {{KEY_NAME}} in a skill's content or an MCP server's
    apiKey field with the matching entry from custom_api_keys, so those
    keys are actually usable somewhere instead of just sitting in storage.
    Unmatched placeholders are left as-is (visible/debuggable rather than
    silently vanishing into an empty string)."""
    if not text or "{{" not in text:
        return text
    settings = settings or load_settings()
    by_name = {k["name"]: k["value"] for k in settings.get("custom_api_keys", [])}

    def _sub(m: re.Match) -> str:
        return by_name.get(m.group(1), m.group(0))

    return _PLACEHOLDER_RE.sub(_sub, text)
