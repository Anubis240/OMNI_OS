# API Inventory

## 1. Gemini Live connection (the core API)

**Client:** `google-genai` SDK, `client.aio.live.connect(model=..., config=types.LiveConnectConfig(...))` (`main.py::JarvisLive`).

**Model:** `models/gemini-2.5-flash-native-audio-preview-12-2025` (`core/settings_store.DEFAULT_LIVE_MODEL`), overridable per-companion or globally via Settings.

**Config assembled fresh on every (re)connect**, `main.py::_build_config()`, in this order:
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

## 2. Built-in Tool Catalog (`TOOL_DECLARATIONS`, `main.py:264-768`)

All 23 tools Gemini can call regardless of Integrations/MCP/Skills configuration. Names and descriptions are copied verbatim from source (they double as the actual model-facing prompt text).

| Tool | Description (as given to Gemini) | Key parameters |
|---|---|---|
| `open_app` | Opens any application on the computer | `app_name` (required) |
| `web_search` | Searches the web for any information | `query` (required), `mode`, `items`, `aspect` |
| `weather_report` | Gives the weather report to user | `city` (required) |
| `send_message` | Sends a text message via WhatsApp, Telegram, or other messaging platform | `receiver`, `message_text`, `platform` (all required) |
| `reminder` | Sets a timed reminder using Task Scheduler | `date`, `time`, `message` (all required) |
| `youtube_video` | Play / summarize / get info / show trending YouTube videos | `action`, `query`, `save`, `region`, `url` |
| `screen_process` | Captures and analyzes the screen or webcam image | `angle` (screen/camera), `text` (required) |
| `computer_settings` | Volume, brightness, window mgmt, shortcuts, typing, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, scrolling, tabs, zoom, screenshots, lock, refresh | `action`, `description`, `value` |
| `browser_control` | Controls any web browser: navigate, search, click, fill forms, scroll, screenshot, tabs, multi-browser | `action` (required, 20 sub-actions), `browser`, `url`, `query`, `selector`, `text`, etc. |
| `file_controller` | Manages files/folders: list, create, delete, move, copy, rename, read, write, find, disk usage | `action` (required), `path`, `destination`, `new_name`, `content`, `name`, `extension`, `count` |
| `desktop_control` | Wallpaper, organize, clean, list, stats | `action` (required), `path`, `url`, `mode`, `task` |
| `code_helper` | Writes/edits/explains/runs/builds source code files — never prose | `action` (required), `description`, `language`, `output_path`, `file_path`, `code`, `args`, `timeout` |
| `dev_agent` | Builds a brand-new project from scratch (plans, writes files, installs deps, opens VS Code, runs/fixes errors) | `description` (required), `language`, `project_name`, `timeout` |
| `claude_agent` | Delegates to Claude running as a real coding agent with Obsidian-vault + file/tool access, for existing-project work | `request` (required), `timeout` |
| `agent_task` | Executes complex multi-step tasks needing multiple different tools | `goal` (required), `priority` |
| `computer_control` | Direct input control: type, click, hotkeys, scroll, move, screenshot, on-screen element finding | `action` (required, 17 sub-actions), `text`, `x`, `y`, `keys`, `key`, etc. |
| `game_updater` | The only tool for any Steam/Epic Games request: install/update/list/schedule | `action`, `platform`, `game_name`, `app_id`, `hour`, `minute`, `shutdown_when_done` |
| `flight_finder` | Searches Google Flights and speaks the best options | `origin`, `destination`, `date` (required), `return_date`, `passengers`, `cabin`, `save` |
| `generate_image` | Generates an image from a text description via AI image generation | `prompt` (required) |
| `launch_trader` | Opens the Trader panel (does not itself trade) | none |
| `delegate_to_agent` | Hands a task to a named sub-agent to work on in the background | `agent_name`, `task` (both required) |
| `share_file` | Gives the user a clickable web link (via the Remote Dashboard) to a local file, instead of a `file://` path | `path` (required) |
| `shutdown_seraph` | Shuts down the assistant completely | none |
| `file_processor` | Acts on an uploaded/dropped file — images, PDFs, docx/txt, CSV/Excel, JSON/XML, code, audio, video, archives, presentations | `file_path`, `action`, `instruction`, `format`, plus type-specific params (width/height/scale/quality/start/end/timestamp/column/value/condition/ascending/save/destination) |
| `save_memory` | Silently saves an important personal fact to long-term memory | `category`, `key`, `value` (all required) |

Plus three modules appended separately (own `TOOL_DECLARATIONS`, concatenated at `main.py:766-768`): **blockchain read-only tools** (`actions/blockchain_readonly.py`), **action-item extraction** (`actions/action_items.py`), and **weekly review** (`actions/weekly_review.py`) — not individually re-verified for this PRD; `[TBC]` for their exact parameter shapes.

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

`main.py::_agent_send_fn(backend)` / `_delegate_to_agent` / `_run_delegation` dispatch a `delegate_to_agent` call to one of: `claude_agent`, `codex_agent`, `opencode_agent`, `openhands_agent`, `grok_agent`, `blackbox_agent` — each backed by its own module under `actions/` (`claude_companion.py`, `codex_companion.py`, `opencode_companion.py`, `openhands_companion.py`, `grok_companion.py`, `blackbox_companion.py`) that shells out to that product's local CLI using the path/vault-dir configured in Settings. The call returns immediately with an acknowledgement; the sub-agent's actual result surfaces asynchronously back into the conversation.

## 7. Third-Party Trading Risk Gate (Seraph)

`trader/engine.py::_risk_check`/`_poll_gate_result` — an MCP-based pre-trade check (`_build_gate_args`, `_parse_verdict`) that must return an allow verdict before any buy/sell proceeds, polled with a ~40s budget. This is the same Seraph guardrail product used elsewhere in this user's project portfolio (Guardian/SeraphTrader) — not re-derived here, treated as an external dependency.
