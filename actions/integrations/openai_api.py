import requests

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "openai_chat",
        "description": "Sends a prompt to OpenAI (GPT) and returns the response. Use only when the user explicitly asks for OpenAI/GPT specifically, not as a general fallback.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"prompt": {"type": "STRING", "description": "The prompt to send"}},
            "required": ["prompt"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("openai")
    if not creds:
        return "OpenAI isn't connected — add an API Key in the Integrations tab."

    try:
        if name == "openai_chat":
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {creds['api_key']}"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": args["prompt"]}]},
                timeout=60,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    except requests.HTTPError as e:
        msg = f"OpenAI API error: {e.response.status_code} {e.response.text[:200]}"
        log("OpenAI", msg, player)
        return msg
    except Exception as e:
        msg = f"OpenAI error: {e}"
        log("OpenAI", msg, player)
        return msg

    return "Unknown OpenAI action."
