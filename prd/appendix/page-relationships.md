# Page Relationships / Navigation Map

```
                         ┌─────────────────────┐
   (no API key) ───────► │  Setup Overlay       │
                         └──────────┬───────────┘
                                    │ key + OS submitted
                                    ▼
┌───────────────────────────────────────────────────────────────────┐
│                     Main Window (HUD & Chat)                       │
│  sidebar: mute / speech-mute / always-listening / trader /         │
│           settings / integrations / world / remote                 │
└───────┬───────────┬───────────────┬───────────────┬───────────────┘
        │            │               │               │
        ▼            ▼               ▼               ▼
   ┌─────────┐  ┌───────────┐  ┌───────────┐  ┌─────────────┐
   │ Settings│  │Integrations│  │   World   │  │   Trader    │
   │  Panel  │  │   Panel    │  │   Panel   │  │   Panel     │
   └─────────┘  └───────────┘  └───────────┘  └─────────────┘
        (each replaces the HUD's left panel area; toggled, not stacked)

   Main Window sidebar "Remote" ──► Remote Key Overlay (QR) ──pairing──► Remote Dashboard
                                                                          (phone browser,
                                                                           mirrors Main
                                                                           Window chat +
                                                                           Trader panel)
```

## Shared state coupling

| Written by | Read by | Shared state |
|---|---|---|
| Settings → Companions | Main Window (switcher), World (graph), `main.py::_build_config` | `settings["companions"]`, `settings["active_companion_id"]` |
| World panel | Main Window (sub-agent directory only via `_build_config`), `delegate_to_agent` dispatch | Same `settings["companions"]` list, filtered to agent backends |
| Settings → Skills / MCP Servers | `main.py::_build_config` (next reconnect only) | `settings["skills"]`, `settings["mcp_servers"]` |
| Integrations panel | `main.py::_build_config` (next reconnect only) | `settings["integrations"]` |
| Settings → Trader toggle | Main Window (`_refresh_trader_visibility`), `main.py` (tool filtering) | `settings["trader"]["enabled"]` |
| Trader panel | Remote Dashboard (`/api/trader/state`, `/api/trader/action`) | `TraderEngine.public_state()` / `.command()` via `MainWindow.get_trader_state`/`run_trader_action` |
| Remote Dashboard pairing (desktop) | Remote Dashboard (phone) | Access key + LAN URL, time-limited |

## Reconnect boundary

Everything left of the dashed line below takes effect **immediately on save**; everything right of it takes effect **only on the next Gemini Live reconnect** (companion switch, app restart, or any code path that raises `_ReconnectRequested`):

```
Immediately          |  On next reconnect only
----------------------|--------------------------------
Trader on/off toggle  |  Skills (add/edit/toggle/remove)
Wallet operations     |  MCP servers (add/remove)
API key text fields   |  Integrations (connect/disconnect)
Remote Dashboard port |  Companion system_prompt/model/voice edits
                       |  (switching active companion forces the reconnect itself)
```
