"""Project Captain America: the rules that run the authored program (core/pca_program.py) day by day.

Pure functions: no storage, no clock, no model, no network. tools/pca.py feeds them his answers, the events he logged
and today's date; every number here comes from code, and every change to a day comes back with one plain sentence.

  QUESTIONS, assess(answers)            onboarding: what is missing, what blocks the start, what the plan will change
  baselines(tests, today), paces(), aft_points()
                                        tested values only (in-program tests); paces from a fresh two-mile only
  resolve_day(day, baselines, flags)    shares of a tested max become real numbers, run segments get pace ranges
  run_review(), next_run_adjust(), run_cap()
                                        the running rules (80% rule, interval repeats, 110% single-run cap)
  adapt(view_day, context)              readiness, pain, outside loads, moved sessions, long gaps, override
  progress(...), next_block(tests)      what he logged and tested, never a projection

Design rule: nothing is calculated from old results. The historical numbers in HISTORICAL are shown as "previously
logged, not confirmed" and are never read by any function that prescribes."""
import copy
import math
import re
from datetime import date, timedelta

from core import coach_engine as coach
from core import pca_program as program
from core.words import plural

# ---- onboarding ------------------------------------------------------------------------------------------------------
EQUIPMENT = (("trap_bar", "Trap (hex) bar"), ("barbell_rack", "Barbell and rack"), ("dumbbells", "Dumbbells"), ("cables", "Cable station"),
             ("pullup_bar", "Pull-up bar"), ("dip_bars", "Dip bars"), ("sled", "Sled (90 lb) with straps"),
             ("kettlebells", "Two 40 lb kettlebells"), ("track", "Track or measured course"), ("box", "Plyo box"))
GOALS = (("aft_score", "AFT score"), ("two_mile", "Two-mile time"), ("strength", "Strength"), ("physique", "Physique"),
         ("healthy", "Staying injury-free"))
# Music services by id (the page and the audio prefs use these ids; the labels are for display).
# Metrolist is an open-source Android YouTube Music client; it opens music.youtube.com and youtube.com links itself.
MUSIC_SERVICES = (("none", "No music"), ("spotify", "Spotify"), ("youtube_music", "YouTube Music"), ("metrolist", "Metrolist"),
                  ("apple_music", "Apple Music"), ("local", "Local files"))
# The onboarding screens of the page, in order: every question carries one of these ids as "group".
GROUPS = (("body", "Body"), ("schedule", "Schedule and equipment"), ("now", "Running and lifting now"), ("health", "Health"),
          ("goals", "Goals and music"))

QUESTIONS = [
    {"id": "height", "label": "Height", "kind": "text", "options": [], "required": True, "profile": "height",
     "help": "Like 6'2\" or 188 cm."},
    {"id": "weight_lbs", "label": "Body weight (lb)", "kind": "number", "options": [], "required": True, "min": 80, "max": 400,
     "profile": "weight_lbs", "help": "Morning weight is fine."},
    {"id": "age", "label": "Age", "kind": "number", "options": [], "required": True, "min": 17, "max": 70, "profile": "age",
     "help": "Sets the AFT score column."},
    {"id": "days_per_week", "label": "Training days a week", "kind": "choice", "options": [5, 6], "required": True,
     "help": "Both carry the same core work. The 6th day is a 45-minute upper-body day, recommended for the physique goal."},
    {"id": "session_minutes", "label": "Minutes per session", "kind": "number", "options": [], "required": True, "min": 30, "max": 120,
     "help": "The program needs 60; most sessions run 60-70 minutes with the warm-up."},
    {"id": "equipment", "label": "Equipment you can use", "kind": "choice", "multiple": True, "options": [e[0] for e in EQUIPMENT],
     "labels": dict(EQUIPMENT), "required": True, "help": "Tick everything you have. Anything missing gets a named substitute."},
    {"id": "runs_per_week", "label": "Runs a week right now", "kind": "number", "options": [], "required": True, "min": 0, "max": 14,
     "help": "Count unit PT runs too."},
    {"id": "miles_per_week", "label": "Miles a week right now", "kind": "number", "options": [], "required": True, "min": 0, "max": 100,
     "help": "A rough number is fine."},
    {"id": "weeks_since_last_run", "label": "Weeks since your last run", "kind": "number", "options": [], "required": True, "min": 0,
     "max": 520, "allow_unknown": True, "help": "0 if you ran this week. Say 'unknown' if you are not sure."},
    {"id": "weeks_since_last_heavy_lift", "label": "Weeks since your last heavy lifting", "kind": "number", "options": [], "required": True,
     "min": 0, "max": 520, "allow_unknown": True, "help": "0 if you lifted heavy this week."},
    {"id": "pain", "label": "Current pain or injury", "kind": "text", "options": [], "required": False,
     "help": "Where and what, in your words. Leave empty if nothing hurts."},
    {"id": "pain_level", "label": "Pain level today (0-10)", "kind": "number", "options": [], "required": True, "min": 0, "max": 10,
     "help": "0 = none. 4 or more stops the start until a medic or physical therapist has looked at it."},
    {"id": "medical_restriction", "label": "Active medical profile or restriction", "kind": "bool", "options": [], "required": True,
     "help": "A temporary or permanent profile, or a provider's limit on training."},
    {"id": "medical_note", "label": "What the profile says", "kind": "text", "options": [], "required": False, "help": "Optional."},
    {"id": "provider_cleared", "label": "Cleared by a provider for unrestricted training", "kind": "bool", "options": [], "required": False,
     "help": "Needed when you have an active profile or restriction."},
    {"id": "sickle_cell", "label": "Sickle cell trait status known", "kind": "choice", "options": ["yes", "no", "unknown"], "required": True,
     "health_note": "Sickle cell trait", "help": "Your status is in your medical record. See the health note on the fitness page for why it matters."},
    {"id": "sleep_hours", "label": "Average sleep (hours)", "kind": "number", "options": [], "required": True, "min": 3, "max": 12,
     "help": "A normal week, not your best night."},
    {"id": "unit_pt_days", "label": "Unit PT days a week", "kind": "number", "options": [], "required": True, "min": 0, "max": 7,
     "help": "0 if you do not have unit PT."},
    {"id": "unit_pt_kind", "label": "What unit PT usually is", "kind": "choice", "options": ["run-heavy", "strength", "mixed", "none"],
     "required": True, "help": "Hard unit runs replace the week's hard run, so the plan needs to know."},
    {"id": "next_aft_date", "label": "Next record AFT date", "kind": "date", "options": [], "required": False, "profile": "test_date",
     "help": "Optional. YYYY-MM-DD."},
    {"id": "goals", "label": "Main goals, most important first", "kind": "choice", "multiple": True, "ordered": True,
     "options": [g[0] for g in GOALS], "labels": dict(GOALS), "required": False, "help": "Order them; the first wins a tie."},
    {"id": "music_prefs", "label": "Music you train to", "kind": "text", "options": [], "required": False, "help": "Artists, genres or playlists."},
    {"id": "music_service", "label": "Music service", "kind": "choice", "options": [m[0] for m in MUSIC_SERVICES],
     "labels": dict(MUSIC_SERVICES), "required": False, "help": ""},
]
_GROUP_OF = {"height": "body", "weight_lbs": "body", "age": "body",
             "days_per_week": "schedule", "session_minutes": "schedule", "equipment": "schedule", "unit_pt_days": "schedule",
             "unit_pt_kind": "schedule", "next_aft_date": "schedule",
             "runs_per_week": "now", "miles_per_week": "now", "weeks_since_last_run": "now", "weeks_since_last_heavy_lift": "now",
             "pain": "health", "pain_level": "health", "medical_restriction": "health", "medical_note": "health",
             "provider_cleared": "health", "sickle_cell": "health", "sleep_hours": "health",
             "goals": "goals", "music_prefs": "goals", "music_service": "goals"}
for _q in QUESTIONS:
    _q["group"] = _GROUP_OF[_q["id"]]
QUESTION = {q["id"]: q for q in QUESTIONS}

# Previously logged numbers, kept for reference ONLY. No function that prescribes reads these.
HISTORICAL = {
    "label": "Previously logged, date unknown, not confirmed. Shown for reference only; nothing in the program is calculated from these.",
    "values": {"mdl": "3-rep max deadlift about 340 lb", "hrp": "hand-release push-ups 41-43", "sdc": "sprint-drag-carry 1:43-1:45",
               "plk": "plank 1:50-1:52", "run": "two-mile 18:50 (earlier 16:37)"},
}

_YES = ("yes", "y", "true", "1", "on")
_NO = ("no", "n", "false", "0", "off")


def _unknown(v) -> bool:
    return str(v).strip().lower() in ("unknown", "not sure", "?", "idk", "dont know", "don't know")


def clean_answer(qid: str, value):
    """(value, error sentence) for one answer. Numbers are range-checked; a checklist becomes a list of ids."""
    q = QUESTION.get(qid)
    if not q:
        return None, "I don't have that question."
    kind = q["kind"]
    if value is None or (isinstance(value, str) and not value.strip() and kind != "text"):
        return None, f"{q['label']}: I need an answer."
    if kind == "number":
        if q.get("allow_unknown") and _unknown(value):
            return "unknown", ""
        try:
            n = float(str(value).strip())
        except ValueError:
            return None, f"{q['label']}: that should be a number" + (", or 'unknown'." if q.get("allow_unknown") else ".")
        if not q.get("min", -1e9) <= n <= q.get("max", 1e9):
            return None, f"{q['label']} should be between {q['min']:g} and {q['max']:g}."
        return (int(n) if n == int(n) else round(n, 1)), ""
    if kind == "bool":
        if isinstance(value, bool):
            return value, ""
        said = str(value).strip().lower()
        if said in _YES or said in _NO:
            return said in _YES, ""
        return None, f"{q['label']}: yes or no."
    if kind == "choice":
        if q.get("multiple"):
            items = value if isinstance(value, list) else [v for v in str(value).replace(";", ",").split(",")]
            items = [str(v).strip() for v in items if str(v).strip()]
            bad = [v for v in items if v not in q["options"]]
            if bad:
                return None, f"{q['label']}: I don't know {', '.join(bad)}. Choose from {', '.join(q['options'])}."
            return list(dict.fromkeys(items)), ""
        pick = next((o for o in q["options"] if str(o).lower() == str(value).strip().lower()), None)
        if pick is None:
            return None, f"{q['label']}: choose {', '.join(str(o) for o in q['options'])}."
        return pick, ""
    if kind == "date":
        try:
            return date.fromisoformat(str(value).strip()).isoformat(), ""
        except ValueError:
            return None, f"{q['label']}: a date like 2027-03-15."
    return str(value).strip()[:300], ""


def _answered(answers: dict, qid: str) -> bool:
    v = (answers or {}).get(qid)
    return v not in (None, "") and v != []


def _n(answers, qid, default=None):
    v = (answers or {}).get(qid)
    if v in (None, "", "unknown"):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# equipment each exercise needs (any one of); a key not listed needs nothing on the checklist
NEEDS = {"deadlift": ("trap_bar",), "back_squat": ("barbell_rack",), "rdl": ("barbell_rack", "dumbbells"), "bench_press": ("barbell_rack",),
         "incline_press": ("dumbbells",), "chest_supported_row": ("dumbbells",), "lateral_raise": ("dumbbells", "cables"), "curl": ("dumbbells",),
         "overhead_triceps": ("cables", "dumbbells"), "pull_up": ("pullup_bar", "cables"), "hanging_leg_raise": ("pullup_bar",),
         "dip": ("dip_bars",), "cable_crossover": ("cables",), "face_pull": ("cables",), "pushdown": ("cables",), "pallof_press": ("cables",),
         "box_jump": ("box",), "farmer_carry": ("dumbbells", "kettlebells", "trap_bar"), "overhead_press": ("barbell_rack", "dumbbells"),
         "row": ("barbell_rack", "dumbbells"), "hip_thrust": ("barbell_rack", "dumbbells")}
# words in an alternative's name -> the equipment it needs (any one of)
_WORD_GEAR = (("trap", ("trap_bar",)), ("barbell", ("barbell_rack",)), ("front squat", ("barbell_rack",)), ("leg press", ("cables",)),
              ("romanian", ("barbell_rack", "dumbbells")), ("dumbbell", ("dumbbells",)), ("goblet", ("dumbbells", "kettlebells")),
              ("cable", ("cables",)), ("pulldown", ("cables",)), ("machine", ("cables",)), ("kettlebell", ("kettlebells", "dumbbells")),
              ("sled", ("sled",)), ("pull-up", ("pullup_bar",)), ("dip", ("dip_bars",)), ("box jump", ("box",)), ("bench press", ("barbell_rack",)))
# checklist ids -> coach_engine gear tags (for coach.substitute when no listed alternative fits)
_COACH_GEAR = {"trap_bar": ("trap_bar",), "barbell_rack": ("barbell", "bench"), "dumbbells": ("dumbbell", "bench"),
               "cables": ("cable", "machine"), "pullup_bar": ("pullup_bar",), "dip_bars": ("dip_bar",), "sled": ("sled",),
               "kettlebells": ("kettlebell",)}


def _alt_ok(alt: str, have: set) -> bool:
    said = alt.lower()
    return all(any(g in have for g in gears) for word, gears in _WORD_GEAR if word in said)


def substitution(key: str, have: set) -> dict | None:
    """What replaces an exercise he has no equipment for: the program's own alternatives first (in its order), then
    coach.substitute. None = he can do it as written. {"name": "", ...} = nothing fits and the item is skipped."""
    if key == "sdc":
        alts = program.EX["sdc"][2]
        notes = [alts[n] for n, need in enumerate(("sled", "kettlebells")) if need not in have]
        return {"name": "", "keep": True, "reason": "Sprint-drag-carry without the full kit: " + "; ".join(notes) + "."} if notes else None
    needs = NEEDS.get(key)
    if not needs or any(g in have for g in needs):
        return None
    for alt in program.EX.get(key, ("", "", []))[2]:
        if _alt_ok(alt, have):
            name = alt.split(" (")[0]
            return {"name": name, "reason": f"No {'/'.join(dict(EQUIPMENT)[g].lower() for g in needs)}: {name.lower()} replaces it, same sets, reps and effort."}
    tags = {"bodyweight", "band"} | {t for g in have for t in _COACH_GEAR.get(g, ())}
    found = coach.substitute(key, tags)
    if found:
        return {"name": coach.TAGS[found[0]]["name"], "reason": found[1]}
    return {"name": "", "reason": f"No equipment for {key.replace('_', ' ')} and no good substitute: it is skipped."}


def assess(answers: dict) -> dict:
    """Is onboarding complete, does anything stop the start, and what will the plan change because of the answers?
    -> {complete, missing, blocking, warnings, provisional_reasons, build_args, flags}"""
    a = answers or {}
    missing = [q["id"] for q in QUESTIONS if q["required"] and not _answered(a, q["id"])]
    if a.get("medical_restriction") is True and not _answered(a, "provider_cleared"):
        missing.append("provider_cleared")
    blocking, warnings, provisional = [], [], []
    pain = _n(a, "pain_level", 0)
    if a.get("medical_restriction") is True and a.get("provider_cleared") is not True:
        blocking.append("You have an active medical profile or restriction and no provider clearance for unrestricted training: get "
                        "cleared first, then the program can start.")
    if pain >= 4:
        blocking.append(f"Pain at {pain:g} out of 10 needs a medic or physical therapist to look at it before a 12-week program starts.")
    elif pain >= 1:
        warnings.append(f"Pain at {pain:g} out of 10: you can train, but tell me if it changes how you move and the plan works around it.")
    since_run = a.get("weeks_since_last_run")
    run_walk = since_run in (None, "", "unknown") or _n(a, "weeks_since_last_run", 99) > 4
    running_now = _n(a, "runs_per_week", 0) >= 3 and _n(a, "weeks_since_last_run", 99) == 0 and pain == 0
    if run_walk:
        provisional.append("You have not run for more than four weeks (or are not sure), so the easy runs in weeks 1-2 are run/walk: "
                           "2 minutes jogging, 1 minute walking.")
    if since_run not in (None, "") and not run_walk and not running_now:
        provisional.append("You are not running three times a week yet, so the two-mile time trial waits until week 2.")
    lift_gap = a.get("weeks_since_last_heavy_lift")
    if lift_gap in ("unknown",) or _n(a, "weeks_since_last_heavy_lift", 0) > 4:
        provisional.append("It has been a while since heavy lifting: week 1 finds your loads from reps in reserve, starting light.")
    provisional.append("Run paces and push-up and plank targets come from the baseline tests in weeks 1-2, not from older results.")
    minutes = _n(a, "session_minutes", 60)
    if minutes < 60:
        warnings.append(f"The program is written for 60-minute sessions; at {plural(minutes, 'minute')} the lowest-priority work is cut first.")
    if _n(a, "sleep_hours", 8) < 6:
        warnings.append("Under 6 hours of sleep on average: hard days will be cut back on the mornings you report short sleep.")
    if a.get("sickle_cell") == "unknown":
        warnings.append("Sickle cell trait status unknown: check your medical record. Build intensity gradually, hydrate, and stop "
                        "at once for unusual cramping, weakness or breathlessness.")
    have = set(a.get("equipment") or []) if isinstance(a.get("equipment"), list) else set()
    subs = {}
    if _answered(a, "equipment"):
        for key in program.EX:
            sub = substitution(key, have)
            if sub:
                subs[key] = sub
                warnings.append(sub["reason"])
        if "track" not in have:
            warnings.append("No track or measured course: run the distance repeats by GPS watch or on a treadmill; the two-mile trial "
                            "needs a measured two miles.")
    if a.get("unit_pt_kind") == "run-heavy":
        warnings.append("Run-heavy unit PT: log it after each session; a hard unit run replaces that week's hard run.")
    build_args = {"days_per_week": int(_n(a, "days_per_week", 5)), "session_minutes": int(max(60, minutes)), "running_now": running_now}
    return {"complete": not missing, "missing": missing, "blocking": blocking, "warnings": warnings, "provisional_reasons": provisional,
            "build_args": build_args, "flags": {"run_walk": run_walk, "subs": subs, "no_track": _answered(a, "equipment") and "track" not in have}}


# ---- tested values ---------------------------------------------------------------------------------------------------
EVENTS = ("mdl", "hrp", "sdc", "plk", "run")
EVENT_NAMES = {"mdl": "3-rep max deadlift", "hrp": "hand-release push-ups", "sdc": "sprint-drag-carry", "plk": "plank", "run": "two-mile run"}
FRESH_DAYS = {"run": 42, "hrp": 56, "plk": 56, "sdc": 56, "mdl": 56}
LOWER_IS_BETTER = ("sdc", "run")

# Official AFT score table, male 22-26 (the same "M | C" column as the combat standard), from the Army's
# AFT Scoring Scales effective 1 June 2025: https://www.army.mil/e2/downloads/rv7/aft/AFT_Scoring_Scales_250601.pdf
# transcribed in docs/pca/research-army.md (Table A and the extra MDL and 2MR rows). Only the listed rows are here; points
# between rows are never interpolated. Times are seconds.
AFT_TABLE = {
    "mdl": [(100, 350), (99, 340), (97, 330), (95, 320), (93, 310), (91, 300), (89, 290), (87, 280), (85, 270), (83, 260), (81, 250),
            (79, 240), (77, 230), (75, 220), (73, 210), (71, 200), (70, 190), (67, 180), (65, 170), (63, 160), (60, 150), (50, 130)],
    "hrp": [(100, 61), (95, 53), (90, 48), (85, 42), (80, 37), (75, 31), (70, 26), (65, 21), (60, 14), (50, 9)],
    "sdc": [(100, 90), (95, 97), (90, 103), (85, 108), (80, 113), (75, 119), (70, 125), (65, 134), (60, 151), (50, 161)],
    "plk": [(100, 215), (95, 199), (90, 183), (85, 166), (80, 150), (75, 134), (70, 118), (65, 101), (60, 85), (50, 80)],
    "run": [(100, 805), (95, 881), (90, 938), (85, 989), (81, 1028), (79, 1038), (75, 1075), (72, 1101), (70, 1103), (65, 1147),
            (60, 1185), (50, 1213)],
}


def mmss(sec) -> str:
    if sec in (None, ""):
        return ""
    sec = int(round(float(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def parse_value(event: str, value) -> tuple:
    """A test result as he says it -> (number, error). mdl in lb, hrp in reps, the rest m:ss (or seconds)."""
    if event not in EVENTS:
        return None, "Which test: deadlift (mdl), push-ups (hrp), sprint-drag-carry (sdc), plank (plk) or two-mile run (run)?"
    said = str(value if value is not None else "").strip().lower().replace("lbs", "").replace("lb", "").replace("reps", "").strip()
    try:
        if event in ("mdl", "hrp"):
            n = float(said)
        elif ":" in said:
            m, s = said.split(":", 1)
            if not (m.strip().isdigit() and re.fullmatch(r"\d{1,2}(?:\.\d+)?", s.strip()) and float(s) < 60):
                raise ValueError(said)                        # seconds are 0-59, nothing negative
            n = int(m) * 60 + float(s)
        else:
            n = float(said)
        if not math.isfinite(n):
            raise ValueError(said)
    except ValueError:
        return None, f"I couldn't read that {EVENT_NAMES[event]} result. " + ("Say it in pounds, like 315." if event == "mdl" else
                                                                             "Say the number of reps." if event == "hrp" else "Say it as minutes and seconds, like 1:52.")
    low, high = {"mdl": (95, 700), "hrp": (1, 150), "sdc": (60, 360), "plk": (10, 600), "run": (540, 2400)}[event]
    if not low <= n <= high:
        shown = (lambda x: f"{x:g}") if event in ("mdl", "hrp") else mmss
        return None, f"A {EVENT_NAMES[event]} of {shown(n)} is outside what I can record ({shown(low)} to {shown(high)}). Check the number."
    return (int(n) if event in ("mdl", "hrp") else int(round(n))), ""


def value_text(event: str, value) -> str:
    if value is None:
        return ""
    return f"{value:g} lb" if event == "mdl" else plural(int(value), "rep") if event == "hrp" else mmss(value)


def aft_points(event: str, value, table: dict | None = None) -> int | None:
    """The points of the highest listed row he meets or beats ('at least N points'); None below the lowest listed row.
    Never interpolated between rows."""
    rows = (table or AFT_TABLE).get(event) or []
    if value is None:
        return None
    for points, need in rows:
        if (value <= need) if event in LOWER_IS_BETTER else (value >= need):
            return points
    return None


def baselines(tests: list, today: date) -> dict:
    """{event: {value, date, age_days, fresh} or None} from the newest in-program result of each event.
    tests: [{event, value, date}] (the caller passes only results logged inside the program)."""
    out = {}
    for ev in EVENTS:
        rows = sorted((t for t in tests or [] if t.get("event") == ev and t.get("value") is not None), key=lambda t: str(t.get("date")))
        if not rows:
            out[ev] = None
            continue
        last = rows[-1]
        age = (today - date.fromisoformat(str(last["date"]))).days
        out[ev] = {"value": last["value"], "date": str(last["date"]), "age_days": age, "fresh": 0 <= age <= FRESH_DAYS[ev]}
    return out


def paces(two_mile_seconds) -> dict:
    """{zone: (fast, slow) seconds per mile} from a two-mile time and the zone factors in pca_program.ZONES."""
    per_mile = float(two_mile_seconds) / 2
    return {z: (round(per_mile * v["factor"][0]), round(per_mile * v["factor"][1])) for z, v in program.ZONES.items()}


def pace_text(pair) -> str:
    return f"{mmss(pair[0])}-{mmss(pair[1])} /mi"


def split_text(pair, metres) -> str:
    return f"{metres:g} m in {mmss(pair[0] * metres / 1609.34)}-{mmss(pair[1] * metres / 1609.34)}"


# ---- a day for display and guidance ----------------------------------------------------------------------------------
HARD_RUN = ("fartlek", "tempo", "intervals", "race_pace", "sharpen")


def resolve_day(day: dict, base: dict, flags: dict | None = None) -> dict:
    """Shares of a tested max become real numbers when a fresh test exists (else the fallback text stays); run segments
    get pace ranges and splits from a fresh two-mile (else effort and the talk test only); run/walk in weeks 1-2 when he
    is coming back to running; equipment substitutions by name."""
    flags = flags or {}
    out = copy.deepcopy(day)
    base = base or {}
    run_base = base.get("run") if (base.get("run") or {}).get("fresh") else None
    table = paces(run_base["value"]) if run_base else None
    for it in out.get("items", []):
        pct, of = it.get("pct"), it.get("pct_of")
        b = base.get(of) if of else None
        if pct and of:
            if b and b.get("fresh"):
                n = max(1, round(pct * b["value"]))
                each = " each side" if "each side" in str(it.get("reps")) else ""
                if of == "hrp":
                    it.update(lo=n, hi=n, reps=str(n), mode="fixed", load=None,
                              target=f"{plural(n, 'rep')} per set ({round(pct * 100)}% of your tested {b['value']} on {b['date']})",
                              note=f"{plural(n, 'rep')} a set: {round(pct * 100)}% of the {b['value']} you tested on {b['date']}.")
                else:
                    it.update(lo=n, hi=n, reps=f"{n} s{each}", mode="fixed",
                              target=f"{n} s per hold ({round(pct * 100)}% of your tested {mmss(b['value'])} on {b['date']}); stop if the hips move",
                              note=f"{plural(n, 'second')} a hold: {round(pct * 100)}% of your tested {mmss(b['value'])}.")
                it["resolved"] = True
            else:
                it["resolved"] = False
                if of == "hrp":
                    it.update(mode="fixed", load=None, note="No fresh push-up test on file: stop each set 4-5 reps before failure.")
        run = it.get("run")
        if run:
            for seg in run.get("segments") or []:
                zone = program.ZONES.get(seg.get("zone"))
                if zone:
                    seg["rpe"], seg["talk"] = zone["rpe"], zone["talk"]
                if table and seg.get("zone") in table:
                    seg["pace"] = pace_text(table[seg["zone"]])
                    if seg.get("m"):
                        seg["split"] = split_text(table[seg["zone"]], seg["m"])
            if table:
                lines, used = [], {}
                for seg in run["segments"]:
                    z = seg["zone"]
                    if z not in table:
                        continue
                    used[z] = pace_text(table[z])
                    reps = seg.get("reps", 1)
                    if seg.get("m"):
                        lines.append(f"{reps} x {split_text(table[z], seg['m'])} ({pace_text(table[z])})" if reps > 1 else
                                     f"{split_text(table[z], seg['m'])} ({pace_text(table[z])})")
                    else:
                        lines.append(f"{seg['min']} min at {pace_text(table[z])}")
                it["paces"] = used
                it["pace_lines"] = lines
                it["pace_note"] = f"From your two-mile test of {mmss(run_base['value'])} on {run_base['date']}. The talk test wins if they disagree."
            else:
                it["paces"] = None
                it["pace_lines"] = []
                it["pace_note"] = "No fresh two-mile test yet: go by effort (RPE) and the talk test, not a pace."
            if flags.get("run_walk") and int(flags.get("week") or 0) in (1, 2) and run["type"] in ("easy", "long"):
                total = round(run["total_min"])
                it["target"] = (f"Run/walk for {total} min: 2 min easy jog, 1 min walk, repeated. Easy (3-4 out of 10), full sentences.")
                it["run_walk"] = True
        sub = (flags.get("subs") or {}).get(it.get("key"))
        if sub and it.get("kind") not in ("test",):
            if sub.get("keep"):
                it["sub_note"] = sub["reason"]
            elif sub.get("name"):
                it.update(substituted_from=it.get("exercise"), exercise=sub["name"], sub_note=sub["reason"])
            else:
                it.update(skipped=True, sub_note=sub["reason"], no_equipment=True)
    return out


# ---- running rules ---------------------------------------------------------------------------------------------------
def run_review(planned_run: dict, logged: dict) -> dict:
    """How a logged run compares with the plan. Two reps in a row slower than the range end an interval session early."""
    planned = float((planned_run or {}).get("total_min") or 0)
    done = float(logged.get("minutes") or 0)
    pct = round(100 * done / planned) if planned else None
    slow = int(logged.get("slow_reps") or 0)
    hard = (planned_run or {}).get("type") in HARD_RUN
    stopped = hard and slow >= 2
    rpe = logged.get("rpe")
    bits = [f"{done:g} of {plural(planned, 'planned minute')}" if planned else plural(done, "minute")]
    if stopped:
        bits.append("two reps in a row were slower than the range, so the session ended there and next week repeats it instead of adding a rep")
    if (planned_run or {}).get("type") in ("easy", "long") and rpe and int(rpe) > 5:
        bits.append(f"an easy run at RPE {rpe} is too hard; slow down next time")
    return {"minutes_planned": planned, "minutes_done": done, "done_pct": pct, "stopped_early": stopped,
            "reason": "; ".join(bits).capitalize() + "."}


def next_run_adjust(history: dict, week: int, flags: dict | None = None) -> dict:
    """R4: running volume follows the plan into this week only when last week at least 80% of the planned run minutes
    were done, no pain flag is open and the easy runs were logged at RPE 5 or less; otherwise last week's minutes are
    repeated. R5: an interval session that ended early (two slow reps in a row) is repeated next week.
    history: {"planned": {week: run minutes}, "runs": [run events with "week", "minutes", "rpe", "kind", "slow_reps"]}
    -> {"progress", "cap_min", "scale", "repeat_intervals", "reasons"}"""
    flags = flags or {}
    planned = {int(k): float(v) for k, v in (history.get("planned") or {}).items()}
    runs = history.get("runs") or []
    out = {"progress": True, "cap_min": None, "scale": None, "repeat_intervals": False, "reasons": []}
    if week <= 1 or (week - 1) not in planned:
        return out
    last = [r for r in runs if int(r.get("week") or 0) == week - 1]
    done = sum(float(r.get("minutes") or 0) for r in last) + 28 * sum(1 for r in last if r.get("kind") == "test")
    want = planned[week - 1]
    easy = [r for r in last if r.get("kind") in ("easy", "long", "recovery") and r.get("rpe") not in (None, "")]
    why = []
    if want and done < 0.8 * want:
        why.append(f"last week you ran {done:g} of {want:g} planned minutes (under 80%)")
    if flags.get("pain_open"):
        why.append("a pain flag is open")
    if any(int(r["rpe"]) > 5 for r in easy):
        why.append("an easy run last week was logged harder than RPE 5")
    if why:
        this = planned.get(week, 0)
        out.update(progress=False, cap_min=want, scale=(want / this) if this > want else None)
        out["reasons"].append("Running volume repeats last week's " + f"{plural(want, 'minute')} because " + " and ".join(why) + ".")
    if any(r.get("kind") in HARD_RUN and int(r.get("slow_reps") or 0) >= 2 for r in last):
        out["repeat_intervals"] = True
        out["reasons"].append("Last week's interval session ended early after two slow reps in a row, so this week repeats it instead of adding a rep.")
    return out


def run_cap(runs: list, today: date) -> tuple:
    """The single-run rule: once he has three or more logged runs in the previous 30 days, no run is longer than 110% of
    the longest of them. -> (cap minutes or None, longest)"""
    since = (today - timedelta(days=30)).isoformat()
    recent = [float(r.get("minutes") or 0) for r in runs or [] if since <= str(r.get("date")) < today.isoformat() and r.get("minutes")]
    if len(recent) < 3:
        return None, None
    longest = max(recent)
    return math.floor(longest * 1.1), longest


# ---- adapting today's session ----------------------------------------------------------------------------------------
_AREAS = {"lower": ("knee", "ankle", "foot", "feet", "shin", "calf", "calves", "hamstring", "quad", "hip", "groin", "achilles", "leg",
                    "glute", "thigh", "toe", "heel", "it band"),
          "back": ("back", "spine", "lumbar"),
          "shoulder": ("shoulder", "rotator", "neck", "trap"),
          "chest": ("chest", "pec", "sternum", "rib"),
          "arm": ("elbow", "wrist", "hand", "forearm", "finger", "bicep", "tricep", "arm")}
_PRESS = ("h_push", "v_push", "fly", "raise", "extension")
_PULL = ("h_pull", "v_pull", "rear", "curl")


def pain_region(area: str) -> str:
    said = str(area or "").lower()
    return next((r for r, words in _AREAS.items() if any(w in said for w in words)), "other")


def _loads(it: dict, region: str) -> bool:
    """Does this item load the painful region?"""
    key = it.get("key") or ""
    t = coach.TAGS.get(key, {})
    if it.get("run") or key in ("sprint", "sdc", "run"):
        return region in ("lower",)
    if it.get("kind") == "test":
        return {"lower": ("mdl", "sdc", "run"), "back": ("mdl", "sdc"), "shoulder": ("hrp", "plk"), "chest": ("hrp",),
                "arm": ("hrp", "plk", "mdl")}.get(region, ()).__contains__(it.get("test"))
    pattern = t.get("pattern", "")
    if region == "lower":
        return bool(t.get("lower")) or pattern in ("squat", "hinge", "jump", "calf", "curl_leg")
    if region == "back":
        return pattern in ("hinge", "squat", "carry", "h_pull") or key in ("deadlift", "back_squat", "rdl", "farmer_carry", "row")
    if region == "shoulder":
        return pattern in _PRESS + _PULL or key in ("hand_release_pushup", "dip", "plank", "side_plank")
    if region == "chest":
        return pattern in ("h_push", "fly") or key in ("dip", "hand_release_pushup")
    if region == "arm":
        return pattern in _PRESS + _PULL + ("carry",) or key in ("hand_release_pushup", "dip", "farmer_carry")
    return False


def _hold_load(it: dict, last_loads: dict) -> bool:
    last = (last_loads or {}).get(it.get("key"))
    if it.get("kind") == "lift" and it.get("load") and last and float(it["load"]) > float(last):
        it.update(load=float(last), mode="hold", note=f"Held at {last:g}: no progression while a pain flag is open.")
        return True
    return False


def _easy_run(it: dict, frac: float = 0.7, why: str = "") -> None:
    run = it["run"]
    m = max(10, round(float(run["total_min"]) * frac))
    it.update(converted_from=it.get("exercise"), exercise="Easy run", reps=f"{m} min", target="easy (3-4 out of 10; full sentences)",
              run={**run, "type": "easy", "segments": [{"min": m, "zone": "E"}], "total_min": m, "hard_min": 0, "warm_min": 0, "cool_min": 0},
              pace_lines=[], note=why)


def _bike(it: dict) -> None:
    run = it.get("run")
    minutes = round(float(run["total_min"])) if run else None
    target = f"same minutes and effort as the run: {it.get('target', '')}" if run else "8 s all-out on an air bike or rower per rep, full rest"
    it.update(converted_from=it.get("exercise"), exercise="Bike or rower", key="bike", target=target, pace_lines=[], paces=None,
              note="Running is off today for the pain flag; the bike or rower keeps the same minutes and effort.")
    if run:
        it["reps"] = f"{minutes} min"


def _buildups(it: dict) -> None:
    it.update(converted_from=it.get("exercise"), exercise="Build-ups: 6 x 40 m", sets="6", count=6, reps="40 m",
              target="build-ups to about 80%: smooth, tall, relaxed")


def _easier_lift(it: dict) -> None:
    planned = int(it.get("planned_sets") or it.get("sets") or 1)
    rir = int(it.get("rir") or 0) + 1
    it.update(planned_sets=it.get("planned_sets") or it.get("sets"), sets=str(max(1, planned - 1)), rir=rir, target=plural(rir, 'rep') + ' in reserve')
    if it.get("load"):
        it["note"] = (it.get("note", "") + f" Today: {rir} in reserve and one set fewer.").strip()


def _is_sprint_day(day: dict) -> bool:
    return day.get("session_kind") in ("field_power", "time_trial", "aft_test", "tests", "lower_strength") or \
        any(i.get("key") == "sprint" or i.get("kind") == "test" for i in day.get("items", []))


def adapt(view_day: dict, context: dict) -> dict:
    """Today's PCA session after the program's own rules, on top of coach.adapt_day (view_day is its output, with
    "intensity" and "session_kind" from the plan). Each change: {"kind", "reason", "overridable"}. Pain rules are never
    overridable; an "override" for today removes every overridable change and says he chose to run it as planned.
    context: today (iso), week, checkin, pain [{area, level, moves, since}], external [{kind, minutes, rpe, miles, date}],
    override, runs [run events], run_history (for next_run_adjust), last_loads {key: last top load}, last_session (iso),
    started (iso), prev_quality_item, schedule rows."""
    day = copy.deepcopy(view_day)
    ctx = context or {}
    today = date.fromisoformat(ctx["today"])
    adj = day.setdefault("adjustments", [])
    for a in adj:
        a.setdefault("overridable", a.get("kind") == "readiness")
    override = bool(ctx.get("override"))
    items = day.setdefault("items", [])
    day.setdefault("postponed", [])

    def note(kind, reason, overridable=True, **more):
        adj.append({"kind": kind, "reason": reason, "overridable": overridable, **more})

    # a long gap: recommend going back a week (shown on rest days too)
    last = ctx.get("last_session") or ""
    anchor = last or ctx.get("started") or ""
    if anchor and day.get("status") in ("train", "rest"):    # a recommendation, not a session change: an override keeps it
        gap = (today - date.fromisoformat(anchor)).days
        if gap >= 14 and (last or gap >= 21):
            note("rollback", f"It has been {plural(gap, 'day')} without a logged session. I recommend rolling the program back one week so you "
                 "restart where you left off (say 'roll back a week', or tap Roll back).", False, action="rollback", weeks=1)
    if day.get("status") != "train":
        return day

    # a moved HIGH session never lands next to another HIGH day
    rows = ctx.get("schedule") or day.get("schedule") or []
    idx = today.weekday()
    if rows and rows[idx].get("moved_from") and (rows[idx].get("session") or {}).get("intensity") == "high":
        near = [rows[i] for i in (idx - 1, idx + 1) if 0 <= i < len(rows) and rows[i].get("session")]
        if any((r["session"] or {}).get("intensity") == "high" for r in near):
            day.update(status="rest", items=[], title="Rest and recovery")
            note("dropped", f"{coach._FULL[rows[idx]['moved_from']]}'s {(rows[idx]['session'] or {}).get('title', 'session').lower()} was missed; "
                 "moving it to today would put two hard days back to back, so it is dropped and the week is a little lighter.", False)
            return day

    # pain: never overridable, never a diagnosis
    for flag in ctx.get("pain") or []:
        level = int(flag.get("level") or 0)
        region = pain_region(flag.get("area"))
        days_open = (today - date.fromisoformat(flag["since"])).days if flag.get("since") else 0
        see = days_open >= 7 or level >= 7
        where = str(flag.get("area") or "that area").lower()
        if (level >= 4 or flag.get("moves")) and region == "other":
            for it in items:                                  # an area I can't map to exercises: change nothing I can't name
                _hold_load(it, ctx.get("last_loads"))
            note("pain", f"Your {where} is at {level} out of 10. I can't tell which exercises load it, so I removed nothing: judge each "
                 "one yourself and skip anything that hurts. No load goes up anywhere while it is open." +
                 (" At this level, please see a medic before you train." if level >= 7 else
                  " If it is still there in a week, or it gets worse, see a medic."), False)
        elif level >= 4 or flag.get("moves") or flag.get("unknown"):      # unknown: chest pain, never asked 0-10, held like a severe one
            hit, swapped = [], False
            for it in list(items):
                if not _loads(it, region):
                    continue
                if (it.get("run") or it.get("key") in ("sprint", "run")) and it.get("kind") != "test":
                    _bike(it)
                    swapped = True
                else:
                    items.remove(it)
                    if it.get("kind") == "test":
                        day["postponed"].append(it.get("exercise"))
                    hit.append(it.get("exercise", "").lower())
            for it in items:
                _hold_load(it, ctx.get("last_loads"))
            hit = list(dict.fromkeys(hit))
            parts = (["running becomes the bike or rower"] if swapped else []) +                 ([f"{', '.join(hit)} {'is' if len(hit) == 1 else 'are'} out"] if hit else [])
            what = f"; today: {' and '.join(parts)}" if parts else ""
            said = "was reported, level unknown" if flag.get("unknown") else f"is at {level} out of 10"
            note("pain", f"Your {where} {said}{' and changes how you move' if flag.get('moves') else ''}, so the work that loads "
                 f"it comes out{what}. No load goes up anywhere while it is open." +
                 (" It has been a week or more, or it is severe: please see a medic or physical therapist." if see else ""), False)
        elif level >= 1:
            held = [it.get("exercise", "").lower() for it in items if _loads(it, region) and _hold_load(it, ctx.get("last_loads"))]
            note("pain", f"Your {where} is at {level} out of 10: train, but the lifts that load it do not go up while it is open"
                 + (f" ({', '.join(held)})" if held else "") + ". Stop if it gets worse or changes how you move." +
                 (" It has been open a week: please see a medic or physical therapist." if see else ""), False)

    if override:
        note("override", "You chose to run today as planned, so the changes I would have made are off. Pain rules still apply.", False)
    else:
        checkin = ctx.get("checkin") or {}
        signs = coach.low_signs(checkin)
        sleep = checkin.get("sleep_hours")
        short = bool(sleep) and float(sleep) < 5 and _is_sprint_day(day)
        if (len(signs) >= 2 and day.get("intensity") == "high") or short:
            why = coach.upfirst(" and ".join(signs[:2])) if len(signs) >= 2 else f"You slept {plural(sleep, 'hour')}" + \
                (" (from your Garmin)" if checkin.get("source") == "garmin" else "")
            changed = _go_easy(day, items, main_lifts=True)
            risk = " Under 5 hours of sleep before sprinting or a max effort raises the risk of a pulled hamstring and gives a result that is not really yours." if short else ""
            note("readiness", f"{why}, so today is easier: {changed}. You can override this.{risk}", True)
        ext = ctx.get("external") or []
        monday = today - timedelta(days=today.weekday())

        def ago(e):
            return (today - date.fromisoformat(str(e["date"]))).days
        unit_run = [e for e in ext if e.get("kind") == "unit_run" and monday <= date.fromisoformat(str(e["date"])) <= today
                    and (int(e.get("rpe") or 0) >= 7 or (float(e.get("minutes") or 0) >= 20 and int(e.get("rpe") or 0) >= 6))]
        hard_runs = [it for it in items if (it.get("run") or {}).get("type") in HARD_RUN]
        if unit_run and hard_runs:
            for it in hard_runs:
                _easy_run(it)
            note("external", f"Your unit run on {date.fromisoformat(str(unit_run[-1]['date'])).strftime('%A')} was this week's hard run, so "
                 "today's hard run becomes an easy run.", True)
        ruck = [e for e in ext if e.get("kind") == "ruck" and float(e.get("miles") or 0) >= 6 and 0 <= ago(e) <= 2]
        if ruck:
            gone = []
            for it in list(items):
                if (it.get("run") or {}).get("type") in HARD_RUN:
                    _easy_run(it)
                    gone.append("the hard run is easy")
                elif it.get("key") in ("sprint", "sdc") and it.get("kind") != "test":
                    items.remove(it)
                    gone.append(f"{it.get('exercise', '').lower()} is out")
            if gone:
                note("external", f"A {float(ruck[-1]['miles']):g}-mile ruck in the last 48 hours: no hard running or sprints yet, so " + ", ".join(gone) + ".", True)
        week_ruck = [e for e in ext if e.get("kind") == "ruck" and float(e.get("miles") or 0) >= 6 and monday <= date.fromisoformat(str(e["date"])) <= today]
        longs = [it for it in items if (it.get("run") or {}).get("type") == "long"]
        if week_ruck and longs:
            for it in longs:
                items.remove(it)
            note("external", f"Your {float(week_ruck[-1]['miles']):g}-mile ruck counts as this week's long run, so today's long run is off.", True)
        legs = [e for e in ext if e.get("kind") == "unit_strength" and int(e.get("rpe") or 0) >= 7 and 0 <= ago(e) <= 1]
        if legs and day.get("session_kind") == "lower_strength":
            for it in items:
                if _main_lift(it) and coach.TAGS.get(it.get("key"), {}).get("lower"):
                    _easier_lift(it)
            note("external", "A hard unit strength session in the last 24 hours: today's heavy lower-body lifts are 1 rep easier and one set fewer.", True)
        field = [e for e in ext if e.get("kind") == "field" and int(e.get("rpe") or 0) >= 7 and 0 <= ago(e) <= 1]
        if field and day.get("intensity") == "high":
            changed = _go_easy(day, items, main_lifts=False)
            note("external", f"A hard field day in the last 24 hours, so today is easier: {changed}.", True)
        # running volume and the single-run cap
        hist = next_run_adjust(ctx.get("run_history") or {}, int(ctx.get("week") or 1), {"pain_open": bool(ctx.get("pain"))})
        if hist["scale"]:
            for it in items:
                run = it.get("run")
                if run and run["type"] in ("easy", "long"):
                    m = max(10, round(run["total_min"] * hist["scale"]))
                    it.update(reps=f"{m} min", run={**run, "total_min": m, "segments": [{"min": m, "zone": "E"}] if len(run["segments"]) == 1 else run["segments"]})
        if not hist["progress"]:
            note("run_volume", hist["reasons"][0], True)
        if hist["repeat_intervals"] and ctx.get("prev_quality_item"):
            for n, it in enumerate(items):
                if (it.get("run") or {}).get("type") in HARD_RUN:
                    items[n] = {**copy.deepcopy(ctx["prev_quality_item"]), "done": it.get("done", []), "repeated": True}
            note("run_repeat", hist["reasons"][-1], True)
        cap, longest = run_cap(ctx.get("runs") or [], today)
        if cap:
            for it in items:
                run = it.get("run")
                if run and run["type"] in ("easy", "long") and float(run["total_min"]) > cap:
                    it.update(reps=f"{cap} min", run={**run, "total_min": cap, "segments": [{"min": cap, "zone": "E"}]})
                    note("run_cap", f"Your longest run in the last 30 days was {plural(longest, 'minute')}, so today's run is capped at {cap} "
                         "minutes (110%) to keep the jump safe.", True)
    day["items"] = items
    return day


def _main_lift(it: dict) -> bool:
    """A main lift: a loaded, RIR-gated, priority-1 lift (not the push-up practice that runs off a tested share)."""
    return it.get("kind") == "lift" and it.get("priority") == 1 and it.get("gate") == "rir"


def _go_easy(day: dict, items: list, main_lifts: bool) -> str:
    """The readiness conversion: the hard run becomes an easy run of 70% of its minutes, sprints become 6 x 40 m
    build-ups at 80%, main lifts get one more rep in reserve and one set fewer, tests are postponed."""
    changed = []
    for it in list(items):
        if (it.get("run") or {}).get("type") in HARD_RUN:
            _easy_run(it)
            changed.append(f"the hard run is an easy {it['reps']}")
        elif it.get("key") == "sprint" and it.get("kind") == "cardio":
            _buildups(it)
            changed.append("sprints are build-ups at 80%")
        elif it.get("kind") == "test":
            items.remove(it)
            day.setdefault("postponed", []).append(it.get("exercise"))
            changed.append(f"the {it.get('exercise', 'test').lower()} is postponed (never tested under-recovered)")
        elif main_lifts and _main_lift(it):
            _easier_lift(it)
    if main_lifts and any(_main_lift(it) for it in items):
        changed.append("main lifts are one rep easier and one set fewer")
    return ", ".join(dict.fromkeys(changed)) or "the session is lighter"


# ---- review --------------------------------------------------------------------------------------------------------------
def progress(sets: list, runs: list, tests: list, weeks_planned: list, session_rpes: list, external: list, start: date, today: date) -> dict:
    """What he did, from logs and tests only (every point carries source "test" or "logged"; nothing is projected).
    weeks_planned: [{week, run_min, lift_sets, days: [{date, optional, minutes}]}] for the program weeks."""
    lifts = []
    for key in ("deadlift", "back_squat", "bench_press", "rdl", "pull_up", "incline_press"):
        points = []
        for day in coach.exposures(sets, key):
            best = max((coach.e1rm(s.get("weight"), s.get("reps"), s.get("rir")) or 0) for s in day)
            if best:
                points.append({"date": str(day[0].get("date")), "value": best, "source": "logged"})
        if points:
            lifts.append({"key": key, "name": coach.TAGS[key]["name"], "points": points})
    series = {ev: [{"date": str(t["date"]), "value": t["value"], "text": value_text(ev, t["value"]), "source": "test",
                    "points_at_least": aft_points(ev, t["value"])}
                   for t in sorted((t for t in tests if t.get("event") == ev), key=lambda t: str(t["date"]))] for ev in EVENTS}
    easy = [{"date": str(r["date"]), "value": round(60 * float(r["minutes"]) / float(r["distance_mi"])), "rpe": r.get("rpe"), "source": "logged"}
            for r in runs if r.get("kind") in ("easy", "long") and r.get("distance_mi") and r.get("minutes")]
    rows, due, done_n = [], 0, 0
    done_dates = {str(s.get("date")) for s in sets} | {str(r.get("date")) for r in runs} | {str(t.get("date")) for t in tests}
    for wk in weeks_planned:
        first = start + timedelta(days=7 * (wk["week"] - 1))
        if first > today:
            break
        last = first + timedelta(days=6)

        def inside(r):
            return first.isoformat() <= str(r.get("date")) <= last.isoformat()
        run_done = sum(float(r.get("minutes") or 0) for r in runs if inside(r)) + 28 * sum(1 for t in tests if inside(t) and t.get("event") == "run")
        sets_done = sum(1 for s in sets if inside(s) and coach.TAGS.get(s.get("key"), {}).get("mechanic") in ("compound", "isolation"))
        load = sum(int(r.get("rpe") or 0) * float(r.get("minutes") or 0) for r in session_rpes if inside(r)) + \
            sum(int(r.get("rpe") or 0) * float(r.get("minutes") or 0) for r in runs if inside(r) and not r.get("in_session")) + \
            sum(int(e.get("rpe") or 0) * float(e.get("minutes") or 0) for e in external if inside(e))
        for d in wk["days"]:
            if not d.get("optional") and d["date"] < today.isoformat():
                due += 1
                done_n += d["date"] in done_dates
        rows.append({"week": wk["week"], "run_min_planned": round(wk["run_min"]), "run_min_done": round(run_done),
                     "sets_planned": wk["lift_sets"], "sets_done": sets_done, "load": round(load)})
    return {"lifts": lifts, "tests": series, "easy_pace": easy, "weeks": rows,
            "compliance": round(done_n / due, 2) if due else None}          # a 0-1 share of the due sessions done


def next_block(tests: dict) -> str | None:
    """The emphasis of the next block, from the event with the fewest points; only when all five week-12 results exist."""
    if not tests or any(tests.get(ev) is None for ev in EVENTS):
        return None
    points = {ev: aft_points(ev, tests[ev]) or 0 for ev in EVENTS}
    weakest = min(EVENTS, key=lambda ev: (points[ev], EVENTS.index(ev)))
    return program.LONG_TERM["emphasis"][weakest]
