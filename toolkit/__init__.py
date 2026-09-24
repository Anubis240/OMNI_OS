"""Omni-OS voice tools.

Each module in this package describes one tool the live model can call: a
Gemini function schema plus a `run(args, ctx)` handler, registered with
`@register`. main.py never names individual tools — it builds its tool list
from `schemas()` and dispatches through `get()`.

Importing this package imports every tool module so they all register.
"""

from toolkit.base import ToolContext, register, get, schemas, names  # noqa: F401

# Import order is irrelevant; each module registers itself on import.
from toolkit import (  # noqa: F401,E402
    apps,
    weather,
    search,
    messaging,
    reminders,
    wallpaper,
    vision,
    input_control,
    system_control,
    files,
    coding,
    project_builder,
    documents,
    browser,
    sharing,
)
