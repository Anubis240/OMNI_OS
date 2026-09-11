# Onboarding & Setup Overlays

> **Source:** `ui.py` (`SetupOverlay`, `RemoteKeyOverlay`)
> **Module:** Onboarding
> **Generated:** 2026-09-11

## Overview

Two modal overlays that gate access to parts of the app until a precondition is met: `SetupOverlay` blocks the entire HUD on first run until a Gemini API key and OS selection are provided; `RemoteKeyOverlay` is the phone-pairing QR screen described in the Remote Dashboard page, listed here as the other "onboarding-shaped" surface in the app.

## Layout

### SetupOverlay
A centered modal card over a dimmed HUD: title/explainer text, an API-key password input, an OS selector, and a submit button.

### RemoteKeyOverlay
A centered modal card: LAN URL + access key rendered as a QR code (`_update_qr`), a countdown/expiry indicator (`_tick`), connected/disconnected state (`mark_connected`/`mark_disconnected`), and a "new key" action (`_refresh_key`) for when the prior key expires before pairing completes.

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

## Interactions

### First-run setup
- **Trigger:** app launch with no API key found (`_check_config` returns false).
- **Behavior:** `_show_setup` blocks the HUD; `_submit` validates and hands off `(key, os_name)` to `_on_setup_done`, which persists the key and unblocks normal startup (proceeding into the Main Window flow described in [01-main-window.md](./01-main-window.md)).

### Phone pairing
- **Trigger:** user clicks the Remote sidebar icon on an already-running Main Window.
- **Behavior:** see [06-remote-dashboard.md](./06-remote-dashboard.md#pair-via-qr) for the full pairing flow; this overlay is purely the desktop-side presentation of that flow.

## API Dependencies
None directly — `SetupOverlay` only writes local config; `RemoteKeyOverlay` only renders state produced by `DashboardServer.new_key()`.

## Page Relationships
- **From:** app launch (`SetupOverlay`, conditionally) or the Main Window sidebar (`RemoteKeyOverlay`).
- **To:** Main Window, once either overlay's precondition is satisfied.
