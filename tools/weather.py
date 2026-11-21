"""Weather from Open-Meteo (free, no account or API key needed)."""
import json
import urllib.parse
import urllib.request
from datetime import date

from tools.registry import tool

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
       81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
       95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _f(c: float) -> int:
    return round(c * 9 / 5 + 32)


def _home_spot() -> tuple[float, float, str] | None:
    """The household's home (address or ZIP from the kitchen profile) on the map, or None if it isn't set."""
    try:                                             # where he is right now (phone / PC), when a device has said
        from core import place
        here = place.current(allow_home=False)
        if here and here.get("lat") is not None:
            return here["lat"], here["lon"], ", ".join(p for p in (here.get("city"), here.get("state")) if p) or "your location"
    except Exception:  # noqa: BLE001 - home, as before
        pass
    from tools import kitchen
    try:
        lat, lon, _ = kitchen._home(kitchen._data()["household"])
    except (LookupError, OSError, ValueError, KeyError):
        return None
    return lat, lon, "Home"


@tool(
    "Current weather and forecast. Leave place empty for their home (the household address/ZIP); name a city "
    "only when they ask about somewhere else.",
    {"place": {"type": "string", "description": "City, optionally with region/country, e.g. 'Miami, FL'. Empty = home"},
     "days": {"type": "integer", "description": "Forecast days, 1-7 (default 2: today and tomorrow)"}},
    required=[],
)
def get_weather(place: str = "", days: int = 2) -> str:
    from tools.travel import HOME_WORDS

    if HOME_WORDS.match(place or ""):
        spot = _home_spot()
        if spot is None:
            return ("I don't know where home is yet. Tell me your city or ZIP code (I'll save it with "
                    "set_household_food), or name the place you'd like the weather for.")
        lat, lon, where = spot
    else:
        name = place.split(",")[0].strip()
        geo = _get("https://geocoding-api.open-meteo.com/v1/search?" +
                   urllib.parse.urlencode({"name": name, "count": 5, "language": "en"}))
        results = geo.get("results") or []
        if not results:
            return f"I couldn't find a place called '{place}'."
        hint = place.lower()
        loc = next((r for r in results if any(p and p.lower() in hint for p in
                    (r.get("admin1"), r.get("country"), r.get("country_code")))), results[0])
        lat, lon = loc["latitude"], loc["longitude"]
        where = ", ".join(p for p in (loc.get("name"), loc.get("admin1"), loc.get("country")) if p)
    days = max(2, min(days, 7))  # always tomorrow too: asked with days=1, the small model described today as tomorrow
    data = _get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": days,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }))
    cur = data["current"]
    lines = [f"{where} now: {WMO.get(cur['weather_code'], 'unknown')}, {_f(cur['temperature_2m'])}°F "
             f"({round(cur['temperature_2m'])}°C), feels like {_f(cur['apparent_temperature'])}°F, "
             f"wind {round(cur['wind_speed_10m'] / 1.609)} mph."]
    d = data["daily"]
    for i in range(len(d["time"])):
        # "Today"/"Tomorrow"/"Saturday", not 2026-10-24: the small model then never has to work out which day it is.
        day = "Today" if i == 0 else "Tomorrow" if i == 1 else date.fromisoformat(d["time"][i]).strftime("%A")
        lines.append(f"{day}: {WMO.get(d['weather_code'][i], '?')}, high {_f(d['temperature_2m_max'][i])}°F "
                     f"/ low {_f(d['temperature_2m_min'][i])}°F, {d['precipitation_probability_max'][i]}% chance of rain.")
    return "\n".join(lines)
