"""Background multi-step tasks ("agent_task").

A goal is queued (agent.tasks), then worked on by agent.worker: Gemini
with function calling over the same toolkit tools the voice session uses,
one tool call at a time, until it can report back.
"""
