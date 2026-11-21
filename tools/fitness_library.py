"""The trainer's coaching library (data only, no tools): exercise details, warm-ups, cool-downs and the
personal health notes. tools/fitness.py attaches these to every session, so each day in the app is a full
professional session (warm-up -> main work -> cool-down) even when a plan only lists the main lifts.

Warm-ups follow the RAMP structure (Jeffreys 2007): Raise (heart rate and muscle temperature), Activate and
Mobilize (the muscles and joint ranges the session needs), Potentiate (ramp-up sets / strides so the first
working set is already at full quality). Static stretching is kept short before lifting (under about 60 s per
muscle has little or no effect on strength, Behm et al. 2016) and is used mainly in the cool-down.
"""

# key -> (name, event, cues). Event links a drill to the AFT event it helps ("" = general / physique).
EXTRA_EXERCISES = {
    # physique / strength
    "rdl": ("Romanian deadlift", "mdl", "Soft knees, push the hips straight back with a flat back until a deep hamstring stretch (bar just below the knees), then drive the hips forward. The stretch is where the growth is: control it for 2-3 s."),
    "bulgarian_split_squat": ("Bulgarian split squat", "sdc", "Rear foot on a bench, front foot far enough out that you can sink straight down; slight forward lean for glutes, upright for quads. Go deep: back knee to an inch off the floor."),
    "pull_up": ("Pull-up / lat pulldown", "", "Start from a full dead hang (stretch the lats), pull the elbows down to your back pockets, chest to the bar; lower for 2-3 s. Builds the V-taper width."),
    "chest_supported_row": ("Chest-supported row", "hrp", "Chest on an incline bench, let the shoulder blades spread at the bottom, then row the elbows back and squeeze; no swinging. A thick upper back means better posture and a bigger-looking chest."),
    "lateral_raise": ("Lateral raise", "", "Slight lean forward, lead with the elbows out to the side to shoulder height, lower slowly. Side delts are what make shoulders look wide: light weight, high quality."),
    "overhead_press": ("Overhead press", "hrp", "Glutes and abs tight, press from the collarbone straight up and finish with the head through; don't lean back."),
    "face_pull": ("Face pull", "", "Rope at forehead height, pull to the eyes with elbows high and spread the rope apart, hold 1 s. Rear delts and healthy shoulders for all the pressing."),
    "rear_delt_fly": ("Rear-delt fly", "", "Hinged or chest on an incline bench, arms slightly bent, sweep them out wide leading with the elbows, pause."),
    "curl": ("Incline dumbbell curl", "", "Lie back on a 45-60 degree bench so the arms hang behind you (biceps fully stretched), curl without the elbows moving forward, lower slowly."),
    "overhead_triceps": ("Overhead triceps extension", "", "Cable or dumbbell behind the head, elbows pointing up, lower into a deep stretch, extend fully. Overhead work grew the triceps about 40% more than pushdowns in a 2023 study (Maeo et al.)."),
    "pushdown": ("Triceps pushdown", "hrp", "Elbows pinned to your sides, push down to full lockout, control back up."),
    "calf_raise": ("Calf raise", "run", "Pause 2 s at the bottom in the full stretch, rise all the way onto the big toe, lower slowly. Calves and Achilles take a beating from running."),
    "hanging_leg_raise": ("Hanging leg raise", "plk", "Dead hang, curl the pelvis up (not just the legs), lower slowly without swinging."),
    "side_plank": ("Side plank", "plk", "Elbow under the shoulder, body in one straight line, hips high, top hand on the hip; breathe."),
    "hip_thrust": ("Hip thrust", "mdl", "Upper back on a bench, chin tucked, drive through the heels until the hips are in line with the knees, squeeze the glutes 1 s."),
    "hip_abduction": ("Hip abduction (machine or band)", "", "Machine: sit tall with your back against the pad and push the knees out wide, hold 1 s, then let them come back in slowly. Band: loop it just above the knees, stand tall (hold something for balance) and lift one leg out to the side with the toes facing forward; no leaning. Works the side of the glutes (glute medius)."),
    "glute_kickback": ("Glute kickback (cable or band)", "", "Brace the belly and keep the back flat, then push one heel straight back until the leg is in line with the body (hip height, no higher), squeeze the glute 1 s and bring it back slowly. On all fours with a band, or standing at a cable holding the frame. If the lower back arches, the leg went too high: go lighter."),
    "step_up": ("Step-up", "sdc", "Whole front foot on a sturdy bench or box (start low: knee height or below), lean slightly forward and push through that heel to stand up tall; do not push off the back foot. Lower yourself slowly until the back foot touches the floor. All reps on one leg, then switch. Hold something for balance at first."),
    "cable_crossover": ("Low-to-high cable fly", "hrp", "Cables at the bottom, scoop the hands up and together to chin height: hits the upper chest that makes it look full in a shirt."),
    # Project Captain America
    "back_squat": ("Back squat", "mdl", "Bar on the upper traps, hands tight, big breath and brace; sit down between the hips with the knees tracking over the toes, to the depth you can hold a neutral back, then drive up with the chest and hips rising together."),
    "nordic_curl": ("Nordic hamstring curl", "sdc", "Kneel on a pad with the ankles held down, body straight from knee to head; lean forward as slowly as you can, fighting with the hamstrings, catch yourself with the hands, push back up and repeat."),
    "box_jump": ("Box jump", "sdc", "Quick dip, swing the arms and jump; land softly on the whole foot in a quarter squat, stand tall, then STEP down. Pick a height you clear easily."),
    "broad_jump": ("Broad jump", "sdc", "Feet hip-width, swing the arms back, jump out as far as you can and stick the landing soft and balanced; walk back and reset every rep."),
    "pallof_press": ("Pallof press", "plk", "Side-on to a cable or band at chest height, feet shoulder-width; press the handle straight out and hold 2 s without letting the trunk turn, then bring it back."),
    "copenhagen_plank": ("Copenhagen plank", "sdc", "Side plank with the top leg on a bench and the bottom leg hanging; lift the hips so the body is straight and squeeze the top inner thigh. Knee on the bench makes it easier."),
    "sdc": ("Sprint-drag-carry", "sdc", "Five 50 m shuttles: sprint, backward sled drag, lateral shuffle, kettlebell carry, sprint. Stay low into every turn, touch the line, and keep the sled moving with short quick steps."),
    # warm-up drills
    "jumping_jack": ("Jumping jacks", "", "Light and springy on the balls of the feet; just getting warm."),
    "arm_circles": ("Arm circles", "", "Small to big circles, forward then back; loosen the shoulders."),
    "leg_swing": ("Leg swings", "run", "Hold something for balance, swing one leg forward and back 10x, then side to side 10x; relaxed, growing range."),
    "worlds_greatest_stretch": ("World's greatest stretch", "", "Long lunge, same-side elbow toward the instep, then rotate and reach that arm to the sky; 3-5 per side. Hips, hamstrings and upper back in one move."),
    "inchworm": ("Inchworm", "", "Hinge down, walk the hands out to a plank (optional push-up), walk the feet up to the hands; keep the legs as straight as you can."),
    "glute_bridge": ("Glute bridge", "mdl", "On your back, feet flat, drive the hips up and squeeze 2 s; wakes up the glutes before deadlifts, squats and sprints."),
    "band_pull_apart": ("Band pull-apart", "hrp", "Arms straight at chest height, pull the band apart until it touches the chest, squeeze the shoulder blades; 15-20 reps."),
    "scap_pushup": ("Scap push-up", "hrp", "In a plank on your hands, keep the arms straight and let the chest sink between the shoulder blades, then push the floor away."),
    "cat_cow": ("Cat-cow", "plk", "On hands and knees, round the whole spine up, then let it sag and look up; slow, with the breath."),
    "high_knees": ("A-skip / high knees", "run", "Tall posture, drive the knee to hip height and snap the foot down under the hip; quick arms."),
    "ramp_sets": ("Ramp-up sets", "", "Lighter sets of your first exercise building toward the working weight. Crisp, fast reps, never tiring: they prime the nervous system so set 1 is already a quality set."),
    # cool-down stretches (static, 30-60 s each, slow breathing)
    "couch_stretch": ("Couch stretch (hip flexor)", "run", "Back knee on the floor near a wall/couch with that foot up behind you, front foot forward; squeeze the back glute and stand tall. Undoes sitting and running stiffness."),
    "hamstring_stretch": ("Hamstring stretch", "mdl", "Seated or with the heel on a low step, hinge forward with a flat back until a gentle pull behind the thigh."),
    "doorway_pec_stretch": ("Doorway chest stretch", "hrp", "Forearm on a door frame at shoulder height, step through until a stretch across the chest; both sides."),
    "childs_pose": ("Child's pose (lats)", "", "Sit back on the heels, arms reaching long in front, sink the chest; walk the hands to each side for the lats."),
    "pigeon": ("Pigeon stretch", "run", "Front shin across in front of you, back leg long behind; sink the hips and breathe into the outer hip."),
    "calf_stretch": ("Calf stretch", "run", "Hands on a wall, back leg straight with the heel down, then bend that knee slightly for the lower calf."),
    "box_breathing": ("Box breathing", "", "Lie down: in 4 s, hold 4 s, out 4 s, hold 4 s, for 2-3 minutes. Shifts the body into recovery mode."),
}

# Extra coaching shown in the app's "How to do it": muscles worked, common mistakes, and notes for a tall
# lifter (he is 6'2.5": long levers mean more range per rep, so loads look lighter than shorter lifters' -
# that's normal - and setup matters more).
DETAILS = {
    "deadlift": {"muscles": "Glutes, hamstrings, quads, back, grip", "mistakes": "Hips shooting up first; rounding the lower back; yanking the bar off the floor.",
                 "tall": "Tall lifters have long femurs: the trap bar (the AFT bar) suits you because you can sit a bit lower with a more upright chest."},
    "rdl": {"muscles": "Hamstrings, glutes, spinal erectors", "mistakes": "Squatting it down; losing the flat back to reach the floor.",
            "tall": "Your range is long: stop where the back would round, usually just below the knee."},
    "squat": {"muscles": "Quads, glutes, adductors", "mistakes": "Heels lifting; knees caving in.",
              "tall": "Long femurs: a slightly wider stance, toes out 15-30 degrees, and small plates under the heels let you squat deep and upright."},
    "bulgarian_split_squat": {"muscles": "Quads, glutes (one leg at a time)", "mistakes": "Front foot too close; bouncing out of the bottom.",
                              "tall": "Use a longer stance than you think and a slightly lower bench."},
    "lunge": {"muscles": "Quads, glutes", "mistakes": "Short steps; front knee caving in.", "tall": "Longer stride to keep the front shin near vertical."},
    "incline_press": {"muscles": "Upper chest, front delts, triceps", "mistakes": "Bench too steep (above 45 degrees turns it into a shoulder press); bouncing the bottom.",
                      "tall": "Long arms = a longer press: own the bottom stretch for 1-2 s instead of rushing it; that stretch is what builds the chest."},
    "bench_press": {"muscles": "Chest, front delts, triceps", "mistakes": "Elbows flared to 90 degrees; butt off the bench.",
                    "tall": "Long arms: a slightly wider grip and a bigger arch shorten the range so you can load it."},
    "dip": {"muscles": "Lower chest, triceps, front delts", "mistakes": "Going too deep too soon; shrugging.", "tall": "Bend the knees and lean forward to keep it on the chest."},
    "chest_fly": {"muscles": "Chest (in the stretched position)", "mistakes": "Turning it into a press; too heavy.", "tall": "Light and slow: long arms make flyes hard on the shoulder at the bottom."},
    "cable_crossover": {"muscles": "Upper chest", "mistakes": "Using the arms instead of squeezing the chest.", "tall": ""},
    "hand_release_pushup": {"muscles": "Chest, triceps, front delts, core", "mistakes": "Hips sagging; not fully releasing the hands.",
                            "tall": "Long arms mean each rep travels further than a shorter soldier's: pace yourself (about 1 rep every 2-3 s) and train volume."},
    "pull_up": {"muscles": "Lats, upper back, biceps", "mistakes": "Half reps; kipping.", "tall": "If full pull-ups are hard, use the lat pulldown with a full stretch at the top."},
    "row": {"muscles": "Lats, mid back, rear delts", "mistakes": "Standing up with the weight; jerking.", "tall": ""},
    "chest_supported_row": {"muscles": "Mid back, lats, rear delts", "mistakes": "Lifting the chest off the pad.", "tall": ""},
    "lateral_raise": {"muscles": "Side delts", "mistakes": "Swinging; shrugging the traps.", "tall": "Long arms = a long lever: go lighter than you think."},
    "overhead_press": {"muscles": "Shoulders, triceps, upper chest", "mistakes": "Leaning back; bar drifting forward.", "tall": ""},
    "overhead_triceps": {"muscles": "Triceps (the long head)", "mistakes": "Elbows flaring out.", "tall": ""},
    "curl": {"muscles": "Biceps", "mistakes": "Elbows moving forward; swinging.", "tall": ""},
    "plank": {"muscles": "Abs, obliques, glutes, shoulders", "mistakes": "Hips piked or sagging; holding the breath.",
              "tall": "A long body is a long lever, so the plank is genuinely harder for you; train it often with short rests."},
    "run": {"muscles": "Heart, lungs, legs", "mistakes": "Starting too fast; overstriding (heel landing far ahead of the hips).",
            "tall": "Tall runners tend to overstride: think quick light steps (about 170-180 a minute) landing under the hips."},
    "sprint": {"muscles": "Glutes, hamstrings, quads, calves", "mistakes": "Standing up too early; slow turns.", "tall": "Get low into the turns at the 25 m line; long legs need more steps to stop."},
    "sled_drag": {"muscles": "Quads, glutes, grip", "mistakes": "Standing tall; long steps.", "tall": ""},
    "kettlebell_carry": {"muscles": "Grip, traps, core", "mistakes": "Shrugging; leaning.", "tall": ""},
    "calf_raise": {"muscles": "Calves", "mistakes": "Bouncing; half reps.", "tall": ""},
    "hip_thrust": {"muscles": "Glutes", "mistakes": "Arching the lower back at the top.", "tall": ""},
    "hip_abduction": {"muscles": "Glutes (glute medius, the side of the hip)", "mistakes": "Swinging or bouncing the weight; leaning the body to lift the leg higher.", "tall": ""},
    "glute_kickback": {"muscles": "Glutes (glute max)", "mistakes": "Arching the lower back to kick higher; swinging the leg.", "tall": ""},
    "step_up": {"muscles": "Glutes, quads", "mistakes": "Pushing off the back foot; a box so high the knee caves in; dropping down fast.", "tall": ""},
    "back_squat": {"muscles": "Quads, glutes, adductors, trunk", "mistakes": "Heels lifting; knees caving in; hips shooting up first out of the bottom.",
                   "tall": "Long femurs: a slightly wider stance with the toes turned out, or small plates under the heels, keeps the chest up."},
    "nordic_curl": {"muscles": "Hamstrings (lengthening under load)", "mistakes": "Bending at the hips; dropping the last half; doing too many too soon.", "tall": "A long body is a long lever: expect fewer reps, and start with a band for help if needed."},
    "box_jump": {"muscles": "Glutes, quads, calves", "mistakes": "A box so high you land in a deep squat; jumping back down.", "tall": ""},
    "broad_jump": {"muscles": "Glutes, hamstrings, quads", "mistakes": "Landing stiff-legged; rushing reps.", "tall": ""},
    "pallof_press": {"muscles": "Obliques, deep trunk", "mistakes": "Letting the hips turn toward the cable.", "tall": ""},
    "copenhagen_plank": {"muscles": "Adductors, obliques", "mistakes": "Hips sagging; holding the breath.", "tall": "Use the knee-on-bench version first: your long leg is a long lever."},
    "sdc": {"muscles": "Whole body: legs, grip, lungs", "mistakes": "Standing tall on the drag; missing the line on a turn; walking the carry.", "tall": "Get low early before each turn."},
    "side_plank": {"muscles": "Obliques, hip abductors", "mistakes": "Hips dropping or rolling forward.", "tall": ""},
    "dead_bug": {"muscles": "Deep abdominals", "mistakes": "Lower back lifting off the floor.", "tall": ""},
    "hanging_leg_raise": {"muscles": "Abs, hip flexors, grip", "mistakes": "Swinging; only lifting the legs without curling the pelvis.", "tall": ""},
    "face_pull": {"muscles": "Rear delts, rotator cuff", "mistakes": "Too heavy; elbows dropping.", "tall": ""},
    "farmer_carry": {"muscles": "Grip, traps, trunk", "mistakes": "Leaning or shrugging; steps too long.", "tall": ""},
    "pushdown": {"muscles": "Triceps", "mistakes": "Elbows drifting forward.", "tall": ""},
}

# Warm-ups by session type (RAMP). Each phase: list of (exercise key, dose).
WARMUPS = {
    "upper": [("Raise", [("jumping_jack", "2 min (or easy bike / rower)"), ("arm_circles", "20 each way")]),
              ("Activate & mobilize", [("band_pull_apart", "2 x 15"), ("scap_pushup", "1 x 10"), ("cat_cow", "8 slow"),
                                       ("worlds_greatest_stretch", "3 per side")]),
              ("Potentiate", [("ramp_sets", "First exercise: 2-3 lighter sets of 5 (about 50%, 70%, 85% of the working weight), 60 s apart")])],
    "lower": [("Raise", [("jumping_jack", "2 min (or easy bike)"), ("leg_swing", "10 front-back + 10 side each leg")]),
              ("Activate & mobilize", [("glute_bridge", "2 x 10"), ("worlds_greatest_stretch", "3 per side"),
                                       ("squat", "1 x 8 bodyweight, slow and deep"), ("inchworm", "5")]),
              ("Potentiate", [("ramp_sets", "First lift: 3-4 build-up sets (bar, then about 50%, 70%, 85% for 3-5 reps), 90 s apart")])],
    "run": [("Raise", [("run", "8-10 min easy jog (conversational)")]),
            ("Activate & mobilize", [("leg_swing", "10 front-back + 10 side each leg"), ("glute_bridge", "1 x 10"),
                                     ("high_knees", "2 x 20 m A-skips + 2 x 20 m high knees")]),
            ("Potentiate", [("sprint", "4 x 80 m strides: build to about 90% speed, walk back")])],
    "sdc": [("Raise", [("run", "5 min easy jog")]),
            ("Activate & mobilize", [("leg_swing", "10 + 10 each leg"), ("worlds_greatest_stretch", "3 per side"),
                                     ("lateral_shuffle", "2 x 15 m each way, easy"), ("glute_bridge", "1 x 10")]),
            ("Potentiate", [("sprint", "3 x 25 m build-ups, then 1 easy rehearsal lane of drag, shuffle and carry")])],
    # the glutes plan (a beginner, often with no bar): no percentages, just one or two easy sets of the first lift
    "glute": [("Raise", [("jumping_jack", "2 min easy (or march on the spot)"), ("leg_swing", "10 front-back + 10 side each leg")]),
              ("Activate & mobilize", [("glute_bridge", "2 x 10, squeeze 2 s at the top"), ("worlds_greatest_stretch", "3 per side"),
                                       ("squat", "1 x 8 bodyweight, slow and deep")]),
              ("Potentiate", [("ramp_sets", "First lift: 1-2 easy sets of 8 with no weight, or about half of what you will use")])],
    "walk": [("Raise", [("run", "3-5 min slow walk, then pick up the pace")]),
             ("Activate & mobilize", [("leg_swing", "10 front-back + 10 side each leg")])],
    "mixed": [("Raise", [("jumping_jack", "3 min (or easy jog)")]),
              ("Activate & mobilize", [("worlds_greatest_stretch", "3 per side"), ("glute_bridge", "1 x 10"),
                                       ("band_pull_apart", "1 x 15"), ("inchworm", "5")]),
              ("Potentiate", [("ramp_sets", "2-3 build-up sets of the first exercise")])],
}

COOLDOWNS = {
    "upper": [("doorway_pec_stretch", "45 s each side"), ("childs_pose", "60 s, then 30 s each side"),
              ("cat_cow", "6 slow"), ("box_breathing", "2 min")],
    "lower": [("couch_stretch", "45 s each side"), ("hamstring_stretch", "45 s each side"), ("pigeon", "45 s each side"),
              ("box_breathing", "2 min")],
    "run": [("run", "5 min walk / very easy jog"), ("calf_stretch", "45 s each side (straight then bent knee)"),
            ("couch_stretch", "45 s each side"), ("hamstring_stretch", "45 s each side")],
    "sdc": [("run", "5 min walk"), ("couch_stretch", "45 s each side"), ("pigeon", "45 s each side"), ("box_breathing", "2 min")],
    "mixed": [("couch_stretch", "45 s each side"), ("doorway_pec_stretch", "45 s each side"), ("hamstring_stretch", "45 s each side"),
              ("box_breathing", "2 min")],
    "glute": [("couch_stretch", "45 s each side"), ("hamstring_stretch", "45 s each side"), ("pigeon", "45 s each side"),
              ("box_breathing", "2 min")],
    "walk": [("calf_stretch", "45 s each side (straight then bent knee)"), ("couch_stretch", "45 s each side")],
}

_KIND_WORDS = {
    "run": ("run", "jog", "interval", "tempo", "mile", "400", "800", "600", "stride", "track"),
    "sdc": ("sdc", "sprint", "drag", "shuffle", "carry", "sled"),
    "lower": ("deadlift", "squat", "lunge", "rdl", "split", "hip thrust", "leg", "calf", "hinge", "glute", "step-up", "abduction"),
    "upper": ("press", "push", "bench", "dip", "fly", "row", "pull", "raise", "curl", "tricep", "chest", "delt", "lat"),
}


def session_kind(day: dict) -> str:
    """Classify a planned day (upper / lower / run / sdc / mixed) so it gets the right warm-up and cool-down."""
    title = str(day.get("title", "")).lower()
    if title.startswith("glutes "):                           # the glutes plan's own sessions (core/coach_engine _SESSION)
        return "glute"
    if title.startswith("easy walk"):
        return "walk"
    text = " ".join([str(day.get("title", ""))] + [str(i.get("exercise", "")) for i in day.get("items", [])]).lower()
    ranked = sorted(((sum(text.count(w) for w in words), k) for k, words in _KIND_WORDS.items()), reverse=True)
    (top, kind), (second, _) = ranked[0], ranked[1]
    if top == 0:
        return "mixed"
    if second and second >= top * 0.6:
        return "mixed"
    return kind


def warmup_for(kind: str) -> list[dict]:
    return [{"phase": phase, "items": [{"exercise": k, "dose": dose} for k, dose in items]}
            for phase, items in WARMUPS.get(kind, WARMUPS["mixed"])]


def cooldown_for(kind: str) -> list[dict]:
    return [{"exercise": k, "dose": dose} for k, dose in COOLDOWNS.get(kind, COOLDOWNS["mixed"])]


# The science the trainer follows for warm-ups, physique and tall lifters (added to fitness.SCIENCE).
SCIENCE_EXTRA = """WARM-UP / COOL-DOWN: every session starts with a RAMP warm-up (Jeffreys 2007; 8-12 min): Raise (easy cardio),
Activate & Mobilize (glutes, upper back, the ranges the session needs), Potentiate (ramp-up sets or strides). Warm-up
type measurably changes sprint and jump results (randomized crossover, Girginer et al. 2025), and static stretches
under about 60 s per muscle have little effect on strength (Behm et al. 2016), so long static stretching goes in the
cool-down (30-60 s holds + slow breathing to switch into recovery).
RANGE OF MOTION: train muscles hard in the stretched position: full range with a controlled 2-3 s lowering, and
long-length partials as a finisher; lengthened partials beat shortened partials in 7 of 8 studies and have a small
edge over full range. Pick exercises loaded at the stretch: incline press with a deep bottom, deficit push-ups,
flyes, overhead triceps work (about 40% more triceps growth than pushdowns, Maeo et al. 2023), incline curls, RDLs,
deep split squats, calf raises with a 2 s bottom pause.
AESTHETICS (V-taper): shoulder width (side delts: lateral raises 2-3x a week, 12-20 sets/week), lat width
(pull-ups/pulldowns), upper chest (incline first), arms, and lower body fat; each muscle 2x a week, 10-20 hard sets,
1-3 reps in reserve, double progression (add reps inside the range, then add weight).
TALL LIFTER (6'2.5"): longer levers mean more range per rep, so lifting less than shorter lifters at the same size is
normal; set up carefully (trap bar, heel elevation or a wider stance for squats, slightly wider bench grip), avoid
overstriding when running (cadence about 170-180), and the plank is harder for a long body: train it often.
More sources: Jeffreys 2007 (RAMP); Behm et al. 2016 stretching review; Maeo et al. 2023 (overhead triceps);
Pedrosa et al. 2022 and Kassiano et al. 2023 (long-length training); Stronger by Science lengthened-partials review."""

# Health notes for someone of Haitian (and broadly African / Caribbean) descent. Evidence-based only: there is
# no good evidence that heritage changes how muscles respond to training, so the program itself is the same;
# these are the screening and nutrition points that do differ on average.
HEALTH_NOTES_HAITIAN = [
    ("Sickle cell trait", "Roughly 8-15% of people of Haitian descent carry the sickle hemoglobin gene. Most carriers never have "
     "problems, but the trait is linked to a higher risk of exertional rhabdomyolysis (Army study of about 48,000 soldiers, NEJM "
     "2016) and, rarely, exertional collapse (ECAST). The Army now tests everyone: check your status in your medical record or at "
     "your next periodic health assessment. Either way: build intensity gradually, hydrate well (the trait can make it harder to "
     "concentrate urine), respect work-rest cycles in heat, and STOP right away for unusual cramping, weakness or breathlessness "
     "out of proportion to the effort; that is never something to push through."),
    ("Vitamin D", "Darker skin makes less vitamin D from sunlight, and deficiency is common and under-diagnosed in the military; "
     "low vitamin D is tied to stress fractures, and calcium plus vitamin D cut stress fractures about 20% in Navy recruits "
     "(Lappe 2008). With high running volume, ask your provider to check your 25-hydroxy vitamin D, and eat vitamin D and "
     "calcium foods (eggs, sardines or salmon, fortified or lactose-free milk, fortified cereal)."),
    ("Lactose", "Lactose intolerance is much more common in people of African descent. If milk bothers you, whey isolate, "
     "lactose-free milk and Greek yogurt are usually fine and still give the protein and calcium."),
    ("Blood pressure", "High blood pressure starts earlier and is more common in Black adults and runs in many Haitian families. "
     "Haitian cooking often leans on bouillon cubes and salted meats: get the flavour from epis, garlic, thyme, citrus and "
     "peppers and use half a cube. Get your BP checked at sick call or a pharmacy now and then."),
    ("Haitian food fits the plan", "Diri ak pwa (rice and beans) is a complete protein base plus great training carbs; legim, "
     "lalo and bouyon are packed with vegetables; poul nan sos, tasso and griot (oven or air-fried instead of deep-fried), "
     "chiktay, akra and eggs bring the protein; bannann peze baked or air-fried is a good pre-run carb; mayi moulen and labouyi "
     "work for breakfast. The kitchen plans keep these favourites, sized to hit your protein."),
]


def health_notes(profile: dict) -> list[dict]:
    heritage = str(profile.get("heritage", "")).lower()
    if any(w in heritage for w in ("haiti", "caribbean", "african", "black")):
        return [{"title": t, "text": x} for t, x in HEALTH_NOTES_HAITIAN]
    return []
