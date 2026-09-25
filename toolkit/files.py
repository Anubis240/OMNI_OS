"""file_controller — everyday file and folder work inside the user's home.

Every path goes through places.resolve() and must stay under the home
folder. Deleting always goes to the Recycle Bin/Trash (never permanent),
and both delete and move need the user's OK in a real dialog (security
audit 2026-09-12, Critical #1).
"""

from __future__ import annotations

import os
import shutil
from datetime import date, datetime
from pathlib import Path

from toolkit import places
from toolkit.base import ToolContext, register, S, I

_READ_LIMIT = 4000
_FIND_LIMIT = 30
_SCAN_LIMIT = 20000   # files examined per find/largest walk


class _Refused(Exception):
    """A user-facing 'no' — its message is returned as the tool result."""


def _target(args: dict, key: str = "path") -> Path:
    base = places.resolve(args.get(key))
    name = (args.get("name") or "").strip() if key == "path" else ""
    path = base / name if name else base
    if not places.within_home(path):
        raise _Refused(f"{path} is outside your home folder, so I won't touch it.")
    return path


def _confirmed(ctx: ToolContext, question: str) -> bool:
    ask = getattr(ctx.ui, "confirm_action", None)
    return bool(callable(ask) and ask(question))


def _walk_files(root: Path):
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            seen += 1
            if seen > _SCAN_LIMIT:
                return
            yield Path(dirpath) / fname


def _list(args, ctx):
    path = _target(args)
    if not path.is_dir():
        return f"{path} isn't a folder."
    entries = sorted((p for p in path.iterdir() if not p.name.startswith(".")),
                     key=lambda p: (not p.is_dir(), p.name.lower()))
    if not entries:
        return f"{path} is empty."
    rows = [f"{p.name}/" if p.is_dir() else f"{p.name} — {places.human_size(p.stat().st_size)}"
            for p in entries[:100]]
    more = f"\n…and {len(entries) - 100} more" if len(entries) > 100 else ""
    return f"{path} ({len(entries)} items):\n" + "\n".join(rows) + more


def _create_file(args, ctx):
    path = _target(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.get("content") or "", encoding="utf-8")
    return f"File created at: {path}"


def _create_folder(args, ctx):
    path = _target(args)
    path.mkdir(parents=True, exist_ok=True)
    return f"Folder created at: {path}"


def _write(args, ctx):
    path = _target(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if args.get("append") else "w", encoding="utf-8") as fh:
        fh.write(args.get("content") or "")
    return f"Saved to: {path}"


def _read(args, ctx):
    path = _target(args)
    if not path.is_file():
        return f"There's no file at {path}."
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > _READ_LIMIT:
        return text[:_READ_LIMIT] + f"\n\n[showing the first {_READ_LIMIT} of {len(text)} characters]"
    return text


def _delete(args, ctx):
    path = _target(args)
    if not path.exists():
        return f"Nothing exists at {path}."
    if places.is_user_root(path):
        return f"{path} is one of your main folders — I won't delete it."
    if not _confirmed(ctx, f"Move \"{path}\" to the Recycle Bin?"):
        return "Not deleted — the user didn't confirm."
    from send2trash import send2trash
    send2trash(str(path))
    return f"Moved to the Recycle Bin: {path}"


def _relocate(args, ctx, *, move: bool):
    src = _target(args)
    if not src.exists():
        return f"Nothing exists at {src}."
    if not (args.get("destination") or "").strip():
        return "Where should it go?"
    dest = _target(args, "destination")
    if dest.is_dir():
        dest = dest / src.name
    if dest.exists():
        return f"{dest} already exists."
    if move:
        if places.is_user_root(src):
            return f"{src} is one of your main folders — I won't move it."
        if not _confirmed(ctx, f"Move \"{src}\" to \"{dest.parent}\"?"):
            return "Not moved — the user didn't confirm."
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return f"Moved to: {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    (shutil.copytree if src.is_dir() else shutil.copy2)(str(src), str(dest))
    return f"Copied to: {dest}"


def _rename(args, ctx):
    path = _target(args)
    new_name = (args.get("new_name") or "").strip()
    if not path.exists():
        return f"Nothing exists at {path}."
    if not new_name or any(sep in new_name for sep in "/\\"):
        return "Give me just the new name, without a folder."
    dest = path.with_name(new_name)
    if dest.exists():
        return f"Something called {new_name} is already there."
    path.rename(dest)
    return f"Renamed to: {dest}"


def _find(args, ctx):
    root = places.resolve(args.get("path"), default="home")
    if not places.within_home(root):
        return f"{root} is outside your home folder."
    term = (args.get("name") or "").lower()
    ext = (args.get("extension") or "").lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    hits = []
    for f in _walk_files(root):
        if (not term or term in f.name.lower()) and (not ext or f.suffix.lower() == ext):
            hits.append(f)
            if len(hits) >= _FIND_LIMIT:
                break
    if not hits:
        return f"No matching files under {root}."
    return f"{len(hits)} match(es):\n" + "\n".join(str(h) for h in hits)


def _largest(args, ctx):
    root = places.resolve(args.get("path"), default="downloads")
    if not places.within_home(root):
        return f"{root} is outside your home folder."
    count = max(1, min(int(args.get("count") or 10), 50))
    sized = []
    for f in _walk_files(root):
        try:
            sized.append((f.stat().st_size, f))
        except OSError:
            continue
    sized.sort(reverse=True)
    if not sized:
        return f"No files under {root}."
    return f"Largest files under {root}:\n" + "\n".join(
        f"{places.human_size(size):>10}  {f}" for size, f in sized[:count])


def _disk_usage(args, ctx):
    root = places.resolve(args.get("path"), default="home")
    total, used, free = shutil.disk_usage(root)
    return (f"Drive holding {root}: {places.human_size(used)} used of {places.human_size(total)} "
            f"({used / total:.0%}), {places.human_size(free)} free.")


def _info(args, ctx):
    path = _target(args)
    if not path.exists():
        return f"Nothing exists at {path}."
    st = path.stat()
    stamp = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")  # noqa: E731
    kind = "folder" if path.is_dir() else (path.suffix[1:].upper() + " file" if path.suffix else "file")
    return (f"{path}\n{kind}, {places.human_size(st.st_size)}\n"
            f"modified {stamp(st.st_mtime)}, created {stamp(st.st_ctime)}")


def _is_loose(item: Path) -> bool:
    """A plain, visible file that isn't a shortcut — what tidying may move."""
    return item.is_file() and not item.name.startswith(".") and item.suffix.lower() not in places.SHORTCUT_SUFFIXES


def _unused_path(path: Path) -> Path:
    """`path` itself, or 'name (2).ext', 'name (3).ext' … if that's taken."""
    candidate, n = path, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
    return candidate


def tidy(folder: Path, by: str = "type") -> str:
    """Move the loose files in `folder` into sub-folders: by file type, by
    month modified ("date"), or all into one dated "Swept" folder ("sweep").
    Folders, hidden files and shortcuts stay put. A name that's already
    taken where a file is going gets a numbered copy, never an overwrite."""
    swept = f"Swept {date.today():%Y-%m-%d}"
    buckets = {
        "date": lambda item: datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m"),
        "sweep": lambda item: swept,
    }
    bucket_of = buckets.get(by, places.category_of)
    loose = [item for item in folder.iterdir() if _is_loose(item)]
    if not loose:
        return f"There are no loose files in {folder}."
    for item in loose:
        destination = folder / bucket_of(item)
        destination.mkdir(exist_ok=True)
        shutil.move(str(item), str(_unused_path(destination / item.name)))
    if by == "sweep":
        return f"Moved {len(loose)} loose file(s) into '{swept}' in {folder}."
    return f"Sorted {len(loose)} file(s) in {folder} by {by}."


def _organize_desktop(args, ctx):
    return tidy(places.folder("desktop"))


_ACTIONS = {
    "list": _list, "create_file": _create_file, "create_folder": _create_folder,
    "write": _write, "read": _read, "delete": _delete,
    "move": lambda a, c: _relocate(a, c, move=True),
    "copy": lambda a, c: _relocate(a, c, move=False),
    "rename": _rename, "find": _find, "largest": _largest,
    "disk_usage": _disk_usage, "info": _info, "organize_desktop": _organize_desktop,
}


@register(
    "file_controller",
    "Works with files and folders under the user's home: list, create, read, write, "
    "delete (to the Recycle Bin), move, copy, rename, find, largest files, disk "
    "usage, file info, and sorting the desktop. Folder keywords like 'desktop' or "
    "'downloads' can start a path.",
    {
        "action": S(" | ".join(_ACTIONS)),
        "path": S("A full path, or just the name of a standard folder (desktop, downloads, documents, pictures, music, videos, home), optionally followed by a file name"),
        "name": S("File name inside `path` (or the search term for find)"),
        "destination": S("Where to move/copy to"),
        "new_name": S("New name for rename"),
        "content": S("Text for create_file/write"),
        "extension": S("File extension to find, e.g. .pdf"),
        "count": I("How many results for largest (default 10)"),
    },
    ["action"],
)
def file_controller(args: dict, ctx: ToolContext) -> str:
    action = (args.get("action") or "").strip().lower()
    handler = _ACTIONS.get(action)
    if handler is None:
        return f"Unknown file action {action!r}. Options: {', '.join(_ACTIONS)}."
    ctx.log(f"[file] {action} {args.get('name') or args.get('path') or ''}".rstrip())
    try:
        return handler(args, ctx)
    except _Refused as no:
        return str(no)
    except PermissionError:
        return "Windows denied access to that location."
    except Exception as err:
        return f"That didn't work: {err}"
