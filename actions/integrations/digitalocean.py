import requests

from ._common import get_credentials, log

_API = "https://api.digitalocean.com/v2"

TOOL_DECLARATIONS = [
    {
        "name": "digitalocean_list_droplets",
        "description": "Lists DigitalOcean droplets on the connected account.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("digitalocean")
    if not creds:
        return "DigitalOcean isn't connected — add a Personal Access Token in the Integrations tab."

    try:
        if name == "digitalocean_list_droplets":
            r = requests.get(
                f"{_API}/droplets", headers={"Authorization": f"Bearer {creds['api_token']}"}, timeout=15,
            )
            r.raise_for_status()
            droplets = r.json().get("droplets", [])
            if not droplets:
                return "No droplets found."
            lines = [f"{d['name']} — {d['status']} — {d['region']['slug']}" for d in droplets]
            return "Droplets:\n" + "\n".join(lines)

    except requests.HTTPError as e:
        msg = f"DigitalOcean API error: {e.response.status_code} {e.response.text[:200]}"
        log("DigitalOcean", msg, player)
        return msg
    except Exception as e:
        msg = f"DigitalOcean error: {e}"
        log("DigitalOcean", msg, player)
        return msg

    return "Unknown DigitalOcean action."
