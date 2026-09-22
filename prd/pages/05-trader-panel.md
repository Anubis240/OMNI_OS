# Trader Panel

> **Source:** `trader_panel.py` (`TraderPanel`, `McpKeySetupOverlay`), authentication in `trader/seraph_auth.py`, engine in `trader/engine.py` (`TraderEngine`), chain registry in `trader/chains.py`
> **Module:** Trader
> **Generated:** 2026-09-11

## Overview

An optional, self-contained crypto trading feature (off by default — enabled via Settings → Trader). In version 1.12.0, Seraph login is required before any use; the local desktop wallet has been replaced by a server-custodial wallet (Carteira Seraph), with no secret signing material on the device. Runs a scanning/trading engine across 9 EVM chains, gated by a third-party risk-check service ("Seraph") before any real money moves, with a live feed, a watchlist, a positions table, and a typed command bar. The voice assistant can only *open* this panel (`launch_trader` tool); it never places a trade itself — manual trades are entered directly into this panel's own command bar or its buy/sell buttons.

## Layout

- **Left config panel** (`_build_left_config_panel`): Seraph identity (`Seraph account: …`) with a single "Desconectar este dispositivo" button, a manual API key field as a fallback, read-only Carteira Seraph details, arm/disarm live trading, and trading config fields (loaded via `_load_config_into_ui`).
- **Connection overlay:** "Connect to Seraph" blocks the entire panel while no credential is available. There is no skip option.
- **Center feed**: a scrolling text+widget feed (`_append_feed_text`/`_append_feed_widget`, capped and trimmed via `_trim_feed`) showing engine events, trade confirmations (with clickable tx-hash buttons linking to a block explorer), and Seraph gate results.
- **Stats bar** (`_refresh_stats`): equity, P&L, and other engine-reported metrics.
- **Positions table** (`_refresh_positions`): open positions with inline Sell / Take-Profit buttons and percent-based partial-sell pickers.
- **Watchlist** (`_refresh_watchlist`): tracked tokens not currently held, with Buy/Remove actions.
- **Command bar** (`_on_command_submit` → `_run_command`): a single text field for typed trading commands.

## Fields

### Carteira Seraph (somente leitura)
| Field | Type | Notes |
|---|---|---|
| Endereço da Carteira Seraph | Label + "Copiar endereço" button | Embedded wallet custodied by Privy; the desktop never sees its secret signing material |
| Estado do signer | Label | "Signer: autorizado" or "Signer: não autorizado — autorize no console"; live mode is refused without authorization |
| Carteira externa (login) | Read-only label | Appears only for wallet login (MetaMask/SIWE); never used for trading |
| Depositar | Button | Opens a dialog with the full Carteira Seraph address and supported networks |
| Retirar ↗ / Gerenciar no console ↗ | Buttons | Open `https://seraph.kondux.io/wallet` in the browser |

### Duas carteiras, uma opera
"Seus trades usam a Carteira Seraph (<embedded>), não sua carteira externa (<externa>). Deposite fundos na Carteira Seraph para operar."

There is no wallet selector or wallet switching. Manual trades and automatic mode use **only** the Carteira Seraph. The external wallet is displayed solely to prevent the user from depositing into the wrong address.

### Retirar
The "Retirar ↗" button opens the console. Withdrawals are signed by the wallet **owner** in the Privy UI (owner path) and, by design, do **not** pass through the Seraph gate or the delegated signer: this is the user moving their own money, not the trading bot.

### Arm/Disarm Live Trading
| Field | Type | Notes |
|---|---|---|
| Live confirmation | Typed confirmation text + signer authorization | Type `LIVE` and have an authorized signer (`signerGranted`). Without it: "Autorize a Carteira Seraph no console antes de operar em live" |
| Arm Live | Button | Calls `engine.arm_live()`; only real chains flagged live-enabled can execute real trades even once armed |
| Disarm Live | Button | Calls `engine.disarm_live(reason)`, reversible any time |

### Trading Config
Loaded/saved via `_load_config_into_ui` / `_on_save_config` → `TraderEngine.set_config`; exact field set lives in the engine's config file, covering at minimum trade-size sizing (`_pick_trade_size_usd`) and which chains are live-enabled (`_enabled_live_chains`).

### Command Bar
Free-text commands parsed by `TraderEngine.command()` — natural-language-ish trading commands (buy/sell/watch/etc.), plus the same actions exposed as buttons.

## Interactions

### Login no Seraph
- **Trigger:** "Sign in with Seraph" opens the browser for OAuth with PKCE and a listener on `127.0.0.1`.
- **Credential creation:** an API key is minted automatically at the end of login with scopes `mcp` and `wallet:execute`. In this flow, the user never copies or pastes a key; manual entry remains a fallback.
- **Header states** (`_seraph_status_text`): `Seraph: key mcfw_xxxxxxx (signed in)`, with `(manual)` or `(legacy)` for those credential sources; `not connected to Seraph — sign in to use the trader`; `Seraph session expired — sign in again`; and the suffix ` — key rejected`. The full key is never displayed, only its 12-character prefix.
- **Disconnect:** the sole exit button, "Desconectar este dispositivo", revokes the device key on the server and clears local state. There is no separate logout action.

### Precedência da credencial
Credentials are selected in this order:
1. Explicit key passed to the constructor.
2. Key minted by OAuth login.
3. Manual key saved by the user.
4. Legacy key migrated from the Seraph Guardian app.
5. None: the panel remains blocked and makes no network calls.

### Execução de uma transação
Every transaction (approve, buy, sell, unwrap) follows two steps:
1. `guardian_pretrade_check` records the exact evaluated payload and returns a `requestId`.
2. `guardian_execute` receives **only** the `requestId`; transaction fields are never sent in or read from its input. The server signs using the custodial wallet.

Server-enforced limits are **0.02 ETH per transaction**, **0.2 ETH and 20 transactions per day**. A normal-path sale triggers three gate checks (minimum-profit check, approve, and swap) and two executions.

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
| `guardian_execute` / `guardian_wallet_status` (MCP) | Transaction execution / wallet status retrieval | Server-custodial execution by `requestId` / Carteira Seraph and signer status |
| Seraph control-plane (OAuth) | Device login / disconnect | Automatic minting and server-side revocation of the device API key |
| Per-chain RPC (Uniswap V2/V3 router + quoter contracts) | A live-armed trade execution on a live-enabled chain | Two separate gates, corrected 2026-09-12 after GEMZ4US's doc review caught this conflated: (1) **capability** — `trader/chains.py`'s registry gives 7 of 9 cataloged chains live trading contract addresses (Ethereum, Optimism, Unichain, World Chain, Robinhood Chain, Base, Arbitrum); Polygon, Soneium, and Ink are cataloged for price/analytics only. (2) **actually enabled** — `TraderEngine._is_chain_live_enabled()` additionally requires the chain to be in the trading config's `chains` list, whose shipped default (`DEFAULT_CONFIG["chains"]`, `trader/engine.py`) is `["ethereum"]` only. So out of the box, only Ethereum executes live trades even though 6 more chains are technically wired — and there's currently no UI field to add more (`_CONFIG_FIELDS` in `trader_panel.py` has no `chains` entry; changing it means hand-editing the trader config file). See [enum-dictionary.md](../appendix/enum-dictionary.md) |
| DexScreener / market-data feed | Trending suggestions refresh | `trending_suggestions`/`cached_suggestions` |

## Business Rules
- No real trade executes without (a) Seraph login, (b) an authorized signer, (c) typing `LIVE` to arm live trading, and (d) an `allow` verdict from the Seraph gate for that specific transaction.
- Remaining irreversible actions are the paper-ledger reset and device disconnection: reset requires explicit confirmation before deleting ledger/P&L history; "Desconectar este dispositivo" revokes the device key on the server and clears local state.

## Page Relationships
- **From:** Main Window sidebar icon, or the `launch_trader` voice/tool call (routed through a Qt signal to stay on the GUI thread).
- **To:** none (terminal panel) — but its live state (`get_trader_state`/`run_trader_action` on `MainWindow`) is also what the Remote Dashboard mirrors to a paired phone.
