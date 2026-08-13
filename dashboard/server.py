"""
dashboard/server.py — Omni-OS Remote Dashboard

A small local HTTP server that lets a phone on the same LAN pair with Omni
via a QR code (or a 6-character manual key), then send it text commands, talk
to it with its mic, and hear Omni's replies. Deliberately scoped down from
a "full" remote-control surface: no file transfer, no automatic firewall
configuration. Pairing is gated by a short-lived one-time key; once paired,
requests carry a per-session bearer token.

Served over HTTPS with a self-signed certificate (generated once, on first
run) rather than plain HTTP — mobile browsers only expose microphone access
(getUserMedia) on secure origins, and a LAN IP isn't "secure" to them unless
it's HTTPS. The phone will show a certificate warning the first time it
connects; that's expected for a self-signed cert and just needs "proceed
anyway" / "visit site" once.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import secrets
import socket
import string
import sys
import threading
import time
from pathlib import Path

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

PORT = 8000


def _base_dir() -> Path:
    # Matches main.py's get_base_dir()/ui.py's _base_dir() exactly — must
    # resolve to the real install directory (not a --onefile temp
    # extraction dir, which Path(__file__) would give instead) so the
    # self-signed cert persists across restarts rather than regenerating
    # (and re-triggering the phone's "proceed anyway" warning) every launch.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
STATIC_DIR = BASE_DIR / "dashboard" / "static"
AVATAR_BG_FILE = STATIC_DIR / "avatar_bg.jpg"
CERT_DIR = BASE_DIR / "config" / "certs"
CERT_FILE = CERT_DIR / "seraph.crt"
KEY_FILE  = CERT_DIR / "seraph.key"


def _ensure_self_signed_cert(ip: str) -> tuple[Path, Path] | None:
    """Generate a self-signed cert/key for this machine's LAN IP if one
    doesn't already exist. Returns (cert_path, key_path), or None if the
    `cryptography` package isn't installed."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return CERT_FILE, KEY_FILE
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime
        import ipaddress
    except ImportError:
        return None

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)])

    san_entries = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    try:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
    except ValueError:
        pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"[Dashboard] Generated self-signed certificate for {ip} (valid 10 years).")
    return CERT_FILE, KEY_FILE

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]


def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


_APP_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Omni-OS Remote</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; color: #f0f0f0;
    font-family: "Segoe UI", sans-serif;
    display: flex; flex-direction: column; height: 100vh;
    background:
      linear-gradient(rgba(6,13,22,0.70), rgba(6,13,22,0.70)),
      url('/static/avatar_bg.jpg') center 15%/cover no-repeat;
    background-color: #000000;
  }
  header {
    padding: 14px 16px; border-bottom: 1px solid #2a2a2e;
    font-weight: bold; letter-spacing: 2px; color: #ff8c42;
    display: flex; justify-content: space-between; align-items: center;
  }
  #status { font-size: 11px; color: #575757; }
  #status.live { color: #06d6a0; }
  #log {
    flex: 1; overflow-y: auto; padding: 12px 16px;
    font-size: 14px; line-height: 1.5;
  }
  #log div { margin-bottom: 8px; white-space: pre-wrap; word-break: break-word; }
  .you { color: #48cae4; }
  .seraph { color: #f0f0f0; }
  .sys { color: #575757; font-style: italic; font-size: 12px; }
  #log img { max-width: 100%; border-radius: 8px; margin-bottom: 8px; display: block; border: 1px solid #2a2a2e; }
  .file-link {
    display: inline-block; margin-bottom: 8px; padding: 10px 14px;
    background: #1a0f08; color: #ff8c42; border: 1px solid #b35a1f;
    border-radius: 8px; text-decoration: none; font-size: 13px;
  }
  .file-link:active { background: #b35a1f; color: #000000; }
  .force-sell-row {
    display: flex; align-items: center; gap: 8px; margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .force-sell-btn {
    padding: 5px 10px; font-size: 11px; font-weight: bold; border-radius: 4px;
    background: #000000; color: #ef476f; border: 1px solid #ef476f; font-family: inherit;
  }
  .force-sell-btn:active { background: #ef476f; color: #000000; }
  #voicebar {
    display: flex; align-items: center; gap: 10px; padding: 8px 16px;
    border-top: 1px solid #2a2a2e; background: #0a0a0c; font-size: 11px; color: #575757;
  }
  #mic-btn {
    width: 46px; height: 46px; border-radius: 50%; flex-shrink: 0;
    background: #1a0f08; color: #ff8c42; border: 1px solid #b35a1f;
    font-size: 20px; display: flex; align-items: center; justify-content: center;
  }
  #mic-btn.recording { background: #ef476f; color: #000000; border-color: #ef476f; animation: pulse 1s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.6} }
  form {
    display: flex; gap: 8px; padding: 10px 12px;
    border-top: 1px solid #2a2a2e; background: #0a0a0c;
  }
  input {
    flex: 1; background: #000000; color: #f0f0f0;
    border: 1px solid #2a2a2e; border-radius: 6px;
    padding: 10px 12px; font-size: 15px; font-family: inherit;
  }
  input:focus { outline: none; border-color: #ff8c42; }
  button {
    background: #1a0f08; color: #ff8c42; border: 1px solid #b35a1f;
    border-radius: 6px; padding: 0 18px; font-weight: bold; font-family: inherit;
  }
  button:active { background: #b35a1f; color: #000000; }
  #trader-btn { padding: 0 10px; font-size: 12px; margin-left: 8px; }
  #trader-btn.open { background: #b35a1f; color: #000000; }
  #trader-panel {
    display: none; flex: 1; overflow-y: auto; padding: 12px 16px;
    font-size: 13px; background: rgba(6,13,22,0.92); border-bottom: 1px solid #2a2a2e;
  }
  #trader-panel.show { display: block; }
  #trader-panel .row { display: flex; justify-content: space-between; gap: 10px; }
  #trader-stats {
    display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px 16px;
    margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px solid #2a2a2e;
  }
  #trader-stats div { font-size: 11px; color: #575757; }
  #trader-stats b { display: block; font-size: 16px; color: #f0f0f0; margin-top: 2px; }
  #trader-stats b.neg { color: #ef476f; }
  #trader-stats b.pos { color: #06d6a0; }
  .pos-row {
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
    gap: 6px; padding: 8px 0; border-bottom: 1px solid #1c1c1f; font-size: 12px;
  }
  .pos-row .sym { color: #ff8c42; font-weight: bold; }
  .pos-empty { color: #575757; font-style: italic; font-size: 12px; }
  .trader-section-title {
    font-size: 11px; color: #575757; letter-spacing: 1px; margin: 14px 0 6px;
  }
  .mini-btn {
    padding: 4px 10px; font-size: 10px; font-weight: bold; border-radius: 4px;
    background: #000000; font-family: inherit;
  }
  .mini-btn.buy { color: #06d6a0; border: 1px solid #b35a1f; }
  .mini-btn.sell { color: #ef476f; border: 1px solid #b35a1f; }
  .mini-btn.tp { color: #e9c46a; border: 1px solid #b35a1f; }
  .mini-btn.remove { color: #575757; border: 1px solid #b35a1f; }
  .pct-picker { display: none; gap: 6px; width: 100%; margin-top: 6px; }
  .pct-picker.show { display: flex; }
  .pct-picker button { flex: 1; padding: 6px 0; font-size: 10px; }
</style></head>
<body>
  <header>
    <span>◈ OMNI-OS REMOTE</span>
    <span style="display:flex;align-items:center">
      <span id="status">connecting…</span>
      <button id="trader-btn" type="button">◈ TRADER</button>
    </span>
  </header>
  <div id="trader-panel">
    <div id="trader-stats"></div>
    <div class="trader-section-title">OPEN POSITIONS</div>
    <div id="trader-positions"></div>
    <div class="trader-section-title">WATCHLIST</div>
    <div id="trader-watchlist"></div>
    <div class="trader-section-title">SUGGESTIONS</div>
    <div id="trader-suggestions"></div>
  </div>
  <div id="log"></div>
  <div id="voicebar">
    <button id="mic-btn" type="button">🎤</button>
    <span id="voice-status">tap to enable voice, tap again to talk</span>
  </div>
  <form id="f"><input id="t" autocomplete="off" placeholder="Message Omni…"><button>SEND</button></form>
<script>
  var TRADER_ENABLED = __TRADER_ENABLED__;
  var token = sessionStorage.getItem('seraph_token');
  if (!token) { location.replace('/login'); }
  var logEl = document.getElementById('log');
  var statusEl = document.getElementById('status');
  var voiceStatusEl = document.getElementById('voice-status');
  var micBtn = document.getElementById('mic-btn');

  function addLine(cls, text) {
    var d = document.createElement('div');
    d.className = cls;
    d.textContent = text;
    logEl.appendChild(d);
    maybeOfferForceSell(text);
    logEl.scrollTop = logEl.scrollHeight;
  }

  // A blocked live sell (net-profit check, or a Seraph gate refusal)
  // already spells out the "sell SYM force" escape hatch in its own error
  // text (see live.py) — mirrors the desktop panel's one-click force-sell
  // button instead of making the user type the command.
  var FORCE_SELL_HINT_RE = /sell\\s+(\\S+)\\s+force/i;
  function maybeOfferForceSell(text) {
    var m = FORCE_SELL_HINT_RE.exec(text || '');
    if (!m) return;
    var symbol = m[1].toUpperCase();
    var row = document.createElement('div');
    row.className = 'force-sell-row';
    var label = document.createElement('span');
    label.className = 'sys';
    label.textContent = 'Sell ' + symbol + ' anyway, skipping the safety checks?';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'force-sell-btn';
    btn.textContent = '⚠ FORCE SELL ' + symbol;
    btn.addEventListener('click', function() {
      runTraderAction('sell', { symbol: symbol, force: true });
    });
    row.appendChild(label);
    row.appendChild(btn);
    logEl.appendChild(row);
  }

  function addImage(dataUrl) {
    var img = document.createElement('img');
    img.src = dataUrl;
    img.alt = 'Generated image';
    logEl.appendChild(img);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function addLink(url, label) {
    var a = document.createElement('a');
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener';
    a.className = 'file-link';
    a.textContent = '📄 ' + (label || 'Open file');
    logEl.appendChild(a);
    logEl.scrollTop = logEl.scrollHeight;
  }

  var proto = location.protocol === 'https:' ? 'wss' : 'ws';
  var ws = new WebSocket(proto + '://' + location.host + '/ws?token=' + encodeURIComponent(token));
  ws.onopen = function() { statusEl.textContent = 'live'; statusEl.className = 'live'; };
  ws.onclose = function() { statusEl.textContent = 'disconnected'; statusEl.className = ''; };
  ws.onmessage = function(ev) {
    var msg = JSON.parse(ev.data);
    if (msg.type === 'you') addLine('you', 'You: ' + msg.text);
    else if (msg.type === 'seraph') addLine('seraph', 'Omni: ' + msg.text);
    else if (msg.type === 'sys') addLine('sys', msg.text);
    else if (msg.type === 'image') addImage(msg.data);
    else if (msg.type === 'link') addLink(msg.url, msg.label);
  };

  document.getElementById('f').addEventListener('submit', function(e) {
    e.preventDefault();
    var input = document.getElementById('t');
    var text = input.value.trim();
    if (!text) return;
    addLine('you', 'You: ' + text);
    // While the TRADER view is open, the command bar talks directly to the
    // trader's own command parser (buy/sell/watch/take profit/etc — see
    // TraderEngine.command) instead of Seraph's general chat, which can
    // never place a trade by design (voice or text).
    if (traderPanel.classList.contains('show')) {
      runTraderAction('command', { text: text });
      input.value = '';
      return;
    }
    fetch('/api/command', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token},
      body: JSON.stringify({text: text}),
    }).then(function(r) {
      if (r.status === 401) { sessionStorage.removeItem('seraph_token'); location.replace('/login'); }
    });
    input.value = '';
  });

  // ── Two-way voice ──────────────────────────────────────────────────────
  // One persistent binary socket: mic frames go out while recording is on,
  // Seraph's speech frames arrive continuously and get queued for gapless
  // playback regardless of mic state.
  var audioWs = null, audioReady = false;
  var playCtx = null, nextPlayTime = 0;
  var micCtx = null, micStream = null, micNode = null, micGain = null;
  var recording = false;

  function ensureAudioWs() {
    if (audioWs && (audioWs.readyState === WebSocket.OPEN || audioWs.readyState === WebSocket.CONNECTING)) return;
    audioWs = new WebSocket(proto + '://' + location.host + '/ws/audio?token=' + encodeURIComponent(token));
    audioWs.binaryType = 'arraybuffer';
    audioWs.onopen  = function() { audioReady = true; voiceStatusEl.textContent = 'voice ready — tap mic to talk'; };
    audioWs.onclose = function() { audioReady = false; voiceStatusEl.textContent = 'voice disconnected'; };
    audioWs.onmessage = function(ev) { playChunk(ev.data); };
  }

  function playChunk(arrayBuffer) {
    if (!playCtx) {
      playCtx = new (window.AudioContext || window.webkitAudioContext)();
      nextPlayTime = playCtx.currentTime;
    }
    var int16 = new Int16Array(arrayBuffer);
    var float32 = new Float32Array(int16.length);
    for (var i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
    // Server sends 24kHz mono PCM16 regardless of this context's native rate —
    // build the buffer at 24000 and let the AudioContext resample on playback.
    var buf = playCtx.createBuffer(1, float32.length, 24000);
    buf.copyToChannel(float32, 0);
    var src = playCtx.createBufferSource();
    src.buffer = buf;
    src.connect(playCtx.destination);
    var startAt = Math.max(playCtx.currentTime, nextPlayTime);
    src.start(startAt);
    nextPlayTime = startAt + buf.duration;
  }

  function resampleTo16k(float32, inRate) {
    if (inRate === 16000) return float32;
    var ratio = inRate / 16000;
    var outLen = Math.floor(float32.length / ratio);
    var out = new Float32Array(outLen);
    for (var i = 0; i < outLen; i++) out[i] = float32[Math.floor(i * ratio)];
    return out;
  }

  async function startMic() {
    ensureAudioWs();
    if (!playCtx) { playCtx = new (window.AudioContext || window.webkitAudioContext)(); nextPlayTime = playCtx.currentTime; }
    if (playCtx.state === 'suspended') { try { await playCtx.resume(); } catch (_) {} }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      voiceStatusEl.textContent = 'mic not supported on this browser';
      return;
    }
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
      });
    } catch (e) {
      voiceStatusEl.textContent = e.name === 'NotAllowedError'
        ? 'microphone denied — allow it in browser settings' : 'mic error: ' + e.message;
      return;
    }

    try { micCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 }); }
    catch (_) { micCtx = new (window.AudioContext || window.webkitAudioContext)(); }
    var inRate = micCtx.sampleRate;

    var src = micCtx.createMediaStreamSource(micStream);
    micNode = micCtx.createScriptProcessor(2048, 1, 1);
    micNode.onaudioprocess = function(e) {
      if (!recording) return;
      var input = e.inputBuffer.getChannelData(0);
      var down = resampleTo16k(input, inRate);
      var int16 = new Int16Array(down.length);
      for (var i = 0; i < down.length; i++) {
        var s = Math.max(-1, Math.min(1, down[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      if (audioWs && audioWs.readyState === WebSocket.OPEN) audioWs.send(int16.buffer);
    };
    // Route through a silent gain node rather than straight to destination —
    // keeps the processing graph alive without echoing the mic back out loud.
    micGain = micCtx.createGain();
    micGain.gain.value = 0;
    src.connect(micNode);
    micNode.connect(micGain);
    micGain.connect(micCtx.destination);

    recording = true;
    micBtn.classList.add('recording');
    micBtn.textContent = '⏹';
    voiceStatusEl.textContent = 'listening… tap to stop';
  }

  function stopMic() {
    recording = false;
    if (micNode) { try { micNode.disconnect(); } catch (_) {} micNode = null; }
    if (micGain) { try { micGain.disconnect(); } catch (_) {} micGain = null; }
    if (micCtx)  { try { micCtx.close(); } catch (_) {} micCtx = null; }
    if (micStream) { micStream.getTracks().forEach(function(t) { t.stop(); }); micStream = null; }
    micBtn.classList.remove('recording');
    micBtn.textContent = '🎤';
    voiceStatusEl.textContent = 'voice ready — tap mic to talk';
  }

  micBtn.addEventListener('click', function() {
    if (recording) stopMic(); else startMic();
  });

  // ── Trader view (read-only — phone can never place a trade) ────────────
  var traderBtn = document.getElementById('trader-btn');
  if (!TRADER_ENABLED) { traderBtn.style.display = 'none'; }
  var traderPanel = document.getElementById('trader-panel');
  var traderStatsEl = document.getElementById('trader-stats');
  var traderPosEl = document.getElementById('trader-positions');
  var traderWatchEl = document.getElementById('trader-watchlist');
  var traderSuggEl = document.getElementById('trader-suggestions');
  var traderTimer = null;
  var traderBusy = false;

  function fmtUsd(n) {
    if (n === null || n === undefined) return '—';
    return '$' + Number(n).toFixed(2);
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function renderTrader(d) {
    if (!d.open) {
      traderStatsEl.innerHTML = '<div style="grid-column:1/-1" class="pos-empty">Trader hasn\\'t been opened on the PC yet.</div>';
      traderPosEl.innerHTML = '';
      traderWatchEl.innerHTML = '';
      traderSuggEl.innerHTML = '';
      return;
    }
    var pnl = d.realizedPnlUsd || 0;
    traderStatsEl.innerHTML =
      '<div>MODE<b>' + (d.mode || '—').toUpperCase() + (d.running ? ' · RUNNING' : ' · STOPPED') + '</b></div>' +
      '<div>EQUITY<b>' + fmtUsd(d.equityUsd) + '</b></div>' +
      '<div>REALIZED P&L<b class="' + (pnl < 0 ? 'neg' : pnl > 0 ? 'pos' : '') + '">' + fmtUsd(pnl) + '</b></div>' +
      '<div>TRADES TODAY<b>' + (d.tradesToday != null ? d.tradesToday : '—') + '</b></div>';

    var positions = d.positions || [];
    traderPosEl.innerHTML = !positions.length ? '<div class="pos-empty">No open positions.</div>' :
      positions.map(function(p) {
        var sym = esc(p.symbol);
        return '<div class="pos-row">' +
          '<span class="sym">' + sym + '</span>' +
          '<span>qty ' + Number(p.qty).toPrecision(6) + ' · entry $' + Number(p.entryPriceUsd).toPrecision(6) + '</span>' +
          '<button class="mini-btn tp" data-toggle-pct="tp-' + sym + '">TAKE PROFIT</button>' +
          '<button class="mini-btn sell" data-toggle-pct="sell-' + sym + '">SELL</button>' +
          '<div class="pct-picker" id="tp-' + sym + '">' +
            [10, 20, 25, 50].map(function(pct) {
              return '<button class="mini-btn tp" data-action="take_profit" data-symbol="' + sym + '" data-pct="' + pct + '">' + pct + '%</button>';
            }).join('') +
          '</div>' +
          '<div class="pct-picker" id="sell-' + sym + '">' +
            [25, 50, 75, 100].map(function(pct) {
              return '<button class="mini-btn sell" data-action="sell" data-symbol="' + sym + '" data-pct="' + pct + '">' + pct + '%</button>';
            }).join('') +
          '</div>' +
        '</div>';
      }).join('');

    var watchlist = d.watchlist || [];
    traderWatchEl.innerHTML = !watchlist.length ? '<div class="pos-empty">Watchlist empty.</div>' :
      watchlist.map(function(w) {
        var addr = w.address || '';
        var short = addr ? addr.slice(0, 6) + '…' + addr.slice(-4) : '?';
        var entry = w.symbol + ':' + (w.chain || 'ethereum') + ':' + addr;
        return '<div class="pos-row">' +
          '<span class="sym">' + esc(w.symbol) + '</span>' +
          '<span>' + esc(w.chain || 'ethereum') + ' · ' + esc(short) + '</span>' +
          '<button class="mini-btn buy" data-action="buy" data-entry="' + esc(entry) + '">BUY</button>' +
          '<button class="mini-btn remove" data-action="remove_watch" data-symbol="' + esc(w.symbol) + '">REMOVE</button>' +
        '</div>';
      }).join('');

    var suggestions = d.suggestions || [];
    traderSuggEl.innerHTML = !suggestions.length ? '<div class="pos-empty">No suggestions right now — try again shortly.</div>' :
      suggestions.map(function(t) {
        var addr = t.address || '';
        var short = addr ? addr.slice(0, 6) + '…' + addr.slice(-4) : '?';
        var entry = t.symbol + ':' + (t.chain || 'ethereum') + ':' + addr;
        var chg = t.chg1h != null ? (t.chg1h >= 0 ? '+' : '') + Number(t.chg1h).toFixed(1) + '%/1h' : '';
        return '<div class="pos-row">' +
          '<span class="sym">' + esc(t.symbol) + '</span>' +
          '<span>' + esc(t.chain || 'ethereum') + ' · ' + esc(short) + (chg ? ' · ' + esc(chg) : '') + '</span>' +
          '<button class="mini-btn buy" data-action="add_watch" data-entry="' + esc(entry) + '">+WATCH</button>' +
        '</div>';
      }).join('');
  }

  function fetchWithTimeout(url, opts, timeoutMs) {
    var ctrl = new AbortController();
    var timer = setTimeout(function() { ctrl.abort(); }, timeoutMs || 20000);
    return fetch(url, Object.assign({}, opts, { signal: ctrl.signal }))
      .finally(function() { clearTimeout(timer); });
  }

  function fetchTraderState() {
    fetchWithTimeout('/api/trader/state', { headers: { 'Authorization': 'Bearer ' + token } }, 10000)
      .then(function(r) {
        if (r.status === 401) { sessionStorage.removeItem('seraph_token'); location.replace('/login'); return null; }
        return r.json();
      })
      .then(function(d) { if (d) renderTrader(d); })
      .catch(function(err) { console.error('fetchTraderState failed', err); });
  }

  // Guards against a stuck traderBusy lock: any code path that sets it true
  // is guaranteed to hit finishAction() below, which always clears it.
  //
  // Real trades don't fit in one request/response: a sell needing a fresh
  // token approval waits on TWO sequential on-chain confirmations (approve,
  // then swap), each up to 180s — worst case ~6 minutes, well past any
  // sane HTTP timeout. So /api/trader/action just starts the job and
  // returns a jobId immediately; this polls /api/trader/action/result for
  // it, with a status line every 20s so a long wait doesn't look frozen.
  var ACTION_MAX_WAIT_MS = 8 * 60 * 1000;
  var ACTION_POLL_MS = 3000;
  var ACTION_PROGRESS_EVERY_MS = 20000;

  function runTraderAction(action, payload) {
    if (traderBusy) { console.warn('trader action ignored — previous one still in flight'); return; }
    traderBusy = true;
    if (action !== 'command') {
      addLine('sys', '◈ ' + action + (payload.symbol ? ' ' + payload.symbol : '') + (payload.pct ? ' ' + payload.pct + '%' : '') + '…');
    }

    function finishAction() { traderBusy = false; }

    fetchWithTimeout('/api/trader/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
      body: JSON.stringify(Object.assign({ action: action }, payload)),
    }, 15000)
      .then(function(r) {
        if (r.status === 401) { sessionStorage.removeItem('seraph_token'); location.replace('/login'); return null; }
        return r.json().then(function(d) { return { status: r.status, body: d }; });
      })
      .then(function(res) {
        if (!res) { finishAction(); return; }
        if (res.status >= 400 || !res.body || !res.body.jobId) {
          addLine('sys', 'SYS: trader action error (' + res.status + '): ' + (res.body && res.body.message ? res.body.message : JSON.stringify(res.body)));
          finishAction();
          return;
        }
        pollActionResult(res.body.jobId, Date.now(), Date.now(), finishAction);
      })
      .catch(function(err) {
        console.error('trader action failed to start', action, payload, err);
        addLine('sys', 'SYS: request failed — ' + (err && err.name === 'AbortError' ? 'timed out' : (err && err.message) || 'network error'));
        finishAction();
      });
  }

  function pollActionResult(jobId, startedAt, lastProgressAt, finishAction) {
    var elapsed = Date.now() - startedAt;
    if (elapsed > ACTION_MAX_WAIT_MS) {
      addLine('sys', 'SYS: still waiting on the trader after ' + Math.round(elapsed / 1000) + 's — check positions on the desktop app; this may still complete server-side.');
      finishAction();
      return;
    }
    fetchWithTimeout('/api/trader/action/result?jobId=' + encodeURIComponent(jobId), {
      headers: { 'Authorization': 'Bearer ' + token },
    }, 10000)
      .then(function(r) {
        if (r.status === 401) { sessionStorage.removeItem('seraph_token'); location.replace('/login'); return null; }
        return r.json();
      })
      .then(function(d) {
        if (!d) { finishAction(); return; }
        if (d.status === 'done') {
          var res = d.result || {};
          addLine(res.ok ? 'seraph' : 'sys', (res.ok ? 'OK: ' : 'SYS: ') + (res.message || ''));
          fetchTraderState();
          finishAction();
          return;
        }
        var now = Date.now();
        if (now - lastProgressAt >= ACTION_PROGRESS_EVERY_MS) {
          addLine('sys', 'SYS: still working… (' + Math.round((now - startedAt) / 1000) + 's)');
          lastProgressAt = now;
        }
        setTimeout(function() { pollActionResult(jobId, startedAt, lastProgressAt, finishAction); }, ACTION_POLL_MS);
      })
      .catch(function(err) {
        console.error('poll failed, retrying', err);
        setTimeout(function() { pollActionResult(jobId, startedAt, lastProgressAt, finishAction); }, ACTION_POLL_MS);
      });
  }

  [traderPosEl, traderWatchEl, traderSuggEl].forEach(function(el) {
    el.addEventListener('click', function(ev) {
      var toggleId = ev.target.getAttribute('data-toggle-pct');
      if (toggleId) {
        var picker = document.getElementById(toggleId);
        var wasOpen = picker.classList.contains('show');
        el.querySelectorAll('.pct-picker.show').forEach(function(p) { p.classList.remove('show'); });
        if (!wasOpen) picker.classList.add('show');
        return;
      }
      var action = ev.target.getAttribute('data-action');
      if (!action) return;
      var payload = {};
      if (ev.target.hasAttribute('data-symbol')) payload.symbol = ev.target.getAttribute('data-symbol');
      if (ev.target.hasAttribute('data-entry')) payload.entry = ev.target.getAttribute('data-entry');
      if (ev.target.hasAttribute('data-pct')) payload.pct = Number(ev.target.getAttribute('data-pct'));
      runTraderAction(action, payload);
    });
  });

  traderBtn.addEventListener('click', function() {
    var show = !traderPanel.classList.contains('show');
    traderPanel.classList.toggle('show', show);
    traderBtn.classList.toggle('open', show);
    document.getElementById('t').placeholder = show
      ? 'Trader command… (buy SYM:0xADDR, sell SYM, help)' : 'Message Omni…';
    if (show) {
      fetchTraderState();
      traderTimer = setInterval(fetchTraderState, 4000);
    } else if (traderTimer) {
      clearInterval(traderTimer);
      traderTimer = null;
    }
  });
</script>
</body></html>"""

_LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Omni-OS Remote — Connect</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; background: #000000; color: #f0f0f0; font-family: "Segoe UI", sans-serif;
    display: flex; align-items: center; justify-content: center; height: 100vh; text-align: center;
  }
  h2 { color: #ff8c42; letter-spacing: 2px; }
  input {
    background: #000000; color: #f0f0f0; border: 1px solid #2a2a2e; border-radius: 6px;
    padding: 12px; font-size: 22px; letter-spacing: 6px; text-align: center;
    text-transform: uppercase; width: 200px; font-family: inherit;
  }
  button {
    display: block; margin: 14px auto 0; background: #1a0f08; color: #ff8c42;
    border: 1px solid #b35a1f; border-radius: 6px; padding: 10px 24px;
    font-weight: bold; font-family: inherit;
  }
  #err { color: #ef476f; font-size: 13px; margin-top: 10px; min-height: 16px; }
</style></head>
<body>
  <div>
    <h2>◈ OMNI-OS REMOTE</h2>
    <p style="color:#575757;font-size:13px">Enter the 6-character key shown on Omni-OS's screen</p>
    <input id="pin" maxlength="6" autocomplete="off" autocapitalize="characters">
    <button id="go">CONNECT</button>
    <div id="err"></div>
  </div>
<script>
  document.getElementById('go').onclick = function() {
    var pin = document.getElementById('pin').value.trim().toUpperCase();
    fetch('/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pin: pin}),
    }).then(function(r) { return r.json().then(function(d) { return {ok: r.ok, d: d}; }); })
      .then(function(res) {
        if (res.ok && res.d.ok) {
          sessionStorage.setItem('seraph_token', res.d.token);
          location.replace('/');
        } else {
          document.getElementById('err').textContent = res.d.error || 'Invalid or expired key';
        }
      });
  };
</script>
</body></html>"""


class DashboardServer:

    def __init__(self):
        self._ip = _local_ip()
        self._tokens: set[str] = set()
        self._clients: set["WebSocket"] = set()
        self._history: list[dict] = []
        self._pending_keys: dict[str, float] = {}
        self._command_queue = asyncio.Queue()
        self._connect_callback = None
        self._trader_state_callback = None
        self._trader_action_callback = None
        # Trade-executing actions (buy/sell/take-profit/unwrap, or a typed
        # command that resolves to one) can legitimately take several
        # minutes — a sell needing a fresh token approval waits on TWO
        # sequential on-chain confirmations (approve, then swap), each up
        # to 180s. No single HTTP request should be held open that long, so
        # /api/trader/action starts the work in the background and returns
        # a job id immediately; the phone polls /api/trader/action/result.
        self._trader_jobs: dict[str, dict] = {}
        # Two-way phone audio: mic chunks in (16kHz PCM16, phone -> Seraph),
        # and connected sockets to fan Seraph's speech out to (24kHz PCM16).
        self._phone_audio_in_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._audio_clients: set["WebSocket"] = set()
        self._use_ssl = False  # flipped True in serve() once a cert is ready
        self.running = False  # True once the HTTP(S) listener is actually up
        self.start_error: str | None = None  # set if serve() fails to start
        self._error_callback = None
        # Files Seraph has created that the phone can fetch by an opaque id —
        # avoids exposing raw filesystem paths; only explicitly-shared files
        # are servable, not arbitrary paths off a URL parameter.
        self._shared_files: dict[str, Path] = {}

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    def set_error_callback(self, fn) -> None:
        """fn(message: str) -> None, called if the dashboard fails to start
        (most commonly: the port is already held by another running copy of
        Omni-OS). Without this, a bind failure inside serve()'s
        fire-and-forget asyncio task fails completely silently — the app
        looks fine, Remote Control still shows a URL/QR code, and nothing
        ever explains to the user why their phone can't connect."""
        self._error_callback = fn

    def set_trader_state_callback(self, fn) -> None:
        """fn() -> dict | None, called synchronously from the request handler
        (see JarvisUI.get_trader_state) to read the trader engine's current
        balance/equity/positions/watchlist."""
        self._trader_state_callback = fn

    def set_trader_action_callback(self, fn) -> None:
        """fn(action: str, payload: dict) -> dict — executes a real trader
        action (buy/sell/take-profit/remove-watch) via JarvisUI.run_trader_action.
        Runs on a worker thread (see the /api/trader/action route) since it
        can reach live network calls; never call it directly from the
        asyncio event loop."""
        self._trader_action_callback = fn

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    def get_url(self) -> str:
        proto = "https" if self._use_ssl else "http"
        return f"{proto}://{self._ip}:{PORT}"

    def register_file(self, path) -> str | None:
        """Make a local file fetchable by the phone (or the PC's own browser)
        at a random, unguessable URL — file:// links mean nothing on a phone,
        this gives Seraph a real clickable one instead. Returns None if the
        path doesn't exist. Deliberately not gated behind a session token:
        the 128 bits of randomness in the id IS the access control, and
        gating it would break the link whenever no phone has paired yet."""
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None
        file_id = secrets.token_urlsafe(16)
        self._shared_files[file_id] = p
        return f"{self.get_url()}/f/{file_id}"

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 100:
            self._history = self._history[-100:]
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def broadcast_audio(self, chunk: bytes) -> None:
        """Fan a raw PCM16 chunk of Seraph's speech out to every connected phone."""
        if not self._audio_clients:
            return
        dead = set()
        for ws in list(self._audio_clients):
            try:
                await ws.send_bytes(chunk)
            except Exception:
                dead.add(ws)
        self._audio_clients -= dead

    def _build_app(self):
        app = FastAPI(docs_url=None, redoc_url=None)

        def _auth(req: "Request") -> bool:
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(_LOGIN_HTML)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            from core import settings_store
            trader_enabled = settings_store.load_settings()["trader"]["enabled"]
            html = _APP_HTML.replace("__TRADER_ENABLED__", "true" if trader_enabled else "false")
            return HTMLResponse(html)

        # Static background — the Omni-OS logo mark, not gated behind auth
        # (it's just a decorative image, no assistant data).
        @app.get("/static/avatar_bg.jpg")
        async def avatar_bg():
            if AVATAR_BG_FILE.exists():
                return FileResponse(str(AVATAR_BG_FILE), media_type="image/jpeg")
            return JSONResponse({"error": "not found"}, status_code=404)

        @app.post("/login")
        async def login(req: "Request"):
            body = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]  # one-time use
                tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                if self._connect_callback:
                    self._connect_callback()
                await self.broadcast({"type": "sys", "text": "Remote connection established."})
                return JSONResponse({"ok": True, "token": tok})
            return JSONResponse({"ok": False, "error": "Invalid or expired key"}, status_code=401)

        @app.get("/auto-login", response_class=HTMLResponse)
        async def auto_login(key: str = ""):
            """QR code target — validates the one-time key, mints a token, redirects the phone."""
            now = time.time()
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                return HTMLResponse(
                    "<body style='background:#000000;color:#f0f0f0;font-family:sans-serif;"
                    "display:flex;align-items:center;justify-content:center;height:100vh;"
                    "margin:0;text-align:center'><div><h2 style='color:#ef476f'>Link Expired</h2>"
                    "<p style='color:#575757'>Press Remote Control on Omni-OS for a new QR code.</p>"
                    "</div></body>"
                )
            del self._pending_keys[key]
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            if self._connect_callback:
                self._connect_callback()
            await self.broadcast({"type": "sys", "text": "Remote connection established via QR code."})
            return HTMLResponse(
                "<body style='background:#000000;color:#f0f0f0;font-family:sans-serif;"
                "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
                f"<script>sessionStorage.setItem('seraph_token','{tok}');"
                "setTimeout(function(){location.replace('/')}, 300);</script>"
                "<p>Connecting to Omni-OS…</p></body>"
            )

        @app.get("/f/{file_id}")
        async def get_shared_file(file_id: str):
            path = self._shared_files.get(file_id)
            if not path or not path.exists():
                return JSONResponse({"error": "Not found or expired"}, status_code=404)
            return FileResponse(str(path))  # no `filename=` — view inline, don't force-download

        @app.post("/api/command")
        async def command(req: "Request"):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            text = (body.get("text") or "").strip()
            if text:
                await self._command_queue.put(text)
            return JSONResponse({"ok": True})

        @app.get("/api/trader/state")
        async def trader_state(req: "Request"):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if not self._trader_state_callback:
                return JSONResponse({"open": False})
            try:
                # get_trader_state() can call trending_suggestions(), which
                # does real blocking network calls (trending.discover()) on
                # a cache miss — this is polled every few seconds by the
                # phone, so it must never run directly on the shared asyncio
                # loop (that would freeze voice, websockets, and every other
                # dashboard request for as long as the network call takes —
                # a real bug hit in testing: the whole app went unresponsive
                # under this exact poll).
                state = await asyncio.to_thread(self._trader_state_callback)
            except Exception as err:
                return JSONResponse({"open": False, "error": str(err)})
            if state is None:
                return JSONResponse({"open": False})
            return JSONResponse({"open": True, **state})

        @app.post("/api/trader/action")
        async def trader_action(req: "Request"):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if not self._trader_action_callback:
                return JSONResponse({"ok": False, "message": "trader not available"}, status_code=503)
            body = await req.json()
            action = (body.get("action") or "").strip()
            payload = {k: v for k, v in body.items() if k != "action"}
            if not action:
                return JSONResponse({"ok": False, "message": "missing action"}, status_code=400)

            job_id = secrets.token_urlsafe(12)
            self._trader_jobs[job_id] = {"status": "pending", "result": None}

            def _run():
                try:
                    result = self._trader_action_callback(action, payload)
                except Exception as err:
                    result = {"ok": False, "message": str(err)}
                self._trader_jobs[job_id] = {"status": "done", "result": result}

            # Fire-and-forget on a plain thread — NOT asyncio.to_thread,
            # which this coroutine would have to await (defeating the
            # purpose: the whole point is returning before the job finishes).
            threading.Thread(target=_run, daemon=True).start()
            return JSONResponse({"pending": True, "jobId": job_id})

        @app.get("/api/trader/action/result")
        async def trader_action_result(req: "Request", jobId: str = ""):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            job = self._trader_jobs.get(jobId)
            if not job:
                return JSONResponse({"status": "unknown"}, status_code=404)
            if job["status"] == "done":
                # One-shot: free the slot once the phone has collected it —
                # jobs are cheap but this is a plain dict with no eviction,
                # no reason to let it grow across a long session.
                del self._trader_jobs[jobId]
                return JSONResponse({"status": "done", "result": job["result"]})
            return JSONResponse({"status": "pending"})

        @app.websocket("/ws")
        async def ws_ep(websocket: "WebSocket", token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "command":
                        t = (data.get("text") or "").strip()
                        if t:
                            await self._command_queue.put(t)
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)

        # ── Two-way voice ────────────────────────────────────────────────
        # One persistent binary socket per phone: client sends 16kHz mono
        # PCM16 mic frames while tap-to-talk is held; server pushes 24kHz
        # mono PCM16 frames of Seraph's speech to it independently (via
        # broadcast_audio, called from main.py's playback loop) — the two
        # directions are unrelated in timing, this is a real duplex stream.
        @app.websocket("/ws/audio")
        async def audio_ws(websocket: "WebSocket", token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._audio_clients.add(websocket)
            await self.broadcast({"type": "sys", "text": "Phone audio connected."})
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_in_queue.put_nowait(data)
                    except asyncio.QueueFull:
                        pass  # drop frame rather than let a stall build unbounded latency
            except WebSocketDisconnect:
                pass
            finally:
                self._audio_clients.discard(websocket)
                await self.broadcast({"type": "sys", "text": "Phone audio disconnected."})

        return app

    def _fail(self, message: str) -> None:
        self.running = False
        self.start_error = message
        print(f"[Dashboard] ERROR: {message}")
        if self._error_callback:
            try:
                self._error_callback(message)
            except Exception:
                pass

    async def serve(self) -> None:
        if not _DEPS_OK:
            self._fail('fastapi/uvicorn not installed — run: pip install fastapi "uvicorn[standard]"')
            return

        app = self._build_app()
        cert = _ensure_self_signed_cert(self._ip)
        ssl_kwargs = {}
        if cert:
            self._use_ssl = True
            ssl_kwargs = {"ssl_certfile": str(cert[0]), "ssl_keyfile": str(cert[1])}
        else:
            print("[Dashboard] `cryptography` not installed — serving plain HTTP.")
            print("[Dashboard] Phone mic won't work over HTTP (mobile browsers require HTTPS "
                  "for microphone access); everything else will. Run: pip install cryptography")

        # uvicorn.Server.startup() swallows bind errors internally (logs them
        # via its own logger, which the GUI never shows) and converts them to
        # a bare sys.exit(3) with no error text attached — by the time that
        # reaches us below there's no way to recover *why* it failed. Doing
        # our own bind attempt first gets the real OSError (e.g. "[WinError
        # 10048] ... normally permitted" for a port already in use) while
        # it's still available, so the failure the user sees is specific
        # instead of a silent no-op.
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("0.0.0.0", PORT))
            probe.close()
        except OSError as e:
            self._fail(
                f"couldn't bind port {PORT} ({e.strerror or e}) — is another copy of "
                "Omni-OS already running? Remote Control won't work until this is freed."
            )
            return

        cfg = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning", **ssl_kwargs)
        print(f"[Dashboard] {self.get_url()}")
        print("[Dashboard] Press 'Remote Control' in the Omni-OS UI to get a pairing QR code.")
        if self._use_ssl:
            print("[Dashboard] First connection will show a certificate warning (self-signed) — "
                  "tap 'proceed anyway' / 'visit site' once, it won't ask again.")
        print("[Dashboard] Phone must be on the same Wi-Fi/LAN. If it can't connect, allow "
              "Python through Windows Firewall on your Private network.")
        try:
            self.running = True
            await uvicorn.Server(cfg).serve()
        except asyncio.CancelledError:
            raise  # normal shutdown path — not a failure, must propagate
        except BaseException as e:
            # Catches SystemExit too (uvicorn's own startup-failure path,
            # e.g. sys.exit(3) on a bind error) — deliberately NOT re-raised:
            # asyncio.Task specially re-raises SystemExit/KeyboardInterrupt
            # instead of absorbing them like other exceptions, which would
            # blow up the whole shared event loop (voice, Claude sub-agents,
            # everything) over what should be an isolated dashboard failure.
            # The pre-flight probe above handles the common case (port
            # already in use) with a specific message; this is the fallback
            # for anything else (cert load failure, permission error, the
            # port getting grabbed in the narrow race between the probe and
            # this call, ...).
            self._fail(f"failed to start ({e})")
        finally:
            self.running = False
