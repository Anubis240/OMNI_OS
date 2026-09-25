"""browser_control — drive a real, visible browser window with Playwright.

Each browser gets its own Omni-OS profile folder (so sign-ins made there
are remembered) rather than borrowing the user's everyday profile, which is
locked while that browser is open and shouldn't be handed to automation.
Chrome and Edge use the installed browser when present; otherwise, and
for Firefox/WebKit, the Playwright engines bundled with the app are used.
All sessions share one background asyncio loop.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
import threading
import urllib.parse
from pathlib import Path

from core.app_paths import get_data_dir
from toolkit import places
from toolkit.base import ToolContext, register, S, I, B

_TIMEOUT = 60  # seconds per action

# name → (Playwright engine, installed-browser channel or None)
_BROWSERS = {
    "chrome": ("chromium", "chrome"),
    "edge": ("chromium", "msedge"),
    "chromium": ("chromium", None),
    "firefox": ("firefox", None),
    "safari": ("webkit", None),
    "webkit": ("webkit", None),
}
_SPOKEN = {"google chrome": "chrome", "microsoft edge": "edge", "ms edge": "edge", "msedge": "edge",
           "mozilla firefox": "firefox", "opera": "chromium", "opera gx": "chromium", "operagx": "chromium",
           "vivaldi": "chromium", "brave": "chromium"}
_SEARCH = {
    "google": "https://www.google.com/search?q=",
    "bing": "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "yandex": "https://yandex.com/search/?text=",
}


def _canonical(name: str | None) -> str:
    key = (name or "").strip().lower()
    return _SPOKEN.get(key, key)


def _default_browser() -> str:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations"
                                                          r"\UrlAssociations\https\UserChoice") as key:
                prog = winreg.QueryValueEx(key, "ProgId")[0].lower()
            for name in ("edge", "firefox", "chrome"):
                if name in prog or (name == "edge" and "msedgehtm" in prog):
                    return name
        except OSError:
            pass
        return "edge"   # present on every Windows install
    return "chrome" if shutil.which("google-chrome") or sys.platform == "darwin" else "chromium"


def _as_url(text: str) -> str:
    text = text.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I) or text.startswith("about:"):
        return text
    if " " in text or "." not in text and text.lower() != "localhost":
        return _SEARCH["google"] + urllib.parse.quote_plus(text)
    return "https://" + text


class _Loop:
    """One asyncio loop in a daemon thread, shared by every browser."""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="browser-loop", daemon=True).start()
        self.playwright = self.call(self._start_playwright(), 60)

    @staticmethod
    async def _start_playwright():
        from playwright.async_api import async_playwright
        return await async_playwright().start()

    def call(self, coro, timeout=_TIMEOUT):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)


class _Browser:
    def __init__(self, name: str, host: _Loop):
        self.name, self.host = name, host
        self.context = None
        self.page = None

    async def _open(self):
        if self.context is not None:
            return
        engine_name, channel = _BROWSERS[self.name]
        engine = getattr(self.host.playwright, engine_name)
        profile = get_data_dir() / "browser-profiles" / self.name
        profile.mkdir(parents=True, exist_ok=True)
        options = {"headless": False, "no_viewport": True}
        if engine_name == "chromium":
            options["args"] = ["--start-maximized", "--no-first-run", "--no-default-browser-check"]
        if channel:
            try:
                self.context = await engine.launch_persistent_context(str(profile), channel=channel, **options)
            except Exception as err:   # that browser isn't installed — use the bundled engine
                print(f"[browser] {channel} unavailable ({err}); using bundled {engine_name}")
        if self.context is None:
            self.context = await engine.launch_persistent_context(str(profile), **options)
        self.context.on("close", lambda *_: self._forget())
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    def _forget(self):
        self.context = self.page = None

    async def tab(self):
        await self._open()
        if self.page is None or self.page.is_closed():
            live = [p for p in self.context.pages if not p.is_closed()]
            self.page = live[-1] if live else await self.context.new_page()
        return self.page

    # -- actions ---------------------------------------------------------
    async def go_to(self, url):
        page = await self.tab()
        target = _as_url(url or "about:blank")
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
        except Exception as err:
            if page.url in ("about:blank", ""):
                return f"Couldn't load {target}: {err}"
        return f"Now on {page.url} — {await page.title()}"

    async def search(self, query, engine):
        base = _SEARCH.get((engine or "google").lower(), _SEARCH["google"])
        return await self.go_to(base + urllib.parse.quote_plus(query))

    async def _find(self, description):
        page = await self.tab()
        tries = [page.get_by_role(role, name=re.compile(re.escape(description), re.I))
                 for role in ("button", "link", "menuitem", "tab", "checkbox", "option")]
        tries += [page.get_by_label(description), page.get_by_placeholder(description),
                  page.get_by_text(description), page.get_by_title(description), page.get_by_alt_text(description)]
        for locator in tries:
            try:
                if await locator.count():
                    first = locator.first
                    if await first.is_visible():
                        return first
            except Exception:
                continue
        return None

    async def click(self, selector, text):
        page = await self.tab()
        target = page.locator(selector).first if selector else await self._find(text or "")
        if target is None:
            return f"I couldn't find {text!r} on the page."
        await target.click(timeout=8_000)
        await page.wait_for_load_state("domcontentloaded")
        return f"Clicked. Now on {page.url}"

    async def type(self, selector, description, text, clear):
        page = await self.tab()
        if selector:
            target = page.locator(selector).first
        elif description:
            target = await self._find(description)
        else:
            target = page.locator(":focus")
        if target is None:
            return f"I couldn't find a field matching {description!r}."
        if clear:
            await target.fill(text)
        else:
            await target.press_sequentially(text, delay=25)
        return "Typed it."

    async def fill_form(self, fields: dict):
        filled, missed = [], []
        for label, value in (fields or {}).items():
            page = await self.tab()
            target = page.locator(label).first if re.match(r"^[#.\[]", label) else await self._find(label)
            try:
                await target.fill(str(value))
                filled.append(label)
            except Exception:
                missed.append(label)
        return f"Filled {', '.join(filled) or 'nothing'}" + (f"; couldn't find {', '.join(missed)}" if missed else "")

    async def scroll(self, direction, amount):
        page = await self.tab()
        await page.mouse.wheel(0, amount if direction != "up" else -amount)
        return f"Scrolled {direction}."

    async def press(self, key):
        page = await self.tab()
        await page.keyboard.press(key)
        return f"Pressed {key}."

    async def read(self):
        page = await self.tab()
        text = re.sub(r"\n{3,}", "\n\n", await page.inner_text("body"))
        return f"{await page.title()} ({page.url})\n\n{text[:5000]}"

    async def where(self):
        page = await self.tab()
        return page.url

    async def new_tab(self, url):
        await self._open()
        self.page = await self.context.new_page()
        return await self.go_to(url) if url else "Opened a new tab."

    async def close_tab(self):
        page = await self.tab()
        await page.close()
        self.page = None
        return "Closed the tab."

    async def screenshot(self, path):
        page = await self.tab()
        dest = places.resolve(path or f"desktop/page-{page.url.split('//')[-1].split('/')[0]}.png")
        if not places.within_home(dest):
            return "Screenshots can only be saved inside your home folder."
        dest.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(dest))
        return f"Screenshot saved to {dest}"

    async def history(self, step):
        page = await self.tab()
        await (page.go_back() if step < 0 else page.go_forward())
        return f"Now on {page.url}"

    async def reload(self):
        page = await self.tab()
        await page.reload()
        return f"Reloaded {page.url}"

    async def close(self):
        if self.context is not None:
            await self.context.close()
        self._forget()
        return f"Closed {self.name}."


class _Browsers:
    def __init__(self):
        self._host: _Loop | None = None
        self._open: dict[str, _Browser] = {}
        self.active = ""
        self._lock = threading.Lock()

    def host(self) -> _Loop:
        with self._lock:
            if self._host is None:
                self._host = _Loop()
            return self._host

    def get(self, name: str | None) -> _Browser:
        name = _canonical(name) or self.active or _default_browser()
        if name not in _BROWSERS:
            raise ValueError(f"I can't automate {name}. Options: {', '.join(_BROWSERS)}")
        if name not in self._open:
            self._open[name] = _Browser(name, self.host())
        self.active = name
        return self._open[name]

    def run(self, coro):
        return self.host().call(coro)

    def close_all(self) -> str:
        names = list(self._open)
        for b in list(self._open.values()):
            try:
                self.run(b.close())
            except Exception:
                pass
        self._open.clear()
        self.active = ""
        return f"Closed {', '.join(names)}." if names else "No browsers were open."


_browsers = _Browsers()


def _as_fields(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    import json
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@register(
    "browser_control",
    "Controls a real browser window: open sites, search, click things by their "
    "visible text or label, type into fields, fill forms, scroll, press keys, read "
    "the page, manage tabs, screenshots, back/forward/reload. Pass `browser` when "
    "the user names one (chrome, edge, firefox, safari); several can be open at once.",
    {
        "action": S("go_to | search | click | type | smart_click | smart_type | fill_form | scroll | press | "
                    "get_text | get_url | new_tab | close_tab | screenshot | back | forward | reload | "
                    "switch | list_browsers | close | close_all"),
        "browser": S("chrome | edge | firefox | safari | chromium; omit for the current/default one"),
        "url": S("Address for go_to/new_tab"),
        "query": S("Search terms"),
        "engine": S("google (default) | bing | duckduckgo | yandex"),
        "selector": S("CSS selector, if known"),
        "text": S("Visible text to click, or text to type"),
        "description": S("For smart_click/smart_type: the words on or next to the thing to click or type into"),
        "fields": S('fill_form: JSON object of field label (or CSS selector) → value, e.g. {"Email": "a@b.com"}'),
        "direction": S("up | down"),
        "amount": I("Scroll distance in pixels (default 600)"),
        "key": S("Key for press, e.g. Enter, Escape, PageDown"),
        "path": S("Where to save a screenshot"),
        "clear_first": B("Replace a field's contents when typing (default true)"),
    },
    ["action"],
)
def browser_control(args: dict, ctx: ToolContext) -> str:
    action = (args.get("action") or "").strip().lower()
    ctx.log(f"[browser] {action}")
    try:
        if action == "list_browsers":
            open_now = [n + (" (active)" if n == _browsers.active else "") for n in _browsers._open]
            return "Open: " + ", ".join(open_now) if open_now else "No browsers open."
        if action == "close_all":
            return _browsers.close_all()
        if action == "switch":
            _browsers.get(args.get("browser") or args.get("target"))
            return f"Using {_browsers.active} now."
        b = _browsers.get(args.get("browser"))
        text = args.get("text") or ""
        calls = {
            "go_to": lambda: b.go_to(args.get("url") or ""),
            "search": lambda: b.search(args.get("query") or text, args.get("engine")),
            "click": lambda: b.click(args.get("selector"), text or args.get("description")),
            "smart_click": lambda: b.click(None, args.get("description") or text),
            "type": lambda: b.type(args.get("selector"), None, text, args.get("clear_first", True)),
            "smart_type": lambda: b.type(None, args.get("description"), text, args.get("clear_first", True)),
            "fill_form": lambda: b.fill_form(_as_fields(args.get("fields"))),
            "scroll": lambda: b.scroll((args.get("direction") or "down").lower(), int(args.get("amount") or 600)),
            "press": lambda: b.press(args.get("key") or "Enter"),
            "get_text": b.read, "get_url": b.where,
            "new_tab": lambda: b.new_tab(args.get("url") or ""),
            "close_tab": b.close_tab,
            "screenshot": lambda: b.screenshot(args.get("path")),
            "back": lambda: b.history(-1), "forward": lambda: b.history(1),
            "reload": b.reload,
            "close": b.close,
        }
        if action not in calls:
            return f"Unknown browser action {action!r}."
        result = _browsers.run(calls[action]())
        if action == "close":
            _browsers._open.pop(b.name, None)
            _browsers.active = next(iter(_browsers._open), "")
        return result
    except TimeoutError:
        return f"The browser took too long on {action}."
    except Exception as err:
        return f"Browser {action} failed: {err}"
