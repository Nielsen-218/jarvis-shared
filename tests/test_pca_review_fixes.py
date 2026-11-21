"""Project Captain America: regressions for the defects an independent review confirmed (2026-11-20). Sandboxed storage
(tests/conftest.py), a pinned clock; no model, no network."""
import json
from datetime import date, datetime, timedelta

import pytest

from core import coach_engine as coach
from core import pca_engine as engine
from core import storage
from tools import fitness, pca, trainer

MON = datetime(2026, 11, 23, 7, 0)
ANSWERS = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
           "equipment": "trap_bar,barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
           "runs_per_week": 1, "miles_per_week": 4, "weeks_since_last_run": 2, "weeks_since_last_heavy_lift": 1, "pain_level": 0,
           "medical_restriction": "no", "sickle_cell": "no", "sleep_hours": 7, "unit_pt_days": 3, "unit_pt_kind": "mixed"}


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    box = {"now": MON}
    monkeypatch.setattr(trainer, "clock", lambda: box["now"])
    monkeypatch.setattr(trainer, "REST", lambda seconds, line, uid: None)
    return box


def at(box, week, weekday):
    box["now"] = MON + timedelta(days=7 * (week - 1) + weekday)
    return box["now"].date()


def _onboard():
    for qid, value in ANSWERS.items():
        assert pca.answer(qid, value).startswith("Saved"), qid


def _started():
    _onboard()
    assert "starts today" in pca.begin()


def _item(key):
    return next(i for i in trainer.view()["items"] if i["key"] == key)


def _adjust(kind):
    return [a for a in trainer.view()["adjustments"] if a["kind"] == kind]


# 1 -------------------------------------------------------------------------------------------------------------------
def test_sets_from_before_the_program_never_drive_a_load():
    data = fitness._data()
    data["workouts"] = [{"date": "2026-11-16", "title": "Old", "items": [{"exercise": "Deadlift", "sets": 4, "reps": 5, "weight_lbs": 405}]}]
    fitness._save(data)
    trainer.record({"type": "set", "key": "back_squat", "exercise": "Back squat", "weight": 315.0, "reps": 6, "rir": 3, "failed": False,
                    "date": "2026-11-20"})
    _started()
    for key in ("deadlift", "back_squat"):
        it = _item(key)
        assert it["load"] is None and it["mode"] == "start", (key, it.get("note"))
    assert len(fitness._data()["workouts"]) == 1                                  # old data stays stored, untouched


# 2 -------------------------------------------------------------------------------------------------------------------
def test_a_failed_set_wins_over_a_changed_rep_target():
    item = {"key": "deadlift", "exercise": "Trap-bar deadlift", "kind": "lift", "lo": 3, "hi": 3, "rir": 2, "gate": "rir"}
    last = [{"key": "deadlift", "date": "2026-11-23", "weight": 300, "reps": r, "rir": 0, "failed": f, "target_lo": 5, "target_hi": 5}
            for r, f in ((5, False), (5, False), (3, True))]
    t = coach.next_target(item, [last])
    assert (t["load"], t["mode"]) == (270, "reset")


# 3 -------------------------------------------------------------------------------------------------------------------
def test_pain_in_an_area_i_cannot_map_says_what_really_happened():
    _started()
    before = [i["key"] for i in trainer.view()["items"]]
    pca.report_pain_text("stomach", 8)
    day = trainer.view()
    assert [i["key"] for i in day["items"]] == before
    reason = next(a["reason"] for a in day["adjustments"] if a["kind"] == "pain")
    assert "removed nothing" in reason and "medic" in reason and "comes out" not in reason


# 4 -------------------------------------------------------------------------------------------------------------------
def test_a_corrupt_events_file_is_never_reported_as_saved():
    _started()
    path = storage.path(f"fitness/events-{trainer._machine()}.json")
    path.write_text("{not json", encoding="utf-8")
    for body in ({"action": "pain", "area": "knee", "level": 3}, {"action": "test", "event": "hrp", "value": "40"},
                 {"action": "run", "minutes": 20, "rpe": 4}, {"action": "external", "kind": "ruck", "minutes": 60, "rpe": 5, "miles": 4},
                 {"action": "session_rpe", "rpe": 6}, {"action": "override"}):
        assert pca.page_action(body).startswith("I could not save that"), body
    assert path.read_text(encoding="utf-8") == "{not json"


# 5 -------------------------------------------------------------------------------------------------------------------
BAD = [float("nan"), float("inf"), float("-inf"), "nan", "inf", "Infinity", "-Infinity", "NaN"]
FIELDS = {"test": ("value",), "pain": ("level",), "run": ("minutes", "rpe", "distance_mi", "slow_reps", "completed_reps"),
          "external": ("minutes", "rpe", "miles"), "session_rpe": ("rpe", "minutes"), "rollback": ("weeks",),
          "set": ("reps", "weight_lbs", "rir"), "music_next": ("bpm",)}
BASE = {"test": {"event": "plk", "value": "1:50"}, "pain": {"area": "knee", "level": 2}, "run": {"minutes": 20, "rpe": 4},
        "external": {"kind": "ruck", "minutes": 60, "rpe": 5, "miles": 4}, "session_rpe": {"rpe": 6, "minutes": 50},
        "rollback": {"weeks": 1}, "set": {"reps": 5, "weight_lbs": 135}, "music_next": {"phase": "hard"}}


def test_non_finite_numbers_anywhere_get_a_sentence_never_an_exception():
    _started()
    pca.page_action({"action": "start"})
    for action, fields in FIELDS.items():
        for field in fields:
            for bad in BAD:
                out = pca.page_action({"action": action, **BASE[action], field: bad})
                assert isinstance(out, str) and out, (action, field, bad)
                assert "nan" not in out.lower().split() and "inf" not in out.lower().split(), (action, field, bad, out)
    for event in engine.EVENTS:
        for bad in BAD:
            assert isinstance(pca.page_action({"action": "test", "event": event, "value": bad}), str)


# 6 -------------------------------------------------------------------------------------------------------------------
def test_begin_refuses_while_the_program_runs_and_keeps_the_old_plan_in_history():
    trainer.set_goal("build muscle", 4, 60, "gym", 24)
    _started()
    for _ in range(4):
        out = pca.begin()
        assert "already running" in out and "week 1" in out, out
    assert any(p.get("goal") == "muscle" for p in fitness._data()["plan_history"])


def test_begin_refuses_while_a_workout_is_open():
    trainer.set_goal("build muscle", 4, 60, "gym", 24)
    trainer.start()
    _onboard()
    assert "workout" in pca.begin().lower() and fitness._data()["plan"]["goal"] == "muscle"


# 7 -------------------------------------------------------------------------------------------------------------------
def test_pain_level_zero_clears_the_flag():
    _started()
    pca.report_pain_text("left knee", 5)
    out = pca.report_pain_text("left knee", 0)
    assert out.startswith("Cleared") and pca._open_pain(trainer.events()) == []


# 8 -------------------------------------------------------------------------------------------------------------------
def test_accepting_a_rollback_makes_the_suggestion_go_away(pinned):
    _started()
    pca.log_run_text(20, 4)
    at(pinned, 4, 0)                                         # 21 days without a session
    assert _adjust("rollback")
    out = pca.rollback(1)
    assert "week 3" in out, out
    assert not _adjust("rollback")


def test_rollback_refuses_zero_weeks_and_names_a_restart_date(pinned):
    _started()
    assert pca.page_action({"action": "rollback", "weeks": 0}).startswith("Roll back 1 to 4")
    at(pinned, 1, 3)
    out = pca.rollback(1)
    assert "restarts on 2026-11-30" in out and "week 1" not in out, out


def test_rollback_is_refused_and_hidden_once_the_program_has_ended(pinned):
    _started()
    at(pinned, 14, 0)
    assert trainer.view()["status"] == "ended" and not _adjust("rollback")
    assert "ended" in pca.rollback(1)


def test_no_rollback_suggestion_before_the_start(pinned):
    _onboard()
    at(pinned, 1, 3)
    pca.begin()
    at(pinned, 1, 5)
    assert trainer.view()["status"] == "before" and not _adjust("rollback")
    assert "not started" in pca.rollback(1)


# 9 -------------------------------------------------------------------------------------------------------------------
def test_a_test_from_before_the_start_is_refused(pinned):
    _onboard()
    at(pinned, 1, 3)
    pca.begin()                                              # starts Monday 2026-11-30
    out = pca.log_test("hrp", "40")
    assert "only tests run inside the program count" in out.lower()
    at(pinned, 2, 1)
    assert "only tests run inside the program count" in pca.log_test("hrp", "40", "2026-11-28").lower()
    assert pca.log_test("hrp", "40").startswith("Recorded")


# 10 ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["1:-5", "1:78", "1:60", "-1:30", "1:5.5.5"])
def test_seconds_must_be_0_to_59(said):
    value, error = engine.parse_value("plk", said)
    assert value is None and error


# 11 ------------------------------------------------------------------------------------------------------------------
def test_override_keeps_the_rollback_suggestion_and_does_not_count_it(pinned):
    _started()
    pca.log_run_text(20, 4)
    at(pinned, 4, 0)
    assert not [a for a in trainer.view()["adjustments"] if a["kind"] == "rollback" and a["overridable"]]
    pca.override_text()
    assert _adjust("rollback")                                # still recommended after an override
    assert _adjust("rollback")[0]["overridable"] is False      # so override_text never counts it as a change it undid
