"""Garmin -> Jarvis through Android Health Connect: the server side.

The Garmin Connect phone app writes his watch data into Android Health Connect; the Jarvis Link app on his phone
(android/jarvis-link, HealthSync.java) reads it with his permission and POSTs compact summaries to
POST /api/link/health. Nothing here talks to Garmin and nothing logs in anywhere: the old direct login
(garmin_login.py + tools/fitness.garmin_sync) is left as it was, and its data["garmin"] is merged with this.

Storage (one writer per file: the phone posts to ONE server, the data folder syncs between machines with a delay):
  fitness/health-<machine>.json      what the phone posted to THIS machine: {"v", "records": {kind: {id: rec}},
                                     "last_sync", "last_post", "garmin_found", "sources", "counts", "app"}
  fitness/health-ask-<machine>.json  {"at", "why"}: this machine would like a sync (a workout opened, the page asked)
Readers merge every machine's files; the record with the newer "at" wins. Kept 400 days, capped per kind.

What Health Connect gives Jarvis is MEASURED data only. Garmin's own scores (Body Battery, Training Readiness, HRV
status, stress, sleep score) are not shared through Health Connect, so they stay None and no text mentions them.

The phone's payload (every field optional; unknown fields are dropped, out-of-range values are dropped one by one):
  {"v": 1, "app": "1.2", "final": true, "garmin_found": true, "sources": ["com.garmin.android.apps.connectmobile"],
   "sleep":      [{"start", "end", "minutes"?, "deep_min"?, "light_min"?, "rem_min"?, "awake_min"?, "src"?}],
   "resting_hr": [{"date", "bpm"}],             "hr_daily": [{"date", "min", "avg", "max"}],
   "hrv":        [{"date", "rmssd_ms"}],        "steps":    [{"date", "count"}],      "calories": [{"date", "kcal"}],
   "exercise":   [{"start", "end", "type", "type_id"?, "title"?, "distance_m"?, "avg_hr"?, "max_hr"?, "kcal"?,
                   "avg_speed_mps"?}],
   "weight":     [{"time", "kg"}],              "vo2max":   [{"time", "value"}]}
Times are ISO 8601 with the phone's zone offset; "date" is the phone's local calendar day. Weight arrives in kg and
is stored in pounds (1 kg = 2.20462 lb)."""
import math
import statistics
import time
from datetime import date, datetime, timedelta, timezone

from core import storage
from core.words import plural
from tools.registry import tool

GARMIN_PACKAGE = "com.garmin.android.apps.connectmobile"
KINDS = ("sleep", "resting_hr", "hr_daily", "hrv", "exercise", "steps", "weight", "vo2max", "calories")
CAP = {"sleep": 500, "resting_hr": 450, "hr_daily": 450, "hrv": 450, "exercise": 1500, "steps": 450, "weight": 1000,
       "vo2max": 500, "calories": 450}
KEEP_DAYS = 400
MAX_ITEMS = 3000              # per list in one post
KG_TO_LB = 2.20462
RUN_TYPES = ("running", "running_treadmill")
SYNC_EVERY = 3 * 3600         # s: the morning window asks for a sync when the last one is older than this
ASK_TTL = 600                 # s: a request for a sync (workout opened, page button) stays open this long
POST_QUIET = 600              # s: after any post (even an unfinished sync) Jarvis does not ask again for this long
RHR_ABOVE = 7                 # bpm above the 14-day median that counts as a low-recovery sign
RHR_MIN_DAYS = 7              # prior days with a resting HR needed before the rule may fire
NAP_MAX_H = 3                 # a daytime session (10:00-19:00) shorter than this is a nap, never night sleep
MIN_NIGHT_H = 3               # a night shorter than this is a failed recording: never an automatic check-in
JOIN_GAP_S = 90 * 60          # after the night, a session starting within 90 min of its end is the same sleep
SETTLE_S = 45 * 60            # a night is used only once it ended at least 45 min ago (not still asleep)


def clock() -> float:
    """Wall time in seconds (tests pin it)."""
    return time.time()


def machine() -> str:
    from core import thread
    return thread.machine()


def _machines() -> tuple[str, ...]:
    return ("pc", "server")


def _today() -> date:
    return datetime.fromtimestamp(clock()).date()


class HealthError(ValueError):
    """A post that was refused as a whole; the message is a sentence."""


# ---- validation -------------------------------------------------------------------------------------------------------
def _num(v, lo: float, hi: float) -> float | None:
    """A number in [lo, hi], else None. Type and size are checked BEFORE float(): a 400-digit integer from JSON would
    raise OverflowError there."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, int) and not -10 ** 15 <= v <= 10 ** 15:
        return None
    try:
        v = float(v)
    except (OverflowError, ValueError):
        return None
    return v if math.isfinite(v) and lo <= v <= hi else None


def _text(v, limit: int) -> str | None:
    if not isinstance(v, str):
        return None
    v = "".join(ch if ch.isprintable() else " " for ch in v).strip()
    return v[:limit] or None


def _day(v) -> date | None:
    """'2026-11-19' -> a date within the last KEEP_DAYS days and not more than a day ahead, else None."""
    if not isinstance(v, str) or len(v) != 10:
        return None
    try:
        d = date.fromisoformat(v)
    except ValueError:
        return None
    today = _today()
    return d if today - timedelta(days=KEEP_DAYS) <= d <= today + timedelta(days=1) else None


def _when(v) -> datetime | None:
    """An ISO time WITH a zone offset (the phone's), within the same window as _day; None otherwise."""
    if not isinstance(v, str) or not 10 <= len(v) <= 40:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    now = datetime.fromtimestamp(clock(), timezone.utc)
    if not now - timedelta(days=KEEP_DAYS) <= t <= now + timedelta(days=1):
        return None
    return t


def _key(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_sleep(r: dict) -> dict | None:
    start, end = _when(r.get("start")), _when(r.get("end"))
    if not start or not end or end <= start:
        return None
    minutes = (end - start).total_seconds() / 60
    if not 0 < minutes <= 16 * 60:
        return None
    out = {"id": "sleep|" + _key(start), "date": end.date().isoformat(), "start": start.isoformat(), "end": end.isoformat(),
           "hours": round(minutes / 60, 2)}
    told = _num(r.get("minutes"), 1, 16 * 60)              # asleep time the phone worked out (stages minus awake)
    if told is not None and told <= minutes + 1:
        out["hours"] = round(told / 60, 2)
    for stage in ("deep", "light", "rem", "awake"):
        m = _num(r.get(f"{stage}_min"), 0, 16 * 60)
        if m is not None and m <= minutes + 1:
            out[f"{stage}_h"] = round(m / 60, 2)
    return out


def _clean_day_value(kind: str, r: dict) -> dict | None:
    d = _day(r.get("date"))
    if not d:
        return None
    iso = d.isoformat()
    if kind == "resting_hr":
        v = _num(r.get("bpm"), 25, 120)
        return {"id": f"rhr|{iso}", "date": iso, "bpm": round(v)} if v is not None else None
    if kind == "hrv":
        v = _num(r.get("rmssd_ms"), 1, 300)
        return {"id": f"hrv|{iso}", "date": iso, "rmssd_ms": round(v, 1)} if v is not None else None
    if kind == "steps":
        v = _num(r.get("count"), 0, 200_000)
        return {"id": f"steps|{iso}", "date": iso, "count": int(v)} if v is not None else None
    if kind == "calories":
        v = _num(r.get("kcal"), 0, 20_000)
        return {"id": f"kcal|{iso}", "date": iso, "kcal": round(v)} if v is not None else None
    if kind == "hr_daily":
        lo, avg, hi = (_num(r.get(k), 25, 240) for k in ("min", "avg", "max"))
        if None in (lo, avg, hi) or not lo <= avg <= hi:
            return None
        return {"id": f"hr|{iso}", "date": iso, "min": round(lo), "avg": round(avg), "max": round(hi)}
    return None


def _clean_exercise(r: dict) -> dict | None:
    start, end = _when(r.get("start")), _when(r.get("end"))
    if not start or not end or end <= start:
        return None
    seconds = (end - start).total_seconds()
    if not 0 < seconds <= 24 * 3600:
        return None
    kind = _text(r.get("type"), 40)
    kind = kind.lower().replace(" ", "_") if kind else "other"
    out = {"id": "ex|" + _key(start), "date": start.date().isoformat(), "start": start.isoformat(), "end": end.isoformat(),
           "seconds": int(seconds), "type": kind, "title": _text(r.get("title"), 80)}
    dist = _num(r.get("distance_m"), 0, 200_000)
    if dist is not None:
        out["miles"] = round(dist / 1609.344, 2)
    for k in ("avg_hr", "max_hr"):
        v = _num(r.get(k), 25, 240)
        if v is not None:
            out[k] = round(v)
    if out.get("avg_hr") and out.get("max_hr") and out["avg_hr"] > out["max_hr"]:
        out.pop("avg_hr"), out.pop("max_hr")
    kcal = _num(r.get("kcal"), 0, 10_000)
    if kcal is not None:
        out["kcal"] = round(kcal)
    speed = _num(r.get("avg_speed_mps"), 0, 12)
    if speed is not None:
        out["speed_mps"] = round(speed, 2)
    return out


def _clean_point(kind: str, r: dict) -> dict | None:
    t = _when(r.get("time"))
    if not t:
        return None
    if kind == "weight":
        kg = _num(r.get("kg"), 0, 1000)
        lb = kg * KG_TO_LB if kg is not None else None
        if lb is None or not 60 <= lb <= 500:
            return None
        return {"id": "weight|" + _key(t), "date": t.date().isoformat(), "time": t.isoformat(), "lb": round(lb, 1)}
    v = _num(r.get("value"), 10, 90)
    return {"id": "vo2|" + _key(t), "date": t.date().isoformat(), "time": t.isoformat(), "value": round(v, 1)} if v is not None else None


def clean_payload(body) -> tuple[dict, int]:
    """The phone's post -> ({kind: [clean records]}, how many were dropped). A post that is not an object (or a version
    this server does not know) is refused as a whole; a bad record is dropped on its own."""
    if not isinstance(body, dict):
        raise HealthError("A health report must be a JSON object.")
    if body.get("v", 1) != 1:
        raise HealthError("This Jarvis does not know that health report version; update Jarvis Link.")
    out, dropped = {}, 0
    for kind in KINDS:
        rows = body.get(kind)
        if rows is None:
            continue
        if not isinstance(rows, list):
            dropped += 1
            continue
        dropped += max(0, len(rows) - MAX_ITEMS)
        clean, seen = [], {}
        for r in rows[:MAX_ITEMS]:
            if not isinstance(r, dict):
                dropped += 1
                continue
            if kind == "sleep":
                c = _clean_sleep(r)
            elif kind == "exercise":
                c = _clean_exercise(r)
            elif kind in ("weight", "vo2max"):
                c = _clean_point(kind, r)
            else:
                c = _clean_day_value(kind, r)
            if c is None:
                dropped += 1
                continue
            src = _text(r.get("src"), 100)
            if src:
                c["src"] = src
            if c["id"] in seen:                              # the same record twice in one post: the later one wins
                dropped += 1
                clean[seen[c["id"]]] = c
                continue
            seen[c["id"]] = len(clean)
            clean.append(c)
        out[kind] = clean
    return out, dropped


# ---- files ------------------------------------------------------------------------------------------------------------
def _name(m: str) -> str:
    return f"fitness/health-{m}.json"


def _ask_name(m: str) -> str:
    return f"fitness/health-ask-{m}.json"


def _load(name: str) -> dict:
    data = storage.load(name, {})
    return data if isinstance(data, dict) else {}


def _bounded(records: dict) -> dict:
    cutoff = (_today() - timedelta(days=KEEP_DAYS)).isoformat()
    out = {}
    for kind in KINDS:
        rows = [r for r in (records.get(kind) or {}).values() if isinstance(r, dict) and str(r.get("date", "")) >= cutoff]
        rows.sort(key=lambda r: (str(r.get("date")), str(r.get("start") or r.get("time") or "")))
        out[kind] = {r["id"]: r for r in rows[-CAP[kind]:]}
    return out


def ingest(body) -> dict:
    """One POST from the phone: validate, merge into THIS machine's file (a record with the same id is replaced), bound
    it, save atomically. "final": true marks the end of a sync (several chunked posts make one sync)."""
    clean, dropped = clean_payload(body)
    now = clock()
    f = _load(_name(machine()))
    records = f.get("records") if isinstance(f.get("records"), dict) else {}
    stored = {}
    for kind, rows in clean.items():
        box = records.get(kind) if isinstance(records.get(kind), dict) else {}
        fresh = set()
        for r in rows:
            if kind == "sleep":                              # Garmin revised a night: the new record replaces any it overlaps
                for old_id in [i for i, o in box.items() if i not in fresh and _overlaps(o, r)]:
                    box.pop(old_id)
            box[r["id"]] = dict(r, at=now)
            fresh.add(r["id"])
        records[kind] = box
        stored[kind] = len(rows)
    f["records"] = _bounded(records)
    f["v"], f["last_post"] = 1, now
    sources = body.get("sources")
    if isinstance(sources, list):
        f["sources"] = [s for s in (_text(x, 100) for x in sources[:10]) if s]
    if isinstance(body.get("garmin_found"), bool):
        f["garmin_found"] = body["garmin_found"]
    app = _text(body.get("app"), 20)
    if app:
        f["app"] = app
    if body.get("final") is True:
        f["last_sync"] = now
        counts = dict(f.get("pending_counts") or {})
        for kind, n in stored.items():
            counts[kind] = counts.get(kind, 0) + n
        f["counts"], f["pending_counts"] = counts, {}
    else:
        pending = dict(f.get("pending_counts") or {})
        for kind, n in stored.items():
            pending[kind] = pending.get(kind, 0) + n
        f["pending_counts"] = pending
    storage.save(_name(machine()), f)
    return {"ok": True, "stored": stored, "dropped": dropped}


def _files() -> list[dict]:
    return [f for f in (_load(_name(m)) for m in _machines()) if f]


def records() -> dict:
    """Every machine's records merged: {kind: {id: rec}}, the newer "at" winning."""
    out = {k: {} for k in KINDS}
    for f in _files():
        for kind in KINDS:
            for rid, r in ((f.get("records") or {}).get(kind) or {}).items():
                if isinstance(r, dict) and (rid not in out[kind] or float(r.get("at") or 0) > float(out[kind][rid].get("at") or 0)):
                    out[kind][rid] = r
    return out


def last_sync() -> float | None:
    times = [float(f.get("last_sync") or 0) for f in _files()]
    return max(times) if times and max(times) > 0 else None


def connected() -> bool:
    """Has the phone ever completed a Health Connect sync to Jarvis?"""
    return last_sync() is not None


# ---- sync requests (the /api/link/cmd reply carries "health": true) ---------------------------------------------------
def request_sync(why: str = "") -> None:
    storage.save(_ask_name(machine()), {"at": clock(), "why": str(why)[:40]})


def sync_wanted() -> bool:
    """Ask the phone for a sync: someone asked (a workout opened, the page's button) since the last sync and within
    ASK_TTL (10 minutes), or it is 05:00-10:00 here and the last sync is older than 3 hours (or there never was one).
    Never within POST_QUIET (10 minutes) of the phone's last post, finished or not: a phone that started a sync and
    never sent its final chunk is not asked again and again."""
    now = clock()
    last = last_sync() or 0
    if any(now - float(f.get("last_post") or 0) < POST_QUIET for f in _files()):
        return False
    asked = max((float(_load(_ask_name(m)).get("at") or 0) for m in _machines()), default=0)
    if asked > last and now - asked < ASK_TTL:
        return True
    hour = datetime.fromtimestamp(now).hour
    return 5 <= hour < 10 and now - last > SYNC_EVERY


# ---- the old data["garmin"] shape -------------------------------------------------------------------------------------
def _by_date(rows) -> dict:
    out = {}
    for r in sorted(rows, key=lambda r: float(r.get("at") or 0)):
        out[r["date"]] = r
    return out


def garmin_view() -> dict:
    """Health Connect data in the SAME shape tools/fitness.garmin_sync wrote to data["garmin"], so every reader works:
    {runs, sleep, hrv, daily, vo2max, synced, source: "health_connect"}. Garmin's own scores are None, never guessed."""
    rec = records()
    runs = []
    for r in sorted(rec["exercise"].values(), key=lambda r: r["start"]):
        miles = r.get("miles") or 0
        is_run = "run" in r["type"]
        runs.append({"id": r["id"], "date": r["date"], "name": r.get("title") or r["type"].replace("_", " ").capitalize(),
                     "type": r["type"], "miles": round(miles, 2), "seconds": r["seconds"],
                     "pace_s_per_mi": int(r["seconds"] / miles) if is_run and miles > 0.1 else None,
                     "avg_hr": r.get("avg_hr"), "max_hr": r.get("max_hr"), "source": "health_connect"})
    sessions = list(rec["sleep"].values())
    now = datetime.fromtimestamp(clock(), timezone.utc)
    nights, naps = {}, []
    for d in _night_dates(sessions):
        n = night_sleep(d, sessions, now)
        if n and n["usable"]:                                # never a night still in progress, a nap or a failed recording
            nights[n["date"]] = {"date": n["date"], "hours": n["hours"], "score": None,
                                 **{k: n[k] for k in ("deep_h", "rem_h", "light_h", "awake_h")}, "source": "health_connect"}
        for nap in (n or {}).get("naps") or []:
            naps.append(nap)
    hrv_rows = sorted(_by_date(rec["hrv"].values()).values(), key=lambda r: r["date"])
    hrv = []
    for r in hrv_rows:
        d = date.fromisoformat(r["date"])
        week = [x["rmssd_ms"] for x in hrv_rows if d - timedelta(days=6) <= date.fromisoformat(x["date"]) <= d]
        hrv.append({"date": r["date"], "last_night": r["rmssd_ms"], "weekly": round(sum(week) / len(week), 1), "status": None,
                    "source": "health_connect"})
    rhr, steps = _by_date(rec["resting_hr"].values()), _by_date(rec["steps"].values())
    daily = [{"date": d, "resting_hr": (rhr.get(d) or {}).get("bpm"), "body_battery": None,
              "steps": (steps.get(d) or {}).get("count"), "readiness": None, "source": "health_connect"}
             for d in sorted(set(rhr) | set(steps))]
    vo2 = sorted(rec["vo2max"].values(), key=lambda r: r["time"])
    synced = last_sync()
    return {"runs": runs[-400:], "sleep": sorted(nights.values(), key=lambda r: r["date"])[-400:], "hrv": hrv[-400:],
            "daily": daily[-400:], "vo2max": vo2[-1]["value"] if vo2 else None, "naps": naps[-60:],
            "synced": datetime.fromtimestamp(synced).isoformat(timespec="minutes") if synced else None,
            "source": "health_connect"}


# ---- one night's sleep --------------------------------------------------------------------------------------------------
def _span(r: dict):
    """(start, end) aware datetimes of a stored sleep record, or None."""
    try:
        a, b = datetime.fromisoformat(str(r["start"])), datetime.fromisoformat(str(r["end"]))
    except (KeyError, ValueError, TypeError):
        return None
    return (a, b) if a.tzinfo and b.tzinfo and b > a else None


def _overlaps(a: dict, b: dict) -> bool:
    x, y = _span(a), _span(b)
    return bool(x and y and x[0] < y[1] and y[0] < x[1])


def _wall(t: datetime) -> datetime:
    """The phone's local clock time (the record's own offset), without the zone."""
    return t.replace(tzinfo=None)


def _night_dates(sessions) -> list[date]:
    out = set()
    for r in sessions:
        sp = _span(r)
        if sp:
            out.add(_wall(sp[1]).date())
            if _wall(sp[0]).hour >= 18:
                out.add(_wall(sp[0]).date() + timedelta(days=1))
    return sorted(out)


def _is_nap(a: datetime, b: datetime) -> bool:
    wa, wb = _wall(a), _wall(b)
    return (wa.date() == wb.date() and wa.time() >= datetime.min.replace(hour=10).time()
            and wb.time() <= datetime.min.replace(hour=19).time() and (b - a).total_seconds() < NAP_MAX_H * 3600)


def night_sleep(day: date, sessions, now: datetime) -> dict | None:
    """The night that ends on `day`, from his stored sleep records (any order, revisions and duplicates included).

    The rules, in the phone's local clock time:
      * Night sleep is the UNION of the sessions overlapping [day-1 18:00, day 12:00]: overlapping or abutting
        records (Garmin revising a night) merge, a split night adds both parts, and a gap is never added.
      * A nap (inside 10:00-19:00 on one day, shorter than 3 h) is never night sleep; it is listed in "naps".
      * Night intervals are the sessions starting before 06:00 on `day`. A later session belongs to the night only when
        it starts within 90 minutes of the night's end (waking briefly and sleeping on).
      * Shift work: with NO night interval, a daytime session of 3 h or more (e.g. 08:00-15:00 after a night shift) is
        his main sleep for that date, with any session within 90 minutes after it.
      * "usable" (good enough to be today's automatic check-in, or to be shown as last night): it ends at or after
        04:00 on `day`, ended at least 45 minutes before `now` (not still asleep), and totals at least 3 hours.
    Hours are real elapsed time, so a daylight-saving night is 9 or 7 hours. Stages come from the newest record of
    each overlapping group. -> {date, hours, start, end, deep_h, rem_h, light_h, awake_h, usable, naps, kind} | None."""
    win_a = datetime.combine(day - timedelta(days=1), datetime.min.time()).replace(hour=18)
    win_b = datetime.combine(day, datetime.min.time()).replace(hour=12)
    six = datetime.combine(day, datetime.min.time()).replace(hour=6)
    spans, naps = [], []
    for r in sessions:
        sp = _span(r)
        if not sp:
            continue
        a, b = sp
        if _is_nap(a, b):
            if _wall(b).date() == day:
                naps.append({"date": day.isoformat(), "start": r["start"], "end": r["end"],
                             "hours": round((b - a).total_seconds() / 3600, 2)})
            continue
        if _wall(a) < win_b and _wall(b) > win_a:
            spans.append((a, b, r))
    spans.sort(key=lambda x: x[0])
    night = [x for x in spans if _wall(x[0]) < six]
    later = [x for x in spans if _wall(x[0]) >= six]
    if night:
        chosen, kind = list(night), "night"
    else:
        main = max((x for x in later if (x[1] - x[0]).total_seconds() >= MIN_NIGHT_H * 3600),
                   key=lambda x: x[1] - x[0], default=None)
        if not main:
            return {"date": day.isoformat(), "hours": 0, "usable": False, "naps": naps, "kind": None} if naps else None
        chosen, kind = [main], "day"
        later = [x for x in later if x is not main and x[0] >= main[0]]
    end = max(x[1] for x in chosen)
    for x in later:
        if x in chosen:
            continue
        if 0 <= (x[0] - end).total_seconds() < JOIN_GAP_S or x[0] < end:
            chosen.append(x)
            end = max(end, x[1])
    chosen.sort(key=lambda x: x[0])
    merged = []
    for a, b, _ in chosen:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    hours = round(sum((b - a).total_seconds() for a, b in merged) / 3600, 2)
    stages, kept = {"deep_h": None, "rem_h": None, "light_h": None, "awake_h": None}, []
    for a, b, r in sorted(chosen, key=lambda x: float(x[2].get("at") or 0), reverse=True):
        if not any(a < kb and ka < b for ka, kb in kept):    # the newest record of an overlapping group
            kept.append((a, b))
            for k in stages:
                if r.get(k) is not None:
                    stages[k] = round((stages[k] or 0) + r[k], 2)
    last_end = merged[-1][1]
    usable = (_wall(last_end) >= six - timedelta(hours=2) and (now - last_end).total_seconds() >= SETTLE_S
              and hours >= MIN_NIGHT_H)
    return {"date": day.isoformat(), "hours": hours, "start": merged[0][0].isoformat(), "end": last_end.isoformat(),
            **stages, "usable": usable, "naps": naps, "kind": kind}


def merged_garmin(old: dict | None) -> dict:
    """data["garmin"] from the old direct sync merged with Health Connect. Health Connect supplies what it measures
    (sleep hours and stages, resting HR, steps, runs, VO2 max); the old sync keeps supplying Garmin's own signals (sleep
    score, HRV status, training readiness, body battery) for the dates it has them: per date, a Health Connect value
    that is not None overlays the old row. With no Health Connect data the old dict comes back unchanged."""
    old = old if isinstance(old, dict) else {}
    if not connected():
        return old
    hc = garmin_view()
    out = dict(old)
    for key in ("sleep", "hrv", "daily"):
        rows = {r.get("date"): dict(r) for r in old.get(key) or [] if isinstance(r, dict)}
        for r in hc[key]:
            rows[r["date"]] = {**rows.get(r["date"], {}), **{k: v for k, v in r.items() if v is not None}}
        out[key] = sorted(rows.values(), key=lambda r: str(r.get("date")))[-400:]
    ident = lambda r: (r.get("date"), round(float(r.get("seconds") or 0) / 60))    # noqa: E731
    runs = {ident(r): r for r in old.get("runs") or [] if isinstance(r, dict)}
    runs.update({ident(r): r for r in hc["runs"]})
    out["runs"] = sorted(runs.values(), key=lambda r: str(r.get("date")))[-400:]
    if hc["vo2max"] is not None:
        out["vo2max"] = hc["vo2max"]
    out["naps"] = hc["naps"]
    out["synced"], out["source"] = hc["synced"], "health_connect"
    return out


# ---- the effective morning check-in ------------------------------------------------------------------------------------
def _usual_rhr(daily: list, day: date) -> float | None:
    """His usual resting heart rate: the median of the 14 days before `day` (at least RHR_MIN_DAYS of them on file)."""
    vals = [r["resting_hr"] for r in daily if r.get("resting_hr")
            and day - timedelta(days=14) <= date.fromisoformat(str(r["date"])) < day]
    return statistics.median(vals) if len(vals) >= RHR_MIN_DAYS else None


def effective_checkin(data: dict, day: date | None = None) -> dict | None:
    """The check-in the coach uses for `day`: his own check-in when he gave one (it always wins); otherwise, when Health
    Connect has a usable night for `day` (night_sleep: ended, settled, 3 h or more), the measured sleep hours, and his
    resting heart rate when it is RHR_ABOVE or more above the median of the 14 days before (at least RHR_MIN_DAYS of
    them on file), marked source "garmin". The old direct sync never makes one. Nothing is written to his check-ins."""
    day = day or _today()
    iso = day.isoformat()
    manual = (data.get("checkins") or {}).get(iso)
    if isinstance(manual, dict) and any(manual.get(k) not in (None, "", 0) for k in ("sleep_hours", "energy", "soreness", "readiness")):
        return manual
    if not connected():                                      # only Health Connect makes an automatic check-in, never the old sync
        return manual if isinstance(manual, dict) else None
    rec = records()                                          # re-evaluated on every call: a later sync extends the night
    night = night_sleep(day, list(rec["sleep"].values()), datetime.fromtimestamp(clock(), timezone.utc))
    if not night or not night["usable"]:
        return manual if isinstance(manual, dict) else None
    out = {"date": iso, "sleep_hours": round(float(night["hours"]), 1), "source": "garmin"}
    rhr = _by_date(rec["resting_hr"].values())
    daily = [{"date": d, "resting_hr": r["bpm"]} for d, r in rhr.items()]
    today_rhr = (rhr.get(iso) or {}).get("bpm")
    usual = _usual_rhr(daily, day)
    if today_rhr and usual is not None:
        out["resting_hr"] = today_rhr
        if today_rhr - usual >= RHR_ABOVE:
            out["rhr_above"] = round(today_rhr - usual)
    return out


def effective_checkins(data: dict, today: date | None = None) -> dict:
    """data["checkins"] with today's entry replaced by effective_checkin when that came from his watch (a copy)."""
    today = today or _today()
    checks = dict(data.get("checkins") or {})
    try:
        eff = effective_checkin(data, today)
    except Exception as error:                               # noqa: BLE001 - the coach still runs on his own check-ins
        from core import diag
        diag.swallowed("health effective checkin", error)
        return checks
    if eff and eff.get("source") == "garmin":
        checks[today.isoformat()] = eff
    return checks


# ---- what the page and the voice show --------------------------------------------------------------------------------
def _mmss(sec) -> str:
    sec = int(round(float(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def _run_row(r: dict) -> dict:
    miles = r.get("miles") or 0
    pace = r["seconds"] / miles if miles > 0.1 else None
    return {"date": r["date"], "start": r["start"], "name": r.get("title") or r["type"].replace("_", " ").capitalize(),
            "type": r["type"], "minutes": round(r["seconds"] / 60, 1), "seconds": r["seconds"], "time": _mmss(r["seconds"]),
            "distance_mi": round(miles, 2) if miles else None, "pace": f"{_mmss(pace)} /mi" if pace else None,
            "pace_s": round(pace) if pace else None, "avg_hr": r.get("avg_hr"), "max_hr": r.get("max_hr")}


def watch_runs(day: date | None = None) -> list[dict]:
    """His watch's runs (running and treadmill), oldest first; only `day`'s when given."""
    rows = [r for r in records()["exercise"].values() if r.get("type") in RUN_TYPES or "run" in str(r.get("type"))]
    if day:
        rows = [r for r in rows if r["date"] == day.isoformat()]
    return [_run_row(r) for r in sorted(rows, key=lambda r: r["start"])]


def watch_run_offer(day: date) -> dict | None:
    """The page's offer to log today's planned run from the watch: {date, minutes, distance_mi, pace, avg_hr, time}."""
    runs = watch_runs(day)
    if not runs:
        return None
    r = runs[-1]
    return {k: r[k] for k in ("date", "minutes", "distance_mi", "pace", "avg_hr", "time", "start")}


def watch_trial_offer(day: date) -> dict | None:
    """A watch run today of about two miles (1.9-2.2 mi), offered for the two-mile test entry; never saved by itself."""
    for r in reversed(watch_runs(day)):
        if r.get("distance_mi") and 1.9 <= r["distance_mi"] <= 2.2:
            return {"date": r["date"], "seconds": r["seconds"], "time": r["time"], "distance_mi": r["distance_mi"],
                    "avg_hr": r.get("avg_hr")}
    return None


def page_status() -> dict:
    """The page's GARMIN card and GET /api/health/status: only what the phone sent, never a made-up value."""
    from tools import pca_link
    rec = records()
    synced = last_sync()
    files = _files()
    told = [f["garmin_found"] for f in files if isinstance(f.get("garmin_found"), bool)]
    found = any(told) if told else None
    g = garmin_view() if synced else {"sleep": [], "daily": [], "vo2max": None}
    night = g["sleep"][-1] if g["sleep"] else None
    rhr = next((r for r in reversed(g["daily"]) if r.get("resting_hr")), None)
    usual = _usual_rhr(g["daily"], date.fromisoformat(rhr["date"])) if rhr else None
    runs = watch_runs()
    counts = {}
    for f in files:
        if float(f.get("last_sync") or 0) == (synced or -1):
            counts = dict(f.get("counts") or {})
    weights = sorted(rec["weight"].values(), key=lambda r: r["time"])
    return {"connected": synced is not None, "paired": pca_link.paired(), "last_sync": synced,
            "garmin_found": found if synced else None, "counts": counts,
            "stored": {k: len(v) for k, v in rec.items()},
            "last_sleep": {k: night.get(k) for k in ("date", "hours", "deep_h", "rem_h", "light_h", "awake_h")} if night else None,
            "newest_sleep_date": night["date"] if night else None,
            "resting_hr": {"date": rhr["date"], "bpm": rhr["resting_hr"], "usual": round(usual) if usual is not None else None} if rhr else None,
            "last_run": runs[-1] if runs else None, "newest_run_date": runs[-1]["date"] if runs else None,
            "weight_lb": {"date": weights[-1]["date"], "lb": weights[-1]["lb"]} if weights else None,
            "vo2max": g.get("vo2max"),
            "steps": next(({"date": r["date"], "count": r["steps"]} for r in reversed(g["daily"]) if r.get("steps") is not None), None),
            "sync_wanted": sync_wanted() if synced is not None or pca_link.paired() else False}


def progress(runs_logged: list, planned: dict, weeks: list, today: date) -> dict:
    """Watch series for the Progress view, every point labelled source "garmin":
    runs (pace and average HR), easy_pace (watch runs on days whose planned or logged run was easy/long/recovery and
    that have no logged pace), avg_hr, resting_hr and sleep (last 30 days), and per program week the watch's run
    minutes on days with no manual run log.
    planned: {date: planned run type}; weeks: [(week number, its Monday)]."""
    since = (today - timedelta(days=30)).isoformat()
    watch = watch_runs()
    logged_dates = {str(r.get("date")) for r in runs_logged}
    logged_pace = {str(r.get("date")) for r in runs_logged if r.get("distance_mi")}
    easy_kinds = {"easy", "long", "recovery"}
    planned_kind = dict(planned or {})
    for r in runs_logged:
        if r.get("kind"):
            planned_kind.setdefault(str(r.get("date")), r["kind"])
    easy = [{"date": r["date"], "value": r["pace_s"], "source": "garmin"} for r in watch
            if r.get("pace_s") and planned_kind.get(r["date"]) in easy_kinds and r["date"] not in logged_pace]
    hr = [{"date": r["date"], "value": r["avg_hr"], "source": "garmin"} for r in watch if r.get("avg_hr")]
    g = garmin_view() if connected() else {"sleep": [], "daily": []}
    rows = []
    for n, monday in weeks or []:
        mins = sum(r["minutes"] for r in watch if monday.isoformat() <= r["date"] <= (monday + timedelta(days=6)).isoformat()
                   and r["date"] not in logged_dates and r["date"] <= today.isoformat())
        rows.append({"week": n, "run_min_watch": round(mins) if mins else None})
    return {"runs": watch[-20:], "easy_pace": easy[-20:], "avg_hr": hr[-20:], "weeks": rows,
            "resting_hr": [{"date": r["date"], "value": r["resting_hr"], "source": "garmin"} for r in g["daily"]
                           if r.get("resting_hr") and r["date"] >= since],
            "sleep": [{"date": r["date"], "value": r["hours"], "source": "garmin"} for r in g["sleep"] if r["date"] >= since]}


# ---- skills -----------------------------------------------------------------------------------------------------------
def _ago(ts: float) -> str:
    s = max(0, clock() - ts)
    if s < 120:
        return "just now"
    if s < 7200:
        return plural(round(s / 60), "minute") + " ago"
    if s < 172800:
        return plural(round(s / 3600), "hour") + " ago"
    return plural(round(s / 86400), "day") + " ago"


HOW_TO = ("To connect it: install Jarvis Link on your phone (Captain America page, Recovery, Garmin card), turn on Health "
          "Connect in the Garmin Connect app, then in Jarvis Link tap 'Allow Jarvis to read health data'. "
          "It needs Android 14 or newer.")


@tool("Is his Garmin watch connected to Jarvis, and when did it last sync ('is my Garmin connected', 'when did my watch "
      "last sync', 'is my watch syncing'). Garmin data reaches Jarvis through Android Health Connect and the Jarvis Link app.")
def health_status() -> str:
    s = page_status()
    if not s["connected"]:
        return ("Your Garmin is not connected to Jarvis yet: no health data has arrived from your phone. " + HOW_TO)
    bits = [f"Your Garmin data last reached Jarvis {_ago(s['last_sync'])}, through Health Connect on your phone."]
    if s["garmin_found"] is False:
        bits.append("But your phone found no data written by Garmin Connect: turn on Health Connect inside the Garmin Connect app.")
    if s["last_sleep"]:
        bits.append(f"Newest night on file: {s['last_sleep']['date']}.")
    if s["last_run"]:
        bits.append(f"Newest run: {s['last_run']['date']}.")
    return " ".join(bits)


@tool("How he slept, from his Garmin watch's measured sleep when it is on file ('how did I sleep', 'how much did I sleep "
      "last night', 'my sleep this week'). Answers only from what his watch recorded; says so when nothing is on file.")
def last_sleep() -> str:
    from tools import fitness
    data = fitness._data()
    g = merged_garmin(data.get("garmin"))
    nights = [n for n in g.get("sleep") or [] if n.get("hours")]
    if not nights:
        manual = sorted(((d, c) for d, c in (data.get("checkins") or {}).items() if isinstance(c, dict) and c.get("sleep_hours")))
        if manual:
            d, c = manual[-1]
            return f"No measured sleep from your watch is on file. Your last check-in ({d}) said {plural(c['sleep_hours'], 'hour')}."
        return "No sleep is on file yet: neither your watch nor a morning check-in has sent any. " + HOW_TO
    n = nights[-1]
    hours = float(n["hours"])
    total = round(hours * 60)
    text = (f"Your watch recorded {plural(total // 60, 'hour')} {plural(total % 60, 'minute')} of sleep for the night "
            f"ending {n['date']}.")
    stages = [f"{label} {plural(round(n[k], 1), 'hour')}" for k, label in (("deep_h", "deep"), ("rem_h", "REM"), ("light_h", "light"))
              if n.get(k)]
    if stages:
        text += " Stages: " + ", ".join(stages) + "."
    week = [float(x["hours"]) for x in nights if date.fromisoformat(x["date"]) > date.fromisoformat(n["date"]) - timedelta(days=7)]
    if len(week) >= 3:
        text += f" Your average over the last {plural(len(week), 'night')}: {sum(week) / len(week):.1f} hours."
    if hours < 6:
        text += " That's short: under 6 hours, the coach eases a hard day."
    return text
