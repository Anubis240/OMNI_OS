# Omni-OS

**A real-time voice AI assistant that can hear, see, understand, and control your computer — and delegate work to a team of its own sub-agents.**

Omni-OS is a local-first, JARVIS-style desktop assistant built on Google's Gemini models
for real-time voice, vision, and system control. On top of that it runs a **multi-companion
system**: your main assistant can create and manage its own named sub-agents (e.g. "Bob",
"Ivy"), each backed by the Claude Agent SDK, to handle coding and project work — visualized
live in a **World view** node graph.
A built-in **Integrations tab** connects 30+ third-party services (GitHub, Slack, Notion,
Google Workspace, Microsoft 365, Jira/Confluence, Zoom, Dropbox, and more) as tools any
companion can call. An optional built-in crypto trading panel, guarded by pre-trade risk
checks, is available as an add-on. Connect your phone for remote control if you are on the same network.

Bring your own Gemini API key (required) and, optionally, either an Anthropic API key or
your own installed, subscription-authenticated Claude Code CLI — Omni-OS is licensed
software, not a hosted subscription; your usage bills directly to your own accounts.

---

## Features

| Feature | Description |
|---|---|
| 🎙️ Real-time Voice | Low-latency conversational voice, powered by Gemini — no wake word, just a one-click always-listening toggle |
| 🖥️ System Control | Launch apps, manage files, run terminal commands |
| 🧩 Autonomous Tasks | Planner/executor loop for complex, multi-step goals |
| 👁️ Visual Awareness | Live screen capture and webcam vision |
| 🤖 Multi-Companion System | Switch between multiple assistant personas, each with its own voice, memory, and system prompt |
| 🕸️ World View | A live node-graph of your sub-agents (Bob, Ivy, ...) showing status, with create/edit/delete controls |
| 🔗 Integrations Tab | Connect 30+ services (GitHub, Slack, Notion, Google Workspace, Microsoft 365, Jira, Zoom, Dropbox, and more) as callable tools |
| 🧑‍💻 Claude Code Delegation | Sub-agents run on the Claude Agent SDK — uses your existing logged-in Claude Code CLI subscription if present, or an Anthropic API key |
| 🧠 Persistent Memory | Remembers your projects, preferences, and context across sessions, namespaced per companion |
| 💹 Trader Panel (optional add-on) | Live crypto trading with Guardian pre-trade risk checks |
| ⚙️ Settings Panel | Manage API keys, companions, MCP connections, integrations, and skills from the UI |
| ⌨️ Hybrid Input | Switch freely between typed and spoken input |

---

## Requirements

| Requirement | Details |
|---|---|
| OS | Windows 10/11 (primary target; installer is Windows-only) |
| Python | 3.11 or 3.12 |
| Microphone | Required for voice interaction |
| API Key | Gemini API key (free tier available) |

---

## Getting API Keys

Omni-OS's default companion is powered by Google's Gemini models and needs a Gemini API
key to run.

1. Gemini (required): go to
   **[Google AI Studio → API Keys](https://aistudio.google.com/api-keys)**, sign in,
   click **Create API key**, and copy it. Paste it into Omni-OS via the in-app
   **Settings panel** on first launch.

Claude-backed sub-agents (Bob, Ivy, and any others you create in the **World view**) need
one of the following — no separate account is required if you already have a Claude Code
subscription:

2. **Claude Code CLI (recommended)** — if you already have
   [Claude Code](https://code.claude.com) installed and logged in with a Claude.ai
   subscription (Pro/Max/Team), Omni-OS will use that login automatically once you point
   **Settings → Claude Code Delegation** at your CLI install. No API billing needed.
3. **Anthropic API key (alternative)** — get one from the
   [Anthropic Console](https://console.anthropic.com/) if you'd rather pay per-token
   instead of using a subscription, and paste it into the Settings panel.

Treat these keys, and your Claude Code login, like passwords — don't commit them or share
them publicly. `config/api_keys.json` and `config/settings.json` are gitignored for this
reason; never remove them from `.gitignore`.

---

## Quick Start

```bash
git clone https://github.com/Anubis240/OMNI_OS.git
cd OMNI_OS
pip install -r requirements.txt
playwright install
python main.py
```

On first launch, open the **Settings panel** and add your API key(s) (see above).

> **Note:** To keep the repo lightweight, some OS-specific dependencies aren't pinned in `requirements.txt`. If you hit a `ModuleNotFoundError`, install the missing package for your platform.

### Windows Installer

A packaged Windows installer is also available under `installer/output` (built with Inno Setup from `installer/installer.iss`), for users who don't want to run from source.

---

## Project Layout

| Path | Purpose |
|---|---|
| `main.py` | Application entry point |
| `ui.py` | Main assistant UI |
| `world_panel.py` | World view — the live sub-agent node graph |
| `integrations_panel.py` | Integrations tab UI |
| `trader_panel.py`, `trader/` | Trading panel and engine (market data, chains, wallet, live execution) |
| `settings_panel.py` | In-app settings UI (API keys, companions, MCP, integrations, skills) |
| `agent/` | Planner, executor, task queue, error handling |
| `actions/` | Individual tool/action implementations (browser, files, desktop, code, etc.) |
| `actions/claude_companion.py` | Turn-based conversation driver for Claude-backed sub-agents |
| `actions/claude_agent.py` | One-shot Claude Agent SDK delegation tool |
| `actions/integrations/` | Per-service integration implementations (GitHub, Slack, Google, etc.) |
| `core/` | MCP registry, settings store, system prompt |
| `config/` | API keys, voice, theme, and trader configuration (gitignored, created on first run) |
| `dashboard/` | Local web dashboard (FastAPI) |
| `memory/` | Persistent memory storage, namespaced per companion |
| `installer/` | Windows installer (Inno Setup) |

---

## License

Proprietary — licensed software. See your purchase/license agreement for terms.
