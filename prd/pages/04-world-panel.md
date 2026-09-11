# World Panel

> **Source:** `world_panel.py` (`WorldPanel`, `_GraphCanvas`, `_NodeCard`, `_AddSubAgentDialog`)
> **Module:** Sub-Agents
> **Generated:** 2026-09-11

## Overview

A visual, node-graph view of the active companion and the sub-agents it can delegate to — the "org chart" for the `delegate_to_agent` tool. Lets the user add, edit, or remove sub-agent companions without leaving a spatial view of who reports to whom.

## Layout

- `_GraphCanvas`: a custom-painted canvas drawing the lead companion and each sub-agent as a draggable `_NodeCard`, connected by lines, on a subtle grid background. Node positions persist per layout (`_layout_nodes`) and can be dragged (`_on_card_dragged`).
- Each `_NodeCard` shows the companion's name, subtitle/specialty, a status indicator (`set_status`, animated via `pulse`), and is click/drag interactive.
- Toolbar/header: Add sub-agent button.

## Fields

### Add/Edit Sub-Agent Dialog (`_AddSubAgentDialog`)
| Field | Type | Required | Notes |
|---|---|---|---|
| Name | Text input | Yes | |
| Backend | Dropdown | Yes | One of the CLI agent backends (`claude_agent`, `codex_agent`, `opencode_agent`, `openhands_agent`, `grok_agent`, `blackbox_agent`) — `gemini_live` companions don't participate as delegation targets |
| Specialty | Text input | No | One-line description shown on the node card and in the lead companion's delegation directory |
| CLI-specific fields | Varies | Depends on backend | Mirrors the corresponding Settings → Agent CLI section for that backend |

## Interactions

### View / refresh
- **Trigger:** panel opened or companions change elsewhere (`refresh`, `refresh_statuses`).
- **Behavior:** rebuilds the graph from `settings["companions"]`, showing the current active companion as the lead node and every other `enabled` agent-backend companion as a sub-agent node (`_agent_backend_fns`).

### Add sub-agent
- **Trigger:** Add button → `_open_add_dialog` → fill form → submit.
- **Behavior:** appends a new companion entry with an agent backend to `settings["companions"]`; graph re-renders with the new node.

### Edit sub-agent
- **Trigger:** click a node → `_open_edit_dialog`.
- **Behavior:** same dialog pre-filled; saving updates the existing companion entry in place.

### Delete sub-agent
- **Trigger:** remove action on a node → `_delete_companion`.
- **Behavior:** confirms, then removes the companion from `settings["companions"]` and calls `_forget_session_everywhere(companion_id)` to clear any in-memory session state tied to that agent elsewhere in the app.

## API Dependencies

This panel is UI-only over local settings state; it does not call an external API directly. The sub-agent nodes it manages are consumed by:

| Consumer | Trigger | Notes |
|---|---|---|
| `main.py::_build_config()`'s sub-agent directory | Every reconnect | Builds the `[SUB-AGENTS AVAILABLE]` prompt block from `settings["companions"]` filtered to agent backends, excluding the currently-active companion |
| `JarvisLive._delegate_to_agent` / `_run_delegation` | A `delegate_to_agent` Gemini tool call | Dispatches to the named sub-agent's backend CLI, asynchronously |

## Page Relationships
- **From:** Main Window sidebar icon.
- **To:** none (terminal panel).
- **Data coupling:** reads/writes the same `settings["companions"]` list as Settings → Companions section; a companion added in either place appears in both.
