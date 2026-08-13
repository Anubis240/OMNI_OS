from core import settings_store

from . import (
    calendly,
    cloudflare,
    confluence,
    deepseek,
    digitalocean,
    discord_bot,
    dropbox_files,
    elevenlabs,
    github,
    gmail,
    google_calendar,
    google_docs,
    google_drive,
    google_maps,
    google_photos,
    google_sheets,
    google_tasks,
    huggingface,
    jira,
    notion,
    onedrive,
    ollama_local,
    openai_api,
    outlook,
    slack,
    trello,
    youtube,
    zoom,
)

# Standalone modules: connected via their own catalog id in
# settings["integrations"][id].
_STANDALONE_MODULES = {
    "github": github,
    "notion": notion,
    "slack": slack,
    "trello": trello,
    "discord": discord_bot,
    "elevenlabs": elevenlabs,
    "openai": openai_api,
    "deepseek": deepseek,
    "huggingface": huggingface,
    "ollama": ollama_local,
    "cloudflare": cloudflare,
    "digitalocean": digitalocean,
    "jira": jira,
    "confluence": confluence,
    "google_maps": google_maps,
    "zoom": zoom,
    "calendly": calendly,
    "dropbox": dropbox_files,
}

# Family modules: connected via the shared family entry
# (settings["integrations"]["google"|"microsoft"]), not their own id.
_FAMILY_MODULES = {
    "google": {
        "gmail": gmail, "google_calendar": google_calendar, "google_drive": google_drive,
        "google_sheets": google_sheets, "google_docs": google_docs, "google_tasks": google_tasks,
        "youtube": youtube, "google_photos": google_photos,
    },
    "microsoft": {
        "outlook": outlook, "onedrive": onedrive,
    },
}

_FAMILY_CONNECTED_KEY = {"google": "refresh_token", "microsoft": "token_cache"}


def _all_modules() -> dict:
    modules = dict(_STANDALONE_MODULES)
    for family_modules in _FAMILY_MODULES.values():
        modules.update(family_modules)
    return modules


def get_active_tool_declarations(settings: dict | None = None) -> list[dict]:
    """Tool schemas for every connected (enabled, credentials saved)
    integration — only these get offered to the model."""
    settings = settings or settings_store.load_settings()
    connected = settings.get("integrations", {})
    declarations = []

    for catalog_id, module in _STANDALONE_MODULES.items():
        entry = connected.get(catalog_id)
        if entry and entry.get("enabled"):
            declarations.extend(module.TOOL_DECLARATIONS)

    for family, family_modules in _FAMILY_MODULES.items():
        entry = connected.get(family)
        if entry and entry.get("enabled") and entry.get(_FAMILY_CONNECTED_KEY[family]):
            for module in family_modules.values():
                declarations.extend(module.TOOL_DECLARATIONS)

    return declarations


def _module_for_tool(name: str):
    for module in _all_modules().values():
        if any(d["name"] == name for d in module.TOOL_DECLARATIONS):
            return module
    return None


def is_integration_tool(name: str) -> bool:
    return _module_for_tool(name) is not None


def dispatch(name: str, args: dict, player=None) -> str:
    module = _module_for_tool(name)
    if module is None:
        return f"No integration handles '{name}'."
    return module.dispatch(name, args, player)
