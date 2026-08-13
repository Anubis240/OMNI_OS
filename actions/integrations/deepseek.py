import requests

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "deepseek_chat",
        "description": "Sends a prompt to DeepSeek and returns the response. Use only when the user explicitly asks for DeepSeek specifically, not as a general fallback.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"prompt": {"type": "STRING", "description": "The prompt to send"}},
            "required": ["prompt"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("deepseek")
    if not creds:
        return "DeepSeek isn't connected — add an API Key in the Integrations tab."

    try:
        if name == "deepseek_chat":
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {creds['api_key']}"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": args["prompt"]}]},
                timeout=60,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    except requests.HTTPError as e:
        msg = f"DeepSeek API error: {e.response.status_code} {e.response.text[:200]}"
        log("DeepSeek", msg, player)
        return msg
    except Exception as e:
        msg = f"DeepSeek error: {e}"
        log("DeepSeek", msg, player)
        return msg

    return "Unknown DeepSeek action."
