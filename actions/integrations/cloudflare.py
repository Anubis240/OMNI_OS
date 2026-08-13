import requests

from ._common import get_credentials, log

_API = "https://api.cloudflare.com/client/v4"

TOOL_DECLARATIONS = [
    {
        "name": "cloudflare_list_zones",
        "description": "Lists Cloudflare zones (domains) on the connected account.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "cloudflare_purge_cache",
        "description": "Purges the entire cache for a Cloudflare zone.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"zone_id": {"type": "STRING", "description": "The Cloudflare zone ID"}},
            "required": ["zone_id"],
        },
    },
]


def _headers(creds: dict) -> dict:
    return {"Authorization": f"Bearer {creds['api_token']}", "Content-Type": "application/json"}


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("cloudflare")
    if not creds:
        return "Cloudflare isn't connected — add an API Token in the Integrations tab."

    try:
        if name == "cloudflare_list_zones":
            r = requests.get(f"{_API}/zones", headers=_headers(creds), timeout=15)
            r.raise_for_status()
            zones = r.json().get("result", [])
            if not zones:
                return "No zones found on this account."
            return "Zones:\n" + "\n".join(f"{z['name']} ({z['status']})" for z in zones)

        if name == "cloudflare_purge_cache":
            r = requests.post(
                f"{_API}/zones/{args['zone_id']}/purge_cache",
                headers=_headers(creds), json={"purge_everything": True}, timeout=15,
            )
            r.raise_for_status()
            return "Cache purge requested." if r.json().get("success") else "Cloudflare reported the purge failed."

    except requests.HTTPError as e:
        msg = f"Cloudflare API error: {e.response.status_code} {e.response.text[:200]}"
        log("Cloudflare", msg, player)
        return msg
    except Exception as e:
        msg = f"Cloudflare error: {e}"
        log("Cloudflare", msg, player)
        return msg

    return "Unknown Cloudflare action."
