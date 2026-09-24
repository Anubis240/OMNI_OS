"""Where the user's folders really are, and how tools turn spoken paths
into real ones.

Windows folders come from the shell (SHGetKnownFolderPath), so a Desktop
or Documents folder redirected into OneDrive resolves correctly. Linux
reads ~/.config/user-dirs.dirs. Everything else is ~/<Name>.
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

# Known-folder GUIDs (KNOWNFOLDERID) for the folders tools talk about.
_WINDOWS_IDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}
_XDG_KEYS = {
    "desktop": "XDG_DESKTOP_DIR", "documents": "XDG_DOCUMENTS_DIR",
    "downloads": "XDG_DOWNLOAD_DIR", "pictures": "XDG_PICTURES_DIR",
    "music": "XDG_MUSIC_DIR", "videos": "XDG_VIDEOS_DIR",
}
FOLDER_NAMES = ("desktop", "documents", "downloads", "pictures", "music", "videos")


def _windows_folder(guid: str) -> Path | None:
    import ctypes
    from ctypes import wintypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    raw = uuid.UUID(guid).bytes_le
    g = GUID.from_buffer_copy(raw)
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def _xdg_folder(key: str) -> Path | None:
    if os.environ.get(key):
        return Path(os.environ[key])
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            m = re.match(rf'\s*{key}="(.*)"', line)
            if m:
                return Path(m.group(1).replace("$HOME", str(Path.home())))
    except OSError:
        pass
    return None


@lru_cache(maxsize=None)
def folder(name: str) -> Path:
    """One of FOLDER_NAMES, or 'home'."""
    name = name.lower()
    if name == "home":
        return Path.home()
    found = None
    try:
        if sys.platform == "win32":
            found = _windows_folder(_WINDOWS_IDS[name])
        elif sys.platform.startswith("linux"):
            found = _xdg_folder(_XDG_KEYS[name])
    except Exception:
        found = None
    return found if found and found.exists() else Path.home() / name.capitalize()


def resolve(spoken: str | None, default: str = "desktop") -> Path:
    """Turn what the model passed into a concrete path.

    'downloads' → the Downloads folder; 'desktop/notes.txt' → inside it;
    an absolute path or '~/x' as given. A bare relative name ('OmniTest')
    has no meaningful anchor — the app's working directory is its install
    folder, where a file would effectively vanish — so it goes on the
    Desktop. A leading folder keyword is honoured rather than nested, so
    'Desktop/OmniTest' doesn't become Desktop/Desktop/OmniTest.
    """
    text = (spoken or "").strip().strip('"') or default
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    head, *rest = path.parts
    if head.lower() in FOLDER_NAMES or head.lower() == "home":
        return folder(head).joinpath(*rest)
    return folder("desktop") / path


def within_home(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(Path.home().resolve())
    except (OSError, ValueError):
        return False


def is_user_root(path: Path) -> bool:
    """True for home itself and the standard user folders — never deleted."""
    try:
        target = path.resolve()
    except OSError:
        return False
    return target == Path.home().resolve() or any(target == folder(n).resolve() for n in FOLDER_NAMES)


def human_size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# File categories used when tidying a folder.
CATEGORIES = {
    "Images": "jpg jpeg png gif bmp webp svg ico heic tif tiff",
    "Documents": "pdf doc docx txt rtf md odt xls xlsx ods csv ppt pptx odp",
    "Video": "mp4 mkv mov avi wmv webm m4v flv",
    "Audio": "mp3 wav flac aac ogg m4a wma",
    "Archives": "zip rar 7z tar gz bz2 xz",
    "Code": "py js ts jsx tsx html css json xml yaml yml c cpp h cs java go rs php rb sh ps1",
    "Installers": "exe msi dmg pkg deb rpm appimage",
}
_BY_EXTENSION = {f".{ext}": cat for cat, exts in CATEGORIES.items() for ext in exts.split()}
SHORTCUT_SUFFIXES = {".lnk", ".url", ".desktop", ".webloc"}


def category_of(path: Path) -> str:
    return _BY_EXTENSION.get(path.suffix.lower(), "Other")
