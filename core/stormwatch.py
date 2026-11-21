"""Storm Watch: the first protocol trigger driven by OUTSIDE data (owner 2026-11-16).

Pinned places (Springfield, Illinois and Haiti). JARVIS warns out loud and pings his phone when:
  * a hurricane or tropical storm is forecast near a pinned place (US National Hurricane Center: the active storms, each
    storm's forecast track and its official watches and warnings), or
  * a strong earthquake hits near one (US Geological Survey, M4.5+ past day), or
  * a severe weather alert covers one (US National Weather Service; it covers the United States only, so not Haiti).

Free, keyless, public sources only; three hosts are allowed and nothing about him is sent: the Weather Service is asked
for the whole state (area=VA) and the match to Springfield is made here.

Two levels. ALERT: said when he is at the PC (held while he is away or in a game; in quiet hours held until he is
back unless he is at the PC) and pushed to the phone as a normal notification (the phone keeps quiet hours). DANGER (an
NHC Hurricane or Storm Surge Warning naming Haiti; for Springfield the NWS's own Hurricane / Storm Surge / Tornado / Extreme
Wind / Tsunami Warning or any Extreme warning; a M6.5+ quake close by): said even over a game or to an empty room, and
pushed as urgent, which goes through quiet hours. NHC bullets for the US coast name capes, not towns, so Springfield's
official tropical warnings come from the NWS alerts for its county.

No repeat spam: one warning per storm per place, said again only when it escalates to DANGER; an NWS alert of one kind is
said once while it stays active (a new one after it ended is new); aftershocks near a place within 12 hours of a quake
that was said are folded into it unless clearly stronger (+0.3 magnitude) or higher in level.

Roles: the PC speaks; the always-on laptop pings the phone (so a phone alert still comes when the PC is off). A PC with
no laptop does both. Each machine keeps its own state file (Syncthing: one writer per file).

Honesty: a source that could not be reached is never reported as "nothing there"; after 3 hours down it is said once.
Everything external is data, never instructions."""
from __future__ import annotations

import html
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from core import storage
from core.words import plural

UA = "JarvisStormWatch/1.0 (personal assistant; keyless public feeds)"
HOSTS = {"www.nhc.noaa.gov", "earthquake.usgs.gov", "api.weather.gov"}
NHC_CURRENT = "https://www.nhc.noaa.gov/CurrentStorms.json"
USGS_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
NWS_AREA = "https://api.weather.gov/alerts/active?area={area}"
MAX_BODY = 12_000_000          # a big outbreak makes the whole-state NWS list large: never "down" just then
POLL_S = 300                    # one check every 5 minutes
SNAPSHOT_S = 180                # the globe and "storm watch" questions reuse a check this fresh

ALERT, DANGER = "alert", "danger"
RANK = {ALERT: 1, DANGER: 2}
KM_PER_MILE = 1.609344
STORM_KM = 300                  # a track point (now or forecast) this close counts as "near"
STORM_MIN_KT = 34               # tropical-storm strength at that point
QUAKE_ALERT = ((5.0, 250), (6.0, 600))     # (magnitude at least, within km)
QUAKE_DANGER = (6.5, 300)
QUAKE_MAX_AGE_H = 6             # an older quake is not news for an unprompted warning (a question still lists 24 h)
AFTERSHOCK_H = 12
FORGET_H = {"nhc": 72, "nws": 6, "usgs": 48}
NWS_GONE_S = 20 * 60            # an NWS alert missing from one poll may be a blip: forgotten after 20 minutes away
SOURCE_DOWN_S = 3 * 3600

# Pinned places. Points are towns (never his address); Springfield's alerts are matched by its county code (FIPS 17167).
PLACES = {
    "springfield": {"name": "Springfield, Illinois", "short": "Springfield", "points": [["Springfield", 39.80, -89.64]],
                "nws_area": "VA", "same": ["051650"], "nhc_words": []},
    "haiti": {"name": "Haiti", "short": "Haiti",
              "points": [["Port-au-Prince", 18.54, -72.34], ["Cap-Haitien", 19.76, -72.20], ["Gonaives", 19.45, -72.69],
                         ["Jeremie", 18.65, -74.11], ["Les Cayes", 18.20, -73.75], ["Jacmel", 18.23, -72.53],
                         ["Hinche", 19.15, -72.01]],
              "nws_area": "", "same": [], "nhc_words": ["haiti"]},
}
SOURCE_NAMES = {"nhc": "the National Hurricane Center", "usgs": "the US Geological Survey", "nws": "the National Weather Service"}

_CLASS = {"HU": "Hurricane", "TS": "Tropical Storm", "TD": "Tropical Depression", "STS": "Subtropical Storm",
          "SD": "Subtropical Depression", "PTC": "Potential Tropical Cyclone", "PC": "Post-Tropical Cyclone"}
NHC_DANGER = {"hurricane warning", "storm surge warning"}
NWS_DANGER = {"tornado warning", "hurricane warning", "extreme wind warning", "storm surge warning", "tsunami warning"}
NWS_TROPICAL = {"hurricane watch", "tropical storm warning", "tropical storm watch", "storm surge watch"}


# -- fetching ----------------------------------------------------------------------------------------------------------
def check_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname not in HOSTS:
        raise ValueError("blocked host")           # only the three public providers
    return url


class _AllowedRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is followed only to one of the same three hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_AllowedRedirects)


def _http(url: str, accept: str = "application/json", timeout: float = 15.0) -> bytes:
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with _OPENER.open(req, timeout=timeout) as r:  # noqa: S310 (host allow-listed above, redirects too)
        data = r.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise ValueError("response too large")
    return data


def _json(get, url: str, accept: str = "application/json") -> dict:
    return json.loads(get(url, accept))


_text_cache: dict[str, tuple[str, str]] = {}       # url -> (the advisory's update time, its text): fetched once per advisory


def _advisory(get, product: dict | None) -> str:
    url = str((product or {}).get("url") or "")
    if not url:
        return ""
    stamp = str((product or {}).get("fileUpdateTime") or (product or {}).get("advNum") or "")
    hit = _text_cache.get(url)
    if hit and hit[0] == stamp and stamp:
        return hit[1]
    raw = get(url, "text/html").decode("utf-8", "replace")
    text = pre_text(raw)
    _text_cache[url] = (stamp, text)
    return text


def pre_text(page: str) -> str:
    """NHC text products come as an HTML page with the bulletin in <pre>."""
    m = re.search(r"<pre[^>]*>(.*?)</pre>", page, re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1) if m else page))


def fetch(places: list[str], get=None) -> dict:
    """{source: {"ok", "error", ...data}} for nhc, usgs and nws (one entry per NWS area). Never raises."""
    get = get or _http
    out: dict = {}
    try:
        current = _json(get, NHC_CURRENT)
        storms = []
        for s in current.get("activeStorms") or []:
            if not str(s.get("id", "")).lower().startswith("al"):
                continue                       # both places are in the Atlantic basin
            storms.append({**s, "tcm": _advisory(get, s.get("forecastAdvisory")), "tcp": _advisory(get, s.get("publicAdvisory"))})
        out["nhc"] = {"ok": True, "storms": storms, "all": len(current.get("activeStorms") or [])}
    except Exception as error:  # noqa: BLE001 - a source down is reported as down, never as "nothing there"
        out["nhc"] = {"ok": False, "error": f"{type(error).__name__}: {error}"[:160]}
    try:
        out["usgs"] = {"ok": True, "features": _json(get, USGS_FEED).get("features") or []}
    except Exception as error:  # noqa: BLE001
        out["usgs"] = {"ok": False, "error": f"{type(error).__name__}: {error}"[:160]}
    areas = sorted({PLACES[p]["nws_area"] for p in places if p in PLACES and PLACES[p]["nws_area"]})
    for area in areas:
        try:
            out[f"nws:{area}"] = {"ok": True, "features": _json(get, NWS_AREA.format(area=area), "application/geo+json").get("features") or []}
        except Exception as error:  # noqa: BLE001
            out[f"nws:{area}"] = {"ok": False, "error": f"{type(error).__name__}: {error}"[:160]}
    return out


# -- parsing -----------------------------------------------------------------------------------------------------------
_LATLON = r"(\d{1,2}\.\d)([NS])\s+(\d{1,3}\.\d)([EW])"


def _ll(m, i: int) -> tuple[float, float]:
    lat = float(m.group(i)) * (1 if m.group(i + 1) == "N" else -1)
    lon = float(m.group(i + 2)) * (1 if m.group(i + 3) == "E" else -1)
    return lat, lon


def _at(day: int, hour: int, minute: int, base: datetime) -> datetime | None:
    """'26/0600Z' next to an advisory issued `base` -> the full UTC time (the month can roll over)."""
    for months in (0, 1, -1):
        y, m = base.year, base.month + months
        y, m = (y + 1, 1) if m == 13 else (y - 1, 12) if m == 0 else (y, m)
        try:
            when = datetime(y, m, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            continue
        if timedelta(days=-3) <= when - base <= timedelta(days=9):
            return when
    return None


_MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def issued(text: str, fallback: str = "") -> datetime | None:
    """'2100 UTC SAT OCT 25 2025' in a forecast advisory; else the feed's ISO issuance time."""
    m = re.search(r"\b(\d{2})(\d{2}) UTC \w{3} ([A-Z]{3}) (\d{1,2}) (\d{4})\b", text or "")
    if m and m.group(3) in _MONTHS and 1 <= int(m.group(4)) <= 31 and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return datetime(int(m.group(5)), _MONTHS[m.group(3)], int(m.group(4)), int(m.group(1)), int(m.group(2)), tzinfo=timezone.utc)
    try:
        when = datetime.fromisoformat(fallback.replace("Z", "+00:00")) if fallback else None
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when is not None and when.tzinfo is None else when


def parse_track(text: str, base: datetime | None) -> list[dict]:
    """The forecast advisory's centre now and its forecast / outlook points, each with its maximum wind (knots)."""
    points: list[dict] = []
    last = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not points and "CENTER LOCATED NEAR" in line and not line.startswith("REPEAT") and "WAS LOCATED" not in line:
            m = re.search(_LATLON + r"\s+AT\s+(\d{2})/(\d{2})(\d{2})Z", line)
            if m:
                lat, lon = _ll(m, 1)
                when = _at(int(m.group(5)), int(m.group(6)), int(m.group(7)), base) if base else None
                last = {"lat": lat, "lon": lon, "kt": None, "when": when, "kind": "now"}
                points.append(last)
            continue
        m = re.match(r"(FORECAST|OUTLOOK) VALID (\d{2})/(\d{2})(\d{2})Z\s+" + _LATLON, line)
        if m:
            lat, lon = _ll(m, 5)
            when = _at(int(m.group(2)), int(m.group(3)), int(m.group(4)), base) if base else None
            last = {"lat": lat, "lon": lon, "kt": None, "when": when, "kind": m.group(1).lower()}
            points.append(last)
            continue
        m = re.match(r"MAX SUSTAINED WINDS\s+(\d+) KT", line) or re.match(r"MAX WIND\s+(\d+) KT", line)
        if m and last is not None and last["kt"] is None:
            last["kt"] = int(m.group(1))
    return points


_WW_HEAD = re.compile(r"^An? (?P<kind>(?:Hurricane|Tropical Storm|Storm Surge)(?: Warning| Watch)) (?:is )?in effect for", re.I)


def parse_warnings(text: str) -> list[tuple[str, str]]:
    """[(kind, area)] from the public advisory's 'SUMMARY OF WATCHES AND WARNINGS IN EFFECT' block."""
    _, found, block = (text or "").partition("SUMMARY OF WATCHES AND WARNINGS IN EFFECT:")
    if not found:
        return []
    out: list[list[str]] = []
    kind = ""
    for raw in block.splitlines():
        line = raw.strip()
        if re.match(r"^(?:DISCUSSION AND OUTLOOK|Interests elsewhere|For storm information)", line) or re.search(r"\bmeans\b", line):
            break
        head = _WW_HEAD.match(line)
        if head:
            kind = " ".join(w.capitalize() for w in head.group("kind").split())
            continue
        if "in effect" in line.lower():
            kind = ""                               # "The Tropical Storm Watch remains in effect for...": not the last heading
            continue
        if line.startswith("*") and kind:
            out.append([kind, line.lstrip("* ").strip()])
        elif line and out and kind and not line.startswith("*"):
            out[-1][1] += " " + line                # a bullet wrapped onto the next line
        elif not line:
            pass
    return [(k, re.sub(r"\s+", " ", a).strip().rstrip(".")) for k, a in out]


def _mentions(place: dict, area: str) -> bool:
    low = area.lower()
    low = re.sub(r"\bborder (?:with|of) (?:the )?[a-z]+(?: [a-z]+)?", " ", low)   # "...to the border with Haiti" is not Haiti
    low = re.sub(r"\b[a-z]+[ -]border\b", " ", low)                                 # nor is "...to the Haiti border"
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in place.get("nhc_words") or [])


# -- geometry ----------------------------------------------------------------------------------------------------------
def km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    p = math.pi / 180
    h = math.sin((b_lat - a_lat) * p / 2) ** 2 + math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin((b_lon - a_lon) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1.0, h)))


def nearest(place: dict, lat: float, lon: float) -> tuple[float, str]:
    return min((km(lat, lon, plat, plon), name) for name, plat, plon in place["points"])


def _inside(lat: float, lon: float, geom) -> bool:
    """Is the point inside an alert's polygon (outer rings only)?"""
    try:
        rings = [geom["coordinates"][0]] if geom["type"] == "Polygon" else [p[0] for p in geom["coordinates"]] if geom["type"] == "MultiPolygon" else []
    except (KeyError, IndexError, TypeError):
        return False
    for ring in rings:
        hit = False
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1:
                hit = not hit
        if hit:
            return True
    return False


def _miles(km_: float) -> str:
    mi = km_ / KM_PER_MILE
    return f"{int(round(mi / 10.0) * 10) if mi >= 50 else max(1, int(round(mi)))} miles"


def _in(hours: float) -> str:
    if hours <= 3:
        return "within a few hours"
    if hours < 36:
        return "in about " + plural(int(round(hours)), "hour")
    return "in about " + plural(int(round(hours / 24)), "day")


def _ago(seconds: float) -> str:
    minutes = max(1, int(seconds // 60))
    if minutes < 90:
        return plural(minutes, "minute") + " ago"
    return plural(int(round(minutes / 60)), "hour") + " ago"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    return "" if not items else items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _short(area: str, limit: int = 110) -> str:
    return area if len(area) <= limit else area[:limit].rsplit(" ", 1)[0] + "..."


# -- the rules ---------------------------------------------------------------------------------------------------------
def _along(track: list[dict]) -> list[dict]:
    """The track with points between its forecast points: they are 12-24 h apart, and a fast storm can pass right over a
    place between two points that are each farther than STORM_KM from it (review 2026-11-16)."""
    out: list[dict] = []
    for a, b in zip(track, track[1:]):
        out.append(a)
        if a["when"] is None or b["when"] is None:
            continue
        for i in range(1, 8):
            f = i / 8
            kt = b["kt"] if a["kt"] is None else a["kt"] if b["kt"] is None else a["kt"] + (b["kt"] - a["kt"]) * f
            when = a["when"] + (b["when"] - a["when"]) * f
            out.append({"lat": a["lat"] + (b["lat"] - a["lat"]) * f, "lon": a["lon"] + (b["lon"] - a["lon"]) * f,
                        "kt": kt, "when": when, "kind": "forecast"})
    return out + track[-1:]


def storm_events(nhc: dict, places: list[str], now: datetime) -> list[dict]:
    """Warnings per storm per place. A storm whose forecast can't be read goes into nhc["unread"]: then no answer may say
    that no storm is near (only its current position is checked)."""
    out = []
    nhc["unread"] = []
    nhc["unread_ids"] = {}
    for s in nhc.get("storms") or []:
        title = f"{_CLASS.get(str(s.get('classification', '')).upper(), 'Storm')} {s.get('name', '')}".strip()
        before = len(nhc["unread"])
        try:
            out += _one_storm(s, title, nhc, places, now)
        except Exception as error:  # noqa: BLE001 - one odd advisory never silences the other storms, quakes or alerts
            nhc["unread"].append(f"{title} ({type(error).__name__})")
        if len(nhc["unread"]) > before:
            nhc["unread_ids"][str(s.get("id") or title).lower()] = title
    return out


def _one_storm(s: dict, title: str, nhc: dict, places: list[str], now: datetime) -> list[dict]:
    out = []
    base = issued(s.get("tcm", ""), str((s.get("forecastAdvisory") or {}).get("issuance") or ""))
    if base is not None and abs((now - base).total_seconds()) > 3 * 86400:
        nhc["unread"].append(f"{title} (its last forecast is days old)")
        return []
    track = parse_track(s.get("tcm", ""), base)
    if not track:
        nhc["unread"].append(title)
        if s.get("latitudeNumeric") is not None:
            track = [{"lat": float(s["latitudeNumeric"]), "lon": float(s["longitudeNumeric"]),
                      "kt": int(float(s.get("intensity") or 0)) or None, "when": None, "kind": "now"}]
    warnings = parse_warnings(s.get("tcp", ""))
    adv = str((s.get("publicAdvisory") or {}).get("advNum") or (s.get("forecastAdvisory") or {}).get("advNum") or "").lstrip("0")
    path = _along(track)
    for pid in places:
        place = PLACES.get(pid)
        if not place:
            continue
        best = None
        for pt in path:
            if pt["kt"] is not None and pt["kt"] < STORM_MIN_KT:
                continue
            if pt["when"] is not None and pt["when"] < now - timedelta(hours=6):
                continue
            d, near = nearest(place, pt["lat"], pt["lon"])
            if d <= STORM_KM and (best is None or d < best[0]):
                best = (d, near, pt)
        hits = [(k, a) for k, a in warnings if _mentions(place, a)]
        if not best and not hits:
            continue
        kinds = list(dict.fromkeys(k for k, _ in hits))
        level = DANGER if any(k.lower() in NHC_DANGER for k in kinds) else ALERT
        said = []
        if hits:
            areas = list(dict.fromkeys(a for _, a in hits))
            said.append(f"The National Hurricane Center has {_join(['a ' + k for k in kinds])} for {_short(areas[0])}.")
        if best:
            d, near, pt = best
            wind = f", with winds near {int(round(pt['kt'] * 1.15078 / 5.0) * 5)} mph" if pt["kt"] else ""
            if pt["kind"] == "now" or pt["when"] is None:
                said.append(f"It is about {_miles(d)} from {near}{wind}.")
            else:
                said.append(f"It is forecast to pass about {_miles(d)} from {near} {_in((pt['when'] - now).total_seconds() / 3600)}{wind}.")
        lead = f"Danger: {title} near {place['short']}." if level == DANGER else f"Storm watch: {title} near {place['short']}."
        src = f" Source: National Hurricane Center{', advisory ' + adv if adv else ''}."
        cur = track[0] if track else None
        out.append({"key": f"nhc:{str(s.get('id', '')).lower()}:{pid}", "source": "nhc", "place": pid, "level": level,
                    "kind": "storm", "title": f"{title} near {place['short']}", "text": " ".join([lead, *said]) + src,
                    "lat": cur["lat"] if cur else None, "lon": cur["lon"] if cur else None,
                    "time": now.timestamp(), "url": "https://www.nhc.noaa.gov/"})
    return out


def quake_events(usgs: dict, places: list[str], now: datetime, max_age_h: float = QUAKE_MAX_AGE_H) -> list[dict]:
    out = []
    for f in usgs.get("features") or []:
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
        if len(g) < 2 or p.get("mag") is None or p.get("time") is None or p.get("type", "earthquake") != "earthquake":
            continue
        try:
            mag, when = float(p["mag"]), datetime.fromtimestamp(float(p["time"]) / 1000, timezone.utc)
            g = [float(g[0]), float(g[1])]
        except (TypeError, ValueError, OverflowError, OSError):
            continue                       # one malformed row is skipped, never the whole feed
        age = (now - when).total_seconds()
        if age < -600 or age > max_age_h * 3600:
            continue
        for pid in places:
            place = PLACES.get(pid)
            if not place:
                continue
            d, near = nearest(place, g[1], g[0])
            if mag >= QUAKE_DANGER[0] and d <= QUAKE_DANGER[1]:
                level = DANGER
            elif any(mag >= m and d <= k for m, k in QUAKE_ALERT):
                level = ALERT
            else:
                continue
            where = str(p.get("place") or "").strip()
            lead = "Danger: a" if level == DANGER else "Storm watch: a"
            text = (f"{lead} magnitude {mag:.1f} earthquake hit{' ' + where if where else ''} {_ago(max(0.0, age))}, "
                    f"about {_miles(d)} from {near}. Source: US Geological Survey.")
            out.append({"key": f"usgs:{f.get('id')}", "source": "usgs", "place": pid, "level": level, "kind": "earthquake",
                        "title": f"M{mag:.1f} earthquake near {place['short']}", "text": text, "lat": g[1], "lon": g[0],
                        "magnitude": mag, "time": when.timestamp(), "url": str(p.get("url") or "")})
    return out


def _until(iso: str, now: datetime) -> str:
    try:
        end = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    clock = end.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
    local_now = now.astimezone(end.tzinfo) if end.tzinfo else now
    return f" until {clock}" + ("" if end.date() == local_now.date() else f" {end.strftime('%A')}")


def nws_level(props: dict) -> str | None:
    event, severity = str(props.get("event") or "").lower(), str(props.get("severity") or "")
    if event in NWS_DANGER or (severity == "Extreme" and event.endswith(("warning", "emergency"))):
        return DANGER
    if event in NWS_TROPICAL or (severity in ("Severe", "Extreme") and event.endswith(("warning", "emergency"))):
        return ALERT
    return None                        # watches, advisories, statements, freeze watches: not a warning


def alert_events(nws: dict, places: list[str], now: datetime) -> list[dict]:
    best: dict[str, dict] = {}
    for f in nws.get("features") or []:
        p = f.get("properties") or {}
        if str(p.get("status", "Actual")) != "Actual" or str(p.get("messageType", "")) == "Cancel":
            continue
        try:
            ends = datetime.fromisoformat(str(p.get("ends") or p.get("expires")))
            if ends.tzinfo and ends < now:
                continue
        except ValueError:
            ends = None
        level = nws_level(p)
        if not level:
            continue
        for pid in places:
            place = PLACES.get(pid)
            if not place or not place.get("same"):
                continue
            same = set((p.get("geocode") or {}).get("SAME") or [])
            if not (same & set(place["same"]) or any(_inside(lat, lon, f.get("geometry")) for _, lat, lon in place["points"])):
                continue
            event = str(p.get("event"))
            key = f"nws:{pid}:{event.lower()}"
            until = _until(str(p.get("ends") or p.get("expires") or ""), now)
            lead = "Danger:" if level == DANGER else "Storm watch:"
            try:
                onset = datetime.fromisoformat(str(p.get("onset") or p.get("effective") or p.get("sent"))).timestamp()
            except ValueError:
                onset = None
            ev = {"key": key, "source": "nws", "place": pid, "level": level, "kind": "alert", "title": f"{event} for {place['short']}",
                  "onset": onset,
                  "text": f"{lead} the National Weather Service has a {event} for {place['short']}{until}. Source: National Weather Service.",
                  "lat": place["points"][0][1], "lon": place["points"][0][2], "time": now.timestamp(),
                  "ends": ends.timestamp() if ends else None, "url": "https://www.weather.gov/akq/", "headline": str(p.get("headline") or "")[:300]}
            old = best.get(key)
            if old is None or (ev["ends"] or 0) > (old["ends"] or 0):
                best[key] = ev
    return list(best.values())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def evaluate(data: dict, places: list[str], now: datetime | None = None, quake_age_h: float = QUAKE_MAX_AGE_H) -> list[dict]:
    now = now or _now()
    out: list[dict] = []
    for key, src in list(data.items()):
        if not (src or {}).get("ok"):
            continue
        try:
            if key == "nhc":
                out += storm_events(src, places, now)
            elif key == "usgs":
                out += quake_events(src, places, now, quake_age_h)
            elif key.startswith("nws:"):
                out += alert_events(src, places, now)
        except Exception as error:  # noqa: BLE001 - its data could not be read: that source counts as down, not as clear
            data[key] = {**src, "ok": False, "error": f"couldn't read its data ({type(error).__name__})"}
    return sorted(out, key=lambda e: (-RANK[e["level"]], -(e.get("magnitude") or 0), e["place"], e["key"]))


# -- state: what was said (one file per machine) -----------------------------------------------------------------------
def _state_name() -> str:
    from core import pcbridge
    return "stormwatch-pc.json" if pcbridge.local_pc() else "stormwatch-server.json"


def _sources_ok(data: dict) -> dict[str, bool]:
    out = {"nhc": bool((data.get("nhc") or {}).get("ok")), "usgs": bool((data.get("usgs") or {}).get("ok"))}
    nws = [v.get("ok") for k, v in data.items() if k.startswith("nws:")]
    if nws:
        out["nws"] = all(nws)
    return out


def pending(places: list[str], data: dict, now: datetime | None = None) -> tuple[list[dict], list[str]]:
    """The warnings to give now (new, or escalated to DANGER) and the notices about a source down for 3 hours. Updates
    what was seen and forgets what is long gone; the caller marks each warning given with `mark`."""
    now = now or _now()
    ts = now.timestamp()
    state = storage.load(_state_name(), {})
    said = state.setdefault("said", {})
    health = state.setdefault("sources", {})
    events = evaluate(data, places, now)
    ok = _sources_ok(data)                          # after evaluate: a source whose data could not be read is down
    current = {e["key"] for e in events}
    out = []
    for ev in events:
        row = said.get(ev["key"])
        if row is not None and row.get("folded") and float(ev.get("magnitude") or 0) > float(row.get("mag") or 0):
            said.pop(ev["key"], None)               # USGS revised a folded aftershock upward: judge it again
            row = None
        if (row is not None and ev["source"] == "nws" and ev.get("onset") and row.get("onset") and row.get("ends")
                and ev["onset"] > row["onset"] and ev["onset"] >= row["ends"]):
            said.pop(ev["key"], None)               # a NEW warning that begins after the one he was told about had ended
            row = None                              # (the same warning keeps its onset through every update)
        if row is not None and row.get("unheard"):
            said.pop(ev["key"], None)               # its held sentence was withdrawn before he heard it: it is news again
            row = None
        if row is not None:
            row["seen"] = ts
            row.pop("gone_since", None)
            if ev.get("ends"):
                row["ends"] = max(float(row.get("ends") or 0), ev["ends"])
            if RANK[ev["level"]] <= RANK.get(row.get("level"), 0):
                continue
        elif ev["source"] == "usgs" and _folded(ev, ts, [*said.values(), *out]):
            said[ev["key"]] = {"level": ev["level"], "at": ts, "seen": ts, "source": "usgs", "place": ev["place"],
                               "mag": ev.get("magnitude"), "folded": True}
            continue
        out.append(ev)
    gone = []
    for key, row in list(said.items()):
        src = row.get("source") or key.split(":", 1)[0]
        if key in current or not ok.get(src, False):
            continue                                # still there, or its source is down: never forgotten on a failure
        # An NWS alert gone for 20 minutes is forgotten: the next Tornado Warning is a new one (review 2026-11-16); a one-poll
        # blip is not. A storm that moved away is kept 3 days (it may turn back), a quake 2 days (the aftershock anchor).
        if src == "nhc":
            gone.append(key)                        # the storm is no longer near: a held sentence about it is withdrawn now
        if src == "nws":
            row.setdefault("gone_since", ts)
            forget = ts - float(row["gone_since"]) >= NWS_GONE_S
        else:
            forget = ts - float(row.get("seen", row.get("at", 0))) > FORGET_H.get(src, 24) * 3600
        if forget:
            said.pop(key, None)
            if src == "nws":
                gone.append(key)                    # ended (not a one-poll blip): its held sentence is withdrawn
        # a quake stays news when he comes back (core.attention drops held sentences after 12 h)
    notices = []
    told = state.setdefault("unread_told", {})
    unread = ((data.get("nhc") or {}).get("unread_ids") or {}) if ok.get("nhc") else {}
    for sid, title in unread.items():
        if sid not in told:                     # once per storm: its forecast can't be read, so only its position is watched
            notices.append(f"Storm watch can't read the Hurricane Center's forecast for {title}, so I'm only watching where it "
                           "is now, not where it's heading.")
        told[sid] = ts                          # refreshed while unread: told again only after a week of being readable
    for sid, at in list(told.items()):
        if sid not in unread and ts - float(at) > 7 * 86400:
            told.pop(sid, None)
    for src, good in ok.items():
        h = health.setdefault(src, {})
        if good:
            if h.get("told"):
                notices.append(f"Storm watch can reach {SOURCE_NAMES[src]} again.")
            health[src] = {"ok_at": ts}
            continue
        h.setdefault("down_since", ts)
        if ts - float(h["down_since"]) >= SOURCE_DOWN_S and not h.get("told"):
            h["told"] = ts
            notices.append(f"Storm watch hasn't been able to reach {SOURCE_NAMES[src]} for {plural(int((ts - float(h['down_since'])) // 3600), 'hour')}, "
                           f"so I can't warn you about {_WHAT[src]} until it's back.")
    for key in _withdraw(gone):                     # withdrawn unheard: if it comes back, he is told then
        if key in said:
            said[key]["unheard"] = True
    storage.save(_state_name(), state)
    return out, notices


def _folded(ev: dict, ts: float, rows: list[dict]) -> bool:
    """An aftershock: a quake already said for this place in the last 12 h, at this level or higher, and not clearly
    weaker than this one. Folded quakes never anchor (a chain of aftershocks does not stretch the window)."""
    mag = float(ev.get("magnitude") or 0)
    for r in rows:
        if r.get("source") != "usgs" or r.get("place") != ev["place"] or r.get("folded"):
            continue
        if ts - float(r.get("at", ts)) >= AFTERSHOCK_H * 3600 or RANK[ev["level"]] > RANK.get(r.get("level"), 0):
            continue
        anchor = r.get("mag", r.get("magnitude"))
        if anchor is not None and mag <= float(anchor) + 0.3:
            return True
    return False


_WHAT = {"nhc": "hurricanes", "usgs": "earthquakes", "nws": "weather alerts for Springfield"}


def mark(ev: dict, now: datetime | None = None) -> None:
    ts = (now or _now()).timestamp()
    state = storage.load(_state_name(), {})
    said = state.setdefault("said", {})
    said[ev["key"]] = {"level": ev["level"], "at": ts, "seen": ts, "source": ev["source"], "place": ev["place"],
                       "mag": ev.get("magnitude"), "ends": ev.get("ends"), "onset": ev.get("onset")}
    storage.save(_state_name(), state)


def _withdraw(keys: list[str]) -> list[str]:
    """A held warning whose alert has ended is no longer news when he comes back. -> the keys whose sentence was still
    held (never heard): if that alert comes back, it is said then (review 2026-11-16: a storm drifting out of range for one
    advisory and back must not end in silence)."""
    out: list[str] = []
    if not keys:
        return out
    try:
        from core import attention
        for key in keys:
            if any([attention.withdraw(f"storm:{key}:{level}") for level in (ALERT, DANGER)]):
                out.append(key)
    except Exception:  # noqa: BLE001
        pass
    return out


# -- delivery (called by the protocol's steps) -------------------------------------------------------------------------
def quiet_now(uid: str | None = None) -> bool:
    from core import notify, users
    try:
        return notify.in_quiet_hours(notify.prefs(uid or users.current()))
    except Exception:  # noqa: BLE001
        return False


def speak(ev: dict) -> str:
    """Say it through core.attention (the one judge of unprompted sentences). -> what happened, in words."""
    from core import attention
    key = f"storm:{ev['key']}:{ev['level']}"
    if ev["level"] == DANGER:
        sit = {**attention.situation(), "present": True}       # said even to an empty room or over a game
        verdict = attention.deliver(ev["text"], attention.CRITICAL, key=key, sit=sit)
        return {"said": "said it out loud", "duplicate": "it was already said"}.get(verdict, f"{verdict}")
    if quiet_now() and not attention.situation().get("present"):
        # LOW waits for his return (attention's later flush skips LOW); at the PC he is awake, so it is said (HIGH below)
        verdict = attention.deliver(ev["text"], attention.LOW, key=key)
        return {"held": "quiet hours: held until you're back at the PC", "duplicate": "it was already held"}.get(verdict, verdict)
    verdict = attention.deliver(ev["text"], attention.HIGH, key=key)
    return {"said": "said it out loud", "held": "held until you're back at the PC (away or in a game)",
            "duplicate": "it was already held"}.get(verdict, verdict)


def ping(ev: dict) -> str:
    from core import notify, users
    urgent = ev["level"] == DANGER
    title = "DANGER - Storm Watch" if urgent else "Storm Watch"
    notify.notify(title, ev["text"], users.current(), "weather", urgent=urgent)
    if urgent:
        return "sent to your phone as urgent (it goes through quiet hours; delivery isn't confirmed)"
    return ("sent to your phone's notification list; quiet hours hold the buzz" if quiet_now()
            else "sent to your phone (delivery isn't confirmed)")


def laptop_pings() -> bool:
    """On a PC next to the always-on laptop, the laptop sends the phone alerts (it runs when the PC is off)."""
    from core import pcbridge
    if not pcbridge.local_pc():
        return False
    from tools import vault
    return bool(vault._remote())


# -- the check the trigger runs, the answer to "storm watch", the globe's feed -----------------------------------------
_snap_lock = threading.Lock()
_snapshot: dict = {}


def check(places: list[str], get=None, now: datetime | None = None) -> dict:
    """Fetch every source once and keep the result for the globe and questions."""
    data = fetch(places, get)
    with _snap_lock:
        _snapshot.update({"at": time.time(), "places": list(places), "data": _own(data)})
    return data


def _own(data: dict) -> dict:
    """A copy a reader may evaluate: evaluate() only replaces a source's entry and writes nhc's unread lists, so one level
    is enough (a deep copy of a 12 MB outbreak list under the lock stalled every reader: review 2026-11-16)."""
    return {k: dict(v) if isinstance(v, dict) else v for k, v in data.items()}


def latest(places: list[str], get=None, max_age: float = SNAPSHOT_S) -> dict:
    with _snap_lock:
        snap = dict(_snapshot)
    if snap.get("data") and time.time() - snap.get("at", 0) < max_age and set(places) <= set(snap.get("places") or []):
        return _own(snap["data"])
    return check(places, get)


def _names(places: list[str]) -> str:
    return _join([PLACES[p]["name"] for p in places if p in PLACES])


def report(places: list[str], get=None, now: datetime | None = None) -> str:
    """'Storm watch protocol' / 'storm watch': what is near his places RIGHT NOW (not only what is new), with sources;
    a source that failed is said as failed, never as all clear."""
    now = now or _now()
    data = latest(places, get)
    events = evaluate(data, places, now, quake_age_h=24)
    ok = _sources_ok(data)
    down = [SOURCE_NAMES[s] for s, good in ok.items() if not good]
    unread = (data.get("nhc") or {}).get("unread") or [] if ok.get("nhc") else []
    us_only = any(p in PLACES and not PLACES[p]["same"] for p in places)
    if events:
        out = " ".join(e["text"] for e in events[:4])
        if len(events) > 4:
            out += f" And {len(events) - 4} more on the World Intelligence globe."
    else:
        checked = [n for s, n in SOURCE_NAMES.items() if ok.get(s)]
        if not checked and not unread:
            return f"I couldn't reach any storm watch source just now ({_join(down)}), so I can't tell you anything about {_names(places)}."
        clear = []
        if ok.get("nhc") and not unread:
            n = (data.get("nhc") or {}).get("all") or 0
            clear.append("no hurricanes or tropical storms near them" + ("" if n else " (no active storms at all)"))
        if ok.get("usgs"):
            clear.append("no strong earthquakes nearby in the last day")
        if ok.get("nws"):
            clear.append("no severe weather alerts for " + _join([PLACES[p]["short"] for p in places if PLACES.get(p, {}).get("same")]))
        out = f"Storm watch: all clear for {_names(places)} right now: {_join(clear)}."
        out += f" Checked with {_join(checked)}."
    if unread:
        out += (f" I couldn't read the Hurricane Center's forecast for {_join(unread)}, so I can't say whether "
                f"{'it is' if len(unread) == 1 else 'they are'} heading your way.")
    if down:
        out += f" I couldn't reach {_join(down)}, so that part is unknown."
    if us_only and ok.get("nws"):
        out += " The Weather Service's alerts cover the US only, not Haiti."
    return out


def globe_feed(places: list[str], get=None) -> dict:
    """The World Intelligence 'Storm Watch' layer: the pinned places and what is active near them now."""
    now = _now()
    data = latest(places, get)
    with _snap_lock:
        at = _snapshot.get("at")
    events = evaluate(data, places, now, quake_age_h=24)
    items = []
    for pid in places:
        place = PLACES.get(pid)
        if place:
            name, lat, lon = place["points"][0]
            items.append({"id": f"pin:{pid}", "kind": "pinned", "title": f"{place['name']} (pinned)", "lat": lat, "lon": lon,
                          "area": place["name"], "provider": "Storm Watch", "feed": "stormwatch",
                          "description": "Watched for storms (NHC), strong quakes (USGS)" + (" and severe weather alerts (NWS)." if place["same"] else "; the NWS doesn't cover it.")})
    for e in events:
        items.append({"id": e["key"], "kind": e["kind"], "title": e["title"], "lat": e.get("lat"), "lon": e.get("lon"),
                      "level": e["level"], "severity": "DANGER" if e["level"] == DANGER else "Alert", "description": e["text"],
                      "time": e.get("time"), "url": e.get("url"), "magnitude": e.get("magnitude"), "feed": "stormwatch",
                      "provider": {"nhc": "US National Hurricane Center", "usgs": "USGS", "nws": "US National Weather Service"}[e["source"]],
                      "area": PLACES[e["place"]]["name"]})
    ok = _sources_ok(data)
    unread = (data.get("nhc") or {}).get("unread") or [] if ok.get("nhc") else []
    problems = [f"unreachable: {_join([SOURCE_NAMES[s] for s, g in ok.items() if not g])}"] if not all(ok.values()) else []
    if unread:
        problems.append(f"forecast unreadable for {_join(unread)}")
    return {"ok": any(ok.values()), "source": "Storm Watch (NHC, USGS, NWS)", "source_url": "https://www.nhc.noaa.gov/",
            "fetched": at, "stale": bool(problems), "items": items, "count": len(events),
            "danger": sum(1 for e in events if e["level"] == DANGER), "sources": ok,
            "error": ("; ".join(problems).capitalize()) if problems else None}
