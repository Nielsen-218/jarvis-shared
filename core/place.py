"""Where he is right now: the phone's position (the app asks the browser, he grants it once) and the desk PC's (Windows Location),
reduced to a town and a state. Only that label ever leaves the machine, and only when he asks something local ("any weather
advisories near me?"): never coordinates, never a street. Asked for by the owner 2026-11-15 ("I need him to have access to live
location on phone and PC"); core/localask.py turns a local question into a search for the label.

Each machine writes its OWN file (place-pc.json on the PC, place-phone.json on the laptop) because data/ syncs over Syncthing and two
writers of one file leave conflict copies. The newest fresh one wins; with none, his home from the household profile."""
import json
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from core import storage

FRESH_S = 6 * 3600             # a position older than this is not "where he is"
PC_REFRESH_S = 20 * 60         # the PC asks Windows again after this
MOVED_DEG = 0.02               # about 2 km: closer than that reuses the town already found
_FILES = {"pc": "place-pc.json", "phone": "place-phone.json"}
_lock = threading.Lock()
_pc_busy = {"at": 0.0}

_PS = r"""
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::Default)
$null = $w.TryStart($false, [TimeSpan]::FromSeconds(15))
$i = 0
while ($w.Position.Location.IsUnknown -and $i -lt 15) { Start-Sleep -Seconds 1; $i++ }
$p = $w.Position.Location
if ($p.IsUnknown) { 'unknown' } else { '{0:F5},{1:F5},{2:F0}' -f $p.Latitude, $p.Longitude, $p.HorizontalAccuracy }
"""


def _coarse(value: float) -> float:
    return round(float(value), 2)          # about 1 km: all the town lookup ever sees


def town_of(lat: float, lon: float) -> dict | None:
    """{"city", "state"} for a point (OpenStreetMap Nominatim, at one-kilometre precision), or None when it cannot say."""
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"format": "jsonv2", "zoom": 10, "addressdetails": 1, "lat": _coarse(lat), "lon": _coarse(lon)})
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "personal-assistant/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            address = (json.loads(response.read()) or {}).get("address") or {}
    except Exception:  # noqa: BLE001 - no town: the caller keeps the position without a label
        return None
    city = next((address[k] for k in ("city", "town", "village", "municipality", "hamlet", "county") if address.get(k)), "")
    state = address.get("state", "")
    if not city and not state:
        return None
    return {"city": city, "state": state}


def update(source: str, lat: float, lon: float, accuracy=None, uid=None) -> dict | None:
    """Record a position from "pc" or "phone". Returns what was stored (None for a position that is not on Earth)."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if source not in _FILES or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    old = storage.load_global(_FILES[source], {}) if hasattr(storage, "load_global") else {}
    town = None
    if old.get("city") or old.get("state"):
        if abs(old.get("lat", 999) - lat) < MOVED_DEG and abs(old.get("lon", 999) - lon) < MOVED_DEG:
            town = {"city": old.get("city", ""), "state": old.get("state", "")}
    if town is None:
        town = town_of(lat, lon) or {"city": "", "state": ""}
    record = {"lat": _coarse(lat), "lon": _coarse(lon), "accuracy": accuracy, "city": town["city"], "state": town["state"],
              "source": source, "at": time.time()}
    with _lock:
        storage.save_global(_FILES[source], record)
    return record


def read_pc():
    """The desk PC's own position from Windows Location: (lat, lon, accuracy) or None (denied, off, or no fix)."""
    import os
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", _PS], capture_output=True, text=True, timeout=40,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip().splitlines()
        text = out[-1] if out else ""
    except Exception:  # noqa: BLE001
        return None
    match = re.fullmatch(r"(-?\d+\.\d+),(-?\d+\.\d+),(\d+)", text)
    return (float(match.group(1)), float(match.group(2)), int(match.group(3))) if match else None


def refresh_pc(force: bool = False) -> None:
    """On the desk PC, ask Windows where it is in the background (never blocks a turn) when the last answer is old."""
    try:
        from core import pcbridge
        if not pcbridge.local_pc():
            return
    except Exception:  # noqa: BLE001
        return
    now = time.time()
    if not force and now - _pc_busy["at"] < PC_REFRESH_S:
        return
    _pc_busy["at"] = now

    def work():
        fix = read_pc()
        if fix:
            update("pc", fix[0], fix[1], fix[2])

    threading.Thread(target=work, name="place-pc", daemon=True).start()


def _home() -> dict | None:
    """His home from the household profile, as a town: the last resort when no device has reported."""
    try:
        from tools import kitchen
        lat, lon, _ = kitchen._home(kitchen._data()["household"])
    except Exception:  # noqa: BLE001
        return None
    cached = storage.load_global("place-home.json", {})
    if cached.get("city") and abs(cached.get("lat", 999) - _coarse(lat)) < MOVED_DEG and abs(cached.get("lon", 999) - _coarse(lon)) < MOVED_DEG:
        return cached
    town = town_of(lat, lon) or {"city": "", "state": ""}
    record = {"lat": _coarse(lat), "lon": _coarse(lon), "accuracy": None, "city": town["city"], "state": town["state"],
              "source": "home", "at": time.time()}
    if record["city"] or record["state"]:
        storage.save_global("place-home.json", record)
    return record


def current(allow_home: bool = True) -> dict | None:
    """The freshest known position as {"lat","lon","city","state","source","at"}, or None."""
    refresh_pc()
    found = [r for r in (storage.load_global(f, {}) for f in _FILES.values())
             if r.get("at") and time.time() - r["at"] <= FRESH_S and (r.get("city") or r.get("state"))]
    if found:
        return max(found, key=lambda r: r["at"])
    return _home() if allow_home else None


def label(allow_home: bool = True) -> str:
    """"Springfield, Illinois": the only part of his position a search may carry."""
    here = current(allow_home)
    if not here:
        return ""
    return ", ".join(p for p in (here.get("city"), here.get("state")) if p)


def where_am_i() -> str:
    """The spoken answer to "where am I?": the town, and where that came from, or why there is none."""
    here = current()
    if not here or not (here.get("city") or here.get("state")):
        return ("I don't know where you are yet, sir. Open the Jarvis app on your phone and allow location, or turn on Location "
                "in Windows settings, and I will.")
    town = ", ".join(p for p in (here.get("city"), here.get("state")) if p)
    source = {"pc": "from this PC's location", "phone": "from your phone", "home": "from your home address, not a live position"}.get(here["source"], "")
    return f"You're in {town}, sir, {source}.".replace(", .", ".")
