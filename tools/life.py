"""Everyday helpers that the PC companion and the Command Center show: timers you can see and cancel, focus mode,
countdowns, the weekly report and training achievements. The same data feeds voice, the phone and the screens."""
import uuid
from datetime import date, timedelta

from core import storage
from core.words import plural
from tools.registry import tool

COUNTDOWNS = "countdowns.json"


# -- timers --------------------------------------------------------------------------------------
@tool("List the running timers and how long each has left.", direct=True)
def list_timers() -> str:
    from core import timers
    rows = timers.active()
    if not rows:
        return "No timers are running."
    if len(rows) == 1:
        return f"One timer: {rows[0]['label']}, {_say_dur(rows[0]['left'])} left."
    return f"{plural(len(rows), 'timer')}: " + "; ".join(f"{r['label']}, {_say_dur(r['left'])} left" for r in rows) + "."


@tool(
    "Cancel a running timer by its name ('the pasta timer'), or all of them.",
    {"which": {"type": "string", "description": "The timer's label, or 'all'"}},
    direct=True,
)
def cancel_timer(which: str = "") -> str:
    from core import timers
    gone = timers.cancel(which)
    if not gone:
        return "There's no timer like that running." if timers.active() else "No timers are running."
    return f"Cancelled the {', '.join(gone)} timer{'s' if len(gone) > 1 else ''}."


def _say_dur(s: int) -> str:
    """Spoken: '2 minutes 40 seconds', '1 hour 5 minutes', '45 seconds'."""
    s = int(s)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    part = lambda n, w: f"{n} {w}{'s' if n != 1 else ''}"   # noqa: E731
    if h:
        return part(h, "hour") + (f" {part(m, 'minute')}" if m else "")
    if m:
        return part(m, "minute") + (f" {part(sec, 'second')}" if sec and m < 10 else "")
    return part(sec, "second")


def _dur(s: int) -> str:
    s = int(s)
    if s >= 3600:
        return f"{s // 3600} h {s % 3600 // 60} min"
    if s >= 60:
        return f"{s // 60} min {s % 60:02d} s" if s % 60 else f"{s // 60} min"
    return f"{s} s"


# -- focus mode ----------------------------------------------------------------------------------
@tool(
    "Focus mode (Pomodoro): blocks of focused work with short breaks; Jarvis calls each switch and the PC companion "
    "stays quiet while a block runs. 'start' (optionally focus_minutes, break_minutes, rounds), 'stop', 'skip' "
    "(end this block now) or 'status'.",
    {"action": {"type": "string", "enum": ["start", "stop", "skip", "status"]},
     "focus_minutes": {"type": "integer"}, "break_minutes": {"type": "integer"}, "rounds": {"type": "integer"}},
    required=["action"], direct=True,
)
def focus_mode(action: str, focus_minutes: int = 25, break_minutes: int = 5, rounds: int = 4) -> str:
    from core import focus
    if action == "start":
        s = focus.start(focus_minutes, break_minutes, rounds)
        return (f"Focus mode on: {plural(s['rounds'], 'block')} of {plural(s['focus_min'], 'minute')} with {s['break_min']}-minute breaks. "
                "I'll keep quiet until the break.")
    if action == "stop":
        was = focus.stop()
        if was.get("phase", "off") == "off":
            return "Focus mode wasn't on."
        done = was.get("done", 0)
        return f"Focus mode off. {done} block{'s' if done != 1 else ''} done — nicely worked." if done else "Focus mode off."
    if action == "skip":
        s = focus.skip()
        return "Skipped ahead: " + ("break time." if s["phase"] == "break" else "next focus block." if s["phase"] == "focus"
                                    else "that was the last block.")
    s = focus.status()
    if s["phase"] == "off":
        return "Focus mode is off."
    return f"{'Focus' if s['phase'] == 'focus' else 'Break'}: {_dur(s['left'])} left, block {s['done'] + (s['phase'] == 'focus')} of {s['rounds']}."


# -- countdowns ----------------------------------------------------------------------------------
def countdowns(today: date | None = None) -> list[dict]:
    """Upcoming dates for this person: their own countdowns, the AFT test date and trips. Soonest first."""
    today = today or date.today()
    rows = []
    for c in storage.load(COUNTDOWNS, []):
        rows.append({**c, "source": "custom"})
    try:
        from tools import fitness
        d = fitness._data().get("profile", {}).get("test_date", "")
        if d:
            rows.append({"id": "aft", "title": "AFT test", "date": d, "source": "fitness"})
    except Exception:
        pass
    folder = storage.user_dir() / "trips"
    if folder.exists():
        for f in sorted(folder.glob("*.json")):
            t = storage.load(f"trips/{f.name}", {})
            if t.get("start"):
                rows.append({"id": "trip-" + f.stem, "title": t.get("name") or t.get("destination") or "Trip",
                             "date": str(t["start"])[:10], "source": "trip"})
    out = []
    for r in rows:
        try:
            days = (date.fromisoformat(str(r["date"])[:10]) - today).days
        except ValueError:
            continue
        if days >= 0:
            out.append({**r, "days": days})
    return sorted(out, key=lambda r: r["days"])


@tool(
    "Add a countdown to a date ('days until Christmas', 'count down to my leave on 2026-12-18'). The AFT date and "
    "trips are counted automatically.",
    {"title": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"}},
    required=["title", "date"], direct=True,
)
def add_countdown(title: str, date: str) -> str:   # noqa: A002 - matches the tool's argument name
    from datetime import date as _d
    try:
        when = _d.fromisoformat(date.strip()[:10])
    except ValueError:
        return "Give the date as YYYY-MM-DD."
    rows = storage.load(COUNTDOWNS, [])
    rows.append({"id": uuid.uuid4().hex[:6], "title": title.strip()[:60], "date": when.isoformat()})
    storage.save(COUNTDOWNS, rows[-50:])
    days = (when - _d.today()).days
    return f"Counting down to {title.strip()}: {days} day{'s' if days != 1 else ''} to go." if days >= 0 \
        else f"Saved, though {when:%b %d} has already passed."


@tool("Remove one of your countdowns by its title.", {"title": {"type": "string"}}, required=["title"], direct=True)
def remove_countdown(title: str) -> str:
    rows = storage.load(COUNTDOWNS, [])
    keep = [r for r in rows if title.strip().lower() not in r["title"].lower()]
    if len(keep) == len(rows):
        return "I don't have a countdown called that."
    storage.save(COUNTDOWNS, keep)
    return "Removed."


@tool("List upcoming countdowns (your own, the AFT test, trips) with days remaining.", direct=True)
def list_countdowns() -> str:
    rows = countdowns()
    if not rows:
        return "Nothing to count down to yet. Say 'count down to my leave on December 18th' to add one."
    return "Coming up: " + "; ".join(f"{r['title']} {_in_days(r['days'])}" for r in rows[:6]) + "."


def _in_days(n: int) -> str:
    return "is today" if n == 0 else "is tomorrow" if n == 1 else f"in {n} days"


def days_until(what: str) -> str | None:
    """'How many days until Christmas?' answered from the countdowns, or None when it isn't one of them."""
    import re
    w = re.sub(r"\b(?:the|my|our|a|an)\b", " ", (what or "").lower()).strip(" ?.!")
    words = [x for x in re.findall(r"[a-z0-9]+", w) if len(x) > 1]
    if not words:
        return None
    for r in countdowns():
        title = r["title"].lower()
        if all(x in title for x in words) or title in w:
            when = date.fromisoformat(r["date"])
            return (f"{r['title']} is today!" if r["days"] == 0 else
                    f"{r['days']} day{'s' if r['days'] != 1 else ''} until {r['title']}, on {when:%A, %B} {when.day}.")
    return None


# -- achievements --------------------------------------------------------------------------------
BADGES = [  # id, icon, name, what it takes
    ("first", "🌱", "First sweat", "Log your first workout"),
    ("ten", "🔟", "Ten down", "Log 10 workouts"),
    ("quarter", "🥉", "Quarter century", "Log 25 workouts"),
    ("fifty", "🥈", "Half century", "Log 50 workouts"),
    ("century", "🥇", "Centurion", "Log 100 workouts"),
    ("week4", "📅", "Consistent", "4 workouts in one week"),
    ("week6", "🔥", "Iron week", "6 workouts in one week"),
    ("streak5", "⚡", "On a roll", "5 workouts in a row (a rest day between is fine)"),
    ("streak10", "🚀", "Unstoppable", "10 workouts in a row"),
    ("sets100", "🏗️", "Volume dealer", "100 working sets in one week"),
    ("plates3", "🏋️", "Three plates", "Deadlift 315 lb or more"),
    ("aft", "🎖️", "Tested", "Log an AFT result"),
    ("aftgoal", "🏆", "Goal crusher", "Hit your AFT goal total"),
]


def achievements(data: dict, today: date | None = None) -> dict:
    """Streaks and badges from the training log (fitness data dict)."""
    today = today or date.today()
    workouts = sorted(data.get("workouts", []), key=lambda w: w.get("date", ""))
    days = sorted({w["date"][:10] for w in workouts if w.get("date")})
    # streaks: consecutive workout days with at most one rest day in between
    best = cur = 0
    prev = None
    for d in days:
        dd = date.fromisoformat(d)
        cur = cur + 1 if prev and (dd - prev).days <= 2 else 1
        best = max(best, cur)
        prev = dd
    if prev is None or (today - prev).days > 2:
        cur = 0
    weeks: dict[str, dict] = {}
    for w in workouts:
        d = date.fromisoformat(w["date"][:10])
        k = (d - timedelta(days=d.weekday())).isoformat()
        wk = weeks.setdefault(k, {"n": 0, "sets": 0})
        wk["n"] += 1
        wk["sets"] += sum(int(it.get("sets") or 0) for it in w.get("items", []))
    max_week = max((v["n"] for v in weeks.values()), default=0)
    max_sets = max((v["sets"] for v in weeks.values()), default=0)
    dl = max((float(it.get("weight_lbs") or 0) for w in workouts for it in w.get("items", [])
              if "deadlift" in str(it.get("exercise", "")).lower()), default=0)
    tests = data.get("aft_tests", [])
    goal = float((data.get("profile") or {}).get("goal_total") or 0)
    best_aft = max((float(t.get("total") or 0) for t in tests), default=0)
    n = len(workouts)
    progress = {"first": min(1, n), "ten": n / 10, "quarter": n / 25, "fifty": n / 50, "century": n / 100,
                "week4": max_week / 4, "week6": max_week / 6, "streak5": best / 5, "streak10": best / 10,
                "sets100": max_sets / 100, "plates3": dl / 315, "aft": min(1, len(tests)),
                "aftgoal": (best_aft / goal) if goal else 0}
    this_monday = (today - timedelta(days=today.weekday())).isoformat()
    badges = [{"id": i, "icon": ic, "name": nm, "desc": ds, "progress": round(min(1, progress[i]), 3),
               "earned": progress[i] >= 1} for i, ic, nm, ds in BADGES]
    return {"streak": cur, "best_streak": best, "total": n, "this_week": weeks.get(this_monday, {}).get("n", 0),
            "badges": badges, "earned": sum(b["earned"] for b in badges)}


@tool("Training achievements: current and best workout streak, workouts this week, and badges earned or close.",
      direct=True)
def my_achievements() -> str:
    from tools import fitness
    a = achievements(fitness._data())
    got = [b for b in a["badges"] if b["earned"]]
    close = sorted((b for b in a["badges"] if not b["earned"]), key=lambda b: -b["progress"])[:2]
    if not a["total"]:
        return "No workouts logged yet, so no streak or badges. Log one and you'll earn First sweat straight away."
    lines = [(f"You're on a {a['streak']}-workout streak" if a["streak"] else "No streak going right now")
             + f" (best {a['best_streak']}), {a['this_week']} workout{'s' if a['this_week'] != 1 else ''} this week, "
             f"{a['total']} in total. Badges: {len(got)} of {len(a['badges'])}."]
    if got:
        lines.append("You've earned " + ", ".join(b["name"] for b in got) + ".")
    if close:
        lines.append("Closest next: " + " and ".join(
            f"{b['name']}, {b['desc'][0].lower() + b['desc'][1:]}, {round(b['progress'] * 100)}% there" for b in close) + ".")
    return " ".join(lines)


# -- weekly report -------------------------------------------------------------------------------
def weekly(today: date | None = None) -> dict:
    """This week (Monday to today) against last week: training, recovery, nutrition, money, Jarvis, homelab."""
    today = today or date.today()
    mon = today - timedelta(days=today.weekday())
    last_mon, last_sun = mon - timedelta(days=7), mon - timedelta(days=1)
    in_week = lambda d, a, b: a.isoformat() <= str(d)[:10] <= b.isoformat()   # noqa: E731
    out: dict = {"week_of": mon.isoformat(), "days": (today - mon).days + 1}
    try:
        from tools import fitness
        f = fitness._data()
        cur = [w for w in f.get("workouts", []) if in_week(w.get("date", ""), mon, today)]
        prev = [w for w in f.get("workouts", []) if in_week(w.get("date", ""), last_mon, last_sun)]
        rpes = [w["rpe"] for w in cur if w.get("rpe")]
        checks = [c for c in (f.get("checkins") or {}).values() if in_week(c.get("date", ""), mon, today)]
        out["training"] = {"workouts": len(cur), "last_week": len(prev),
                           "sets": sum(int(it.get("sets") or 0) for w in cur for it in w.get("items", [])),
                           "avg_rpe": round(sum(rpes) / len(rpes), 1) if rpes else None,
                           "sleep": round(sum(c.get("sleep_hours") or 0 for c in checks) / len(checks), 1) if checks else None}
        out["achievements"] = {k: v for k, v in achievements(f, today).items() if k != "badges"}
    except Exception:
        pass
    try:
        from tools import kitchen
        n = storage.load(kitchen.NUTRITION, {"targets": {}, "log": []})
        days = {}
        for r in n["log"]:
            if in_week(r["date"], mon, today):
                d = days.setdefault(r["date"], [0, 0])
                d[0] += r["kcal"]
                d[1] += r["protein_g"]
        target = (n.get("targets") or {}).get("protein_g")
        out["nutrition"] = {"days_logged": len(days),
                            "avg_protein": round(sum(v[1] for v in days.values()) / len(days)) if days else None,
                            "avg_kcal": round(sum(v[0] for v in days.values()) / len(days)) if days else None,
                            "protein_target": target,
                            "days_on_target": sum(1 for v in days.values() if target and v[1] >= target)}
    except Exception:
        pass
    try:
        from tools import finance
        rows = [e for e in finance._data()["expenses"] if in_week(e["t"], mon, today)]
        out["money"] = {"spent": round(sum(e["amount"] for e in rows), 2), "entries": len(rows)}
    except Exception:
        pass
    try:
        from core import journal
        out["jarvis"] = {"conversations": sum(1 for e in journal.entries() if in_week(e.get("t", ""), mon, today))}
    except Exception:
        pass
    try:
        out["homelab"] = {"problems": len(storage.load_global("homelab_watch.json", {}).get("problems", {}))}
    except Exception:
        pass
    return out


@tool("This week's report: workouts vs last week, sets, effort, sleep, protein, spending, conversations with Jarvis, "
      "homelab health and streak. Use for 'how was my week?' or a Sunday recap.", direct=True)
def weekly_report() -> str:
    w = weekly()
    bits = []
    t = w.get("training")
    if t:
        n, last = t["workouts"], t["last_week"]
        if not n:
            bits.append("No workouts yet this week" + (f" (you'd done {last} by the end of last week)." if last else ", and none last week either."))
        else:
            trend = f"up from {last}" if n > last else f"down from {last}" if n < last else "the same as"
            bits.append(f"{n} workout{'s' if n != 1 else ''}, {trend}{' last week' if n != last else ' last week'}, "
                        f"{plural(t['sets'], 'working set')}" +(f" at an average effort of {t['avg_rpe']:g} out of 10" if t["avg_rpe"] else "") + ".")
        if t["sleep"]:
            sleep_h = float(format(t["sleep"], "g"))      # 7.0 -> 7, 6.6666 -> 6.6666 (same rounding as before)
            bits.append(f"Sleep is averaging {plural(sleep_h, 'hour')}.")
    a = w.get("achievements")
    if a and a.get("streak"):
        bits.append(f"You're on a {a['streak']}-workout streak.")
    nu = w.get("nutrition")
    if nu and nu["days_logged"]:
        bits.append(f"Protein about {nu['avg_protein']} grams a day" + (
            f", on target {nu['days_on_target']} of {plural(nu['days_logged'], 'day')}." if nu["protein_target"] else "."))
    m = w.get("money")
    if m and m["entries"]:
        bits.append(f"You've logged ${m['spent']:,.0f} of spending.")
    j, h = w.get("jarvis"), w.get("homelab")
    tail = []
    if j and j["conversations"]:
        tail.append(f"we've talked {j['conversations']} time{'s' if j['conversations'] != 1 else ''}")
    if h is not None:
        tail.append("the homelab is healthy" if not h["problems"] else f"the homelab has {h['problems']} open problem{'s' if h['problems'] != 1 else ''}")
    if tail:
        bits.append(" and ".join(tail).capitalize() + ".")
    mon = date.fromisoformat(w["week_of"])
    return f"Here's your week so far, since {mon:%A} the {mon.day}{_th(mon.day)}. " + " ".join(bits)


def _th(d: int) -> str:
    return "th" if 11 <= d % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
