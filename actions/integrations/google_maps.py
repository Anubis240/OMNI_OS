import requests

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "maps_directions",
        "description": "Gets directions and travel time between two places.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING", "description": "Starting address or place"},
                "destination": {"type": "STRING", "description": "Destination address or place"},
                "mode":        {"type": "STRING", "description": "driving | walking | bicycling | transit (default: driving)"},
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "maps_search_nearby",
        "description": "Searches for places (restaurants, gas stations, etc.) near a location.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":    {"type": "STRING", "description": "What to search for, e.g. 'coffee shops'"},
                "location": {"type": "STRING", "description": "Address or place to search near"},
            },
            "required": ["query", "location"],
        },
    },
]


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("google_maps")
    if not creds:
        return "Google Maps isn't connected — add an API Key in the Integrations tab."
    key = creds["api_key"]

    try:
        if name == "maps_directions":
            r = requests.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params={"origin": args["origin"], "destination": args["destination"],
                        "mode": args.get("mode", "driving"), "key": key},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "OK" or not data.get("routes"):
                return f"Couldn't find a route: {data.get('status', 'unknown error')}"
            leg = data["routes"][0]["legs"][0]
            return f"{leg['distance']['text']}, {leg['duration']['text']} — via {data['routes'][0].get('summary', 'route')}"

        if name == "maps_search_nearby":
            r = requests.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={"query": f"{args['query']} near {args['location']}", "key": key},
                timeout=15,
            )
            r.raise_for_status()
            results = r.json().get("results", [])[:8]
            if not results:
                return f"No results for '{args['query']}' near {args['location']}."
            lines = [f"{p['name']} — {p.get('formatted_address', '')} ({p.get('rating', '?')}★)" for p in results]
            return "Results:\n" + "\n".join(lines)

    except requests.HTTPError as e:
        msg = f"Google Maps API error: {e.response.status_code} {e.response.text[:200]}"
        log("Google Maps", msg, player)
        return msg
    except Exception as e:
        msg = f"Google Maps error: {e}"
        log("Google Maps", msg, player)
        return msg

    return "Unknown Google Maps action."
