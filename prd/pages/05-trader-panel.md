# Trader Panel

> **Source:** `trader_panel.py` (`TraderPanel`, `McpKeySetupOverlay`), engine in `trader/engine.py` (`TraderEngine`), chain registry in `trader/chains.py`
> **Module:** Trader
> **Generated:** 2026-09-11

## Overview

An optional, self-contained crypto trading feature (off by default — enabled via Settings → Trader). Runs a scanning/trading engine across 9 EVM chains, gated by a third-party risk-check service ("Seraph") before any real money moves, with wallet management, a live feed, a watchlist, a positions table, and a typed command bar. The voice assistant can only *open* this panel (`launch_trader` tool); it never places a trade itself — trades are only ever entered directly into this panel's own command bar or its buy/sell buttons.

## Layout

- **Left config panel** (`_build_left_config_panel`): Seraph API key entry, wallet row (create/import/export/lock/remove), arm/disarm live trading, trading config fields (loaded via `_load_config_into_ui`).
- **Center feed**: a scrolling text+widget feed (`_append_feed_text`/`_append_feed_widget`, capped and trimmed via `_trim_feed`) showing engine events, trade confirmations (with clickable tx-hash buttons linking to a block explorer), and Seraph gate results.
- **Stats bar** (`_refresh_stats`): equity, P&L, and other engine-reported metrics.
- **Positions table** (`_refresh_positions`): open positions with inline Sell / Take-Profit buttons and percent-based partial-sell pickers.
- **Watchlist** (`_refresh_watchlist`): tracked tokens not currently held, with Buy/Remove actions.
- **Command bar** (`_on_command_submit` → `_run_command`): a single text field for typed trading commands.

## Fields

### Wallet Row
| Field | Type | Notes |
|---|---|---|
| Create wallet | Button | Generates a new wallet; the recovery phrase is shown exactly once in a modal dialog (`_SecretRevealDialog`) the user must acknowledge and close — never written to the Event Feed |
| Import wallet | Button + text input | Accepts a private key; `_looks_like_private_key` sanity-checks the format before accepting |
| Export wallet | Button | Reveals the private key or recovery phrase in the same one-time modal as Create, behind its own distinct confirmation on top of the general risk-acknowledgment gate |
| Lock/Unlock | Button | Toggles wallet accessibility without removing it — does **not** require the risk-acknowledgment phrase, unlike Create/Import/Export/Remove |
| Remove wallet | Button | Its own distinct confirmation dialog, on top of the general risk-acknowledgment gate |

### Arm/Disarm Live Trading
| Field | Type | Notes |
|---|---|---|
| Risk acknowledgment | Typed confirmation text | Must match an exact "I OWN THIS RISK"-style phrase before Arm is accepted (`_risk_acknowledged`) |
| Arm Live | Button | Calls `engine.arm_live()`; only real chains flagged live-enabled can execute real trades even once armed |
| Disarm Live | Button | Calls `engine.disarm_live(reason)`, reversible any time |

### Trading Config
Loaded/saved via `_load_config_into_ui` / `_on_save_config` → `TraderEngine.set_config`; exact field set lives in the engine's config file, covering at minimum trade-size sizing (`_pick_trade_size_usd`) and which chains are live-enabled (`_enabled_live_chains`).

### Command Bar
Free-text commands parsed by `TraderEngine.command()` — natural-language-ish trading commands (buy/sell/watch/etc.), plus the same actions exposed as buttons.

## Interactions

### Buy / Sell (button or typed command)
- **Trigger:** a watchlist Buy click, a position's Sell/Take-Profit click, a percent-picker, or a typed command.
- **Behavior:** always runs in the background (`_background`, via `_run_command`), since it can hit real network calls (price lookup, the Seraph pre-trade gate, and — once armed live — real RPC calls and on-chain confirmation); an immediate "checking with Seraph" status line appears before the engine's own streaming log lines. A watchlist Buy click reuses the exact same gated path as typing `buy SYM:0xADDR` — no shortcut around the risk gate.
- **Risk gate:** `_risk_check`/`_poll_gate_result` call the Seraph pre-trade check, parse a verdict (`_parse_verdict`), and only proceed on an allow verdict within a ~40s polling budget.

### Reset (paper ledger wipe)
- **Trigger:** RESET button next to SAVE CONFIG.
- **Behavior:** confirmation dialog required before `engine.reset()` wipes the persisted paper trade ledger/P&L history — added after a beta report that this button used to fire on a single click with no way back, immediately next to Save.

### Auto-scan / auto-trade cycle
- **Trigger:** internal loop (`_loop`/`_cycle`) once the engine is started (`start()`).
- **Behavior:** on each cycle, refreshes trending suggestions, checks stop conditions on open positions (`_check_stops`), and — if configured — executes buys/sells automatically, subject to the same Seraph gate as manual trades.

## API Dependencies

| API | Trigger | Notes |
|---|---|---|
| Seraph pre-trade risk gate (MCP) | Every buy/sell, manual or automatic | `_risk_check` builds gate args from a schema (`_build_gate_args`) and polls for a verdict |
| Per-chain RPC (Uniswap V2/V3 router + quoter contracts) | A live-armed trade execution on a live-enabled chain | Two separate gates, corrected 2026-09-12 after GEMZ4US's doc review caught this conflated: (1) **capability** — `trader/chains.py`'s registry gives 7 of 9 cataloged chains live trading contract addresses (Ethereum, Optimism, Unichain, World Chain, Robinhood Chain, Base, Arbitrum); Polygon, Soneium, and Ink are cataloged for price/analytics only. (2) **actually enabled** — `TraderEngine._is_chain_live_enabled()` additionally requires the chain to be in the trading config's `chains` list, whose shipped default (`DEFAULT_CONFIG["chains"]`, `trader/engine.py`) is `["ethereum"]` only. So out of the box, only Ethereum executes live trades even though 6 more chains are technically wired — and there's currently no UI field to add more (`_CONFIG_FIELDS` in `trader_panel.py` has no `chains` entry; changing it means hand-editing the trader config file). See [enum-dictionary.md](../appendix/enum-dictionary.md) |
| DexScreener / market-data feed | Trending suggestions refresh | `trending_suggestions`/`cached_suggestions` |

## Business Rules
- No real trade executes without both (a) live trading armed with the typed risk acknowledgment, and (b) an allow verdict from the Seraph gate for that specific trade.
- Every irreversible action in this panel (wallet remove, wallet export, config reset) requires an explicit confirmation step of equal or greater friction than the action's real-world consequence.

## Page Relationships
- **From:** Main Window sidebar icon, or the `launch_trader` voice/tool call (routed through a Qt signal to stay on the GUI thread).
- **To:** none (terminal panel) — but its live state (`get_trader_state`/`run_trader_action` on `MainWindow`) is also what the Remote Dashboard mirrors to a paired phone.
