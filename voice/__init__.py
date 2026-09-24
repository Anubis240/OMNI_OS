"""The live voice assistant: one Gemini Live session at a time, with the
microphone, speaker, phone dashboard and tools wired into it.

  session.py   Assistant — connect / run / reconnect loop and the turn stream
  audio.py     microphone capture and speaker playback
  prompt.py    the system instruction and tool list for a connection
  dispatch.py  executing the model's tool calls
  phone.py     relaying the Remote Dashboard phone into the session
  timeouts.py  bounded waits for the SDK calls that have none of their own
"""
