# S.E.R.A.P.H

**A real-time voice AI assistant that can hear, see, understand, and control your computer.**

S.E.R.A.P.H is a local-first, JARVIS-style desktop assistant built on Google's Gemini models. It talks with you in real time, sees your screen and webcam, executes multi-step tasks on your machine, and includes a built-in crypto trading panel guarded by risk checks before any trade goes out.

---

## Features

| Feature | Description |
|---|---|
| 🎙️ Real-time Voice | Low-latency conversational voice, powered by Gemini |
| 🖥️ System Control | Launch apps, manage files, run terminal commands |
| 🧩 Autonomous Tasks | Planner/executor loop for complex, multi-step goals |
| 👁️ Visual Awareness | Live screen capture and webcam vision |
| 🧠 Persistent Memory | Remembers your projects, preferences, and context across sessions |
| 💹 Trader Panel | Live crypto trading with Seraph Guardian pre-trade risk checks |
| ⚙️ Settings Panel | Manage API keys, MCP connections, and skills from the UI |
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

## Getting a Gemini API Key

S.E.R.A.P.H is powered by Google's Gemini models and needs a Gemini API key to run.

1. Go to **[Google AI Studio → API Keys](https://aistudio.google.com/api-keys?project=gen-lang-client-0368720913)**.
2. Sign in with your Google account.
3. Click **Create API key** and copy it.
4. Paste it into S.E.R.A.P.H via the in-app **Settings panel**, or set it directly in `config/api_keys.json` under `gemini_api_key`.

Treat this key like a password — don't commit it or share it publicly.

---

## Quick Start

```bash
git clone https://github.com/Kondux/S.E.R.A.P.H-python-version-FULL.git
cd S.E.R.A.P.H-python-version-FULL
pip install -r requirements.txt
playwright install
python main.py
```

On first launch, open the **Settings panel** and add your Gemini API key (see above).

> **Note:** To keep the repo lightweight, some OS-specific dependencies aren't pinned in `requirements.txt`. If you hit a `ModuleNotFoundError`, install the missing package for your platform.

### Windows Installer

A packaged Windows installer is also available under `installer/output` (built with Inno Setup from `installer/installer.iss`), for users who don't want to run from source.

---

## Project Layout

| Path | Purpose |
|---|---|
| `main.py` | Application entry point |
| `ui.py` | Main assistant UI |
| `trader_panel.py`, `trader/` | Trading panel and engine (market data, chains, wallet, live execution) |
| `settings_panel.py` | In-app settings UI (API keys, MCP, skills) |
| `agent/` | Planner, executor, task queue, error handling |
| `actions/` | Individual tool/action implementations (browser, files, desktop, code, etc.) |
| `core/` | MCP registry, settings store, system prompt |
| `config/` | API keys, voice, theme, and trader configuration |
| `dashboard/` | Local web dashboard (FastAPI) |
| `memory/` | Persistent memory storage |
| `installer/` | Windows installer (Inno Setup) |

---

## License

Personal and non-commercial use only, unless otherwise agreed.
