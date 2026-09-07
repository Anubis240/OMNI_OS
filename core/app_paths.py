"""Bundle resources and persistent application data (no directory creation)."""

import os
import platform
import sys
from pathlib import Path


def get_resource_dir() -> Path:
    """Read-only bundled assets, or the repository root in source runs."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    """Keep source/Windows data in place; never write inside Unix bundles."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent
    if sys.platform == "win32":
        return Path(sys.executable).resolve().parent
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Omni-OS"
    xdg_home = os.environ.get("XDG_DATA_HOME", "")
    root = Path(xdg_home) if xdg_home else Path.home() / ".local" / "share"
    if not root.is_absolute():
        root = Path.home() / ".local" / "share"
    return root / "Omni-OS"


def get_os_name() -> str:
    """Names used by the existing user settings; explicit settings win."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux"):
        return "linux"
    return {"Windows": "windows", "Darwin": "mac"}.get(platform.system(), "linux")
