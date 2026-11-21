"""The personal trainer's rules, in plain Python: no model, no storage, no clock of its own.

The model never chooses a load or a set count. This module does, from four inputs: the profile (goal, days, minutes,
equipment, training age), the saved plan, the sets he actually logged, and his morning check-ins. Every change it
makes to a day comes back with one sentence he can hear (docs/mission/research-fitness-coaching.md has the sources).

  intake(profile)                 -> the normalized answers (goal, days, minutes, equipment, level)
  build_plan(profile, start)      -> a weekly plan in the shape tools/fitness.py already stores and the page shows
  weekly_volume(week)             -> hard sets per muscle group for one week of a plan
  next_target(item, exposures)    -> load and reps for the next session of one exercise, with the reason
  e1rm(weight, reps, rir)         -> estimated one-rep max (Epley on reps + reps in reserve), a trend number only
  deload_due(...)                 -> (True, reason) when two fatigue signs show up inside two weeks
  adapt_day(plan, sets, checkins, today, ...) -> today's session after reshuffles, deloads, re-entry and readiness trims
  substitute(key, equipment, ...) -> the closest exercise he can do instead
  progress(sets, today)           -> strength per lift, weekly volume per muscle

Loads are pounds (the stored data already uses weight_lbs)."""
import math
import re
from datetime import date, timedelta

from core.words import plural

GOALS = {"strength": "get stronger", "muscle": "build muscle", "fat_loss": "lose fat and keep muscle",
         "general": "general fitness", "aft": "the Army Fitness Test",
         "glutes": "build the glutes and shape the lower body"}
MUSCLES = ("chest", "back", "shoulders", "biceps", "triceps", "quads", "hamstrings", "glutes", "calves", "core")
BIG = ("chest", "back", "shoulders", "quads", "hamstrings", "glutes")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ALL_GEAR = {"barbell", "dumbbell", "cable", "machine", "bench", "pullup_bar", "kettlebell", "trap_bar", "sled", "band",
            "dip_bar", "bodyweight"}

# key -> (name, pattern, primary muscles, secondary muscles, mechanic, any one of this gear, lower body?)
_T = {
    "deadlift": ("Trap-bar deadlift", "hinge", ("glutes", "hamstrings"), ("back", "quads"), "compound", ("trap_bar", "barbell"), True),
    "rdl": ("Romanian deadlift", "hinge", ("hamstrings",), ("glutes", "back"), "compound", ("barbell", "dumbbell"), True),
    "kettlebell_swing": ("Kettlebell swing", "hinge", ("glutes", "hamstrings"), (), "compound", ("kettlebell", "dumbbell"), True),
    "hip_thrust": ("Hip thrust", "hinge", ("glutes",), ("hamstrings",), "compound", ("barbell", "dumbbell"), True),
    "glute_bridge": ("Glute bridge", "hinge", ("glutes",), ("hamstrings",), "compound", ("bodyweight",), True),
    "squat": ("Goblet squat", "squat", ("quads",), ("glutes",), "compound", ("dumbbell", "kettlebell"), True),
    "bulgarian_split_squat": ("Bulgarian split squat", "squat", ("quads",), ("glutes",), "compound", ("dumbbell", "bodyweight"), True),
    "lunge": ("Walking lunge", "squat", ("quads",), ("glutes",), "compound", ("bodyweight", "dumbbell"), True),
    "bench_press": ("Bench press", "h_push", ("chest",), ("triceps", "shoulders"), "compound", ("barbell",), False),
    "incline_press": ("Incline dumbbell press", "h_push", ("chest",), ("shoulders", "triceps"), "compound", ("dumbbell",), False),
    "dip": ("Chest dip", "h_push", ("chest",), ("triceps",), "compound", ("dip_bar",), False),
    "hand_release_pushup": ("Hand-release push-up", "h_push", ("chest",), ("triceps", "shoulders"), "compound", ("bodyweight",), False),
    "chest_fly": ("Cable / dumbbell fly", "fly", ("chest",), (), "isolation", ("cable", "dumbbell"), False),
    "cable_crossover": ("Low-to-high cable fly", "fly", ("chest",), (), "isolation", ("cable",), False),
    "overhead_press": ("Overhead press", "v_push", ("shoulders",), ("triceps",), "compound", ("barbell", "dumbbell"), False),
    "lateral_raise": ("Lateral raise", "raise", ("shoulders",), (), "isolation", ("dumbbell", "cable", "band"), False),
    "row": ("Bent-over row", "h_pull", ("back",), ("biceps",), "compound", ("barbell", "dumbbell"), False),
    "chest_supported_row": ("Chest-supported row", "h_pull", ("back",), ("biceps",), "compound", ("dumbbell",), False),
    "pull_up": ("Pull-up / lat pulldown", "v_pull", ("back",), ("biceps",), "compound", ("pullup_bar", "machine"), False),
    "face_pull": ("Face pull", "rear", ("shoulders",), ("back",), "isolation", ("cable", "band"), False),
    "rear_delt_fly": ("Rear-delt fly", "rear", ("shoulders",), ("back",), "isolation", ("dumbbell",), False),
    "curl": ("Incline dumbbell curl", "curl", ("biceps",), (), "isolation", ("dumbbell",), False),
    "overhead_triceps": ("Overhead triceps extension", "extension", ("triceps",), (), "isolation", ("dumbbell", "cable"), False),
    "pushdown": ("Triceps pushdown", "extension", ("triceps",), (), "isolation", ("cable", "band"), False),
    "calf_raise": ("Calf raise", "calf", ("calves",), (), "isolation", ("bodyweight", "dumbbell", "machine"), True),
    "plank": ("Plank", "core", ("core",), (), "hold", ("bodyweight",), False),
    "side_plank": ("Side plank", "core", ("core",), (), "hold", ("bodyweight",), False),
    "dead_bug": ("Dead bug", "core", ("core",), (), "isolation", ("bodyweight",), False),
    "hanging_leg_raise": ("Hanging leg raise", "core", ("core",), (), "isolation", ("pullup_bar",), False),
    "farmer_carry": ("Farmer carry", "carry", ("core",), ("back",), "carry", ("dumbbell", "kettlebell"), True),
    "kettlebell_carry": ("Kettlebell carry", "carry", ("core",), ("back",), "carry", ("kettlebell", "dumbbell"), True),
    "back_squat": ("Back squat", "squat", ("quads",), ("glutes",), "compound", ("barbell",), True),
    "nordic_curl": ("Nordic hamstring curl", "curl_leg", ("hamstrings",), (), "isolation", ("bodyweight",), True),
    "box_jump": ("Box jump", "jump", (), (), "power", ("bodyweight",), True),
    "broad_jump": ("Broad jump", "jump", (), (), "power", ("bodyweight",), True),
    "pallof_press": ("Pallof press", "core", ("core",), (), "isolation", ("cable", "band"), False),
    "copenhagen_plank": ("Copenhagen plank", "core", ("core",), (), "hold", ("bench", "bodyweight"), False),
    "sdc": ("Sprint-drag-carry", "cardio", (), (), "cardio", ("sled", "kettlebell", "bodyweight"), True),
    "run": ("Run", "cardio", (), (), "cardio", ("bodyweight",), True),
    "sprint": ("Sprint", "cardio", (), (), "cardio", ("bodyweight",), True),
    "sled_drag": ("Sled drag (backwards)", "cardio", (), (), "cardio", ("sled",), True),
    "lateral_shuffle": ("Lateral shuffle", "cardio", (), (), "cardio", ("bodyweight",), True),
    "hip_abduction": ("Hip abduction (machine or band)", "abduction", ("glutes",), (), "isolation", ("machine", "band", "cable"), True),
    "glute_kickback": ("Glute kickback (cable or band)", "kickback", ("glutes",), ("hamstrings",), "isolation", ("cable", "band", "bodyweight"), True),
    "step_up": ("Step-up", "squat", ("glutes", "quads"), (), "compound", ("bench", "bodyweight", "dumbbell"), True),
}
TAGS = {k: {"name": v[0], "pattern": v[1], "primary": v[2], "secondary": v[3], "mechanic": v[4], "gear": v[5], "lower": v[6]}
        for k, v in _T.items()}

# A slot in a session -> the exercises that can fill it, best first.
_SLOT = {
    "h_push": ("bench_press", "incline_press", "dip", "hand_release_pushup"),
    "h_push2": ("incline_press", "dip", "hand_release_pushup", "bench_press"),
    "fly": ("chest_fly", "cable_crossover", "hand_release_pushup"),
    "v_push": ("overhead_press",), "side": ("lateral_raise",),
    "h_pull": ("row", "chest_supported_row"), "h_pull2": ("chest_supported_row", "row"), "v_pull": ("pull_up",),
    "rear": ("face_pull", "rear_delt_fly"), "biceps": ("curl",), "triceps": ("pushdown", "overhead_triceps"),
    "squat": ("squat", "bulgarian_split_squat", "lunge"), "single": ("bulgarian_split_squat", "lunge"),
    "hinge": ("deadlift", "rdl", "kettlebell_swing", "hip_thrust", "glute_bridge"),
    "hinge2": ("rdl", "hip_thrust", "kettlebell_swing", "glute_bridge"), "glute": ("hip_thrust", "glute_bridge"),
    "calves": ("calf_raise",), "core": ("plank", "hanging_leg_raise", "dead_bug", "side_plank"),
    "core2": ("hanging_leg_raise", "dead_bug", "side_plank", "plank"), "carry": ("farmer_carry", "kettlebell_carry"),
    "pushup": ("hand_release_pushup",), "deadlift": ("deadlift", "rdl", "kettlebell_swing", "glute_bridge"),
    # the glutes goal (a beginner's lower-body plan): glute work first, light upper-body work for posture only
    "thrust": ("hip_thrust", "glute_bridge"), "bridge": ("glute_bridge", "hip_thrust"),
    "g_hinge": ("rdl", "kettlebell_swing", "glute_bridge"), "g_squat": ("squat", "lunge", "bulgarian_split_squat"),
    "g_single": ("step_up", "lunge", "bulgarian_split_squat"), "g_single2": ("lunge", "bulgarian_split_squat", "step_up"),
    "abduct": ("hip_abduction", "glute_kickback", "side_plank"), "kick": ("glute_kickback", "hip_abduction"),
    "row_easy": ("chest_supported_row", "row"), "push_easy": ("incline_press",),
    "core_easy": ("dead_bug", "plank", "side_plank"), "core_easy2": ("side_plank", "plank", "dead_bug"),
}
_SESSION = {
    "full_a": ("Full body A", ("squat", "h_push", "h_pull", "hinge2", "side", "core")),
    "full_b": ("Full body B", ("hinge", "v_push", "v_pull", "single", "h_push2", "core2")),
    "full_c": ("Full body C", ("single", "h_push2", "h_pull2", "glute", "biceps", "triceps")),
    "upper_a": ("Upper body A", ("h_push", "h_pull", "v_push", "v_pull", "fly", "biceps", "triceps")),
    "upper_b": ("Upper body B", ("h_push2", "v_pull", "h_pull2", "side", "fly", "triceps", "biceps", "rear")),
    "lower_a": ("Lower body A", ("squat", "hinge2", "single", "calves", "core")),
    "lower_b": ("Lower body B", ("hinge", "single", "glute", "calves", "core2")),
    "push": ("Push", ("h_push", "v_push", "h_push2", "fly", "side", "triceps")),
    "pull": ("Pull", ("v_pull", "h_pull", "h_pull2", "rear", "biceps", "core")),
    "legs": ("Legs", ("squat", "hinge2", "single", "glute", "calves", "core2")),
    "aft_strength": ("AFT strength: deadlift and push-ups", ("deadlift", "squat", "pushup", "h_pull", "core", "carry")),
    "aft_intervals": ("AFT run intervals and push-up volume", ("@intervals", "pushup", "core2")),
    "aft_sdc": ("AFT sprint-drag-carry and upper body", ("@sprint", "@drag", "@shuffle", "carry", "v_push", "v_pull")),
    "aft_tempo": ("AFT tempo run and plank", ("@tempo", "core", "hinge2", "h_push2")),
    "aft_long": ("Easy long run and core", ("@long", "core2")),
    "cardio": ("Easy conditioning", ("@easy", "core")),
    "glute_a": ("Glutes A: hip thrust and squat", ("thrust", "g_squat", "g_hinge", "abduct", "row_easy", "core_easy")),
    "glute_b": ("Glutes B: single leg and hinge", ("g_single", "g_hinge", "bridge", "kick", "rear", "core_easy2")),
    "glute_c": ("Glutes C: hip thrust and lunge", ("thrust", "g_single2", "abduct", "push_easy", "core_easy")),
    "walk": ("Easy walk and core", ("@walk", "core_easy")),
}
_WHY = {
    "full": "Every big muscle group in one session, so each one is trained two or three times this week.",
    "upper": "Chest, back, shoulders and arms; the legs recover for the next lower day.",
    "lower": "Quads, hamstrings, glutes and calves, plus core, while the upper body recovers.",
    "push": "Chest, shoulders and triceps together, because they work together on every press.",
    "pull": "Back, rear shoulders and biceps: the pulling muscles in one session.",
    "legs": "The whole lower body in one hard session.",
    "aft_strength": "Builds the three-rep deadlift and push-up strength the test scores.",
    "aft_intervals": "Repeats at your goal two-mile pace teach the legs and lungs that speed.",
    "aft_sdc": "Practises the sprint, drag, shuffle and carry so the event feels familiar.",
    "aft_tempo": "A steady hard run raises the pace you can hold, and the plank work builds the hold time.",
    "aft_long": "Easy miles build the engine without wearing you down.",
    "cardio": "Easy cardio for the heart and recovery; you should be able to talk in full sentences.",
    "glute": "Glute work comes first while you are fresh, then the legs that support it. Upper-body work stays light on purpose: "
             "enough for posture, not for size.",
    "walk": "An easy walk for the heart and recovery; it does not take anything away from the glute days.",
}
_CARDIO = {
    "@intervals": ("run", "Run intervals", "6", "400 m", "goal two-mile pace, a few words at a time", 90),
    "@tempo": ("run", "Tempo run", "1", "20 min", "comfortably hard, short sentences only", 0),
    "@long": ("run", "Easy long run", "1", "35 min", "easy, full sentences", 0),
    "@easy": ("run", "Easy run or brisk walk", "1", "25 min", "easy, full sentences", 0),
    "@sprint": ("sprint", "Sprint", "6", "50 m", "fast, full recovery", 60),
    "@drag": ("sled_drag", "Sled drag (backwards)", "4", "50 m", "90 lb sled, quick steps", 75),
    "@shuffle": ("lateral_shuffle", "Lateral shuffle", "4", "50 m", "hips low", 45),
    "@walk": ("run", "Brisk walk (uphill if you are on a treadmill)", "1", "30 min", "easy, full sentences", 0),
}
_SPLITS = {2: (("Mon", "full_a"), ("Thu", "full_b")),
           3: (("Mon", "full_a"), ("Wed", "full_b"), ("Fri", "full_c")),
           4: (("Mon", "upper_a"), ("Tue", "lower_a"), ("Thu", "upper_b"), ("Fri", "lower_b")),
           5: (("Mon", "push"), ("Tue", "pull"), ("Wed", "legs"), ("Fri", "upper_b"), ("Sat", "lower_b")),
           6: (("Mon", "push"), ("Tue", "pull"), ("Wed", "legs"), ("Thu", "push"), ("Fri", "pull"), ("Sat", "legs"))}
_AFT = {3: (("Mon", "aft_strength"), ("Wed", "aft_intervals"), ("Fri", "aft_sdc")),
        4: (("Mon", "aft_strength"), ("Tue", "aft_intervals"), ("Thu", "aft_sdc"), ("Sat", "aft_tempo")),
        5: (("Mon", "aft_strength"), ("Tue", "aft_intervals"), ("Thu", "aft_sdc"), ("Fri", "aft_tempo"), ("Sat", "aft_long")),
        6: (("Mon", "aft_strength"), ("Tue", "aft_intervals"), ("Wed", "upper_b"), ("Thu", "aft_sdc"), ("Fri", "aft_tempo"),
            ("Sat", "aft_long"))}
# The glutes goal: three glute sessions a week is the most a beginner needs (each one trains them again once they have
# recovered); extra days are easy walks, never more lifting.
_GLUTES = {2: (("Mon", "glute_a"), ("Thu", "glute_b")),
           3: (("Mon", "glute_a"), ("Wed", "glute_b"), ("Fri", "glute_c")),
           4: (("Mon", "glute_a"), ("Wed", "glute_b"), ("Fri", "glute_c"), ("Sat", "walk")),
           5: (("Mon", "glute_a"), ("Tue", "walk"), ("Wed", "glute_b"), ("Fri", "glute_c"), ("Sat", "walk"))}
_VOLUME = {"novice": (6, 12), "intermediate": (10, 20), "advanced": (12, 20)}     # effective weekly sets, big muscles
_SPLIT_NAME = {2: "full body twice a week", 3: "full body three times a week", 4: "upper and lower, twice each",
               5: "push, pull, legs plus an upper and a lower day", 6: "push, pull, legs twice"}


# ---- intake ----------------------------------------------------------------------------------------------------------
_GLUTE_WORDS = r"\b(?:glutes?|butt|booty|curv(?:es|y|ier)|hourglass)\b"
_NAMED_OTHER = r"\b(?:muscle|hypertrophy|mass|bulk|jacked|physique|strength|strong(?:er)?|powerlift\w*)\b"
_GOAL_WORDS = (
    ("aft", r"\b(?:aft|acft|army (?:fitness|combat fitness) test|fitness test|pt test|army test)\b"),
    ("fat_loss", r"\b(?:fat|lose weight|weight loss|lean(?:er)?|cut(?:ting)?|shred(?:ded)?|slim|drop \d+ (?:pounds|lbs?))\b"),
    ("muscle", r"\b(?:muscle|hypertrophy|size|mass|bulk|bigger|physique|aesthetic|jacked|toned?)\b"),
    ("strength", r"\b(?:strength|strong(?:er)?|powerlift\w*|heavier|one[- ]rep max|1rm|pr)\b"),
    ("general", r"\b(?:general|overall|health(?:y|ier)?|fit(?:ter|ness)?|shape|conditioning|endurance|stamina)\b"),
)


def parse_goal(text: str) -> str:
    """His words -> one of GOALS, or '' when they name none."""
    said = str(text or "").lower()
    if said.strip() in GOALS:
        return said.strip()
    for goal, pattern in _GOAL_WORDS[:2]:                     # the test and fat loss: named outright, they win
        if re.search(pattern, said):
            return goal
    # glutes: only when the sentence does not name muscle or strength as the goal ("strong glutes" in a muscle goal
    # stays a muscle goal; "a bigger butt, not muscular" is this one)
    if re.search(_GLUTE_WORDS, said) and (not re.search(_NAMED_OTHER, said) or re.search(r"\bnot\s+(?:too\s+)?(?:muscular|bulky|big)\b|\bfeminine\b", said)):
        return "glutes"
    for goal, pattern in _GOAL_WORDS[2:]:
        if re.search(pattern, said):
            return goal
    return ""


def gear(text) -> set:
    """'gym' / 'home dumbbells and a pull-up bar' / 'bodyweight only' -> gear tags."""
    said = str(text or "").lower()
    if not said.strip() or re.search(r"\b(?:full|commercial|army|base|post)?\s*gym\b|\beverything\b", said) and "home gym" not in said:
        return set(ALL_GEAR)
    have = {"bodyweight"}
    for tag, pattern in (("dumbbell", r"dumb ?bell|\bdbs?\b|home"), ("bench", r"bench|home"), ("band", r"band|home"),
                         ("barbell", r"barbell|rack|home gym"), ("kettlebell", r"kettle ?bell|\bkbs?\b"),
                         ("pullup_bar", r"pull[- ]?up|chin[- ]?up"), ("trap_bar", r"trap[- ]?bar|hex[- ]?bar"),
                         ("cable", r"cable"), ("machine", r"machine"), ("sled", r"sled"), ("dip_bar", r"dip")):
        if re.search(pattern, said):
            have.add(tag)
    if re.search(r"\b(?:body ?weight|no equipment|nothing|none)\b", said) and len(have) <= 4 and "dumb" not in said:
        return {"bodyweight"}
    return have


def level(months) -> str:
    try:
        months = float(months)
    except (TypeError, ValueError):
        said = str(months or "").lower()
        if re.search(r"advanc|expert|years", said):
            return "advanced" if "advanc" in said or "expert" in said else "intermediate"
        return "intermediate" if "inter" in said else "novice"
    return "novice" if months < 12 else "intermediate" if months < 36 else "advanced"


def _num(value, default, low, high):
    found = re.search(r"\d+", str(value if value is not None else ""))
    return max(low, min(high, int(found.group()))) if found else default


def intake(profile: dict) -> dict:
    """The profile as the planner reads it; anything he has not said gets a careful default."""
    p = profile or {}
    goal = parse_goal(p.get("goal")) or ("aft" if p.get("test_date") or p.get("goal_total") else
                                         parse_goal(p.get("physique_goal")) or "general")
    return {"goal": goal, "days": _num(p.get("days_per_week"), 3 if goal != "aft" else 4, 2, 6),
            "minutes": _num(p.get("session_minutes"), 60, 20, 120), "gear": gear(p.get("equipment")),
            "level": level(p.get("training_months") if p.get("training_months") not in (None, "") else p.get("experience")),
            "avoid": {w for w in re.split(r"[,;/]| and ", str(p.get("avoid") or "").lower()) if w.strip()},
            "test_date": str(p.get("test_date") or "")}


# ---- building a plan -------------------------------------------------------------------------------------------------
def can_do(key: str, have: set) -> bool:
    return key in TAGS and any(g in have for g in TAGS[key]["gear"])


def _pick(slot: str, have: set, taken: set, avoid=()) -> str | None:
    for key in _SLOT[slot]:
        if key not in taken and can_do(key, have) and not _avoided(key, avoid):
            return key
    return None


def _avoided(key: str, avoid) -> bool:
    words = TAGS[key]["name"].lower() + " " + key.replace("_", " ") + " " + TAGS[key]["pattern"]
    return any(a.strip() and a.strip() in words for a in avoid)


_EACH_SIDE = ("step_up", "lunge", "bulgarian_split_squat", "glute_kickback")


def _glute_prescription(key: str, lvl: str) -> dict:
    """The glutes goal. Glute and leg lifts: 8 to 12 reps (10 to 15 where the load is light: bridges, step-ups), three
    sets, about 2 reps left in the tank. Small glute moves: 12 to 20 reps. Upper body: two easy sets, for posture.
    Loads go up 5 lb at a time: a 10 lb jump is too big for a beginner's dumbbells."""
    t = TAGS[key]
    side = ", each side" if key in _EACH_SIDE else ""
    if t["lower"] and t["mechanic"] == "compound":
        lo, hi = (10, 15) if key in ("glute_bridge", "step_up") else (8, 12)
        sets, rest, rir = 3 if lvl == "novice" else 4, 90, 2
    elif t["lower"]:
        lo, hi, sets, rest, rir = 12, 20, 2 if lvl == "novice" else 3, 60, 2
    elif t["pattern"] == "core":
        return {"sets": 2, "reps": "8-12", "lo": 8, "hi": 12, "rir": None, "rest_s": 45, "kind": "lift", "step": 5,
                "target": "slow and controlled, each side"}
    else:
        lo, hi, sets, rest, rir = 10, 15, 2, 60, 3
    light = "bodyweight" in t["gear"]                         # can be done with no weight: reps first, then a harder version
    return {"sets": sets, "reps": f"{lo}-{hi}", "lo": lo, "hi": hi, "rir": rir, "rest_s": rest, "kind": "lift", "step": 5,
            "target": f"stop with {rir} good reps left{side}",
            "progression": f"{hi} reps on every set, then add 5 lb" + (" (with no weight: add a band, or lower for 3 seconds)" if light else "")}


def _prescription(key: str, goal: str, lvl: str, first: bool) -> dict:
    t = TAGS[key]
    if t["mechanic"] == "hold" and goal == "glutes":
        return {"sets": 2, "reps": "20-40 s", "lo": 20, "hi": 40, "rir": None, "rest_s": 45, "kind": "hold",
                "target": "hold with perfect form"}
    if t["mechanic"] == "hold":
        return {"sets": 3, "reps": "45-60 s", "lo": 45, "hi": 60, "rir": None, "rest_s": 60, "kind": "hold",
                "target": "hold with perfect form"}
    if t["mechanic"] == "carry":
        return {"sets": 3, "reps": "40 m", "lo": 0, "hi": 0, "rir": None, "rest_s": 75, "kind": "carry", "target": "heavy, tall posture"}
    compound = t["mechanic"] == "compound"
    if goal == "glutes":
        return _glute_prescription(key, lvl)
    if key == "hand_release_pushup" and goal == "aft":
        lo, hi, rest = 10, 20, 90
    elif goal == "strength" and compound and first:
        lo, hi, rest = 3, 6, 180
    elif goal == "aft" and key == "deadlift":
        lo, hi, rest = 3, 5, 180
    elif compound:
        lo, hi, rest = (6, 10, 150) if goal == "strength" else (8, 12, 90 if goal == "fat_loss" else 120)
    else:
        lo, hi, rest = 10, 15, 60 if goal == "fat_loss" else 75
    sets = (3 if lvl == "novice" else 4 if first or lvl == "advanced" else 3) if compound else (2 if lvl == "novice" else 3)
    rir = 2
    step = 10 if t["lower"] else 5
    return {"sets": sets, "reps": f"{lo}-{hi}", "lo": lo, "hi": hi, "rir": rir, "rest_s": rest, "kind": "lift",
            "target": f"{rir} reps in reserve",
            "progression": f"{hi} reps on every set, then add {step} lb"}


def _rest_words(seconds: int) -> str:
    if not seconds:
        return ""
    return plural(seconds // 60, "min", "min") if seconds % 60 == 0 else f"{seconds} s"


def _item(key: str, rx: dict, name: str = "") -> dict:
    return {"exercise": name or TAGS[key]["name"], "key": key, "sets": str(rx["sets"]), "reps": rx["reps"],
            "target": rx.get("target", ""), "rest": _rest_words(rx.get("rest_s", 0)),
            **{k: rx[k] for k in ("lo", "hi", "rir", "rest_s", "kind", "progression", "step") if k in rx}}


def _minutes(items: list) -> float:
    return sum(_sets(i) * ((i.get("rest_s") or 0) + 45) for i in items if i.get("kind") != "cardio") / 60 + \
        sum(_num(i.get("reps"), 0, 0, 90) * _sets(i) for i in items if i.get("kind") == "cardio" and "min" in str(i.get("reps")))


def _sets(item: dict) -> int:
    return _num(item.get("sets"), 0, 0, 20)


def _session(kind: str, who: dict) -> dict:
    title, slots = _SESSION[kind]
    items, taken = [], set()
    for slot in slots:
        if slot.startswith("@"):
            key, name, sets, reps, target, rest = _CARDIO[slot]
            if not can_do(key, who["gear"]):
                key, name, target = "sprint", "Hill or flat sprint", "fast, full recovery"
            items.append(_item(key, {"sets": sets, "reps": reps, "target": target, "rest_s": rest, "kind": "cardio"}, name))
            continue
        key = _pick(slot, who["gear"], taken, who["avoid"])
        if key is None:
            continue
        taken.add(key)
        items.append(_item(key, _prescription(key, who["goal"], who["level"], not any(i["kind"] == "lift" for i in items))))
    if who["goal"] in ("fat_loss", "general") and not kind.startswith(("aft", "cardio")):
        items.append(_item("run", {"sets": "1", "reps": "15 min", "target": "easy, full sentences", "rest_s": 0, "kind": "cardio"},
                           "Easy cardio finisher"))
    room = who["minutes"] - 10                                # the warm-up takes about ten
    while len(items) > 3 and _minutes(items) > room:          # short on time: the last accessory goes first
        drop = next((i for i in reversed(items) if i.get("kind") != "cardio" or len(items) > 4), items[-1])
        items.remove(drop)
    while _minutes(items) > room and any(_sets(i) > 2 for i in items if i.get("kind") != "cardio"):
        lifts = [i for i in items if i.get("kind") != "cardio"]
        most = max(lifts, key=(lambda i: (_sets(i), lifts.index(i))) if who["goal"] == "glutes" else _sets)
        most["sets"] = str(_sets(most) - 1)
    family = "full" if kind.startswith("full") else kind.split("_")[0] if not kind.startswith("aft") and kind != "cardio" else kind
    return {"title": title, "items": items, "why": _WHY.get(family, ""), "kind_id": kind}


def weekly_volume(week: dict, hard_only: bool = False) -> dict:
    """Effective sets per muscle for one plan week: a primary muscle counts 1 per set, a helper muscle a half."""
    out = dict.fromkeys(MUSCLES, 0.0)
    for day in week.get("days", []):
        for it in day.get("items", []):
            t = TAGS.get(it.get("key") or key_of(it.get("exercise")))
            if not t:
                continue
            for m in t["primary"]:
                out[m] += _sets(it)
            for m in t["secondary"]:
                out[m] += _sets(it) * 0.5
    return out


def _fit_volume(days: list, who: dict) -> list[str]:
    """Nudge set counts until the big muscles sit inside the range for his training age. Returns notes on any miss."""
    low, high = _VOLUME[who["level"]]
    room = who["minutes"] - 10
    for _ in range(40):
        vol = weekly_volume({"days": days})
        short = [m for m in BIG if vol[m] < low]
        over = [m for m in MUSCLES if vol[m] > high]
        changed = False
        for m in over:
            pool = [i for d in days for i in d["items"] if m in TAGS.get(i["key"], {}).get("primary", ()) and _sets(i) > 2
                    and not set(TAGS[i["key"]]["primary"]) & set(short)]
            if pool:
                it = max(pool, key=_sets)
                it["sets"] = str(_sets(it) - 1)
                changed = True
        for m in short if not over else []:
            pool = [(d, i) for d in days for i in d["items"] if i.get("kind") == "lift" and m in TAGS[i["key"]]["primary"]
                    and _sets(i) < 5 and _minutes(d["items"]) + ((i.get("rest_s") or 0) + 45) / 60 <= room]
            if pool:
                _, it = min(pool, key=lambda p: _sets(p[1]))
                it["sets"] = str(_sets(it) + 1)
                changed = True
        if not changed:
            break
    vol = weekly_volume({"days": days})
    return [f"{m.capitalize()} is at {plural(round(vol[m], 1), 'set')} a week, under the {low} I aim for, because the sessions are short "
            "or the equipment is limited." for m in BIG if vol[m] < low and who["goal"] in ("muscle", "strength", "fat_loss")]


_GLUTE_NOTES = (
    "A rounder shape comes from glute muscle, and that grows slowly: expect to feel stronger within a few weeks and to see a "
    "change over months, not days. Training cannot choose where the body stores or loses fat.",
    "Lifting like this does not make a beginner bulky. Eat enough, with protein at each meal, or the glutes have nothing to grow from.",
    "If you are pregnant, gave birth in the last few months, or have back, hip or pelvic pain, ask your doctor before you start.",
)


def _layout(who: dict) -> tuple:
    days = who["days"]
    if who["goal"] == "aft":
        return _AFT[max(3, days)]
    if who["goal"] == "glutes":
        return _GLUTES[min(5, days)]
    if who["level"] == "novice" and days >= 5:               # five or six hard days is more than a first year needs
        return _SPLITS[4] + (("Sat", "cardio"),)
    if who["goal"] == "general" and days >= 5:
        return _SPLITS[4] + (("Sat", "cardio"),)
    return _SPLITS[days]


def build_plan(profile: dict, start: date, today: date | None = None) -> dict:
    """An eight-week block (or up to the test date) from the rules: split by days and training age, sets per muscle
    inside the range for that training age, a lighter week on schedule, a taper before a test."""
    who = intake(profile)
    layout = _layout(who)
    base = [dict(_session(kind, who), day=day) for day, kind in layout]
    notes = _fit_volume(base, who) if who["goal"] not in ("aft", "glutes") else []
    if who["goal"] == "glutes":                               # the sets are set by hand for this goal, not levelled per muscle
        notes = list(_GLUTE_NOTES)
    count, taper = 8, False
    if who["goal"] == "aft" and who["test_date"]:
        try:
            left = (date.fromisoformat(who["test_date"]) - start).days
            if left > 6:
                count, taper = max(2, min(12, math.ceil(left / 7))), True
        except ValueError:
            pass
    deload = 8 if who["level"] == "novice" else 6
    weeks = []
    for n in range(1, count + 1):
        mode = "taper" if taper and n == count else "deload" if n % deload == 0 and not (taper and n == count - 1) else \
            "base" if n <= 2 else "build"
        focus = {"taper": "Taper: fresh for the test", "deload": "Deload: half the sets, same loads", "base": "Base: learn the lifts, find your loads",
                 "build": "Build: add reps, then load"}[mode]
        days = []
        for d in base:
            items = []
            for i, it in enumerate(d["items"]):
                it = dict(it)
                sets = _sets(it)
                if mode in ("deload", "taper"):
                    sets = max(1, math.ceil(sets / 2))
                elif mode == "base" and who["goal"] == "glutes" and it.get("kind") == "lift":
                    sets = max(2, sets - 1)                   # the first two weeks are for learning the movements
                elif mode == "build" and who["level"] != "novice" and it.get("kind") == "lift" and i == 0 and sets < 5:
                    sets += 1
                it["sets"] = str(sets)
                items.append(it)
            days.append({"day": d["day"], "title": d["title"], "items": items, "why": d["why"]})
        weeks.append({"week": n, "focus": focus, "days": days})
    split = "the five test events across the week" if who["goal"] == "aft" else _SPLIT_NAME.get(len(layout), _SPLIT_NAME[4]) \
        if len(layout) != 5 or layout[-1][1] != "cardio" else "upper and lower twice each, plus an easy cardio day"
    low, high = _VOLUME[who["level"]]
    if who["goal"] == "glutes":
        lifting = sum(1 for _, kind in layout if kind != "walk")
        walks = len(layout) - lifting
        why = (f"{plural(lifting, 'glute session')} a week{f', plus {plural(walks, chr(34) + 'easy walk' + chr(34))}'.replace(chr(34), '') if walks else ''}. "
               "Each session starts with the lifts that load the glutes hardest (hip thrusts or bridges, hinges, step-ups and lunges), "
               "then small glute moves, and only a little upper-body work for posture. The first two weeks use fewer sets so you can "
               f"learn the movements; after that you add reps, then 5 lb at a time. Week {deload if deload <= count else count} is lighter so you recover.")
        return {"name": f"{plural(count, 'week')} to {GOALS['glutes']}", "start": start.isoformat(), "weeks": weeks,
                "saved": (today or start).isoformat(), "generated": True, "goal": "glutes", "level": who["level"],
                "days_per_week": len(layout), "why": why, "notes": notes}
    why = (f"{plural(len(layout), 'day')} a week as {split}. As {'an' if who['level'][0] in 'ai' else 'a'} {who['level']} lifter you get "
           f"{low} to {high} hard sets per big muscle a week, most sets stop about 2 reps short of failure, and week {deload if deload <= count else count} "
           f"is lighter so you recover." if who["goal"] != "aft" else
           f"{plural(len(layout), 'day')} a week covering deadlift, push-ups, sprint-drag-carry, plank and the two-mile run"
           + (", with an easy final week before the test." if taper else "."))
    return {"name": f"{plural(count, 'week')} for {GOALS[who['goal']]}", "start": start.isoformat(), "weeks": weeks,
            "saved": (today or start).isoformat(), "generated": True, "goal": who["goal"], "level": who["level"],
            "days_per_week": len(layout), "why": why, "notes": notes}


# ---- names, substitution ---------------------------------------------------------------------------------------------
_ALIAS = {"bench": "bench_press", "bench press": "bench_press", "incline": "incline_press", "incline press": "incline_press",
          "deadlift": "deadlift", "deadlifts": "deadlift", "trap bar": "deadlift", "rdl": "rdl", "romanian": "rdl",
          "squat": "squat", "squats": "squat", "goblet": "squat", "split squat": "bulgarian_split_squat", "bulgarian": "bulgarian_split_squat",
          "lunge": "lunge", "lunges": "lunge", "overhead press": "overhead_press", "shoulder press": "overhead_press",
          "military press": "overhead_press", "ohp": "overhead_press", "row": "row", "rows": "row", "bent over row": "row",
          "chest supported row": "chest_supported_row", "pull up": "pull_up", "pull ups": "pull_up", "pullup": "pull_up",
          "pullups": "pull_up", "chin up": "pull_up", "pulldown": "pull_up", "lat pulldown": "pull_up", "dip": "dip", "dips": "dip",
          "push up": "hand_release_pushup", "push ups": "hand_release_pushup", "pushup": "hand_release_pushup",
          "pushups": "hand_release_pushup", "fly": "chest_fly", "flies": "chest_fly", "flyes": "chest_fly", "crossover": "cable_crossover",
          "lateral raise": "lateral_raise", "lateral raises": "lateral_raise", "face pull": "face_pull", "face pulls": "face_pull",
          "rear delt": "rear_delt_fly", "curl": "curl", "curls": "curl", "pushdown": "pushdown", "pushdowns": "pushdown",
          "triceps extension": "overhead_triceps", "calf raise": "calf_raise", "calf raises": "calf_raise", "plank": "plank",
          "side plank": "side_plank", "dead bug": "dead_bug", "leg raise": "hanging_leg_raise", "leg raises": "hanging_leg_raise",
          "hip thrust": "hip_thrust", "hip thrusts": "hip_thrust", "glute bridge": "glute_bridge", "swing": "kettlebell_swing",
          "swings": "kettlebell_swing", "farmer carry": "farmer_carry", "farmers carry": "farmer_carry", "carry": "farmer_carry",
          "glute bridges": "glute_bridge",
          "step up": "step_up", "step ups": "step_up", "step-up": "step_up", "step-ups": "step_up", "stepups": "step_up",
          "kickback": "glute_kickback", "kickbacks": "glute_kickback", "glute kickback": "glute_kickback",
          "glute kickbacks": "glute_kickback", "donkey kick": "glute_kickback", "donkey kicks": "glute_kickback",
          "abduction": "hip_abduction", "abductions": "hip_abduction", "hip abduction": "hip_abduction",
          "hip abductions": "hip_abduction"}


def key_of(name) -> str:
    """A spoken or written exercise name -> its library key ('' when it is not a lift the engine knows)."""
    said = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower().replace("-", " ")).strip()
    said = re.sub(r"\s+", " ", re.sub(r"^(?:the|my|some|a|an)\s+", "", said))
    if not said:
        return ""
    if said.replace(" ", "_") in TAGS:
        return said.replace(" ", "_")
    for key, t in TAGS.items():
        if said == re.sub(r"[^a-z0-9 ]+", " ", t["name"].lower().replace("-", " ")).strip():
            return key
    for alias in sorted(_ALIAS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", said):
            return _ALIAS[alias]
    return ""


def substitute(key: str, have: set, taken=(), avoid=()) -> tuple[str, str] | None:
    """(new key, reason): same movement and main muscle first, then the same main muscle, among what he can do."""
    t = TAGS.get(key)
    if not t:
        return None
    pool = [k for k in TAGS if k != key and k not in taken and can_do(k, have) and not _avoided(k, avoid) and TAGS[k]["mechanic"] != "cardio"]
    same = [k for k in pool if TAGS[k]["pattern"] == t["pattern"] and set(TAGS[k]["primary"]) & set(t["primary"])]
    near = [k for k in pool if TAGS[k]["primary"][:1] == t["primary"][:1] and k not in same]
    for group, how in ((same, "the same movement"), (near, "the same main muscle")):
        if group:
            pick = sorted(group, key=lambda k: (TAGS[k]["mechanic"] != t["mechanic"], TAGS[k]["primary"] != t["primary"]))[0]
            return pick, (f"{TAGS[pick]['name']} trains {how} ({' and '.join(t['primary'][:2])}), so the sets, reps and rest stay as planned.")
    return None


# ---- progression ----------------------------------------------------------------------------------------------------
def e1rm(weight, reps, rir=0) -> float | None:
    """Estimated one-rep max: Epley on reps to failure (reps + reps in reserve). Only for sets of 1 to 10 reps."""
    try:
        weight, reps, rir = float(weight or 0), int(reps or 0), max(0, min(5, int(rir or 0)))
    except (TypeError, ValueError):
        return None
    if weight <= 0 or not 1 <= reps <= 10:
        return None
    if reps == 1 and rir == 0:
        return round(weight, 1)
    return round(weight * (1 + (reps + rir) / 30), 1)


def exposures(sets: list, key: str) -> list[list[dict]]:
    """His logged sets of one exercise, grouped per training day, oldest first."""
    days: dict[str, list] = {}
    for s in sets:
        if (s.get("key") or key_of(s.get("exercise"))) == key and s.get("reps"):
            days.setdefault(str(s.get("date")), []).append(s)
    return [days[d] for d in sorted(days)]


def _round5(x: float) -> float:
    return max(5.0, round(x / 5) * 5)


def _w(x) -> str:
    return f"{x:g}"


def describe(day_sets: list) -> str:
    """'135 x 10, 9, 8' / '12, 10 reps'."""
    loads = {float(s.get("weight") or 0) for s in day_sets}
    reps = ", ".join(str(s["reps"]) for s in day_sets)
    return f"{_w(loads.pop())} x {reps}" if len(loads) == 1 and 0 not in loads else \
        ", ".join(f"{_w(float(s.get('weight') or 0))} x {s['reps']}" if s.get("weight") else str(s["reps"]) for s in day_sets) + \
        ("" if any(s.get("weight") for s in day_sets) else " reps")


def next_target(item: dict, history: list[list[dict]]) -> dict:
    """Double progression with reps in reserve: add reps inside the range, then load; step back when it stalls.
    -> {"load", "reps", "mode", "why", "last"}; load None means bodyweight or nothing logged yet."""
    lo, hi = int(item.get("lo") or 0), int(item.get("hi") or 0)
    key = item.get("key") or key_of(item.get("exercise"))
    name = item.get("exercise") or TAGS.get(key, {}).get("name", "this lift")
    step = int(item.get("step") or 0) or (10 if TAGS.get(key, {}).get("lower") else 5)
    if item.get("kind") not in (None, "lift") or not hi:
        return {"load": None, "reps": item.get("reps", ""), "mode": "fixed", "why": "", "last": describe(history[-1]) if history else ""}
    if not history:
        free = "bodyweight" in TAGS.get(key, {}).get("gear", ()) and item.get("step") == 5
        return {"load": None, "reps": f"{lo}-{hi}", "mode": "start", "last": "",
                "why": (f"No {name.lower()} logged yet: start with no weight or a light one, and stop at {lo} to {hi} clean reps." if free else
                        f"No {name.lower()} logged yet: pick a load you could lift about {lo + 2} times and stop at {lo} to {hi}.")}
    last = history[-1]
    load = max(float(s.get("weight") or 0) for s in last)
    top = [s for s in last if float(s.get("weight") or 0) == load]
    reps = [int(s["reps"]) for s in top]
    rirs = [int(s["rir"]) for s in top if s.get("rir") not in (None, "")]
    out = {"last": describe(last), "reps": f"{lo}-{hi}"}
    if not load:                                              # bodyweight: reps are the only dial
        goal = min(reps) + 1
        if goal > hi and item.get("step") == 5:               # the glutes plan: the top of the range with no weight is the cue to make it harder
            return {**out, "load": None, "mode": "harder", "reps": f"{lo}-{hi}",
                    "why": f"Last time you got {describe(last)}: every set reached {hi}. Make it harder now (hold a weight, add a band, "
                           f"or lower for 3 seconds) and work back up from {lo}."}
        return {**out, "load": None, "mode": "add_reps", "reps": f"{goal}+" if goal > hi else f"{max(lo, goal)}-{hi}",
                "why": f"Last time you got {describe(last)}; add one rep to each set."}
    if item.get("gate") == "rir":
        return {**out, **_rir_gate(item, history, load, top, name, step, lo, hi)}
    failed = any(s.get("failed") for s in last)
    same = [h for h in history[-3:] if max(float(s.get("weight") or 0) for s in h) == load]
    totals = [sum(int(s["reps"]) for s in h if float(s.get("weight") or 0) == load) / max(1, len([s for s in h if float(s.get("weight") or 0) == load]))
              for h in same]
    falling = len(totals) >= 3 and totals[-1] < totals[-2] < totals[-3]
    flat = len(totals) >= 3 and totals[-1] <= totals[-3] and min(reps) < hi
    if failed or falling or flat:
        new = _round5(load * 0.9)
        what = "you missed a rep" if failed else "your reps fell two sessions in a row" if falling else \
            f"{name.lower()} has not moved in {plural(len(totals), 'session')}"
        return {**out, "load": new, "mode": "reset", "reps": f"{lo}-{hi}",
                "why": f"{what[0].upper() + what[1:]} at {_w(load)}, so I dropped it to {_w(new)} to rebuild with clean reps."}
    if min(reps) >= hi or (rirs and min(rirs) >= 4 and min(reps) >= lo):
        new = load + step
        because = f"you hit {hi} on every set" if min(reps) >= hi else "you had four or more reps left on every set"
        return {**out, "load": new, "mode": "add_load", "reps": f"{lo}-{hi}",
                "why": f"Last time {because} at {_w(load)}, so the load goes up to {_w(new)}."}
    if min(reps) < lo:
        new = _round5(load * 0.95)
        return {**out, "load": new if new < load else load - 5, "mode": "ease",
                "why": f"{_w(load)} only gave you {min(reps)}, under the {lo} to {hi} range, so it comes down a little."}
    return {**out, "load": load, "mode": "add_reps",
            "why": f"Same {_w(load)} as last time ({describe(last)}); beat it by a rep until every set reaches {hi}."}


def _floor5(x: float) -> float:
    return max(5.0, math.floor(x / 5) * 5)


def _rir_gate(item: dict, history: list, load: float, top: list, name: str, step: int, lo: int, hi: int) -> dict:
    """The reps-in-reserve gate (items with gate "rir", the Captain America program). Load goes up only when every top
    set reached the target reps AND the fewest reps he had left was at least the target; a missing RIR is unknown, so
    the load holds and he is asked for it. One rep short of the target RIR holds. A failed set, or 0 in reserve on two
    sessions when the target is 2 or more, takes 5-10% off. When the rep target changed since the last session (5s to
    3s), the new load comes from that session's estimated max for the new reps and target RIR, rounded down to 5 lb and
    never more than 5% over the last top load."""
    target = int(item.get("rir") if item.get("rir") is not None else 2)
    reps = [int(s["reps"]) for s in top]
    rirs = [s.get("rir") for s in top]
    known = [int(r) for r in rirs if r not in (None, "")]
    lname = name.lower()
    if any(s.get("failed") for s in top) or any(s.get("failed") for s in history[-1]):
        new = _floor5(load * 0.9)
        return {"load": new, "mode": "reset", "why": f"You missed a rep at {_w(load)}, so it comes down to {_w(new)} to rebuild with clean reps."}
    was = (top[0].get("target_lo"), top[0].get("target_hi"))
    if was[1] not in (None, "") and (int(was[0] or 0), int(was[1])) != (lo, hi):
        best = max((e1rm(s.get("weight"), s.get("reps"), s.get("rir")) or 0) for s in top)
        if best:
            raw = best / (1 + (lo + target) / 30)
            cap = _floor5(load * 1.05)
            new = min(_floor5(raw), cap)
            capped = " (capped at 5% over your last top load)" if _floor5(raw) > cap else ""
            return {"load": new, "mode": "retarget",
                    "why": f"The target changed from {was[1] if was[0] == was[1] else f'{was[0]}-{was[1]}'} to {lo if lo == hi else f'{lo}-{hi}'} reps. "
                           f"From your last session's estimated max of about {round(best)} pounds, {lo} reps with {target} in reserve is "
                           f"{_w(new)}{capped}."}
    if target >= 2:
        zero = [h for h in history[-2:] if any(s.get("rir") not in (None, "") and int(s["rir"]) == 0 for s in h)]
        if len(history) >= 2 and len(zero) == 2:
            new = _floor5(load * 0.95)
            return {"load": new, "mode": "reset",
                    "why": f"Two sessions in a row went to 0 reps in reserve when the target is {target}, so {lname} comes down to {_w(new)}."}
    if min(reps) < lo:
        new = _floor5(load * 0.95)
        return {"load": new if new < load else load - 5, "mode": "ease",
                "why": f"{_w(load)} only gave you {min(reps)}, under the {lo} you need, so it comes down a little."}
    if min(reps) < hi:
        return {"load": load, "mode": "add_reps",
                "why": f"Same {_w(load)} as last time; add a rep until every set reaches {hi} with {target} in reserve."}
    if len(known) < len(rirs):
        return {"load": load, "mode": "hold",
                "why": f"You hit the reps at {_w(load)}, but I don't know how many you had left on every set. Same load; tell me your reps "
                       "in reserve on each set and I can add weight next time."}
    low = min(known)
    if low >= target:
        new = load + step
        return {"load": new, "mode": "add_load",
                "why": f"Every set reached {hi} with at least {target} in reserve at {_w(load)}, so the load goes up to {_w(new)}."}
    if low == target - 1:
        return {"load": load, "mode": "hold",
                "why": f"You hit the reps at {_w(load)} but with {low} in reserve, one short of the {target} I want, so the load stays."}
    return {"load": load, "mode": "hold",
            "why": f"You hit the reps at {_w(load)} with only {low} in reserve against a target of {target}, so the load stays until it gets easier."}


# ---- readiness, deloads, missed sessions ----------------------------------------------------------------------------
def upfirst(text: str) -> str:
    """Capital first letter only (str.capitalize would also lower 'Garmin')."""
    return text[:1].upper() + text[1:]


def low_signs(checkin: dict | None) -> list[str]:
    """What in a morning check-in says 'go easier', in his words."""
    c = checkin or {}
    out = []
    if c.get("sleep_hours") and c["sleep_hours"] < 6:
        out.append(f"you slept {plural(c['sleep_hours'], 'hour')}")
    if c.get("soreness") and c["soreness"] >= 4:
        out.append(f"soreness is {c['soreness']} out of 5")
    if c.get("energy") and c["energy"] <= 2:
        out.append(f"energy is {c['energy']} out of 5")
    if c.get("readiness") is not None and c["readiness"] < 40:
        out.append(f"readiness is {c['readiness']}")
    if c.get("rhr_above") and c["rhr_above"] >= 7:           # only a check-in measured by his watch carries this
        out.append(f"your resting heart rate is {plural(c['rhr_above'], 'beat')} above your usual")
    if out and c.get("source") == "garmin":                  # no check-in today: tools/pca_health used his watch's night
        out[-1] += " (from your Garmin)"
    return out


def deload_due(sets: list, checkins: dict, today: date) -> tuple[bool, str]:
    """A lighter week ahead of schedule when two or more fatigue signs appear inside two weeks."""
    since = (today - timedelta(days=14)).isoformat()
    recent = [s for s in sets if since <= str(s.get("date")) <= today.isoformat()]
    signs = []
    fails = sum(1 for s in recent if s.get("failed"))
    if fails >= 2:
        signs.append(f"{plural(fails, 'missed set')}")
    for key in {s.get("key") for s in recent if s.get("key")}:
        if TAGS.get(key, {}).get("mechanic") != "compound":
            continue
        best = [max((e1rm(s.get("weight"), s.get("reps"), s.get("rir")) or 0) for s in day) for day in exposures(recent, key)]
        best = [b for b in best if b]
        if len(best) >= 3 and best[-1] < best[-2] < best[-3]:
            signs.append(f"{TAGS[key]['name'].lower()} strength slipping three sessions running")
            break
    low = sum(1 for d, c in (checkins or {}).items() if since <= d <= today.isoformat() and low_signs(c))
    if low >= 3:
        signs.append(f"{plural(low, 'low-recovery morning')}")
    if len(signs) >= 2:
        return True, f"I'm making this a lighter week: {' and '.join(signs[:2])} in the last two weeks say you need the recovery."
    return False, ""


def _week_of(plan: dict, day: date):
    try:
        start = date.fromisoformat(str((plan or {}).get("start")))
    except ValueError:
        return None, "none", None
    weeks = (plan or {}).get("weeks") or []
    if not weeks:
        return None, "none", start
    idx = (day - start).days
    if idx < 0:
        return None, "before", start
    wk = next((w for w in weeks if int(w.get("week", 0) or 0) == idx // 7 + 1), None)
    return wk, "ok" if wk else "ended", start


def _planned(week: dict, name: str):
    return next((d for d in week.get("days", []) if str(d.get("day", "")).lower().startswith(name.lower()) and d.get("items")), None)


_FULL = dict(zip(DAYS, ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))


def schedule_week(week: dict, monday: date, done: set, today: date, counted_from: date | None = None,
                  priority: dict | None = None) -> tuple[list, list]:
    """The week as it will really run. A planned session with nothing logged on a day already gone moves to the next
    free day; it is never stacked on another session, and it is dropped if the week has no room left.
    priority: session id -> rank (lower first) when several missed sessions wait for one free day; default oldest first.
    -> ([{date, day, session or None, moved_from}], [{kind, reason}])"""
    rows, queue, notes = [], [], []
    for n, name in enumerate(DAYS):
        day = monday + timedelta(days=n)
        own = _planned(week, name)
        session, moved = own, ""
        if own is None and queue and day >= today:
            if priority:
                queue.sort(key=lambda q: priority.get(q[0].get("session"), 99))
            session, moved = queue.pop(0)
            notes.append({"kind": "moved", "day": name, "reason": f"{_FULL[moved]}'s {session['title'].lower()} was not logged, so I moved it to "
                          f"{'today' if day == today else _FULL[name]} instead of stacking it on another session."})
        if day < today and own is not None and day.isoformat() not in done and (counted_from is None or day >= counted_from):
            queue.append((own, name))
            session = own
        rows.append({"date": day.isoformat(), "day": name, "session": session, "moved_from": moved,
                     "done": day.isoformat() in done, "missed": day < today and own is not None and day.isoformat() not in done
                     and (counted_from is None or day >= counted_from)})
    for session, name in queue:
        notes.append({"kind": "dropped", "day": name, "reason": f"{_FULL[name]}'s {session['title'].lower()} was missed and the week has no free "
                      "day left, so it is dropped rather than crammed in; this week is a little lighter."})
    return rows, notes


def adapt_day(plan: dict, sets: list, checkins: dict, today: date, done: set | None = None, deload_until: str = "",
              priority: dict | None = None) -> dict:
    """Today's session as the coach would run it, and why it differs from the plan.
    -> {"status": none|before|ended|rest|train, "week", "focus", "title", "why", "items", "adjustments": [{kind, reason}],
        "schedule": the reshuffled week, "deload": a reason when a reactive deload should start}"""
    wk, status, start = _week_of(plan, today)
    out = {"status": status, "adjustments": [], "items": [], "schedule": [], "title": "", "why": "", "week": None, "focus": ""}
    if wk is None:
        return out
    done = set(done or ()) | {str(s.get("date")) for s in sets}
    monday = today - timedelta(days=today.weekday())
    try:
        saved = date.fromisoformat(str(plan.get("saved") or plan.get("start")))
    except ValueError:
        saved = start
    rows, notes = schedule_week(wk, monday, done, today, max(saved, start), priority)
    out.update(week=wk.get("week"), focus=wk.get("focus", ""), schedule=rows)
    row = rows[today.weekday()]
    out["adjustments"] += [n for n in notes if n["kind"] == "dropped" or n["day"] == row["day"]]
    session = row["session"]
    if not session:
        out["status"] = "rest"
        out["title"] = "Rest and recovery"
        return out
    out.update(status="train", title=session.get("title", ""), why=session.get("why", ""))
    items = [dict(i) for i in session.get("items", [])]

    def trim(by: int = 1, halve: bool = False):
        for it in items:
            n = _sets(it)
            if n > 1:
                it["planned_sets"] = it.get("planned_sets") or it["sets"]
                it["sets"] = str(max(1, math.ceil(n / 2) if halve else n - by))

    planned_light = bool(re.search(r"deload|taper", str(wk.get("focus", "")), re.I))
    due, why = (False, "") if planned_light else deload_due(sets, checkins, today)
    in_deload = bool(deload_until and today.isoformat() <= deload_until)
    if (due or in_deload) and not planned_light:
        trim(halve=True)
        out["adjustments"].append({"kind": "deload", "reason": why or "This is a lighter week: half the sets at the same loads, "
                                   "because the last two weeks showed you needed recovery."})
        if due and not in_deload:
            out["deload"] = why
    logged = sorted(d for d in done if d < today.isoformat())
    gap = (today - date.fromisoformat(logged[-1])).days if logged else 0
    back = gap >= 7 and not (due or in_deload)
    if back:
        trim()
        out["adjustments"].append({"kind": "reentry", "reason": f"It has been {plural(gap, 'day')} since your last session, so today is "
                                   "one set lighter per exercise at about 90 percent of your last loads."})
    signs = low_signs((checkins or {}).get(today.isoformat()))
    if signs and not (due or in_deload or back or planned_light):
        trim()
        cut = ""
        if len(signs) >= 2:
            extra = [i for i in items if TAGS.get(i.get("key") or "", {}).get("mechanic") == "isolation"]
            if extra and len(items) > 2:
                items.remove(extra[-1])
                cut = f" and dropped {extra[-1]['exercise'].lower()}"
        out["adjustments"].append({"kind": "readiness", "reason": f"{upfirst(' and '.join(signs[:2]))}, so I took one set off each "
                                   f"exercise{cut} and kept the loads."})
    for it in items:
        key = it.get("key") or key_of(it.get("exercise"))
        if not key or (it.get("kind") or "lift") != "lift" and not it.get("hi"):
            continue
        if not it.get("hi"):                                  # a plan the model wrote: read its rep range
            found = re.match(r"\s*(\d+)\s*(?:-|to)\s*(\d+)\s*$", str(it.get("reps", "")))
            if not found or TAGS.get(key, {}).get("mechanic") not in ("compound", "isolation"):
                continue
            it.update(lo=int(found.group(1)), hi=int(found.group(2)), key=key, kind="lift")
        t = next_target(dict(it, key=key), exposures(sets, key))
        if back and t.get("load"):
            t["load"] = _round5(t["load"] * 0.9)
        it.update(load=t["load"], last=t["last"], note=t["why"], mode=t["mode"])
        if t["mode"] in ("reset", "ease", "add_load"):
            out["adjustments"].append({"kind": "load", "exercise": it["exercise"], "reason": t["why"]})
    out["items"] = items
    return out


# ---- progress --------------------------------------------------------------------------------------------------------
def progress(sets: list, today: date, weeks: int = 8) -> dict:
    """{"lifts": [{key, name, points: [[date, e1rm]], now, change}], "volume": {muscle: sets this week}, "weeks": [...]}"""
    lifts = []
    for key in sorted({s.get("key") for s in sets if s.get("key") in TAGS}):
        points = []
        for day in exposures(sets, key):
            best = max((e1rm(s.get("weight"), s.get("reps"), s.get("rir")) or 0) for s in day)
            if best:
                points.append([str(day[0].get("date")), best])
        if points:
            lifts.append({"key": key, "name": TAGS[key]["name"], "points": points[-12:], "now": points[-1][1],
                          "change": round(points[-1][1] - points[0][1], 1) if len(points) > 1 else 0})
    lifts.sort(key=lambda r: -len(r["points"]))
    monday = today - timedelta(days=today.weekday())
    history = []
    for back in range(weeks - 1, -1, -1):
        first = monday - timedelta(days=7 * back)
        last = first + timedelta(days=6)
        vol = dict.fromkeys(MUSCLES, 0.0)
        for s in sets:
            if first.isoformat() <= str(s.get("date")) <= last.isoformat() and s.get("key") in TAGS:
                for m in TAGS[s["key"]]["primary"]:
                    vol[m] += 1
                for m in TAGS[s["key"]]["secondary"]:
                    vol[m] += 0.5
        history.append({"week": first.isoformat(), "volume": vol, "sets": sum(1 for s in sets if first.isoformat() <= str(s.get("date")) <= last.isoformat())})
    return {"lifts": lifts, "volume": history[-1]["volume"], "weeks": history}
