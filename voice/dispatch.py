"""The tool list for a connection, and running the model's tool calls."""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime
from typing import TYPE_CHECKING

from google.genai import types

import toolkit
from actions import blockchain_readonly, action_items, weekly_review
from actions.claude_agent import claude_agent
from actions.image_generator import generate_image
from actions.integrations import registry as integrations
from actions.launch_trader import launch_trader
from core import mcp_registry
from memory import profile
from toolkit.base import ToolContext, S, I

if TYPE_CHECKING:
    from voice.prompt import Briefing
    from voice.session import Assistant


def _decl(name: str, description: str, properties: dict | None = None, required: list | None = None) -> dict:
    schema = {"name": name, "description": description,
              "parameters": {"type": "OBJECT", "properties": properties or {}}}
    if required:
        schema["parameters"]["required"] = required
    return schema


# Tools that belong to the session itself rather than to toolkit.
_SESSION_TOOLS = {
    "save_memory": _decl(
        "save_memory",
        "Quietly remembers a lasting personal fact the user mentioned — name, city, job, likes, "
        "people in their life, projects, plans, habits. Don't announce it; don't use it for "
        "one-off requests. Write the value in English whatever language you're speaking.",
        {"category": S("Which part of the profile it belongs to: identity (who they are), preferences "
                          "(likes/dislikes), projects, relationships (people), wishes (plans, wants) or notes (anything else)"),
         "key": S("short snake_case label, e.g. favorite_food"),
         "value": S("the fact, briefly, in English")},
        ["category", "key", "value"]),
    "get_current_time": _decl(
        "get_current_time",
        "The actual current date and time. Use it whenever the time matters — the time you "
        "were given at the start can be stale after a reconnect."),
    "agent_task": _decl(
        "agent_task",
        "Hands a genuinely multi-step goal (several different tools in sequence, e.g. research "
        "something and save a report) to a background worker and returns straight away; you'll "
        "be told the outcome later. Not for anything one tool call can do.",
        {"goal": S("The whole goal, stated completely"), "priority": S("low | normal | high")},
        ["goal"]),
    "claude_agent": _decl(
        "claude_agent",
        "Asks Claude (Anthropic), running as a coding agent with access to the user's Obsidian "
        "vault and projects. Use it for real coding or project work and for anything about the "
        "user's own notes, vault or existing projects. Not for small talk or general knowledge.",
        {"request": S("The user's request in their own words"), "timeout": I("Seconds to wait (default 180)")},
        ["request"]),
    "generate_image": _decl(
        "generate_image",
        "Creates an image from a description and shows it to the user. Use it for any request to "
        "draw, design or generate a picture, logo, artwork or illustration.",
        {"prompt": S("A detailed description of the image")}, ["prompt"]),
    "launch_trader": _decl(
        "launch_trader",
        "Opens the built-in crypto trader panel. Omni never places trades itself — buy and sell "
        "commands are only ever typed into the trader panel's own command bar."),
    "delegate_to_agent": _decl(
        "delegate_to_agent",
        "Gives a task to one of the listed sub-agents to work on in the background while you keep "
        "talking. Returns immediately; you'll hear the result when it's done. Only use a name "
        "from the sub-agent list.",
        {"agent_name": S("The sub-agent's exact name"), "task": S("Complete, self-contained instructions")},
        ["agent_name", "task"]),
    "close_assistant": _decl(
        "close_assistant",
        "Closes Omni-OS. Only when the user clearly wants to end the session or close the app "
        "(in any language)."),
}


class ToolRouter:
    def __init__(self, assistant: "Assistant"):
        self.a = assistant
        self._mcp: dict = {}   # prefixed tool name → (server, tool name)
        self.busy = False

    async def declarations(self, brief: "Briefing") -> list[dict]:
        names = [n for n in _SESSION_TOOLS
                 if (n != "launch_trader" or brief.trader_enabled)
                 and (n != "delegate_to_agent" or brief.sub_agents)]
        decls = [_SESSION_TOOLS[n] for n in names] + toolkit.schemas()
        decls += (blockchain_readonly.TOOL_DECLARATIONS + action_items.TOOL_DECLARATIONS
                  + weekly_review.TOOL_DECLARATIONS)
        # Listing a custom MCP server's tools is a network call — keep it off the event loop.
        mcp_decls, self._mcp = await asyncio.to_thread(mcp_registry.gather_custom_tool_declarations,
                                                       brief.mcp_servers)
        from core import settings_store
        return decls + mcp_decls + integrations.get_active_tool_declarations(settings_store.load_settings())

    def _context(self) -> ToolContext:
        return ToolContext(ui=self.a.ui, speak=self.a.speak, namespace=self.a.namespace,
                           dashboard=self.a.dashboard, loop=self.a.loop)

    async def run(self, call) -> types.FunctionResponse:
        name, args = call.name, dict(call.args or {})
        print(f"[tool] {name} {args}")
        self.busy = True
        self.a.ui.show_thinking()
        silent = False
        try:
            result, silent = await self._run(name, args)
        except Exception as err:
            traceback.print_exc()
            result = f"{name} failed: {err}"
            self.a.ui.post(f"ERR: {name} — {str(err)[:120]}")
        finally:
            self.busy = False
            if not self.a.ui.mic_muted:
                self.a.ui.show_listening()
        response = {"result": result or "Done."}
        if silent:
            response["silent"] = True
        return types.FunctionResponse(id=call.id, name=name, response=response)

    async def _run(self, name: str, args: dict) -> tuple[str, bool]:
        ui, loop = self.a.ui, asyncio.get_running_loop()
        off_loop = lambda fn: loop.run_in_executor(None, fn)  # noqa: E731

        if name == "save_memory":
            if args.get("key") and args.get("value"):
                profile.remember_one(args.get("category", "notes"), args["key"], args["value"], self.a.namespace)
            return "ok", True
        if name == "get_current_time":
            return datetime.now().strftime("%A %d %B %Y, %H:%M"), False
        if name == "agent_task":
            from agent.tasks import Priority, board
            job = board().submit(args.get("goal", ""), Priority.parse(args.get("priority")),
                                 ui=ui, speak=self.a.speak, dashboard=self.a.dashboard, loop=self.a.loop)
            return f"Started background task {job}; I'll report back when it's done.", False
        if name == "claude_agent":
            return await claude_agent(parameters=args, player=ui, speak=self.a.speak), False
        if name == "generate_image":
            return await off_loop(lambda: generate_image(parameters=args, player=ui, speak=self.a.speak,
                                                         notify_image=self.a.phone.send_image)), False
        if name == "launch_trader":
            return await off_loop(lambda: launch_trader(player=ui)), False
        if name == "delegate_to_agent":
            return self.a.delegate(args.get("agent_name", ""), args.get("task", "")), False
        if name == "close_assistant":
            await self.a.close()
            return "Closing.", False
        if name in self._mcp:
            server, tool = self._mcp[name]
            out = await off_loop(lambda: mcp_registry.call_custom_tool(server, tool, args))
            return (out.get("text") if out.get("ok") else f"[{server.get('name')}] error: {out.get('error')}"), False
        if integrations.is_integration_tool(name):
            return await off_loop(lambda: integrations.dispatch(name, args, ui)), False
        if name in ("check_wallet_balance", "check_token_balance", "check_gas_price"):
            fn = getattr(blockchain_readonly, name)
            return await off_loop(lambda: fn(parameters=args, player=ui)), False
        if name == "extract_action_items":
            return await off_loop(lambda: action_items.extract_action_items(
                parameters=args, player=ui, namespace=self.a.namespace)), False
        if name == "weekly_review":
            return await off_loop(lambda: weekly_review.weekly_review(
                parameters=args, player=ui, namespace=self.a.namespace)), False
        tool = toolkit.get(name)
        if tool is None:
            return f"There's no tool called {name}.", False
        ctx = self._context()
        if tool.blocking:
            return await off_loop(lambda: tool.handler(args, ctx)), False
        return tool.handler(args, ctx), False
