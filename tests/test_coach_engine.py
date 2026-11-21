"""The trainer's rules (core/coach_engine): pure functions, scripted histories, a pinned calendar."""
from datetime import date, timedelta

from core import coach_engine as coach

MON = date(2026, 11, 2)          # a Monday


def _sets(key, day, weight, reps, rir=2, failed=False):
    return [{"key": key, "date": day.isoformat(), "weight": weight, "reps": r, "rir": rir, "failed": failed} for r in reps]


def test_goal_words_and_intake_defaults():
    assert coach.parse_goal("I want to build muscle") == "muscle"
    assert coach.parse_goal("get stronger") == "strength"
    assert coach.parse_goal("lose some fat") == "fat_loss"
    assert coach.parse_goal("max the Army fitness test") == "aft"
    assert coach.parse_goal("be in better shape") == "general"
    assert coach.parse_goal("play some music") == ""
    who = coach.intake({})
    assert (who["goal"], who["days"], who["minutes"], who["level"]) == ("general", 3, 60, "novice")
    assert coach.intake({"test_date": "2027-01-10"})["goal"] == "aft"
    assert coach.level(6) == "novice" and coach.level(20) == "intermediate" and coach.level("48") == "advanced"
    assert coach.gear("bodyweight only") == {"bodyweight"}
    assert {"dumbbell", "pullup_bar"} <= coach.gear("dumbbells and a pull-up bar") and "barbell" not in coach.gear("dumbbells")
    assert coach.gear("full gym") == coach.ALL_GEAR


def test_split_follows_days_and_training_age():
    def titles(**p):
        return [d["title"] for d in coach.build_plan({"goal": "muscle", **p}, MON)["weeks"][0]["days"]]
    assert titles(days_per_week="2") == ["Full body A", "Full body B"]
    assert titles(days_per_week="4", training_months="24") == ["Upper body A", "Lower body A", "Upper body B", "Lower body B"]
    assert titles(days_per_week="6", training_months="24")[:3] == ["Push", "Pull", "Legs"]
    assert "Easy conditioning" in titles(days_per_week="6", training_months="3")      # a novice is not given six hard days


def test_weekly_volume_sits_in_the_range_and_every_big_muscle_is_trained_twice():
    for months, (low, high) in (("6", (6, 12)), ("24", (10, 20))):
        plan = coach.build_plan({"goal": "muscle", "days_per_week": "4", "training_months": months, "equipment": "gym"}, MON)
        week = plan["weeks"][0]
        vol = coach.weekly_volume(week)
        for muscle in coach.BIG:
            assert low <= vol[muscle] <= high, (months, muscle, vol)
            hit = sum(1 for d in week["days"] if any(muscle in coach.TAGS[i["key"]]["primary"] + coach.TAGS[i["key"]]["secondary"]
                                                     for i in d["items"]))
            assert hit >= 2, (muscle, hit)
        assert not plan["notes"]


def test_plan_respects_equipment_time_and_the_stored_shape():
    plan = coach.build_plan({"goal": "strength", "days_per_week": "3", "equipment": "bodyweight only", "session_minutes": "30"}, MON)
    for week in plan["weeks"]:
        for day in week["days"]:
            assert day["day"] in coach.DAYS and day["title"] and day["why"]
            for it in day["items"]:
                assert coach.can_do(it["key"], {"bodyweight"}) and it["exercise"] and it["sets"].isdigit() and it["reps"]
            assert coach._minutes(day["items"]) <= 30
    gym = coach.build_plan({"goal": "strength", "days_per_week": "3", "equipment": "gym"}, MON)
    first = gym["weeks"][0]["days"][0]["items"][0]
    assert (first["lo"], first["hi"], first["rest_s"]) == (3, 6, 180)                  # the main lift is heavy and rested
    assert len(gym["weeks"]) == 8 and gym["start"] == MON.isoformat() and gym["generated"]


def test_scheduled_deload_and_aft_taper():
    plan = coach.build_plan({"goal": "muscle", "days_per_week": "4", "training_months": "24"}, MON)
    normal, light = plan["weeks"][4], plan["weeks"][5]
    assert "Deload" in light["focus"]
    assert sum(int(i["sets"]) for d in light["days"] for i in d["items"]) <= 0.6 * sum(int(i["sets"]) for d in normal["days"] for i in d["items"])
    aft = coach.build_plan({"goal": "aft", "days_per_week": "5", "test_date": (MON + timedelta(days=34)).isoformat()}, MON)
    assert len(aft["weeks"]) == 5 and "Taper" in aft["weeks"][-1]["focus"]
    keys = {i["key"] for d in aft["weeks"][0]["days"] for i in d["items"]}
    assert {"deadlift", "hand_release_pushup", "plank", "run", "sprint"} <= keys       # every test event is trained


def test_estimated_one_rep_max_uses_reps_in_reserve():
    assert coach.e1rm(200, 5, 0) == round(200 * (1 + 5 / 30), 1)
    assert coach.e1rm(200, 5, 2) > coach.e1rm(200, 5, 0)
    assert coach.e1rm(225, 1, 0) == 225
    assert coach.e1rm(100, 15) is None and coach.e1rm(0, 5) is None


def test_double_progression():
    item = {"key": "bench_press", "exercise": "Bench press", "lo": 8, "hi": 12, "kind": "lift"}
    start = coach.next_target(item, [])
    assert start["load"] is None and start["mode"] == "start" and "No bench press logged yet" in start["why"]
    more = coach.next_target(item, [_sets("bench_press", MON, 135, [10, 9, 8])])
    assert (more["load"], more["mode"]) == (135, "add_reps") and "135 x 10, 9, 8" in more["why"]
    up = coach.next_target(item, [_sets("bench_press", MON, 135, [12, 12, 12])])
    assert (up["load"], up["mode"]) == (140, "add_load") and "goes up to 140" in up["why"]
    legs = coach.next_target({"key": "deadlift", "lo": 3, "hi": 5, "kind": "lift"}, [_sets("deadlift", MON, 275, [5, 5, 5])])
    assert legs["load"] == 285                                                        # lower-body lifts take the bigger step
    easy = coach.next_target(item, [_sets("bench_press", MON, 135, [9, 9, 9], rir=4)])
    assert easy["mode"] == "add_load"


def test_a_stalled_or_failed_lift_changes_the_progression():
    item = {"key": "bench_press", "exercise": "Bench press", "lo": 8, "hi": 12, "kind": "lift"}
    days = [MON, MON + timedelta(days=3), MON + timedelta(days=7)]
    stalled = coach.next_target(item, [_sets("bench_press", d, 150, [9, 8, 8]) for d in days])
    assert (stalled["load"], stalled["mode"]) == (135, "reset") and "has not moved in 3 sessions" in stalled["why"]
    falling = coach.next_target(item, [_sets("bench_press", d, 150, r) for d, r in zip(days, ([10, 10], [9, 9], [8, 8]))])
    assert falling["mode"] == "reset" and "fell two sessions in a row" in falling["why"]
    failed = coach.next_target(item, [_sets("bench_press", MON, 150, [8, 7], failed=True)])
    assert failed["mode"] == "reset" and failed["load"] == 135
    moving = coach.next_target(item, [_sets("bench_press", d, 150, r) for d, r in zip(days, ([8, 8], [9, 8], [10, 9]))])
    assert moving["mode"] == "add_reps"


def _plan():
    return coach.build_plan({"goal": "muscle", "days_per_week": "4", "training_months": "24", "equipment": "gym"}, MON)


def test_a_missed_session_moves_to_the_next_free_day_and_is_never_stacked():
    plan = _plan()
    wed = MON + timedelta(days=2)                              # Monday done, Tuesday missed, Wednesday is a free day
    day = coach.adapt_day(plan, _sets("bench_press", MON, 135, [10, 9, 8]), {}, wed)
    assert day["status"] == "train" and day["title"] == "Lower body A"
    assert day["adjustments"][0]["kind"] == "moved" and "Tuesday's lower body a was not logged, so I moved it to today" in day["adjustments"][0]["reason"]
    thu = coach.adapt_day(plan, _sets("bench_press", MON, 135, [10, 9, 8]), {}, MON + timedelta(days=3))
    assert thu["title"] == "Upper body B"                      # the planned day keeps its own session
    assert thu["schedule"][5]["session"]["title"] == "Lower body A" and thu["schedule"][5]["moved_from"] == "Tue"
    sun = coach.adapt_day(plan, [], {}, MON + timedelta(days=6))
    kinds = [a["kind"] for a in sun["adjustments"]]
    assert sun["title"] == "Upper body A" and kinds.count("dropped") == 3 and "dropped rather than crammed in" in sun["adjustments"][-1]["reason"]


def test_days_before_the_plan_was_saved_are_not_missed():
    plan = dict(_plan(), saved=(MON + timedelta(days=2)).isoformat())
    day = coach.adapt_day(plan, [], {}, MON + timedelta(days=2))
    assert day["status"] == "rest" and not day["adjustments"]


def test_poor_sleep_or_soreness_trims_the_day_with_a_reason():
    plan = _plan()
    normal = coach.adapt_day(plan, [], {}, MON)
    tired = coach.adapt_day(plan, [], {MON.isoformat(): {"sleep_hours": 5}}, MON)
    assert [int(i["sets"]) for i in tired["items"]] == [int(i["sets"]) - 1 for i in normal["items"]]
    assert tired["adjustments"][0] == {"kind": "readiness", "reason": "You slept 5 hours, so I took one set off each exercise and kept the loads."}
    wrecked = coach.adapt_day(plan, [], {MON.isoformat(): {"sleep_hours": 5, "soreness": 5}}, MON)
    assert len(wrecked["items"]) == len(normal["items"]) - 1 and "and dropped" in wrecked["adjustments"][0]["reason"]
    assert not normal["adjustments"]


def test_reactive_deload_needs_two_signs():
    plan = _plan()
    day = MON + timedelta(days=7)
    fails = _sets("bench_press", MON, 150, [8, 6], failed=True) + _sets("bench_press", MON + timedelta(days=3), 150, [7, 5], failed=True)
    assert coach.deload_due(fails, {}, day) == (False, "")
    low = {(MON + timedelta(days=n)).isoformat(): {"sleep_hours": 5} for n in (1, 2, 4)}
    due, why = coach.deload_due(fails, low, day)
    assert due and "4 missed sets and 3 low-recovery mornings" in why
    adapted = coach.adapt_day(plan, fails, low, day, done={(MON + timedelta(days=4)).isoformat()})
    assert adapted["deload"] == why and adapted["adjustments"][0]["kind"] == "deload"
    usual = plan["weeks"][1]["days"][0]["items"]
    assert sum(int(i["sets"]) for i in adapted["items"]) <= 0.67 * sum(int(i["sets"]) for i in usual)


def test_a_week_away_gets_a_lighter_way_back():
    plan = _plan()
    day = MON + timedelta(days=14)
    back = coach.adapt_day(plan, _sets("bench_press", MON, 150, [10, 9, 9]), {}, day)
    reason = next(a["reason"] for a in back["adjustments"] if a["kind"] == "reentry")
    assert "It has been 14 days since your last session" in reason
    bench = next(i for i in back["items"] if i["key"] == "bench_press")
    assert bench["load"] == 135 and bench["last"] == "150 x 10, 9, 9"


def test_plan_end_and_free_text_plans():
    plan = _plan()
    assert coach.adapt_day(plan, [], {}, MON + timedelta(days=70))["status"] == "ended"
    assert coach.adapt_day(plan, [], {}, MON - timedelta(days=1))["status"] == "before"
    assert coach.adapt_day({}, [], {}, MON)["status"] == "none"
    old = {"start": MON.isoformat(), "weeks": [{"week": 1, "focus": "Base", "days": [{"day": "Monday", "title": "Strength", "items": [
        {"exercise": "Bench press", "sets": "4", "reps": "8-12", "target": "RIR 2"},
        {"exercise": "Intervals: 400 m repeats", "sets": "6", "reps": "400m", "target": "1:40"}]}]}]}
    day = coach.adapt_day(old, _sets("bench_press", MON - timedelta(days=3), 135, [12, 12, 12, 12]), {}, MON)
    assert day["items"][0]["load"] == 140 and "load" not in day["items"][1]
    assert any(a["kind"] == "load" and "goes up to 140" in a["reason"] for a in day["adjustments"])


def test_substitution_keeps_the_movement_and_respects_equipment():
    key, why = coach.substitute("bench_press", coach.ALL_GEAR)
    assert coach.TAGS[key]["pattern"] == "h_push" and "the same movement" in why
    key, _ = coach.substitute("bench_press", {"bodyweight"})
    assert key == "hand_release_pushup"
    key, _ = coach.substitute("pull_up", {"dumbbell", "bodyweight"})
    assert "back" in coach.TAGS[key]["primary"]
    assert coach.substitute("bench_press", coach.ALL_GEAR, taken={"incline_press", "dip", "hand_release_pushup"})[0] in ("chest_fly", "cable_crossover")
    assert coach.key_of("the bench") == "bench_press" and coach.key_of("Trap-bar deadlift (MDL)") == "deadlift" and coach.key_of("guitar") == ""


def test_progress_series_and_weekly_volume():
    sets = _sets("bench_press", MON, 135, [8, 8]) + _sets("bench_press", MON + timedelta(days=7), 145, [8, 8]) + \
        _sets("curl", MON + timedelta(days=7), 30, [12, 12, 12])
    p = coach.progress(sets, MON + timedelta(days=8))
    bench = p["lifts"][0]
    assert bench["key"] == "bench_press" and len(bench["points"]) == 2 and bench["change"] > 0
    assert p["volume"]["chest"] == 2 and p["volume"]["biceps"] == 3 and p["volume"]["triceps"] == 1
    assert [w["sets"] for w in p["weeks"]][-2:] == [2, 5]


# ---- the glutes goal: a beginner's lower-body plan (Maya, 2026-11-20: "big butt, feminine not muscular") ----------
def _glute_plan(days=3, equipment="gym", months=0):
    return coach.build_plan({"goal": "big butt, feminine not muscular", "days_per_week": days, "session_minutes": 60,
                             "equipment": equipment, "training_months": months}, MON)


def test_glute_goal_words():
    for said in ("big butt", "a bigger booty", "grow my glutes", "curves, not muscular", "glutes"):
        assert coach.parse_goal(said) == "glutes", said
    assert coach.parse_goal("build muscle") == "muscle" and coach.parse_goal("lose fat") == "fat_loss"   # unchanged
    assert "glutes" in coach.GOALS


def test_glute_plan_is_glute_first_with_light_upper_body():
    plan = _glute_plan(3)
    assert plan["goal"] == "glutes" and len(plan["weeks"]) == 8 and plan["days_per_week"] == 3
    build = plan["weeks"][2]
    assert [d["day"] for d in build["days"]] == ["Mon", "Wed", "Fri"]                    # a rest day between sessions
    for d in build["days"]:
        first = coach.TAGS[d["items"][0]["key"]]
        assert "glutes" in first["primary"], d["title"]                                  # glutes while she is fresh
        assert coach._minutes(d["items"]) + 10 <= 60
    vol = coach.weekly_volume(build)
    assert vol["glutes"] >= 2 * max(vol["quads"], vol["hamstrings"], vol["back"])        # the emphasis is real
    assert vol["chest"] + vol["shoulders"] + vol["biceps"] + vol["triceps"] <= 8         # posture work, not size
    upper = [i for d in build["days"] for i in d["items"] if not coach.TAGS[i["key"]]["lower"] and coach.TAGS[i["key"]]["pattern"] != "core"]
    assert upper and all(i["sets"] == "2" for i in upper)
    assert all(i.get("step") == 5 for d in build["days"] for i in d["items"] if i.get("kind") == "lift")


def test_glute_plan_starts_light_then_builds_then_deloads():
    plan = _glute_plan(3)
    sets = lambda n: sum(int(i["sets"]) for d in plan["weeks"][n - 1]["days"] for i in d["items"])
    assert sets(1) == sets(2) < sets(3) == sets(7) and sets(8) < sets(1)
    assert "fewer sets" in plan["why"] and any("doctor" in n for n in plan["notes"]) and any("bulky" in n for n in plan["notes"])


def test_glute_plan_extra_days_are_walks_never_more_lifting():
    for days, lifting in ((2, 2), (3, 3), (4, 3), (5, 3), (6, 3)):
        week = _glute_plan(days)["weeks"][2]
        lifts = [d for d in week["days"] if d["title"].startswith("Glutes")]
        assert len(lifts) == lifting and len(week["days"]) == min(days, 5), days
        assert all("walk" in d["title"].lower() for d in week["days"] if d not in lifts)


def test_glute_plan_works_at_home_and_with_no_equipment():
    for equipment in ("home dumbbells bench bands", "bodyweight only"):
        have = coach.gear(equipment)
        week = _glute_plan(3, equipment)["weeks"][0]
        for d in week["days"]:
            assert len(d["items"]) >= 3, (equipment, d["title"])
            assert all(coach.can_do(i["key"], have) for i in d["items"] if i.get("kind") != "cardio")
            assert "glutes" in coach.TAGS[d["items"][0]["key"]]["primary"]


def test_glute_loads_go_up_five_pounds_and_other_goals_keep_ten():
    item = next(i for i in _glute_plan(3)["weeks"][2]["days"][0]["items"] if i["key"] == "hip_thrust")
    day = MON
    up = coach.next_target(item, [_sets("hip_thrust", day, 40, [12, 12, 12])])
    assert up["mode"] == "add_load" and up["load"] == 45
    other = coach.build_plan({"goal": "muscle", "days_per_week": 3, "equipment": "gym"}, MON)
    lower = next(i for d in other["weeks"][2]["days"] for i in d["items"] if i.get("kind") == "lift" and coach.TAGS[i["key"]]["lower"])
    hi = lower["hi"]
    assert coach.next_target(lower, [_sets(lower["key"], day, 100, [hi, hi, hi])])["load"] == 110


def test_glute_words_do_not_take_over_a_goal_named_outright():
    assert coach.parse_goal("Army Fitness Test, 540 points, stronger glutes and hamstrings") == "aft"
    assert coach.parse_goal("build muscle: V-taper, big shoulders, strong glutes") == "muscle"
    assert coach.parse_goal("lose fat and tone my butt") == "fat_loss"
    assert coach.parse_goal("big butt looking for feminine not muscular") == "glutes"


def test_glute_plan_speaks_to_a_beginner_and_handles_no_weight():
    plan = _glute_plan(3, "bodyweight only")
    bridge = next(i for i in plan["weeks"][2]["days"][0]["items"] if i["key"] == "glute_bridge")
    assert "good reps left" in bridge["target"] and "reserve" not in bridge["target"]
    first = coach.next_target(bridge, [])
    assert "no weight" in first["why"] and "pick a load" not in first["why"]
    top = coach.next_target(bridge, [[{"weight": 0, "reps": 15, "rir": 2} for _ in range(3)]])
    assert top["mode"] == "harder" and top["reps"] == "10-15" and "band" in top["why"]
    more = coach.next_target(bridge, [[{"weight": 0, "reps": 12, "rir": 2} for _ in range(3)]])
    assert more["mode"] == "add_reps"
    assert coach.substitute("hip_thrust", {"bodyweight", "dumbbell"})[0] == "glute_bridge"     # never a deadlift or a swing
    assert (coach.key_of("step ups"), coach.key_of("kickbacks"), coach.key_of("abduction")) == ("step_up", "glute_kickback", "hip_abduction")
    short = coach.build_plan({"goal": "glutes", "days_per_week": 3, "session_minutes": 30, "equipment": "gym", "training_months": 0}, MON)
    day = short["weeks"][2]["days"][0]
    assert day["items"][0]["key"] == "hip_thrust" and int(day["items"][0]["sets"]) >= max(int(i["sets"]) for i in day["items"][1:])
