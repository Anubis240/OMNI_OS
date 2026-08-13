from pathlib import Path

import requests

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "elevenlabs_list_voices",
        "description": "Lists available ElevenLabs voices.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "elevenlabs_text_to_speech",
        "description": "Generates speech audio from text using ElevenLabs and saves it as an MP3 file.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text":     {"type": "STRING", "description": "Text to speak"},
                "voice_id": {"type": "STRING", "description": "ElevenLabs voice ID (optional — uses a default if omitted)"},
            },
            "required": ["text"],
        },
    },
]

_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" — ElevenLabs' standard default voice


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("elevenlabs")
    if not creds:
        return "ElevenLabs isn't connected — add an API Key in the Integrations tab."
    headers = {"xi-api-key": creds["api_key"]}

    try:
        if name == "elevenlabs_list_voices":
            r = requests.get("https://api.elevenlabs.io/v1/voices", headers=headers, timeout=15)
            r.raise_for_status()
            voices = r.json().get("voices", [])
            if not voices:
                return "No voices found."
            return "Voices:\n" + "\n".join(f"{v['name']} ({v['voice_id']})" for v in voices)

        if name == "elevenlabs_text_to_speech":
            voice_id = args.get("voice_id") or _DEFAULT_VOICE
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={**headers, "Content-Type": "application/json"},
                json={"text": args["text"], "model_id": "eleven_multilingual_v2"}, timeout=60,
            )
            r.raise_for_status()
            out_dir = Path.home() / "Downloads" / "Omni-OS Generated"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"speech_{abs(hash(args['text'])) % 100000}.mp3"
            out_path.write_bytes(r.content)
            return f"Speech saved to {out_path}"

    except requests.HTTPError as e:
        msg = f"ElevenLabs API error: {e.response.status_code} {e.response.text[:200]}"
        log("ElevenLabs", msg, player)
        return msg
    except Exception as e:
        msg = f"ElevenLabs error: {e}"
        log("ElevenLabs", msg, player)
        return msg

    return "Unknown ElevenLabs action."
