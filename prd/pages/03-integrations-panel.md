# Integrations Panel

> **Source:** `integrations_panel.py` (`IntegrationsPanel`, `_ConnectDialog`), catalog in `core/settings_store.py::INTEGRATION_CATALOG`, tool wiring in `actions/integrations/registry.py`
> **Module:** Integrations
> **Generated:** 2026-09-11

## Overview

A visual catalog/grid of 30 third-party services the assistant can be connected to (Gmail, Slack, GitHub, Notion, Discord, Google Drive/Sheets/Docs/Tasks/Photos/Calendar/Maps, Microsoft/Outlook/OneDrive, Jira, Confluence, Trello, Zoom, Calendly, Dropbox, ElevenLabs, OpenAI, DeepSeek, Hugging Face, Ollama, Cloudflare, DigitalOcean, YouTube, Obsidian). Connecting a service adds its tool declarations to the live Gemini session (next reconnect); the assistant can then act on that service when the user asks.

## Layout

- A filterable card grid (`_populate_grid`), one card per catalog entry, each showing an icon badge, name, category, and connect/connected state (`_is_connected`).
- Filter control narrows the grid by category or connection state (`_on_filter_changed`).
- Clicking a card's Connect opens `_ConnectDialog`.

## Fields

### Card
| Field | Notes |
|---|---|
| Icon badge | Rendered per catalog id (`_icon_badge_pixmap`) |
| Name / category | From `INTEGRATION_CATALOG` |
| Status | Connected (green) vs. not connected, derived from `settings["integrations"][id]` presence |

### Connect Dialog
Fields are generated dynamically from the catalog entry's `fields` list — each is either a plain text input or a masked (secret) input, per `auth_type`:
- **`credentials`** — one or more pasted values (API key, token, key+secret pair, etc.), saved immediately, no network round-trip required.
- **`local`** — a reachable base URL only (Ollama); no account.
- **`oauth`** — a real browser consent flow for a "family" (Google or Microsoft); individual services under that family (e.g. Gmail, Google Calendar) piggyback on the family's stored credentials rather than asking again.

Each dialog also renders the catalog entry's `help` text (linkified via `_linkify_help`) pointing to exactly where to obtain the credential.

## Interactions

### Connect a credentials-based service
- **Trigger:** click Connect on a card → fill fields → Save.
- **Behavior:** `_collect_values` reads the form, `_on_save` writes `settings["integrations"][id]`, marking it enabled; dialog closes; grid re-renders the card as connected.

### Connect an OAuth-family service (Google / Microsoft)
- **Trigger:** click Connect on `google`/`microsoft`, or on a family member service, which opens the family's dialog instead (`_open_family_dialog`).
- **Behavior:** `_on_connect` runs the actual OAuth flow in the background (`_run`), storing the resulting token(s) once complete.

### Disconnect
- **Trigger:** `_on_disconnect` on an already-connected card.
- **Behavior:** removes/disables the `settings["integrations"][id]` entry.

## API Dependencies

| API | Trigger | Notes |
|---|---|---|
| Each connected service's own tool module (`actions/integrations/<id>.py`) | A Gemini tool call once connected | Each module defines its own `TOOL_DECLARATIONS`; `get_active_tool_declarations(settings)` (`actions/integrations/registry.py`) filters to only the ones the user has actually connected |
| Google / Microsoft OAuth consent | Connect click on either family | Browser-based; token(s) persisted to `settings.json` on success |

## Business Rules
- The grid only ever offers **Connect** for entries where `implemented: True` in the catalog; anything else would show as "Coming soon" rather than accepting credentials nothing will use — as read, every current catalog entry is marked implemented (see [README.md](../README.md#uncertainty-flagged)).
- A family entry (`google`, `microsoft`) is the actual credential-holder; its member services (`gmail`, `outlook`, etc.) are catalog rows for grid completeness, not separate logins.

## Page Relationships
- **From:** Main Window sidebar icon.
- **To:** none (terminal panel, aside from external OAuth browser windows).
- **Data coupling:** every connection here changes the tool list Gemini receives on the next reconnect — same mechanism as Skills/MCP servers.
