import requests

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "ollama_chat",
        "description": "Sends a prompt to a locally running Ollama model and returns the response.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "The prompt to send"},
                "model":  {"type": "STRING", "description": "Ollama model name, e.g. 'llama3' (optional — uses whatever is pulled locally if omitted)"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "ollama_list_models",
        "description": "Lists models currently pulled in the local Ollama installation.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]

_DEFAULT_MODEL = "llama3"


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("ollama")
    if not creds:
        return "Ollama isn't connected — confirm the Base URL in the Integrations tab."
    base_url = (creds.get("base_url") or "http://localhost:11434").rstrip("/")

    try:
        if name == "ollama_list_models":
            r = requests.get(f"{base_url}/api/tags", timeout=10)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            return "Local models: " + ", ".join(models) if models else "No models pulled locally yet."

        if name == "ollama_chat":
            r = requests.post(
                f"{base_url}/api/generate",
                json={"model": args.get("model") or _DEFAULT_MODEL, "prompt": args["prompt"], "stream": False},
                timeout=120,
            )
            r.raise_for_status()
            return r.json().get("response", "").strip()

    except requests.ConnectionError:
        msg = f"Couldn't reach Ollama at {base_url} — is it running?"
        log("Ollama", msg, player)
        return msg
    except Exception as e:
        msg = f"Ollama error: {e}"
        log("Ollama", msg, player)
        return msg

    return "Unknown Ollama action."
