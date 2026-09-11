# Omni-OS — Product Requirements Document

> **Generated:** 2026-09-11 · reverse-engineered from the codebase at `C:\Users\leion\Downloads\Omni-OS`
> **Snapshot basis:** v1.11.2 (latest tagged release; a few post-tag commits on top, not yet re-versioned)
> **Method:** Code → PRD skill, adapted for a PyQt6 desktop app — "pages" below are the app's major panels/overlays, and "APIs" are the app's real external integration points (Gemini's realtime API, the phone-facing LAN dashboard, MCP servers, and CLI sub-agent backends) rather than routes in a web framework.

## System Overview

Omni-OS (internally "Kondux") is a JARVIS-style desktop voice assistant for Windows, built on PyQt6 and Google's **Gemini Live** realtime API. A user talks (or types) to an animated on-screen HUD; Gemini streams back voice, and the assistant can act in the world through ~23 built-in tools (open apps, control the browser, manage files, control the OS, search the web, generate images, book flights, process uploaded files, etc.) plus an open-ended catalog of 30 third-party service integrations (Gmail, Slack, GitHub, Notion, Discord, Google Drive, Dropbox, Jira, Zoom, and more) that the user connects with their own credentials.

Beyond the core assistant, three larger subsystems are bolted onto the same shell:

1. **A multi-companion / sub-agent system.** The user can define named companions (different personas, voices, and system prompts) and — for companions backed by a real coding-agent CLI (Claude Code, Codex, OpenCode, OpenHands, Grok CLI, Blackbox) instead of Gemini Live — hand them background work via a `delegate_to_agent` tool call, visualized as a live graph in the **World** panel.
2. **An optional crypto trading panel** ("Trader") — a self-contained paper/live trading engine across 9 EVM chains, gated by a third-party risk-check service ("Seraph") before any real trade, with its own wallet management, watchlist, and command bar. Off by default; the voice assistant can only *open* the panel, never place trades itself.
3. **A LAN-bound Remote Dashboard** — a small embedded web server (FastAPI + a single-page HTML/JS client) that lets a phone on the same network pair via QR code and act as a second microphone/screen for the same running session, including a mirrored, read-only-by-default view of the Trader panel.

The product is currently in **closed beta** with a single external tester (GEMZ4US, communicating over Telegram); releases are shipped as a signed Windows installer (PyInstaller + Inno Setup) with an internal beta-bug-report/artifact-tracking workflow, not a public storefront yet.

**Primary user:** a single power user running Omni-OS on their own always-on Windows PC, who wants a hands-free assistant for everyday computer/OS tasks, an on-ramp into their own external tools and coding agents, and (optionally) a supervised way to try automated crypto trading with real guardrails.

## Module Overview

| Module | Panel(s) | Core Functionality |
|---|---|---|
| **Core Assistant** | Main Window (HUD + chat) | Realtime voice/text conversation over Gemini Live; tool-calling; per-companion long-term memory |
| **Companion Registry** | Settings → Companions, World | Create/edit/delete personas; pick voice, backend (Gemini Live vs. a CLI agent), model, MCP servers, specialty; switch the active companion live |
| **Settings** | Settings panel (14 sections) | API keys, agent-CLI paths, MCP servers, custom Skills (system-prompt add-ons), Remote Dashboard config, trader on/off |
| **Integrations** | Integrations panel | Grid of 30 cataloged third-party services; per-service credential entry or OAuth connect; feeds extra tool calls into the live session |
| **Sub-Agents / World** | World panel | Node-graph view of the active companion and its available sub-agents; add/edit/remove sub-agent companions |
| **Trader** | Trader panel | Paper/live crypto trading across 9 chains; wallet create/import/export; Seraph pre-trade risk gate; watchlist, journal, feed |
| **Remote Dashboard** | Browser (phone) + Settings → Remote Dashboard | LAN HTTPS web app + WebSocket relay so a phone can mic/text into the same session and mirror the Trader panel |
| **Onboarding** | Setup overlay, Remote-key overlay | First-run Gemini API key + OS entry; QR-code phone pairing with a rotating access key |

## Page / Panel Inventory

| # | Name | Entry point | Module | Doc |
|---|---|---|---|---|
| 1 | Main Window (HUD & Chat) | App launch | Core Assistant | [→](./pages/01-main-window.md) |
| 2 | Settings Panel | Sidebar gear icon | Settings / Companions / Integrations backing config | [→](./pages/02-settings-panel.md) |
| 3 | Integrations Panel | Sidebar / top-bar icon | Integrations | [→](./pages/03-integrations-panel.md) |
| 4 | World Panel | Sidebar / top-bar icon | Sub-Agents | [→](./pages/04-world-panel.md) |
| 5 | Trader Panel | Sidebar icon, or voice `launch_trader` tool | Trader | [→](./pages/05-trader-panel.md) |
| 6 | Remote Dashboard (phone web app) | `https://<lan-ip>:<port>` from a phone, via QR | Remote Dashboard | [→](./pages/06-remote-dashboard.md) |
| 7 | Onboarding / Setup Overlays | First launch with no API key; Remote button | Onboarding | [→](./pages/07-onboarding-setup.md) |

## Global Notes

### Architecture in one paragraph
`main.py`'s `JarvisLive` class owns one persistent connection to Gemini Live. On every (re)connect it calls `_build_config()`, which re-reads `settings.json` from disk and re-assembles the **entire** system prompt from scratch — identity, current date/time, the model's own name (so it can't hallucinate which model it is), long-term memory for the active companion's namespace, a one-line "last session" recap, an Obsidian-vault memory index, the trader on/off note, a sub-agent directory (if any), and any enabled Skills — plus the full tool list (built-in + custom MCP + active integrations). **This rebuild only happens once per connection** — see "Reconnect-to-apply" below.

### Reconnect-to-apply (recurring product behavior, not a bug)
Skills, MCP servers, and Integrations all take effect only on the *next* Gemini Live reconnect, because `_build_config()` is not re-run mid-session. Every settings screen that touches one of these now explicitly tells the user "— takes effect on next reconnect" after saving, rather than implying the change is live immediately (this was mis-reported as "the SKILLS feature doesn't work" by the beta tester before the messaging fix).

### Confirmation gates on irreversible actions
Anything that destroys state without an undo path gets an explicit confirmation dialog: removing a wallet, resetting the trader's paper ledger, removing a companion or custom key. The wallet's "arm live trading" step additionally requires the user to type a literal risk-acknowledgment phrase, not just click a button.

### A recurring Windows-only rendering bug class
The app runs Qt's `Fusion` style. On a Windows box set to OS-level dark mode, several native Qt widgets (`QMessageBox`, `QToolTip`, and one hand-built confirmation dialog) have rendered with invisible/illegible text, because Fusion's dark-mode-derived palette applies inconsistently between text color and face color. The fix pattern each time has been an explicit stylesheet override rather than relying on the native widget's default palette — worth knowing before adding any new native dialog.

### Storage
All user data lives locally under the app's per-user data directory (`core/app_paths.get_data_dir()`): `config/settings.json` (this PRD's `DEFAULT_SETTINGS`/`INTEGRATION_CATALOG` schema — see [enum-dictionary.md](./appendix/enum-dictionary.md)), `config/api_keys.json` (Gemini key, predates `settings.json`), per-namespace memory JSON files, and the trader's own config/state/journal files. There is no server-side account system — everything is single-machine, single-user.

### Uncertainty flagged
- The exact **live vs. planned** split for the Integrations catalog could not be confirmed from `core/settings_store.py::INTEGRATION_CATALOG` alone — every one of its 30 entries is currently marked `implemented: True` in the code read for this PRD. `[TBC]` whether a separate "coming soon" list exists elsewhere in the UI layer beyond what `IntegrationsPanel` renders from this same catalog.
- Whether a `forget_memory`-equivalent tool is exposed to Gemini itself is unconfirmed — `memory_manager.forget()` exists as a Python function, but no matching entry was found in `main.py`'s `TOOL_DECLARATIONS` (only `save_memory` is). `[TBC]`.
