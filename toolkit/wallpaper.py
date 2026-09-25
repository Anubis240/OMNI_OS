"""desktop_control — wallpaper and desktop housekeeping.

(A free-form "task" action that generated and exec()'d Python was removed
in the 2026-09-12 security audit and is deliberately not offered here.)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from toolkit import places
from toolkit.base import ToolContext, register, S
from toolkit.files import tidy

_IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def apply_wallpaper(image: Path) -> None:
    image = image.resolve()
    if sys.platform == "win32":
        import ctypes
        if image.suffix.lower() == ".webp":  # the Windows wallpaper API won't take WebP
            from PIL import Image
            converted = image.with_suffix(".jpg")
            Image.open(image).convert("RGB").save(converted, "JPEG", quality=95)
            image = converted
        SPI_SETDESKWALLPAPER, UPDATE_AND_BROADCAST = 0x0014, 0x01 | 0x02
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, str(image), UPDATE_AND_BROADCAST):
            raise ctypes.WinError()
    elif sys.platform == "darwin":
        script = f'tell application "System Events" to set picture of every desktop to POSIX file "{image}"'
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
    else:
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        uri = image.as_uri()
        if any(d in desktop for d in ("gnome", "unity", "cinnamon", "budgie")):
            for key in ("picture-uri", "picture-uri-dark"):
                subprocess.run(["gsettings", "set", "org.gnome.desktop.background", key, uri], capture_output=True)
        elif "kde" in desktop:
            js = ("desktops().forEach(function(d){d.wallpaperPlugin='org.kde.image';"
                  "d.currentConfigGroup=['Wallpaper','org.kde.image','General'];"
                  f"d.writeConfig('Image','{uri}');}});")
            subprocess.run(["qdbus", "org.kde.plasmashell", "/PlasmaShell",
                            "org.kde.PlasmaShell.evaluateScript", js], check=True, capture_output=True)
        elif shutil.which("feh"):
            subprocess.run(["feh", "--bg-fill", str(image)], check=True, capture_output=True)
        else:
            raise RuntimeError(f"don't know how to set the wallpaper on {desktop or 'this desktop'}")


def _saved_copy(url: str) -> Path:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    suffix = suffix if suffix in _IMAGE_TYPES else ".jpg"
    # Kept (not a temp file): Windows reads the image again after a reboot.
    dest = places.folder("pictures") / "Omni-OS wallpapers"
    dest.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=suffix, prefix="wallpaper-", dir=dest)
    os.close(fd)
    with urllib.request.urlopen(url, timeout=30) as resp, open(name, "wb") as out:
        shutil.copyfileobj(resp, out)
    return Path(name)


def _current_wallpaper() -> str:
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
            return winreg.QueryValueEx(key, "WallPaper")[0] or "none set"
    if sys.platform == "darwin":
        out = subprocess.run(["osascript", "-e", 'tell application "System Events" to get picture of desktop 1'],
                             capture_output=True, text=True)
        return out.stdout.strip() or "unknown"
    out = subprocess.run(["gsettings", "get", "org.gnome.desktop.background", "picture-uri"],
                         capture_output=True, text=True)
    return out.stdout.strip().strip("'") or "unknown"


def _desktop_summary(detailed: bool) -> str:
    desk = places.folder("desktop")
    items = [p for p in desk.iterdir() if not p.name.startswith(".")]
    files = [p for p in items if p.is_file()]
    folders = [p for p in items if p.is_dir()]
    size = sum(p.stat().st_size for p in files)
    head = f"Desktop ({desk}): {len(files)} files ({places.human_size(size)}), {len(folders)} folders."
    if not detailed:
        return head
    names = [f"{p.name}/" for p in sorted(folders)] + [p.name for p in sorted(files)]
    return head + "\n" + "\n".join(names[:80])


@register(
    "desktop_control",
    "Desktop wallpaper and housekeeping: set the wallpaper from a file or image URL, "
    "say what the current wallpaper is, sort the desktop into folders, archive loose "
    "files, or list/summarise what's on the desktop.",
    {
        "action": S("wallpaper | wallpaper_url | current_wallpaper | organize | clean | list | stats"),
        "path": S("Image file for 'wallpaper'"),
        "url": S("Image URL for 'wallpaper_url'"),
        "mode": S("For organize: by_type (default) or by_date"),
    },
    ["action"],
)
def desktop_control(args: dict, ctx: ToolContext) -> str:
    action = (args.get("action") or "").strip().lower()
    ctx.log(f"[desktop] {action}")
    try:
        if action == "wallpaper":
            image = places.resolve(args.get("path"), default="pictures")
            if not image.is_file():
                return f"There's no image at {image}."
            if image.suffix.lower() not in _IMAGE_TYPES:
                return "The wallpaper has to be a JPG, PNG, BMP or WebP image."
            apply_wallpaper(image)
            return f"Wallpaper changed to {image.name}."
        if action == "wallpaper_url":
            if not (args.get("url") or "").startswith(("http://", "https://")):
                return "I need a web address for the image."
            image = _saved_copy(args["url"])
            apply_wallpaper(image)
            return f"Wallpaper changed (saved as {image})."
        if action == "current_wallpaper":
            return f"The current wallpaper is {_current_wallpaper()}."
        if action == "organize":
            return tidy(places.folder("desktop"), by="date" if args.get("mode") == "by_date" else "type")
        if action == "clean":
            return tidy(places.folder("desktop"), by="sweep")
        if action in ("list", "stats"):
            return _desktop_summary(detailed=action == "list")
    except Exception as err:
        return f"That didn't work: {err}"
    return "Options are wallpaper, wallpaper_url, current_wallpaper, organize, clean, list and stats."
