# Main Window — HUD & Chat

> **Source:** `ui.py` (`MainWindow`, `JarvisUI`, `HudCanvas`, `LogWidget`, `_SysMetrics`) · `main.py` (`JarvisLive`)
> **Module:** Core Assistant
> **Generated:** 2026-09-11

## Overview

The always-open home screen of Omni-OS: a fullscreen, near-black HUD showing an animated face/orb, a live system-status readout, a scrolling chat/log feed, and a text input — the surface for every voice or typed interaction with the active companion.

## Layout

- **Center:** `HudCanvas` — an animated particle-sphere / video face that reacts to app state (idle, listening, speaking) and can show a theme-specific companion video.
- **Top bar:** live clock, connection/state pill (`_refresh_state_pill`), companion switcher (name + left/right chevrons to cycle companions).
- **Left sidebar:** icon buttons — mute mic, mute speech output, always-listening toggle, open Trader, open Settings, open Integrations, open World, open Remote Dashboard pairing.
- **Status card:** a togglable panel under the top bar that can switch into "trader config" mode (`_set_status_card_mode`) showing live system metrics (CPU/GPU/RAM/temperature via `_SysMetrics`) otherwise.
- **Bottom:** chat input row with a text field, send button, and a file-attach button opening a `FileDropZone` (drag-and-drop or browse) for uploads the `file_processor` tool can act on.
- **Chat/log panel:** `LogWidget`, a `QTextBrowser`-based scrolling feed rendering `SYS:`, `you:`, `omni:` (and legacy `jarvis:`) tagged lines in distinct colors, one line queued and animated in at a time.

## Fields

### Chat Input Row
| Field | Type | Required | Notes |
|---|---|---|---|
| Message text | Text input | No | Free text sent as a turn to the active companion's live session |
| Attach file | File picker / drag-drop | No | Populates `FileDropZone`; cleared after send or explicit clear |

### Top Bar
| Element | Notes |
|---|---|
| Companion switcher | Shows active companion's name; ‹ › cycle through `settings["companions"]` in id order |
| State pill | Reflects connection lifecycle: connecting / online / listening / speaking / reconnecting / error |

## Interactions

### App launch
- If no Gemini API key is configured, a modal `SetupOverlay` blocks the HUD until one is entered (see [07-onboarding-setup.md](./07-onboarding-setup.md)).
- Otherwise `JarvisLive.run()` connects to Gemini Live immediately; the very first successful connection logs `"SYS: OMNI-OS online."`; any later reconnect in the same process instead logs `"SYS: Reconnected (session #N)."` — a counter (`_connection_count`) was added specifically so the log doesn't misleadingly repeat "online" on every automatic reconnect.

### Voice conversation
- **Trigger:** user speaks (mic always streaming unless muted) or types and sends.
- **Behavior:** audio/text is forwarded to the current Gemini Live session; assistant audio plays back through `_play_audio`, transcripts append to the log, and any tool call Gemini requests is dispatched via `JarvisLive._execute_tool` (see [api-inventory.md](../appendix/api-inventory.md)).
- **Barge-in:** if Gemini reports `interrupted`, playback stops immediately.

### Companion switching
- **Trigger:** clicking a chevron in the switcher (`_cycle_companion`).
- **Behavior:** advances to the next/previous id in `settings["companions"]`, wrapping around; on an actual change, persists `active_companion_id` and triggers a reconnect so the new companion's identity/voice/memory namespace takes effect.
- **No-op guard:** if only one companion exists (or cycling lands back on the currently-active id), the click is a no-op and **does not** log or reconnect — added after a beta report that repeated clicks produced stacked, identical `SYS:` log lines.

### Mute / speech-mute / always-listening toggles
- Independent boolean states, each with its own styled button (`_style_mute_btn`, `_style_speech_mute_btn`, `_style_always_listening_btn`) reflecting on/off visually.
- Always-listening off means the mic only streams while a manual "hold to talk"-style gesture is active; on means continuous streaming (exact push-to-talk vs. continuous gating lives in `_listen_audio`).

### Opening other panels
- Trader, Settings, Integrations, and World are each lazily constructed on first open (`_ensure_trader_panel`, `_ensure_settings_panel`, `_ensure_integrations_panel`, `_ensure_world_panel`) and toggle-shown/hidden thereafter, replacing the HUD's left-panel area (`set_left_panel_extra`) rather than opening a separate window.
- Opening Trader from a voice command (`launch_trader` tool) is routed through a Qt signal (`open_trader_panel`) rather than called directly from the background asyncio thread, specifically to avoid constructing/reparenting widgets off the Qt GUI thread.

### File attach → file_processor
- **Trigger:** drop or browse a file into `FileDropZone`.
- **Behavior:** the path becomes "the currently uploaded file"; a follow-up voice/text command referencing "this file" routes to the `file_processor` tool, which branches on file type (image/PDF/docx/csv/json/code/audio/video/archive/pptx).

## API Dependencies

| Dependency | Trigger | Notes |
|---|---|---|
| Gemini Live `BidiGenerateContent` WS | App launch, companion switch, settings change requiring reconnect | See [api-inventory.md](../appendix/api-inventory.md) |
| `_execute_tool` → one of ~23 built-in tools, active MCP servers, or active Integrations | Any Gemini tool call | Dispatch table lives in `main.py`; each tool's own module documents its side effects |
| `save_memory` (Gemini-initiated tool call) | Gemini decides a fact is worth remembering | Writes to the active companion's memory namespace — see `memory/memory_manager.py` |

## Page Relationships
- **To:** Settings, Integrations, World, Trader, Remote Dashboard pairing overlay (all opened from the sidebar).
- **From:** Setup overlay (blocks this page until API key entry is complete).
- **Data coupling:** switching the active companion here changes which memory namespace, voice, and system prompt every other panel implicitly operates against.
