"""Maps, trips and flights.

- Places and directions use free OpenStreetMap services (Nominatim to find places, OSRM for driving
  routes) and hand back Google Maps links to open on the PC or phone. No account or key needed.
- Trips: a per-person itinerary (flights, hotel, car, plans, packing, costs) Jarvis keeps as you plan.
- Flights: Jarvis acts as a flight agent: builds ready-to-open searches on Google Flights, Kayak and
  Skyscanner, researches current fares, airline military perks and Space-A. He never books or pays;
  you do the buying.
"""
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

from core import storage
from core.words import plural
from tools.registry import tool

UA = {"User-Agent": "Jarvis-personal-assistant/1.0 (household use)"}
_last_nominatim = [0.0]
MODES = ["driving", "walking", "transit", "bicycling"]
# What people (and small models that don't know the city) say for where they live: all mean the household's home.
HOME_WORDS = re.compile(r"^\s*(?:|here|home|my (?:home|house|place|city|town|area|location)|current(?: location)?|"
                        r"my current location|where i am|nearby|around here|local|(?:the )?user'?s?(?: \w+)*|"
                        r"your (?:home|city|town|area|location))\s*$", re.I)


def _place(name: str) -> str:
    """'here' / 'home' -> the household address (or ZIP), so "how far is Costco from here?" can be answered."""
    if not HOME_WORDS.match(name or ""):
        return name
    from tools import kitchen

    household = kitchen._data()["household"]
    address = str(household.get("home_address") or "").strip()
    zip_code = str(household.get("zip") or "").strip()
    return address or (f"{zip_code}, USA" if zip_code else name)


def _get(url: str, timeout: int = 15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(query: str, limit: int = 1) -> list[dict]:
    """OpenStreetMap search (their rule: at most one request per second)."""
    wait = 1.1 - (time.monotonic() - _last_nominatim[0])
    if wait > 0:
        time.sleep(wait)
    _last_nominatim[0] = time.monotonic()
    q = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 0})
    return _get(f"https://nominatim.openstreetmap.org/search?{q}")


def search_near(query: str, lat: float, lon: float, limit: int = 5) -> list[dict]:
    """`query` (a business or kind of place) within ~30 miles of a point, nearest first, each with its straight-line
    'miles'. Plain Nominatim ignores "near": "Costco" alone found one in another country (OSRM then refused to
    route it) and "Walmart near Springfield, IL" found nothing at all."""
    wait = 1.1 - (time.monotonic() - _last_nominatim[0])
    if wait > 0:
        time.sleep(wait)
    _last_nominatim[0] = time.monotonic()
    box = f"{lon - 0.45},{lat + 0.35},{lon + 0.45},{lat - 0.35}"
    q = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 20, "viewbox": box, "bounded": 1})
    try:   # a refinement: if it fails, callers fall back to the plain search
        found = [p for p in _get(f"https://nominatim.openstreetmap.org/search?{q}") if isinstance(p, dict)]
        for p in found:
            dy, dx = (float(p["lat"]) - lat) * 69.0, (float(p["lon"]) - lon) * 69.0 * math.cos(math.radians(lat))
            p["miles"] = math.hypot(dx, dy)
    except (OSError, ValueError, KeyError, TypeError):
        return []

    def side_counter(p):  # "Costco" means the warehouse, not Costco Gasoline next to it
        text = f"{p.get('type', '')} {p.get('display_name', '')}".lower()
        return any(w in text and w not in query.lower() for w in _SIDE_COUNTERS)
    return sorted(found, key=lambda p: (side_counter(p), p["miles"]))[:limit]


_SIDE_COUNTERS = ("fuel", "gasoline", "gas station", "car wash", "tire", "tyres", "pharmacy", "optical", "hearing")


def _home_spot() -> tuple[float, float] | None:
    from tools import kitchen

    try:
        lat, lon, _ = kitchen._home(kitchen._data()["household"])
    except (LookupError, OSError, ValueError, KeyError):
        return None
    return lat, lon


def maps_link(destination: str, origin: str = "", mode: str = "driving") -> str:
    p = {"api": "1", "destination": destination, "travelmode": mode}
    if origin:
        p["origin"] = origin
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(p)


@tool(
    "Find places on the map: an address, a business, or things near somewhere ('Walmart near Fort Liberty', "
    "'Chick-fil-A near 33101'). With no area given it looks near home. Returns names, distances, addresses and "
    "Google Maps links.",
    {"query": {"type": "string"}, "near": {"type": "string", "description": "Optional town/area/ZIP to search around"}},
    required=["query"],
)
def find_places(query: str, near: str = "") -> str:
    m = re.match(r"(?i)^\s*(?:the\s+)?(?:nearest|closest)?\s*(.+?)(?:\s+(?:near|around|close to)\s+(.+))?\s*$", query)
    query, near = m.group(1), near or (m.group(2) or "")    # "Walmart near Springfield, IL" -> Walmart, Springfield, IL
    center = None
    if not near or HOME_WORDS.match(near):
        center = _home_spot()
    else:
        spot = geocode(near)
        center = (float(spot[0]["lat"]), float(spot[0]["lon"])) if spot else None
    found = search_near(query, *center) if center else []
    if not found:
        found = geocode(f"{query} {_place(near) if near else ''}".strip(), limit=5)
    if not found:  # OpenStreetMap is weak on some businesses; Google Maps search handles them well
        q = f"{query} near {near}" if near else query
        return (f"OpenStreetMap had no match for '{q}'. Google Maps search (open it with open_website): "
                f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(q)} . "
                "For hours/addresses, search_web_free also works. Don't guess a distance.")
    where = "from home" if not near or HOME_WORDS.match(near) else f"from {near}"
    return "\n".join(f"{p.get('name') or query}" + (f" ({p['miles']:.1f} miles {where}, straight line)" if "miles" in p else "")
                     + f": {p['display_name']} | https://www.google.com/maps/search/?api=1&query={p['lat']},{p['lon']}"
                     for p in found)


@tool(
    "Directions between two places: driving distance and time (live from OpenStreetMap routing) plus a Google "
    "Maps link for turn-by-turn navigation (open it on the PC with open_website, or it shows on the phone app). "
    "A business name finds the nearest one. Leave origin empty to start from home.",
    {"origin": {"type": "string", "description": "Start; empty = home"}, "destination": {"type": "string"},
     "mode": {"type": "string", "enum": MODES}},
    required=["destination"],
)
def directions(origin: str = "", destination: str = "", mode: str = "driving") -> str:
    if not destination.strip():
        return "Where to? Give me the destination (a place, an address, or a store like Costco)."
    start = "home" if HOME_WORDS.match(origin or "") else ""
    origin, destination = _place(origin), _place(destination)
    a = geocode(origin)
    # "Costco": the one near the start, not the first Costco on Earth. A full address or a far city falls through.
    b = search_near(destination, float(a[0]["lat"]), float(a[0]["lon"]), limit=1) if a else []
    b = b or geocode(destination)
    if b and "miles" in b[0]:
        destination = b[0]["display_name"]
    link = maps_link(destination, origin, mode)
    if not a or not b:
        return f"Couldn't place {'the start' if not a else 'the destination'} on the map. Link anyway: {link}"
    if mode != "driving":
        return f"Directions ({mode}): {link}"
    coords = f"{a[0]['lon']},{a[0]['lat']};{b[0]['lon']},{b[0]['lat']}"
    try:
        r = _get(f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=false")
    except urllib.error.HTTPError:   # 400 NoRoute: e.g. across an ocean
        r = {}
    if r.get("code") != "Ok" or not r.get("routes"):
        return f"No driving route found. Google Maps: {link}"
    route = r["routes"][0]
    miles, mins = route["distance"] / 1609.344, route["duration"] / 60
    hours = f"{int(mins // 60)} h {int(mins % 60)} min" if mins >= 60 else f"{int(mins)} min"
    return (f"{miles:,.0f} miles, about {hours} driving without traffic "
            f"({start or a[0]['display_name'].split(',')[0]} to {b[0]['display_name'].split(',')[0]}). Navigation: {link}")


# -- trips ---------------------------------------------------------------------------------------
TRIP_KINDS = ["flight", "hotel", "car", "plan", "packing", "cost", "note"]


def _trip_file(name: str) -> str:
    return f"trips/{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:50] or 'trip'}.json"


@tool(
    "Start (or reopen) a trip plan: name, destination and dates. Then add flights, hotel, car, plans, packing "
    "and costs with trip_add as you plan.",
    {"name": {"type": "string", "description": "e.g. 'Miami Christmas'"}, "destination": {"type": "string"},
     "start": {"type": "string", "description": "YYYY-MM-DD"}, "end": {"type": "string", "description": "YYYY-MM-DD"}},
    required=["name", "destination"],
)
def plan_trip(name: str, destination: str, start: str = "", end: str = "") -> str:
    data = storage.load(_trip_file(name), None)
    if data:
        data.update({k: v for k, v in (("destination", destination), ("start", start), ("end", end)) if v})
    else:
        data = {"name": name, "destination": destination, "start": start, "end": end, "items": []}
    storage.save(_trip_file(name), data)
    return f"Trip '{name}' to {destination}" + (f", {start} to {end}" if start else "") + " is set up."


@tool(
    "Add to a trip plan: a flight (airline, times, price, confirmation), hotel, car, a plan/activity, a packing "
    "item, a cost, or a note.",
    {"name": {"type": "string"}, "kind": {"type": "string", "enum": TRIP_KINDS}, "item": {"type": "string"},
     "cost": {"type": "number", "description": "Optional cost in dollars"}},
    required=["name", "kind", "item"],
)
def trip_add(name: str, kind: str, item: str, cost: float = 0) -> str:
    data = storage.load(_trip_file(name), None)
    if data is None:
        return f"No trip called '{name}'. Start it with plan_trip."
    data["items"].append({"t": datetime.now().isoformat(timespec="minutes"), "kind": kind, "note": item,
                          "cost": round(float(cost or 0), 2)})
    storage.save(_trip_file(name), data)
    return f"Added to {data['name']} ({kind})."


@tool("Everything planned for a trip: dates, countdown, flights, hotel, plans, packing list and total cost.",
      {"name": {"type": "string"}})
def trip_summary(name: str) -> str:
    data = storage.load(_trip_file(name), None)
    if data is None:
        return f"No trip called '{name}'."
    lines = [f"{data['name']}: {data['destination']}" + (f", {data['start']} to {data['end']}" if data.get("start") else "")]
    if data.get("start"):
        try:
            days = (date.fromisoformat(data["start"]) - date.today()).days
            lines.append(f"{plural(days, 'day')} to go." if days > 0 else ("Today!" if days == 0 else "Trip date has passed."))
        except ValueError:
            pass
    for kind in TRIP_KINDS:
        items = [i for i in data["items"] if i["kind"] == kind]
        if items:
            lines.append(f"{kind.capitalize()}: " + "; ".join(i["note"] + (f" (${i['cost']:,.0f})" if i["cost"] else "")
                                                           for i in items))
    total = sum(i["cost"] for i in data["items"])
    if total:
        lines.append(f"Total so far: ${total:,.2f}")
    return "\n".join(lines)


@tool("List this person's trips.")
def list_trips() -> str:
    folder = storage.user_dir() / "trips"
    trips = [storage.load(f"trips/{f.name}", {}) for f in sorted(folder.glob("*.json"))] if folder.exists() else []
    return "\n".join(f"{t['name']}: {t['destination']} {t.get('start', '')}" for t in trips) or "No trips yet."


# -- flights -------------------------------------------------------------------------------------
@tool(
    "Flight agent: build flight searches for a route and dates (Google Flights, Kayak, Skyscanner links ready to "
    "open), then research current fares with search_web_free and compare. Use airport or city codes when known "
    "(FAY, ATL, MIA). Never book or pay: the user books.",
    {"origin": {"type": "string", "description": "Airport/city code or city, e.g. FAY or Fayetteville NC"},
     "destination": {"type": "string"}, "depart": {"type": "string", "description": "YYYY-MM-DD"},
     "return_date": {"type": "string", "description": "YYYY-MM-DD, empty for one-way"},
     "adults": {"type": "integer"}, "cabin": {"type": "string", "enum": ["economy", "premium", "business", "first"]}},
    required=["origin", "destination", "depart"],
)
def flight_search(origin: str, destination: str, depart: str, return_date: str = "", adults: int = 1,
                  cabin: str = "economy") -> str:
    adults = max(1, int(adults or 1))
    o, d = origin.strip(), destination.strip()
    q = f"Flights to {d} from {o} on {depart}" + (f" through {return_date}" if return_date else " one way") + \
        (f" for {adults} adults" if adults > 1 else "") + ("" if cabin == "economy" else f" {cabin} class")
    links = {"Google Flights": "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q)}
    if re.fullmatch(r"[A-Za-z]{3}", o) and re.fullmatch(r"[A-Za-z]{3}", d):
        kd = f"{o.upper()}-{d.upper()}/{depart}" + (f"/{return_date}" if return_date else "")
        links["Kayak"] = f"https://www.kayak.com/flights/{kd}/{adults}adults?sort=bestflight_a"
        sd = depart.replace("-", "")[2:]
        sr = return_date.replace("-", "")[2:] if return_date else ""
        links["Skyscanner"] = (f"https://www.skyscanner.com/transport/flights/{o.lower()}/{d.lower()}/{sd}/" +
                               (f"{sr}/" if sr else "") + f"?adults={adults}")
    lines = [f"{o} to {d}, {depart}" + (f" returning {return_date}" if return_date else " one way") +
             f", {adults} adult{'s' if adults > 1 else ''}, {cabin}:"]
    lines += [f"- {k}: {v}" for k, v in links.items()]
    lines.append("Next: search_web_free for current fares on this route and airline military perks (many airlines "
                 "give extra free checked bags to active-duty members, more on orders); mention Space-A if the dates "
                 "are flexible. Open the best link for the user (open_website) and never book or pay.")
    return "\n".join(lines)
