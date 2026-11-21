"""Project Captain America: onboarding, starting the 12-week program, logging runs, tests, pain and outside loads,
the page data for app/captain.html, and the skills he talks to.

The program itself is core/pca_program.py (authored data); the day-by-day rules are core/pca_engine.py; the guided
workout is tools/trainer.py, which calls coach_day() here for a PCA plan and leaves every other plan untouched.

Records: his onboarding answers live in fitness.json under "pca" (tools/fitness.py), the plan in the usual "plan"
slot (the old plan moves to plan_history first). Everything that happens in a session is an EVENT through
trainer.record (types run, test, pain, pain_clear, external, override, session_rpe, pca), one file per machine, so
the PC and the laptop never overwrite each other. No model and no network: every number comes from code.

The page contract (GET /api/pca -> page_data(), POST /api/pca/action -> page_action(); app/captain.html):
  * Before the program starts, program, today, baselines and history are null; status is "onboarding" or "ready".
  * Each onboarding question carries "group" (an id of pca_engine.GROUPS: one page screen each). A checklist is
    kind "choice" with "multiple": true; "labels" maps option ids to display names.
  * Plan-browser items (program.weeks[].days[].items[]) and today's items carry "key", "test" (mdl|hrp|sdc|plk|run or null)
    and "bodyweight" (true for pca_program.BODYWEIGHT lifts and every hold: no load to find).
  * "set" {exercise, reps, weight_lbs, rir, failed}: for a hold, reps is the SECONDS held; for a carry, reps is 1 and
    weight_lbs the load carried; power and field items send reps only.
  * "test" {event, value}: mdl in lb, hrp in reps, sdc/plk/run as "m:ss" or as seconds (pca_engine.parse_value).
  * "run" logs the run AND marks today's run item done, so an open session moves on to the next item.
  * "begin" {start?}: start is optional (this week's Monday until Tuesday, else next Monday).
  * history.compliance is a 0-1 share of the due sessions done, or null before any session is due.
  * audio.prefs.service is one of none|spotify|youtube_music|metrolist|apple_music|local; playlists are playlist_<phase>
    (warmup, lift, easy, hard, cooldown), auto_open opens them in voice sessions (audio_cue).
  * audio.dj is tools/pca_music.music_state(): {enabled, service, phase, now, upnext, liked, picked_at, fallback, taste {artists, genres,
    avoid, open, cadence, tempo}, stats {plays, likes, skips}, link_form}. now/upnext are songs the search returned:
    {id, title, channel, duration_s, source, artist, query, phase, why, link}; Jarvis picks them by rules, never a model.
    The DJ is on by default for metrolist and youtube_music (prefs "dj": false turns it off).
  * "music_next" {phase?, reason? start|skip|next|phase, source? artist|surprise|speech|cadence, run_kind?, bpm?}, "music_like"
    (a second like changes nothing), "music_unlike" (the page's LIKE toggle), "music_skip" (gives the upnext the page already opened), "music_neutral", "music_taste"
    {artists?, genres?, avoid? (lists of short strings), open?, cadence?, tempo? (true/false)}.
  * Jarvis Link (tools/pca_link, the helper app on his phone): audio.dj also carries "learned" (artists learned from likes in
    Metrolist's own queue, a low-weight source) and "link": {paired, connected, last_contact (epoch s), apk, auto_dj,
    stop_at_end, playing: {title, artist, playing, liked, position_ms, duration_ms, age_s, id, mine, source "jarvis" |
    "metrolist_queue", sig} | null (only what the phone reported, fresh within 2 minutes), pending: the last play sent
    {id, what, song_id, waiting, done, ok, detail} | null, last: the last command of any kind, same shape | null}.
    With the Link reachable, "music_next" / "music_skip" answer at once ("Sending it to your phone.") and the play's
    outcome shows in link.pending; when it failed or the phone did not confirm within 13 s the page shows the PLAY tap
    link. "music_like" / "music_skip" apply to the Metrolist-queue track when one is playing (and it is not Jarvis's
    pick; the page sends "id" only for Jarvis's own song). New: "music_play" (Jarvis's current pick sent to the phone again),
    "music_pause", "music_resume" (sent to the phone, no
    wait), "music_link" {auto_dj?, stop_at_end? (true/false)}, and the read-only "music_state" -> {"dj": music_state()}
    (no "data": the page polls it). POST /api/link/paircode makes a one-use pairing code for the app (15 minutes).
  * "pain" is the open pain flags [{area, level, since, days_open}]; "checkins" the last 14 check-ins [{date, ...}];
    "historical" ({label, values}, old numbers with no date, never used to prescribe) is there before the start too.
  * "exercises" is filled before the start too (the program's exercise table): {key: {name, cues, muscles, mistakes, tall,
    purpose, equipment, alternatives, drill}}; drill is true for warm-up and cool-down drills; "bike" is the pain swap.
  * Read-only actions answer {"ok", "message", ...} WITHOUT "data" (nothing changed, so the page keeps what it has):
    "day" {week, day: Mon..Sun} -> {"day": that day in full as PLANNED (tested shares and paces resolved; no readiness
    or pain change; nothing recorded): {week, day, date, title, why, stimulus, intensity, minutes, optional, notes,
    warmup, cooldown, items (as today's items), as_planned: true} | null};
    "demo" {key} / "demo_next" {key, id?} / "demo_pin" {key, id?} / "demo_unpin" {key} -> tools/pca_demo: {"key", "video":
    {id, title, channel, duration_s, pinned} | null, "alternatives"}. A search runs only on these taps, never for page_data."""
import math
import re
from datetime import date, timedelta

from core import coach_engine as coach
from core import users
from core import pca_engine as engine
from core import pca_program as program
from core.words import plural
from tools import fitness, pca_health, pca_music, registry, trainer
from tools import fitness_library as lib

DISCLAIMER = ("Written by an AI coach from published evidence; not reviewed by a certified human coach or a clinician. "
              "Stop for pain; see a medic for anything that changes how you move.")
# Several missed sessions, one free day: which one moves there first.
PRIORITY = {"run_quality": 0, "time_trial": 0, "tests": 0, "lower_strength": 1, "field_power": 2, "upper_a": 3, "long_run": 4}
RUN_KINDS = ("easy", "long", "recovery", "fartlek", "tempo", "intervals", "race_pace", "sharpen")
EXTERNAL = {"unit_run": "unit PT run", "ruck": "ruck march", "unit_strength": "unit strength session", "field": "field day"}
DAY_INDEX = {d: n for n, d in enumerate(coach.DAYS)}
NOT_SAVED = "I could not save that: the training log on this machine could not be read, so nothing was recorded."


# ---- state -----------------------------------------------------------------------------------------------------------
def _state(data: dict) -> dict:
    pca = data.get("pca") if isinstance(data.get("pca"), dict) else {}
    pca.setdefault("answers", {})
    pca.setdefault("historical", engine.HISTORICAL)
    pca.setdefault("started", "")
    pca.setdefault("audio", {})
    data["pca"] = pca
    return pca


def _answers(data: dict) -> dict:
    """His answers, with the fitness profile filling any question he has not answered here yet (height, weight, age)."""
    out = dict(_state(data)["answers"])
    profile = data.get("profile") or {}
    for q in engine.QUESTIONS:
        if q.get("profile") and q["id"] not in out and profile.get(q["profile"]) not in (None, ""):
            value, error = engine.clean_answer(q["id"], profile[q["profile"]])
            if not error:
                out[q["id"]] = value
    return out


def _store(event: dict) -> bool:
    """Record an event; False when the log could not be read and nothing was stored (trainer.record leaves it alone)."""
    return not trainer.record(event).get("unsaved")


def _is_pca(data: dict) -> bool:
    return trainer._pca(data)


def _start(data: dict) -> date | None:
    try:
        return date.fromisoformat(str((data.get("plan") or {}).get("start")))
    except ValueError:
        return None


def _first_start(data: dict) -> date | None:
    """The first day of the program as it was begun (a rollback moves the plan, not what counts as in-program)."""
    started = _state(data).get("started")
    plan_start = _start(data)
    try:
        return min(date.fromisoformat(started), plan_start) if started and plan_start else plan_start
    except ValueError:
        return plan_start


def _week_n(data: dict, day: date) -> int:
    start = _start(data)
    return (day - start).days // 7 + 1 if start and day >= start else 0


def _tests(data: dict, rows: list) -> list[dict]:
    """In-program test results only: single-event test events and full tests in aft_tests, from the program start on."""
    first = _first_start(data)
    if not first:
        return []
    out = [{"event": r.get("event"), "value": r.get("value"), "date": r["date"]} for r in rows
           if r["type"] == "test" and str(r.get("date")) >= first.isoformat()]
    seen = {(t["event"], t["date"]) for t in out}
    for t in data.get("aft_tests") or []:
        if str(t.get("date")) >= first.isoformat():
            out += [{"event": ev, "value": t.get(ev), "date": t["date"]} for ev in engine.EVENTS
                    if t.get(ev) is not None and (ev, t["date"]) not in seen]
    return out


def _open_pain(rows: list) -> list[dict]:
    """Pain flags still open: the last report per area with no clear after it."""
    flags: dict[str, dict] = {}
    for r in rows:
        area = str(r.get("area") or "").strip().lower()
        if r["type"] == "pain" and area:
            old = flags.get(area)
            flags[area] = {"area": area, "level": r.get("level"), "moves": bool(r.get("moves")), "note": r.get("note", ""),
                           "unknown": r.get("level") is None,
                           "since": old["since"] if old else r["date"]}
        elif r["type"] == "pain_clear":
            for key in [k for k in flags if area in ("all", "") or area in k or k in area]:
                flags.pop(key)
    return list(flags.values())


def _planned_runs(plan: dict) -> dict:
    return {row["week"]: row["run_min"] for row in program.summary(plan)}


def _prev_quality(plan: dict, week: int) -> dict | None:
    wk = next((w for w in plan.get("weeks") or [] if w.get("week") == week - 1), None)
    for d in (wk or {}).get("days", []):
        for it in d.get("items", []):
            if (it.get("run") or {}).get("type") in engine.HARD_RUN:
                return it
    return None


# ---- the trainer's day for a PCA plan --------------------------------------------------------------------------------
def coach_day(data: dict, rows: list, sets: list, done: set, deload: str, today: date) -> dict:
    """coach.adapt_day with the PCA priority for moved sessions, then pca_engine.adapt and resolve_day. An override for
    today runs the coach without today's check-in, so its readiness trim goes too."""
    plan = data.get("plan") or {}
    iso = today.isoformat()
    first = _first_start(data)
    if first:                                                # nothing from before the program prescribes a load or a re-entry
        sets = [s for s in sets if str(s.get("date")) >= first.isoformat()]
        done = {d for d in done if d >= first.isoformat()}
    override = any(r["type"] == "override" and r.get("date") == iso for r in rows)
    checkins = pca_health.effective_checkins(data, today)    # no check-in today: last night from his watch, if on file
    if override:
        checkins.pop(iso, None)
    day = coach.adapt_day(plan, sets, checkins, today, done, deload, PRIORITY)
    rows_sched = day.get("schedule") or []
    if day["status"] == "train" and rows_sched:
        planned = rows_sched[today.weekday()]["session"] or {}
        day.update(intensity=planned.get("intensity", ""), session_kind=planned.get("session", ""), optional=bool(planned.get("optional")),
                   stimulus=planned.get("stimulus", ""), warmup=planned.get("warmup") or [], cooldown=planned.get("cooldown") or [],
                   planned_minutes=planned.get("minutes") or 0, notes=planned.get("notes", ""))
    week = int(day.get("week") or 0)
    tests = _tests(data, rows)
    base = engine.baselines(tests, today)
    flags = dict(engine.assess(_answers(data))["flags"], week=week)
    runs = [r for r in rows if r["type"] == "run" and (not first or str(r.get("date")) >= first.isoformat())]   # in-program runs only
    last_loads = {}
    for it in day.get("items", []):
        ex = coach.exposures(sets, it.get("key") or "")
        if ex:
            last_loads[it["key"]] = max(float(s.get("weight") or 0) for s in ex[-1]) or None
    past = sorted(d for d in done if d < iso) + sorted(str(r.get("date")) for r in rows
                                                        if r["type"] == "pca" and r.get("kind") == "rollback" and str(r.get("date")) <= iso)
    past = sorted(past)                                      # an accepted rollback restarts the gap count
    ctx = {"today": iso, "week": week, "checkin": checkins.get(iso), "pain": _open_pain(rows), "override": override,
           "external": [r for r in rows if r["type"] == "external" and str(r.get("date")) >= (today - timedelta(days=7)).isoformat()],
           "runs": runs, "last_loads": last_loads, "last_session": past[-1] if past else "",
           "started": _state(data).get("started") or plan.get("start", ""),
           "run_history": {"planned": _planned_runs(plan), "runs": runs}, "prev_quality_item": _prev_quality(plan, week),
           "schedule": rows_sched}
    day = engine.adapt(day, ctx)
    day = engine.resolve_day(day, base, flags)
    day["baselines"] = base
    table = engine.paces(base["run"]["value"]) if (base.get("run") or {}).get("fresh") else None
    for it in day.get("items", []):
        if it.get("run") and table:
            it["pace_s"] = {z: list(p) for z, p in table.items()}          # seconds per mile, for the spoken splits
        if it.get("run") or it.get("kind") in ("hold", "test"):
            it["say"], it["say_short"] = spoken_item(it), spoken_item(it, short=True)
    return day


# ---- onboarding and the start ----------------------------------------------------------------------------------------
def answer(qid: str, value) -> str:
    q = engine.QUESTION.get(str(qid))
    if not q:
        return "I don't have that question. The questions are: " + ", ".join(x["id"] for x in engine.QUESTIONS) + "."
    clean, error = engine.clean_answer(q["id"], value)
    if error:
        return error
    with trainer._lock:
        data = fitness._data()
        pca = _state(data)
        pca["answers"][q["id"]] = clean
        profile = data.setdefault("profile", {})
        if q.get("profile") and profile.get(q["profile"]) in (None, ""):
            profile[q["profile"]] = str(clean)
        fitness._save(data)
        a = engine.assess(_answers(data))
    shown = ", ".join(str(v) for v in clean) if isinstance(clean, list) else "yes" if clean is True else "no" if clean is False else clean
    text = f"Saved {q['label'].lower()}: {shown}."
    if a["blocking"]:
        text += " " + a["blocking"][0]
    elif a["missing"]:
        text += f" Next: {engine.QUESTION[a['missing'][0]]['label'].lower()}."
    else:
        text += " That's everything: say 'start Captain America' or tap Begin when you're ready."
    return text


def _start_date(start: str, today: date) -> tuple:
    if start:
        try:
            day = date.fromisoformat(str(start).strip())
        except ValueError:
            return None, "Give the start date like 2026-11-23."
        monday = day - timedelta(days=day.weekday())
        if monday < today - timedelta(days=today.weekday()):
            return None, "That date is in the past. Pick this week or later."
        if monday > today + timedelta(days=120):
            return None, "That is more than four months away. Pick a start within the next four months."
        return monday, ""
    this = today - timedelta(days=today.weekday())
    return (this if today.weekday() <= 1 else this + timedelta(days=7)), ""


def begin(start: str = "") -> str:
    """Start the program: refuses with the blocking sentences when onboarding blocks, or when the built plan fails its
    own structural review. The current plan moves to plan_history exactly as trainer._build does."""
    today = trainer._today()
    with trainer._lock:
        data = fitness._data()
        now = trainer.view(data=data) if (data.get("plan") or {}).get("weeks") else {}
        if now.get("session", {}).get("open"):
            return "A workout is open right now. End it first, then start Project Captain America."
        if _is_pca(data) and now.get("status") != "ended":
            week = _week_n(data, today)
            return (f"Project Captain America is already running; week {week}." if week else
                    f"Project Captain America is already set to start on {data['plan']['start']}.")
        a = engine.assess(_answers(data))
        if a["blocking"]:
            return "I can't start Project Captain America yet. " + " ".join(a["blocking"])
        if not a["complete"]:
            return "Before I can start, I still need: " + ", ".join(engine.QUESTION[m]["label"].lower() for m in a["missing"]) + "."
        monday, error = _start_date(start, today)
        if error:
            return error
        plan = program.build(a["build_args"], monday, today)
        problems = program.verify(plan)
        if problems:
            return "The plan failed its own check, so I did not start it: " + "; ".join(problems[:3]) + "."
        old = data.get("plan") or {}
        if old.get("weeks"):
            history = (data.get("plan_history") or []) + [dict(old, replaced=today.isoformat())]
            while len(history) > 3:                          # an earlier program run goes before any other plan he had
                pca_runs = [h for h in history[:-1] if str(h.get("program") or "").startswith("pca-")]
                history.remove(pca_runs[0] if pca_runs else history[0])
            data["plan_history"] = history
        plan["provisional"] = True
        stored = _store({"type": "pca", "kind": "begin", "reason": f"Program started for {monday.isoformat()} "
                         f"({plural(plan['days_per_week'], 'day')} a week, two-mile trial in week {plan['trial_week']})."}) and \
            _store({"type": "plan", "name": plan["name"], "goal": "aft", "reason": plan["why"]})
        if not stored:                                       # the log is unreadable: nothing changes, and he hears so
            return NOT_SAVED + " Project Captain America did not start."
        data["plan"] = plan
        pca = _state(data)
        pca["started"] = monday.isoformat()
        fitness._save(data)
    when = "today" if monday == today else "this week" if monday <= today else f"on Monday {monday.isoformat()}"
    first = plan["weeks"][0]["days"][0]
    return (f"Project Captain America starts {when}: 12 weeks in three phases, {plural(plan['days_per_week'], 'day')} a week. Week 1 is calibration: "
            f"loads come from reps in reserve and the first tests set your targets. First session: {first['title'].lower()}. "
            + (" ".join(a["provisional_reasons"][:1])))


def _day_name(d: date) -> str:
    return f"{d.strftime('%A')} {d.isoformat()}"


def running_text(data: dict) -> str:
    """'' when no Captain America plan is active; else the one sentence that refuses to replace it."""
    if not _is_pca(data) or trainer.view(data=data)["status"] == "ended":
        return ""
    week = _week_n(data, trainer._today())
    where = f"week {week}" if week else f"starts {_day_name(_start(data))}"
    return f"Project Captain America is running ({where}). To replace it with another plan, say 'end Captain America' first."


def prestart(data: dict, today: date) -> dict:
    """What the days before the start look like: the first session, what to sort out, and nothing invented."""
    start = _start(data)
    first = data["plan"]["weeks"][0]["days"][0]
    subs = engine.assess(_answers(data))["flags"]["subs"]
    return {"start": start.isoformat(), "days_to_go": (start - today).days,
            "first_session": {"title": first["title"], "why": first["why"], "minutes": first["minutes"],
                              "items": [{"exercise": i["exercise"], "sets": i["sets"], "reps": i["reps"]} for i in first["items"]]},
            "checklist": list(dict.fromkeys(s["reason"] for s in subs.values())) + ["sleep 7-9 h", "eat normally"]}


def prestart_text(data: dict) -> str:
    first = data["plan"]["weeks"][0]["days"][0]
    return (f"Project Captain America starts {_day_name(_start(data))}. Until then: rest, or an easy 20 minute walk or jog if you feel "
            f"like it. First session: {first['title'].lower()}.")


def end_program() -> str:
    """End the program on his word: the plan goes to plan_history (same eviction rule as begin), an event keeps the
    reason, and every logged set, run and test stays. He is left with no plan and his answers, ready to begin again."""
    today = trainer._today()
    with trainer._lock:
        data = fitness._data()
        if not _is_pca(data):
            return "Project Captain America is not running."
        if trainer.view(data=data)["session"]["open"]:
            return "A workout is open right now. End it first, then end the program."
        week = _week_n(data, today)
        reason = f"Project Captain America ended on his word in week {week}." if week else "Project Captain America ended before it started."
        if not _store({"type": "pca", "kind": "end", "week": week, "reason": reason}):
            return NOT_SAVED + " The program is still running."
        history = (data.get("plan_history") or []) + [dict(data["plan"], replaced=today.isoformat(), ended=today.isoformat())]
        while len(history) > 3:
            pca_runs = [h for h in history[:-1] if str(h.get("program") or "").startswith("pca-")]
            history.remove(pca_runs[0] if pca_runs else history[0])
        data["plan_history"] = history
        data["plan"] = {}
        _state(data)["started"] = ""
        fitness._save(data)
    return ("Project Captain America ended. The plan is archived and every set, run and test you logged stays. "
            "Say 'start Captain America' to begin again, or ask for another plan.")


# ---- logging ---------------------------------------------------------------------------------------------------------
def _num(v, kind=float):
    """A number from the page or his words; None for anything missing, unreadable, NaN or infinite."""
    try:
        f = float(v) if v not in (None, "") and not isinstance(v, bool) else None
        return kind(f) if f is not None and math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _when(day: str, today: date) -> tuple:
    if not day:
        return today, ""
    try:
        d = date.fromisoformat(str(day).strip())
    except ValueError:
        return None, "Give the date like 2026-11-23."
    if d > today:
        return None, "That date is in the future."
    if d < today - timedelta(days=14):
        return None, "I only take entries from the last two weeks."
    return d, ""


def log_run_text(minutes=0, rpe=0, distance_mi=0, kind="", completed_reps=None, slow_reps=None, notes="", day="") -> str:
    today = trainer._today()
    when, error = _when(day, today)
    if error:
        return error
    minutes, rpe, miles = _num(minutes), _num(rpe, int), _num(distance_mi) or 0
    if not minutes or not 1 <= minutes <= 300:
        return "How many minutes did you run? Say it like 'ran 25 minutes at RPE 4'."
    if not rpe or not 1 <= rpe <= 10:
        return "How hard was it, 1 to 10 (RPE)? Say it like 'ran 25 minutes at RPE 4'."
    if not 0 <= miles <= 50:
        return "That distance doesn't look right; give it in miles."
    with trainer._lock:
        data = fitness._data()
        planned = None
        if when == today and _is_pca(data):
            planned = next((it.get("run") for it in trainer.view(data=data)["items"] if it.get("run")), None)
        kind = str(kind or "").strip().lower().replace("-", "_").replace(" ", "_")
        if kind and kind not in RUN_KINDS:
            return f"I know these run kinds: {', '.join(RUN_KINDS)}."
        kind = kind or (planned or {}).get("type") or "easy"
        event = {"type": "run", "minutes": round(minutes, 1), "rpe": rpe, "distance_mi": round(miles, 2) or None, "kind": kind,
                 "completed_reps": _num(completed_reps, int), "slow_reps": _num(slow_reps, int) or 0, "notes": str(notes or "")[:200],
                 "week": _week_n(data, when), "date": when.isoformat(),
                 "planned_min": (planned or {}).get("total_min"), "in_session": bool(trainer.view(data=data)["session"]["open"]) if when == today else False}
        if not _store(event):
            return NOT_SAVED
    if when == today:
        trainer._cancel_cues()                               # today's run is logged: its timeline must not keep talking
    review = engine.run_review(planned or {}, event) if planned else {"reason": f"Logged {plural(minutes, 'minute')}."}
    pace = f" That's {engine.mmss(60 * minutes / miles)} per mile." if miles else ""
    return f"Run logged: {plural(minutes, 'minute')} at RPE {rpe}. {review['reason']}{pace}".replace("..", ".") + _next_words()


def log_test(event: str, value, day: str = "") -> str:
    today = trainer._today()
    ev = {"deadlift": "mdl", "pushups": "hrp", "push-ups": "hrp", "push ups": "hrp", "sprint drag carry": "sdc", "plank": "plk",
          "two mile": "run", "2 mile": "run", "2mr": "run"}.get(str(event or "").strip().lower(), str(event or "").strip().lower())
    number, error = engine.parse_value(ev, value)
    if error:
        return error
    when, error = _when(day, today)
    if error:
        return error
    with trainer._lock:
        data = fitness._data()
        if not _is_pca(data):
            return "Start Project Captain America first; tests are recorded inside the program."
        first = _first_start(data)
        if not first or when < first:
            return ("Only tests run inside the program count, and the program starts on "
                    f"{(first or when).isoformat()}. Run the test then and tell me the result.")
        before = [t["value"] for t in _tests(data, trainer.events()) if t["event"] == ev and t.get("value") is not None]
        if not _store({"type": "test", "event": ev, "value": number, "week": _week_n(data, when), "date": when.isoformat(),
                       "reason": f"{engine.EVENT_NAMES[ev].capitalize()} test: {engine.value_text(ev, number)}."}):
            return NOT_SAVED
        rows = trainer.events()
        same = {r.get("event"): r.get("value") for r in rows if r["type"] == "test" and r.get("date") == when.isoformat()}
        if all(e in same for e in engine.EVENTS) and not any(t.get("date") == when.isoformat() for t in data.get("aft_tests") or []):
            data["aft_tests"] = sorted((data.get("aft_tests") or []) + [{"date": when.isoformat(), **{e: same[e] for e in engine.EVENTS},
                                                                         "points": {}, "official": False, "source": "pca"}],
                                       key=lambda t: str(t.get("date")))
            fitness._save(data)
    if when == today:
        trainer._cancel_cues()
    points = engine.aft_points(ev, number)
    pts = f" On the male 22-26 table that is at least {plural(points, 'point')}." if points else " That is under the lowest listed row of the table."
    effect = {"run": " Run paces now come from this result.", "hrp": " Push-up sets now use it.", "plk": " Plank holds now use it."}.get(ev, "")
    return f"Recorded your {engine.EVENT_NAMES[ev]}: {engine.value_text(ev, number)}, a real result.{_new_best(ev, number, before)}{pts}{effect}" + \
        _next_words()


def _new_best(ev: str, number, before: list) -> str:
    """A tested best beaten, said only when there IS an earlier in-program result to beat (never on the first test)."""
    if not before:
        return ""
    lower = ev in engine.LOWER_IS_BETTER
    best = min(before) if lower else max(before)
    if (number < best) if lower else (number > best):
        return f" That's a new best: {'faster than' if lower else 'up from'} your {engine.value_text(ev, best)}."
    return ""


CHEST_LINE = ("Chest pain is not something to train through. If it feels like pressure or tightness, spreads to your arm, jaw "
              "or back, or comes with shortness of breath, stop now and get medical help: call emergency services. I've logged it, "
              "so today's training holds back on everything that loads your chest until you clear it; please tell a medic about it.")


def _is_chest(area: str) -> bool:
    return bool(re.search(r"\bchest\b", str(area or ""), re.I))


def program_on() -> bool:
    """Is his plan the program? (For the Brain: should the program's wider words be listened for.)"""
    return _is_pca(fitness._data())


def report_pain_text(area: str, level, note: str = "", moves: bool = False) -> str:
    area = str(area or "").strip()[:60]
    level = _num(level, int)
    if not area:
        return "Where does it hurt?"
    if _is_chest(area):                                      # never the training pain flow, never a 0-10 question
        if not _store({"type": "pain", "area": "chest", "level": None, "moves": False, "note": str(note or "")[:200],
                       "reason": "Chest pain reported: today's training holds back on everything that loads the chest."}):
            return NOT_SAVED
        trainer._cancel_cues()
        return CHEST_LINE
    if level is None or not 0 <= level <= 10:
        return "How bad is it, 0 to 10?"
    if level == 0 and not moves:                             # no pain any more: that clears the flag, it never opens one
        return clear_pain_text(area)
    if not _store({"type": "pain", "area": area, "level": level, "moves": bool(moves), "note": str(note or "")[:200],
                   "reason": f"Pain flag: {area}, {level} out of 10."}):
        return NOT_SAVED
    if level >= 4 or moves:
        trainer._cancel_cues()                               # the run may become the bike: its cues are out of date
    if (level >= 4 or moves) and engine.pain_region(area) == "other":
        tail = " At this level, please see a medic before you train." if level >= 7 else " If it is still there in a week, see a medic."
        return (f"Noted: {area}, {level} out of 10. I can't tell which exercises load it, so I removed nothing: judge each one yourself and "
                f"skip anything that hurts. Nothing gets heavier until it clears.{tail} I can't tell you what it is; a provider can.")
    if level >= 4 or moves:
        tail = (" Please see a medic or physical therapist." if level >= 7 else
                " If it is still there in a week, see a medic or physical therapist.")
        return (f"Noted: {area}, {level} out of 10. Today's plan leaves out what loads it and nothing gets heavier until it clears.{tail} "
                "I can't tell you what it is; a provider can.")
    return (f"Noted: {area}, {level} out of 10. Train, but the lifts that load it hold their weight for now. Stop if it gets worse or "
            "changes how you move.")


def clear_pain_text(area: str) -> str:
    area = str(area or "").strip().lower()[:60]
    open_flags = _open_pain(trainer.events())
    match = [f for f in open_flags if area in ("", "all") or area in f["area"] or f["area"] in area]
    if not match:
        return "No pain flag is open." if not open_flags else f"No open pain flag for {area}. Open: {', '.join(f['area'] for f in open_flags)}."
    if not _store({"type": "pain_clear", "area": area or "all", "reason": f"Pain flag cleared: {', '.join(f['area'] for f in match)}."}):
        return NOT_SAVED
    return f"Cleared: {', '.join(f['area'] for f in match)}. Progression resumes from the next session."


def log_external(kind: str, minutes=0, rpe=0, miles=0, day: str = "") -> str:
    kind = str(kind or "").strip().lower().replace(" ", "_").replace("-", "_")
    kind = {"pt": "unit_run", "unit_pt": "unit_run", "run": "unit_run", "strength": "unit_strength", "field_day": "field"}.get(kind, kind)
    if kind not in EXTERNAL:
        return "What was it: a unit run, a ruck, a unit strength session or a field day?"
    minutes, rpe, miles = _num(minutes), _num(rpe, int), _num(miles) or 0
    if not minutes or not 1 <= minutes <= 720:
        return "How many minutes was it?"
    if not rpe or not 1 <= rpe <= 10:
        return "How hard was it, 1 to 10?"
    if not 0 <= miles <= 40:
        return "That distance doesn't look right; give it in miles."
    today = trainer._today()
    when, error = _when(day, today)
    if error:
        return error
    if not _store({"type": "external", "kind": kind, "minutes": minutes, "rpe": rpe, "miles": miles or None, "date": when.isoformat(),
                   "reason": f"{EXTERNAL[kind].capitalize()}: {minutes:g} min at {rpe}/10" + (f", {miles:g} mi" if miles else "") + "."}):
        return NOT_SAVED
    if when == today:
        trainer._cancel_cues()                               # outside load today can turn today's run into another one
    hint = {"unit_run": " If it was hard, it counts as this week's hard run.", "ruck": " Six miles or more counts as the week's long run.",
            "unit_strength": " A hard leg session eases tomorrow's heavy lower work.", "field": ""}[kind]
    return f"Logged your {EXTERNAL[kind]}: {plural(minutes, 'minute')} at {rpe} out of 10.{hint}"


def override_text() -> str:
    with trainer._lock:
        data = fitness._data()
        if not _is_pca(data):
            return "Project Captain America is not running, so there is nothing to override."
        before = [a for a in trainer.view(data=data)["adjustments"] if a.get("overridable") and a.get("kind") != "rollback"]
        if not _store({"type": "override", "reason": "He chose to run today as planned."}):
            return NOT_SAVED
        after = trainer.view(data=data)
    kept = [a for a in after["adjustments"] if a.get("kind") == "pain"]
    text = "Done: today runs as planned." if before else "Nothing was changed today, so it already runs as planned."
    return text + (" The pain rules still apply." if kept else "") + " Back off if anything hurts."


def session_rpe_text(rpe, minutes=0) -> str:
    rpe = _num(rpe, int)
    if not rpe or not 1 <= rpe <= 10:
        return "How hard was the whole session, 1 to 10?"
    data = fitness._data()
    minutes = _num(minutes) or (trainer.view(data=data).get("planned_minutes") if _is_pca(data) else 0) or 60
    if not _store({"type": "session_rpe", "rpe": rpe, "minutes": minutes}):
        return NOT_SAVED
    return f"Session logged at {rpe} out of 10 over about {plural(minutes, 'minute')}: a training load of {round(rpe * minutes)}."


def rollback(weeks=1) -> str:
    weeks = _num(weeks, int)
    if not weeks or not 1 <= weeks <= 4:
        return "Roll back 1 to 4 weeks."
    with trainer._lock:
        data = fitness._data()
        if not _is_pca(data):
            return "Project Captain America is not running."
        status = trainer.view(data=data)["status"]
        if status == "ended":
            return "Project Captain America has ended, so there is nothing to roll back."
        if status == "before":
            return "Project Captain America has not started yet, so there is nothing to roll back."
        today = trainer._today()
        start = _start(data) + timedelta(days=7 * weeks)
        if start > today + timedelta(days=7):
            return "The program would not have started yet; roll back fewer weeks."
        reason = f"Rolled the program back {plural(weeks, 'week')}: it now counts from {start.isoformat()}."
        if not _store({"type": "pca", "kind": "rollback", "weeks": weeks, "reason": reason}):
            return NOT_SAVED
        data["plan"]["start"] = start.isoformat()
        fitness._save(data)
    week = _week_n(data, today)
    return reason + (f" This week is week {week}." if week else f" The program restarts on {start.isoformat()}.")


def music_text(out: dict) -> str:
    """One sentence for a pick: what plays and why, or the honest failure."""
    return out.get("message") or ("Done." if out.get("ok") else "That did not work.")


def audio_text(prefs) -> str:
    if not isinstance(prefs, dict):
        return "Send the music preferences as a set of named choices."
    clean = {str(k)[:40]: (v if isinstance(v, (bool, int, float)) else str(v)[:200]) for k, v in list(prefs.items())[:20]
             if isinstance(v, (bool, int, float, str))}
    if "service" in clean:
        said = str(clean["service"]).strip().lower().replace(" ", "_").replace("-", "_")
        ids = [m[0] for m in engine.MUSIC_SERVICES]
        clean["service"] = said if said in ids else next((i for i in ids if i != "none" and i.split("_")[0] in said), "none")
    with trainer._lock:
        data = fitness._data()
        _state(data)["audio"] = clean
        fitness._save(data)
    return "Music preferences saved."


# ---- the page --------------------------------------------------------------------------------------------------------
def _questions(data: dict) -> list[dict]:
    answers = _answers(data)
    return [{k: v for k, v in dict(q, value=answers.get(q["id"])).items() if k != "profile"} for q in engine.QUESTIONS]


def _briefing(day: dict) -> str:
    if day.get("status") != "train":
        return ""
    main = [it for it in day["items"] if it.get("priority") == 1 and not it.get("skipped")][:2]
    work = " and ".join(f"{it['exercise'].lower()}" + (f" ({it.get('reps')})" if it.get("reps") and not it.get("run") else "") for it in main)
    bits = [f"Today: {day['title'].lower()}.", day.get("why") or ""]
    if work:
        bits.append(f"The main work is {work}.")
    change = next((a["reason"] for a in day.get("adjustments", []) if a.get("reason") and a.get("kind") != "load"), "")
    if change:
        bits.append(change.split(". ")[0].rstrip(".") + ".")
    return " ".join(b for b in bits if b)


BIKE = ("Bike or rower", "Pedal or row at the same effort the run asked for: tall posture, smooth rhythm, the talk test still applies.")


def _drill_keys(plan: dict | None) -> set:
    """The warm-up and cool-down drills of the plan (or of every session type before there is a plan)."""
    if plan:
        return {i.get("exercise") for w in plan.get("weeks") or [] for d in w["days"]
                for i in [x for p in d.get("warmup") or [] for x in p.get("items") or []] + list(d.get("cooldown") or [])}
    return {k for v in lib.WARMUPS.values() for _, items in v for k, _ in items} | {k for v in lib.COOLDOWNS.values() for k, _ in v}


def _exercises(plan: dict | None) -> dict:
    """Every exercise the page can show: the plan's items (before the start, the program's whole exercise table, so
    Learn works from the first visit), its warm-up and cool-down drills (drill: true) and the bike or rower a pain
    swap uses."""
    items = {it.get("key") for w in (plan or {}).get("weeks") or [] for d in w["days"] for it in d["items"]} if plan else set(program.EX)
    drills = _drill_keys(plan) - items
    out = {}
    for k in sorted(k for k in items | drills | {"bike"} if isinstance(k, str) and k):
        purpose, equipment, alts = program.EX.get(k, ("", "", []))
        if k == "bike":
            name, cues, purpose, equipment = BIKE[0], BIKE[1], "Replaces running when a pain flag says so: the same minutes and effort.", "Exercise bike or rowing machine"
        else:
            name = fitness.EXERCISES.get(k, (coach.TAGS.get(k, {}).get("name") or k.replace("_", " ").capitalize(),))[0]
            cues = fitness.EXERCISES.get(k, ("", "", ""))[2]
        info = lib.DETAILS.get(k, {})
        out[k] = {"name": name, "cues": cues, "muscles": info.get("muscles", ""),
                  "mistakes": info.get("mistakes", ""), "tall": info.get("tall", ""), "purpose": purpose, "equipment": equipment,
                  "alternatives": alts, "drill": k in drills}
    return out


def day_detail(week, day) -> dict:
    """The page's day sheet: one day of the plan in full, exactly as it would be shown on that day as PLANNED (tested
    shares and paces resolved from the tests on file), without today's readiness or pain changes, and recording nothing.
    -> {"ok", "message", "day": {week, day, date, title, why, stimulus, intensity, minutes, optional, warmup, cooldown,
    items, as_planned: true} | None}."""
    def refuse(msg):
        return {"ok": False, "message": msg, "day": None}
    data = fitness._data()
    if not _is_pca(data):
        return refuse("The plan appears once the program has begun.")
    plan = data["plan"]
    weeks = plan.get("weeks") or []
    f = _num(week) if isinstance(week, (int, float, str)) else None
    n = int(f) if f is not None and f == int(f) else None
    if n is None or not 1 <= n <= len(weeks):
        return refuse(f"Which week? Send a number from 1 to {len(weeks)}.")
    said = str(day)[:3].strip().capitalize() if isinstance(day, str) else ""
    if said not in DAY_INDEX:
        return refuse("Which day? Send Mon, Tue, Wed, Thu, Fri, Sat or Sun.")
    wk = next((w for w in weeks if w.get("week") == n), None)
    d = next((x for x in (wk or {}).get("days") or [] if x.get("day") == said), None)
    if not d:
        return refuse(f"Week {n} has nothing planned on {said}: it is a rest day.")
    today = trainer._today()
    rows = trainer.events()
    base = engine.baselines(_tests(data, rows), today)
    flags = dict(engine.assess(_answers(data))["flags"], week=n)
    resolved = engine.resolve_day(dict(d, status="train"), base, flags)
    start = _start(data)
    when = (start + timedelta(days=7 * (n - 1) + DAY_INDEX[said])).isoformat() if start else ""
    items = [{**it, "test": it.get("test"), "bodyweight": _bodyweight(it)} for it in resolved.get("items") or []]
    out = {"week": n, "day": said, "date": when, "title": d.get("title", ""), "why": d.get("why", ""), "stimulus": d.get("stimulus", ""),
           "intensity": d.get("intensity", ""), "minutes": d.get("minutes") or 0, "optional": bool(d.get("optional")),
           "notes": d.get("notes", ""), "warmup": d.get("warmup") or [], "cooldown": d.get("cooldown") or [], "items": items,
           "as_planned": True}
    return {"ok": True, "message": f"Week {n}, {said}: {out['title']}, as planned.", "day": out}


def _bodyweight(it: dict) -> bool:
    return it.get("key") in program.BODYWEIGHT or it.get("kind") == "hold"


def _checkins(data: dict) -> list[dict]:
    rows = data.get("checkins") or {}
    return [dict(rows[d], date=d) for d in sorted(rows)[-14:] if isinstance(rows[d], dict)]


def _pain_flags(rows: list, today: date) -> list[dict]:
    out = []
    for f in _open_pain(rows):
        try:
            days = (today - date.fromisoformat(str(f["since"]))).days
        except ValueError:
            days = None
        out.append({"area": f["area"], "level": f["level"], "since": f["since"], "days_open": days, "note": f.get("note", "")})
    return out


def page_data() -> dict:
    today = trainer._today()
    data = fitness._data()
    rows = trainer.events()
    pca = _state(data)
    a = engine.assess(_answers(data))
    onboarding = {"questions": _questions(data), "missing": a["missing"], "complete": a["complete"], "blocking": a["blocking"],
                  "warnings": a["warnings"]}
    out = {"onboarding": onboarding, "program": None, "today": None, "baselines": None, "history": None, "changes": [],
           "long_term": dict(program.LONG_TERM, next_emphasis=None), "exercises": {}, "audio": {"prefs": pca.get("audio") or {}},
           "disclaimer": DISCLAIMER, "pain": _pain_flags(rows, today), "checkins": _checkins(data),
           "historical": pca.get("historical") or engine.HISTORICAL}
    try:
        out["audio"]["dj"] = pca_music.music_state()
    except Exception:                                        # noqa: BLE001 - music never costs him the page
        out["audio"]["dj"] = None
    pca_music.page_touch(data)                               # the page is open: keep his phone's Jarvis Link listening
    try:
        out["health"] = pca_health.page_status()             # the GARMIN card: only what his phone sent
    except Exception as error:                               # noqa: BLE001 - never costs him the page
        from core import diag
        diag.swallowed("pca health status", error)
        out["health"] = None
    if not _is_pca(data):
        out["status"] = "ready" if a["complete"] and not a["blocking"] else "onboarding"
        out["exercises"] = _exercises(None)
        return out
    plan = data["plan"]
    day = trainer.view(rows, data)
    start = _start(data)
    week_n = _week_n(data, today)
    wk = next((w for w in plan["weeks"] if w["week"] == week_n), None)
    block = next((b for b in plan.get("blocks") or [] if week_n in b["weeks"]), None)
    done_dates = {str(w.get("date")) for w in data.get("workouts") or []} | {str(r.get("date")) for r in rows if r["type"] in ("set", "run", "test")}
    summary = program.summary(plan)
    weeks = []
    for w in plan["weeks"]:
        monday = start + timedelta(days=7 * (w["week"] - 1))
        days = []
        for d in w["days"]:
            when = (monday + timedelta(days=DAY_INDEX[d["day"]])).isoformat()
            days.append({"day": d["day"], "date": when, "title": d["title"], "session": d["session"], "intensity": d["intensity"],
                         "optional": d["optional"], "minutes": d["minutes"], "done": when in done_dates,
                         "missed": when < today.isoformat() and when not in done_dates and not d["optional"],
                         "items": [{"exercise": i["exercise"], "key": i.get("key"), "test": i.get("test"), "bodyweight": _bodyweight(i),
                                    "sets": i["sets"], "reps": i["reps"], "target": i["target"], "kind": i["kind"]} for i in d["items"]]})
        weeks.append({"week": w["week"], "focus": w["focus"], "mode": w["mode"], "block": w["block"], "days": days})
    base = day.get("baselines") or engine.baselines(_tests(data, rows), today)
    fresh_run = (base.get("run") or {}).get("fresh")
    provisional = list(a["provisional_reasons"]) if not all(base.get(e) for e in ("hrp", "plk", "run")) else []
    status = "ended" if day["status"] == "ended" else "active"
    out.update(status=status, program={
        "name": plan["name"], "start": plan["start"], "week": week_n or None, "mode": (wk or {}).get("mode"), "focus": (wk or {}).get("focus"),
        "block": {"id": block["id"], "name": block["name"], "goal": block["goal"]} if block else None,
        "provisional": bool(provisional), "provisional_reasons": provisional, "why": plan["why"], "notes": plan.get("notes") or [],
        "blocks": plan.get("blocks") or [], "weeks": weeks, "summary": summary})
    items = []
    for it in day["items"]:
        items.append({**{k: v for k, v in it.items() if k not in ("cues_long",)}, "test": it.get("test"), "bodyweight": _bodyweight(it)})
    session = day.get("session") or {}
    out["today"] = {"date": today.isoformat(), "status": day["status"], "title": day.get("title", ""), "why": day.get("why", ""),
                    "stimulus": day.get("stimulus", ""), "intensity": day.get("intensity", ""),
                    "minutes": day.get("planned_minutes") or 0, "optional": bool(day.get("optional")),
                    "warmup": day.get("warmup") or [], "cooldown": day.get("cooldown") or [], "items": items,
                    "adjustments": [{"kind": x.get("kind"), "reason": x.get("reason", ""), "overridable": bool(x.get("overridable")),
                                     **({"action": x["action"], "weeks": x.get("weeks")} if x.get("action") else {})}
                                    for x in day.get("adjustments", [])],
                    "postponed": day.get("postponed") or [],
                    "session": {k: session.get(k) for k in ("open", "current", "started", "ended")}, "briefing": _briefing(day)}
    out["today"]["garmin_run"] = out["today"]["garmin_trial"] = None
    if day["status"] == "train":                             # offered, never assumed: one tap logs it, he gives the effort
        run_it = next((it for it in day["items"] if it.get("run")), None)
        trial_it = next((it for it in day["items"] if it.get("test") == "run"), None)
        try:
            if run_it and not run_it.get("done"):
                out["today"]["garmin_run"] = pca_health.watch_run_offer(today)
            if trial_it and not trial_it.get("done"):
                out["today"]["garmin_trial"] = pca_health.watch_trial_offer(today)
        except Exception as error:                           # noqa: BLE001 - the page works without the watch
            from core import diag
            diag.swallowed("pca watch run offer", error)
    if day["status"] == "before":                            # the days before the start: the same true sentence everywhere
        out["today"].update(title=f"Starts {_day_name(start)}", prestart=prestart(data, today), briefing=prestart_text(data))
    out["baselines"] = {**{ev: ({"value": b["value"], "text": engine.value_text(ev, b["value"]), "date": b["date"], "fresh": b["fresh"],
                                 "points_at_least": engine.aft_points(ev, b["value"])} if b else None) for ev, b in base.items()},
                        "paces": {z: engine.pace_text(p) for z, p in engine.paces(base["run"]["value"]).items()} if fresh_run else None,
                        "historical": pca.get("historical") or engine.HISTORICAL}
    tests = [dict(t, text=engine.value_text(t["event"], t["value"]), points_at_least=engine.aft_points(t["event"], t["value"]), source="test")
             for t in sorted(_tests(data, rows), key=lambda t: str(t["date"]))]
    runs = [{k: r.get(k) for k in ("date", "minutes", "rpe", "distance_mi", "kind", "slow_reps", "week")} | {"source": "logged"}
            for r in rows if r["type"] == "run"]
    sets = trainer._legacy_sets(data) + [r for r in rows if r["type"] == "set"]
    first = _first_start(data) or start
    in_program = [s for s in sets if str(s.get("date")) >= first.isoformat()]
    planned_weeks = [{"week": w["week"], "run_min": s["run_min"], "lift_sets": s["lift_sets"],
                      "days": [{"date": d["date"], "optional": d["optional"]} for d in w["days"]]} for w, s in zip(weeks, summary)]
    prog = engine.progress(in_program, [r for r in rows if r["type"] == "run"], _tests(data, rows), planned_weeks,
                           [r for r in rows if r["type"] == "session_rpe"], [r for r in rows if r["type"] == "external"], start, today)
    out["history"] = {"tests": tests, "runs": runs, "lifts": prog["lifts"], "easy_pace": prog["easy_pace"], "weeks": prog["weeks"],
                      "compliance": prog["compliance"], "watch": None}
    try:                                                     # his watch's series, every point labelled source "garmin"
        if pca_health.connected():
            planned_runs = {}
            for w in plan["weeks"]:
                monday = start + timedelta(days=7 * (w["week"] - 1))
                for d in w["days"]:
                    kind = next(((i.get("run") or {}).get("type") for i in d.get("items") or [] if i.get("run")), None)
                    if kind:
                        planned_runs[(monday + timedelta(days=DAY_INDEX[d["day"]])).isoformat()] = kind
            watch = pca_health.progress([r for r in rows if r["type"] == "run"], planned_runs,
                                        [(w["week"], start + timedelta(days=7 * (w["week"] - 1))) for w in plan["weeks"]], today)
            out["history"]["watch"] = watch
            by_week = {w["week"]: w["run_min_watch"] for w in watch["weeks"]}
            for w in out["history"]["weeks"]:
                w["run_min_watch"] = by_week.get(w.get("week"))
    except Exception as error:                               # noqa: BLE001 - progress still shows what he logged
        from core import diag
        diag.swallowed("pca watch progress", error)
    since = (today - timedelta(days=21)).isoformat()
    out["changes"] = [{"date": r["date"], "kind": r.get("kind") or r["type"], "reason": r["reason"]} for r in rows
                      if r["type"] in ("adjust", "deload", "plan", "pca", "override", "pain", "pain_clear", "external", "test")
                      and str(r.get("date")) >= since and r.get("reason")][-12:][::-1]
    w12 = start + timedelta(days=77)
    final = {t["event"]: t["value"] for t in _tests(data, rows) if w12.isoformat() <= t["date"] <= (w12 + timedelta(days=6)).isoformat()}
    out["long_term"]["next_emphasis"] = engine.next_block(final)
    out["exercises"] = _exercises(plan)
    return out


def health_sync_text() -> str:
    from tools import pca_link
    if not pca_link.paired():
        return "Install and pair Jarvis Link on your phone first; the Garmin card lists the steps."
    pca_health.request_sync("page")
    pca_link.keep(120)                                       # the phone polls now instead of after its idle sleep
    return ("Asked your phone for a sync. On Android 15 and newer it sends your Garmin data within a minute; on Android 14, "
            "open Jarvis Link on the phone to send it.")


def page_action(body: dict) -> str | dict:
    """One tap on the Captain America page. Each answer is one short sentence; nonsense is refused helpfully. The
    read-only actions (day, demo*) answer with a dict instead, which the route returns as it is (no page data)."""
    body = body if isinstance(body, dict) else {}
    action = str(body.get("action") or "")
    if action == "day":
        return day_detail(body.get("week"), body.get("day"))
    if action in ("demo", "demo_next", "demo_pin", "demo_unpin"):
        from tools import pca_demo
        vid = body.get("id")
        if action == "demo":
            return pca_demo.demo(body.get("key"))
        if action == "demo_next":
            return pca_demo.demo_next(body.get("key"), vid)
        if action == "demo_pin":
            return pca_demo.demo_pin(body.get("key"), vid)
        return pca_demo.demo_unpin(body.get("key"))
    if action in ("start", "set", "skip", "swap", "end"):
        return trainer.page_action(body)
    if action == "answer":
        return answer(str(body.get("id") or ""), body.get("value"))
    if action == "begin":
        return begin(str(body.get("start") or ""))
    if action == "end_program":
        return end_program()
    if action == "run":
        return log_run_text(body.get("minutes"), body.get("rpe"), body.get("distance_mi"), str(body.get("kind") or ""), body.get("completed_reps"),
                       body.get("slow_reps"), str(body.get("notes") or ""), str(body.get("date") or ""))
    if action == "test":
        return log_test(str(body.get("event") or ""), body.get("value"), str(body.get("date") or ""))
    if action == "pain":
        return report_pain_text(str(body.get("area") or ""), body.get("level"), str(body.get("note") or ""), bool(body.get("moves")))
    if action == "pain_clear":
        return clear_pain_text(str(body.get("area") or ""))
    if action == "external":
        return log_external(str(body.get("kind") or ""), body.get("minutes"), body.get("rpe"), body.get("miles"), str(body.get("date") or ""))
    if action == "override":
        return override_text()
    if action == "session_rpe":
        return session_rpe_text(body.get("rpe"), body.get("minutes"))
    if action == "rollback":
        return rollback(body.get("weeks", 1))
    if action == "audio":
        return audio_text(body.get("prefs"))
    if action == "music_next":
        return music_text(pca_music.next_song(str(body.get("phase") or ""), str(body.get("reason") or "start"), str(body.get("source") or ""),
                                              str(body["run_kind"])[:20] if body.get("run_kind") is not None else None, body.get("bpm") or 0,
                                              wait=False))
    if action in ("music_like", "music_unlike", "music_skip", "music_neutral"):
        return music_text(pca_music.feedback(action.split("_")[1], str(body.get("id") or "")[:40], wait=False))
    if action == "music_taste":
        return pca_music.set_taste(body)
    if action == "music_play":
        return music_text(pca_music.play_now(wait=False))
    if action in ("music_pause", "music_resume"):
        return music_text(pca_music.control(action.split("_")[1], wait=False))
    if action == "music_link":
        return pca_music.set_link_prefs(body)
    if action == "health_sync":                              # the GARMIN card's Sync now: the phone's next poll asks for it
        return health_sync_text()
    if action == "music_state":                              # read-only: the page polls what the phone is doing
        try:
            return {"dj": pca_music.music_state()}
        except Exception:                                    # noqa: BLE001 - never a crash for a poll
            return {"dj": None}
    return "I don't know that action."


# ---- what he hears ---------------------------------------------------------------------------------------------------
def status_text() -> str:
    data = fitness._data()
    if not _is_pca(data):
        a = engine.assess(_answers(data))
        if a["blocking"]:
            return "Project Captain America can't start yet. " + " ".join(a["blocking"])
        if a["missing"]:
            return (f"Project Captain America isn't started. I still need {plural(len(a['missing']), 'answer')}, starting with "
                    f"{engine.QUESTION[a['missing'][0]]['label'].lower()}.")
        return "Your answers are in. Say 'start Captain America' to begin the 12-week program."
    day = trainer.view(data=data)
    week = _week_n(data, trainer._today())
    if day["status"] == "before":
        return prestart_text(data)
    if day["status"] == "ended":
        return "The 12 weeks are done. Your week-12 results set the emphasis of the next block."
    wk = next((w for w in data["plan"]["weeks"] if w["week"] == week), {})
    block = next((b["name"] for b in data["plan"].get("blocks") or [] if week in b["weeks"]), "")
    head = f"Week {week} of 12, {block}: {wk.get('focus', '')}."
    if day["status"] == "rest":
        return head + " Today is a rest day." + (" " + " ".join(a["reason"] for a in day["adjustments"] if a.get("reason")) if day["adjustments"] else "")
    return head + " " + _briefing(day)


def progress_words() -> str:
    data = fitness._data()
    if not _is_pca(data):
        return "Project Captain America isn't running yet, so there is no program progress to show."
    p = page_data()
    h = p["history"]
    bits = []
    for t in h["tests"][-5:]:
        bits.append(f"{engine.EVENT_NAMES[t['event']]} {t['text']} on {t['date']}")
    line = ("Tests: " + "; ".join(bits) + ".") if bits else "No tests logged yet."
    weeks = [w for w in h["weeks"] if w["run_min_planned"]]
    if weeks:
        w = weeks[-1]
        line += f" Week {w['week']}: {w['run_min_done']} of {w['run_min_planned']} run minutes, {w['sets_done']} of {w['sets_planned']} lifting sets."
    if h["compliance"] is not None:
        line += f" Sessions done: {round(100 * h['compliance'])}%."
    for lift in h["lifts"][:2]:
        line += f" {lift['name']}: estimated max about {round(lift['points'][-1]['value'])} pounds from your logged sets."
    return line


# ---- the voice: lines for the ear, the briefing, holds, runs and tests -----------------------------------------------
# Every number said here comes from the resolved day (core/pca_engine) or his logged events. Cues are spoken only in a
# turn that came by voice (trainer.by_voice), through trainer.SAY, and stop with trainer._cancel_rest like the rest line.
_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")
_ZONE_SAY = {"E": "an easy effort", "S": "a steady effort", "T": "tempo effort", "I": "two-mile pace", "R": "a fast, relaxed effort"}
_EXAMPLE = {"run": "17:52", "hrp": "44 push-ups", "plk": "2:05", "sdc": "1:41", "mdl": "340"}
_TELL_RUN = "tell me the run: the minutes and how hard, out of 10."


def _word(n) -> str:
    n = float(n)
    return _WORDS[int(n)] if n == int(n) and 0 <= n < len(_WORDS) else f"{n:g}"


def _dur(seconds) -> str:
    """75 -> '1 minute 15 seconds'; 120 -> 'two minutes'; 150 -> '2 and a half minutes'."""
    s = int(round(float(seconds or 0)))
    if s < 60:
        return plural(s, "second")
    m, r = divmod(s, 60)
    if r == 0:
        return _word(m) + (" minute" if m == 1 else " minutes")
    if r == 30:
        return f"{m} and a half minutes"
    return f"{plural(m, 'minute')} {plural(r, 'second')}"


def _rest_phrase(seg: dict) -> str:
    """'200 m walk-jog (about 1:45)' -> 'a 200 metre walk-jog'."""
    rest = re.sub(r"\s*\([^)]*\)", "", str(seg.get("rest") or "").split(";")[0]).strip()
    rest = re.sub(r"\b(\d+) m\b", r"\1 metre", rest)
    rest = re.sub(r"\b(\d+) min\b", r"\1 minute", rest)
    rest = re.sub(r"\b(\d+) s\b", r"\1 second", rest)
    return ("a " + rest) if rest else "an easy walk"


def _split(it: dict, seg: dict):
    pace = (it.get("pace_s") or {}).get(seg.get("zone"))
    if not pace or not seg.get("m"):
        return None
    return pace[0] * seg["m"] / 1609.34, pace[1] * seg["m"] / 1609.34


def _seg_words(it: dict, seg: dict, rest: bool = True, brief: bool = False) -> str:
    """One run segment for the ear: 'six times 400 metres at two-mile pace, 2:13 to 2:18 each, with a 200 metre walk-jog
    between'. Splits only from a fresh two-mile test (it["pace_s"]); without one, the effort words alone."""
    reps = int(seg.get("reps") or 1)
    what = f"{float(seg['m']):g} metres" if seg.get("m") else plural(seg.get("min") or 0, "minute")
    if seg.get("zone") == "E" and reps == 1:
        z = program.ZONES["E"]
        return f"{what} easy" if brief else f"{what} easy, {z['rpe'].replace('-', ' to ')}, {z['talk'].split(',')[0]}"
    text = (f"{_word(reps)} times {what}" if reps > 1 else what) + f" at {_ZONE_SAY.get(seg.get('zone'), 'a hard, even effort')}"
    pair = _split(it, seg)
    if pair:
        text += f", {engine.mmss(pair[0])} to {engine.mmss(pair[1])}" + (" each" if reps > 1 else "")
    if rest and reps > 1 and seg.get("rest"):
        text += f", with {_rest_phrase(seg)} between"
    return text


def _distance_reps(run: dict) -> list[tuple]:
    """[(segment index, rep number, reps in that segment)] for a run he counts with 'rep done' (distance repeats)."""
    segs = run.get("segments") or []
    if not any(s.get("m") and int(s.get("reps") or 1) > 1 for s in segs):
        return []
    return [(n, i, int(s.get("reps") or 1)) for n, s in enumerate(segs) if s.get("m") for i in range(1, int(s.get("reps") or 1) + 1)]


def _timed_seg(run: dict) -> dict | None:
    return next((s for s in run.get("segments") or [] if s.get("min") and int(s.get("reps") or 1) > 1), None)


def _cool_line(run: dict) -> str:
    cool = run.get("cool_min") or 0
    return (f" Cool down {plural(cool, 'minute')} easy, then " if cool else " Then ") + _TELL_RUN


def _run_say(it: dict, short: bool = False) -> str:
    run = it["run"]
    head = str(it.get("exercise") or "Run").split(":")[0]
    if it.get("key") == "bike":
        return f"{head}: {it.get('reps', '')} at the same effort as the run." + ("" if short else " Then " + _TELL_RUN)
    if it.get("run_walk"):
        return f"{head}: {it.get('target', '')}" + ("" if short else " Then " + _TELL_RUN)
    body = ", then ".join(_seg_words(it, s) for s in run.get("segments") or [])
    if short:
        return f"{head}: {body}."
    if run.get("warm_min"):
        body = f"warm up {plural(run['warm_min'], 'minute')} easy, then {body}"
    text = f"{head}: {body}."
    if run.get("cool_min"):
        text += f" Cool down {plural(run['cool_min'], 'minute')} easy."
    if _distance_reps(run):
        return text + " Say 'rep done' after each one."
    if _timed_seg(run):
        return text + " Say go when you start the first one, and I'll call the changes."
    return text + " When you're back, " + _TELL_RUN


def _hold_say(it: dict, short: bool = False) -> str:
    lo, hi = it.get("lo") or 0, it.get("hi") or 0
    secs = plural(hi, "second") if lo == hi else f"{lo} to {hi} seconds"
    each = " each side" if "each side" in str(it.get("reps")) else ""
    text = f"{it['exercise']}: {plural(coach._sets(it) or 1, 'hold')} of {secs}{each}."
    return text if short else text + " Say go when you're set."


def _test_say(it: dict, short: bool = False) -> str:
    text = f"This is a test: {str(it.get('exercise', 'the test')).lower()}."
    if short:
        return text
    how = re.sub(r"\s*\([^)]*\)", "", str(it.get("target") or "").split(". ")[0]).strip().rstrip(".")
    return f"{text} {how}. Tell me the result when you're done, like '{_EXAMPLE.get(it.get('test'), '')}'."


def spoken_item(it: dict, short: bool = False) -> str:
    """A hold, run or test of the program as he hears it (trainer._line uses it through it["say"])."""
    if it.get("kind") == "test":
        return _test_say(it, short)
    if it.get("run"):
        return _run_say(it, short)
    return _hold_say(it, short)


def _clause(text: str) -> str:
    """The first clause of a sentence, for the briefing: 'You slept 4.5 hours, so today is easier.'"""
    text = re.sub(r"\s*\([^)]*\)", "", str(text or "")).strip()
    cut = min([i for i in (text.find(";"), text.find(": "), text.find(". ")) if i > 0] or [len(text)])
    text = text[:cut].strip().rstrip(".")
    return (text[0].upper() + text[1:] + ".") if text else ""


def _brief_item(it: dict) -> str:
    if it.get("kind") == "test":
        return f"the {it['exercise'].lower()}"
    if it.get("run"):
        run = it["run"]
        if it.get("key") == "bike":
            return f"{it.get('reps', '')} on the bike or rower"
        main = next((s for s in run.get("segments") or [] if s.get("zone") != "E"), (run.get("segments") or [{}])[0])
        return f"{str(it['exercise']).split(':')[0].lower()}, {_seg_words(it, main, rest=False, brief=True)}" if main else it["exercise"].lower()
    if it.get("kind") == "hold":
        text = _hold_say(it, short=True).rstrip(".")
        return text[0].lower() + text[1:]
    reps = str(it.get("reps", "")).replace("-", " to ")
    text = f"{it['exercise'].lower()}, {plural(coach._sets(it) or 1, 'set')} of {reps}"
    if it.get("load"):
        text += f" at {float(it['load']):g} pounds"
    elif it.get("gate") == "rir" and it.get("rir") is not None:
        text += f", {it['rir']} in reserve"
    return text


def briefing_text() -> str:
    """'What's my mission today': the title, the purpose, the main work with its real numbers and any change with its
    reason, kept to about 34 words (12 seconds of speech): the purpose goes first, then the second main item."""
    data = fitness._data()
    if not _is_pca(data):
        return status_text()
    day = trainer.view(data=data)
    if day["status"] != "train":
        return status_text()
    order = lambda it: 0 if it.get("kind") == "test" else 1 if it.get("run") else 2 if it.get("gate") == "rir" else 3   # noqa: E731
    main = sorted((it for it in day["items"] if it.get("priority") == 1 and not it.get("skipped")), key=order)[:2]
    # the change that matters most; of two of a kind the program's own (it comes after the coach's) is the fuller one
    rank = {"pain": 0, "dropped": 1, "external": 2, "readiness": 3, "run_volume": 4, "run_repeat": 5, "run_cap": 6}
    changes = [(rank.get(a.get("kind"), 9), -n, a["reason"]) for n, a in enumerate(day.get("adjustments", []))
               if a.get("reason") and a.get("kind") not in ("load", "override")]
    change = min(changes)[2] if changes else ""
    title = "Today's mission: " + re.sub(r"\b(\d+) x (\d+) m\b", r"\1 by \2 metres", day["title"].lower()) + "."
    purpose = _clause(day.get("stimulus") or day.get("why") or "")
    for keep_purpose, items in ((True, main), (False, main), (False, main[:1])):
        work = f"Main work: {' and '.join(_brief_item(it) for it in items)}." if items else ""
        text = " ".join(x for x in (title, purpose if keep_purpose else "", work, _clause(change)) if x)
        if len(text.split()) <= 34:
            break
    return text


def start_mission() -> str:
    """'Start my mission': the briefing, then the guided workout exactly as start_workout runs it."""
    data = fitness._data()
    if not _is_pca(data) or trainer.view(data=data)["status"] != "train":
        return briefing_text()
    brief = briefing_text()
    out = trainer.start()
    if trainer._by_voice.get():
        _music("warmup")
    return f"{brief} {out}"


def _current() -> tuple:
    """(day, index of the current item or None) of an open Captain America session; (None, None) when there is none."""
    data = fitness._data()
    if not _is_pca(data):
        return None, None
    day = trainer.view(data=data)
    return (day, day["session"]["current"]) if day["session"]["open"] else (day, None)


def _cues(plan: list, uid: str) -> None:
    for seconds, line in plan:
        try:
            trainer.SAY(int(seconds), line, uid)
        except Exception:                                    # noqa: BLE001 - a cue that cannot start never loses the turn
            pass


def _timeline(seg: dict, run: dict) -> list[tuple]:
    """A time-based repeat ('6 x 1 minute hard, 2 minutes easy') as (seconds from go, line): the first line is said now,
    the others are cues."""
    reps, work, rest = int(seg.get("reps") or 1), float(seg.get("min") or 0) * 60, int(seg.get("rest_s") or 0)
    effort = "hard" if seg.get("zone") in ("T", "I", "R") else "steady"
    out, t = [(0, f"Go: {_dur(work)} {effort}.")], 0.0
    for i in range(reps):
        if i:
            out.append((t, f"Rep {i + 1} of {reps}: {_dur(work)} {effort}."))
        t += work
        if i < reps - 1:
            out.append((t, f"Easy for {_dur(rest)}."))
            t += rest
        else:
            out.append((t, "Time. That was the last one." + _cool_line(run)))
    return out


def go() -> str:
    """'Go': starts the timed part of the current item. A hold gets 'Halfway', 'Ten seconds' and 'Time'; a time-based run
    gets every change called; distance repeats are counted with 'rep done'."""
    with trainer._lock:
        day, n = _current()
        if day is None:
            return "No Captain America session is running."
        if n is None:
            return "No workout is running. Say 'start my mission' to begin." if not day["session"]["open"] else \
                "Everything in today's session is logged. Say 'end my workout' for the summary."
        it = day["items"][n]
    uid = users.current()
    voice = trainer._by_voice.get()
    trainer._cancel_rest(uid)
    if voice:
        _music(audio_phase(day))
    if it.get("kind") == "hold":
        lo, hi = int(it.get("lo") or 0), int(it.get("hi") or 0)
        secs = hi if lo == hi else lo
        if voice and secs:
            _cues([(secs // 2, "Halfway.")] + ([(secs - 10, "Ten seconds.")] if secs >= 25 else []) +
                  [(secs, "Time. Say done, or the seconds you held.")], uid)
        each = " each side" if "each side" in str(it.get("reps")) else ""
        return f"Go: hold {plural(secs, 'second')}{each}." if lo == hi else f"Go: hold at least {plural(lo, 'second')}, up to {hi}{each}."
    run = it.get("run")
    if run:
        timed = _timed_seg(run)
        if timed and not _distance_reps(run):
            plan = _timeline(timed, run)
            if voice:
                _cues(plan[1:], uid)
            return plan[0][1]
        if _distance_reps(run):
            return "Go. Say 'rep done' after each one."
        return "Go. When you're back, " + _TELL_RUN
    if it.get("kind") == "test":
        return "Go. Tell me the result when you're done."
    return "Go when you're ready, and tell me the set when it's done."


def rep_done() -> str:
    """'Rep done' in distance repeats: counts the rep, says the rest, and a cue says go when the rest is over."""
    with trainer._lock:
        day, n = _current()
        it = day["items"][n] if day is not None and n is not None else {}
        run = it.get("run") or {}
        flat = _distance_reps(run)
        if not flat:
            return "No repeats are running right now. 'Rep done' is for interval repeats."
        today = trainer._today().isoformat()
        done = sum(1 for r in trainer.events() if r["type"] == "rep" and r.get("date") == today and str(r["t"]) >= day["session"]["started"])
        if done >= len(flat):
            return "That's every rep already." + _cool_line(run)
        trainer.record({"type": "rep", "key": "run", "n": done + 1})
    uid = users.current()
    trainer._cancel_rest(uid)
    segs = run["segments"]
    s, i, reps = flat[done]
    head = f"Rep {i} of {reps} done."
    if done + 1 == len(flat):
        return head + " That's every rep." + _cool_line(run)
    s2, i2, reps2 = flat[done + 1]
    rest = int(segs[s].get("rest_s") or 0)
    if s2 == s:
        cue, tail = f"Go: rep {i2} of {reps2}.", f" Rest {_dur(rest)}, {_rest_phrase(segs[s])}." if rest else ""
    else:
        nxt = _seg_words(it, segs[s2], rest=False)
        cue = f"Go: {nxt}. Rep 1 of {reps2}." if reps2 > 1 else f"Go: {nxt}."
        tail = (f" Rest {_dur(rest)}, then {nxt}." if rest else f" Next: {nxt}.")
    if trainer._by_voice.get() and rest:
        _cues([(rest, cue)], uid)
        return head + tail + " I'll say go."
    return head + tail + " Go when you're ready."


def _next_words() -> str:
    """After a run or a test logged by voice inside an open session: what comes next."""
    if not trainer._by_voice.get():
        return ""
    day, n = _current()
    if day is None or not day["session"]["open"]:
        return ""
    if n is None:
        return " Everything in today's session is logged. Say 'end my workout' for the summary."
    return " Next: " + trainer._line(day["items"][n])


def _first_full_week(data: dict, rows: list, today: date) -> str:
    """'Your first full week': every required session of this program week done, said once, from logged events only."""
    if any(r["type"] == "pca" and r.get("kind") == "milestone" and r.get("what") == "first_full_week" for r in rows):
        return ""
    week, start = _week_n(data, today), _start(data)
    wk = next((w for w in (data.get("plan") or {}).get("weeks") or [] if w.get("week") == week), None)
    if not wk or not start:
        return ""
    monday = start + timedelta(days=7 * (week - 1))
    due = [(monday + timedelta(days=DAY_INDEX[d["day"]])).isoformat() for d in wk["days"] if not d.get("optional")]
    done = {str(w.get("date")) for w in data.get("workouts") or []} | {str(r.get("date")) for r in rows if r["type"] in ("set", "run", "test")}
    if not due or not all(d in done for d in due):
        return ""
    trainer.record({"type": "pca", "kind": "milestone", "what": "first_full_week", "week": week,
                    "reason": f"Every session of week {week} done: the first full week of the program."})
    return f"That's every session of week {week} done: your first full week of the program."


def end_words(day: dict) -> str:
    """What the end of a Captain America session adds: the cool-down, and a milestone only when one really happened."""
    out = []
    cool = day.get("cooldown") or []
    if cool and day.get("status") == "train":
        out.append("Cool down now: " + ", then ".join(f"{fitness._drill(c['exercise']).lower()}, {c['dose']}" for c in cool[:2]) + ".")
    data = fitness._data()
    if _is_pca(data):
        line = _first_full_week(data, trainer.events(), trainer._today())
        if line:
            out.append(line)
    if trainer._by_voice.get():
        _music("cooldown")
    return " ".join(out)


# ---- what he says without every number: one question, asked once ---------------------------------------------------
ASK_SECONDS = 180
_ASK: dict[str, dict] = {}            # person -> {"skill", "args", "need", "t"}: the one question waiting for his answer


def _ask(skill: str, args: dict, need: str) -> None:
    if trainer._by_voice.get():
        _ASK[users.current()] = {"skill": skill, "args": {k: v for k, v in args.items() if v not in (None, "", 0, -1)},
                                 "need": need, "t": trainer.clock()}


def asking() -> bool:
    """Is a question of the coach waiting for his answer (so a bare '5' is his pain level, not noise)?"""
    ask = _ASK.get(users.current())
    return bool(ask) and (trainer.clock() - ask["t"]).total_seconds() < ASK_SECONDS


def voice_context() -> dict:
    """What core/coachintent needs to route a Captain America line: is the program on, the current item of an open
    session, is a change active today that 'run it as planned' can undo, the open pain areas, and the question waiting
    for an answer (handed over once: whatever he says next, it is not asked again)."""
    ask = _ASK.pop(users.current(), None)
    if ask and (trainer.clock() - ask["t"]).total_seconds() >= ASK_SECONDS:
        ask = None
    data = fitness._data()
    if not _is_pca(data):
        return {"on": False, "ask": ask}
    day = trainer.view(data=data)
    n = day["session"]["current"] if day["session"]["open"] else None
    it = day["items"][n] if n is not None else None
    current = None
    if it:
        run = it.get("run") or {}
        current = {"kind": it.get("kind"), "test": it.get("test"), "run": bool(run), "reps": bool(_distance_reps(run)) if run else False,
                   "timed": bool(_timed_seg(run)) if run else False}
    music = {"enabled": not pca_music.gate(data), "recent": pca_music.picked_within(1800),   # brain/llm adds "prompted"
             "link": pca_music.link_reachable()}            # pause / resume / what song: only with the phone's Link reachable
    return {"on": True, "ask": ask, "current": current, "open": bool(day["session"]["open"]), "music": music,
            "adjusted": any(a.get("overridable") for a in day.get("adjustments", [])),
            "pain": [f["area"] for f in _open_pain(trainer.events())]}


# ---- Performance Audio: music hooks ----------------------------------------------------------------------------------
# Real: audio_phase (pure), and audio_cue opening the playlist link he saved for a phase in his default browser or app
# (tools/system.open_website, https only) when he turned auto-open on. Off by default: nothing happens until he sets
# {"auto_open": true, "playlist_<phase>": "https://..."} on the page. NOT possible with tools/media.py: lowering only
# the music while Jarvis speaks. media.py controls the MASTER volume, which would lower his voice too, so
# duck_media_for_speech is a named no-op.
AUDIO_PHASES = ("warmup", "lift", "easy", "hard", "cooldown", "rest")
_audio_last: dict[str, str] = {}      # person -> the phase whose playlist was last opened (never reopened twice in a row)


def audio_phase(day_view: dict) -> str:
    """Which music fits right now: rest day, warm-up (not started), lift, easy or hard running, cool-down (all logged)."""
    day = day_view or {}
    if day.get("status") != "train":
        return "rest"
    session = day.get("session") or {}
    if session.get("ended"):
        return "cooldown"
    if not session.get("open"):
        return "warmup"
    n = session.get("current")
    if n is None:
        return "cooldown"
    it = day["items"][n]
    if it.get("kind") == "test" or (it.get("run") or {}).get("type") in engine.HARD_RUN or it.get("key") in ("sprint", "sdc"):
        return "hard"
    if it.get("run"):
        return "easy"
    return "lift"


def _open_link(url: str) -> str:
    from tools import system
    return system.open_website(url)


def audio_cue(phase: str, prefs: dict | None = None) -> str:
    """Open his playlist for this phase when he asked for it (prefs auto_open + playlist_<phase>, an https link).
    -> 'off' | 'no link' | 'same' | 'opened' | 'failed'. No download, no streaming, no third-party API."""
    if phase not in AUDIO_PHASES:
        return "off"
    prefs = prefs if prefs is not None else _state(fitness._data()).get("audio") or {}
    if prefs.get("auto_open") not in (True, "true", "on", "yes"):
        return "off"
    url = str(prefs.get(f"playlist_{phase}") or "").strip()
    if not url.startswith("https://"):
        return "no link"
    uid = users.current()
    if _audio_last.get(uid) == phase:
        return "same"
    try:
        _open_link(url)
    except Exception:                                        # noqa: BLE001 - music never loses the workout turn
        return "failed"
    _audio_last[uid] = phase
    return "opened"


def duck_media_for_speech() -> bool:
    """NO-OP: tools/media.py has no per-app (music-only) volume; its master volume would lower Jarvis's own voice too.
    Returns False so callers know nothing was ducked."""
    return False


def _music(phase: str) -> None:
    try:
        audio_cue(phase)
    except Exception:                                        # noqa: BLE001
        pass


# ---- the skills ------------------------------------------------------------------------------------------------------
tool = registry.tool


@tool("Where Project Captain America stands: the week, the phase, today's session and why ('how's Captain America going', "
      "'what's today in my program', 'what week am I in').", direct=True)
def captain_status() -> str:
    return status_text()


@tool("Save one onboarding answer for Project Captain America ('I train 6 days a week', 'my pain level is 2', 'I have a trap "
      "bar and dumbbells'). question is the question id: " + ", ".join(q["id"] for q in engine.QUESTIONS) + ".",
      {"question": {"type": "string", "description": "The question id"},
       "value": {"type": "string", "description": "His answer; a checklist as comma-separated ids"}}, required=["question", "value"], direct=True)
def captain_onboarding_answer(question: str, value: str) -> str:
    return answer(question, value)


@tool("Start the 12-week Project Captain America program once onboarding is complete ('start Captain America', 'begin the "
      "program next Monday').", {"start": {"type": "string", "description": "YYYY-MM-DD when he names a start date"}}, required=[], direct=True)
def captain_begin(start: str = "") -> str:
    return begin(start)


@tool("End the Project Captain America program on his word ('end Captain America', 'stop the program'). The plan is archived; "
      "every logged set, run and test stays.", direct=True)
def captain_end() -> str:
    return end_program()


@tool("Log a run he did ('ran 25 minutes at RPE 4', 'did the intervals, 35 minutes, RPE 8, the last two reps were slow', "
      "'3 miles in 27 minutes, easy').",
      {"minutes": {"type": "number"}, "rpe": {"type": "integer", "description": "How hard, 1-10"},
       "distance_mi": {"type": "number", "description": "Miles, when he says"},
       "kind": {"type": "string", "description": "easy, long, recovery, tempo, intervals, fartlek, race_pace or sharpen, when he says"},
       "slow_reps": {"type": "integer", "description": "Interval reps slower than the range, when he says"},
       "notes": {"type": "string"}}, required=[], direct=True)
def log_run(minutes: float = 0, rpe: int = 0, distance_mi: float = 0, kind: str = "", slow_reps: int = 0, notes: str = "") -> str:
    args = {"minutes": minutes, "rpe": rpe, "distance_mi": distance_mi, "kind": kind, "slow_reps": slow_reps, "notes": notes}
    if not minutes:
        _ask("log_run", args, "minutes")
    elif not rpe:
        _ask("log_run", args, "rpe")
    return log_run_text(minutes, rpe, distance_mi, kind, None, slow_reps, notes)


@tool("Record a fitness test result inside the program ('my push-up test was 42', 'plank 1:52', 'two-mile 17:40', 'deadlift "
      "triple at 315', 'sprint-drag-carry 1:48'). Recorded as a real result.",
      {"event": {"type": "string", "description": "mdl, hrp, sdc, plk or run"},
       "value": {"type": "string", "description": "lb for mdl, reps for hrp, m:ss for the others"}}, required=["event", "value"], direct=True)
def log_fitness_test(event: str, value: str) -> str:
    return log_test(event, value)


@tool("Report pain or an injury so today's training works around it ('my knee hurts, about a 5', 'my shoulder is at 3', "
      "'my ankle hurts and I'm limping'). Never a diagnosis.",
      {"area": {"type": "string"}, "level": {"type": "integer", "description": "0-10"},
       "changes_movement": {"type": "boolean", "description": "True when it changes how he moves (limping, can't press)"},
       "note": {"type": "string"}}, required=["area"], direct=True)
def report_pain(area: str, level: int = -1, changes_movement: bool = False, note: str = "") -> str:
    if (level is None or level < 0) and not _is_chest(area):
        _ask("report_pain", {"area": area, "changes_movement": changes_movement, "note": note}, "level")
        return report_pain_text(area, None, note, changes_movement)
    return report_pain_text(area, level, note, changes_movement)


@tool("Clear a pain flag when it no longer hurts ('my knee is fine now', 'the shoulder pain is gone').",
      {"area": {"type": "string", "description": "The area, or 'all'"}}, required=["area"], direct=True)
def clear_pain(area: str) -> str:
    return clear_pain_text(area)


@tool("Log unit PT or other outside training so the program accounts for it ('we ran 4 miles hard at PT', 'did a 6 mile ruck', "
      "'unit leg day this morning', 'field day').",
      {"kind": {"type": "string", "description": "unit_run, ruck, unit_strength or field"}, "minutes": {"type": "number"},
       "rpe": {"type": "integer", "description": "How hard, 1-10"}, "miles": {"type": "number"}}, required=["kind"], direct=True)
def log_unit_pt(kind: str, minutes: float = 0, rpe: int = 0, miles: float = 0) -> str:
    out = log_external(kind, minutes, rpe, miles)
    need = next((n for head, n in (("What was it", "kind"), ("How many minutes", "minutes"), ("How hard", "rpe")) if out.startswith(head)), "")
    if need:                                                 # the one thing he left out: asked once
        _ask("log_unit_pt", {"kind": kind, "minutes": minutes, "rpe": rpe, "miles": miles}, need)
    return out


@tool("Run today's Captain America session as planned, undoing the changes made for sleep, readiness or outside load "
      "('run it as planned', 'I feel fine, give me the full session'). Pain changes stay.", direct=True)
def captain_override() -> str:
    return override_text()


@tool("Progress in Project Captain America from his logged sets, runs and tests ('how am I doing in the program', 'show my "
      "test results'). Only real results, no predictions.", direct=True)
def captain_progress() -> str:
    return progress_words()


@tool("Today's Captain America briefing, short enough to hear: the session, its purpose, the main work with its real "
      "numbers, and any change with its reason ('what's my mission today', 'brief me', 'mission briefing').", direct=True)
def captain_briefing() -> str:
    return briefing_text()


@tool("Start today's Captain America session by voice: the briefing, then the guided workout ('start my mission').", direct=True)
def captain_start() -> str:
    return start_mission()


@tool("'Go' in a guided Captain America session: starts the hold timer (halfway, ten seconds, time) or calls the changes "
      "of a timed run.", direct=True)
def captain_go() -> str:
    return go()


@tool("'Rep done' in interval repeats: counts the rep, says the rest, and says go when the rest is over.", direct=True)
def captain_rep() -> str:
    return rep_done()


def _voice_pick(source: str = "", reason: str = "") -> str:
    """A pick said out loud. On this machine the song opens only when he turned auto-open on (off by default)."""
    off = pca_music.gate()
    if off:
        return off
    st = pca_music._state()
    out = pca_music.next_song(reason=reason or ("next" if st.get("now") else "start"), source=source, wait=True)
    if not out.get("ok") or not out.get("song"):
        return music_text(out)
    if out.get("link_message"):                              # Jarvis Link tried it: the message says the true outcome
        return out["message"]
    prefs = _state(fitness._data()).get("audio") or {}
    if prefs.get("auto_open") in (True, "true", "on", "yes") and out["song"].get("link"):
        try:
            _open_link(out["song"]["link"])
            return out["message"]
        except Exception:                                    # noqa: BLE001 - the pick still stands on the page
            pass
    return out["message"] + " Tap Play on the Captain America page."


@tool("Jarvis picks another workout song for Project Captain America ('play something else', 'next song'). The song he had "
      "is neither liked nor skipped.", direct=True)
def music_next() -> str:
    return _voice_pick()


@tool("He likes the song Jarvis picked ('I like this song', 'like this one'): more like it.", direct=True)
def music_like() -> str:
    return music_text(pca_music.feedback("like", wait=True))


@tool("Skip the song Jarvis picked ('skip this song', 'I don't like this one'): never again, and another one.", direct=True)
def music_skip() -> str:
    off = pca_music.gate()
    if off:
        return off
    if pca_music._queue_target() is not None:               # Metrolist's own queue is playing: skip THAT track
        return music_text(pca_music.feedback("skip", wait=True))
    return _voice_pick(reason="skip") if pca_music._state().get("now") else "There is no song of mine playing to skip."


@tool("Play a motivational speech in the workout ('play a motivational speech').", direct=True)
def music_speech() -> str:
    return _voice_pick("speech")


@tool("Play an Army running cadence ('play an army cadence', 'play running cadence').", direct=True)
def music_cadence() -> str:
    return _voice_pick("cadence")


@tool("Pause the workout music on his phone through Jarvis Link ('pause the music').", direct=True)
def music_pause() -> str:
    return music_text(pca_music.control("pause", wait=True))


@tool("Resume the workout music on his phone through Jarvis Link ('resume the music').", direct=True)
def music_resume() -> str:
    return music_text(pca_music.control("resume", wait=True))


@tool("Which song is playing on his phone, from Jarvis Link's report ('what song is this').", direct=True)
def music_what() -> str:
    return pca_music.what_song()
