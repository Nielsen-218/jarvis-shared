"""Project Captain America rules (core/pca_engine + the RIR gate in core/coach_engine.next_target): scripted histories for
every rule. Pure functions, a pinned calendar, no storage."""
import copy
from datetime import date, timedelta

import pytest

from core import coach_engine as coach
from core import pca_engine as engine
from core import pca_program as program

START = date(2026, 11, 23)
PLAN5 = program.build({"days_per_week": 5}, START)


def _day(week, name):
    return next(d for d in PLAN5["weeks"][week - 1]["days"] if d["day"] == name)


def _view(week, name):
    d = copy.deepcopy(_day(week, name))
    return {"status": "train", "title": d["title"], "why": d["why"], "items": d["items"], "adjustments": [], "intensity": d["intensity"],
            "session_kind": d["session"], "schedule": [], "week": week}


def _date(week, name):
    return START + timedelta(days=7 * (week - 1) + coach.DAYS.index(name))


def _ctx(week, name, **more):
    return {"today": _date(week, name).isoformat(), "week": week, **more}


def _item(day, key=None, kind=None):
    return next(i for i in day["items"] if (key is None or i["key"] == key) and (kind is None or i["kind"] == kind))


# ---- the reps-in-reserve gate ----------------------------------------------------------------------------------------
DL = {"key": "deadlift", "exercise": "Trap-bar deadlift", "kind": "lift", "lo": 5, "hi": 5, "rir": 3, "gate": "rir"}


def _ex(day, weight, reps, rirs, failed=False, lo=5, hi=5):
    return [{"key": "deadlift", "date": day, "weight": weight, "reps": r, "rir": q, "failed": failed, "target_lo": lo, "target_hi": hi}
            for r, q in zip(reps, rirs)]


def test_rir_gate_adds_load_only_with_the_reps_and_the_reserve():
    t = coach.next_target(DL, [_ex("2026-11-23", 225, [5, 5, 5], [3, 4, 3])])
    assert (t["load"], t["mode"]) == (235, "add_load") and "at least 3 in reserve" in t["why"]


def test_rir_one_below_the_target_holds():
    t = coach.next_target(DL, [_ex("2026-11-23", 225, [5, 5, 5], [3, 2, 3])])
    assert (t["load"], t["mode"]) == (225, "hold") and "one short" in t["why"]


def test_missing_rir_holds_and_asks():
    t = coach.next_target(DL, [_ex("2026-11-23", 225, [5, 5, 5], [3, None, 3])])
    assert (t["load"], t["mode"]) == (225, "hold") and "reps in reserve" in t["why"]


def test_a_failed_set_takes_ten_percent_off_rounded_down():
    t = coach.next_target(DL, [_ex("2026-11-23", 225, [5, 5, 3], [1, 0, 0], failed=True)])
    assert (t["load"], t["mode"]) == (200, "reset")


def test_zero_in_reserve_twice_when_the_target_is_two_or_more_drops_the_load():
    t = coach.next_target(DL, [_ex("2026-11-16", 225, [5, 5, 5], [2, 1, 0]), _ex("2026-11-23", 225, [5, 5, 5], [1, 0, 0])])
    assert (t["load"], t["mode"]) == (210, "reset") and "Two sessions in a row" in t["why"]


def test_a_changed_rep_target_starts_from_the_last_estimated_max_capped_at_five_percent():
    three = dict(DL, lo=3, hi=3, rir=2)
    capped = coach.next_target(three, [_ex("2026-11-23", 300, [5, 5, 5], [4, 4, 4])])       # e1RM 390 -> 334, capped at 315
    assert (capped["load"], capped["mode"]) == (315, "retarget") and "capped" in capped["why"]
    plain = coach.next_target(dict(DL, lo=3, hi=3, rir=3), [_ex("2026-11-23", 300, [5, 5, 5], [2, 2, 2])])   # 370 / 1.2 = 308 -> 305
    assert (plain["load"], plain["mode"]) == (305, "retarget") and "from 5 to 3 reps" in plain["why"]


def test_items_without_the_gate_keep_the_old_double_progression():
    old = {"key": "deadlift", "exercise": "Trap-bar deadlift", "kind": "lift", "lo": 3, "hi": 5}
    t = coach.next_target(old, [_ex("2026-11-23", 225, [5, 5, 5], [None, None, None])])
    assert (t["load"], t["mode"]) == (235, "add_load")


# ---- tested values ---------------------------------------------------------------------------------------------------
def test_pace_table_from_an_eighteen_minute_two_mile():
    p = engine.paces(18 * 60)
    assert engine.pace_text(p["I"]) == "8:55-9:16 /mi" and engine.pace_text(p["E"]) == "11:15-12:25 /mi"
    assert engine.split_text(p["I"], 400) == "400 m in 2:13-2:18"


def test_aft_points_take_the_highest_row_met_never_interpolated():
    assert engine.aft_points("hrp", 47) == 85 and engine.aft_points("hrp", 48) == 90
    assert engine.aft_points("mdl", 345) == 99 and engine.aft_points("mdl", 350) == 100
    assert engine.aft_points("run", 13 * 60 + 25) == 100 and engine.aft_points("run", 13 * 60 + 26) == 95
    assert engine.aft_points("plk", 60) is None and engine.aft_points("sdc", 103) == 90


def test_baselines_age_and_freshness():
    today = date(2027, 1, 20)
    b = engine.baselines([{"event": "run", "value": 1080, "date": "2026-12-01"}, {"event": "hrp", "value": 40, "date": "2027-01-01"}], today)
    assert b["run"]["fresh"] is False and b["run"]["age_days"] == 50
    assert b["hrp"]["fresh"] is True and b["mdl"] is None


def test_a_stale_two_mile_gives_no_paces_and_a_fresh_one_gives_splits():
    stale = engine.baselines([{"event": "run", "value": 1080, "date": "2026-10-01"}], _date(5, "Wed"))
    run = _item(engine.resolve_day(_view(5, "Wed"), stale), "run")
    assert run["paces"] is None and "talk test" in run["pace_note"]
    fresh = engine.baselines([{"event": "run", "value": 1080, "date": _date(2, "Sat").isoformat()}], _date(5, "Wed"))
    run = _item(engine.resolve_day(_view(5, "Wed"), fresh), "run")
    assert run["pace_lines"][0] == "6 x 400 m in 2:13-2:18 (8:55-9:16 /mi)"


def test_no_test_leaves_the_fallback_and_run_walk_rewrites_weeks_1_and_2():
    day = engine.resolve_day(_view(2, "Wed"), engine.baselines([], _date(2, "Wed")), {"run_walk": True, "week": 2})
    assert _item(day, "hand_release_pushup", "lift")["resolved"] is False
    tue = engine.resolve_day(_view(2, "Tue"), {}, {"run_walk": True, "week": 2})
    assert _item(tue, "run")["target"].startswith("Run/walk for 15 min: 2 min easy jog, 1 min walk")
    later = engine.resolve_day(_view(3, "Tue"), {}, {"run_walk": True, "week": 3})
    assert not later["items"][-1].get("run_walk")


# ---- onboarding ------------------------------------------------------------------------------------------------------
def test_assess_blocks_on_pain_and_on_a_profile_without_clearance():
    assert any("4 out of 10" in b for b in engine.assess({"pain_level": 4})["blocking"])
    assert any("clearance" in b for b in engine.assess({"medical_restriction": True, "provider_cleared": False})["blocking"])
    assert not engine.assess({"medical_restriction": True, "provider_cleared": True, "pain_level": 3})["blocking"]


def test_assess_running_now_run_walk_and_substitutions():
    a = engine.assess({"runs_per_week": 3, "weeks_since_last_run": 0, "pain_level": 0, "equipment": ["dumbbells", "pullup_bar", "track"]})
    assert a["build_args"]["running_now"] is True and a["flags"]["run_walk"] is False
    assert a["flags"]["subs"]["deadlift"]["name"] == "Romanian deadlift" and a["flags"]["subs"]["dip"]["name"] == "Deficit push-up"
    assert engine.assess({"weeks_since_last_run": "unknown"})["flags"]["run_walk"] is True
    assert engine.assess({"runs_per_week": 4, "weeks_since_last_run": 0, "pain_level": 1})["build_args"]["running_now"] is False
    assert engine.clean_answer("days_per_week", "4")[1] and engine.clean_answer("weeks_since_last_run", "unknown") == ("unknown", "")


def test_historical_values_are_labelled_and_never_prescribe():
    assert "not confirmed" in engine.HISTORICAL["label"]
    day = engine.resolve_day(_view(2, "Wed"), engine.baselines([], _date(2, "Wed")))
    assert "41" not in str(day) and "340" not in str(day)


# ---- adapting a day --------------------------------------------------------------------------------------------------
BAD = {"sleep_hours": 4.5, "energy": 2}


def test_two_low_signs_on_a_high_day_convert_the_hard_work():
    day = engine.adapt(_view(5, "Wed"), _ctx(5, "Wed", checkin=BAD))
    run = _item(day, "run")
    assert run["run"]["type"] == "easy" and run["reps"] == "27 min"
    fri = engine.adapt(_view(5, "Fri"), _ctx(5, "Fri", checkin=BAD))
    assert _item(fri, "sprint")["exercise"] == "Build-ups: 6 x 40 m"
    ready = next(a for a in fri["adjustments"] if a["kind"] == "readiness")
    assert ready["overridable"] is True and "pulled hamstring" in ready["reason"]


def test_tests_are_postponed_when_under_recovered_and_main_lifts_ease():
    aft = engine.adapt(_view(12, "Sat"), _ctx(12, "Sat", checkin=BAD))
    assert not [i for i in aft["items"] if i["kind"] == "test"] and len(aft["postponed"]) == 5
    mon = engine.adapt(_view(5, "Mon"), _ctx(5, "Mon", checkin=BAD))
    dl = _item(mon, "deadlift")
    assert dl["rir"] == 3 and dl["sets"] == "3"                                      # planned 4 x 4 at 2 in reserve


def test_one_low_sign_leaves_the_conversion_to_the_coach_trim():
    day = engine.adapt(_view(5, "Wed"), _ctx(5, "Wed", checkin={"energy": 2}))
    assert _item(day, "run")["run"]["type"] == "intervals"


def test_short_sleep_alone_converts_a_sprint_day():
    day = engine.adapt(_view(5, "Fri"), _ctx(5, "Fri", checkin={"sleep_hours": 4.5}))
    assert _item(day, "sprint")["exercise"].startswith("Build-ups")


def test_override_removes_overridable_changes_but_never_pain():
    pain = [{"area": "right knee", "level": 5, "since": _date(5, "Mon").isoformat()}]
    day = engine.adapt(_view(5, "Mon"), _ctx(5, "Mon", checkin=BAD, pain=pain, override=True))
    keys = [i["key"] for i in day["items"]]
    assert "deadlift" not in keys and "back_squat" not in keys and "box_jump" not in keys
    kinds = {a["kind"]: a["overridable"] for a in day["adjustments"]}
    assert kinds["pain"] is False and "readiness" not in kinds and kinds["override"] is False


def test_pain_turns_running_into_the_bike_and_holds_every_load():
    pain = [{"area": "ankle", "level": 4, "since": _date(5, "Mon").isoformat()}]
    view = _view(5, "Tue")
    _item(view, "bench_press").update(load=190, mode="add_load")
    day = engine.adapt(view, _ctx(5, "Tue", pain=pain, last_loads={"bench_press": 185}))
    assert _item(day, "bike")["exercise"] == "Bike or rower" and _item(day, "bench_press")["load"] == 185


def test_mild_pain_trains_without_progression_and_long_pain_says_see_a_medic():
    pain = [{"area": "shoulder", "level": 2, "since": _date(4, "Mon").isoformat()}]
    view = _view(5, "Tue")
    _item(view, "bench_press").update(load=190, mode="add_load")
    day = engine.adapt(view, _ctx(5, "Tue", pain=pain, last_loads={"bench_press": 185}))
    note = next(a for a in day["adjustments"] if a["kind"] == "pain")
    assert _item(day, "bench_press")["load"] == 185 and "medic or physical therapist" in note["reason"]
    assert "diagnos" not in note["reason"]


def test_a_hard_unit_run_this_week_replaces_the_hard_run():
    ext = [{"kind": "unit_run", "minutes": 30, "rpe": 8, "date": _date(5, "Mon").isoformat()}]
    day = engine.adapt(_view(5, "Wed"), _ctx(5, "Wed", external=ext))
    assert _item(day, "run")["run"]["type"] == "easy"
    assert next(a for a in day["adjustments"] if a["kind"] == "external")["overridable"] is True


def test_a_long_ruck_blocks_sprints_for_48_hours_and_counts_as_the_long_run():
    ext = [{"kind": "ruck", "minutes": 110, "rpe": 6, "miles": 7, "date": _date(5, "Thu").isoformat()}]
    fri = engine.adapt(_view(5, "Fri"), _ctx(5, "Fri", external=ext))
    assert "sprint" not in [i["key"] for i in fri["items"]] and "sdc" not in [i["key"] for i in fri["items"]]
    sat = engine.adapt(_view(5, "Sat"), _ctx(5, "Sat", external=ext))
    assert not [i for i in sat["items"] if (i.get("run") or {}).get("type") == "long"]


def test_a_hard_unit_leg_day_eases_monday():
    ext = [{"kind": "unit_strength", "minutes": 50, "rpe": 8, "date": (_date(5, "Mon") - timedelta(days=1)).isoformat()}]
    day = engine.adapt(_view(5, "Mon"), _ctx(5, "Mon", external=ext))
    assert _item(day, "deadlift")["rir"] == 3 and _item(day, "deadlift")["sets"] == "3"
    assert _item(day, "calf_raise")["rir"] == 2                                     # accessories untouched


def test_a_moved_hard_session_next_to_another_hard_day_is_dropped():
    wed, thu, fri = _day(5, "Wed"), _day(5, "Thu"), _day(5, "Fri")
    rows = [{"day": d, "session": None, "moved_from": ""} for d in coach.DAYS]
    rows[2] = {"day": "Wed", "session": wed, "moved_from": ""}
    rows[3] = {"day": "Thu", "session": wed, "moved_from": "Wed"}
    rows[4] = {"day": "Fri", "session": fri, "moved_from": ""}
    view = _view(5, "Wed")
    day = engine.adapt(view, _ctx(5, "Thu", schedule=rows))
    assert day["status"] == "rest" and day["adjustments"][-1]["kind"] == "dropped"
    rows[4] = {"day": "Fri", "session": thu, "moved_from": ""}
    rows[2] = {"day": "Wed", "session": thu, "moved_from": ""}
    assert engine.adapt(_view(5, "Wed"), _ctx(5, "Thu", schedule=rows))["status"] == "train"


def test_missed_sessions_move_by_priority():
    week = {"days": [dict(_day(5, "Mon")), dict(_day(5, "Tue")), dict(_day(5, "Wed"))]}
    rows, _ = coach.schedule_week(week, _date(5, "Mon"), set(), _date(5, "Thu"), None, {"run_quality": 0, "lower_strength": 1, "upper_a": 3})
    assert rows[3]["session"]["session"] == "run_quality" and rows[4]["session"]["session"] == "lower_strength"
    plain, _ = coach.schedule_week(week, _date(5, "Mon"), set(), _date(5, "Thu"))
    assert plain[3]["session"]["session"] == "lower_strength"                        # without a priority: oldest first


def test_the_single_run_cap_is_110_percent_of_the_longest_recent_run():
    runs = [{"date": (_date(6, "Sat") - timedelta(days=n)).isoformat(), "minutes": m} for n, m in ((3, 20), (6, 25), (10, 30))]
    day = engine.adapt(_view(6, "Sat"), _ctx(6, "Sat", runs=runs))                 # the planned 36-minute long run
    assert _item(day, "run")["reps"] == "33 min"
    cap = next(a for a in day["adjustments"] if a["kind"] == "run_cap")
    assert "longest run in the last 30 days was 30 minutes" in cap["reason"]
    assert engine.run_cap(runs[:2], _date(6, "Sat")) == (None, None)               # fewer than three runs: no cap yet


def test_the_80_percent_rule_repeats_last_weeks_minutes():
    hist = {"planned": {4: 100, 5: 120}, "runs": [{"week": 4, "minutes": 60, "rpe": 4, "kind": "easy"}]}
    out = engine.next_run_adjust(hist, 5)
    assert out["progress"] is False and out["cap_min"] == 100 and round(out["scale"], 3) == 0.833
    good = {"planned": {4: 100, 5: 120}, "runs": [{"week": 4, "minutes": 85, "rpe": 4, "kind": "easy"}]}
    assert engine.next_run_adjust(good, 5)["progress"] is True
    hard_easy = {"planned": {4: 100, 5: 120}, "runs": [{"week": 4, "minutes": 95, "rpe": 7, "kind": "easy"}]}
    assert engine.next_run_adjust(hard_easy, 5)["progress"] is False
    assert engine.next_run_adjust(good, 5, {"pain_open": True})["progress"] is False


def test_an_interval_session_that_ended_early_repeats_next_week():
    prev = _item(_view(6, "Wed"), "run")
    hist = {"planned": {6: 120, 7: 130}, "runs": [{"week": 6, "minutes": 110, "rpe": 8, "kind": "intervals", "slow_reps": 2}]}
    day = engine.adapt(_view(7, "Wed"), _ctx(7, "Wed", run_history=hist, prev_quality_item=prev))
    assert _item(day, "run")["exercise"] == "Intervals: 4 x 800 m"
    assert engine.run_review(prev["run"], {"minutes": 30, "slow_reps": 2})["stopped_early"] is True


def test_a_long_gap_recommends_a_rollback():
    day = engine.adapt(_view(5, "Mon"), _ctx(5, "Mon", last_session=(_date(5, "Mon") - timedelta(days=15)).isoformat()))
    roll = next(a for a in day["adjustments"] if a["kind"] == "rollback")
    assert roll["action"] == "rollback" and roll["weeks"] == 1
    assert not [a for a in engine.adapt(_view(5, "Mon"), _ctx(5, "Mon", last_session=_date(4, "Sat").isoformat()))["adjustments"]
                if a["kind"] == "rollback"]


# ---- review ----------------------------------------------------------------------------------------------------------
def test_progress_marks_every_point_and_never_projects():
    sets = [{"key": "deadlift", "date": "2026-11-23", "weight": 225, "reps": 5, "rir": 3}]
    tests = [{"event": "hrp", "value": 40, "date": "2026-11-25"}]
    weeks = [{"week": 1, "run_min": 90, "lift_sets": 40, "days": [{"date": "2026-11-23", "optional": False}, {"date": "2026-11-24", "optional": False}]}]
    p = engine.progress(sets, [{"date": "2026-11-25", "minutes": 25, "rpe": 4, "kind": "easy", "distance_mi": 2.5}], tests, weeks, [], [],
                        START, date(2026, 11, 26))
    assert p["lifts"][0]["points"] == [{"date": "2026-11-23", "value": 285.0, "source": "logged"}]
    assert p["tests"]["hrp"][0]["source"] == "test" and p["tests"]["run"] == []
    assert p["easy_pace"][0]["value"] == 600 and p["weeks"][0]["run_min_done"] == 25 and p["compliance"] == 0.5


def test_next_block_needs_all_five_results_and_picks_the_weakest():
    assert engine.next_block({"mdl": 330, "hrp": 50}) is None
    text = engine.next_block({"mdl": 330, "hrp": 50, "sdc": 100, "plk": 200, "run": 1100})
    assert text.startswith("Running emphasis")
