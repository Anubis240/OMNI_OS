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

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
PROJECT_DIR = Path(SPECPATH)

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

hiddenimports = [
    # plyer dispatches to platform backends via importlib at runtime —
    # PyInstaller's static analysis can't see those, so they need to be
    # named explicitly or the compiled app silently no-ops on first use.
    "plyer.platforms.win.notification",
    "plyer.platforms.win.filechooser",
    "plyer.platforms.win.audio",
    # win32com/pywin32 COM support (win10toast, image_generator's Explorer
    # integration if any) sometimes needs its genpy cache pre-seeded.
    "win32timezone",
    # google-genai's websocket transport.
    "websockets",
    # web3/eth-* stack — eth_account's key backends are looked up dynamically.
    "eth_account",
    "eth_account.hdaccount",
]

a = Analysis(
    ["main.py"],
    pathex=[str(PROJECT_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

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
    icon=str(PROJECT_DIR / "assets" / "icon.ico"),
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
