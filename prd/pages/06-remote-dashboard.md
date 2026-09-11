# Remote Dashboard (Phone Web App)

> **Source:** `dashboard/server.py` (`DashboardServer`, embedded HTML/JS client)
> **Module:** Remote Dashboard
> **Generated:** 2026-09-11

## Overview

A small embedded HTTPS web server that turns any phone on the same LAN into a second input surface for the *same running desktop session* — no separate account, no cloud relay. The phone pairs via a QR code shown on the desktop, then gets a mirrored chat/mic interface and a read-mostly view of the Trader panel. This is explicitly different from the standalone Omni-OS Mobile app project: the dashboard requires the desktop PC to already be running and reachable, and does not run the assistant independently.

## Layout (as served to the phone browser)

A single self-contained HTML page (`_APP_HTML`, served at `/`) with:
- A login/key-entry screen (falls back to `/login` if not auto-authenticated).
- A chat transcript view mirroring the desktop's log.
- A mic capture control that streams raw PCM audio to the desktop over its own private WebSocket.
- A mirrored Trader stats/positions view (polls `/api/trader/state`, can submit actions via `/api/trader/action`).
- A shared-file link view for anything the desktop's `share_file` tool has exposed.

## Fields

### Pairing / Login
| Field | Type | Notes |
|---|---|---|
| Access key | Auto-filled from QR, or manual entry | Time-limited (`new_key(expiry_secs=600)` default) — a `RemoteKeyOverlay` on desktop shows the QR and a countdown, and can mint a fresh key |

## Interactions

### Pair via QR
- **Trigger:** desktop user opens the Remote pairing overlay (`RemoteKeyOverlay`).
- **Behavior:** desktop generates a fresh key + LAN URL, renders it as a QR code; scanning it opens `/auto-login?key=...` on the phone, which validates the key and redirects into the main app page, or the phone can instead see live connect/disconnect status reflected as `mark_connected`/`mark_disconnected` on the desktop overlay.

### Voice/text from the phone
- **Trigger:** phone user taps mic or types in the mirrored chat.
- **Behavior:** phone-side JS opens `/ws` for command/text/log traffic and a separate `/ws/audio` for raw mic audio; the desktop relays this into the same `JarvisLive` session as if spoken locally (`_relay_phone_audio`, `_process_dashboard_commands`).
- **Known issue, fixed this cycle:** the phone's mic-capture JS only opened `/ws/audio` lazily, from inside `startMic()`; if that reconnecting WebSocket handshake hadn't finished yet, the very first phone utterance after a reconnect could be silently dropped with zero buffering. Fixed by buffering pending mic frames (`pendingMicFrames`, capped at `MAX_PENDING_MIC_FRAMES`) and flushing them once the socket's `onopen` fires.

### Trader mirroring
- **Trigger:** phone polls `GET /api/trader/state`.
- **Behavior:** returns `MainWindow.get_trader_state()`'s public snapshot; a phone-submitted action (`POST /api/trader/action`) is dispatched through `MainWindow.run_trader_action`, with the result retrievable via `GET /api/trader/action/result?jobId=...` for actions that run asynchronously (e.g. a trade awaiting the Seraph gate).

### File sharing
- **Trigger:** desktop's `share_file` tool is called (e.g. after generating a document Gemini wants to hand the user a link to, since `file://` paths are meaningless on a phone).
- **Behavior:** `register_file(path)` returns a short-lived id; `GET /f/{file_id}` serves it to the phone browser.

## API Dependencies (this server's own routes)

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serves the single-page app shell |
| `/login` | GET | Manual key-entry page (fallback if no `key` query param) |
| `/login` | POST | Validates a submitted key, sets the session cookie |
| `/auto-login` | GET | QR-scan entry point — validates `key` query param, redirects in |
| `/static/avatar_bg.jpg` | GET | Static background asset for the phone UI |
| `/f/{file_id}` | GET | Serves a file previously registered via `share_file` |
| `/api/command` | POST | Text/command input from the phone |
| `/api/trader/state` | GET | Mirrored trader snapshot |
| `/api/trader/action` | POST | Submit a trader action from the phone |
| `/api/trader/action/result` | GET | Poll result of an async trader action by `jobId` |
| `/ws` | WebSocket | Main bidirectional channel: text, log lines, connection state, heartbeats |
| `/ws/audio` | WebSocket | Raw PCM mic-audio relay channel, kept separate from `/ws` so audio framing never interferes with JSON control messages |

Every route (except the static asset and the login pages themselves) is gated by `_auth(req)` checking the paired access key/session.

## Business Rules
- The server is self-signed HTTPS (`_ensure_self_signed_cert`/`regenerate_certificate`), LAN-only by construction — no port-forwarding or public exposure is part of this design.
- Access keys expire (default 600s to complete pairing) and can be regenerated on demand from the desktop overlay; there's no permanent phone-side credential.
- This protocol (JSON over the app's own `/ws`, raw binary PCM over `/ws/audio`) is a private, desktop-invented protocol between phone and PC — distinct from Gemini Live's own `BidiGenerateContent` wire protocol, which only the desktop speaks directly.

## Page Relationships
- **From:** Main Window's Remote pairing overlay (desktop side); a QR scan or manually-typed LAN URL (phone side).
- **To:** mirrors Main Window's chat and the Trader panel; has no navigation of its own beyond those two surfaces.
