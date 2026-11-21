"""The personal trainer he talks to: goal, plan, today's session, a guided workout, set logging, progress, and why.

core/coach_engine decides every number (no model is asked for a load or a set count); this module keeps the records
and turns the engine's answers into short lines he can hear. core/coachintent routes his words here before any model
chooses a skill, and never makes Jarvis deaf during a workout.

Records: the profile, plan, check-ins and finished workouts stay in fitness.json (tools/fitness.py). Everything a
workout produces is an event in fitness/events-<machine>.json under the person's data folder: one file per machine,
append-only, so the PC (voice) and the laptop (phone page) never overwrite each other through Syncthing. The running
workout is rebuilt from today's events, so it survives a restart and the phone page and the voice share one session:
"8 at 135" said out loud ticks the same set the page shows.

Everything here runs on this machine with no model and no network."""
import contextvars
import json
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from core import coach_engine as coach
from core import storage, users
from core.words import plural
from tools import fitness, registry

clock = datetime.now                 # tests pin this
OPEN_HOURS = 4                       # a workout nobody touched for this long is over
_lock = threading.RLock()
_by_voice = contextvars.ContextVar("trainer_voice", default=False)


def _rest_timer(seconds: int, line: str, uid: str) -> None:
    """Say `line` when the rest is over (voice sessions only)."""
    from core import timers
    from tools import system
    _cancel_rest(uid)
    _rest_ids[uid] = timers.add(seconds, "rest", lambda _name: system.announce(line))["id"]


def _say_after(seconds: int, line: str, uid: str) -> None:
    """Say `line` after `seconds` (a coaching cue: 'Halfway.', 'Go: rep 3 of 6.'). Voice sessions only. Every cue is
    kept by its timer id for this person, so _cancel_rest stops them exactly as it stops the rest line."""
    from core import timers
    from tools import system
    _cue_ids.setdefault(uid, []).append(timers.add(max(1, int(seconds)), "workout cue", lambda _name: system.announce(line))["id"])


def _cancel_rest(uid: str | None = None) -> None:
    """Stop this person's pending "rest is over" line and coaching cues, and only those: by the ids the timers got,
    never by their label (cancel("rest") would also stop a "restaurant" or "dough rest" timer, his or anyone's)."""
    try:
        from core import timers
        who = uid or users.current()
        for tid in [_rest_ids.pop(who, None)] + _cue_ids.pop(who, []):
            if tid:
                timers.cancel_id(tid)
    except Exception:                                        # noqa: BLE001 - a timer that cannot stop never loses the turn
        pass


def _cancel_cues(uid: str | None = None) -> None:
    """Stop only this person's coaching cues (by id), leaving a rest line alone: a logged run or test ends its timeline."""
    try:
        from core import timers
        for tid in _cue_ids.pop(uid or users.current(), []):
            timers.cancel_id(tid)
    except Exception:                                        # noqa: BLE001 - a timer that cannot stop never loses the turn
        pass


_rest_ids: dict[str, str] = {}       # person -> the id of the rest timer running for them
_cue_ids: dict[str, list] = {}       # person -> the ids of the coaching cues running for them
REST = _rest_timer                   # tests replace this
SAY = _say_after                     # tests replace this


@contextmanager
def by_voice():
    """Inside this, a logged set starts a spoken rest timer (the phone page runs its own on screen)."""
    token = _by_voice.set(True)
    try:
        yield
    finally:
        _by_voice.reset(token)


def _today() -> date:
    return clock().date()


# ---- the event log ---------------------------------------------------------------------------------------------------
def _machine() -> str:
    from core import thread
    return thread.machine()


def record(event: dict) -> dict:
    """Append one event to THIS machine's file. A file that cannot be read whole is left alone, and the returned event
    then carries "unsaved": True so a caller can say it was not stored."""
    name = f"fitness/events-{_machine()}.json"
    event = dict(event, t=clock().isoformat(timespec="seconds"), date=event.get("date") or _today().isoformat())
    with _lock:
        path = storage.path(name)
        rows = []
        if path.exists():
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return dict(event, unsaved=True)
        rows = rows if isinstance(rows, list) else []
        rows.append(event)
        storage.save(name, rows[-6000:])
    return event


def events() -> list[dict]:
    folder = storage.user_dir() / "fitness"
    rows = []
    for path in sorted(folder.glob("events-*.json")) if folder.is_dir() else []:
        if ".sync-conflict" in path.name or ".corrupt" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows += [r for r in data if isinstance(r, dict) and r.get("t") and r.get("type")] if isinstance(data, list) else []
    return sorted(rows, key=lambda r: str(r["t"]))


def _legacy_sets(data: dict) -> list[dict]:
    """Whole-workout logs from before set logging, read as sets so progression has a history."""
    out = []
    for w in data.get("workouts") or []:
        if w.get("coach"):
            continue
        for it in w.get("items") or []:
            key = coach.key_of(it.get("exercise"))
            try:
                reps, count = int(it.get("reps") or 0), max(1, min(10, int(it.get("sets") or 1)))
            except (TypeError, ValueError):
                continue
            if key and reps:
                out += [{"key": key, "exercise": it.get("exercise"), "date": w.get("date"), "weight": float(it.get("weight_lbs") or 0),
                         "reps": reps, "rir": None}] * count
    return out


# ---- today's view ----------------------------------------------------------------------------------------------------
def view(rows: list | None = None, data: dict | None = None) -> dict:
    """Today as the coach runs it: the engine's adapted day with today's swaps, skips and logged sets, and the
    running workout if there is one."""
    today = _today()
    data = data if data is not None else fitness._data()
    rows = rows if rows is not None else events()
    sets = _legacy_sets(data) + [r for r in rows if r["type"] == "set"]
    done = {str(w.get("date")) for w in data.get("workouts") or []} | {str(r.get("date")) for r in rows if r["type"] in ("run", "test")}
    deload = max((str(r.get("until")) for r in rows if r["type"] == "deload"), default="")
    if _pca(data):                                           # Project Captain America runs its own rules on top of the coach's
        from tools import pca
        day = pca.coach_day(data, rows, sets, done, deload, today)
        first = pca._first_start(data)                       # a swapped lift is prescribed from in-program sets only, too
        sets = [s for s in sets if not first or str(s.get("date")) >= first.isoformat()]
    else:
        from tools import pca_health                          # no check-in today: last night from his watch, if on file
        day = coach.adapt_day(data.get("plan") or {}, sets, pca_health.effective_checkins(data, today), today, done, deload)
    mine =[r for r in rows if r.get("date") == today.isoformat()]
    gear = coach.intake(data.get("profile") or {})["gear"]
    for swap in (r for r in mine if r["type"] == "swap"):
        for it in day["items"]:
            if (it.get("key") or coach.key_of(it.get("exercise"))) == swap.get("from") and swap.get("to") in coach.TAGS:
                it.update(key=swap["to"], exercise=coach.TAGS[swap["to"]]["name"], swapped_from=swap["from"])
                if it.get("hi") and (it.get("kind") or "lift") == "lift" and coach.TAGS[swap["to"]]["mechanic"] in ("compound", "isolation"):
                    t = coach.next_target(it, coach.exposures(sets, swap["to"]))
                    it.update(load=t["load"], last=t["last"], note=t["why"], mode=t["mode"])
                else:
                    it.update(load=None, last="", note="")
                day["adjustments"].append({"kind": "swap", "exercise": it["exercise"], "reason": swap.get("reason", "")})
    skipped = {r.get("key") for r in mine if r["type"] == "skip"}
    for it in day["items"]:
        key = it.get("key") or coach.key_of(it.get("exercise")) or str(it.get("exercise", "")).lower()
        it["key"] = key
        it["done"] = [{k: s.get(k) for k in ("weight", "reps", "rir", "failed")} for s in mine
                      if s["type"] == "set" and (s.get("key") or str(s.get("exercise", "")).lower()) == key]
        it["skipped"] = key in skipped or bool(it.get("skipped") and it.get("no_equipment"))
        it["count"] = coach._sets(it) or 1
        if it.get("run"):                                    # a run is done by a logged run, not by sets
            it["done"], it["count"] = [{"minutes": r.get("minutes"), "rpe": r.get("rpe")} for r in mine if r["type"] == "run"][:1], 1
        elif it.get("kind") == "test":                       # a test is done by its logged result
            it["done"], it["count"] = [{"value": r.get("value")} for r in mine if r["type"] == "test" and r.get("event") == it.get("test")][:1], 1
    starts = [r for r in mine if r["type"] == "start"]
    ends = [r for r in mine if r["type"] == "end"]
    last = max((str(r["t"]) for r in mine if r["type"] in ("start", "set", "swap", "skip", "rep")), default="")
    is_open = bool(starts) and (not ends or str(ends[-1]["t"]) < str(starts[-1]["t"])) and bool(last) and \
        clock() - datetime.fromisoformat(last) < timedelta(hours=OPEN_HOURS)
    day["gear"] = gear
    day["date"] = today.isoformat()
    day["session"] = {"open": is_open, "started": str(starts[-1]["t"]) if starts else "", "ended": bool(ends) and not is_open,
                      "current": _current(day["items"]) if is_open else None,
                      "extra": [s for s in mine if s["type"] == "set" and s.get("key") not in {i["key"] for i in day["items"]}]}
    if day.get("deload") and not (deload and today.isoformat() <= deload):
        record({"type": "deload", "until": (today + timedelta(days=6 - today.weekday())).isoformat(), "reason": day["deload"]})
    return day


def _pca(data: dict) -> bool:
    """Is the saved plan the Project Captain America program (core/pca_program)?"""
    return str((data.get("plan") or {}).get("program") or "").startswith("pca-")


def _pca_running(data: dict) -> str:
    """The sentence that refuses to replace an active Captain America plan ('' when none is active)."""
    if not _pca(data):
        return ""
    from tools import pca
    return pca.running_text(data)


def _current(items: list) -> int | None:
    return next((n for n, it in enumerate(items) if not it["skipped"] and len(it["done"]) < it["count"]), None)


def running() -> bool:
    """Is a workout open right now (for the Brain: should a set report be listened for)?"""
    rows = events()
    today = _today().isoformat()
    if not any(r["type"] == "start" and r.get("date") == today for r in rows):
        return False
    return bool(view(rows)["session"]["open"])


def active() -> bool:
    """Does he use the trainer at all (a plan or logged sets)? Decides whether a bare 'how am I progressing' is his."""
    return bool((fitness._data().get("plan") or {}).get("weeks")) or (storage.user_dir() / "fitness").is_dir()


# ---- words -----------------------------------------------------------------------------------------------------------
def _lb(x) -> str:
    return f"{float(x):g}"


def _line(it: dict, short: bool = False) -> str:
    """One exercise as he hears it: 'Bench press: 4 sets of 8 to 12 at 135 pounds.'"""
    if it.get("say"):                                        # a Captain America hold, run or test: tools/pca wrote it for the ear
        return it["say_short"] if short else it["say"]
    if it.get("run") or it.get("kind") == "test":           # a run or a test is said by what it is
        text = f"{it['exercise']}: {it.get('target', '')}".rstrip(": ") + "."
        if it.get("pace_lines") and not short:
            text += " Paces: " + "; ".join(it["pace_lines"]) + "."
        return text
    reps = (str(it["lo"]) if it["lo"] == it["hi"] else f"{it['lo']} to {it['hi']}") if it.get("hi") and (it.get("kind") or "lift") == "lift" else         str(it.get("reps", "")).replace("-", " to ")
    text = f"{it['exercise']}: {plural(it['count'], 'set')} of {reps}".rstrip()
    if it.get("load"):
        text += f" at {_lb(it['load'])} pounds"
    elif it.get("target") and not it.get("hi"):
        text += f", {it['target']}"
    text += "."
    if not short:
        if it.get("last"):
            text += f" Last time: {it['last']}."
        if it.get("mode") in ("start", "reset", "ease", "add_load") and it.get("note"):
            text += " " + it["note"]
    return text


def _rest_words(seconds: int) -> str:
    if seconds >= 60 and seconds % 30 == 0:
        return plural(seconds / 60, "minute") if seconds % 60 == 0 else f"{seconds // 60} and a half minutes"
    return plural(seconds, "second")


def speakable(text: str) -> str:
    """Tool output written for the screen ('- deadlift: 5x3 80% 3RM') as a line for the ear."""
    import re
    lines = []
    for line in str(text).splitlines():
        line = re.sub(r"^\s*-\s*", "", line)
        line = re.sub(r"\b(\d+)x(\d[\w-]*)", lambda m: f"{plural(int(m.group(1)), 'set')} of {m.group(2).replace('-', ' to ')}", line)
        if line.strip():
            lines.append(line.strip() if line.strip()[-1] in ".!?" else line.strip() + ".")
    return " ".join(lines)


def _reasons(day: dict) -> str:
    return " ".join(a["reason"] for a in day["adjustments"] if a.get("reason") and a["kind"] != "load")


def _no_session(day: dict) -> str:
    if day["status"] == "none":
        return "You have no training plan yet. Tell me your goal, like 'my goal is to build muscle', and I'll build one."
    if day["status"] == "ended":
        return "Your training plan has ended. Say 'build me a training plan' and I'll write the next block from what you logged."
    if day["status"] == "before":
        data = fitness._data()
        if _pca(data):
            from tools import pca
            return pca.prestart_text(data)
        return "Your plan has not started yet."
    return ("Today is a rest day. " + _reasons(day)).strip()


# ---- goal and plan ---------------------------------------------------------------------------------------------------
def _save_profile(data: dict, **fields) -> None:
    for key, value in fields.items():
        if value not in (None, "", 0, -1):
            data["profile"][key] = str(value).strip()


def _build(data: dict) -> dict:
    today = _today()
    old = data.get("plan") or {}
    if old.get("weeks"):
        data["plan_history"] = ((data.get("plan_history") or []) + [dict(old, replaced=today.isoformat())])[-3:]
    plan = coach.build_plan(data["profile"], today - timedelta(days=today.weekday()), today)
    data["plan"] = plan
    fitness._save(data)
    record({"type": "plan", "name": plan["name"], "goal": plan["goal"], "reason": plan["why"]})
    return plan


def _plan_words(plan: dict) -> str:
    day = view()
    today = f"Today: {day['title'].lower()}. Say 'start my workout' when you're ready." if day["status"] == "train" else \
        "Today is a rest day."
    return f"Your plan: {plan['name']}. {plan['why']} {' '.join(plan.get('notes') or [])} {today}".replace("  ", " ")


_ASK_GOAL = "What are you training for: strength, muscle, fat loss, general fitness, or the Army fitness test?"


def set_goal(goal: str, days_per_week: int = 0, session_minutes: int = 0, equipment: str = "", training_months: int = -1,
             test_date: str = "") -> str:
    key = coach.parse_goal(goal)
    if not key:
        return "I didn't catch the goal. " + _ASK_GOAL
    with _lock:
        data = fitness._data()
        if _pca_running(data):
            return _pca_running(data)
        _save_profile(data, goal=key, days_per_week=days_per_week, session_minutes=session_minutes, equipment=equipment,
                      test_date=test_date, training_months=training_months if training_months >= 0 else None)
        fitness._save(data)
        record({"type": "goal", "goal": key})
        has_plan = coach.adapt_day(data.get("plan") or {}, [], {}, _today())["status"] in ("train", "rest", "before")
        if has_plan and (data["plan"].get("goal") == key or not data["plan"].get("generated")) and not (days_per_week or session_minutes or equipment):
            return (f"Your goal is set: {coach.GOALS[key]}. Your current plan stays as it is; say 'build me a training plan' and "
                    "I'll write a new one around this goal.")
        plan = _build(data)
    return f"Goal set: {coach.GOALS[key]}. " + _plan_words(plan)


def build_plan(goal: str = "", days_per_week: int = 0, session_minutes: int = 0, equipment: str = "", training_months: int = -1) -> str:
    with _lock:
        data = fitness._data()
        if _pca_running(data):
            return _pca_running(data)
        key = coach.parse_goal(goal)
        _save_profile(data, goal=key, days_per_week=days_per_week, session_minutes=session_minutes, equipment=equipment,
                      training_months=training_months if training_months >= 0 else None)
        p = data["profile"]
        if not (coach.parse_goal(p.get("goal")) or p.get("test_date") or p.get("goal_total") or coach.parse_goal(p.get("physique_goal"))):
            fitness._save(data)
            return _ASK_GOAL
        plan = _build(data)
    return _plan_words(plan)


# ---- the guided workout ----------------------------------------------------------------------------------------------
def start() -> str:
    with _lock:
        day = view()
        if day["status"] != "train":
            return _no_session(day)
        if day["session"]["open"]:
            return "Your workout is already running. " + status()
        record({"type": "start", "title": day["title"]})
        try:                                                 # his phone sends the latest watch data (Health Connect)
            from tools import pca_health
            pca_health.request_sync("workout")
        except Exception as error:                           # noqa: BLE001 - never costs him the workout
            from core import diag
            diag.swallowed("health sync request", error)
        for a in day["adjustments"]:
            record({"type": "adjust", "kind": a["kind"], "exercise": a.get("exercise", ""), "reason": a["reason"]})
        day = view()
    n = day["session"]["current"]
    if n is None:
        return "Everything in today's session is already logged."
    minutes = round(coach._minutes(day["items"])) + 10
    head = f"{day['title']}: {plural(len(day['items']), 'exercise')}, about {plural(minutes, 'minute')} with the warm-up."
    return " ".join(x for x in (head, _reasons(day), "First, " + _line(day["items"][n]),
                                "Tell me each set, like '8 at 135', or just 'done'.") if x)


def status() -> str:
    day = view()
    if not day["session"]["open"]:
        return "No workout is running. Say 'start my workout' to begin." if day["status"] == "train" else _no_session(day)
    n = day["session"]["current"]
    if n is None:
        return "Every set is logged. Say 'end my workout' for the summary."
    it = day["items"][n]
    left = sum(max(0, i["count"] - len(i["done"])) for i in day["items"] if not i["skipped"])
    rest = f" Rest {_rest_words(it['rest_s'])} between sets." if it.get("rest_s") else ""
    return f"Set {len(it['done']) + 1} of {it['count']}. {_line(it)}{rest} {plural(left, 'set')} left today."


def report(reps: int = 0, weight_lbs: float = 0, rir: int = -1, failed: bool = False, feel: str = "", exercise: str = "") -> str:
    """One set of the current exercise (or of `exercise`, when the page names it)."""
    with _lock:
        day = view()
        if not day["session"]["open"]:
            return "No workout is running. Say 'start my workout', or log it with the exercise: 'log a set of bench, 8 at 135'."
        n = day["session"]["current"]
        if exercise:
            n = next((i for i, it in enumerate(day["items"]) if it["key"] == (coach.key_of(exercise) or exercise.lower())), n)
        if n is None:
            return "Every set is logged. Say 'end my workout' for the summary."
        it = day["items"][n]
        if it.get("kind") == "test":
            return (f"{it['exercise']} is a test: tell me the result, like 'my push-up test was 42' or 'plank 1:52', and I'll record it "
                    "as a real result.")
        if it.get("run"):
            return "Tell me the run when it's done: the minutes and how hard it felt, like 'ran 25 minutes at RPE 4'."
        lift = (it.get("kind") or "lift") == "lift"
        hold = it.get("kind") == "hold"
        before = it["done"][-1] if it["done"] else {}
        if lift:
            if not weight_lbs:
                weight_lbs = before.get("weight") or it.get("load") or 0
            if not weight_lbs and coach.TAGS.get(it["key"], {}).get("gear") != ("bodyweight",) and "bodyweight" not in coach.TAGS.get(it["key"], {}).get("gear", ()):
                return f"What weight was that? Say it like '{reps or it.get('lo') or 8} at 135'."
            if not reps:
                reps = (it.get("hi") if feel in ("easy", "too easy", "light", "too light") else before.get("reps") or it.get("lo")) or 1
        else:
            reps, weight_lbs = reps or (it.get("hi") if hold else 0) or 1, weight_lbs or 0
        if not 0 < int(reps) <= (600 if hold else 200) or not 0 <= float(weight_lbs) <= 1500:
            return "Those numbers don't look right. Say the set again, like '8 at 135'."
        record({"type": "set", "key": it["key"], "exercise": it["exercise"], "weight": float(weight_lbs), "reps": int(reps),
                "rir": int(rir) if rir is not None and rir >= 0 else None, "failed": bool(failed),
                "src": "voice" if _by_voice.get() else "page",
                **({"target_lo": it.get("lo"), "target_hi": it.get("hi"), "target_rir": it.get("rir")} if it.get("gate") == "rir" else {}),
                **({"seconds": int(reps)} if hold else {})})
        day = view()
    it = day["items"][n]
    said = (f"{reps} at {_lb(weight_lbs)}" if weight_lbs else plural(reps, "rep")) if lift else plural(int(reps), "second") if hold else "done"
    done, count = len(it["done"]), it["count"]
    head = f"Logged {said}, set {done} of {count}."
    nxt = day["session"]["current"]
    if nxt is None:
        return head + " " + end()
    if nxt != n or done >= count:
        tail = f"That's {it['exercise'].lower()} done. Next, {_line(day['items'][nxt])}"
        after = "Next up, " + _line(day["items"][nxt], short=True)
    else:
        tip = ""
        if lift and weight_lbs:
            step = int(it.get("step") or 0) or (10 if coach.TAGS.get(it["key"], {}).get("lower") else 5)
            if failed or it.get("lo") and reps < it["lo"]:
                tip = f" Take {step} pounds off for the next set."
            elif rir is not None and rir >= 4 and reps >= (it.get("lo") or 0):
                tip = f" That was easy: add {step} pounds for the next set."
        tail = (f"Rest {_rest_words(it['rest_s'])}." if it.get("rest_s") else "Go again when you're ready.") + tip
        after = f"Rest is over. Set {done + 1} of {count}: {it['exercise'].lower()}."
        if hold and it.get("say"):
            after = f"Rest is over. Hold {done + 1} of {count}: {it['exercise'].lower()}. Say go when you're set."
    rest = it.get("rest_s") or 0
    _cancel_rest()                                           # the line waiting from the set before is out of date now
    if _by_voice.get() and rest:
        try:
            REST(int(rest), after, users.current())
        except Exception:                                    # noqa: BLE001 - a timer that cannot start never loses the set
            pass
    return head + " " + tail


def skip() -> str:
    _cancel_rest()
    with _lock:
        day = view()
        n = day["session"]["current"] if day["session"]["open"] else None
        if n is None:
            return "No exercise is waiting."
        record({"type": "skip", "key": day["items"][n]["key"], "exercise": day["items"][n]["exercise"]})
        was = day["items"][n]["exercise"]
        day = view()
    nxt = day["session"]["current"]
    return f"Skipped {was.lower()}. " + ("Next, " + _line(day["items"][nxt]) if nxt is not None else end())


def swap(to: str = "", exercise: str = "") -> str:
    _cancel_rest()
    with _lock:
        day = view()
        if day["status"] != "train":
            return _no_session(day)
        n = day["session"]["current"] if day["session"]["open"] else None
        if exercise:
            n = next((i for i, it in enumerate(day["items"]) if it["key"] == coach.key_of(exercise)), None)
            if n is None:
                return f"{exercise.capitalize()} is not in today's session."
        if n is None:
            n = next((i for i, it in enumerate(day["items"]) if not it["skipped"] and len(it["done"]) < it["count"]), None)
        if n is None:
            return "Nothing is left to swap today."
        it = day["items"][n]
        taken = {i["key"] for i in day["items"]}
        if to:
            key = coach.key_of(to)
            if not key:
                return f"I don't have {to} in my exercise list."
            why = f"You asked for {coach.TAGS[key]['name'].lower()} instead of {it['exercise'].lower()}; the sets, reps and rest stay as planned."
        else:
            found = coach.substitute(it["key"], day["gear"], taken)
            if not found:
                return f"I have no good substitute for {it['exercise'].lower()} with your equipment. Say 'skip this one' to move on."
            key, why = found
        record({"type": "swap", "from": it["key"], "to": key, "reason": why})
        record({"type": "adjust", "kind": "swap", "exercise": coach.TAGS[key]["name"], "reason": why})
        day = view()
    return f"Swapped {it['exercise'].lower()} for {coach.TAGS[key]['name'].lower()}. {why} {_line(day['items'][n])}"


def end() -> str:
    _cancel_rest()
    with _lock:
        day = view()
        today = _today().isoformat()
        rows = events()
        mine = [r for r in rows if r["type"] == "set" and r.get("date") == today]
        other = [r for r in rows if r["type"] in ("run", "test") and r.get("date") == today]
        was_open = day["session"]["open"]
        if was_open:
            record({"type": "end"})
        if not mine and not other:
            return "Workout ended with nothing logged." if was_open else "No workout is running."
        data = fitness._data()
        already = any(w.get("coach") and w.get("date") == today for w in data.get("workouts") or [])
        names: dict[str, list] = {}
        for s in mine:
            names.setdefault(s.get("exercise") or s.get("key"), []).append(s)
        items = [{"exercise": name, "sets": len(ss), "reps": max(int(s["reps"]) for s in ss),
                  "weight_lbs": max(float(s.get("weight") or 0) for s in ss)} for name, ss in names.items()]
        items += [{"exercise": "Run", "sets": 1, "reps": 0, "minutes": r.get("minutes"), "distance_mi": r.get("distance_mi")} for r in other
                  if r["type"] == "run"]
        items += [{"exercise": f"Test: {r.get('event')}", "sets": 1, "reps": 0, "result": r.get("value")} for r in other if r["type"] == "test"]
        workout = {"date": today, "title": day.get("title") or "Training", "items": items, "rpe": 0, "coach": True}
        data["workouts"] = [w for w in data.get("workouts") or [] if not (w.get("coach") and w.get("date") == today)] + [workout]
        data["workouts"] = sorted(data["workouts"], key=lambda w: str(w.get("date")))[-1500:]
        fitness._save(data)
    volume = sum(float(s.get("weight") or 0) * int(s["reps"]) for s in mine)
    text = f"Workout {'done' if was_open or not already else 'updated'}: {plural(len(mine), 'set')} across {plural(len(names), 'exercise')}"
    if other:
        text += f", plus {plural(len(other), 'run or test', 'runs and tests')}"
    text += f", {volume:,.0f} pounds moved." if volume else "."
    older = [r for r in rows if r["type"] == "set" and str(r.get("date")) < today]
    bests = []
    for key in {s.get("key") for s in mine if s.get("key") in coach.TAGS}:
        now = max((coach.e1rm(s.get("weight"), s.get("reps"), s.get("rir")) or 0) for s in mine if s.get("key") == key)
        was = max(((coach.e1rm(s.get("weight"), s.get("reps"), s.get("rir")) or 0) for s in older if s.get("key") == key), default=0)
        if now and was and now > was:
            bests.append(f"New best on {coach.TAGS[key]['name'].lower()}: an estimated max of about {round(now)} pounds, up from {round(was)}.")
    text += " " + " ".join(sorted(bests)[:2]) if bests else ""
    left = [it["exercise"].lower() for it in day["items"] if not it["skipped"] and len(it["done"]) < it["count"]]
    if left and was_open:
        text += f" Not finished: {', '.join(left[:3])}."
    nxt = next((r for r in day.get("schedule", []) if r["date"] > today and r["session"]), None)
    if nxt:
        text += f" Next session: {nxt['session']['title'].lower()} on {coach._FULL[nxt['day']]}."
    if was_open and _pca(data):                              # Captain America: the cool-down, and a real milestone if there is one
        from tools import pca
        extra = pca.end_words(day)
        text += (" " + extra) if extra else ""
    return text


def log_set_text(exercise: str, weight_lbs: float = 0, reps: int = 0, rir: int = -1) -> str:
    key = coach.key_of(exercise)
    if not reps or int(reps) > 200 or not 0 <= float(weight_lbs or 0) <= 1500:
        return "I need the reps (and the weight) to log a set, like 'log a set of bench, 8 at 135'."
    name = coach.TAGS[key]["name"] if key else str(exercise).strip().capitalize()
    if not name:
        return "Which exercise was that set?"
    with _lock:
        record({"type": "set", "key": key or name.lower(), "exercise": name, "weight": float(weight_lbs or 0), "reps": int(reps),
                "rir": int(rir) if rir is not None and rir >= 0 else None, "failed": False, "src": "voice" if _by_voice.get() else "page"})
        today = _today().isoformat()
        count = sum(1 for r in events() if r["type"] == "set" and r.get("date") == today and r.get("exercise") == name)
    said = f"{reps} at {_lb(weight_lbs)}" if weight_lbs else plural(int(reps), "rep")
    est = coach.e1rm(weight_lbs, reps, max(rir, 0)) if key else None
    return f"Logged {name.lower()}, {said}. That's {plural(count, 'set')} of it today." + \
        (f" Estimated max about {round(est)} pounds." if est else "")


# ---- progress, and why -----------------------------------------------------------------------------------------------
def progress_data(rows: list | None = None, data: dict | None = None) -> dict:
    data = data if data is not None else fitness._data()
    rows = rows if rows is not None else events()
    sets = _legacy_sets(data) + [r for r in rows if r["type"] == "set"]
    out = coach.progress(sets, _today())
    low, high = coach._VOLUME[coach.intake(data.get("profile") or {})["level"]]
    out["range"] = [low, high]
    out["sets_total"] = len(sets)
    return out


def progress_text() -> str:
    p = progress_data()
    if not p["lifts"] and not p["sets_total"]:
        return ""
    lines = []
    for lift in p["lifts"][:4]:
        trend = f", up {lift['change']:g} since you started logging it" if lift["change"] > 0 else \
            f", down {abs(lift['change']):g}" if lift["change"] < 0 else ""
        lines.append(f"{lift['name']}: estimated max about {round(lift['now'])} pounds{trend}.")
    vol = {m: v for m, v in p["volume"].items() if v}
    if vol:
        low = p["range"][0]
        top = sorted(vol.items(), key=lambda kv: -kv[1])[:4]
        lines.append("Hard sets this week: " + ", ".join(f"{m} {v:g}" for m, v in top) + ".")
        short = [m for m in coach.BIG if p["volume"][m] < low]
        if (fitness._data().get("plan") or {}).get("goal") == "glutes":   # her plan keeps the upper body small on purpose
            short = [m for m in short if m in ("glutes", "quads", "hamstrings")]
        if short and _today().weekday() >= 4:
            lines.append(f"Under the {low} a week I aim for: {', '.join(short[:3])}.")
    return " ".join(lines)


_PCA_KINDS = ("readiness", "pain", "external", "run_volume", "run_cap", "run_repeat", "override", "dropped", "rollback")


def why(what: str = "") -> str:
    day = view()
    key = coach.key_of(what)
    notes = list(day["adjustments"])
    if key:
        for it in day["items"]:
            if it["key"] == key and it.get("note"):
                return it["note"]
        notes = [a for a in notes if coach.key_of(a.get("exercise")) == key]
        if not notes and key in ("run", "plank", "sprint", "sdc", "hand_release_pushup", "deadlift"):
            # a Captain America change (readiness, pain, unit PT, run volume or cap) names no exercise: it is still the answer
            notes = [a for a in day["adjustments"] if a.get("kind") in _PCA_KINDS and a.get("reason")]
    if notes:
        return " ".join(a["reason"] for a in notes if a.get("reason"))
    since = (_today() - timedelta(days=14)).isoformat()
    past = [r for r in events() if r["type"] in ("adjust", "deload") and str(r.get("date")) >= since and r.get("reason")
            and (not key or coach.key_of(r.get("exercise")) == key)]
    if past:
        last = past[-1]
        when = "today" if last["date"] == _today().isoformat() else "on " + date.fromisoformat(last["date"]).strftime("%A")
        return f"The last change was {when}: {last['reason']}"
    plan = fitness._data().get("plan") or {}
    if key:
        return f"I have not changed {coach.TAGS[key]['name'].lower()}: it is running as planned."
    return "I have not changed anything: today runs as planned." + (f" The plan itself: {plan['why']}" if plan.get("why") else "")


# ---- the phone page --------------------------------------------------------------------------------------------------
def _warm_cool(data: dict, day: dict) -> dict:
    """The warm-up and cool-down lists the Trainer page shows around the session (a rest day gets the mobility flow)."""
    planned = fitness._today_plan(data, _today())
    parts = {k: planned.get(k) for k in ("kind", "warmup", "cooldown")} if planned else fitness._session_parts(day)
    return {"kind": parts.get("kind") or "", "warmup": parts.get("warmup") or [], "cooldown": parts.get("cooldown") or []}


def page_data() -> dict:
    data = fitness._data()
    rows = events()
    day = view(rows, data)
    plan = data.get("plan") or {}
    who = coach.intake(data.get("profile") or {})
    week = [{"date": r["date"], "day": r["day"], "title": (r["session"] or {}).get("title", ""), "done": r["done"], "missed": r["missed"],
             "moved_from": r["moved_from"], "today": r["date"] == day["date"],
             "items": [{"exercise": i.get("exercise"), "sets": i.get("sets"), "reps": i.get("reps")} for i in (r["session"] or {}).get("items", [])]}
            for r in day.pop("schedule", [])]
    since = (_today() - timedelta(days=14)).isoformat()
    changes = [{"date": r["date"], "kind": r.get("kind") or r["type"], "reason": r["reason"]} for r in rows
               if r["type"] in ("adjust", "deload", "plan") and str(r.get("date")) >= since and r.get("reason")][-8:]
    day["gear"] = sorted(day["gear"])
    day["minutes"] = round(coach._minutes(day["items"])) + 10 if day["items"] else 0
    day.update(_warm_cool(data, day))
    return {"today": day, "week": week, "progress": progress_data(rows, data), "changes": changes[::-1],
            "goal": {"key": who["goal"] if (data.get("profile") or {}).get("goal") or plan.get("generated") else "", "days": who["days"],
                     "minutes": who["minutes"], "level": who["level"], "equipment": (data.get("profile") or {}).get("equipment", "")},
            "goals": coach.GOALS,
            "plan": {"name": plan.get("name", ""), "why": plan.get("why", ""), "notes": plan.get("notes") or [],
                     "generated": bool(plan.get("generated")), "status": day["status"]}}


def page_action(body: dict) -> str:
    """One tap on the Trainer page."""
    def num(key, kind=int, default=0):
        try:
            value = kind(body.get(key)) if body.get(key) not in (None, "") else default
            return value if value == value and abs(value) != float("inf") else default     # NaN and infinity are not numbers he said
        except (TypeError, ValueError, OverflowError):
            return default
    action = str(body.get("action") or "")
    if action == "start":
        return start()
    if action == "set":
        return report(num("reps"), num("weight_lbs", float), num("rir", int, -1), bool(body.get("failed")), exercise=str(body.get("exercise") or "")[:60])
    if action == "skip":
        return skip()
    if action == "swap":
        return swap(str(body.get("to") or "")[:60], str(body.get("exercise") or "")[:60])
    if action == "end":
        return end()
    if action == "goal":
        return set_goal(str(body.get("goal") or "")[:60], num("days_per_week"), num("session_minutes"), str(body.get("equipment") or "")[:80],
                        num("training_months", int, -1))
    if action == "plan":
        return build_plan(str(body.get("goal") or "")[:60], num("days_per_week"), num("session_minutes"), str(body.get("equipment") or "")[:80],
                          num("training_months", int, -1))
    return "I don't know that action."


# ---- the skills ------------------------------------------------------------------------------------------------------
tool = registry.tool
_PLAN_ARGS = {"days_per_week": {"type": "integer", "description": "Training days a week, 2 to 6, when he says"},
              "session_minutes": {"type": "integer", "description": "Minutes per session, when he says"},
              "equipment": {"type": "string", "description": "What he trains with: 'gym', 'home dumbbells', 'bodyweight only'"},
              "training_months": {"type": "integer", "description": "Months of steady training so far, when he says"}}


@tool("Set what he is training for and build his plan around it. Use for 'my goal is to build muscle', 'I want to get "
      "stronger', 'train me for the Army fitness test', 'my goal is fat loss, four days a week'. The plan is written by "
      "rules on this machine; say the result as it is.",
      {"goal": {"type": "string", "description": "strength, muscle, fat loss, general fitness, or the Army fitness test, in his words"},
       **_PLAN_ARGS, "test_date": {"type": "string", "description": "YYYY-MM-DD of a fitness test, when he gives one"}},
      required=["goal"], direct=True)
def set_training_goal(goal: str, days_per_week: int = 0, session_minutes: int = 0, equipment: str = "", training_months: int = -1,
                      test_date: str = "") -> str:
    return set_goal(goal, days_per_week, session_minutes, equipment, training_months, test_date)


@tool("Build his training plan, or rebuild it after a change ('build me a training plan', 'change my plan to three days a "
      "week', 'I only have dumbbells now', 'make my sessions 45 minutes'). Rules on this machine choose the split, the "
      "exercises, the sets and the progression from his goal, days, time, equipment and training age, and the answer "
      "explains the plan. Prefer this to set_training_plan.",
      {"goal": {"type": "string", "description": "Only when he names a new goal"}, **_PLAN_ARGS}, required=[], direct=True)
def build_training_plan(goal: str = "", days_per_week: int = 0, session_minutes: int = 0, equipment: str = "", training_months: int = -1) -> str:
    return build_plan(goal, days_per_week, session_minutes, equipment, training_months)


@tool("Start today's guided workout: says each exercise with sets, reps and target load, takes his set reports, runs the "
      "rest timer and ends with a summary. Use for 'start my workout', 'let's train', 'I'm at the gym'.", direct=True)
def start_workout() -> str:
    return start()


@tool("Log one set of the exercise he is on in the running workout, from what he says: '8 at 135', '135 for 8', 'done, "
      "that was easy', 'failed at 6'. Leave out what he did not say.",
      {"reps": {"type": "integer"}, "weight_lbs": {"type": "number"},
       "rir": {"type": "integer", "description": "Reps he had left in reserve, when he says"},
       "failed": {"type": "boolean", "description": "True when he missed a rep"},
       "feel": {"type": "string", "description": "'easy' or 'hard', when that is all he says"}}, required=[], direct=True)
def workout_set(reps: int = 0, weight_lbs: float = 0, rir: int = -1, failed: bool = False, feel: str = "") -> str:
    if rir < 0 and feel:
        rir = 4 if "easy" in feel or "light" in feel else 0 if feel not in ("fine", "good", "ok", "okay", "solid") else -1
    return report(reps, weight_lbs, rir, failed, feel)


@tool("Skip the current exercise of the running workout and move to the next one.", direct=True)
def workout_skip() -> str:
    return skip()


@tool("Swap an exercise in today's workout for one that trains the same movement with what he has ('swap this for "
      "something else', 'the bench is taken', 'I can't do dips', 'swap rows for pull-ups').",
      {"to": {"type": "string", "description": "The exercise he asks for, if he names one"},
       "exercise": {"type": "string", "description": "The exercise to replace, if not the current one"}}, required=[], direct=True)
def swap_exercise(to: str = "", exercise: str = "") -> str:
    return swap(to, exercise)


@tool("Where the running workout stands: the current exercise and set, the target, last time's numbers, the rest, and how "
      "many sets are left ('what's next', 'what did I do last time', 'how long is my rest').", direct=True)
def workout_status() -> str:
    return status()


@tool("End the running workout and give the summary (sets, pounds moved, new bests, the next session).", direct=True)
def end_workout() -> str:
    return end()


@tool("Log one set outside a guided workout ('log a set of bench, 8 at 135', 'I did 10 pull-ups').",
      {"exercise": {"type": "string"}, "weight_lbs": {"type": "number", "description": "0 for bodyweight"},
       "reps": {"type": "integer"}, "rir": {"type": "integer", "description": "Reps in reserve, when he says"}},
      required=["exercise", "reps"], direct=True)
def log_set(exercise: str, reps: int, weight_lbs: float = 0, rir: int = -1) -> str:
    return log_set_text(exercise, weight_lbs, reps, rir)


@tool("Why the trainer changed something: fewer sets today, a lower or higher load, a moved or dropped session, a swapped "
      "exercise, a lighter week ('why did you cut my sets', 'why did my bench go down', 'why is today lighter').",
      {"what": {"type": "string", "description": "What he asks about: an exercise, 'sets', 'today', 'plan'"}}, required=[], direct=True)
def why_training_change(what: str = "") -> str:
    return why(what)
