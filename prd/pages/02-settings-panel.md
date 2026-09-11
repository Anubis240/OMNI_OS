# Settings Panel

> **Source:** `settings_panel.py` (`SettingsPanel`), backed by `core/settings_store.py`
> **Module:** Settings / Companion Registry
> **Generated:** 2026-09-11

## Overview

A single scrolling panel, replacing the HUD's left area, holding every piece of user-configurable state that isn't a full panel of its own: the companion registry, agent-CLI paths, API keys, MCP servers, custom Skills, and Remote Dashboard config. Every section saves independently via its own `_persist()` call, most showing a "— takes effect on next reconnect" note where relevant (see [README.md](../README.md#reconnect-to-apply-recurring-product-behavior-not-a-bug)).

## Layout

Fourteen stacked sections, each built by its own `_build_*_section` method: Trader toggle, Companions, Claude Agent CLI, Codex Agent CLI, (generic) Agent-CLI builder used for OpenCode/OpenHands/Grok/Blackbox, Remote Dashboard, API Keys, Custom API Keys, MCP Servers, Skills.

## Fields

### Companions Section
| Field | Type | Required | Notes |
|---|---|---|---|
| Companion list | List with radio-style "active" selector | — | `_refresh_companions_list`; each row: name, backend badge, set-active / remove |
| Name | Text input | Yes | |
| Backend | Dropdown | Yes | `gemini_live` \| `claude_agent` \| `codex_agent` \| `opencode_agent` \| `openhands_agent` \| `grok_agent` \| `blackbox_agent` |
| Model | Text input | No | Overrides the Gemini Live model for this companion only (`gemini_live` backend) |
| Voice | Dropdown | No (Gemini Live only) | One of the 8 prebuilt voices — see [enum-dictionary.md](../appendix/enum-dictionary.md) |
| System prompt | Multiline text | No | Persona only — safety/tool-routing rules are always appended automatically |
| Specialty | Text input | No | One-line description shown in the World graph and to the lead companion's `delegate_to_agent` directory; meaningless for `gemini_live` companions |
| MCP servers | Multi-select | No | Scopes which custom MCP tools this companion sees |
| Live-model override (global) | Text input | No | Falls back to `DEFAULT_LIVE_MODEL` when a companion has none of its own |

**Actions:** Add companion, Remove companion (with confirmation), Set active, Use default companion (clears `active_companion_id`, reverting to the unnamespaced original "Omni" behavior).

### Claude Agent CLI Section
| Field | Type | Notes |
|---|---|---|
| Enabled | Checkbox | Gates whether `claude_agent`/`delegate_to_agent` can use this backend |
| CLI path | Text input + Browse | Path to the local Claude Code CLI executable |
| Vault dir | Text input + Browse | Obsidian vault root the agent gets file access to |
| Extra dir | Text input + Browse | An additional directory granted access, beyond the vault |

### Codex / OpenCode / OpenHands / Grok / Blackbox Agent CLI Sections
Same shape minus `Extra dir` (Codex has no confirmed CLI-level equivalent to `add_dirs`; the other four reuse a shared `_build_agent_cli_section` builder with per-product copy). OpenCode and OpenHands flag verification was checked against each project's own docs; Grok and Blackbox flag verification was only checked against community sources, noted inline in the code as lower-confidence.

### Remote Dashboard Section
| Field | Type | Notes |
|---|---|---|
| Port | Number input | Default 8000; changeable because the original fork this shipped from defaults to the same port |
| Regenerate certificate | Button | Re-issues the self-signed HTTPS cert used for the LAN dashboard |

### API Keys Section
| Field | Type | Notes |
|---|---|---|
| OpenAI key | Password input | Used by skills/MCP servers that need it — Omni's own core LLM stays Gemini regardless |
| Anthropic key | Password input | Same caveat |

### Custom API Keys Section
A free-form named-key vault (`custom_api_keys`: `[{id, name, value}]`) for anything a hand-written Skill or custom MCP server needs that isn't OpenAI/Anthropic. List with add/remove.

### MCP Servers Section
| Field | Type | Notes |
|---|---|---|
| Name | Text input | Display label |
| URL | Text input | MCP server endpoint |
| API key | Password input | Optional, per-server |

Add/remove; each save shows the reconnect-required note. Declarations are gathered live (`core/mcp_registry.gather_custom_tool_declarations`) on every `_build_config()` call via a background thread, so a slow/offline server can't freeze the app.

### Skills Section
| Field | Type | Notes |
|---|---|---|
| Name | Text input | |
| Content | Multiline text | Free-text instructions appended to the system prompt as a `### {name}` block; supports the same key-placeholder substitution as other prompt text (`resolve_key_placeholders`) |
| Enabled | Checkbox | Toggling shows the reconnect-required note; no longer silently does nothing (root cause of a beta report — see [README.md](../README.md)) |

## Interactions

### Editing a skill (in-place)
- **Trigger:** click EDIT on a skill row.
- **Behavior:** the skill's name/content load into the same add-form fields (`_editing_skill_id` tracks which); Save now updates in place instead of requiring remove-then-recreate; Cancel restores the form to its empty "add new" state.
- **Edge case:** removing a skill that is currently being edited clears the in-progress edit state to avoid saving over a deleted row.

### Toggling a skill / adding an MCP server / integration change
- **Trigger:** any checkbox/save affecting `skills`, `mcp_servers`, or done from the Integrations panel.
- **Behavior:** persisted immediately to `settings.json`, but has **no effect on the current Gemini Live session** until the next reconnect — the panel now says so explicitly rather than implying an instant change.

### Save Trader toggle
- **Trigger:** checkbox in the Trader section.
- **Behavior:** flips `settings["trader"]["enabled"]`; controls whether `launch_trader` is offered as a tool and whether the Trader sidebar icon is shown (`_refresh_trader_visibility` on the main window).

## API Dependencies

This page mostly writes to local JSON (`core/settings_store.save_settings`) rather than calling a remote API itself; the one live check is MCP tool discovery:

| Dependency | Trigger | Notes |
|---|---|---|
| Configured MCP server's `tools/list` | Every `_build_config()` call (not from this panel directly) | Runs off the main event loop via `asyncio.to_thread` |

## Page Relationships
- **From:** Main Window sidebar gear icon.
- **To:** none (terminal panel); Companions section changes are reflected live in the World panel's sub-agent graph and the Main Window's companion switcher.
- **Data coupling:** every section here writes to the same `settings.json` also read by `main.py::_build_config()`, `integrations_panel.py`, `world_panel.py`, and `trader_panel.py`.
