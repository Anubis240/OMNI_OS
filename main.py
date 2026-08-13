import asyncio
import re
import threading
import json
import sys
import traceback
from pathlib import Path

# print() calls throughout this file use emoji (🔌, 💾, etc.) for readable
# logs. Two separate packaged-build failure modes land on the same fix:
# (1) a console-mode/redirected stream defaults to the OS's legacy codepage
# (cp1252 on most Windows installs) unless PYTHONIOENCODING/PYTHONUTF8 is
# set — never true for a double-clicked .exe — so the first emoji print()
# raises UnicodeEncodeError; (2) a --windowed (console=False) build with no
# redirection gets sys.stdout/stderr = None entirely, so print() raises
# AttributeError instead. Either way the exception silently kills whatever
# thread hit it (the main connection loop, in practice) with no visible
# error, since there's no console to show it in even if there were one.
# Route to a log file next to the exe so this is diagnosable at all, and
# make it UTF-8 so the emoji themselves never crash it again.
if getattr(sys, "frozen", False) and (sys.stdout is None or sys.stderr is None):
    _log_path = Path(sys.executable).parent / "seraph.log"
    _log_f = open(_log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stdout or _log_f
    sys.stderr = sys.stderr or _log_f
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

if sys.platform == "win32":
    # python.exe is a console-subsystem binary, so launching it always pops a
    # console window first, no matter what the app does afterward — hide it
    # immediately rather than relying on how it happens to be launched.
    # No-op if there's no console (e.g. already launched via pythonw.exe).
    import ctypes
    _console_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if _console_hwnd:
        ctypes.windll.user32.ShowWindow(_console_hwnd, 0)  # SW_HIDE

import os
import ssl

import sounddevice as sd

# Some machines run SSL-inspecting antivirus/corporate proxies (Norton, Zscaler,
# etc.) that re-sign HTTPS traffic with a locally-generated root CA. Windows
# trusts that root (it's in the OS certificate store), but google-genai's
# client pins its outbound SSL context to the certifi package's public CA
# bundle unless SSL_CERT_FILE is set (see google/genai/_api_client.py:
# `cafile=os.environ.get('SSL_CERT_FILE', certifi.where())`), and certifi can
# never contain a private, machine-specific MITM root — so the Gemini Live
# websocket fails to verify and can never connect.
#
# Tried `truststore.inject_into_ssl()` here first — it replaces ssl.SSLContext
# process-wide, which fixed the Gemini connection but also broke the Remote
# dashboard's own HTTPS server (dashboard/server.py's uvicorn instance), since
# truststore's context only implements client-side verification and silently
# kills the TLS handshake when used server-side (confirmed: phone connections
# failed with "server closed abruptly (missing close_notify)" whenever this
# was active). Building an explicit merged CA file and pointing SSL_CERT_FILE
# at it only affects code that reads that env var (google-genai does; the
# dashboard's self-signed cert setup never touches it), so it can't leak into
# unrelated server-side TLS the way a global monkeypatch does.
def _write_merged_ca_bundle() -> None:
    if os.environ.get("SSL_CERT_FILE"):
        return  # user/deployment already set one explicitly — don't override
    import certifi
    parts = [Path(certifi.where()).read_bytes()]
    if sys.platform == "win32":
        try:
            seen = set()
            for der, _encoding, _trust in ssl.enum_certificates("ROOT"):
                if der not in seen:
                    seen.add(der)
                    parts.append(ssl.DER_cert_to_PEM_cert(der).encode("ascii"))
        except Exception:
            pass  # fall back to certifi-only bundle rather than fail startup
    # get_base_dir() isn't defined until later in this file — inline the same
    # frozen-vs-source logic rather than reordering the whole module.
    base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    bundle_path = base_dir / "config" / "ca_bundle.pem"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_bytes(b"\n".join(parts))
    os.environ["SSL_CERT_FILE"] = str(bundle_path)


_write_merged_ca_bundle()

from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.claude_agent      import claude_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.image_generator   import generate_image
from actions.launch_trader     import launch_trader
from actions.integrations      import registry as integration_registry
from core import settings_store, mcp_registry


def _play_listen_chime():
    """Short ascending beep confirming listening mode turned on. Runs on its
    own thread so it never blocks the mic-capture callback."""
    try:
        import winsound
        for freq in (700, 1000):
            winsound.Beep(freq, 90)
    except Exception:
        pass


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
(BASE_DIR / "config").mkdir(parents=True, exist_ok=True)  # first-run on a packaged install: config/ doesn't ship in the bundle
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
SHARED_RULES_PATH = BASE_DIR / "core" / "shared_rules.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

def _load_vault_memory_index() -> str:
    """Reads the index of Claude's memory (the user's 'second brain') from
    the Obsidian vault, so the voice layer knows what topics/projects exist
    even before delegating a detailed question to claude_agent."""
    vault_dir = settings_store.load_settings()["claude_agent"].get("vaultDir")
    if not vault_dir:
        return ""
    index_path = Path(vault_dir) / "Memory" / "MEMORY.md"
    try:
        text = index_path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not text:
        return ""
    if len(text) > 3000:
        text = text[:3000] + "…"
    return (
        "[YOUR SHARED KNOWLEDGE BASE]\n"
        "This is the index of Claude's memory about the user, stored in his Obsidian "
        "vault (his 'second brain') — one line per topic. It's a map, not the full "
        "detail. If a question needs more than a one-liner here, or touches a specific "
        "project/note, call claude_agent to get the real answer instead of guessing.\n"
        f"{text}\n"
    )


def _get_input_device():
    """Prefer 'Microphone Array' (built-in mic) over sounddevice's system
    default, which on this machine points at an empty 3.5mm jack and
    captures near-silence instead of real audio."""
    try:
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and "microphone array" in d["name"].lower():
                return i
    except Exception:
        pass
    return None

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


_TRADER_SECTION_RE = re.compile(r"\[TRADER_SECTION\](.*?)\[/TRADER_SECTION\]", re.DOTALL)

def _apply_trader_section(prompt: str, trader_enabled: bool) -> str:
    """core/shared_rules.txt wraps trader-only routing/safety text in
    [TRADER_SECTION]...[/TRADER_SECTION] markers — strip the markers (and
    keep the text) when the trader add-on is on, or drop it entirely
    (markers + text) when it's off, so a customer who never enabled trading
    never sees launch_trader mentioned as an available tool."""
    if trader_enabled:
        return _TRADER_SECTION_RE.sub(r"\1", prompt)
    return _TRADER_SECTION_RE.sub("", prompt)


def _load_system_prompt() -> str:
    """The default companion's identity block (core/prompt.txt) — used as
    the system prompt's identity half whenever no companion is active, or
    a companion has no system_prompt of its own. Always combined with
    _load_shared_rules() (see _build_config()); this function alone is
    NOT a complete system prompt."""
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are Omni — the default companion of Omni-OS. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


def _load_shared_rules() -> str:
    """Tool-routing, safety, and execution rules that apply to EVERY
    companion — default or custom. Kept separate from the per-companion
    identity block so a custom companion's system_prompt only needs to
    define its persona; it can't accidentally drop the trading safety
    rule or tool-routing behavior by omitting text it never had to write."""
    try:
        return SHARED_RULES_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds a BRAND NEW project from scratch that does not exist yet: plans, writes files, installs deps, opens VSCode, runs and fixes errors. Do NOT use this for any EXISTING project already in the user's files — use claude_agent for those instead.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "claude_agent",
        "description": (
            "Delegates to Claude (Anthropic), running as a real coding agent with access to "
            "the user's Obsidian second-brain vault, his active projects, and file/tool "
            "access. Use this instead of answering directly "
            "whenever the request is: real coding or project work, anything referencing the "
            "user's notes/memory/vault, or a task requiring multi-step file edits or tool use. "
            "Do NOT use this for small talk, general knowledge questions, or anything you can "
            "answer yourself quickly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "request": {"type": "STRING", "description": "The user's request, in their own words, to hand to Claude"},
                "timeout": {"type": "INTEGER", "description": "Max seconds to wait (default: 180)"},
            },
            "required": ["request"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "generate_image",
        "description": (
            "Generates an image from a text description using AI image generation, and "
            "opens it for the user. Always call this tool for requests like 'generate an "
            "image of...', 'make a picture of...', 'draw me a...', 'create an image/picture "
            "of...', or any other request to create, design, or produce a picture, image, "
            "artwork, logo, or illustration. Never say you can't create images — always call "
            "this tool instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "Detailed description of the image to generate"}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "launch_trader",
        "description": (
            "Opens the built-in trader panel — an optional crypto trading feature with "
            "wallet connect and live trade execution. Call this whenever the user asks to "
            "open, launch, or start the trader, trading app, or crypto trader. Omni itself "
            "never places trades — actual buy/sell commands must be typed directly into "
            "the trader panel's own command bar, never through voice."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "delegate_to_agent",
        "description": (
            "Hands a task off to a named specialized sub-agent to work on in the "
            "background while you keep talking to the user — for coding, research, or "
            "multi-step work that fits a specific sub-agent's specialty better than you "
            "handling it directly. Returns immediately with an acknowledgement; the "
            "sub-agent works asynchronously and you'll be told its result afterward so "
            "you can report back. Only call this with a sub-agent name from the list "
            "you were given — never invent one."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "agent_name": {"type": "STRING", "description": "The sub-agent's exact name to delegate to"},
                "task": {"type": "STRING", "description": "Clear, self-contained instructions for what the sub-agent should do"},
            },
            "required": ["agent_name", "task"],
        },
    },
    {
        "name": "share_file",
        "description": (
            "Gives the user a clickable web link to a local file Omni just created "
            "(e.g. an HTML page, document, or generated file), so it can be opened from "
            "the phone dashboard. ALWAYS call this instead of reciting a file:// path or "
            "local filesystem path when the user asks for 'a link' or when a phone is "
            "connected via the Remote Dashboard — file:// paths do not work on a phone."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Full local path to the file to share"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "shutdown_seraph",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Omni. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

class _ReconnectRequested(Exception):
    """Raised to force run()'s connect loop to drop and re-establish the
    Live session — used when the user picks a different voice, since
    voice_name is only read at connect time."""


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self._custom_tool_dispatch: dict = {}  # populated by _build_config(); {prefixed_name: (server, tool_name)}
        self._companion: dict | None = None  # active gemini_live companion (see _resolve_active_companion);
                                              # None means the original single-companion behavior
        self._live_model    = LIVE_MODEL     # overridden by _build_config() from companion["model"]
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._tool_running  = False  # True while _execute_tool is mid-call; suppresses
                                      # set_speaking(True) so trailing/residual audio
                                      # chunks from _play_audio can't flip the state back
                                      # to SPEAKING while we're silently waiting on a tool
        self.ui.on_text_command = self._on_text_command
        self.ui.on_voice_change = self._on_voice_change
        self.ui.on_companions_changed = self._on_companions_changed
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_trader_clicked = lambda: launch_trader(player=self.ui)
        self.ui.on_always_listening_toggled = self._on_always_listening_toggled
        self._reconnect_event = asyncio.Event()
        self._turn_done_event: asyncio.Event | None = None
        self._session_log: list[str] = []  # "You: ..." / "Seraph: ..." lines for this connection,
                                            # summarized and saved to memory on disconnect/shutdown
        self._dashboard = None      # DashboardServer | None — remote/phone control, started once in run()
        self._phone_active = False  # True while the phone mic is actively streaming audio

    def _make_remote_key(self):
        """Called from the Qt main thread when the user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                'SYS: Dashboard unavailable. Run: pip install fastapi "uvicorn[standard]" qrcode[pil]'
            )
            return None
        if not self._dashboard.running:
            # Handing out a key/QR code for a server that never actually
            # bound its port would just fail silently on the phone with no
            # clue why — tell the user the real reason up front instead.
            reason = self._dashboard.start_error or "hasn't started yet — try again in a moment."
            self.ui.write_log(f"SYS: Remote Dashboard isn't running: {reason}")
            return None
        key  = self._dashboard.new_key()
        url  = self._dashboard.get_url()
        return url, key, f"{url}/auto-login?key={key}"

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    def _share_file(self, path: str) -> str:
        if not self._dashboard:
            return (
                f"The file is saved at {path} on the PC — the Remote Dashboard isn't running, "
                "so there's no link to share. Tell the user where it's saved, briefly."
            )
        url = self._dashboard.register_file(path)
        if not url:
            return f"Sir, I couldn't find that file to share: {path}"

        filename = Path(path).name
        self.ui.write_log(f'SYS: <a href="{url}">Click here to open {filename}</a>')
        asyncio.create_task(
            self._dashboard.broadcast({"type": "link", "url": url, "label": filename})
        )

        return (
            "The link is now shown on screen (and sent to the phone if one is connected). "
            "Tell the user briefly that it's ready and they can click it — do NOT say the "
            "URL, the link, or any part of it out loud."
        )

    def _broadcast_image_to_phone(self, image_bytes: bytes, mime_type: str) -> None:
        """Called from generate_image's worker thread (run_in_executor), so
        scheduling onto the dashboard's event loop must be thread-safe."""
        if not self._dashboard or not self._loop:
            return
        import base64
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        asyncio.run_coroutine_threadsafe(
            self._dashboard.broadcast({"type": "image", "data": data_url}),
            self._loop,
        )

    async def _process_dashboard_commands(self) -> None:
        """Runs for the whole app lifetime — relays phone-typed text into the live session."""
        while True:
            try:
                text = await asyncio.wait_for(self._dashboard._command_queue.get(), timeout=0.5)
                if not text:
                    continue
                for _ in range(80):  # wait up to 8s for a session to exist after a fresh connect
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Phone]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    async def _relay_phone_audio(self) -> None:
        """Runs for the whole app lifetime — forwards phone mic PCM chunks into the
        live session, same input path as the PC mic. Drops chunks when there's no
        active session rather than buffering (this is a real-time stream)."""
        while True:
            try:
                chunk = await asyncio.wait_for(
                    self._dashboard._phone_audio_in_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                self._phone_active = False  # no audio for 1s — give the PC mic back
                continue
            self._phone_active = True
            if self.session and self.out_queue and not self.ui.muted:
                try:
                    self.out_queue.put_nowait({"data": chunk, "mime_type": "audio/pcm"})
                except asyncio.QueueFull:
                    pass

    def _on_always_listening_toggled(self, is_on: bool) -> None:
        if is_on:
            _play_listen_chime()

    def _on_voice_change(self, name: str):
        if self._loop:
            self._loop.call_soon_threadsafe(self._reconnect_event.set)

    def _on_companions_changed(self):
        """A companion was added/edited (e.g. a new World-view sub-agent) —
        reconnect immediately so _build_config() re-reads settings and the
        lead's delegate_to_agent directory picks it up right away, instead
        of the user having no idea why "Ask X to do Y" fails until whatever
        next triggers a natural reconnect."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._reconnect_event.set)

    async def _watch_reconnect(self):
        await self._reconnect_event.wait()
        self._reconnect_event.clear()
        raise _ReconnectRequested()

    def _on_text_command(self, text: str):
        if not self._loop:
            return
        # A claude_agent-backend companion has no live voice session to route
        # into (see _resolve_active_companion's docstring) — its interaction
        # surface is this same text box, just routed to its own Claude
        # conversation instead of the Gemini Live session below.
        settings = settings_store.load_settings()
        active_id = settings.get("active_companion_id")
        companion = next((c for c in settings.get("companions", []) if c.get("id") == active_id), None)
        if companion and companion.get("backend") == "claude_agent" and companion.get("enabled", True):
            asyncio.run_coroutine_threadsafe(self._handle_claude_companion_text(companion, text), self._loop)
            return
        if not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    async def _handle_claude_companion_text(self, companion: dict, text: str) -> None:
        from actions.claude_companion import send as claude_companion_send
        self.ui.write_log(f"SYS: {companion['name']} is thinking…")
        reply = await claude_companion_send(companion, text)
        self.ui.write_log(f"{companion['name']}: {reply}")

    def _delegate_to_agent(self, agent_name: str, task: str) -> str:
        """Handles delegate_to_agent tool calls — looks up the named
        claude_agent-backend companion and kicks off the work as a
        background asyncio task on the live event loop, so the voice
        session isn't blocked waiting for it (a sub-agent turn can easily
        take longer than a voice turn should)."""
        if not agent_name or not task:
            return "Both an agent name and a task are required."
        settings = settings_store.load_settings()
        companion = next(
            (c for c in settings["companions"]
             if c.get("backend") == "claude_agent" and c["name"].lower() == agent_name.lower()),
            None,
        )
        if not companion:
            return f"No sub-agent named '{agent_name}' found."

        asyncio.run_coroutine_threadsafe(self._run_delegation(companion, task), self._loop)
        return f"Delegating to {companion['name']} now — I'll let you know when it's done."

    async def _run_delegation(self, companion: dict, task: str) -> None:
        from actions.claude_companion import send as claude_companion_send
        self.ui.write_log(f"SYS: {companion['name']} started: {task[:80]}")
        result = await claude_companion_send(companion, task)
        self.ui.write_log(f"{companion['name']}: {result}")
        self.speak(f"{companion['name']} finished — {result[:300]}")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            if not self._tool_running:
                self.ui.set_state("SPEAKING")
        else:
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        convo  = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            "Summarize this conversation in 1-2 sentences. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            client = genai.Client(api_key=_get_api_key(), http_options={"api_version": "v1beta"})
            resp   = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.5-flash",
                contents=prompt,
            )
            summary = (resp.text or "").strip()
            if summary:
                namespace = self._companion.get("memory_namespace") if self._companion else None
                save_session_summary(summary, namespace=namespace)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    def _resolve_active_companion(self, settings: dict) -> dict | None:
        """Returns the active companion dict, or None to fall back to the
        original single-companion behavior (core/prompt.txt, unnamespaced
        memory, self.ui.voice, LIVE_MODEL). Only backend "gemini_live"
        companions are usable in this realtime voice loop — an active
        "claude_agent" companion (turn-based, see Phase 3) also falls back
        here, since that backend has no live audio session to drive."""
        active_id = settings.get("active_companion_id")
        if not active_id:
            return None
        for c in settings.get("companions", []):
            if c.get("id") == active_id and c.get("enabled", True) and c.get("backend") == "gemini_live":
                return c
        return None

    async def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        settings = settings_store.load_settings()
        companion = self._resolve_active_companion(settings)
        self._companion = companion
        namespace = companion.get("memory_namespace") if companion else None
        self._live_model = (companion.get("model") if companion else None) or LIVE_MODEL

        memory     = load_memory(namespace)
        mem_str    = format_memory_for_prompt(memory)
        vault_str  = _load_vault_memory_index()
        identity   = (companion.get("system_prompt") if companion else None) or _load_system_prompt()
        # Every companion gets the same tool-routing/safety rules appended —
        # a custom companion's system_prompt only ever defines its persona,
        # never the mechanics of how tools/safety rules work (see
        # _load_shared_rules()'s docstring).
        sys_prompt = identity.rstrip() + "\n\n" + _load_shared_rules()

        trader_enabled = settings["trader"]["enabled"]
        enabled_skills = [s for s in settings["skills"] if s.get("enabled")]
        sys_prompt = _apply_trader_section(sys_prompt, trader_enabled)
        # Sub-agents: claude_agent-backend companions the active (voice)
        # companion can hand work off to via delegate_to_agent — see
        # actions/claude_companion.py. A companion delegating to itself
        # would just be a slower version of answering directly, so it's
        # excluded from its own directory.
        sub_agents = [
            c for c in settings["companions"]
            if c.get("backend") == "claude_agent" and c.get("enabled", True)
            and (companion is None or c.get("id") != companion.get("id"))
        ]
        base_tool_declarations = TOOL_DECLARATIONS
        if not trader_enabled:
            base_tool_declarations = [t for t in base_tool_declarations if t["name"] != "launch_trader"]
        if sub_agents:
            directory = "\n".join(
                f"- {c['name']}: {c.get('specialty') or 'general-purpose coding/research assistant'}"
                for c in sub_agents
            )
            sys_prompt += (
                f"\n\n[SUB-AGENTS AVAILABLE]\nYou can delegate tasks to these specialized "
                f"sub-agents via delegate_to_agent — pick whichever best fits the task:\n{directory}"
            )
        else:
            base_tool_declarations = [t for t in base_tool_declarations if t["name"] != "delegate_to_agent"]
        # Custom MCP servers are scoped per-companion once one is active
        # (companion["mcp_server_ids"], possibly empty — a fresh companion
        # starts with no extra tools); with no active companion, every
        # configured server applies, matching the original behavior.
        mcp_servers = settings["mcp_servers"]
        if companion is not None:
            allowed_ids = set(companion.get("mcp_server_ids") or [])
            mcp_servers = [s for s in mcp_servers if s.get("id") in allowed_ids]
        # Real network calls (each custom server's tools/list) — never run
        # directly on this shared event loop (voice, dashboard, everything
        # else would freeze for as long as a slow/offline server takes).
        custom_tool_declarations, self._custom_tool_dispatch = await asyncio.to_thread(
            mcp_registry.gather_custom_tool_declarations, mcp_servers
        )
        integration_tool_declarations = integration_registry.get_active_tool_declarations(settings)

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)

        last = pop_last_session(namespace)  # consumed here so it's never mentioned twice
        if last:
            try:
                delta = (now - datetime.strptime(last["date"], "%Y-%m-%d")).days
                when  = "earlier today" if delta == 0 else ("yesterday" if delta == 1 else f"{delta} days ago")
            except Exception:
                when = "last time"
            parts.append(
                f"[LAST SESSION]\nBriefly and naturally mention, once, the first time you "
                f"respond: {when} — {last['summary']}\n\n"
            )

        if vault_str:
            parts.append(vault_str)
        parts.append(sys_prompt)

        if enabled_skills:
            skills_block = "\n\n".join(
                f"### {s['name']}\n{settings_store.resolve_key_placeholders(s['content'], settings)}"
                for s in enabled_skills
            )
            parts.append(f"[SKILLS — additional instructions the user configured]\n{skills_block}")

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": base_tool_declarations + custom_tool_declarations + integration_tool_declarations}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=(companion.get("voice") if companion else None) or self.ui.voice
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")
        self._tool_running = True

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                namespace = self._companion.get("memory_namespace") if self._companion else None
                update_memory({category: {key: {"value": value}}}, namespace=namespace)
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            self._tool_running = False
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name in self._custom_tool_dispatch:
                server, tool_name = self._custom_tool_dispatch[name]
                r = await loop.run_in_executor(None, lambda: mcp_registry.call_custom_tool(server, tool_name, args))
                result = r.get("text") if r.get("ok") else f"[{server.get('name')}] error: {r.get('error')}"

            elif integration_registry.is_integration_tool(name):
                r = await loop.run_in_executor(None, lambda: integration_registry.dispatch(name, args, self.ui))
                result = r or "Done."
            elif name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None,
                            "speak": self.speak},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "claude_agent":
                r = await claude_agent(parameters=args, player=self.ui, speak=self.speak)
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(
                    goal=args.get("goal", ""), priority=priority, speak=self.speak,
                    dashboard=self._dashboard, player=self.ui, loop=self._loop,
                )
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "generate_image":
                r = await loop.run_in_executor(
                    None,
                    lambda: generate_image(
                        parameters=args, player=self.ui, speak=self.speak,
                        notify_image=self._broadcast_image_to_phone,
                    ),
                )
                result = r or "Done."

            elif name == "share_file":
                result = self._share_file(args.get("path", ""))

            elif name == "launch_trader":
                r = await loop.run_in_executor(None, lambda: launch_trader(player=self.ui))
                result = r or "Done."

            elif name == "delegate_to_agent":
                result = self._delegate_to_agent(args.get("agent_name", ""), args.get("task", ""))

            elif name == "shutdown_seraph":
                self.ui.write_log("SYS: Shutdown requested.")
                await self._save_session_summary()
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        self._tool_running = False
        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if jarvis_speaking or self.ui.muted or self._phone_active:
                return

            if not self.ui.always_listening:
                # Listening toggle is off: mic audio never leaves this device.
                return

            data = indata.tobytes()
            loop.call_soon_threadsafe(
                self.out_queue.put_nowait,
                {"data": data, "mime_type": "audio/pcm"}
            )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=_get_input_device(),
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self._session_log.append(f"You: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({"type": "you", "text": full_in}))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Omni: {full_out}")
                                self._session_log.append(f"Omni: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({"type": "seraph", "text": full_out}))  # NOTE: "type" is a wire-protocol key matched by dashboard/server.py's JS — kept as "seraph" intentionally, do not rename without updating that JS too
                            out_buf = []

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                if not self.ui.speech_muted:
                    await asyncio.to_thread(stream.write, chunk)
                if self._dashboard:
                    asyncio.create_task(self._dashboard.broadcast_audio(chunk))
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        # Remote Dashboard (optional — needs: pip install fastapi "uvicorn[standard]" qrcode[pil]).
        # Runs for the whole process lifetime, independent of the reconnect loop below.
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._dashboard.set_trader_state_callback(self.ui.get_trader_state)
            self._dashboard.set_trader_action_callback(self.ui.run_trader_action)
            self._dashboard.set_error_callback(
                lambda msg: self.ui.write_log(f"SYS: Remote Dashboard {msg}")
            )
            asyncio.create_task(self._dashboard.serve())
            asyncio.create_task(self._process_dashboard_commands())
            asyncio.create_task(self._relay_phone_audio())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = await self._build_config()

                async with (
                    client.aio.live.connect(model=self._live_model, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)
                    self._turn_done_event = asyncio.Event()

                    print("[JARVIS] ✅ Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: OMNI-OS online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._watch_reconnect())

            except Exception as e:
                # TaskGroup wraps a child task's exception in an
                # ExceptionGroup — check for the voice-change marker inside
                # it so that expected reconnect doesn't print as an error.
                if isinstance(e, ExceptionGroup) and e.subgroup(_ReconnectRequested):
                    print("[JARVIS] 🔁 Reconnecting for voice change...")
                elif isinstance(e, _ReconnectRequested):
                    print("[JARVIS] 🔁 Reconnecting for voice change...")
                else:
                    print(f"[JARVIS] ⚠️ {e}")
                    traceback.print_exc()
            self.session = None
            if len(self._session_log) >= 3:  # only worth summarizing if there was a real exchange
                await self._save_session_summary()
            self.set_speaking(False)
            self.ui.set_state("THINKING")
            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)

def main():
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()