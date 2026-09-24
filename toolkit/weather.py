"""weather_report — current conditions and today's range for a city.

Uses Open-Meteo (free, keyless): geocode the city, then read the forecast,
so the model gets real numbers to say. If either call fails, falls back to
opening a web search for the weather in the default browser.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
import webbrowser

from toolkit.base import ToolContext, register, S

_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, grouped into plain words.
_CONDITIONS = [
    ({0}, "clear sky"),
    ({1, 2}, "partly cloudy"),
    ({3}, "overcast"),
    ({45, 48}, "fog"),
    ({51, 53, 55, 56, 57}, "drizzle"),
    ({61, 63, 65, 66, 67, 80, 81, 82}, "rain"),
    ({71, 73, 75, 77, 85, 86}, "snow"),
    ({95, 96, 99}, "thunderstorms"),
]


def _describe(code) -> str:
    for codes, words in _CONDITIONS:
        if code in codes:
            return words
    return "mixed conditions"


def _get_json(url: str, params: dict) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(full, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def forecast(city: str) -> str:
    places = _get_json(_GEOCODE, {"name": city, "count": 1}).get("results") or []
    if not places:
        raise LookupError(f"no place called {city!r}")
    place = places[0]
    data = _get_json(_FORECAST, {
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto", "forecast_days": 1,
    })
    now, day = data["current"], data["daily"]
    where = ", ".join(p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p)
    rain = day.get("precipitation_probability_max", [None])[0]
    parts = [
        f"{where}: {_describe(now.get('weather_code'))}, {now['temperature_2m']:.0f}°C "
        f"(feels like {now['apparent_temperature']:.0f}°C).",
        f"Today {day['temperature_2m_min'][0]:.0f}–{day['temperature_2m_max'][0]:.0f}°C,",
        f"humidity {now['relative_humidity_2m']:.0f}%, wind {now['wind_speed_10m']:.0f} km/h",
    ]
    if rain is not None:
        parts.append(f", {rain:.0f}% chance of rain.")
    else:
        parts[-1] += "."
    return " ".join(parts).replace(" ,", ",")


@register(
    "weather_report",
    "Gets the current weather and today's forecast for a city.",
    {"city": S("City name")},
    ["city"],
)
def weather_report(args: dict, ctx: ToolContext) -> str:
    city = (args.get("city") or "").strip()
    if not city:
        return "Which city should I check the weather for?"
    ctx.log(f"[Weather] {city}")
    try:
        return forecast(city)
    except Exception as err:
        ctx.log(f"SYS: weather lookup failed ({err}) — opening a web search instead")
    query = urllib.parse.quote_plus(f"weather {city}")
    if webbrowser.open(f"https://www.google.com/search?q={query}"):
        return f"I couldn't fetch the forecast directly, so I opened a weather search for {city}."
    return f"I couldn't get the weather for {city} right now."
