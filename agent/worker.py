"""Work one background goal to completion with Gemini function calling.

The model sees the goal and the toolkit's tool schemas, calls one tool at a
time, gets the real result back, and decides what to do next — until it
answers in plain text or runs out of steps. Tools keep their own safety
gates (confirmation dialogs), so nothing here can act more freely than the
voice session itself.
"""

from __future__ import annotations

import toolkit
from toolkit import llm
from toolkit.base import ToolContext

MAX_STEPS = 14
# Tools that make no sense inside a background task.
_EXCLUDED = {"dev_agent"}

_BRIEF = (
    "You are Omni's background worker. Complete the user's goal using the tools, one "
    "step at a time, checking each result before the next step. Prefer the most direct "
    "tool. When the goal is done — or clearly can't be done — reply with a short, plain "
    "summary for the user in the same language as the goal, without further tool calls."
)


def _tool_config():
    from google.genai import types
    declarations = [types.FunctionDeclaration(**s) for s in toolkit.schemas() if s["name"] not in _EXCLUDED]
    return types.GenerateContentConfig(
        system_instruction=_BRIEF,
        tools=[types.Tool(function_declarations=declarations)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def pursue(job) -> str:
    """TaskBoard worker: returns the final summary (also spoken if possible)."""
    from google.genai import types

    ctx = ToolContext(ui=job.context.get("ui"), speak=job.context.get("speak"),
                      dashboard=job.context.get("dashboard"), loop=job.context.get("loop"))
    config = _tool_config()
    history = [types.Content(role="user", parts=[types.Part.from_text(text=job.goal)])]
    ctx.log(f"[Task {job.id}] started: {job.goal[:80]}")

    summary = ""
    for step in range(1, MAX_STEPS + 1):
        if job.stop.is_set():
            return "Cancelled."
        reply = llm.client().models.generate_content(model=llm.FAST, contents=history, config=config)
        content = reply.candidates[0].content if reply.candidates else None
        calls = [p.function_call for p in (content.parts if content and content.parts else []) if p.function_call]
        if not calls:
            summary = (reply.text or "").strip() or "Finished."
            break
        history.append(content)
        answers = []
        for call in calls:
            if job.stop.is_set():
                return "Cancelled."
            ctx.log(f"[Task {job.id}] step {step}: {call.name}")
            if call.name in _EXCLUDED:
                result = f"{call.name} isn't available inside a background task."
            else:
                try:
                    result = toolkit.base.run(call.name, dict(call.args or {}), ctx)
                except Exception as err:
                    result = f"error: {err}"
            answers.append(types.Part.from_function_response(name=call.name, response={"result": str(result)[:8000]}))
        history.append(types.Content(role="user", parts=answers))
    else:
        summary = f"I stopped after {MAX_STEPS} steps without finishing: {job.goal[:80]}"

    ctx.log(f"[Task {job.id}] {summary[:120]}")
    ctx.say(f"Background task finished. {summary}")
    return summary
