# API Inventory

## 1. Gemini Live connection (the core API)

**Client:** `google-genai` SDK, `client.aio.live.connect(model=..., config=types.LiveConnectConfig(...))` (`voice/session.py::Assistant`).

**Model:** `models/gemini-2.5-flash-native-audio-preview-12-2025` (`core/settings_store.DEFAULT_LIVE_MODEL`), overridable per-companion or globally via Settings.

**Config assembled fresh on every (re)connect**, `voice/prompt.py::build()` plus `voice/dispatch.py::ToolRouter.declarations()`, in this order:
1. Load `settings.json`; resolve the active companion (or fall back to the unnamespaced default "Omni" identity).
2. Resolve the effective model (companion override → global override → hardcoded default).
3. Load that companion's memory namespace and format it into a `[WHAT YOU KNOW ABOUT THIS PERSON]`-style block.
4. Load an Obsidian-vault memory index, if configured.
5. Compose the system prompt: companion's `system_prompt` (or the default identity file) + always-appended shared tool-routing/safety rules + a trader-availability note + (if any agent-backend companions exist) a `[SUB-AGENTS AVAILABLE]` directory.
6. Gather custom MCP-server tool declarations (off the event loop, so a slow/offline server can't stall the app) and active-integration tool declarations.
7. Prepend current date/time and an explicit "this is exactly which model you are" fact block (added after the assistant once confidently hallucinated its own version).
8. Prepend a one-line "last session" recap, consumed once so it's never repeated.
9. Append enabled Skills as `### {name}` blocks, with key-placeholder substitution.
10. Return a `LiveConnectConfig` with `response_modalities=["AUDIO"]`, both input/output audio transcription enabled, the combined tool list (built-in + custom MCP + integrations), session-resumption (so a reconnect can continue the prior conversation), and the resolved voice.

**This entire assembly only happens once per connection** — see the README's "Reconnect-to-apply" note for the product implication.

## 2. Built-in Tool Catalog (`toolkit/*.py` via `toolkit.schemas()`, plus the session tools in `voice/dispatch.py`)

All 23 tools Gemini can call regardless of Integrations/MCP/Skills configuration. Generated from the live schemas; the full descriptions are in the source and double as the model-facing prompt text.

| Tool | Description (first sentence of what Gemini is given) | Parameters |
|---|---|---|
| `save_memory` | Quietly remembers a lasting personal fact the user mentioned — name, city, job, likes, people in their life, projects, plans, habits. | `category` (required), `key` (required), `value` (required) |
| `get_current_time` | The actual current date and time. | none |
| `agent_task` | Hands a genuinely multi-step goal (several different tools in sequence, e.g. research something and save a report) to a background worker and returns straight away; yo… | `goal` (required), `priority` |
| `claude_agent` | Asks Claude (Anthropic), running as a coding agent with access to the user's Obsidian vault and projects. | `request` (required), `timeout` |
| `generate_image` | Creates an image from a description and shows it to the user. | `prompt` (required) |
| `launch_trader` | Opens the built-in crypto trader panel. | none |
| `delegate_to_agent` | Gives a task to one of the listed sub-agents to work on in the background while you keep talking. | `agent_name` (required), `task` (required) |
| `close_assistant` | Closes Omni-OS. | none |
| `open_app` | Launches a program installed on this PC by its name (found through the Start menu and known install folders), or opens a web address in the default browser. | `app_name` (required) |
| `weather_report` | Gets the current weather and today's forecast for a city. | `city` (required) |
| `web_search` | Look something up on the web. | `query` (required), `mode`, `items`, `aspect` |
| `send_message` | Sends a text message to a contact through a desktop messaging app (WhatsApp, Telegram, Signal, Discord, Slack or Teams). | `receiver` (required), `message_text` (required), `platform` (required) |
| `reminder` | Sets a one-off reminder that pops up as a desktop notification at the given date and time, even if Omni-OS is closed by then. | `date` (required), `time` (required), `message` (required) |
| `file_controller` | Manages files and folders in the user's home: list, create, read, write, delete (to the Recycle Bin), move, copy, rename, find, largest files, disk usage, file info, a… | `action` (required), `path`, `name`, `destination`, `new_name`, `content`, `extension`, `count` |
| `desktop_control` | Desktop wallpaper and housekeeping: set the wallpaper from a file or image URL, say what the current wallpaper is, sort the desktop into folders, archive loose files,… | `action` (required), `path`, `url`, `mode` |
| `screen_process` | Looks at the user's screen (or webcam) and answers a question about what's visible. | `angle`, `text` (required) |
| `computer_control` | Direct mouse and keyboard control: type, click at coordinates, hotkeys, key presses, scrolling, pointer moves, clipboard, screenshots, focusing a window, and finding o… | `action` (required), `text`, `x`, `y`, `keys`, `key`, `direction`, `amount`, `seconds`, `title`, `description`, `field`, `clear_first`, `path` |
| `computer_settings` | One-shot computer commands: volume (up/down/set/mute), brightness, window management, keyboard shortcuts, typing text, media keys, tabs, zoom, page navigation, screens… | `action`, `value`, `description` |
| `code_helper` | Works on a single PROGRAMMING source file (Python, JavaScript, HTML, etc.): write new code, edit or optimize an existing file, explain code, run it, build-and-fix unti… | `action` (required), `description`, `language`, `output_path`, `file_path`, `code`, `args`, `timeout` |
| `dev_agent` | Builds a BRAND NEW small project from scratch: writes the files, installs its packages into the project's own environment, opens it in the editor, and runs and repairs… | `description` (required), `language`, `project_name`, `timeout` |
| `file_processor` | Does something with a file the user uploaded or dropped on the window (or a named file): summarize/describe/analyze/transcribe/OCR/explain/review it, or transform it —… | `file_path`, `action`, `instruction`, `format`, `width`, `height`, `scale`, `quality`, `start`, `end`, `timestamp`, `column`, `value`, `condition`, `ascending`, `save`, `destination` |
| `browser_control` | Controls a real browser window: open sites, search, click things by their visible text or label, type into fields, fill forms, scroll, press keys, read the page, manag… | `action` (required), `browser`, `url`, `query`, `engine`, `selector`, `text`, `description`, `fields`, `direction`, `amount`, `key`, `path`, `clear_first` |
| `share_file` | Gives the user a clickable link to a local file Omni just made (a page, document, image…), so it can be opened from the phone dashboard. | `path` (required) |

Plus three modules appended separately (own `TOOL_DECLARATIONS`, concatenated in `voice/dispatch.py::ToolRouter.declarations()`): **blockchain read-only tools** (`actions/blockchain_readonly.py`), **action-item extraction** (`actions/action_items.py`), and **weekly review** (`actions/weekly_review.py`) — not individually re-verified for this PRD; `[TBC]` for their exact parameter shapes.

Two tools are conditionally excluded from the declared list before it's sent to Gemini: `launch_trader` when the Trader feature is disabled in Settings, and `delegate_to_agent` when there are no eligible sub-agent companions.

## 3. Custom MCP Servers

`core/mcp_registry.gather_custom_tool_declarations(servers)` — queries each configured MCP server's `tools/list`, scoped per-companion via `mcp_server_ids`, run off the main event loop. Failures/timeouts on one server should not block the others or the connection (exact isolation behavior not re-verified for this PRD — `[TBC]`).

## 4. Integrations

`actions/integrations/registry.py::get_active_tool_declarations(settings)` filters the full 30-entry catalog (see [enum-dictionary.md](./enum-dictionary.md)) down to only those the user has connected, pulling each one's own `TOOL_DECLARATIONS` from its dedicated module (`actions/integrations/<id>.py`).

## 5. Remote Dashboard (phone-facing HTTP/WS API)

Full route table in [06-remote-dashboard.md](../pages/06-remote-dashboard.md#api-dependencies-this-servers-own-routes). Summary:
- `GET /`, `GET /login`, `POST /login`, `GET /auto-login` — page shell + auth.
- `GET /f/{file_id}` — shared-file serving.
- `POST /api/command` — text/command input.
- `GET /api/trader/state`, `POST /api/trader/action`, `GET /api/trader/action/result` — Trader mirroring.
- `WS /ws` — main bidirectional channel (JSON).
- `WS /ws/audio` — raw PCM mic-audio relay, deliberately separate from `/ws`.

This is a private protocol the desktop itself defines — not Gemini's wire format. Self-signed HTTPS, LAN-only, key-based pairing with a default 600s expiry.

## 6. Agent-CLI Delegation (sub-agents)

`voice/session.py::_agent_sender(backend)` / `Assistant.delegate` / `Assistant._delegated` dispatch a `delegate_to_agent` call to one of: `claude_agent`, `codex_agent`, `opencode_agent`, `openhands_agent`, `grok_agent`, `blackbox_agent` — each backed by its own module under `actions/` (`claude_companion.py`, `codex_companion.py`, `opencode_companion.py`, `openhands_companion.py`, `grok_companion.py`, `blackbox_companion.py`) that shells out to that product's local CLI using the path/vault-dir configured in Settings. The call returns immediately with an acknowledgement; the sub-agent's actual result surfaces asynchronously back into the conversation.

## 7. Third-Party Trading Risk Gate (Seraph)

`trader/engine.py::_risk_check`/`_poll_gate_result` — an MCP-based pre-trade check (`_build_gate_args`, `_parse_verdict`) that must return an allow verdict before any buy/sell proceeds, polled with a ~40s budget. This is the same Seraph guardrail product used elsewhere in this user's project portfolio (Guardian/SeraphTrader) — not re-derived here, treated as an external dependency.
