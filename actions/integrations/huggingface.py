import requests

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "huggingface_query",
        "description": "Runs a Hugging Face model via the Inference API and returns the result.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "model": {"type": "STRING", "description": "Model ID, e.g. 'gpt2' or 'facebook/bart-large-cnn'"},
                "input_text": {"type": "STRING", "description": "Input text for the model"},
            },
            "required": ["model", "input_text"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("huggingface")
    if not creds:
        return "Hugging Face isn't connected — add an Access Token in the Integrations tab."

    try:
        if name == "huggingface_query":
            r = requests.post(
                f"https://api-inference.huggingface.co/models/{args['model']}",
                headers={"Authorization": f"Bearer {creds['api_key']}"},
                json={"inputs": args["input_text"]}, timeout=60,
            )
            r.raise_for_status()
            return str(r.json())

    except requests.HTTPError as e:
        msg = f"Hugging Face API error: {e.response.status_code} {e.response.text[:200]}"
        log("Hugging Face", msg, player)
        return msg
    except Exception as e:
        msg = f"Hugging Face error: {e}"
        log("Hugging Face", msg, player)
        return msg

    return "Unknown Hugging Face action."
