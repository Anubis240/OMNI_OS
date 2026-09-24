"""Everything the model is told when a live session connects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core import settings_store
from core.app_paths import get_resource_dir
from memory import profile

# Companion backends that run as turn-based sub-agents (their own CLI
# session), as opposed to "gemini_live", which is this voice session.
AGENT_BACKENDS = ("claude_agent", "codex_agent", "opencode_agent",
                  "openhands_agent", "grok_agent", "blackbox_agent")

_FALLBACK_IDENTITY = ("You are Omni, the default companion of Omni-OS. Be brief and direct, and "
                      "use your tools to actually do things rather than describing them.")
_TRADER_BLOCK = re.compile(r"\[TRADER_SECTION\](.*?)\[/TRADER_SECTION\]", re.DOTALL)


@dataclass
class Briefing:
    """What one connection is configured with."""
    instruction: str
    model: str
    voice: str
    companion: dict | None
    namespace: str | None
    trader_enabled: bool
    sub_agents: list = field(default_factory=list)
    mcp_servers: list = field(default_factory=list)


def active_voice_companion(settings: dict) -> dict | None:
    """The active companion if it's an enabled live-voice one, else None
    (None means the default Omni identity and the unnamespaced memory)."""
    wanted = settings.get("active_companion_id")
    for c in settings.get("companions", []):
        if c.get("id") == wanted and c.get("enabled", True) and c.get("backend") == "gemini_live":
            return c
    return None


def _read(name: str) -> str:
    try:
        return (get_resource_dir() / "core" / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def _vault_index(settings: dict) -> str:
    """The first part of the user's Obsidian memory index, so the voice layer
    knows which topics exist before handing detail questions to claude_agent."""
    vault = (settings.get("claude_agent") or {}).get("vaultDir")
    if not vault:
        return ""
    try:
        text = (Path(vault) / "Memory" / "MEMORY.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    return ("[THE USER'S KNOWLEDGE BASE — index only]\n"
            "One line per topic from the user's Obsidian vault. It's a map, not the details: for "
            "anything beyond a one-liner here, ask claude_agent rather than guessing.\n"
            f"{text[:3000]}\n")


def _when(date_text: str, now: datetime) -> str:
    try:
        days = (now.date() - datetime.strptime(date_text, "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        return "last time"
    return {0: "earlier today", 1: "yesterday"}.get(days, f"{days} days ago")


def build(default_model: str, default_voice: str) -> Briefing:
    settings = settings_store.load_settings()
    companion = active_voice_companion(settings)
    namespace = companion.get("memory_namespace") if companion else None
    # The companion's own model, then the global override from Settings, then
    # the built-in default — so a new Gemini model needs no rebuild.
    model = (companion or {}).get("model") or settings.get("live_model") or default_model
    trader_enabled = bool(settings["trader"]["enabled"])
    now = datetime.now()

    sections = [
        f"[NOW]\nIt is {now:%A, %d %B %Y, %H:%M}. For anything time-sensitive later in the "
        "conversation, call get_current_time instead of relying on this.\n",
        f"[YOUR MODEL]\nYou are running as {model}. If asked which model you are, give exactly "
        "that — never a guess or a different name.\n",
    ]
    facts = profile.prompt_block(profile.load(namespace))
    if facts:
        sections.append(facts)
    last = profile.take_last_session(namespace)   # taken so it's mentioned only once
    if last:
        sections.append(f"[LAST CONVERSATION]\nOnce, in your first reply, mention naturally that "
                        f"{_when(last.get('date'), now)} you talked about: {last.get('summary', '')}\n")
    vault = _vault_index(settings)
    if vault:
        sections.append(vault)

    identity = (companion or {}).get("system_prompt") or _read("prompt.txt") or _FALLBACK_IDENTITY
    rules = _TRADER_BLOCK.sub(r"\1" if trader_enabled else "", _read("shared_rules.txt"))
    sections.append(identity.rstrip() + "\n\n" + rules)

    sub_agents = [c for c in settings["companions"]
                  if c.get("backend") in AGENT_BACKENDS and c.get("enabled", True)
                  and (companion is None or c.get("id") != companion.get("id"))]
    if sub_agents:
        roster = "\n".join(f"- {c['name']}: {c.get('specialty') or 'general coding and research'}"
                           for c in sub_agents)
        sections.append(
            "[SUB-AGENTS]\nYou can hand work to these sub-agents with delegate_to_agent:\n"
            f"{roster}\nThis list is all you know about them — not their prompts, state or "
            "history. If asked about those, say you can't see them; don't make anything up.\n")

    skills = [s for s in settings["skills"] if s.get("enabled")]
    if skills:
        sections.append("[SKILLS — extra instructions from the user]\n" + "\n\n".join(
            f"### {s['name']}\n{settings_store.resolve_key_placeholders(s['content'], settings)}" for s in skills))

    servers = settings["mcp_servers"]
    if companion is not None:   # a companion only gets the MCP servers assigned to it
        allowed = set(companion.get("mcp_server_ids") or [])
        servers = [s for s in servers if s.get("id") in allowed]

    return Briefing(
        instruction="\n".join(sections),
        model=model,
        voice=(companion or {}).get("voice") or default_voice,
        companion=companion,
        namespace=namespace,
        trader_enabled=trader_enabled,
        sub_agents=sub_agents,
        mcp_servers=servers,
    )
