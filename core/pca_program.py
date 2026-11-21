"""Project Captain America: the authored 12-week program, as data the trainer can run.

This is a written program, not a generator of random workouts. Every week, day and exercise below was chosen on
purpose; build() only fills the tables into the plan shape tools/fitness.py stores and tools/trainer.py guides
(weeks -> days -> items), adding what each prescription needs to be actionable: purpose, equipment, sets, reps,
intensity (reps in reserve, talk test or a tested pace), rest, technique cues, alternatives and the progression rule.

Design rules (docs/pca/PROGRAM.md has the evidence and the review):
  * Nothing is calculated from old results. Loads start from reps in reserve and then follow what he logs; run paces
    exist only after a time trial run inside the program (week 1 or 2), and until then runs go by talk test and RPE.
  * Three blocks: FOUNDATION (weeks 1-4), FORGE (5-8), ASCENSION (9-12). Weeks 4 and 8 are lighter weeks with a
    small test; week 12 is a taper into a full practice AFT.
  * Hard days are separated: heavy lower body Monday, hard run Wednesday, sprints Friday; the days between are
    upper-body or easy. Sunday is always off. Five-day and six-day layouts carry the same work.
  * At most two hard run sessions a week (one interval/tempo session, plus short sprints); the rest is easy.
  * The longest run never grows more than about a tenth over the longest run of the previous four weeks.

No model, no storage, no clock here: build() is a pure function of (profile answers, start date)."""
from datetime import date

from tools import fitness_library as lib

PROGRAM_ID = "pca-12w-v1"
NAME = "Project Captain America: 12-week program"

BLOCKS = (
    {"id": "foundation", "name": "Phase I - Foundation", "weeks": (1, 2, 3, 4),
     "goal": "Find honest starting loads and paces, rebuild the running habit, groove technique, prepare hamstrings, "
             "calves and tendons for sprinting and faster running."},
    {"id": "forge", "name": "Phase II - Forge", "weeks": (5, 6, 7, 8),
     "goal": "Build: heavier triples and fours, intervals at current two-mile pace, true sprint speed, more muscle-building volume."},
    {"id": "ascension", "name": "Phase III - Ascension", "weeks": (9, 10, 11, 12),
     "goal": "Turn the work into test-day performance: race-pace running, timed sprint-drag-carry, heavy low-volume lifting, "
             "then a taper and a full practice AFT."},
)
# week -> (mode, focus)
WEEKS = {
    1: ("calibrate", "Calibration: find starting loads, first baseline tests"),
    2: ("build", "Foundation: build the habit, two-mile baseline"),
    3: ("build", "Foundation: first tempo work, sprint speed rises"),
    4: ("deload", "Deload: about 60% of the sets, timed sprint-drag-carry"),
    5: ("build", "Forge: intervals at your tested two-mile pace"),
    6: ("build", "Forge: heavy triples, full-speed sprints"),
    7: ("build", "Forge: the biggest training week"),
    8: ("deload", "Deload and mid-point tests"),
    9: ("peak", "Ascension: race-pace running, heavy and low volume"),
    10: ("peak", "Ascension: sharpest week"),
    11: ("sharpen", "Sharpen: less volume, same intensity"),
    12: ("taper", "Taper: fresh for the practice AFT on Saturday"),
}
DELOAD = (4, 8)

# Run intensity zones. `factor` multiplies his CURRENT tested two-mile pace (seconds per mile); it is a guide only,
# and the talk test wins whenever the two disagree. No test on file -> the engine shows only RPE and the talk test.
ZONES = {
    "E": {"name": "Easy", "rpe": "3-4 out of 10", "talk": "full sentences, nose-breathing possible", "factor": (1.25, 1.38)},
    "S": {"name": "Steady", "rpe": "5 out of 10", "talk": "sentences, but you would rather not", "factor": (1.15, 1.22)},
    "T": {"name": "Tempo", "rpe": "6-7 out of 10", "talk": "short phrases only", "factor": (1.08, 1.12)},
    "I": {"name": "Two-mile pace", "rpe": "8-9 out of 10", "talk": "a word or two", "factor": (0.99, 1.03)},
    "R": {"name": "Fast and relaxed", "rpe": "9 out of 10, never straining", "talk": "none during the rep", "factor": (0.93, 0.96)},
}

# ---- why each exercise is here, what it needs, and what replaces it ------------------------------------------------------
# key -> (purpose, equipment, alternatives)
EX = {
    "box_jump": ("Explosive hip and knee extension with a soft landing: power without heavy fatigue.", "Sturdy box 20-30 in",
                 ["Broad jump", "Squat jump", "Kettlebell swing (if jumping hurts)"]),
    "broad_jump": ("Horizontal power for sprint starts and the sprint-drag-carry.", "Open floor or turf",
                   ["Box jump", "Kettlebell swing"]),
    "deadlift": ("The AFT deadlift itself: maximal strength of hips, legs, back and grip.", "Trap (hex) bar and plates",
                 ["Barbell deadlift", "Romanian deadlift (if the floor position hurts)"]),
    "back_squat": ("Leg strength through a full range: the base for sprinting, the sled drag and running economy.",
                   "Barbell and rack", ["Goblet squat", "Leg press", "Front squat"]),
    "rdl": ("Hamstrings and glutes trained long under load: muscle, and protection for sprinting.", "Barbell or dumbbells",
            ["Dumbbell Romanian deadlift", "Hip thrust", "45-degree back extension"]),
    "hanging_leg_raise": ("Trunk flexion strength and grip time; carries over to the plank.", "Pull-up bar",
                          ["Dead bug", "Lying leg raise"]),
    "bench_press": ("Pressing strength behind the hand-release push-up, and chest mass.", "Barbell, bench, rack",
                    ["Dumbbell bench press", "Incline dumbbell press", "Weighted push-up"]),
    "pull_up": ("Lat and upper-back strength and width (the V-taper); balances all the pressing.", "Pull-up bar or lat pulldown",
                ["Lat pulldown", "Band-assisted pull-up", "Inverted row"]),
    "incline_press": ("Upper chest, the weak point named in the profile, loaded in the stretched position.",
                      "Adjustable bench and dumbbells", ["Incline barbell press", "Deficit push-up", "Low-to-high cable fly"]),
    "chest_supported_row": ("Mid-back muscle without loading a lower back that already deadlifts and runs.",
                            "Incline bench and dumbbells", ["Seated cable row", "Bent-over row", "Inverted row"]),
    "lateral_raise": ("Side delts: shoulder width.", "Light dumbbells or a cable", ["Cable lateral raise", "Band lateral raise"]),
    "curl": ("Biceps, trained in the stretch.", "Dumbbells and an incline bench", ["Standing dumbbell curl", "Cable curl"]),
    "overhead_triceps": ("Triceps long head; lockout strength for push-ups.", "Cable or dumbbell", ["Triceps pushdown", "Close-grip push-up"]),
    "plank": ("The AFT plank: anti-extension endurance, practised as several good holds rather than one all-out one.", "Floor",
              ["Long-lever plank (harder)", "Plank from the knees (easier)", "Dead bug"]),
    "hand_release_pushup": ("The AFT push-up, practised fresh and submaximal so volume is high and form stays test-legal.", "Floor",
                            ["Push-up from an incline (easier)", "Bench press (if the wrists hurt)"]),
    "side_plank": ("Lateral trunk endurance for carries, shuffles and running posture.", "Floor", ["Suitcase carry", "Pallof press"]),
    "dead_bug": ("Bracing with the limbs moving: the skill under running and the plank.", "Floor", ["Bird dog", "Hollow hold"]),
    "sprint": ("Maximal-speed and acceleration work: the fastest way to a better sprint-drag-carry, and it makes two-mile pace feel slower.",
               "Track, turf or a flat field", ["Hill sprints (kinder to hamstrings)", "Sled push", "Air bike 8 s sprints (if running hurts)"]),
    "sdc": ("The sprint-drag-carry itself: turns, sled drag, lateral shuffle and kettlebell carry rehearsed at rising effort.",
            "25 m lane, 90 lb sled with straps, two 40 lb kettlebells", ["No sled: drag a loaded duffel or do backward lunges 2 x 25 m",
                                                                        "No kettlebells: 40 lb dumbbells"]),
    "bulgarian_split_squat": ("Single-leg strength and hip stability: evens out the legs for running and turning.",
                              "Bench and dumbbells", ["Reverse lunge", "Step-up", "Walking lunge"]),
    "nordic_curl": ("Eccentric hamstring strength, the best-supported exercise for preventing hamstring strains in sprinting.",
                    "A partner, a loaded bar or a Nordic bench to hold the ankles", ["Slider leg curl (eccentric only)",
                                                                                   "Single-leg Romanian deadlift", "Hamstring curl machine, slow lowering"]),
    "hip_thrust": ("Glute strength at full hip extension: sprint drive and deadlift lockout.", "Bench and barbell or dumbbell",
                   ["Single-leg glute bridge", "Kettlebell swing"]),
    "farmer_carry": ("Grip, traps and trunk under load: the carry leg of the sprint-drag-carry and general load carriage.",
                     "Heavy dumbbells, kettlebells or a trap bar", ["Kettlebell carry", "Trap-bar carry", "Dead hang 30-45 s (if no space)"]),
    "calf_raise": ("Calf and Achilles capacity, the tissue that complains first when running comes back.", "A step; dumbbell optional",
                   ["Single-leg calf raise", "Seated calf raise"]),
    "copenhagen_plank": ("Adductor strength for the lateral shuffle and hard turns; lowers groin-strain risk.", "Bench",
                         ["Short-lever Copenhagen (knee on the bench)", "Side-lying adduction", "Side plank"]),
    "pallof_press": ("Anti-rotation trunk strength for carries and sprint posture.", "Cable or band", ["Suitcase carry", "Side plank"]),
    "dip": ("Lower chest and triceps through a long range.", "Dip bars", ["Deficit push-up", "Decline dumbbell press", "Close-grip bench press"]),
    "cable_crossover": ("Upper-chest isolation with constant tension: chest definition without more joint load.", "Cables",
                        ["Incline dumbbell fly", "Band fly"]),
    "face_pull": ("Rear delts and shoulder external rotators: keeps the shoulders healthy under the pressing volume.", "Cable rope or band",
                  ["Band pull-apart", "Rear-delt fly"]),
    "overhead_press": ("Shoulder strength and mass; overhead stability.", "Barbell or dumbbells", ["Dumbbell shoulder press", "Landmine press"]),
    "row": ("Back thickness and hinge posture.", "Barbell or dumbbells", ["Chest-supported row", "Seated cable row"]),
    "pushdown": ("Triceps; elbow-friendly volume.", "Cable or band", ["Overhead triceps extension", "Close-grip push-up"]),
    "run": ("See the session purpose.", "Running shoes; track, road or treadmill", ["Treadmill at 1% incline", "Bike or rower for the same minutes and effort (if running hurts)"]),
}


# Lifts loaded with bodyweight (added weight only when the progression says so); holds are always bodyweight.
BODYWEIGHT = ("pull_up", "dip", "nordic_curl", "hanging_leg_raise", "hand_release_pushup")


def _lift(key, sets, lo, hi, rir, rest_s, pri=1, name="", note="", each=False, tempo=""):
    step = "10 lb" if key in ("deadlift", "back_squat", "rdl", "hip_thrust") else "5 lb (or the smallest jump you have)"
    reps = (str(lo) if lo == hi else f"{lo}-{hi}") + (" each side" if each else "")
    body = key in BODYWEIGHT
    if body:
        rule = f"All sets at {hi} clean reps: add a rep per set next time; past {hi + 2}, add load (belt, vest or dumbbell)."
    elif lo == hi:
        rule = f"Every set at {lo} with at least {rir} in reserve: add {step} next time. Fewer in reserve: repeat the load. A missed rep: take 5-10% off."
    else:
        rule = f"Add reps until every set reaches {hi} with {rir} in reserve, then add {step} and go back to {lo}."
    return {"key": key, "name": name, "kind": "lift", "sets": sets, "lo": lo, "hi": hi, "reps": reps, "rir": rir, "rest_s": rest_s,
            "target": f"{rir} reps in reserve" + (f", tempo {tempo}" if tempo else ""), "tempo": tempo, "progression": rule,
            "priority": pri, "gate": "rir", "notes": note}


def _hold(key, sets, seconds, rest_s, pri=1, pct=None, note="", each=False):
    """A timed hold. pct: a share of his tested max plank, which the engine turns into seconds once a test is on file."""
    return {"key": key, "kind": "hold", "sets": sets, "lo": seconds[0], "hi": seconds[1],
            "reps": f"{seconds[0]}-{seconds[1]} s" + (" each side" if each else ""), "rir": None, "rest_s": rest_s,
            "target": "stop when the hips move, not when the clock says", "priority": pri, "notes": note,
            "progression": "Add 5-10 s per hold each week while the line from head to heels stays straight.",
            **({"pct_of": "plk", "pct": pct} if pct else {})}


def _power(key, sets, reps, rest_s=75, note=""):
    return {"key": key, "kind": "power", "sets": sets, "lo": reps, "hi": reps, "reps": str(reps), "rir": None, "rest_s": rest_s,
            "target": "maximal intent, full recovery, stop if height or distance drops", "priority": 1, "notes": note,
            "progression": "Jump higher or farther, never more often: quality is the whole point."}


def _carry(sets, metres, note):
    return {"key": "farmer_carry", "kind": "carry", "sets": sets, "lo": 0, "hi": 0, "reps": f"{metres} m", "rir": None, "rest_s": 75,
            "target": "heavy, tall posture, no leaning", "priority": 2, "notes": note,
            "progression": "Add load when every carry is finished without setting the weights down."}


def _run(kind, title, purpose, segments, warm=0, cool=0, note="", pri=1, builds=""):
    """One run. segments: [{"reps", "min" or "m", "zone", "rest_s", "rest": words}]. Minutes for distance work are an
    estimate at about 9:30 per mile so the session length is honest for a slower day; the engine re-times them from a test."""
    def minutes(seg):
        work = seg.get("min") or seg["m"] / 1609.34 * 9.5 * (0.9 if seg["zone"] in ("R",) else 1.0)
        return seg.get("reps", 1) * work + max(0, seg.get("reps", 1) - 1) * seg.get("rest_s", 0) / 60
    total = warm + cool + sum(minutes(s) for s in segments)
    hard = sum(s.get("reps", 1) * (s.get("min") or s["m"] / 1609.34 * 9.5) for s in segments if s["zone"] in ("T", "I", "R", "TT"))
    lines = []
    for s in segments:
        what = f"{s['min']} min" if s.get("min") else f"{s['m']} m"
        z = ZONES.get(s["zone"])
        how = f"{z['name'].lower()} ({z['rpe']}; {z['talk']})" if z else "a hard, even effort you can hold to the finish"
        rest = f", {s['rest']}" if s.get("rest") else ""
        lines.append(f"{s['reps']} x {what} at {how}{rest}" if s.get("reps", 1) > 1 else f"{what} {how}{rest}")
    return {"key": "run", "name": title, "kind": "cardio", "sets": 1, "lo": 0, "hi": 0, "reps": f"{round(total)} min", "rir": None,
            "rest_s": 0, "target": "; ".join(lines), "priority": pri, "notes": note,
            "progression": builds or "Volume grows by the plan only when last week's runs were done and nothing hurts.",
            "run": {"type": kind, "purpose": purpose, "warm_min": warm, "cool_min": cool, "segments": segments,
                    "total_min": round(total, 1), "hard_min": round(hard, 1)}}


def _field(kind, title, sets, reps, target, rest_s, note="", progression=""):
    return {"key": kind, "name": title, "kind": "cardio", "sets": sets, "lo": 0, "hi": 0, "reps": reps, "rir": None, "rest_s": rest_s,
            "target": target, "priority": 1, "notes": note, "progression": progression or "Effort rises week by week before volume does."}


def _test(event, title, how, note=""):
    return {"key": {"mdl": "deadlift", "hrp": "hand_release_pushup", "plk": "plank", "sdc": "sdc", "run": "run"}[event], "name": title,
            "kind": "test", "test": event, "sets": 1, "lo": 0, "hi": 0, "reps": "1 test", "rir": None, "rest_s": 0, "target": how,
            "priority": 1, "notes": note, "progression": "This result resets your targets. It is recorded as a real result, never estimated."}


# ---- the week-by-week tables ----------------------------------------------------------------------------------------------
# Trap-bar deadlift: (sets, reps, reps in reserve). Volume is modest on purpose: his old three-rep max (340 lb) was already
# within a few points of the 100-point mark (350 lb, male 22-26), so the deadlift needs holding and a little more, not a rebuild.
_DL = {1: (3, 5, 4), 2: (4, 5, 3), 3: (4, 5, 2), 4: (2, 5, 4), 5: (4, 4, 2), 6: (5, 3, 2), 7: (5, 3, 1), 8: (2, 3, 4),
       9: (4, 3, 1), 10: (3, 3, 1), 11: (3, 2, 2), 12: (2, 2, 4)}
_SQ = {1: (3, 6, 4), 2: (3, 6, 3), 3: (3, 6, 2), 4: (2, 6, 4), 5: (3, 5, 2), 6: (3, 5, 2), 7: (3, 5, 2), 8: (2, 5, 4),
       9: (3, 4, 2), 10: (3, 4, 2), 11: (2, 4, 3), 12: (2, 3, 4)}
_RDL = {1: (2, 8, 4), 2: (3, 8, 3), 3: (3, 8, 3), 4: (2, 8, 4), 5: (3, 8, 2), 6: (3, 8, 2), 7: (3, 8, 2), 8: (2, 8, 4),
        9: (2, 8, 2), 10: (2, 8, 2), 11: (2, 6, 3), 12: None}
_JUMP = {1: ("box_jump", 3), 2: ("box_jump", 4), 3: ("box_jump", 4), 4: ("box_jump", 2), 5: ("broad_jump", 4), 6: ("broad_jump", 4),
         7: ("broad_jump", 4), 8: ("broad_jump", 2), 9: ("box_jump", 3), 10: ("box_jump", 3), 11: ("box_jump", 2), 12: ("box_jump", 2)}
# Bench press: (sets, low rep, high rep, reps in reserve)
_BP = {1: (3, 6, 8, 3), 2: (4, 6, 8, 2), 3: (4, 6, 8, 2), 4: (2, 6, 8, 4), 5: (4, 5, 7, 2), 6: (4, 5, 7, 2), 7: (4, 5, 7, 2),
       8: (2, 5, 7, 4), 9: (4, 4, 6, 2), 10: (4, 4, 6, 1), 11: (3, 4, 6, 2), 12: (2, 5, 5, 4)}
# Minutes of easy running after Tuesday's lifting, and Saturday's long easy run (0 = a test replaces it)
_TUE_RUN = {1: 12, 2: 15, 3: 15, 4: 12, 5: 18, 6: 20, 7: 20, 8: 15, 9: 20, 10: 20, 11: 15, 12: 15}
_LONG = {1: 28, 2: 0, 3: 30, 4: 26, 5: 33, 6: 36, 7: 39, 8: 0, 9: 40, 10: 43, 11: 34, 12: 0}
# Plank holds: share of his tested max per hold, by block
_PLK = {"foundation": 0.6, "forge": 0.7, "ascension": 0.8}
# Hand-release push-up practice on Wednesday: (sets, share of tested two-minute max, rest seconds)
_HRP = {2: (4, 0.5, 60), 3: (5, 0.5, 60), 4: (3, 0.5, 60), 5: (5, 0.55, 60), 6: (5, 0.6, 60), 7: (6, 0.6, 60),
        9: (4, 0.65, 60), 10: (3, 0.7, 90), 11: (3, 0.6, 60), 12: (2, 0.4, 60)}
# Nordic curls: (sets, reps). Starts very low: the soreness from this exercise is real.
_NORDIC = {1: (2, 3), 2: (2, 4), 3: (3, 4), 4: (2, 3), 5: (3, 5), 6: (3, 5), 7: (3, 6), 8: (2, 4), 9: (3, 5), 10: (2, 5), 11: (2, 4)}
# Friday speed: (sprint reps, metres, effort, rest seconds), then (sprint-drag-carry reps, effort, rest seconds)
_SPEED = {
    1: ((6, 40, "build-ups to about 80%: smooth, tall, relaxed", 75), (2, "a walk-through at 60-70%: learn the turns, the drag and the carry", 150)),
    2: ((6, 40, "85-90%", 90), (2, "75%", 180)),
    3: ((4, 30, "95%, from a two-point start", 120), (2, "85%", 240)),
    4: ((3, 30, "90%", 120), None),
    5: ((5, 30, "95-100%", 120), (2, "90%", 240)),
    6: ((6, 30, "maximal", 150), (2, "90-95%", 240)),
    7: ((4, 40, "maximal", 180), (3, "two at 90%, the last at 95% or more", 300)),
    9: ((4, 30, "maximal", 150), (3, "test effort", 300)),
    10: ((4, 30, "maximal", 150), (2, "all-out", 360)),
    11: ((3, 30, "95%", 150), (1, "95%", 0)),
}
_EASY_WARM = [{"phase": "Raise", "items": [{"exercise": "run", "dose": "3 min brisk walk, then start the run slower than you think you need"}]},
              {"phase": "Activate & mobilize", "items": [{"exercise": "leg_swing", "dose": "10 front-back + 10 side each leg"},
                                                         {"exercise": "calf_raise", "dose": "1 x 12 slow, bodyweight"}]}]


def _block(n):
    return next(b for b in BLOCKS if n in b["weeks"])


def _wed_run(n, ready):
    """Wednesday's quality run. Until a two-mile time trial is on file the hard work is by effort, not pace."""
    strides = {"reps": 4, "m": 80, "zone": "R", "rest_s": 60, "rest": "walk back"}
    if n == 1:
        return _run("easy", "Easy run and strides", "Re-introduce running and practise relaxed speed; nothing hard yet.",
                    [{"min": 20, "zone": "E"}, strides], note="Walk breaks are fine. If you cannot talk, slow down.")
    if n == 2:
        return _run("fartlek", "Fartlek: 6 x 1 minute", "First taste of faster running, by feel, with plenty of recovery.",
                    [{"reps": 6, "min": 1, "zone": "T", "rest_s": 120, "rest": "2 min easy jog or walk"}], warm=10, cool=6)
    if n == 3:
        return _run("tempo", "Tempo: 2 x 8 minutes", "Raise the pace you can hold aerobically (the lactate threshold) with low injury cost.",
                    [{"reps": 2, "min": 8, "zone": "T", "rest_s": 180, "rest": "3 min easy jog"}], warm=10, cool=6)
    if n in DELOAD:
        return _run("easy", "Easy run and strides", "Keep the legs moving while the body absorbs the last three weeks.",
                    [{"min": 20, "zone": "E"}, strides])
    if n == 5:
        return _run("intervals", "Intervals: 6 x 400 m", "Teach the legs and lungs your current two-mile pace in short, repeatable pieces.",
                    [{"reps": 6, "m": 400, "zone": "I", "rest_s": 105, "rest": "200 m walk-jog (about 1:45)"}], warm=10, cool=6,
                    note="Even pace: the last rep should match the first. Two reps in a row more than 5 s slow: stop there.")
    if n == 6:
        return _run("intervals", "Intervals: 4 x 800 m", "Longer pieces at two-mile pace: the strongest stimulus for maximal aerobic power.",
                    [{"reps": 4, "m": 800, "zone": "I", "rest_s": 150, "rest": "2:30 easy jog"}], warm=10, cool=6,
                    note="Two reps in a row more than 8 s slow: stop there.")
    if n == 7:
        return _run("intervals", "Intervals: 5 x 800 m", "One more rep than last week at the same pace: more time at high oxygen use.",
                    [{"reps": 5, "m": 800, "zone": "I", "rest_s": 150, "rest": "2:30 easy jog"}], warm=10, cool=6,
                    note="Only if all four reps were on pace last week; otherwise repeat 4 x 800 m.")
    if n == 9:
        return _run("race_pace", "Race pace: 2 x (4 x 400 m)", "Slightly faster than your tested pace with short rests: makes test pace feel controlled.",
                    [{"reps": 8, "m": 400, "zone": "I", "rest_s": 70, "rest": "70 s walk; 3 min after the fourth"}], warm=10, cool=6,
                    note="Paces come from the week-8 time trial. Aim for the fast end of the range.")
    if n == 10:
        return _run("race_pace", "Race pace: 3 x 1000 m", "The most test-specific session: long pieces at two-mile pace.",
                    [{"reps": 3, "m": 1000, "zone": "I", "rest_s": 180, "rest": "3 min easy jog"},
                     {"reps": 2, "m": 200, "zone": "R", "rest_s": 90, "rest": "walk back"}], warm=10, cool=6)
    if n == 11:
        return _run("race_pace", "Dress rehearsal: 1 mile + 4 x 400 m", "One mile at exact two-mile pace to lock in the rhythm, then a little speed.",
                    [{"reps": 1, "m": 1600, "zone": "I", "rest_s": 240, "rest": "4 min walk-jog"},
                     {"reps": 4, "m": 400, "zone": "I", "rest_s": 90, "rest": "90 s walk"}], warm=10, cool=6,
                    note="Start the mile at pace, not faster. This is the opening mile of your test.")
    return _run("sharpen", "Sharpen: 3 x 400 m", "Short and quick with long rests: keeps the speed, adds no fatigue before the test.",
                [{"reps": 3, "m": 400, "zone": "I", "rest_s": 150, "rest": "2:30 walk"}], warm=10, cool=5)


def _trial(n):
    return [_test("run", "Two-mile time trial",
                  "8 laps of a 400 m track (or a measured flat two miles). Start at an effort you are sure you can hold; "
                  "speed up only in the last half mile. Record the time.",
                  "This is a controlled hard effort, about 9 out of 10, not a race to collapse." if n == 2 else
                  "Run it like the test: even laps, strong last 800 m.")]


def _monday(n, mode):
    jump, jsets = _JUMP[n]
    s, r, rir = _DL[n]
    items = [_power(jump, jsets, 3 if n != 12 else 2, note="Step down from the box; land quietly." if jump == "box_jump" else "Stick each landing, walk back, reset.")]
    note = {1: "Calibration: work up in 20-40 lb steps to a set of 5 that leaves about 4 reps in reserve, then repeat that load. No grinding today.",
            10: "First set is the top triple (1 in reserve). Take 10% off for the two sets after it.",
            12: "Openers only: fast, easy doubles. Saturday is the heavy day."}.get(n, "")
    items.append(_lift("deadlift", s, r, r, rir, 180, note=note))
    s, r, rir = _SQ[n]
    items.append(_lift("back_squat", s, r, r, rir, 165, note="Full depth you can own with a neutral back; long legs may need a slightly wider stance."))
    if _RDL[n] and mode != "deload":
        s, r, rir = _RDL[n]
        items.append(_lift("rdl", s, r, r, rir, 120, pri=2, tempo="3-1-1", note="Three seconds down; stop where the back would round."))
    if n != 12:
        items.append(_lift("calf_raise", 2 if mode in ("calibrate", "deload", "sharpen") else 3, 10, 15, 2, 60, pri=2, tempo="2-2-1",
                           note="Two-second pause in the bottom stretch. Calves and Achilles are what complain first when running returns."))
    if n != 12 and mode != "deload":
        items.append(_lift("hanging_leg_raise", 2 if mode in ("calibrate", "sharpen") else 3, 8, 12, 2, 60, pri=3))
    return {"day": "Mon", "session": "lower_strength", "title": "Lower-body strength and power", "intensity": "high" if mode not in ("deload", "taper") else "moderate",
            "why": "Heavy deadlifts and squats done fresh after a day off, with a few explosive jumps first; this is the strength base "
                   "for the deadlift, the sled drag and faster running.",
            "stimulus": "Maximal strength (few reps, long rests) and power.", "warm": "lower", "cool": "lower", "items": items}


def _tuesday(n, mode):
    s, lo, hi, rir = _BP[n]
    light = mode in ("deload", "taper")
    acc = 2 if light or mode == "calibrate" else 3
    arir = 4 if light else 3 if mode == "calibrate" else 2
    items = [_lift("bench_press", s, lo, hi, rir, 165),
             _lift("pull_up", 2 if light else 3 if mode in ("calibrate", "sharpen") else 4, 6, 10, arir, 120,
                   note="Full hang to chin over the bar. Fewer than 6 clean reps: use the lat pulldown at the same reps."),
             _lift("incline_press", acc, 8, 12, arir, 105, tempo="3-1-1", note="Own the stretch at the bottom for a full second."),
             {**_lift("chest_supported_row", acc, 8, 12, arir, 0, note="Superset: do a set in each rest of the incline press."),
              "superset": True},
             _lift("lateral_raise", acc, 12, 20, 1 if not light else 3, 45, pri=2)]
    if not light:
        items += [_lift("curl", 2, 10, 15, 2, 60, pri=3), _lift("overhead_triceps", 2, 10, 15, 2, 60, pri=3)]
    if n not in (1, 12):
        items.append(_hold("plank", 2, (45, 75), 45, pri=3, pct=_PLK[_block(n)["id"]]))
    items.append(_run("easy", "Easy run", "A short easy run after lifting: more running frequency at almost no recovery cost.",
                      [{"min": _TUE_RUN[n], "zone": "E"}],
                      note="Truly easy. A bike or rower for the same minutes is fine if the legs are beaten up."))
    return {"day": "Tue", "session": "upper_a", "title": "Upper-body strength and physique A, easy run", "intensity": "low",
            "why": "The legs recover from Monday while the chest, back and shoulders get their heaviest work; the short easy run "
                   "adds aerobic volume without a second hard day.",
            "stimulus": "Upper-body strength and hypertrophy; easy aerobic minutes.", "warm": "upper", "cool": "upper", "items": items}


def _wednesday(n, mode, trial_week):
    items = []
    easy = n == trial_week and n != 1                         # the time trial is this Saturday: no other hard running
    run = _wed_run(1 if easy else n, True)
    if n in (1, 8):
        items.append(_test("hrp", "Hand-release push-up test", "As many test-legal reps as you can in 2 minutes. Count only good ones.",
                           "Done first, while fresh. Stop if form breaks; that number is your baseline."))
    if n == 8:
        items.append(_test("sdc", "Sprint-drag-carry, timed", "One full sprint-drag-carry at test effort, to the AFT standard. Record the time.",
                           "Rest 5 minutes after the push-ups and rehearse one easy lane of each leg first."))
        items.append(_test("plk", "Plank test", "One hold to the AFT standard for as long as the straight line lasts. Record the time."))
        run = _run("easy", "Easy run", "Flush the legs after the tests.", [{"min": 12, "zone": "E"}])
    items.append(run)
    if n in _HRP:
        sets, pct, rest = _HRP[n]
        items.append({**_lift("hand_release_pushup", sets, 8, 30, 4, rest, pri=1,
                              note="Every rep test-legal: straight body, chest down, hands fully off the ground, full lockout."),
                      "pct_of": "hrp", "pct": pct, "gate": "",
                      "target": "a share of your tested max per set; no test on file: stop 4-5 reps before failure",
                      "progression": "The share of your tested max rises by block; the test in week 8 resets it."})
    if n == 9:
        items.append(_field("hand_release_pushup", "Push-up pacing drill", 1, "60 s", "test cadence, one rep every 2-2.5 s, no pause at the top", 0,
                            "Learn the rhythm you will hold for two minutes."))
    if n == 1:
        items.append(_test("plk", "Plank test", "One hold to the AFT standard for as long as the straight line lasts. Record the time."))
    elif n != 8:
        items.append(_hold("plank", 2 if mode in ("deload", "taper") else 3, (45, 75), 45, pct=_PLK[_block(n)["id"]],
                           note="Each hold is a share of your tested best, not a max: three good holds beat one ugly one."))
        items.append(_hold("side_plank", 2, (30, 45), 20, pri=3, each=True))
    hard = n == 8 or (mode not in ("calibrate", "deload") and not easy)
    title = "Mid-point tests: push-ups, sprint-drag-carry, plank" if n == 8 else (
        "Baseline tests and easy run" if n == 1 else run["name"] + ", push-ups and plank")
    why = ("Three of the five events measured fresh at the half-way mark; the two-mile follows on Saturday after two easy days." if n == 8 else
           "An easy run with strides and the first baseline tests, so targets come from today's body, not old numbers." if not hard else
           "The week's one hard run, 48 hours after heavy legs and 48 hours before sprinting, followed by fresh push-up and plank practice.")
    return {"day": "Wed", "session": "tests" if n == 8 else "run_quality", "title": title, "intensity": "high" if hard else "low", "why": why,
            "stimulus": "Aerobic power or threshold (the hard run); push-up and plank endurance.", "warm": "run", "cool": "run", "items": items}


def _upper_b(n, mode, full):
    light = mode in ("deload", "taper")
    s = 2 if light else 3
    rir = 4 if light else 2
    items = [_lift("dip", s, 8, 12, rir, 105 if full else 75, note="No dip bars or sore shoulders: deficit push-ups."),
             _lift("pull_up", s, 10, 12, rir, 105 if full else 75, name="Lat pulldown", note="A different grip from Tuesday's pull-ups."),
             _lift("cable_crossover", s if full else 2, 12, 15, 1 if not light else 3, 60, pri=2),
             _lift("lateral_raise", s if full else 2, 12, 20, 1 if not light else 3, 45, pri=2),
             _lift("face_pull", 2, 15, 20, 2, 45, pri=2)]
    if light:
        items = [i for i in items if i["key"] in ("dip", "pull_up", "lateral_raise")]
    if full and not light:
        items[2:2] = [_lift("overhead_press", 3, 6, 10, 2, 120), _lift("row", 3, 8, 12, 2, 105)]
        items += [_lift("curl", 2, 10, 15, 1, 60, pri=3), _lift("pushdown", 2, 10, 15, 1, 60, pri=3)]
    return items


def _thursday(n, mode, six):
    if six and n != 12:
        return {"day": "Thu", "session": "upper_b", "title": "Upper-body physique B", "intensity": "low",
                "why": "A second upper-body day so every muscle is trained twice a week, with the legs fully resting before Friday's sprints.",
                "stimulus": "Hypertrophy: moderate loads, close to failure, short rests.", "warm": "upper", "cool": "upper",
                "items": _upper_b(n, mode, True)}
    return {"day": "Thu", "session": "recovery", "title": "Optional recovery: easy movement and mobility", "intensity": "low", "optional": True,
            "why": "Rest is the default today. If you feel good, twenty easy minutes and some mobility speed recovery without adding fatigue.",
            "stimulus": "Blood flow and range of motion only.", "warm": None, "cool": "mixed",
            "items": [{**_run("easy", "Easy walk, bike or jog", "Recovery, not training: finish fresher than you started.",
                              [{"min": 20, "zone": "E"}], pri=3, note="A brisk walk counts. Skip it without guilt if you are tired."),
                       "optional": True}]}


def _friday(n, mode, trial_week):
    if n == 12:
        return {"day": "Fri", "session": "primer", "title": "Primer: easy run and strides", "intensity": "low",
                "why": "The day before the practice test: wake the legs up, add no fatigue.", "stimulus": "Neuromuscular priming.",
                "warm": "easy", "cool": "run",
                "items": [_run("easy", "Easy run and strides", "Loosen up for tomorrow.",
                               [{"min": 12, "zone": "E"}, {"reps": 4, "m": 80, "zone": "R", "rest_s": 60, "rest": "walk back"}],
                               note="Then lay out your kit, eat a normal dinner and sleep.")]}
    if n == 8:
        return {"day": "Fri", "session": "primer", "title": "Primer: easy run, strides and hamstring care", "intensity": "low",
                "why": "The day before the two-mile time trial: loosen up, keep the Nordic habit, add no fatigue.",
                "stimulus": "Neuromuscular priming.", "warm": "easy", "cool": "run",
                "items": [_run("easy", "Easy run and strides", "Loosen up for tomorrow.",
                               [{"min": 12, "zone": "E"}, {"reps": 4, "m": 80, "zone": "R", "rest_s": 60, "rest": "walk back"}]),
                          _lift("nordic_curl", 2, 3, 3, 2, 90, note="Easy today: slow lowering, well short of failure."),
                          _lift("calf_raise", 2, 10, 15, 3, 60, pri=2, tempo="2-2-1")]}
    before_trial = n == trial_week
    (reps, metres, effort, rest), sdc = _SPEED[1 if before_trial else n]
    items = [_field("sprint", f"Sprints: {reps} x {metres} m", reps, f"{metres} m", effort, rest,
                    "Walk back and take the full rest: these are speed reps, not conditioning. Any hamstring tightness: stop sprinting for the day.",
                    "Effort rises before distance; full speed only from week 5, after four weeks of build-ups and Nordic curls.")]
    if sdc:
        k, eff, srest = sdc
        items.append(_field("sdc", "Sprint-drag-carry practice", k, "5 x 50 m", eff, srest,
                            "Order: sprint, backward sled drag, lateral shuffle, kettlebell carry, sprint. Touch the line with foot and hand on every turn."))
    else:
        items.append(_test("sdc", "Sprint-drag-carry, timed", "One full sprint-drag-carry at test effort, to the AFT standard. Record the time.",
                           "Warm up with one easy rehearsal lane of each leg first."))
    block = _block(n)["id"]
    light = mode in ("deload", "sharpen") or before_trial
    bs = (2, 8, 8, 4) if n == 1 or light else (3, 8, 8, 3) if block == "foundation" else (3, 6, 8, 2) if block == "forge" else (2, 6, 6, 2)
    items.append(_lift("bulgarian_split_squat", bs[0], bs[1], bs[2], bs[3], 105, each=True))
    ns, nr = _NORDIC[1 if before_trial else n]
    items.append(_lift("nordic_curl", ns, nr, nr, 1, 90, note="Lower as slowly as you can, catch yourself with the hands, push back up. Quality over depth."))
    items.append(_carry(2 if light else 3, 40 if block != "ascension" else 50,
                        "Heavier than the test's two 40 lb kettlebells; by Phase III, walk fast."))
    items.append(_hold("copenhagen_plank", 2, (15, 25), 30, pri=3, each=True))
    if not light and n != 1:
        items.append(_lift("hip_thrust", 2 if block == "ascension" else 3, 8, 10, 2, 90, pri=3))
    return {"day": "Fri", "session": "field_power", "title": "Speed, sprint-drag-carry and single-leg strength",
            "intensity": "high" if mode != "calibrate" and not before_trial else "moderate",
            "why": "Sprinting is done first and fresh, two days after the hard run; single-leg strength, Nordic curls and carries follow "
                   "because they protect the hamstrings and build the drag and carry.",
            "stimulus": "Speed and acceleration, event skill, single-leg strength, hamstring resilience, grip.",
            "warm": "sdc", "cool": "sdc", "items": items}


def _aft_day():
    return {"day": "Sat", "session": "aft_test", "title": "Practice Army Fitness Test", "intensity": "high",
            "why": "Twelve weeks of work, measured the way the Army measures it. These five results become the baseline for the next block.",
            "stimulus": "Testing.", "warm": "mixed", "cool": "run",
            "items": [_test("mdl", "3-repetition maximum deadlift", "Work up in 3-4 warm-up sets, then up to three attempts at a heavy triple. "
                            "Stop at the first triple that slows down hard; a clean triple beats a grinder.",
                            "Rest about 3 minutes between attempts."),
                      _test("hrp", "Hand-release push-ups, 2 minutes", "Test-legal reps in 2 minutes at the cadence you practised."),
                      _test("sdc", "Sprint-drag-carry", "One all-out effort to the AFT standard."),
                      _test("plk", "Plank", "One hold to the AFT standard."),
                      _test("run", "Two-mile run", "Even laps at the pace from your dress rehearsal; give what is left in the last 800 m.")],
            "notes": "Follow the official order and rest times between events (docs/pca/research-army.md). Eat and sleep as you would for a record test."}


def _saturday(n, mode, six, trial_week):
    if n == 12:
        return _aft_day()
    items = []
    if n in (trial_week, 8):
        items += _trial(n)
        title, why, intensity = "Two-mile time trial", ("Your real current two-mile time. Every run pace from here on comes from this result, "
                                                         "not from an old one."), "high"
    else:
        minutes = _LONG[n] or 28
        segs = [{"min": minutes, "zone": "E"}]
        if n == 10:
            segs = [{"min": minutes - 10, "zone": "E"}, {"min": 10, "zone": "S"}]
        items.append(_run("long", "Long easy run", "Aerobic base: more capillaries and mitochondria, tougher tendons, at an effort that needs little recovery.",
                          segs, note="Slow enough to hold a conversation the whole way. Walk breaks are allowed and do not spoil the session.",
                          builds="The long run grows by no more than about a tenth over your longest run of the last four weeks."))
        title, why, intensity = "Long easy run", ("The easy long run is where the two-mile engine is built; it follows Friday so the only hard "
                                                  "thing left this week is behind you."), "moderate"
    if not six:
        ub = _upper_b(n, mode, False)
        for it in ub:
            it["priority"] = 3 if it["key"] in ("cable_crossover", "face_pull") else 2
        items += ub
        title += " and upper-body physique B"
    if n not in (trial_week, 8):
        items.append(_hold("plank", 2 if mode in ("deload", "sharpen") else 3, (45, 75), 45, pct=_PLK[_block(n)["id"]], pri=1))
        if mode != "deload":
            items.append(_lift("pallof_press", 2, 10, 12, 3, 30, pri=3, each=True))
    return {"day": "Sat", "session": "long_run" if intensity != "high" else "time_trial", "title": title, "intensity": intensity,
            "why": why, "stimulus": "Aerobic endurance" + ("" if six else "; upper-body hypertrophy"), "warm": "easy" if intensity != "high" else "run",
            "cool": "run", "items": items}


# ---- assembling the plan ---------------------------------------------------------------------------------------------------
def _minutes(items) -> int:
    total = 0.0
    for it in items:
        if it.get("run"):
            total += it["run"]["total_min"]
        elif it["kind"] == "test":
            total += {"mdl": 15, "hrp": 5, "sdc": 6, "plk": 6, "run": 30}[it["test"]]
        elif it["kind"] == "hold":
            total += it["sets"] * (it["hi"] + it["rest_s"]) / 60 * (2 if "each side" in it["reps"] else 1)
        elif it["kind"] == "cardio":
            total += it["sets"] * (it["rest_s"] + 30) / 60 if it["key"] == "sprint" else it["sets"] * (it["rest_s"] + 150) / 60
        else:
            total += it["sets"] * ((0 if it.get("superset") else it["rest_s"]) + 40 * (2 if "each side" in it["reps"] else 1)) / 60
    return round(total)


def _finish(day: dict, exercises: dict, details: dict, room: int) -> dict:
    """Fill in names, cues, equipment and alternatives, and fit the day to his session length by cutting the
    lowest-priority work first (3 = optional extra, 2 = secondary; priority 1 is never cut)."""
    warm = {"easy": _EASY_WARM, None: []}.get(day["warm"]) if day["warm"] in ("easy", None) else lib.warmup_for(day["warm"])
    cool = lib.cooldown_for(day["cool"])
    items = day["items"]
    first_run = next((i["run"] for i in items if i.get("run")), {})
    extra = {"run": 0 if first_run.get("warm_min") else 10, "easy": 4, None: 0}.get(day["warm"], 10) + (
        0 if day["cool"] == "run" and first_run.get("cool_min") else 5)
    trimmed = []
    for pri in (3, 2):
        while _minutes(items) + extra > room + 10 and any(i["priority"] == pri for i in items):
            drop = [i for i in items if i["priority"] == pri][-1]
            items.remove(drop)
            trimmed.append(drop.get("name") or exercises.get(drop["key"], (drop["key"],))[0])
    out = []
    for it in items:
        purpose, equipment, alts = EX.get(it["key"], ("", "", []))
        name = it.pop("name", "") or exercises.get(it["key"], (it["key"].replace("_", " ").capitalize(),))[0]
        rest = it["rest_s"]
        out.append({"exercise": name, **it, "sets": str(it["sets"]),
                    "rest": "" if not rest else f"{rest // 60} min" if rest % 60 == 0 else f"{rest // 60}:{rest % 60:02d}" if rest > 60 else f"{rest} s",
                    "purpose": (it.get("run") or {}).get("purpose") or purpose, "equipment": equipment, "alternatives": alts,
                    "cues": exercises.get(it["key"], ("", "", ""))[2], "mistakes": details.get(it["key"], {}).get("mistakes", "")})
    return {"day": day["day"], "title": day["title"], "session": day["session"], "intensity": day["intensity"],
            "optional": bool(day.get("optional")), "why": day["why"], "stimulus": day["stimulus"], "items": out,
            "warmup": warm, "cooldown": cool, "minutes": _minutes(items) + extra, "trimmed": trimmed, "notes": day.get("notes", "")}


def build(answers: dict, start: date, today: date | None = None) -> dict:
    """The whole plan for these onboarding answers. answers: days_per_week (5 or 6), session_minutes, running_now
    (True when he already runs three times a week with no pain: the time trial then sits in week 1 instead of week 2)."""
    from tools import fitness                                 # the exercise names and cues the page already uses
    six = int(answers.get("days_per_week") or 5) >= 6
    room = max(60, min(90, int(answers.get("session_minutes") or 60)))
    trial_week = 1 if answers.get("running_now") else 2
    weeks = []
    for n in range(1, 13):
        mode, focus = WEEKS[n]
        days = [_monday(n, mode), _tuesday(n, mode), _wednesday(n, mode, trial_week), _thursday(n, mode, six),
                _friday(n, mode, trial_week),
                _saturday(n, mode, six, trial_week)]
        if trial_week == 1 and n == 2:                        # the trial already happened: week 2 gets its long run back
            days[5] = _saturday(3, mode, six, trial_week)
        block = _block(n)
        weeks.append({"week": n, "focus": focus, "mode": mode, "block": block["id"], "block_name": block["name"],
                      "days": [_finish(d, fitness.EXERCISES, lib.DETAILS, room) for d in days]})
    return {"name": NAME, "program": PROGRAM_ID, "start": start.isoformat(), "saved": (today or start).isoformat(), "weeks": weeks,
            "generated": False, "goal": "aft", "level": "intermediate", "days_per_week": 6 if six else 5, "session_minutes": room,
            "provisional": True, "trial_week": trial_week, "blocks": [dict(b, weeks=list(b["weeks"])) for b in BLOCKS], "zones": ZONES,
            "why": ("Three four-week phases. Heavy legs on Monday, the hard run on Wednesday and sprints on Friday are kept 48 hours apart; "
                    "the days between are upper body or easy. Weeks 4 and 8 are lighter with a small test, and week 12 tapers into a full "
                    "practice AFT. Loads follow what you log and run paces come only from a time trial you run in this program."),
            "notes": ["Provisional until the onboarding answers and the first baseline tests are in.",
                      "Written by an AI coach from published evidence. It has not been reviewed by a certified human coach or a clinician."]}


# ---- what happens after week 12 --------------------------------------------------------------------------------------------
LONG_TERM = {
    "week_13": "A transition week: three or four easy sessions, nothing heavy, nothing timed. Then the next block starts.",
    "retest_every_weeks": 6,
    "blocks": [
        {"id": "elite_ready", "name": "Phase IV - Elite Ready", "weeks": 4,
         "goal": "Raise the weakest test event from the week-12 results while holding the others with about a third of the building volume."},
        {"id": "evolution", "name": "Phase V - Evolution", "weeks": 12,
         "goal": "Repeat the three-phase cycle with a new emphasis; alternate a strength-and-size emphasis with a running emphasis so "
                 "neither is pushed all year."},
    ],
    # weakest event by points -> what the next block changes
    "emphasis": {
        "run": "Running emphasis: a fourth run day (easy), the long run grows toward 60 minutes, lifting drops to two full-body days that hold strength.",
        "plk": "Trunk emphasis: plank work four days a week (holds at 80% of max, long-lever and weighted variations), carries twice a week.",
        "hrp": "Push-up emphasis: three push-up exposures a week (one heavy pressing day, two submaximal volume days) and a weekly 2-minute pacing set.",
        "sdc": "Speed emphasis: two short sprint days, heavier sled drags, one timed sprint-drag-carry every other week.",
        "mdl": "Strength emphasis: deadlift and squat twice a week (one heavy, one speed), running held at two easy days plus one interval session.",
    },
    "plateau": ["A lift that has not moved for three weeks: change the rep range (5s to 3s, or 8s) before changing the exercise.",
                "A run time that has not moved across two tests: add easy minutes first, hard minutes second, and check sleep and body weight.",
                "Everything stalling at once is fatigue, not a plateau: take a lighter week."],
    "maintenance": "A quality held, not built, needs about one hard exposure a week at the same intensity and a third of the volume.",
    "physique": "Hold body weight steady or gain slowly (about a quarter to half a pound a week) while building; cut no faster than about "
                "one pound a week, never in the last three weeks before a record test.",
    "standards": "Expert Fitness Badge and school standards (Ranger, Air Assault) come after a passing-with-margin AFT; add ruck marching "
                 "progressively (start 2 miles at 25 lb, add distance before load) only once the two-mile baseline has recovered.",
}


# ---- the structural review -------------------------------------------------------------------------------------------------
_NEED = ("exercise", "purpose", "equipment", "sets", "reps", "target", "progression", "alternatives")


def verify(plan: dict) -> list[str]:
    """Problems found in a built plan (empty = it passes). These are the rules the program claims to follow, checked
    on the data itself: complete prescriptions, hard days apart, a rest day, run load that grows slowly, lighter weeks."""
    found = []
    longest, run_minutes, sets_by_week = [], [], []
    for wk in plan["weeks"]:
        n = wk["week"]
        days = {d["day"]: d for d in wk["days"]}
        if "Sun" in days:
            found.append(f"week {n}: Sunday must be a rest day")
        order = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
        for a, b in zip(order, order[1:]):
            if days.get(a, {}).get("intensity") == "high" and days.get(b, {}).get("intensity") == "high" and not (n == 12):
                found.append(f"week {n}: two hard days in a row ({a}, {b})")
        hard_runs = total = hard = longest_run = sets = 0
        tested = False
        for d in wk["days"]:
            if not d["warmup"] and d["session"] != "recovery":
                found.append(f"week {n} {d['day']}: no warm-up")
            if not d["cooldown"] or not d["why"] or not d["minutes"]:
                found.append(f"week {n} {d['day']}: missing cool-down, purpose or duration")
            if d["minutes"] > plan["session_minutes"] + 10 and d["session"] not in ("aft_test",):
                found.append(f"week {n} {d['day']}: {d['minutes']} min is over his {plan['session_minutes']} min")
            for it in d["items"]:
                for field in _NEED:
                    if not it.get(field):
                        found.append(f"week {n} {d['day']} {it.get('exercise')}: no {field}")
                if it["kind"] == "lift" and not it.get("cues"):
                    found.append(f"week {n} {d['day']} {it['exercise']}: no technique cues")
                run = it.get("run")
                if run and not d["optional"]:
                    total += run["total_min"]
                    hard += run["hard_min"]
                    hard_runs += run["type"] in ("fartlek", "tempo", "intervals", "race_pace", "sharpen")
                    longest_run = max(longest_run, run["total_min"] if run["type"] in ("long", "easy") else 0)
                if it["kind"] == "test" and it["test"] == "run":
                    total += 28
                    hard += 18
                    hard_runs += 1
                    tested = True
                    longest_run = max(longest_run, 27)
                if it["kind"] == "lift":
                    sets += int(it["sets"])
        if hard_runs > 2:
            found.append(f"week {n}: {hard_runs} hard run sessions (limit 2)")
        if total and hard / total > 0.30 and not tested:
            found.append(f"week {n}: {hard / total:.0%} of run minutes are hard (limit 30%)")
        if hard > 28:
            found.append(f"week {n}: {hard:g} hard run minutes (limit 28)")
        recent = longest[-4:]
        if recent and longest_run > max(recent) * 1.11 + 0.5:
            found.append(f"week {n}: longest run {longest_run:g} min is more than a tenth over the last four weeks ({max(recent):g})")
        if run_minutes and total > max(run_minutes) * 1.25 + 1:
            found.append(f"week {n}: {total:g} run minutes is more than a quarter over any earlier week ({max(run_minutes):g})")
        if n in DELOAD and sets > sets_by_week[-1] * 0.7:
            found.append(f"week {n}: a deload week with {sets} lifting sets against {sets_by_week[-1]} the week before")
        longest.append(longest_run)
        run_minutes.append(total)
        sets_by_week.append(sets)
    tests = {(wk["week"], it["test"]) for wk in plan["weeks"] for d in wk["days"] for it in d["items"] if it["kind"] == "test"}
    for need in ((1, "hrp"), (1, "plk"), (plan["trial_week"], "run"), (4, "sdc"), (8, "run"), (8, "hrp"), (8, "plk"), (8, "sdc"),
                 (12, "mdl"), (12, "hrp"), (12, "sdc"), (12, "plk"), (12, "run")):
        if need not in tests:
            found.append(f"week {need[0]}: the {need[1]} test is missing")
    return found


def summary(plan: dict) -> list[dict]:
    """Per week: lifting sets, run minutes, hard run minutes, sprint metres. The numbers behind the review."""
    rows = []
    for wk in plan["weeks"]:
        sets = run = hard = sprint = 0
        for d in wk["days"]:
            for it in d["items"]:
                if it["kind"] == "lift":
                    sets += int(it["sets"])
                if it.get("run") and not d["optional"]:
                    run += it["run"]["total_min"]
                    hard += it["run"]["hard_min"]
                if it["kind"] == "test" and it["test"] == "run":
                    run, hard = run + 28, hard + 18
                if it["key"] == "sprint" and it["kind"] == "cardio":
                    sprint += int(it["sets"]) * int(str(it["reps"]).split()[0])
        rows.append({"week": wk["week"], "mode": wk["mode"], "lift_sets": sets, "run_min": round(run), "hard_run_min": round(hard),
                     "sprint_m": sprint, "minutes": {d["day"]: d["minutes"] for d in wk["days"]}})
    return rows
