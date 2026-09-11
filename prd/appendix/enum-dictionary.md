# Enum Dictionary

> Every value list below was read directly from source, not inferred. Line refs point at `Omni-OS/` unless noted.

## Gemini Live Prebuilt Voices
`ui.py:150` — `VOICES = ["Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"]`
Selectable per-companion (Settings → Companions) or as the global default (`JarvisUI.voice`).

## Companion Backend
`core/settings_store.py:64-68` — a companion's `backend` field:
| Value | Meaning |
|---|---|
| `gemini_live` | Realtime voice companion — the only backend that can be the "active" HUD companion |
| `claude_agent` | Turn-based delegation to a local Claude Code CLI |
| `codex_agent` | Turn-based delegation to OpenAI Codex CLI |
| `opencode_agent` | Turn-based delegation to OpenCode CLI |
| `openhands_agent` | Turn-based delegation to OpenHands CLI |
| `grok_agent` | Turn-based delegation to Grok CLI |
| `blackbox_agent` | Turn-based delegation to Blackbox CLI |

Any non-`gemini_live` backend is eligible as a `delegate_to_agent` sub-agent target (see [page-relationships.md](./page-relationships.md)).

## Companion Schema
`core/settings_store.py:62-80` — one entry in `settings["companions"]`:
`{id, name, backend, model, system_prompt, avatar, voice, memory_namespace, mcp_server_ids, enabled, specialty}`
- `model`: per-companion Gemini Live model override (falls back to `settings["live_model"]`, then the hardcoded `DEFAULT_LIVE_MODEL`).
- `memory_namespace`: isolates long-term memory per companion.
- `mcp_server_ids`: scopes which custom MCP servers' tools this companion sees.
- `specialty`: shown in the World graph and in the lead companion's sub-agent directory; not meaningful for `gemini_live` companions.

## Memory Categories
`memory/memory_manager.py` (via `save_memory` tool description in `main.py`):
| Category | Contains |
|---|---|
| `identity` | name, age, birthday, city, job, language, nationality |
| `preferences` | favorite food/color/music/film/game/sport, hobbies |
| `projects` | active projects, goals, things being built |
| `relationships` | friends, family, partner, colleagues |
| `wishes` | future plans, things to buy, travel dreams |
| `notes` | habits, schedule, anything else worth remembering |

## Integration `auth_type`
`core/settings_store.py:94-102`:
| Value | Meaning |
|---|---|
| `credentials` | One or more pasted fields (API key/token/key+secret); saved directly, no round-trip |
| `local` | No credentials — just a reachable base URL (Ollama) |
| `oauth` | Real browser consent flow, only on the `google`/`microsoft` family entries; member services reuse the family's stored token |

## Integration Catalog
`core/settings_store.py:107-212` — 30 entries, every one currently marked `implemented: True` as read (see [README.md](../README.md#uncertainty-flagged) for the caveat on a possible separate "planned" list elsewhere):

| Id | Name | Category | Auth |
|---|---|---|---|
| `github` | GitHub | Developer Tools | credentials |
| `notion` | Notion | Productivity | credentials |
| `obsidian` | Obsidian | Note-Taking | local |
| `slack` | Slack | Communication | credentials |
| `trello` | Trello | Project Management | credentials |
| `discord` | Discord | Communication | credentials |
| `elevenlabs` | ElevenLabs | AI Models | credentials |
| `openai` | OpenAI | AI Models | credentials |
| `deepseek` | DeepSeek | AI Models | credentials |
| `huggingface` | Hugging Face | AI Models | credentials |
| `ollama` | Ollama | AI Models | local |
| `cloudflare` | Cloudflare | Developer Tools | credentials |
| `digitalocean` | DigitalOcean | Developer Tools | credentials |
| `google` | Google Account | Account | credentials (OAuth family root) |
| `gmail` | Gmail | Email | oauth (family: google) |
| `google_calendar` | Google Calendar | Scheduling | oauth (family: google) |
| `google_drive` | Google Drive | File Storage | oauth (family: google) |
| `google_sheets` | Google Sheets | Productivity | oauth (family: google) |
| `google_docs` | Google Docs | Productivity | oauth (family: google) |
| `google_tasks` | Google Tasks | Productivity | oauth (family: google) |
| `google_photos` | Google Photos | Media | oauth (family: google) |
| `google_maps` | Google Maps | Maps & Location | credentials (plain API key, not OAuth) |
| `youtube` | YouTube | Media | oauth (family: google) |
| `microsoft` | Microsoft Account | Account | credentials (OAuth family root) |
| `outlook` | Outlook | Email | oauth (family: microsoft) |
| `onedrive` | OneDrive | File Storage | oauth (family: microsoft) |
| `jira` | Jira | Project Management | credentials |
| `confluence` | Confluence | Docs & Wiki | credentials (shares Jira's API token) |
| `zoom` | Zoom | Video Meetings | credentials |
| `calendly` | Calendly | Scheduling | credentials |
| `dropbox` | Dropbox | File Storage | credentials |

## Trader Chains
`trader/chains.py:8-80` — 9 cataloged chains; 7 have a `live` sub-config (real contract addresses + RPC endpoints), 3 are catalog/analytics-only:

| Key | Name | Chain ID | Live trading? |
|---|---|---|---|
| `ethereum` | Ethereum | 1 | Yes |
| `optimism` | Optimism | 10 | Yes |
| `unichain` | Unichain | 130 | Yes |
| `polygon` | Polygon | 137 | No (paper/analytics only) |
| `worldchain` | World Chain | 480 | Yes |
| `soneium` | Soneium | 1868 | No (paper/analytics only) |
| `robinhood` | Robinhood Chain | 4663 | Yes |
| `base` | Base | 8453 | Yes |
| `arbitrum` | Arbitrum One | 42161 | Yes |
| `ink` | Ink | 57073 | No (paper/analytics only) |

`DEFAULT_CHAIN = "ethereum"`. Live chains route through Uniswap V2/V3 router + quoter contracts per-chain; each carries 1-2 public RPC fallback URLs.

## Dashboard Access-Key Lifecycle
`dashboard/server.py::new_key(expiry_secs=600)` — default 10-minute expiry window to complete phone pairing; regenerable on demand from the desktop's `RemoteKeyOverlay`.
