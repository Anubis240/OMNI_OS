"""The two pages the phone loads: the paired app and the key-entry login.

APP_HTML contains __TRADER_ENABLED__, replaced per request with true/false.
"""

APP_HTML = """<!DOCTYPE html>
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
  // Opened once at page load and, unlike /ws/audio (re-opened fresh every
  // time the mic is tapped, so it self-heals), had no reconnect logic at
  // all — an idle timeout or a transient mobile-network drop silently
  // killed the ONLY channel Omni's replies are broadcast through, with no
  // visible sign anything had failed (the phone just went quiet forever).
  // Retry with backoff instead of giving up after the first drop.
  var ws = null, wsRetryMs = 1000;
  var wsConnectedAt = 0;
  function connectWs() {
    ws = new WebSocket(proto + '://' + location.host + '/ws?token=' + encodeURIComponent(token));
    ws.onopen = function() {
      // The server always replays its last 50 history entries fresh on
      // every /ws accept (so a phone reconnecting mid-session sees prior
      // context) — harmless when a connection barely ever reconnected, but
      // now that this reconnects automatically (below), leaving the old
      // DOM in place meant every reconnect (e.g. the phone's own screen
      // timeout, ~every 15s) re-appended the same history on top of what
      // was already shown — confirmed in testing as 5+ stacked copies of
      // one exchange. Clear first so the replay rebuilds cleanly instead.
      logEl.innerHTML = '';
      statusEl.textContent = 'live'; statusEl.className = 'live';
      wsConnectedAt = Date.now();
    };
    ws.onclose = function() {
      statusEl.textContent = 'reconnecting…'; statusEl.className = '';
      // Only treat this as a healthy connection (reset backoff to the
      // floor) if it actually held for a while first. Resetting on every
      // successful open — even a fleeting one — meant a flapping network
      // could keep reconnecting at the 1s floor forever, never backing
      // off: confirmed in testing as a burst of 6 identical reconnect log
      // lines, twice, bracketing a duplicated-message render on the
      // client. A connection that dies within 5s doesn't get treated as
      // "recovered".
      var wasStable = wsConnectedAt && (Date.now() - wsConnectedAt) >= 5000;
      wsRetryMs = wasStable ? 1000 : Math.min(wsRetryMs * 2, 15000);
      setTimeout(connectWs, wsRetryMs);
    };
    ws.onerror = function() { ws.close(); };
    ws.onmessage = function(ev) {
      var msg = JSON.parse(ev.data);
      // Server-initiated liveness check (see _WS_PING_INTERVAL/_WS_PONG_TIMEOUT
      // in dashboard/server.py) — echo it straight back, nothing to render.
      if (msg.type === 'ping') { try { ws.send(JSON.stringify({type: 'pong'})); } catch (_) {} return; }
      if (msg.type === 'you') addLine('you', 'You: ' + msg.text);
      else if (msg.type === 'seraph') addLine('seraph', 'Omni: ' + msg.text);
      else if (msg.type === 'sys') addLine('sys', msg.text);
      else if (msg.type === 'image') addImage(msg.data);
      else if (msg.type === 'link') addLink(msg.url, msg.label);
    };
  }
  connectWs();

  // 2026-09-15 report (GEMZ4US finding #38): while the combined Trader+Chat
  // view was open, EVERY message routed to the trader parser regardless of
  // content — a plain question came back "unrecognized command", never
  // reaching Omni at all, despite the view showing the chat log right next
  // to the trader state (the "split screen" implied both were usable at
  // once). Mirrors TraderEngine.command()'s own recognized verbs (trader/
  // engine.py) — the same vocabulary already shown in this bar's own
  // placeholder text — so only text actually shaped like a trader command
  // takes that path; anything else reaches Omni normally, trader view open
  // or not.
  var TRADER_COMMAND_RE = /^\/?(help|scan|sync(\s+positions)?|resume|clear halt|unhalt|unwrap(\s+\S+)?|(sell|close)\s+all|(sell|close)\s+\S+|hold\s+\S+|unhold\s+\S+|take[\s-]?profit\s+\S+|buy\s+\S+|adopt\s+\S+|watch\s+\S+|(unwatch|remove)\s+\S+)$/i;

  document.getElementById('f').addEventListener('submit', function(e) {
    e.preventDefault();
    var input = document.getElementById('t');
    var text = input.value.trim();
    if (!text) return;
    addLine('you', 'You: ' + text);
    if (traderPanel.classList.contains('show') && TRADER_COMMAND_RE.test(text)) {
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
  // GEMZ4US, Finding #44 (2026-09-16): "Phone audio" channel slow/unreliable
  // to establish — needed ~10 manual attempts in a long-running session,
  // versus near-instant right after a restart. Root cause: unlike the main
  // /ws channel just above (which retries with backoff on its own), this
  // socket had NO reconnect logic at all — the original design (see Bug 12
  // comment below) assumed re-opening it fresh on every mic tap was
  // sufficient "self-healing", i.e. the user manually retrying via another
  // tap was the only recovery path. That assumption doesn't hold in
  // practice on a flaky/waking mobile radio: manual re-taps are far slower
  // and more irregular than an automatic backoff loop, and each failed
  // attempt is itself a start/stop recording cycle that can leave a partial
  // buffer (see MAX_PENDING_MIC_FRAMES below) — plausibly contributing to
  // Finding #45's merged/garbled flushes too. Give it the same
  // stability-aware retry-with-backoff the main channel already has.
  var audioRetryMs = 1000, audioConnectedAt = 0;
  var playCtx = null, nextPlayTime = 0;
  var micCtx = null, micStream = null, micNode = null, micGain = null;
  var recording = false;
  // GEMZ4US, Bug 12 (documented since the first beta report, root-caused
  // 2026-09-11): "first voice attempt after a reconnect is silent on both
  // devices, second attempt works." ensureAudioWs() only ever gets called
  // from startMic() — the socket is opened lazily, on the mic tap itself —
  // so any reconnect scenario (screen lock/unlock, a network blip) leaves
  // audioWs CLOSED until the next tap re-opens it. getUserMedia() usually
  // resolves fast when permission was already granted, so onaudioprocess
  // starts firing well before a fresh WSS handshake (TLS negotiation over a
  // just-woken mobile radio) actually completes — and every frame captured
  // during that window was silently dropped by the `readyState === OPEN`
  // check below, with no buffering and no retry. A short utterance spoken
  // right after tapping mic could complete entirely inside that window,
  // explaining why it reached neither device: the frames never left the
  // phone. Buffering here (bounded, and only while actively recording —
  // stopMic() clears it) instead of dropping preserves exactly that window.
  var pendingMicFrames = [];
  var micDropReported = false;
  // GEMZ4US, Finding #45 (2026-09-16): fixed — this cap's own comment always
  // intended "~1-3s of audio... generous headroom over a real WSS handshake,
  // not 'buffer forever'", but 150 frames at 2048 samples/16kHz is
  // 150*2048/16000 = 19.2s, roughly 6-8x that. A silent-accumulation window
  // that long — across however many manual retries happened while the
  // channel was down (see Finding #44 above) — is a plausible contributor
  // to messages arriving merged/garbled rather than as separate utterances.
  // 24 frames = 24*2048/16000 = 3.072s, matching the top of the originally
  // stated range.
  var MAX_PENDING_MIC_FRAMES = 24;

  function ensureAudioWs() {
    if (audioWs && (audioWs.readyState === WebSocket.OPEN || audioWs.readyState === WebSocket.CONNECTING)) return;
    audioWs = new WebSocket(proto + '://' + location.host + '/ws/audio?token=' + encodeURIComponent(token));
    audioWs.binaryType = 'arraybuffer';
    audioWs.onopen  = function() {
      audioReady = true;
      audioConnectedAt = Date.now();
      voiceStatusEl.textContent = 'voice ready — tap mic to talk';
      for (var i = 0; i < pendingMicFrames.length; i++) audioWs.send(pendingMicFrames[i]);
      pendingMicFrames = [];
    };
    audioWs.onclose = function() {
      audioReady = false;
      voiceStatusEl.textContent = 'voice disconnected — reconnecting…';
      // Same stability-aware backoff as the main /ws channel: only reset to
      // the 1s floor if the connection actually held a while, so a flapping
      // network doesn't retry forever at the fastest rate.
      var wasStable = audioConnectedAt && (Date.now() - audioConnectedAt) >= 5000;
      audioRetryMs = wasStable ? 1000 : Math.min(audioRetryMs * 2, 15000);
      setTimeout(ensureAudioWs, audioRetryMs);
    };
    audioWs.onerror = function() { audioWs.close(); };
    audioWs.onmessage = function(ev) { playChunk(ev.data); };
  }

  function playChunk(arrayBuffer) {
    if (!playCtx) {
      playCtx = new (window.AudioContext || window.webkitAudioContext)();
      nextPlayTime = playCtx.currentTime;
    }
    // Mobile browsers can suspend this context as a side effect of the mic
    // toggle — getUserMedia capture and AudioContext output appear to share
    // OS-level audio focus on at least Android Chrome. Confirmed in
    // testing: toggling the phone's mic on/off was silencing/resuming
    // Omni's own in-progress reply audio, making some real responses look
    // like "no response" at all. Re-resuming on every incoming chunk makes
    // playback self-healing regardless of what suspended it, instead of
    // only recovering on whatever timing the user happens to tap next.
    if (playCtx.state === 'suspended') { playCtx.resume().catch(function() {}); }
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
      // getUserMedia's echoCancellation:true mostly targets loopback from
      // a <video>/<audio> element or another WebRTC peer's stream — it
      // doesn't reliably reference a manually-built Web Audio graph like
      // playChunk()'s (AudioContext.createBufferSource() straight to
      // destination), so it doesn't reliably cancel Omni's own TTS output
      // here. Confirmed in testing: a delayed reply playing back at the
      // same moment the mic was open for a new attempt got picked up and
      // transcribed as new "user" input, echoing Omni's own prior wording
      // back at it. Belt-and-suspenders fix matching the desktop app's own
      // guard (voice/audio.py holds the mic while the reply is playing) —
      // don't send mic frames while a scheduled chunk is still playing,
      // plus a short tail to cover speaker/mic pickup decay.
      if (playCtx && playCtx.currentTime < nextPlayTime + 0.3) return;
      var input = e.inputBuffer.getChannelData(0);
      var down = resampleTo16k(input, inRate);
      var int16 = new Int16Array(down.length);
      for (var i = 0; i < down.length; i++) {
        var s = Math.max(-1, Math.min(1, down[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      if (audioWs && audioWs.readyState === WebSocket.OPEN) {
        audioWs.send(int16.buffer);
        micDropReported = false;
      } else if (pendingMicFrames.length < MAX_PENDING_MIC_FRAMES) {
        // Socket still (re)connecting — hold the frame instead of dropping
        // it; onopen above flushes this in order the moment it's ready.
        pendingMicFrames.push(int16.buffer);
      } else if (!micDropReported) {
        // N30 (2026-09-23): frames past the buffer cap above were being
        // dropped completely silently — no client-side error, and nothing
        // for the server-side drop-reason logging (voice/phone.py's
        // _voice(), added 2026-09-21) to see either, since these
        // frames never reach the server at all. That's the likely
        // explanation for GEMZ4US's 3/3 repro showing "zero trace of the
        // logging I added last time" — the loss was happening upstream of
        // everything that logging could see. Reported once per drop
        // episode (not per frame) over the main /ws, same as the
        // server-side pattern, and cleared as soon as a frame gets through.
        micDropReported = true;
        if (ws && ws.readyState === WebSocket.OPEN) {
          try { ws.send(JSON.stringify({type: 'mic_dropped'})); } catch (_) {}
        }
      }
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
    pendingMicFrames = [];  // don't ship stale audio on some later, unrelated reconnect
    micDropReported = false;
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
      ? 'Trader command (buy SYM:0xADDR, sell SYM, help) or just chat…' : 'Message Omni…';
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

LOGIN_HTML = """<!DOCTYPE html>
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
