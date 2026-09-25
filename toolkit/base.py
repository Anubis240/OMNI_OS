"""Tool registry and the context object handed to every tool handler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolContext:
    """What a tool may touch in the running app.

    Every field is optional so tools stay callable from tests and from the
    background task runner, which has no window.
    """

    ui: Any = None                      # gui.facade.OmniUI, or None when headless
    speak: Callable[[str], None] | None = None
    namespace: str | None = None        # active companion's memory namespace
    dashboard: Any = None
    loop: Any = None                    # the live session's asyncio loop
    extras: dict = field(default_factory=dict)

    def log(self, text: str) -> None:
        """One line in the app's activity log (and the console)."""
        print(text)
        if self.ui is not None:
            try:
                self.ui.post(text)
            except Exception:
                pass

    def say(self, text: str) -> None:
        if self.speak is not None:
            try:
                self.speak(text)
            except Exception:
                pass

    @property
    def attached_file(self) -> str | None:
        return getattr(self.ui, "attached_file", None) if self.ui is not None else None


@dataclass(frozen=True)
class Tool:
    name: str
    schema: dict
    handler: Callable[[dict, ToolContext], str]
    # Handlers that block (network, subprocess, GUI automation) run in a
    # worker thread; voice/dispatch.py reads this to decide.
    blocking: bool = True


_REGISTRY: dict[str, Tool] = {}


def register(name: str, description: str, properties: dict | None = None,
             required: list[str] | None = None, *, blocking: bool = True):
    """Decorator: `@register("open_app", "…", {...}, ["app_name"])`.

    `properties` uses Gemini's schema dialect (type names in upper case).
    """
    def wrap(fn: Callable[[dict, ToolContext], str]):
        schema = {
            "name": name,
            "description": description,
            "parameters": {"type": "OBJECT", "properties": properties or {}},
        }
        if required:
            schema["parameters"]["required"] = list(required)
        if name in _REGISTRY:
            raise ValueError(f"tool {name!r} registered twice")
        _REGISTRY[name] = Tool(name, schema, fn, blocking)
        return fn
    return wrap


def get(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def names() -> list[str]:
    return sorted(_REGISTRY)


def schemas() -> list[dict]:
    return [t.schema for t in _REGISTRY.values()]


def run(name: str, args: dict, ctx: ToolContext) -> str:
    """Synchronous dispatch, used by the background task runner."""
    tool = _REGISTRY.get(name)
    if tool is None:
        return f"Unknown tool: {name}"
    return tool.handler(dict(args or {}), ctx) or "Done."


# Small shared helpers ------------------------------------------------------

def S(desc: str) -> dict:
    return {"type": "STRING", "description": desc}


def I(desc: str) -> dict:
    return {"type": "INTEGER", "description": desc}


def N(desc: str) -> dict:
    return {"type": "NUMBER", "description": desc}


def B(desc: str) -> dict:
    return {"type": "BOOLEAN", "description": desc}
