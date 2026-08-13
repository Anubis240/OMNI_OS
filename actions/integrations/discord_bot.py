import requests

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "discord_send_message",
        "description": "Sends a message to a Discord channel via the connected bot.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "channel_id": {"type": "STRING", "description": "The Discord channel ID"},
                "text":       {"type": "STRING", "description": "Message text"},
            },
            "required": ["channel_id", "text"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("discord")
    if not creds:
        return "Discord isn't connected — add a Bot Token in the Integrations tab."

    try:
        if name == "discord_send_message":
            r = requests.post(
                f"https://discord.com/api/v10/channels/{args['channel_id']}/messages",
                headers={"Authorization": f"Bot {creds['bot_token']}"},
                json={"content": args["text"]}, timeout=15,
            )
            r.raise_for_status()
            return "Message sent to Discord."

    except requests.HTTPError as e:
        msg = f"Discord API error: {e.response.status_code} {e.response.text[:200]}"
        log("Discord", msg, player)
        return msg
    except Exception as e:
        msg = f"Discord error: {e}"
        log("Discord", msg, player)
        return msg

    return "Unknown Discord action."
