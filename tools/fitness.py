"""Personal trainer: built to max the Army Fitness Test (AFT), fed by your Garmin.

AFT (since June 2025): five events, each scored 0-100 (500 max):
  MDL  3-repetition maximum deadlift      HRP  hand-release push-ups (2 minutes)
  SDC  sprint-drag-carry (5 x 50 m)       PLK  plank (time)        2MR  2-mile run
Minimum 60 points per event. Combat MOSs use one sex-neutral standard with a 350 minimum total; other
MOSs are scored by age and sex with a 300 minimum. Score tables change, so Jarvis looks up the current
official scales (army.mil) rather than trusting memory.

Garmin, the supported route: the Garmin Connect phone app writes his watch data to Android Health Connect and the
Jarvis Link app posts it to Jarvis (tools/pca_health). garmin_data(data) merges that with the older direct sync below,
so every reader of data["garmin"] sees both.
Garmin, the old route (kept for existing tokens): a one-time login (garmin-login.bat) saves a renewable token in
data/users/<id>/garmin/; no password is stored. garmin_sync pulls runs, sleep, HRV, resting HR, body battery, training
readiness, VO2 max and race predictions (a 2-mile estimate) every morning. Garmin's portal now blocks that login
(HTTP 403, a bot challenge), so new setups use Health Connect.

Everything is stored per person in data/users/<id>/fitness.json and shown in the phone fitness app
(/fitness.html).
"""
from datetime import date, datetime, timedelta

from core import storage
from tools import fitness_library as lib
from core.words import plural
from tools.registry import tool

FILE = "fitness.json"
EVENTS = {"mdl": "3-rep max deadlift (lbs)", "hrp": "hand-release push-ups", "sdc": "sprint-drag-carry (m:ss)",
          "plk": "plank (m:ss)", "run": "2-mile run (mm:ss)"}
PROFILE_FIELDS = ["age", "sex", "mos_category", "height", "weight_lbs", "goal_total", "test_date", "equipment",
                  "days_per_week", "session_minutes", "injuries", "physique_goal", "weak_points", "heritage", "notes",
                  "goal", "training_months"]

# Exercise library: the phone app animates these keys and shows the coaching cues.
EXERCISES = {
    "deadlift": ("Trap-bar deadlift", "mdl", "Feet hip-width, grip the handles, chest up, brace hard, push the floor away; lock out hips and knees together; lower with control."),
    "hand_release_pushup": ("Hand-release push-up", "hrp", "Body in a straight line; lower until chest touches, lift hands off the ground, replace, press up in one piece. Don't let the hips sag."),
    "plank": ("Plank", "plk", "Forearms under shoulders, squeeze glutes, ribs down, straight line head to heels; breathe slowly."),
    "sprint": ("Sprint", "sdc", "Drive the arms, stay on the balls of the feet, lean slightly forward; turn at the line with a low, quick plant."),
    "sled_drag": ("Sled drag (backwards)", "sdc", "Grip the straps, sit back, chest up, take quick short steps backwards keeping tension on the straps."),
    "lateral_shuffle": ("Lateral shuffle", "sdc", "Hips low, feet never cross, stay facing the same direction, push off the trailing leg."),
    "kettlebell_carry": ("Kettlebell carry", "sdc", "Two 40 lb kettlebells, shoulders down and back, tall posture, fast short steps."),
    "run": ("Run", "run", "Relaxed shoulders, quick cadence (about 170-180 steps a minute), land under the hips."),
    "squat": ("Goblet squat", "mdl", "Weight at the chest, sit between the heels, knees track over toes, chest up, drive up through mid-foot."),
    "lunge": ("Walking lunge", "sdc", "Long step, back knee just off the floor, front shin vertical, push through the front heel."),
    "kettlebell_swing": ("Kettlebell swing", "mdl", "Hinge (not squat), hike the bell back, snap the hips so it floats to chest height; arms are just ropes."),
    "row": ("Bent-over row", "hrp", "Hinge, flat back, pull elbows toward the hips, pause, lower slowly."),
    "dead_bug": ("Dead bug", "plk", "Low back pressed into the floor, reach opposite arm and leg away slowly, breathe out."),
    "farmer_carry": ("Farmer carry", "sdc", "Heavy dumbbells or kettlebells at the sides, tall, brace, walk with control."),
    "incline_press": ("Incline dumbbell press", "hrp", "Bench at 30 degrees; shoulder blades pinched and down, lower the dumbbells to upper-chest level with elbows about 45 degrees from the body, press up and slightly in. Builds the upper chest."),
    "bench_press": ("Bench press", "hrp", "Eyes under the bar, shoulder blades squeezed, feet planted; lower to mid-chest with control, press up and slightly back. Leave 1-2 reps in the tank on most sets."),
    "dip": ("Chest dip", "hrp", "Lean the torso forward, elbows slightly out, lower until you feel a stretch in the chest, press up without locking hard."),
    "chest_fly": ("Cable / dumbbell fly", "hrp", "Slight bend in the elbows, open wide until a deep chest stretch, then hug a big tree; squeeze the chest for a second at the top."),
    **lib.EXTRA_EXERCISES,  # physique work, warm-up drills and cool-down stretches
}


# Official AFT scales (army.mil, AFT_Scoring_Scales_250601.pdf, effective 1 June 2025), male 22-26 column,
# which is also the sex-neutral combat standard. Points -> time.
AFT_MALE_22_26 = {"plk": {100: "3:35", 90: "3:03", 80: "2:30", 75: "2:14", 70: "1:58", 65: "1:41", 60: "1:25"},
                  "run": {100: "13:25", 90: "15:38", 75: "17:55", 70: "18:23", 65: "19:07", 60: "19:45"}}

# The evidence the trainer follows. Jarvis reads this before designing or changing a plan.
SCIENCE = """Evidence base for the plans (what the research and Army guidance support):
RUNNING (2-mile): combine interval work near/above goal 2-mile pace, threshold (tempo) runs, and easy aerobic running;
soldier trials show endurance training is what lowers 2-mile time, and Army guidance found 3 well-designed run days
(not 5) gave equal 2-mile times with substantially less injury. Increase weekly running by no more than about 10% a
week, keep most running easy (conversational) and only 2 hard run sessions a week. Retest every 4-6 weeks.
PLANK: train it 2-3 times a week with several near-max holds (e.g. 3-4 sets at 70-80% of your max, short rests)
rather than one all-out hold; add 10-15 s per set per week; once holds pass about 3 minutes, use harder variations
(weighted, long-lever, RKC plank) and bracing work (dead bugs, side planks). Retest the max every 2-3 weeks.
MUSCLE / CHEST: hypertrophy rises with weekly hard sets (dose-response meta-analyses: about 10-20+ sets per muscle per
week), taken close to failure (1-3 reps in reserve), each muscle trained at least twice a week, 6-15 reps, progressive
overload. A weak chest gets the high end: incline pressing first (upper chest), flat press, dips, flyes.
CONCURRENT TRAINING: meta-analyses find little or no loss of whole-muscle growth from adding cardio when it's managed;
interference is larger with running and lower-body sessions on the same day, so put hard runs and heavy leg work on
different days (or 6+ hours apart), and don't let run volume balloon.
STRENGTH MAINTENANCE: a maxed deadlift is kept with much less volume (about a third of building volume, heavy, 1x a week).
NUTRITION: protein about 1.6-2.2 g/kg/day (0.7-1 g/lb) supports muscle gain; a small deficit (~300 kcal) allows slow
fat loss for definition while keeping performance; sleep 7-9 h (low sleep and low HRV = cut the hard day back).
DELOAD every 4th week (cut volume ~40%), and taper the final 7-10 days before a record AFT.
Sources: Army AFT scoring scales (army.mil, June 2025); army.mil 'Avoiding common PT test training errors';
Lundberg et al. 2022 concurrent-training meta-analysis; Wilson et al. 2012; Schoenfeld et al. 2017 volume dose-response;
Morton et al. 2018 protein meta-analysis; NSCA concurrent-training review; British Army 10-week concurrent trial."""


def _data() -> dict:
    data = storage.load(FILE, {})
    for key, empty in (("profile", {}), ("aft_tests", []), ("plan", {}), ("workouts", []), ("garmin", {}), ("pca", {})):
        if not isinstance(data.get(key), type(empty)):       # an older file without a section must still load
            data[key] = empty
    return data


@tool("The training science the plans must follow (running, plank, muscle/chest, concurrent training, nutrition, "
      "recovery) with sources, plus the official AFT targets for males 22-26 (also the combat standard). Read it "
      "before designing or changing a training plan.")
def training_science() -> str:
    t = AFT_MALE_22_26
    targets = ("Official AFT targets, male 22-26 / combat: plank " +
               ", ".join(f"{p} pts = {v}" for p, v in t["plk"].items()) + "; 2-mile run " +
               ", ".join(f"{p} pts = {v}" for p, v in t["run"].items()) + ".")
    return SCIENCE + "\n" + lib.SCIENCE_EXTRA + "\n" + targets


def _save(data: dict) -> None:
    storage.save(FILE, data)


def _secs(v) -> int | None:
    """'15:20' / '3:10' / '920' -> seconds."""
    if v in (None, ""):
        return None
    s = str(v).strip()
    if ":" in s:
        m, sec = s.split(":", 1)
        return int(m) * 60 + int(float(sec))
    try:
        return int(float(s))
    except ValueError:
        return None


def _mmss(sec) -> str:
    return f"{int(sec) // 60}:{int(sec) % 60:02d}" if sec else ""


# -- profile, tests ------------------------------------------------------------------------------
@tool(
    "Save something about the user for their training: age, sex, mos_category (combat / non-combat), height, "
    "weight_lbs, goal_total (e.g. 500), test_date (YYYY-MM-DD of the next AFT), equipment (gym, home, trap bar...), "
    "days_per_week, session_minutes, injuries, heritage (e.g. Haitian: adds evidence-based health notes such as "
    "sickle cell trait and vitamin D to the coach page), notes.",
    {"field": {"type": "string", "enum": PROFILE_FIELDS}, "value": {"type": "string"}},
)
def set_fitness_profile(field: str, value: str) -> str:
    data = _data()
    data["profile"][field] = value.strip()
    _save(data)
    return f"Saved {field.replace('_', ' ')}: {value}."


@tool(
    "Record an AFT result (a real test or a practice test). Times as m:ss. Points per event are optional (from the "
    "official scorecard or app); if missing, look up the current official scoring scale to estimate them.",
    {"date": {"type": "string", "description": "YYYY-MM-DD"}, "mdl": {"type": "integer", "description": "deadlift lbs"},
     "hrp": {"type": "integer"}, "sdc": {"type": "string"}, "plk": {"type": "string"}, "run": {"type": "string"},
     "points": {"type": "object", "description": "Optional, e.g. {\"mdl\": 90, \"hrp\": 72, ...}"},
     "official": {"type": "boolean", "description": "true for a record test, false for practice"}},
    required=["date"],
)
def log_aft_test(date: str, mdl: int = 0, hrp: int = 0, sdc: str = "", plk: str = "", run: str = "",
                 points: dict | None = None, official: bool = False) -> str:
    data = _data()
    test = {"date": date, "mdl": mdl or None, "hrp": hrp or None, "sdc": _secs(sdc), "plk": _secs(plk),
            "run": _secs(run), "points": {k: int(v) for k, v in (points or {}).items() if k in EVENTS},
            "official": bool(official)}
    data["aft_tests"] = sorted([t for t in data["aft_tests"] if t["date"] != date] + [test], key=lambda t: t["date"])
    _save(data)
    return "Saved the AFT result. " + aft_status()


@tool("Where the user stands on the AFT: latest results, points per event, total, change since the previous test, "
      "weakest events and days until the next test.")
def aft_status() -> str:
    data = _data()
    tests = data["aft_tests"]
    if not tests:
        return "No AFT results yet: ask for their last scores (or run a practice test)."
    last, prev = tests[-1], (tests[-2] if len(tests) > 1 else None)

    def fmt(ev, t):
        v = t.get(ev)
        return "-" if v is None else (f"{v} lbs" if ev == "mdl" else str(v) if ev == "hrp" else _mmss(v))
    lines = [f"Latest AFT ({last['date']}{', record' if last.get('official') else ', practice'}):"]
    for ev, name in EVENTS.items():
        pts = last["points"].get(ev)
        change = ""
        if prev and prev.get(ev) is not None and last.get(ev) is not None:
            d = last[ev] - prev[ev]
            better = d < 0 if ev in ("sdc", "run") else d > 0
            unit = " s" if ev in ("sdc", "plk", "run") else ""
            change = f" ({'better' if better else 'worse' if d else 'same'}: {abs(d)}{unit})"
        lines.append(f"- {name.split(' (')[0]}: {fmt(ev, last)}" + (f" = {pts} pts" if pts is not None else "") + change)
    if len(last["points"]) == 5:
        total = sum(last["points"].values())
        weakest = sorted(last["points"], key=last["points"].get)[:2]
        lines.append(f"Total {total}/500. Weakest: {', '.join(weakest)}.")
    test_date = data["profile"].get("test_date")
    if test_date:
        try:
            left = (datetime.fromisoformat(test_date).date() - date.today()).days
            lines.append(f"Next AFT in {plural(left, 'day')}." if left >= 0 else
                         f"The AFT date on file ({test_date}) has passed: tell me the next one.")
        except ValueError:
            pass
    return "\n".join(lines)


# -- plan and workouts ---------------------------------------------------------------------------
@tool(
    "Save the training plan you designed, shown day by day in the phone app with animated how-tos. Design it like a "
    "top-tier tactical strength coach: periodized blocks (base, build, peak, taper the last 7-10 days before the "
    "test), 4-6 days a week, every AFT event trained: deadlift strength (3RM work, hinges), hand-release push-up "
    "volume and pace, SDC (sprints, sled drags, lateral shuffles, 40 lb carries), plank holds and core, running "
    "(intervals at goal 2-mile pace, tempo, an easy long run). Prioritize their weakest events, use their Garmin "
    "paces/VO2max/race predictions for run targets, respect equipment, injuries and time. Use exercise keys from "
    "the library (deadlift, hand_release_pushup, plank, sprint, sled_drag, lateral_shuffle, kettlebell_carry, run, "
    "squat, lunge, kettlebell_swing, row, dead_bug, farmer_carry, incline_press, bench_press, dip, chest_fly, rdl, "
    "bulgarian_split_squat, pull_up, chest_supported_row, lateral_raise, overhead_press, face_pull, rear_delt_fly, curl, "
    "overhead_triceps, pushdown, calf_raise, hanging_leg_raise, side_plank, hip_thrust, cable_crossover) or any other "
    "name. A RAMP warm-up and a cool-down are added to every day automatically from the session type (only give a "
    "day its own 'warmup'/'cooldown' to override). Program like a pro: each item gets sets, a rep range, a target "
    "(load / pace / RIR), rest, tempo for hypertrophy work (e.g. '3-1-1', slow lowering) and the progression rule; "
    "give each day a short 'why' (the purpose of the session). Physique matters too (profile physique_goal / weak_points): add hypertrophy work for an aesthetic "
    "build: 10-20 hard sets per muscle per week in the 6-15 rep range, progressive overload, extra volume for weak "
    "points (a weak chest: incline presses first, flat press, dips, flyes, about 14-20 sets a week across 2-3 sessions), "
    "plus shoulders, back and arms for the V-taper. Manage the interference effect: keep running quality over junk "
    "miles, separate heavy leg days from sprint/SDC days, and feed recovery (protein about 0.8-1 g per lb).",
    {"name": {"type": "string", "description": "e.g. '10-week AFT 500 plan'"},
     "start": {"type": "string", "description": "YYYY-MM-DD (a Monday)"},
     "weeks": {"type": "array", "items": {"type": "object", "properties": {
         "week": {"type": "integer"}, "focus": {"type": "string"},
         "days": {"type": "array", "items": {"type": "object", "properties": {
             "day": {"type": "string", "description": "Mon..Sun"}, "title": {"type": "string"},
             "items": {"type": "array", "items": {"type": "object", "properties": {
                 "exercise": {"type": "string"}, "sets": {"type": "string"}, "reps": {"type": "string"},
                 "target": {"type": "string", "description": "load / pace / time, e.g. '85% 3RM', '7:30/mi'"},
                 "rest": {"type": "string"}, "tempo": {"type": "string"},
                 "progression": {"type": "string", "description": "e.g. 'top of the range on all sets -> +5 lb'"},
                 "notes": {"type": "string"}}}},
             "why": {"type": "string", "description": "The purpose of the session in one sentence"}}}}}}}},
)
def set_training_plan(name: str, start: str, weeks: list) -> str:
    data = _data()
    data["plan"] = {"name": name, "start": start, "weeks": weeks, "saved": date.today().isoformat()}
    _save(data)
    return f"Saved '{name}' ({plural(len(weeks), 'week')} from {start}). It's in the phone fitness app. Today: {today_workout()}"


def _today_plan(data: dict, day: date | None = None) -> dict | None:
    plan = data.get("plan") or {}
    if not plan.get("weeks"):
        return None
    day = day or date.today()
    try:
        start = date.fromisoformat(plan["start"])
    except ValueError:
        return None
    idx = (day - start).days
    if idx < 0:
        return None
    wk = next((w for w in plan["weeks"] if int(w.get("week", 0)) == idx // 7 + 1), None)
    if not wk:
        return None
    name = day.strftime("%a").lower()
    rest = {"title": "Rest / recovery", "items": []}
    found = next((d for d in wk.get("days", []) if str(d.get("day", "")).lower().startswith(name)), rest)
    return {**found, "week": wk["week"], "focus": wk.get("focus", ""), **_session_parts(found)}


def _session_parts(day: dict) -> dict:
    """Every training day gets a warm-up and cool-down matched to the session (unless the plan gave its own)."""
    if not day.get("items"):
        return {"kind": "rest", "warmup": [], "cooldown": lib.cooldown_for("mixed")}  # rest day: a mobility flow
    kind = lib.session_kind(day)
    return {"kind": kind, "warmup": day.get("warmup") or lib.warmup_for(kind),
            "cooldown": day.get("cooldown") or lib.cooldown_for(kind)}


def _drill(key: str) -> str:
    return EXERCISES[key][0] if key in EXERCISES else key


@tool("Today's workout from the training plan, already adapted by the trainer's rules: target loads from what he "
      "logged, sets trimmed after a poor night or soreness, a missed session moved, each with its reason.")
def today_workout() -> str:
    data = _data()
    coached = None
    from tools import trainer
    today = _today_plan(data, trainer._today())
    try:
        coached = trainer.view(data=data)
    except Exception as error:                               # noqa: BLE001 - the plain plan still answers
        from core import diag
        diag.swallowed("fitness coach view", error)
    if coached and (coached["status"] == "ended" or coached["status"] == "before" and trainer._pca(data)):
        return trainer._no_session(coached)
    if coached and coached["status"] == "train" and today is not None:   # the day as the coach runs it
        today = {**today, "title": coached["title"], "why": coached.get("why", ""), **_session_parts(coached),
                 "items": [{**it, "target": (str(it.get("target", "")) + (f" at {it['load']:g} lb" if it.get("load") else "")).strip()}
                           for it in coached["items"]]}
    if today is None:
        return "No active plan for today: offer to build one (build_training_plan)."
    lines = [f"Week {today['week']} ({today['focus']}): {today['title']}" + (f". Why: {today['why']}" if today.get("why") else "")]
    if today["warmup"]:
        lines.append("Warm-up: " + "; ".join(f"{_drill(i['exercise'])} {i['dose']}" for ph in today["warmup"] for i in ph["items"]))
    for it in today["items"]:
        lines.append(f"- {it.get('exercise')}: {it.get('sets', '')}x{it.get('reps', '')} {it.get('target', '')}".strip())
    if today["cooldown"]:
        lines.append("Cool-down: " + "; ".join(f"{_drill(i['exercise'])} {i['dose']}" for i in today["cooldown"]))
    ready = _readiness(data)
    if ready:
        lines.append(ready)
    for change in (coached or {}).get("adjustments", []):
        lines.append("Changed: " + change["reason"])
    return "\n".join(lines)


@tool(
    "Log a finished workout (from what they say: 'did deadlifts 5x3 at 275, ran 3 miles in 24 minutes'). Keep "
    "items structured so progress charts work.",
    {"title": {"type": "string"}, "items": {"type": "array", "items": {"type": "object", "properties": {
        "exercise": {"type": "string"}, "sets": {"type": "integer"}, "reps": {"type": "integer"},
        "weight_lbs": {"type": "number"}, "time": {"type": "string", "description": "m:ss"},
        "distance_mi": {"type": "number"}, "notes": {"type": "string"}}}},
     "rpe": {"type": "integer", "description": "How hard 1-10"}, "date": {"type": "string"}},
    required=["title", "items"],
)
def log_workout(title: str, items: list, rpe: int = 0, date: str = "") -> str:
    from datetime import date as _date

    data = _data()
    data["workouts"].append({"date": date or _date.today().isoformat(), "title": title, "items": items, "rpe": rpe})
    data["workouts"] = data["workouts"][-1500:]
    _save(data)
    week = [w for w in data["workouts"] if w["date"] >= (_date.today() - timedelta(days=6)).isoformat()]
    return f"Logged '{title}'. {plural(len(week), 'workout')} in the last 7 days."


@tool("Training progress: workouts per week, deadlift trend, run paces, sleep and recovery, and how the AFT total "
      "moved, so you can coach on what to push or back off.")
def fitness_progress() -> str:
    data = _data()
    lines = []
    weeks: dict[str, int] = {}
    for w in data["workouts"]:
        d = date.fromisoformat(w["date"])
        key = (d - timedelta(days=d.weekday())).isoformat()
        weeks[key] = weeks.get(key, 0) + 1
    if weeks:
        lines.append("Workouts per week (recent): " + ", ".join(f"{k}: {v}" for k, v in sorted(weeks.items())[-6:]))
    best_dl = max((it.get("weight_lbs") or 0 for w in data["workouts"] for it in w["items"]
                   if "deadlift" in str(it.get("exercise", "")).lower()), default=0)
    if best_dl:
        lines.append(f"Heaviest logged deadlift: {best_dl:.0f} lbs.")
    g = garmin_data(data)
    runs = [r for r in g.get("runs", []) if r.get("pace_s_per_mi")][-10:]
    if runs:
        avg = sum(r["pace_s_per_mi"] for r in runs) / len(runs)
        lines.append(f"Last {len(runs)} runs: {sum(r['miles'] for r in runs):.1f} mi, average pace {_mmss(avg)}/mi.")
    if g.get("race_predictions"):
        rp = g["race_predictions"]
        lines.append(f"Garmin predicts 5K {rp.get('5k', '?')}; estimated 2-mile about {rp.get('2mile_est', '?')}.")
    if g.get("vo2max"):
        lines.append(f"VO2 max {g['vo2max']}.")
    ready = _readiness(data)
    if ready:
        lines.append(ready)
    if data["aft_tests"]:
        lines.append(aft_status())
    try:
        from tools import trainer
        coached = trainer.progress_text()                    # strength per lift and weekly sets per muscle, from set logs
        if coached:
            lines.append(coached)
    except Exception as error:                               # noqa: BLE001 - the older numbers still answer
        from core import diag
        diag.swallowed("fitness coach progress", error)
    return "\n".join(lines) or "Nothing logged yet."


# -- Garmin --------------------------------------------------------------------------------------
CONNECT_HINT = ("Connect your Garmin through your phone: install Jarvis Link (Captain America page, Recovery, the Garmin "
                "card), turn on Health Connect in the Garmin Connect app, then tap 'Allow Jarvis to read health data' in "
                "Jarvis Link (Android 14 or newer).")


def garmin_data(data: dict) -> dict:
    """data["garmin"] merged with what his phone sent through Health Connect (tools/pca_health): same shape."""
    try:
        from tools import pca_health
        return pca_health.merged_garmin(data.get("garmin"))
    except Exception as error:                               # noqa: BLE001 - the old data still answers
        from core import diag
        diag.swallowed("fitness health connect merge", error)
        return data.get("garmin") or {}


def token_dir():
    return storage.user_dir() / "garmin"


def _garmin():
    from garminconnect import Garmin

    tokens = token_dir()
    if not tokens.exists() or not any(tokens.iterdir()):
        raise RuntimeError("Garmin isn't connected yet. " + CONNECT_HINT +
                           " (The old direct login, garmin-login.bat on the PC, is blocked by Garmin's portal for most people.)")
    g = Garmin()
    g.login(tokenstore=str(tokens))
    return g


@tool(
    "Log today's morning check-in when they tell you how they slept or feel (or read you their Garmin numbers): "
    "sleep hours, energy and soreness 1-5, optional Garmin training readiness 0-100 and resting heart rate. It "
    "decides whether today's session runs as planned or is cut back, and shows in the phone fitness app.",
    {"sleep_hours": {"type": "number"}, "energy": {"type": "integer", "description": "1 drained - 5 great"},
     "soreness": {"type": "integer", "description": "1 none - 5 very sore"},
     "readiness": {"type": "integer", "description": "Garmin training readiness 0-100, if they have it"},
     "resting_hr": {"type": "integer"}, "date": {"type": "string", "description": "YYYY-MM-DD, default today"}},
    required=[],
)
def log_checkin(sleep_hours: float = 0, energy: int = 0, soreness: int = 0, readiness: int | None = None,
                resting_hr: int | None = None, date: str = "") -> str:
    from datetime import date as _date

    day = date or _date.today().isoformat()
    data = _data()
    checks = data.setdefault("checkins", {})
    entry = {**checks.get(day, {}), "date": day}

    def clamp(v, lo, hi):
        return max(lo, min(hi, v))
    if sleep_hours:
        entry["sleep_hours"] = round(clamp(float(sleep_hours), 0, 16), 1)
    if energy:
        entry["energy"] = clamp(int(energy), 1, 5)
    if soreness:
        entry["soreness"] = clamp(int(soreness), 1, 5)
    if readiness not in (None, ""):
        entry["readiness"] = clamp(int(readiness), 0, 100)
    if resting_hr:
        entry["resting_hr"] = clamp(int(resting_hr), 30, 140)
    checks[day] = entry
    data["checkins"] = dict(sorted(checks.items())[-120:])
    _save(data)
    return "Check-in saved. " + (_readiness(data) or "")


def _checkin_readiness(c: dict) -> tuple[list[str], bool]:
    bits = []
    if c.get("sleep_hours"):
        bits.append(f"slept {c['sleep_hours']:.1f} h")
    if c.get("readiness") is not None:
        bits.append(f"readiness {c['readiness']}")
    if c.get("energy"):
        bits.append(f"energy {c['energy']}/5")
    if c.get("soreness"):
        bits.append(f"soreness {c['soreness']}/5")
    if c.get("resting_hr"):
        bits.append(f"resting HR {c['resting_hr']}")
    low = (c.get("sleep_hours") or 8) < 6 or (c.get("readiness") if c.get("readiness") is not None else 100) < 40 \
        or (c.get("energy") or 5) <= 2 or (c.get("soreness") or 1) >= 4
    return bits, low


def _effective_today(data: dict) -> dict | None:
    try:
        from tools import pca_health
        return pca_health.effective_checkin(data)            # today, by the same clock as the health data
    except Exception as error:                               # noqa: BLE001 - his own check-in still answers
        from core import diag
        diag.swallowed("fitness effective checkin", error)
        return (data.get("checkins") or {}).get(date.today().isoformat())


def _readiness(data: dict) -> str:
    today = _effective_today(data)
    if today and today.get("source") == "garmin":            # no check-in today: last night measured through Health Connect
        bits = [f"slept {today['sleep_hours']:.1f} h"]
        if today.get("rhr_above"):
            bits.append(f"resting heart rate {today['resting_hr']}, {today['rhr_above']} above your usual")
        low = today["sleep_hours"] < 6 or bool(today.get("rhr_above"))
        g = garmin_data(data)                                # Garmin's own signals, only where the old sync has them
        hrv, daily = (g.get("hrv") or [{}])[-1], (g.get("daily") or [{}])[-1]
        if hrv.get("status"):
            bits.append(f"HRV {str(hrv['status']).lower()}")
        if daily.get("readiness") is not None:
            bits.append(f"training readiness {daily['readiness']}")
        if daily.get("body_battery") is not None:
            bits.append(f"body battery {daily['body_battery']}")
        low = low or str(hrv.get("status", "")).upper() in ("LOW", "POOR", "UNBALANCED") or \
            (daily.get("readiness") if daily.get("readiness") is not None else 100) < 40
        advice = " Recovery looks low: keep today easy (cut volume about 30%, no max efforts)." if low else ""
        return "From your Garmin: " + ", ".join(bits) + "." + advice
    if today:  # this morning's check-in beats older Garmin numbers
        bits, low = _checkin_readiness(today)
        if bits:
            advice = " Recovery looks low: keep today easy (cut volume about 30%, no max efforts)." if low else ""
            return "Check-in: " + ", ".join(bits) + "." + advice
    g = garmin_data(data)
    sleep = (g.get("sleep") or [{}])[-1]
    hrv = (g.get("hrv") or [{}])[-1]
    daily = (g.get("daily") or [{}])[-1]
    bits = []
    if sleep.get("hours"):
        bits.append(f"slept {sleep['hours']:.1f} h" + (f" (score {sleep['score']})" if sleep.get("score") else ""))
    if hrv.get("status"):
        bits.append(f"HRV {str(hrv['status']).lower()}")
    if daily.get("readiness") is not None:
        bits.append(f"training readiness {daily['readiness']}")
    if daily.get("body_battery") is not None:
        bits.append(f"body battery {daily['body_battery']}")
    if not bits:
        return ""
    low = (sleep.get("hours") or 8) < 6 or str(hrv.get("status", "")).upper() in ("LOW", "POOR", "UNBALANCED") or \
        (daily.get("readiness") if daily.get("readiness") is not None else 100) < 40
    advice = " Recovery looks low: keep today easy (cut volume about 30%, no max efforts)." if low else ""
    return "Garmin: " + ", ".join(bits) + "." + advice


@tool("Pull the latest data from Garmin (runs, sleep, HRV, resting heart rate, body battery, training readiness, "
      "VO2 max, race predictions) for the last few days. Runs automatically every morning.",
      {"days": {"type": "integer", "description": "How many days back (default 7)"}}, required=[], slow=True)
def garmin_sync(days: int = 7) -> str:
    try:
        g = _garmin()
    except RuntimeError as e:
        return str(e)                       # not connected yet — the message already says how to fix it
    except ImportError:
        return "Garmin sync needs the 'garminconnect' package installed in Jarvis's Python environment."
    except Exception as e:
        if "Authentication" in type(e).__name__ or "Login" in type(e).__name__:
            return ("Garmin's saved login has expired. " + CONNECT_HINT +
                    " (Or sign in again with garmin-login.bat on the PC, if Garmin's portal lets it through.)")
        if "TooManyRequests" in type(e).__name__:
            return "Garmin is rate-limiting sign-ins right now — try the sync again in a few minutes."
        raise                               # anything else: network outages are translated by the registry
    data = _data()
    gd = data.setdefault("garmin", {})
    end = date.today()
    start = end - timedelta(days=max(1, min(int(days or 7), 60)))
    runs = {r["id"]: r for r in gd.get("runs", [])}
    for a in g.get_activities_by_date(start.isoformat(), end.isoformat()) or []:
        kind = (a.get("activityType") or {}).get("typeKey", "")
        miles = (a.get("distance") or 0) / 1609.344
        secs = a.get("duration") or 0
        runs[a.get("activityId")] = {
            "id": a.get("activityId"), "date": str(a.get("startTimeLocal", ""))[:10], "name": a.get("activityName"),
            "type": kind, "miles": round(miles, 2), "seconds": int(secs),
            "pace_s_per_mi": int(secs / miles) if miles > 0.1 and "run" in kind else None,
            "avg_hr": a.get("averageHR"), "max_hr": a.get("maxHR")}
    gd["runs"] = sorted(runs.values(), key=lambda r: r["date"])[-400:]
    sleep = {s["date"]: s for s in gd.get("sleep", [])}
    hrv = {h["date"]: h for h in gd.get("hrv", [])}
    daily = {d["date"]: d for d in gd.get("daily", [])}
    day = start
    while day <= end:
        ds = day.isoformat()
        try:
            dto = (g.get_sleep_data(ds) or {}).get("dailySleepDTO", {}) or {}
            if dto.get("sleepTimeSeconds"):
                sleep[ds] = {"date": ds, "hours": round(dto["sleepTimeSeconds"] / 3600, 2),
                             "score": ((dto.get("sleepScores") or {}).get("overall") or {}).get("value"),
                             "deep_h": round((dto.get("deepSleepSeconds") or 0) / 3600, 2),
                             "rem_h": round((dto.get("remSleepSeconds") or 0) / 3600, 2)}
        except Exception:
            pass
        try:
            summ = (g.get_hrv_data(ds) or {}).get("hrvSummary") or {}
            if summ:
                hrv[ds] = {"date": ds, "last_night": summ.get("lastNightAvg"), "weekly": summ.get("weeklyAvg"),
                           "status": summ.get("status")}
        except Exception:
            pass
        try:
            stats = g.get_stats(ds) or {}
            rd = g.get_training_readiness(ds) or []
            rd = rd[0] if isinstance(rd, list) and rd else (rd if isinstance(rd, dict) else {})
            daily[ds] = {"date": ds, "resting_hr": stats.get("restingHeartRate"),
                         "body_battery": stats.get("bodyBatteryMostRecentValue"), "steps": stats.get("totalSteps"),
                         "readiness": rd.get("score")}
        except Exception:
            pass
        day += timedelta(days=1)
    gd["sleep"] = sorted(sleep.values(), key=lambda x: x["date"])[-400:]
    gd["hrv"] = sorted(hrv.values(), key=lambda x: x["date"])[-400:]
    gd["daily"] = sorted(daily.values(), key=lambda x: x["date"])[-400:]
    try:
        mm = g.get_max_metrics(end.isoformat()) or []
        mm = mm[0] if isinstance(mm, list) and mm else (mm if isinstance(mm, dict) else {})
        vo2 = (mm.get("generic") or {}).get("vo2MaxPreciseValue")
        if vo2:
            gd["vo2max"] = vo2
    except Exception:
        pass
    try:
        rp = g.get_race_predictions() or {}
        five = rp.get("time5K") if isinstance(rp, dict) else None
        if five:  # Riegel's formula: T2 = T1 * (D2/D1)^1.06, 5 km -> 2 miles (3.2187 km)
            gd["race_predictions"] = {"5k": _mmss(five), "2mile_est": _mmss(five * (3.2187 / 5) ** 1.06)}
    except Exception:
        pass
    gd["synced"] = datetime.now().isoformat(timespec="minutes")
    _save(data)
    recent = len([r for r in gd["runs"] if r["date"] >= start.isoformat()])
    return f"Synced Garmin: {recent} activities, {len(gd['sleep'])} nights of sleep on file. " + _readiness(data)


def app_data() -> dict:
    """Everything the phone fitness app shows (core.appserver calls it as the signed-in person)."""
    data = _data()
    coach = None
    try:
        from tools import trainer
        coach = trainer.page_data()
    except Exception as error:                               # noqa: BLE001 - the page still shows the plan
        from core import diag
        diag.swallowed("fitness coach page", error)
    return {"coach": coach, "profile": data["profile"], "aft_tests": data["aft_tests"], "plan": data.get("plan", {}),
            "today": _today_plan(data), "workouts": data["workouts"][-120:], "garmin": garmin_data(data),
            "readiness": _readiness(data),
            "exercises": {k: {"name": v[0], "event": v[1], "cues": v[2], **lib.DETAILS.get(k, {})}
                          for k, v in EXERCISES.items()},
            "health": lib.health_notes(data["profile"]),
            "checkin": (data.get("checkins") or {}).get(date.today().isoformat()),
            "checkins": list((data.get("checkins") or {}).values())[-30:],
            "achievements": _achievements(data)}


def _achievements(data: dict) -> dict:
    from tools import life
    return life.achievements(data)
