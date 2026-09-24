"""share_file — turn a local file into a link the phone dashboard can open."""

from __future__ import annotations

import asyncio
from pathlib import Path

from toolkit import places
from toolkit.base import ToolContext, register, S


@register(
    "share_file",
    "Gives the user a clickable link to a local file Omni just made (a page, "
    "document, image…), so it can be opened from the phone dashboard. Always use "
    "this instead of reading out a file path when the user asks for a link or a "
    "phone is connected — file paths don't work on a phone.",
    {"path": S("Full path to the file (a folder keyword like 'desktop/report.html' also works)")},
    ["path"],
)
def share_file(args: dict, ctx: ToolContext) -> str:
    path = places.resolve(args.get("path"))
    if ctx.dashboard is None:
        return (f"The file is at {path} on the PC. The Remote Dashboard isn't running, so there's "
                "no link to share — just tell the user where it's saved.")
    url = ctx.dashboard.register_file(str(path))
    if not url:
        return f"There's no file at {path} to share."
    ctx.log(f'SYS: <a href="{url}">Open {Path(path).name}</a>')
    if ctx.loop is not None:
        message = {"type": "link", "url": url, "label": Path(path).name}
        asyncio.run_coroutine_threadsafe(ctx.dashboard.broadcast(message), ctx.loop)
    return ("The link is on screen now (and on the phone if one is connected). Tell the user it's "
            "ready to click — don't read the link or any part of it aloud.")
