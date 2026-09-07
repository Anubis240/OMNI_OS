"""Frozen Linux native-library locations; no host installation or downloads."""

import ctypes.util
import os
from pathlib import Path
import sys


if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
    root = Path(sys._MEIPASS)
    os.environ["GIO_MODULE_DIR"] = str(root / "gio" / "modules")
    os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] = str(root / "gstreamer-1.0")
    os.environ["GST_PLUGIN_PATH_1_0"] = str(root / "gstreamer-1.0")
    scanners = sorted(root.glob("gst-helpers/**/gst-plugin-scanner"))
    if scanners:
        os.environ["GST_PLUGIN_SCANNER"] = str(scanners[0])
    # sounddevice asks the host ldconfig cache for PortAudio. The bundled SONAME
    # is intentionally not registered there, so redirect only this import's
    # lookup; restore ctypes immediately, including on failure.
    original_find_library = ctypes.util.find_library

    def bundled_find_library(name):
        if name == "portaudio":
            return str(root / "libportaudio.so.2")
        return original_find_library(name)

    ctypes.util.find_library = bundled_find_library
    try:
        import sounddevice
    finally:
        ctypes.util.find_library = original_find_library
