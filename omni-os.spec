# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Omni-OS — build with:
#   .venv312\Scripts\pyinstaller.exe omni-os.spec --noconfirm
#
# --onedir (not --onefile): a --onefile build re-extracts everything to a
# temp dir on every launch (slow startup for an app this size — PyQt6,
# onnxruntime, web3, torch-free but still heavy) and, more importantly,
# several paths in this app (dashboard cert persistence, config files) are
# written relative to the real install directory via a getattr(sys,
# "frozen")-aware BASE_DIR helper (see ui.py/main.py/dashboard/server.py) —
# --onefile's temp extraction dir would break that persistence across runs.
#
# Deliberately NOT bundling config/*.json, config/certs/, or config/trader/
# — those are the developer's own personal data (Gemini key, Claude Code
# CLI paths, trader ledger). Every one of them is created fresh on first
# run by the app's own code; shipping them would leak Lee's own paths/keys
# into anyone else's install.

import os
import re
import sys
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_all, copy_metadata

block_cipher = None
PROJECT_DIR = Path(SPECPATH)
APP_VERSION = os.environ.get("APP_VERSION", "1.10.3")
if os.environ.get("CI") and "APP_VERSION" not in os.environ:
    raise RuntimeError("CI must supply validated APP_VERSION")
if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", APP_VERSION):
    raise RuntimeError("APP_VERSION must be numeric X.Y.Z")

# The official Playwright hook collects these package-local browser installs.
# Install all three engines with this same environment before building.
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
import playwright
import nudenet

browser_dir = Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
for engine in ("chromium", "firefox", "webkit"):
    if not any(path.is_dir() for path in browser_dir.glob(f"{engine}-*")):
        raise RuntimeError(f"Missing bundled {engine}; run python -m playwright install with PLAYWRIGHT_BROWSERS_PATH=0")
if not (Path(nudenet.__file__).parent / "320n.onnx").is_file():
    raise RuntimeError("NudeNet's packaged 320n.onnx model is required")

binaries = []

datas = [
    (str(PROJECT_DIR / "core" / "prompt.txt"), "core"),
    (str(PROJECT_DIR / "core" / "shared_rules.txt"), "core"),
    (str(PROJECT_DIR / "leda_idle_timeline1.mp4"), "."),
    (str(PROJECT_DIR / "leda_speaking.mp4"), "."),
    (str(PROJECT_DIR / "dashboard" / "static" / "avatar_bg.jpg"), "dashboard/static"),
    # Per-service brand icons for the Integrations tab — integrations_panel.py
    # resolves these relative to its own frozen module dir at runtime, same
    # BASE_DIR pattern as everything else in this spec.
    (str(PROJECT_DIR / "assets" / "integration_icons"), "assets/integration_icons"),
]
# eth_account.hdaccount reads its BIP-39 wordlist .txt files (english.txt,
# etc.) off disk at runtime for wallet/mnemonic generation — same
# non-code-data blind spot as openwakeword above. Missing this doesn't
# fail at import time (eth_account.hdaccount is already a hiddenimport
# below); it only surfaces the instant a wallet is actually created,
# which is exactly how this was found — a real "create wallet" click in
# the packaged trader panel throwing FileNotFoundError.
datas += collect_data_files("eth_account")
# py_ecc queries its installed version while Web3 imports eth_keyfile.
datas += copy_metadata("py-ecc")

# actions/image_generator.py's local NSFW safety gate (nudenet + onnxruntime).
# onnxruntime has a well-documented frozen-build failure mode ("DLL load
# failed" importing onnxruntime_pybind11_state) if its native DLLs aren't
# collected explicitly — collect_all is the community-standard fix (same
# effect as `pyinstaller --collect-all onnxruntime`). nudenet ships its own
# 320n.onnx model file as package data, same non-code-data blind spot as
# eth_account's wordlists above — collect_data_files pulls it in the same
# way. Both collected here were verified by actually building and running a
# frozen test executable (2026-09-07), not assumed from documentation alone.
onnxruntime_datas, onnxruntime_binaries, onnxruntime_hiddenimports = collect_all("onnxruntime")
datas += onnxruntime_datas
binaries += onnxruntime_binaries
datas += collect_data_files("nudenet")

# The SDK's wheel ships its own native CLI. Collect it as a binary (including
# Mach-O signing/dependency processing), not just Python modules. No CLI download.
sdk_spec = importlib.util.find_spec("claude_agent_sdk")
if sdk_spec is not None:
    sdk_root = Path(sdk_spec.origin).parent
    cli = sdk_root / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")
    if not cli.is_file():
        raise RuntimeError("Installed Claude Agent SDK wheel has no bundled native CLI")
    datas += collect_data_files("claude_agent_sdk", excludes=["_bundled/*"])
    binaries.append((str(cli), "claude_agent_sdk/_bundled"))

runtime_hooks = []
if sys.platform.startswith("linux"):
    runtime_hooks.append(str(PROJECT_DIR / "scripts" / "linux_runtime.py"))

hiddenimports = onnxruntime_hiddenimports + [
    # plyer dispatches to platform backends via importlib at runtime —
    # PyInstaller's static analysis can't see those, so they need to be
    # named explicitly or the compiled app silently no-ops on first use.
    # win32com/pywin32 COM support (win10toast, image_generator's Explorer
    # integration if any) sometimes needs its genpy cache pre-seeded.
    # google-genai's websocket transport.
    "websockets",
    # web3/eth-* stack — eth_account's key backends are looked up dynamically.
    "eth_account",
    "eth_account.hdaccount",
]
plyer_platform = {"win32": "win", "darwin": "macosx", "linux": "linux"}[sys.platform]
hiddenimports += [f"plyer.platforms.{plyer_platform}.{name}" for name in ("notification", "filechooser", "audio")]
if sys.platform == "win32":
    hiddenimports.append("win32timezone")

a = Analysis(
    ["main.py"],
    pathex=[str(PROJECT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

if sys.platform.startswith("linux"):
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    from release_bundle import linux_binaries, without_browser_toc, append_linux_browsers
    import PyQt6
    qt_root = Path(PyQt6.__file__).parent.resolve()
    # Qt hooks select QtWidgets/QtMultimedia and their required plugins. Do not
    # seed closure from the whole Qt wheel (including unused QML design tools).
    qt_inputs = [Path(source) for _, source, kind in a.binaries
                 if kind in {"BINARY", "EXTENSION"} and Path(source).resolve().is_relative_to(qt_root)]
    closure = linux_binaries([
        Path(playwright.__file__).parent / "driver",
        sdk_root / "_bundled" if sdk_spec else browser_dir,
        *qt_inputs,
    ], library_paths=[qt_root / "Qt6" / "lib"], library_scope=qt_root,
       browser_root=browser_dir)
    a.binaries += [(str(Path(destination) / Path(source).name), source, "BINARY")
                   for source, destination in closure]
    # ldd discovers external dependencies, not every dlopen payload (libxul).
    # Exclude the whole browser TOC, including generated aliases, then copy
    # complete upstream distributions unchanged after COLLECT.
    a.binaries = without_browser_toc(a.binaries, browser_dir)
    a.datas = without_browser_toc(a.datas, browser_dir)

if sys.platform == "darwin":
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    from release_bundle import without_browser_toc, append_macos_browsers
    # Keep Node and the SDK CLI in normal binary processing. Only the upstream
    # browser distributions bypass relocation and are appended after BUNDLE.
    a.binaries = without_browser_toc(a.binaries, browser_dir)
    a.datas = without_browser_toc(a.datas, browser_dir)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Omni-OS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_DIR / "assets" / "icon.ico") if sys.platform == "win32" else None,
    # PyInstaller 6.x defaults onedir builds to a separate "_internal"
    # subfolder for everything but the .exe itself. This app's own
    # frozen-mode BASE_DIR (see get_base_dir()/_base_dir() in main.py/
    # ui.py/dashboard/server.py) deliberately resolves to sys.executable's
    # own directory — for config/cert persistence, NOT a nested folder —
    # so bundled data (prompt.txt, avatar video/image) needs to live at
    # that same top level, not under _internal. (Belongs on EXE, not
    # COLLECT — verified against PyInstaller's own source after passing
    # it to the wrong one first and seeing no effect.)
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Omni-OS",
)

if sys.platform.startswith("linux"):
    append_linux_browsers(Path(coll.name), browser_dir, closure)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Omni-OS.app",
        bundle_identifier="com.kondux.omnios",
        version=APP_VERSION,
        info_plist={
            "CFBundleShortVersionString": APP_VERSION,
            "NSPrincipalClass": "NSApplication",
            "LSMinimumSystemVersion": "15.0",
            "NSMicrophoneUsageDescription": "Omni-OS uses your microphone for voice conversations when enabled.",
            "NSCameraUsageDescription": "Omni-OS uses your camera for visual assistance when enabled.",
        },
    )
    append_macos_browsers(Path(app.name), browser_dir)
