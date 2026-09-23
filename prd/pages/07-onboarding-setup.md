# Onboarding & Setup Overlays

> **Source:** `ui.py` (`SetupOverlay`, `RemoteKeyOverlay`); `trader_panel.py` (`McpKeySetupOverlay`)
> **Module:** Onboarding
> **Generated:** 2026-09-11

## Overview

Three modal overlays that gate access to parts of the app until a precondition is met: `SetupOverlay` blocks the entire HUD on first run until a Gemini API key and OS selection are provided; `RemoteKeyOverlay` is the phone-pairing QR screen described in the Remote Dashboard page; `McpKeySetupOverlay` ("Connect to Seraph") gates the Trader Panel when no Seraph credential is available or the session expires. The Seraph overlay is local to the Trader Panel, not a global app overlay.

## Layout

### SetupOverlay
A centered modal card over a dimmed HUD: title/explainer text, an API-key password input, an OS selector, and a submit button.

### RemoteKeyOverlay
A centered modal card: LAN URL + access key rendered as a QR code (`_update_qr`), a countdown/expiry indicator (`_tick`), connected/disconnected state (`mark_connected`/`mark_disconnected`), and a "new key" action (`_refresh_key`) for when the prior key expires before pairing completes.

### McpKeySetupOverlay ("Connect to Seraph")
A modal inside the Trader Panel with a primary browser sign-in button, a status line, and a cancel button during pending login. An alternative API-key link reveals a password input and save button, with a link to obtain a key in the console. All panel controls (configuration, start, scan, and command bar) remain blocked until authentication completes. There is no "Skip for now" option: without authentication, the trader cannot be used. When system encryption is unavailable, the overlay warns that the credential will be stored without operating-system protection.

## Fields

### SetupOverlay
| Field | Type | Required | Notes |
|---|---|---|---|
| Gemini API key | Password input | Yes | Stored to `config/api_keys.json` |
| OS | Selector (`_sel`) | Yes | Used to tailor OS-specific behavior in `computer_settings`/`desktop_control` tools |

### RemoteKeyOverlay
| Field | Type | Notes |
|---|---|---|
| QR code | Generated image | Encodes the LAN URL + access key |
| Expiry countdown | Read-only, ticking | `expiry_secs` default 600 |

### McpKeySetupOverlay
| Field | Type | Notes |
|---|---|---|
| Sign in with Seraph | Button | Opens browser sign-in; creates the device API key automatically |
| Status | Read-only text | Shows sign-in progress and completion |
| Cancel | Button | Visible while login is pending |
| Use an API key instead | Link | Reveals the manual API-key entry controls |
| API key | Password input | Accepts a key created manually in the console; saved with "SAVE KEY" |
| SAVE KEY | Button | Saves the manually entered API key |
| Get a Seraph API key ↗ | Link | Opens the console to obtain a manual key |

## Interactions

### First-run setup
- **Trigger:** app launch with no API key found (`_check_config` returns false).
- **Behavior:** `_show_setup` blocks the HUD; `_submit` validates and hands off `(key, os_name)` to `_on_setup_done`, which persists the key and unblocks normal startup (proceeding into the Main Window flow described in [01-main-window.md](./01-main-window.md)).

### Phone pairing
- **Trigger:** user clicks the Remote sidebar icon on an already-running Main Window.
- **Behavior:** see [06-remote-dashboard.md](./06-remote-dashboard.md#pair-via-qr) for the full pairing flow; this overlay is purely the desktop-side presentation of that flow.

### Seraph sign-in
- **Trigger:** opening the Trader Panel without a Seraph credential, or session expiration.
- **Primary flow:** "Sign in with Seraph" opens the browser for OAuth 2.1 with PKCE and a loopback listener on `127.0.0.1` at an ephemeral port. The client uses dynamic client registration (DCR) on first use. The user authenticates through Privy by email, SMS, or wallet (MetaMask/SIWE).
- **Browser consent:** the first consent authorizes creation of the device API key. A second consent appears only when the requested scope includes transaction execution and the signer has not yet been granted. It authorizes Seraph to execute trades within server-enforced limits: 0.02 ETH per transaction, 0.2 ETH and 20 transactions per day, restricted to Uniswap swaps, approve, and unwrap. This authorization can be revoked at any time in the console.
- **Declining execution consent:** choosing "Agora não" on the second consent does not cancel login. The session continues, the API key is created, and the trader works in paper mode; only live mode remains unavailable until the signer is authorized.
- **Completion:** the device API key is created automatically, with no copying or pasting required, and the panel is unlocked.
- **Status messages, in order:** "Checking Seraph configuration…", "Opening your browser to sign in…", "Waiting for browser sign-in…", "Completing sign-in…", "Creating your Seraph API key…", "Signed in".
- **Alternative flow:** "Use an API key instead" reveals the password field for a key created manually in the console. A manual key does not carry the execution scope, so live mode is refused with an explicit message.

## API Dependencies
- `SetupOverlay` has no direct API dependency; it only writes local config.
- `RemoteKeyOverlay` only renders state produced by `DashboardServer.new_key()`.
- `McpKeySetupOverlay` depends on the Seraph authorization server (metadata, dynamic client registration, authorize, and token) and the control-plane (device API-key creation and revocation).

## Page Relationships
- **From:** app launch (`SetupOverlay`, conditionally) or the Main Window sidebar (`RemoteKeyOverlay`).
- **To:** Main Window, once either overlay's precondition is satisfied.
- **From (Seraph):** opening the Trader Panel without a credential, or session expiration within that panel.
- **To (Seraph):** the unlocked [Trader Panel](./05-trader-panel.md), once authentication completes; this does not gate the rest of the app. Live mode additionally requires execution scope and signer authorization.
