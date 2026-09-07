"""Offline frozen-bundle probe; deliberately never imports the application."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

from core.app_paths import get_resource_dir


def bundle_boundary():
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent
        raise RuntimeError("Frozen macOS executable must be inside an .app")
    return executable.parent


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true", required=True)
    parser.add_argument("--smoke-report", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(get_resource_dir()).resolve()
    boundary = bundle_boundary() if getattr(sys, "frozen", False) else root
    report_path = args.smoke_report.resolve()
    if not args.smoke_report.is_absolute() or report_path.is_relative_to(boundary):
        parser.error("--smoke-report must be absolute and outside the entire bundle")

    report = {"ok": False, "checks": [], "executable": str(sys.executable)}
    try:
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Run the packaged executable, not Python source")
        if not root.is_relative_to(boundary):
            raise RuntimeError("Resources resolved outside the bundle")
        report["resource_dir"] = str(root)
        report["bundle_boundary"] = str(boundary)
        if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") != "0":
            raise RuntimeError("Frozen entry point must enable package-local browsers")
        for relative in (
            "core/prompt.txt", "core/shared_rules.txt",
            "leda_idle_timeline1.mp4", "leda_speaking.mp4",
            "dashboard/static/avatar_bg.jpg",
        ):
            asset = root / relative
            if (not asset.resolve().is_relative_to(boundary)
                    or not asset.is_file() or asset.stat().st_size == 0):
                raise FileNotFoundError(f"Missing/empty asset: {relative}")
        icons = root / "assets" / "integration_icons"
        if (not icons.resolve().is_relative_to(boundary) or not icons.is_dir()
                or not any(p.is_file() for p in icons.rglob("*"))
                or any(not p.resolve().is_relative_to(boundary) for p in icons.rglob("*"))):
            raise FileNotFoundError("Missing integration icons")
        report["checks"].append("assets")

        # Static imports allow PyInstaller to discover the native dependencies.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtMultimedia import QMediaPlayer
        import sounddevice
        from google import genai
        import uvicorn
        from web3 import Web3
        import numpy as np
        import onnxruntime
        import nudenet
        from nudenet import NudeDetector
        from playwright.sync_api import sync_playwright

        report["portaudio"] = sounddevice.get_portaudio_version()
        report["onnx_providers"] = onnxruntime.get_available_providers()
        report["checks"].append("imports")
        app = QApplication([])
        player = QMediaPlayer()
        app.processEvents()
        del player
        app.quit()
        report["checks"].append("qt_offscreen")
        report["qt_platform"] = os.environ["QT_QPA_PLATFORM"]

        model = Path(nudenet.__file__).resolve().parent / "320n.onnx"
        if not model.is_relative_to(boundary) or not model.is_file():
            raise FileNotFoundError("Missing bundled NudeNet 320n.onnx")
        detector = NudeDetector(model_path=str(model), providers=["CPUExecutionProvider"])
        detector.detect(np.zeros((320, 320, 3), dtype=np.uint8))
        report["checks"].append("nudenet_inference")

        with tempfile.TemporaryDirectory(prefix="omni-bundle-smoke-") as temporary:
            temporary = Path(temporary)
            with sync_playwright() as playwright:
                for name in ("chromium", "firefox", "webkit"):
                    engine = getattr(playwright, name)
                    executable = Path(engine.executable_path).resolve()
                    if not executable.is_relative_to(boundary) or not executable.is_file():
                        raise FileNotFoundError(f"{name} did not resolve to a bundled executable")
                    context = engine.launch_persistent_context(
                        str(temporary / name), headless=True, offline=True,
                        service_workers="block", timeout=60_000,
                    )
                    try:
                        context.route("http://**/*", lambda route: route.abort())
                        context.route("https://**/*", lambda route: route.abort())
                        page = context.new_page()
                        # WebKit cannot navigate file:// in an offline context.
                        page.set_content(
                            "<!doctype html><title>OMNI smoke</title><h1>offline</h1>",
                            timeout=30_000,
                        )
                        if page.title() != "OMNI smoke":
                            raise RuntimeError(f"{name}: local HTML did not render")
                    finally:
                        context.close()
                    report["checks"].append(f"browser_{name}")
        report["ok"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()

    # Failure to write also propagates as a nonzero exit; CI requires the report.
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1
