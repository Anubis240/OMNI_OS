from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QImage, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QTextEdit, QTextBrowser, QVBoxLayout, QWidget, QProgressBar,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_SIDEBAR_W    = 64    # docked icon-only nav column, flush left
_STATUS_CARD_W = 220  # floating system-status card, top-left over the orb
_CHAT_PANEL_W  = 340  # floating chat panel, right side over the orb

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

# Every subprocess.run() below needs this on Windows: without it, a
# console-subsystem child (nvidia-smi.exe, powershell.exe) launched from a
# console=False-built .exe pops its OWN visible console window, since
# there's no parent console for it to inherit. Invisible in dev mode
# (python.exe already has a console these inherit from) — only surfaces in
# the packaged build, and _get_gpu()/_get_temp() run on a repeating timer,
# so it looked like a window flashing open/closed nonstop until the app
# was closed. Real bug, found by actually launching the packaged .exe.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


class C:
    # Omni palette — black canvas + a single warm accent, in the vein of
    # modern voice-first AI workspace products: near-black panels instead of
    # tinted-blue ones, orange instead of cyan for the primary accent, and a
    # neutral grey text scale instead of blue-tinted readouts. Every value
    # stays a flat hex string (not CSS rgba()) so it works unchanged through
    # both QSS f-strings and qcol()/QColor(hex).
    BG        = "#000000"
    PANEL     = "#0a0a0c"
    PANEL2    = "#111114"
    BORDER    = "#2a2a2e"
    BORDER_B  = "#ff8c42"
    BORDER_A  = "#1c1c1f"
    PRI       = "#ff8c42"
    PRI_DIM   = "#b35a1f"
    PRI_GHO   = "#1a0f08"
    ACC       = "#e9c46a"
    ACC2      = "#48cae4"
    GREEN     = "#06d6a0"
    GREEN_D   = "#049268"
    RED       = "#ef476f"
    MUTED_C   = "#ef476f"
    TEXT      = "#f0f0f0"
    TEXT_DIM  = "#575757"
    TEXT_MED  = "#9e9e9e"
    WHITE     = "#ffffff"
    DARK      = "#000000"
    BAR_BG    = "#0a0a0c"
    # QSS-only fields — never passed to qcol()/QColor(), so unlike everything
    # above these could hold a full qlineargradient(...) expression, though
    # this palette just mirrors the flat PANEL/PANEL2/PRI_GHO values.
    PANEL_BG    = "#0a0a0c"
    PANEL2_BG   = "#111114"
    PRI_GHO_BG  = "#1a0f08"


# Rotating per-companion accent colors (distinguishes companions at a glance
# in the companions list / any future multi-companion badge UI) — cycles by
# list index, not stored per-companion, so it stays stable without adding a
# "color" field to the companion schema.
COMPANION_HUES = ["#48cae4", "#e9c46a", "#06d6a0", "#b5179e", "#ef476f", "#ff8c42"]

def companion_color(companion_id: str, companions: list[dict]) -> str:
    """Stable color for a companion — index into COMPANION_HUES by position
    in the full companions list, so the same companion is always the same
    color everywhere (orb tint, World view nodes) regardless of which
    backend-filtered subset is asking. Empty id (the Omni default identity)
    isn't in the list at all, so callers should special-case it themselves."""
    ids = [c["id"] for c in companions]
    if companion_id not in ids:
        return C.PRI
    return COMPANION_HUES[ids.index(companion_id) % len(COMPANION_HUES)]


# The HUD center panel plays a looping video instead of the orb/face.
# "idle" plays normally; "speaking" takes over — looping just its tail, from
# speaking_loop_start_sec to speaking_loop_end_sec — for as long as Seraph is
# actually talking, then plays the outro window once and falls back to
# "idle". Paths are checked for existence at build time, so a missing/
# renamed asset just silently falls back to the normal orb rather than
# erroring.
FACE_VIDEO: dict = {
    # "Timeline 1.mov" — its LAB color stats already nearly match the
    # speaking video's (measured directly, no correction needed), and it's
    # rock-still/front-facing throughout, unlike the earlier idle sources.
    # Trimmed to its calmest 3s window (frame 228-317, lowest measured
    # motion + best loop-seam match of the whole clip).
    "idle": str(BASE_DIR / "leda_idle_timeline1.mp4"),
    "speaking": str(BASE_DIR / "leda_speaking.mp4"),
    # Continuous talking motion the whole way through (measured via
    # mouth-region crops every 5%), built as a 5s loop (the file is 10s —
    # two cycles back to back), no camera zoom per the filename. Loop just
    # the first cycle while speaking; last half-second is closest to a rest
    # pose, used as a short outro back to idle.
    "speaking_loop_start_sec": 0.0,
    "speaking_loop_end_sec": 5.0,
    "speaking_outro_start_sec": 9.5,
    "speaking_outro_end_sec": 10.0,
}


# --- Voices ----------------------------------------------------------------
# Gemini Live native-audio prebuilt voices this app exposes for preview.
VOICES = ["Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"]
DEFAULT_VOICE = "Leda"
VOICE_CONFIG_PATH = CONFIG_DIR / "voice.json"


def load_saved_voice() -> str:
    try:
        data = json.loads(VOICE_CONFIG_PATH.read_text(encoding="utf-8"))
        name = data.get("voice", DEFAULT_VOICE)
        return name if name in VOICES else DEFAULT_VOICE
    except Exception:
        return DEFAULT_VOICE


def save_voice(name: str) -> None:
    try:
        VOICE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        VOICE_CONFIG_PATH.write_text(json.dumps({"voice": name}), encoding="utf-8")
    except Exception:
        pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c

def lerp_hex(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ca, cb = QColor(a), QColor(b)
    r = round(ca.red()   + (cb.red()   - ca.red())   * t)
    g = round(ca.green() + (cb.green() - ca.green()) * t)
    bl = round(ca.blue() + (cb.blue()  - ca.blue())  * t)
    return f"#{r:02x}{g:02x}{bl:02x}"

class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, creationflags=_NO_WINDOW,
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2, creationflags=_NO_WINDOW,
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1, creationflags=_NO_WINDOW,
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS — powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2, creationflags=_NO_WINDOW,
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2, creationflags=_NO_WINDOW,
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW,
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        # Particle-sphere orb state: a Fibonacci-sphere point cloud (with
        # light per-point radius jitter for an organic, scanned look) that
        # slowly rotates around the vertical axis — the default HUD center
        # display now that the video-face theme isn't active by default.
        self._sphere_rotation = 0.0
        self._sphere_points   = self._generate_sphere_points(950)
        # Per-companion tint: crossfades from the previous color to the new
        # one over _tint_duration, driven by _step(). _spin_boost is extra
        # rotation speed (added on top of the normal idle/speaking rate)
        # that decays back to 0 — the "flourish" that makes a companion
        # switch read as an event instead of a silent color change.
        self._tint_color    = C.PRI
        self._tint_from     = C.PRI
        self._tint_target   = C.PRI
        self._tint_progress = 1.0
        self._tint_duration = 0.8
        self._tint_start    = 0.0
        self._spin_boost    = 0.0
        self._face_px: QPixmap | None = None
        # Crossfade state: whenever the video mode switches (idle<->speak<->
        # outro), the two sources are different footage, so an instant cut
        # is always jarring even with matched color grading. Blend the last
        # frame of the outgoing clip into the first frames of the incoming
        # one over a short window instead.
        self._transition_prev_px   = None
        self._transition_start     = 0.0
        self._transition_duration  = 0.6  # seconds
        # Three-phase theme video: "idle" plays/loops normally; the instant
        # Seraph starts speaking, "speak" takes over and loops just its
        # speaking-loop window; the instant Seraph stops, "outro" plays
        # once through its outro window as a one-shot transition, then
        # falls back to "idle" automatically.
        self._video_mode           = "idle"   # "idle" | "speak" | "outro"
        self._video_idle           = None
        self._video_idle_fps       = 24.0
        self._video_idle_frames    = 0
        self._video_speak          = None
        self._video_speak_fps      = 24.0
        self._video_speak_frames   = 0
        self._video_speak_loop_start = 0
        self._video_speak_loop_end   = 0
        self._video_outro_start      = 0
        self._video_outro_end        = 0
        self._video_last_t = 0.0
        self._video_mask   = None
        self._load_face(face_path)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _generate_sphere_points(self, n: int) -> list[tuple[float, float, float]]:
        """Fibonacci-sphere distribution with light per-point radius jitter
        (organic/scanned look rather than a perfectly smooth ball). Returns
        unit-ish (x, y, z) tuples; paintEvent scales, rotates, and projects
        these every frame."""
        pts = []
        golden = math.pi * (3.0 - math.sqrt(5.0))
        for i in range(n):
            y = 1 - (i / (n - 1)) * 2
            r_xy = math.sqrt(max(0.0, 1 - y * y))
            theta = golden * i
            x = math.cos(theta) * r_xy
            z = math.sin(theta) * r_xy
            jitter = random.uniform(0.88, 1.0)
            pts.append((x * jitter, y * jitter, z * jitter))
        return pts

    def set_theme_video(self, config: dict | None):
        """Swap the center HUD to a looping video instead of the static
        orb/face, or back to normal if config is None/missing. config is
        {"idle": path, "speaking": path (optional),
        "speaking_loop_start_sec"/"speaking_loop_end_sec": the window that
        loops for as long as Seraph is actually talking, "speaking_outro_
        start_sec"/"speaking_outro_end_sec": a one-shot window played once
        when Seraph stops talking before falling back to "idle"}."""
        self.stop_video()
        self._video_mode = "idle"
        self._transition_prev_px = None
        if not config:
            return
        import cv2
        idle_path = config.get("idle")
        if idle_path and Path(idle_path).exists():
            try:
                cap = cv2.VideoCapture(idle_path)
                if cap.isOpened():
                    self._video_idle        = cap
                    self._video_idle_fps    = cap.get(cv2.CAP_PROP_FPS) or 24.0
                    self._video_idle_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
            except Exception:
                self._video_idle = None

        speak_path = config.get("speaking")
        if speak_path and Path(speak_path).exists():
            try:
                cap2 = cv2.VideoCapture(speak_path)
                if cap2.isOpened():
                    self._video_speak        = cap2
                    fps                      = cap2.get(cv2.CAP_PROP_FPS) or 24.0
                    self._video_speak_fps    = fps
                    total = int(cap2.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
                    self._video_speak_frames = total
                    lo = max(0, int(config.get("speaking_loop_start_sec", 0.0) * fps))
                    hi = int(config.get("speaking_loop_end_sec", total / fps) * fps)
                    self._video_speak_loop_start = lo
                    self._video_speak_loop_end   = max(lo + 1, min(hi, total))
                    olo = max(0, int(config.get("speaking_outro_start_sec", hi / fps) * fps))
                    ohi = int(config.get("speaking_outro_end_sec", total / fps) * fps)
                    self._video_outro_start = olo
                    self._video_outro_end   = max(olo + 1, min(ohi, total))
            except Exception:
                self._video_speak = None

        self._video_last_t = time.time()
        self._video_mask   = None

    def animate_companion_switch(self, color_hex: str):
        """Crossfades the orb's tint to color_hex and gives it a decaying
        burst of extra spin — called whenever the active companion changes
        (see MainWindow._cycle_companion)."""
        self._tint_from     = self._tint_color
        self._tint_target   = color_hex
        self._tint_progress = 0.0
        self._tint_start    = time.time()
        self._spin_boost    = 14.0

    def video_active(self) -> bool:
        return self._video_idle is not None or self._video_speak is not None

    def stop_video(self):
        for attr in ("_video_idle", "_video_speak"):
            cap = getattr(self, attr)
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _advance_video_frame(self):
        import cv2
        from PIL import Image, ImageDraw

        if self._video_mode in ("speak", "outro") and self._video_speak is not None:
            cap = self._video_speak
        else:
            cap = self._video_idle
            self._video_mode = "idle"
        if cap is None:
            return

        if self._video_mode == "speak":
            if cap.get(cv2.CAP_PROP_POS_FRAMES) >= self._video_speak_loop_end - 1:
                cap.set(cv2.CAP_PROP_POS_FRAMES, self._video_speak_loop_start)
        elif self._video_mode == "outro":
            if cap.get(cv2.CAP_PROP_POS_FRAMES) >= self._video_outro_end - 1:
                # One-shot outro finished — drop back to the idle loop.
                self._transition_prev_px = self._face_px
                self._transition_start   = time.time()
                self._video_mode = "idle"
                cap = self._video_idle
                if cap is None:
                    return
        else:  # idle
            if self._video_idle_frames and cap.get(cv2.CAP_PROP_POS_FRAMES) >= self._video_idle_frames - 1:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        ok, frame = cap.read()
        if not ok:
            if self._video_mode == "outro":
                self._video_mode = "idle"
                cap = self._video_idle
                if cap is None:
                    return
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            else:
                fallback = self._video_speak_loop_start if self._video_mode == "speak" else 0
                cap.set(cv2.CAP_PROP_POS_FRAMES, fallback)
            ok, frame = cap.read()
            if not ok:
                return
        SZ = 480
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = frame.shape[:2]
        if w != h:
            # Center-crop to square first — a plain resize on a non-square
            # source (e.g. 1280x720) would squish the image instead of
            # cropping it, distorting faces/content.
            m = min(h, w)
            y0, x0 = (h - m) // 2, (w - m) // 2
            frame = frame[y0:y0 + m, x0:x0 + m]
        frame = cv2.resize(frame, (SZ, SZ), interpolation=cv2.INTER_AREA)
        img = Image.fromarray(frame).convert("RGBA")
        if self._video_mask is None:
            mask = Image.new("L", (SZ, SZ), 0)
            ImageDraw.Draw(mask).ellipse((2, 2, SZ - 2, SZ - 2), fill=255)
            self._video_mask = mask
        img.putalpha(self._video_mask)
        qimg = QImage(img.tobytes("raw", "RGBA"), SZ, SZ, QImage.Format.Format_RGBA8888).copy()
        self._face_px = QPixmap.fromImage(qimg)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1

        if self.video_active():
            if self._video_speak is not None:
                import cv2
                if self.speaking and self._video_mode != "speak":
                    self._transition_prev_px = self._face_px
                    self._transition_start   = time.time()
                    self._video_mode = "speak"
                    self._video_speak.set(cv2.CAP_PROP_POS_FRAMES, self._video_speak_loop_start)
                elif not self.speaking and self._video_mode == "speak":
                    self._transition_prev_px = self._face_px
                    self._transition_start   = time.time()
                    self._video_mode = "outro"
                    self._video_speak.set(cv2.CAP_PROP_POS_FRAMES, self._video_outro_start)

            now_t = time.time()
            active_fps = self._video_speak_fps if self._video_mode in ("speak", "outro") else self._video_idle_fps
            if now_t - self._video_last_t >= 1.0 / max(active_fps, 1.0):
                self._advance_video_frame()
                self._video_last_t = now_t

        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                # A video face zooming in/out while it's supposedly just
                # talking looks bad — only pulse-scale the abstract orb.
                self._tgt_scale = 1.0 if self.video_active() else random.uniform(1.06, 1.14)
                self._tgt_halo  = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo  = random.uniform(15, 28)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        if self._tint_progress < 1.0:
            self._tint_progress = min(1.0, (time.time() - self._tint_start) / self._tint_duration)
            eased = 1 - (1 - self._tint_progress) ** 3  # ease-out cubic
            self._tint_color = lerp_hex(self._tint_from, self._tint_target, eased)

        self._sphere_rotation += (0.35 if self.speaking else 0.12) + self._spin_boost
        self._spin_boost *= 0.90
        if self._spin_boost < 0.02:
            self._spin_boost = 0.0
        self.update()

    def _draw_face_layer(self, p, cx, cy, fw):
        if self._face_px:
            face_frac = 0.90 if self.video_active() else 0.62
            fsz    = int(fw * face_frac * self._scale)
            scaled = self._face_px.scaled(
                fsz, fsz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x, y = int(cx - fsz / 2), int(cy - fsz / 2)

            elapsed = time.time() - self._transition_start
            if self._transition_prev_px is not None and elapsed < self._transition_duration:
                prev_scaled = self._transition_prev_px.scaled(
                    fsz, fsz,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                alpha = max(0.0, min(1.0, elapsed / self._transition_duration))
                p.setOpacity(1.0)
                p.drawPixmap(x, y, prev_scaled)
                p.setOpacity(alpha)
                p.drawPixmap(x, y, scaled)
                p.setOpacity(1.0)
            else:
                p.drawPixmap(x, y, scaled)
        else:
            self._draw_particle_sphere(p, cx, cy, fw)

    def _draw_particle_sphere(self, p, cx, cy, fw):
        r = fw * 0.30 * self._scale
        rot = math.radians(self._sphere_rotation)
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        pri = QColor(C.MUTED_C if self.muted else self._tint_color)
        pulse = 0.5 + 0.5 * math.sin(self._tick * 0.05)
        # Painter's algorithm: draw back-to-front so front-facing points
        # overdraw the ones behind them.
        pts = sorted(self._sphere_points, key=lambda pt: pt[2] * cos_r - pt[0] * sin_r)
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, z in pts:
            xr = x * cos_r - z * sin_r   # rotate around the vertical axis
            zr = x * sin_r + z * cos_r
            depth = (zr + 1) / 2         # 0 = back, 1 = front
            sx = cx + xr * r
            sy = cy - y * r
            size = 0.8 + depth * 2.2
            energy = (0.85 + 0.3 * pulse) if self.speaking else 1.0
            alpha = max(0, min(255, int(40 + depth * 190 * energy)))
            p.setBrush(QBrush(QColor(pri.red(), pri.green(), pri.blue(), alpha)))
            p.drawEllipse(QPointF(sx, sy), size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # faint background grid dots — the only chrome left around the orb;
        # everything else (state text, ticks, scanners, waveform) moved out
        # to MainWindow's floating top bar / companion switcher, matching
        # the plain-black-canvas-plus-orb presentation this theme targets.
        p.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)

        # soft halo glow behind the orb
        r_face = fw * 0.31
        for i in range(8):
            r   = r_face * (1.7 - i * 0.09)
            frc = 1.0 - i / 8
            a   = max(0, min(255, int(self._halo * 0.09 * frc)))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        self._draw_face_layer(p, cx, cy, fw)

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

class LogWidget(QTextBrowser):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.setFont(QFont("Segoe UI", 9))
        self.setStyleSheet(f"""
            QTextBrowser {{
                background: {C.PANEL_BG};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 1px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO_BG};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 1px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0

        # Pre-built clickable links (e.g. from share_file) arrive as actual
        # HTML — render them instantly rather than typing out the markup
        # character by character, which would type out raw "<a href=...".
        if "<a href=" in self._text:
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertHtml(f'<span style="color:{C.ACC2}">{self._text}</span><br>')
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)
            return

        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for Omni", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Segoe UI", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Segoe UI", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 440

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry     = time.time() + expiry_secs
        self._on_new_key = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Segoe UI", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually at:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(url)
        self._url_lbl.setFont(QFont("Segoe UI", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 2px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Segoe UI", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 1px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 1px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Segoe UI", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Segoe UI", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 2px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Segoe UI", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — Omni ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url, key, auto = result[0], result[1], (result[2] if len(result) >= 3 else "")
                self._url_lbl.setText(url)
                self._key_lbl.setText(key)
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 2px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 1px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Segoe UI", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure Omni-OS before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Segoe UI", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 1px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)

        get_key_btn = QPushButton("Get a free Gemini API key ↗")
        get_key_btn.setFont(QFont("Segoe UI", 8))
        get_key_btn.setFixedHeight(22)
        get_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        get_key_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.ACC2};
                border: none; text-align: left; padding: 2px 0;
            }}
            QPushButton:hover {{ color: {C.PRI}; text-decoration: underline; }}
        """)
        get_key_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://aistudio.google.com/api-keys?project=gen-lang-client-0368720913")
        ))
        layout.addWidget(get_key_btn)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 1px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO_BG}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 1px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 1px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("OMNI-OS")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_voice_change  = None
        self.on_companions_changed = None  # callable: () -> None — a companion was added/edited (e.g. a new World-view sub-agent); triggers a reconnect so the live session picks it up immediately instead of waiting for the next natural reconnect
        self.on_remote_clicked = None   # callable: () -> (url, key, auto_login_url) | None
        self.on_trader_clicked = None   # callable: () -> None — opens the trader panel
        self.on_always_listening_toggled = None   # callable: (bool) -> None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._muted           = False
        self._speech_muted    = False
        self._always_listening = False
        self._current_file: str | None = None
        self._face_path       = face_path
        self.voice              = load_saved_voice()

        self._build_ui()

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik güncelleme timer'ı
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._state_sig.connect(self._apply_state)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_speech = QShortcut(QKeySequence("F5"), self)
        sc_speech.activated.connect(self._toggle_speech_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)

    def _build_ui(self):
        """Builds (or rebuilds, on a theme switch) the entire widget tree
        from the current C palette. Safe to call more than once: the old
        central widget, if any, is torn down first.

        Layout: a docked icon sidebar (flush left) plus a full-bleed center
        stack (the orb, or the trader/settings panel swapped over it) fill
        `central` via a real QHBoxLayout. The top bar, system-status card,
        chat panel, and companion switcher are NOT laid out — they're child
        widgets of `central` positioned by absolute geometry in
        _position_overlays() (same technique already used for SetupOverlay/
        RemoteKeyOverlay below), so they float over the orb like Zoey OS's
        panels float over its 3D world view."""
        old_central = self.centralWidget()
        if old_central is not None:
            old_central.deleteLater()

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar(), stretch=0)

        if getattr(self, "hud", None) is not None:
            self.hud.stop_video()  # release the old theme's video file handle, if any
        self.hud = HudCanvas(self._face_path)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Procedural particle-sphere orb is the default HUD now (matches the
        # black/orange, orb-style companion look this theme targets) rather
        # than the video avatar. The video theme is still fully wired — call
        # self.hud.set_theme_video(FACE_VIDEO) to switch back to it.
        self._init_hud_tint()

        # Center column is a stack: the HUD orb/video, or the native trader
        # panel swapped in over it (see open_trader_panel()) — same
        # in-place-swap mechanic the old theme picker used, no separate
        # window. Trader panel itself is built lazily on first open.
        self._center_stack = QStackedWidget()
        self._center_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._center_stack.addWidget(self.hud)
        self._trader_panel = None
        self._settings_panel = None
        self._integrations_panel = None
        self._world_panel = None

        # A 56px top strip is reserved (not just floated over) for the top
        # bar — trader/settings' own headers start right at y=0 of their
        # own widget, so without this they'd render underneath/behind the
        # floating top bar instead of clear of it.
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)
        right_col.addSpacing(56)
        right_col.addWidget(self._center_stack, stretch=1)
        root.addLayout(right_col, stretch=1)

        self._top_bar             = self._build_top_bar(central)
        self._status_card         = self._build_status_card(central)
        self._chat_panel          = self._build_chat_panel(central)
        self._companion_switcher  = self._build_companion_switcher(central)
        self._corner_label        = QLabel("© KONDUX", central)
        self._corner_label.setFont(QFont("Segoe UI", 7))
        self._corner_label.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._corner_label.adjustSize()
        self._position_overlays()

        self._refresh_trader_visibility()
        self._refresh_companion_switcher()

        try:
            self._log_sig.disconnect()
        except TypeError:
            pass  # nothing was connected yet (first build)
        self._log_sig.connect(self._log.append_log)

    def _position_overlays(self):
        """Places the floating top bar / status card / chat panel /
        companion switcher over the orb. Called once from _build_ui() and
        again on every resize (see resizeEvent)."""
        cw = self.centralWidget()
        if cw is None:
            return
        W, H = cw.width(), cw.height()

        if hasattr(self, "_top_bar"):
            self._top_bar.setGeometry(_SIDEBAR_W, 0, max(0, W - _SIDEBAR_W), 56)
        if hasattr(self, "_status_card"):
            # Not layout-managed (it's a manually-positioned overlay), so
            # nothing auto-sizes it to its content — force it explicitly
            # rather than trusting .width()/.height(), which can still
            # report Qt's pre-layout default size at this point.
            self._status_card.adjustSize()
            self._status_card.move(_SIDEBAR_W + 16, 68)
        if hasattr(self, "_chat_panel"):
            self._chat_panel.resize(_CHAT_PANEL_W, max(240, H - 68 - 16))
            self._chat_panel.move(max(_SIDEBAR_W + 16, W - _CHAT_PANEL_W - 16), 68)
        if hasattr(self, "_companion_switcher"):
            sw_w = 220
            self._companion_switcher.move(_SIDEBAR_W + (W - _SIDEBAR_W - sw_w) // 2, H - 66)
        if hasattr(self, "_corner_label"):
            self._corner_label.move(W - self._corner_label.width() - 14, H - 22)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        self._position_overlays()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 420
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_sidebar(self) -> QWidget:
        """Docked icon-only nav column, flush against the left edge."""
        w = QWidget()
        w.setFixedWidth(_SIDEBAR_W)
        w.setStyleSheet(f"background: {C.PANEL_BG}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 14, 8, 12)
        lay.setSpacing(8)

        logo = QPushButton("Ω")
        logo.setFixedSize(40, 40)
        logo.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        logo.setCursor(Qt.CursorShape.PointingHandCursor)
        logo.setToolTip("Home")
        logo.setStyleSheet(f"""
            QPushButton {{ color: {C.PRI}; background: transparent; border: none; border-radius: 20px; }}
            QPushButton:hover {{ background: {C.PANEL2_BG}; }}
        """)
        logo.clicked.connect(self._go_home)
        lay.addWidget(logo)
        lay.addSpacing(8)

        def _icon_btn(txt, cb, tooltip):
            b = QPushButton(txt)
            b.setFixedSize(40, 40)
            b.setFont(QFont("Segoe UI", 14))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tooltip)
            b.clicked.connect(cb)
            lay.addWidget(b)
            return b

        self._mute_btn = _icon_btn("🎙", self._toggle_mute, "Mute microphone [F4]")
        self._style_mute_btn()
        self._speech_mute_btn = _icon_btn("🔊", self._toggle_speech_mute, "Mute speech [F5]")
        self._style_speech_mute_btn()

        lay.addSpacing(6)
        self._trader_btn = _icon_btn("◈", self._open_trader, "Trader panel")
        self._trader_btn.setStyleSheet(f"""
            QPushButton {{ color: {C.ACC2}; background: transparent; border: none; border-radius: 20px; }}
            QPushButton:hover {{ background: {C.PANEL2_BG}; color: {C.PRI}; }}
        """)
        _icon_btn("⛓", self._open_remote, "Remote control").setStyleSheet(f"""
            QPushButton {{ color: {C.TEXT_MED}; background: transparent; border: none; border-radius: 20px; }}
            QPushButton:hover {{ background: {C.PANEL2_BG}; color: {C.PRI}; }}
        """)
        _icon_btn("▦", self._toggle_integrations_panel, "Integrations").setStyleSheet(f"""
            QPushButton {{ color: {C.TEXT_MED}; background: transparent; border: none; border-radius: 20px; }}
            QPushButton:hover {{ background: {C.PANEL2_BG}; color: {C.PRI}; }}
        """)
        _icon_btn("◎", self._toggle_world_panel, "World").setStyleSheet(f"""
            QPushButton {{ color: {C.TEXT_MED}; background: transparent; border: none; border-radius: 20px; }}
            QPushButton:hover {{ background: {C.PANEL2_BG}; color: {C.PRI}; }}
        """)
        _icon_btn("⚙", self._toggle_settings_panel, "Settings").setStyleSheet(f"""
            QPushButton {{ color: {C.TEXT_MED}; background: transparent; border: none; border-radius: 20px; }}
            QPushButton:hover {{ background: {C.PANEL2_BG}; color: {C.PRI}; }}
        """)
        _icon_btn("⛶", self._toggle_fullscreen, "Fullscreen [F11]").setStyleSheet(f"""
            QPushButton {{ color: {C.TEXT_MED}; background: transparent; border: none; border-radius: 20px; }}
            QPushButton:hover {{ background: {C.PANEL2_BG}; color: {C.PRI}; }}
        """)

        lay.addStretch()
        return w

    def _build_top_bar(self, parent: QWidget) -> QWidget:
        """Thin transparent bar floating over the top of the orb: wordmark,
        the always-listening pill (center), and a state pill + clock."""
        w = QWidget(parent)
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 12, 20, 0)

        wordmark = QLabel("OMNI_OS")
        wordmark.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        wordmark.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(wordmark)
        lay.addStretch()

        self._always_listening_btn = QPushButton("○  CLICK TO LISTEN")
        self._always_listening_btn.setFixedHeight(32)
        self._always_listening_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._always_listening_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._always_listening_btn.clicked.connect(self._toggle_always_listening)
        self._style_always_listening_btn()
        lay.addWidget(self._always_listening_btn)
        lay.addStretch()

        self._state_pill = QLabel("IDLE")
        self._state_pill.setFixedHeight(26)
        self._state_pill.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._state_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._state_pill)
        lay.addSpacing(10)

        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(self._clock_lbl)

        self._refresh_state_pill()
        self._state_pill_tmr = QTimer(self)
        self._state_pill_tmr.timeout.connect(self._refresh_state_pill)
        self._state_pill_tmr.start(400)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))

    def _refresh_state_pill(self):
        hud = getattr(self, "hud", None)
        if hud is None or not hasattr(self, "_state_pill"):
            return
        if hud.muted:
            txt, col = "MUTED", C.MUTED_C
        elif hud.speaking:
            txt, col = "SPEAKING", C.ACC
        elif hud.state in ("THINKING", "PROCESSING"):
            txt, col = hud.state, C.ACC2
        elif hud.state == "LISTENING":
            txt, col = "LISTENING", C.GREEN
        else:
            txt, col = hud.state, C.TEXT_MED
        self._state_pill.setText(txt)
        self._state_pill.setStyleSheet(
            f"color: {col}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER}; "
            f"border-radius: 13px; padding: 4px 14px;"
        )

    def _build_status_card(self, parent: QWidget) -> QWidget:
        """Floating card, top-left over the orb: system metrics (hidden
        while trader/settings are open — see _set_status_card_mode — since
        they'd otherwise float over those panels' own content), plus the
        mount point trader uses for its own config controls
        (set_left_panel_extra) while active."""
        w = QWidget(parent)
        w.setFixedWidth(_STATUS_CARD_W)
        w.setStyleSheet(f"background: {C.PANEL_BG}; border: 1px solid {C.BORDER}; border-radius: 14px;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        self._status_metrics_section = QWidget()
        ms_lay = QVBoxLayout(self._status_metrics_section)
        ms_lay.setContentsMargins(0, 0, 0, 0)
        ms_lay.setSpacing(6)

        hdr = QLabel("SYSTEM STATUS")
        hdr.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        ms_lay.addWidget(hdr)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ef476f")
        for bar in [self._bar_cpu, self._bar_mem, self._bar_net, self._bar_gpu, self._bar_tmp]:
            ms_lay.addWidget(bar)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        ms_lay.addWidget(sep)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        ms_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Segoe UI", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        ms_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Segoe UI", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        ms_lay.addWidget(os_lbl)

        lay.addWidget(self._status_metrics_section)

        # Empty by default — trader mounts its own config controls here
        # while active (see set_left_panel_extra()). Settings never uses
        # this slot, so the whole card just hides for settings instead
        # (see _set_status_card_mode).
        self._left_extra_container = QWidget()
        self._left_extra_layout = QVBoxLayout(self._left_extra_container)
        self._left_extra_layout.setContentsMargins(0, 0, 0, 0)
        self._left_extra_layout.setSpacing(4)
        self._left_extra_scroll = QScrollArea()
        self._left_extra_scroll.setWidgetResizable(True)
        self._left_extra_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._left_extra_scroll.setMaximumHeight(280)
        self._left_extra_scroll.setStyleSheet("background: transparent; border: none;")
        self._left_extra_scroll.setWidget(self._left_extra_container)
        self._left_extra_scroll.setVisible(False)
        lay.addWidget(self._left_extra_scroll)

        return w

    def _set_status_card_mode(self, mode: str):
        """'home' — metrics visible, trader-config slot hidden.
        'trader_config' — metrics hidden, trader-config slot visible; only
        entered via the trader panel's own ⚙ CONFIG button (see
        _toggle_trader_config) since it covers that panel's wallet/live-mode
        controls otherwise — trader opens with the card hidden by default.
        'hidden' — whole card hidden (settings has no use for this slot, and
        entering trader defaults here too, until CONFIG is clicked)."""
        if mode == "hidden":
            self._status_card.setVisible(False)
            return
        self._status_card.setVisible(True)
        self._status_metrics_section.setVisible(mode == "home")
        self._left_extra_scroll.setVisible(mode == "trader_config")

    def _toggle_trader_config(self):
        showing = self._status_card.isVisible() and self._left_extra_scroll.isVisible()
        self._set_status_card_mode("hidden" if showing else "trader_config")

    def _build_companion_switcher(self, parent: QWidget) -> QWidget:
        """Floating pill below the orb: chevrons + active companion name —
        cycles settings["active_companion_id"] through the real companion
        registry (see core/settings_store.py)."""
        w = QWidget(parent)
        w.setFixedSize(220, 40)
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        def _chevron(txt, direction):
            b = QPushButton(txt)
            b.setFixedSize(28, 28)
            b.setFont(QFont("Segoe UI", 12))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ color: {C.TEXT_MED}; background: transparent; border: none; }} "
                f"QPushButton:hover {{ color: {C.PRI}; }}"
            )
            b.clicked.connect(lambda: self._cycle_companion(direction))
            return b

        lay.addWidget(_chevron("‹", -1))
        self._companion_name_lbl = QLabel("OMNI")
        self._companion_name_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._companion_name_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 2px;")
        self._companion_name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._companion_name_lbl.setMinimumWidth(140)
        lay.addWidget(self._companion_name_lbl)
        lay.addWidget(_chevron("›", 1))

        return w

    def _init_hud_tint(self):
        """Sets the orb's starting color to match whichever companion is
        already active on launch — instantly, no crossfade/spin (that
        flourish is reserved for an actual switch, see _cycle_companion)."""
        from core import settings_store
        settings = settings_store.load_settings()
        active_id = settings.get("active_companion_id") or ""
        color = companion_color(active_id, settings.get("companions", [])) if active_id else C.PRI
        self.hud._tint_color = self.hud._tint_from = self.hud._tint_target = color
        self.hud._tint_progress = 1.0

    def _cycle_companion(self, direction: int):
        from core import settings_store
        settings = settings_store.load_settings()
        companions = settings.get("companions", [])
        ids = [""] + [c["id"] for c in companions]  # "" = default Omni identity
        current = settings.get("active_companion_id") or ""
        idx = ids.index(current) if current in ids else 0
        idx = (idx + direction) % len(ids)
        settings["active_companion_id"] = ids[idx]
        settings_store.save_settings(settings)
        self._refresh_companion_switcher()
        new_color = companion_color(ids[idx], companions) if ids[idx] else C.PRI
        self.hud.animate_companion_switch(new_color)
        name = "Omni (default)" if not ids[idx] else next(c["name"] for c in companions if c["id"] == ids[idx])
        self._log.append_log(f"SYS: Switched to {name} — takes effect on next reconnect.")

    def _refresh_companion_switcher(self):
        from core import settings_store
        settings = settings_store.load_settings()
        active_id = settings.get("active_companion_id") or ""
        if not active_id:
            name = "OMNI"
        else:
            comp = next((c for c in settings.get("companions", []) if c["id"] == active_id), None)
            name = comp["name"].upper() if comp else "OMNI"
        self._companion_name_lbl.setText(name)
        if hasattr(self, "_chat_header_lbl"):
            self._chat_header_lbl.setText(name)

    def _build_chat_panel(self, parent: QWidget) -> QWidget:
        """Floating card, right side over the orb: transcript + message
        input — the same LogWidget/text-input plumbing as before, just
        restyled into a docked-panel look instead of a full-width column."""
        w = QWidget(parent)
        w.setFixedWidth(_CHAT_PANEL_W)
        w.setStyleSheet(f"background: {C.PANEL_BG}; border: 1px solid {C.BORDER}; border-radius: 14px;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 10)
        lay.setSpacing(8)

        hdr_row = QHBoxLayout()
        dot = QLabel("●")
        dot.setFont(QFont("Segoe UI", 8))
        dot.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        hdr_row.addWidget(dot)
        self._chat_header_lbl = QLabel("OMNI")
        self._chat_header_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._chat_header_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent; letter-spacing: 1px;")
        hdr_row.addWidget(self._chat_header_lbl)
        hdr_row.addStretch()
        lay.addLayout(hdr_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        lay.addWidget(sep)

        self._log = LogWidget()
        self._log.setStyleSheet(f"QTextBrowser {{ background: transparent; border: none; color: {C.TEXT}; }}")
        lay.addWidget(self._log, stretch=1)

        lay.addLayout(self._build_input_row())

        hint = QLabel("AI-generated · verify important actions")
        hint.setFont(QFont("Segoe UI", 6))
        hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)

        return w

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        attach = QPushButton("+")
        attach.setFixedSize(30, 30)
        attach.setFont(QFont("Segoe UI", 13))
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setToolTip("Attach a file")
        attach.setStyleSheet(f"""
            QPushButton {{ color: {C.TEXT_MED}; background: {C.PANEL2_BG}; border: none; border-radius: 15px; }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        attach.clicked.connect(self._browse_attach_file)
        row.addWidget(attach)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Message Omni…")
        self._input.setFont(QFont("Segoe UI", 9))
        self._input.setFixedHeight(34)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2_BG}; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 17px; padding: 3px 14px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input, stretch=1)

        send = QPushButton("↑")
        send.setFixedSize(34, 34)
        send.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{ background: {C.PRI}; color: #000000; border: none; border-radius: 17px; }}
            QPushButton:hover {{ background: {C.ACC}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _browse_attach_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Attach a file")
        if path:
            self._on_file_selected(path)

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._log.append_log(f"FILE: {icon} {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {C.MUTED_C}; background: {C.PANEL2_BG};
                    border: 1px solid {C.MUTED_C}; border-radius: 20px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{ color: {C.TEXT_MED}; background: transparent; border: none; border-radius: 20px; }}
                QPushButton:hover {{ background: {C.PANEL2_BG}; color: {C.GREEN}; }}
            """)

    def _toggle_speech_mute(self):
        self._speech_muted = not self._speech_muted
        self._style_speech_mute_btn()
        if self._speech_muted:
            self._log.append_log("SYS: Speech playback muted — Omni will respond silently.")
        else:
            self._log.append_log("SYS: Speech playback active.")

    def _style_speech_mute_btn(self):
        if self._speech_muted:
            self._speech_mute_btn.setText("🔈")
            self._speech_mute_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {C.MUTED_C}; background: {C.PANEL2_BG};
                    border: 1px solid {C.MUTED_C}; border-radius: 20px;
                }}
            """)
        else:
            self._speech_mute_btn.setText("🔊")
            self._speech_mute_btn.setStyleSheet(f"""
                QPushButton {{ color: {C.TEXT_MED}; background: transparent; border: none; border-radius: 20px; }}
                QPushButton:hover {{ background: {C.PANEL2_BG}; color: {C.GREEN}; }}
            """)

    def _toggle_always_listening(self):
        self._always_listening = not self._always_listening
        self._style_always_listening_btn()
        if self._always_listening:
            self._log.append_log("SYS: Listening — click again to stop.")
        else:
            self._log.append_log("SYS: Listening stopped.")
        if self.on_always_listening_toggled:
            threading.Thread(target=self.on_always_listening_toggled, args=(self._always_listening,), daemon=True).start()

    def _style_always_listening_btn(self):
        if self._always_listening:
            self._always_listening_btn.setText("●  LISTENING")
            self._always_listening_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {C.PRI}; background: {C.PRI_GHO_BG};
                    border: 1px solid {C.PRI}; border-radius: 16px; padding: 4px 18px;
                }}
            """)
        else:
            self._always_listening_btn.setText("○  CLICK TO LISTEN")
            self._always_listening_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_MED}; background: {C.PANEL2_BG};
                    border: 1px solid {C.BORDER}; border-radius: 16px; padding: 4px 18px;
                }}
                QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI_DIM}; }}
            """)

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url, key = result[0], result[1]
        auto = result[2] if len(result) >= 3 else ""
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov = RemoteKeyOverlay(url, key, auto_login_url=auto, expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — {url}")

    def _open_trader(self):
        # Direct button click: toggle, like the old theme picker — a second
        # click is the user consciously asking to peek back at the HUD.
        self._toggle_trader_panel()

    def _ensure_trader_panel(self):
        if self._trader_panel is None:
            from trader_panel import TraderPanel  # deferred: see trader_panel.py's module docstring
            self._trader_panel = TraderPanel()
            self._trader_panel.on_config_toggle = self._toggle_trader_config
            self._center_stack.addWidget(self._trader_panel)
        return self._trader_panel

    def _set_home_overlays_visible(self, visible: bool):
        """The floating chat panel and companion switcher sit on top of the
        orb — hide them while trader/settings fill the center stack instead,
        so they don't float over those panels' own content. The status card
        stays visible either way since trader/settings mount their own extra
        controls into it (set_left_panel_extra)."""
        if hasattr(self, "_chat_panel"):
            self._chat_panel.setVisible(visible)
        if hasattr(self, "_companion_switcher"):
            self._companion_switcher.setVisible(visible)

    def _go_home(self):
        """Sidebar logo click — always returns to the HUD view, regardless
        of which panel (trader/settings) is currently open. The trader and
        settings icons only toggle their OWN panel, which isn't an obvious
        "go back" affordance from the other one, so this is the one control
        that unconditionally gets back to the orb."""
        if self._center_stack.currentWidget() is self.hud:
            return
        self._center_stack.setCurrentWidget(self.hud)
        self.set_left_panel_extra(None)
        self._set_home_overlays_visible(True)
        self._set_status_card_mode("home")
        self._refresh_trader_visibility()

    def _toggle_trader_panel(self):
        panel = self._ensure_trader_panel()
        if self._center_stack.currentWidget() is panel:
            self._center_stack.setCurrentWidget(self.hud)
            self.set_left_panel_extra(None)
            self._set_home_overlays_visible(True)
            self._set_status_card_mode("home")
        else:
            self._center_stack.setCurrentWidget(panel)
            self.set_left_panel_extra(panel.left_panel_widget())
            self._set_home_overlays_visible(False)
            self._set_status_card_mode("hidden")

    def open_trader_panel(self):
        """Idempotent show — used by the voice/tool-call path (see
        actions/launch_trader.py) so a repeated "open the trader" while it's
        already open doesn't flip back to the HUD. Only the manual button
        click toggles (see _open_trader above)."""
        panel = self._ensure_trader_panel()
        self._center_stack.setCurrentWidget(panel)
        self.set_left_panel_extra(panel.left_panel_widget())
        self._set_home_overlays_visible(False)
        self._set_status_card_mode("hidden")

    def _ensure_settings_panel(self):
        if self._settings_panel is None:
            from settings_panel import SettingsPanel  # deferred: see settings_panel.py's module docstring
            self._settings_panel = SettingsPanel()
            self._center_stack.addWidget(self._settings_panel)
        return self._settings_panel

    def _toggle_settings_panel(self):
        panel = self._ensure_settings_panel()
        if self._center_stack.currentWidget() is panel:
            self._center_stack.setCurrentWidget(self.hud)
            self.set_left_panel_extra(None)
            self._set_home_overlays_visible(True)
            self._set_status_card_mode("home")
        else:
            self._center_stack.setCurrentWidget(panel)
            self.set_left_panel_extra(None)
            self._set_home_overlays_visible(False)
            self._set_status_card_mode("hidden")
        self._refresh_trader_visibility()  # settings may have just changed (e.g. trader toggle)
        self._refresh_companion_switcher()  # or the active companion

    def _ensure_integrations_panel(self):
        if self._integrations_panel is None:
            from integrations_panel import IntegrationsPanel  # deferred: see integrations_panel.py's module docstring
            self._integrations_panel = IntegrationsPanel()
            self._center_stack.addWidget(self._integrations_panel)
        return self._integrations_panel

    def _toggle_integrations_panel(self):
        panel = self._ensure_integrations_panel()
        if self._center_stack.currentWidget() is panel:
            self._center_stack.setCurrentWidget(self.hud)
            self.set_left_panel_extra(None)
            self._set_home_overlays_visible(True)
            self._set_status_card_mode("home")
        else:
            self._center_stack.setCurrentWidget(panel)
            self.set_left_panel_extra(None)
            self._set_home_overlays_visible(False)
            self._set_status_card_mode("hidden")

    def _ensure_world_panel(self):
        if self._world_panel is None:
            from world_panel import WorldPanel  # deferred: see world_panel.py's module docstring
            self._world_panel = WorldPanel()
            self._world_panel.on_companion_added = self._notify_companions_changed
            self._center_stack.addWidget(self._world_panel)
        return self._world_panel

    def _notify_companions_changed(self):
        if self.on_companions_changed:
            self.on_companions_changed()

    def _toggle_world_panel(self):
        panel = self._ensure_world_panel()
        if self._center_stack.currentWidget() is panel:
            self._center_stack.setCurrentWidget(self.hud)
            self.set_left_panel_extra(None)
            self._set_home_overlays_visible(True)
            self._set_status_card_mode("home")
        else:
            panel.refresh()
            self._center_stack.setCurrentWidget(panel)
            self.set_left_panel_extra(None)
            self._set_home_overlays_visible(False)
            self._set_status_card_mode("hidden")

    def _refresh_trader_visibility(self):
        from core import settings_store
        self._trader_btn.setVisible(settings_store.load_settings()["trader"]["enabled"])

    def get_trader_state(self) -> dict | None:
        """Snapshot for the phone dashboard's TRADER view (stats, positions,
        watchlist) — None until the trader panel has been opened at least
        once (no engine exists yet), since it's created lazily (see
        _ensure_trader_panel)."""
        if self._trader_panel is None:
            return None
        engine = self._trader_panel.engine
        # Never block this on a live network fetch — trending.discover()'s
        # own 429 backoff can take up to 45s per chain, and this is polled
        # every few seconds. Return whatever's cached now (instant) and
        # kick off a background refresh for next time if it's gone stale.
        engine.ensure_suggestions_refreshing()
        # Equity DOES get a fresh RPC read every time (single eth_getBalance
        # call, fast — nothing like the trending API's rate-limit backoff)
        # so a wallet change made outside the app never shows stale.
        engine.refresh_live_equity()
        return {
            **engine.public_state(),
            "watchlist": engine.config.get("watchlist") or [],
            "suggestions": engine.cached_suggestions(),
        }

    def run_trader_action(self, action: str, payload: dict) -> dict:
        """Executes a real trader action (buy/sell/take-profit/remove-watch)
        on behalf of the phone dashboard. Called from a worker thread (see
        DashboardServer's /api/trader/action handler), never the Qt GUI
        thread directly — safe because TraderEngine's own event callback
        (see TraderPanel._on_engine_event) already marshals every resulting
        UI update onto the GUI thread via a Qt signal, exactly like the
        desktop panel's own background-thread button handlers do."""
        if self._trader_panel is None:
            return {"ok": False, "message": "trader panel not open on the PC yet"}
        engine = self._trader_panel.engine
        symbol = (payload.get("symbol") or "").strip().upper()
        if action == "buy":
            entry = (payload.get("entry") or "").strip()
            if not entry:
                return {"ok": False, "message": "missing entry (SYM:CHAIN:0xADDR)"}
            return engine.buy_one(entry)
        if action == "sell":
            pct = payload.get("pct", 100)
            if payload.get("force"):
                return engine.sell_one(symbol, True)
            if pct == 100:
                return engine.sell_one(symbol)
            return engine.partial_sell(symbol, pct)
        if action == "take_profit":
            pct = payload.get("pct")
            if pct is None:
                return {"ok": False, "message": "missing pct"}
            return engine.partial_sell(symbol, pct)
        if action == "remove_watch":
            return engine.remove_watch(symbol)
        if action == "add_watch":
            entry = (payload.get("entry") or "").strip()
            if not entry:
                return {"ok": False, "message": "missing entry (SYM:CHAIN:0xADDR)"}
            return engine.add_watch(entry)
        if action == "command":
            text = (payload.get("text") or "").strip()
            if not text:
                return {"ok": False, "message": "empty command"}
            return engine.command(text)
        return {"ok": False, "message": f"unknown action: {action}"}

    def set_left_panel_extra(self, widget: QWidget | None):
        """Mounts (or clears) content in the left sidebar's otherwise-empty
        stretch region — used by TraderPanel to put its config controls
        there while it's the active center view. Re-parenting a QWidget
        into a new layout automatically removes it from wherever it was
        before, so this is safe to call repeatedly with the same widget."""
        while self._left_extra_layout.count():
            item = self._left_extra_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        if widget is not None:
            self._left_extra_layout.addWidget(widget)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. OMNI-OS online.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def speech_muted(self) -> bool:
        return self._win._speech_muted

    @speech_muted.setter
    def speech_muted(self, v: bool):
        if v != self._win._speech_muted:
            self._win._toggle_speech_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._current_file

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def voice(self) -> str:
        return self._win.voice

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_companions_changed(self):
        return self._win.on_companions_changed

    @on_companions_changed.setter
    def on_companions_changed(self, cb):
        self._win.on_companions_changed = cb

    @property
    def always_listening(self) -> bool:
        return self._win._always_listening

    @property
    def on_always_listening_toggled(self):
        return self._win.on_always_listening_toggled

    @on_always_listening_toggled.setter
    def on_always_listening_toggled(self, cb):
        self._win.on_always_listening_toggled = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_trader_clicked(self):
        return self._win.on_trader_clicked

    @on_trader_clicked.setter
    def on_trader_clicked(self, cb):
        self._win.on_trader_clicked = cb

    def open_trader_panel(self):
        self._win.open_trader_panel()

    def get_trader_state(self) -> dict | None:
        return self._win.get_trader_state()

    def run_trader_action(self, action: str, payload: dict) -> dict:
        return self._win.run_trader_action(action, payload)

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")
