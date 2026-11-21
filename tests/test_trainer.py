"""The trainer he talks to (tools/trainer + core/coachintent): goal, plan, a guided workout, set logs, progress, why.
No model, no speaker, no network; the clock is pinned to a Monday."""
from datetime import datetime, timedelta

import pytest

from core import coachintent
from tools import fitness, registry, trainer

NOW = datetime(2026, 11, 2, 18, 0)        # a Monday evening


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    box = {"now": NOW, "rests": []}
    monkeypatch.setattr(trainer, "clock", lambda: box["now"])
    monkeypatch.setattr(trainer, "REST", lambda seconds, line, uid: box["rests"].append((seconds, line)))
    return box


def _plan(**more):
    return trainer.set_goal("build muscle", 4, 60, "gym", 24, **more)


# ---- routing ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, name, args", [
    ("start my workout", "start_workout", {}),
    ("Jarvis, let's work out", "start_workout", {}),
    ("What's my workout for today?", "today_workout", {}),
    ("what am I training today", "today_workout", {}),
    ("my goal is to build muscle", "set_training_goal", {"goal": "muscle"}),
    ("set my fitness goal to lose fat, 4 days a week", "set_training_goal", {"goal": "fat_loss", "days_per_week": 4}),
    ("train me for the army fitness test", "set_training_goal", {"goal": "aft"}),
    ("build me a training plan", "build_training_plan", {}),
    ("make me a workout plan", "build_training_plan", {}),
    ("change my plan to 3 days a week", "build_training_plan", {"days_per_week": 3}),
    ("I can only train 3 days a week now", "build_training_plan", {"days_per_week": 3}),
    ("log a set of bench, 8 at 135", "log_set", {"exercise": "bench", "weight_lbs": 135.0, "reps": 8}),
    ("I benched 185 for 5", "log_set", {"exercise": "Bench press", "weight_lbs": 185.0, "reps": 5}),
    ("how am I progressing", "fitness_progress", {}),
    ("how is my bench going", "fitness_progress", {}),
    ("why did you cut my sets", "why_training_change", {"what": "sets"}),
    ("why did my bench go down", "why_training_change", {"what": "bench"}),
])
def test_clear_training_requests_are_routed_in_code(said, name, args):
    assert coachintent.call_for(said, None, True) == (name, args)


@pytest.mark.parametrize("said", [
    "play some music", "what's a good workout for today?", "make a plan for the trip", "create a new project plan",
    "how is my Spanish going", "why did you change the volume", "I want to lose weight", "continue my session", "lift",
    "set a timer for 8 minutes", "is the gym open today", "I did 8 loads of laundry", "my goal is to finish the report",
    "what's the plan for tonight", "remind me to work out at 5", "log my workout from this morning", "start the game",
])
def test_ordinary_talk_is_not_a_training_request(said):
    assert coachintent.call_for(said, None, True) is None


def test_a_bare_progress_question_is_his_only_when_he_trains():
    assert coachintent.call_for("how am I progressing", None, False) is None


@pytest.mark.parametrize("said, args", [
    ("8 at 135", {"reps": 8, "weight_lbs": 135.0}),
    ("135 for 8", {"reps": 8, "weight_lbs": 135.0}),
    ("135 x 8", {"reps": 8, "weight_lbs": 135.0}),
    ("got 8 at 135, two in reserve", {"reps": 8, "weight_lbs": 135.0, "rir": 2}),
    ("eight reps", {"reps": 8}),
    ("failed at 6", {"reps": 6, "rir": 0, "failed": True}),
])
def test_set_reports_are_taken_during_a_workout_even_after_other_talk(said, args):
    assert coachintent.call_for(said, {"prompted": False}) == ("workout_set", args)


@pytest.mark.parametrize("said, call", [
    ("done", ("workout_set", {})), ("done, that was easy", ("workout_set", {"rir": 4, "feel": "easy"})),
    ("8", ("workout_set", {"reps": 8})), ("that was hard", ("workout_set", {"rir": 0, "feel": "hard"})),
    ("next", ("workout_skip", {})), ("repeat that", ("workout_status", {})),
])
def test_short_words_count_only_as_the_reply_to_a_workout_line(said, call):
    assert coachintent.call_for(said, {"prompted": True}) == call
    assert coachintent.call_for(said, {"prompted": False}) is None


@pytest.mark.parametrize("said", [
    "what time is it", "play the next song", "turn the volume to 8", "set a timer for 8 minutes", "call mom at 8",
    "volume 30", "what's the weather", "pause the music", "that was a great movie", "I'm hungry", "skip this song",
    "next song", "remind me at 8 to stretch", "how do you say water in Spanish", "open Spotify",
])
def test_a_workout_never_makes_jarvis_deaf(said):
    assert coachintent.call_for(said, {"prompted": True}) is None, said


def test_session_talk():
    s = {"prompted": False}
    assert coachintent.call_for("swap this exercise for something else", s) == ("swap_exercise", {})
    assert coachintent.call_for("the bench is taken", s) == ("swap_exercise", {})
    assert coachintent.call_for("all the squat racks are in use", s) == ("swap_exercise", {})
    assert coachintent.call_for("swap it for dips", s) == ("swap_exercise", {"to": "dips"})
    assert coachintent.call_for("I can't do dips", s) == ("swap_exercise", {"exercise": "dips"})
    assert coachintent.call_for("I can't do this right now, call mom", s) is None
    assert coachintent.call_for("skip this one", s) == ("workout_skip", {})
    assert coachintent.call_for("what's the next exercise", s) == ("workout_status", {})
    assert coachintent.call_for("how many sets are left", s) == ("workout_status", {})
    assert coachintent.call_for("end my workout", s) == ("end_workout", {})
    assert coachintent.call_for("I'm done training", s) == ("end_workout", {})


@pytest.mark.parametrize("said, call", [
    ("swap this for something else", ("swap_exercise", {})), ("i can't do this", ("swap_exercise", {})),
    ("change this", ("swap_exercise", {})), ("what's next", ("workout_status", {})), ("how much weight", ("workout_status", {})),
    ("that's it for today", ("end_workout", {})), ("I'm done for today", ("end_workout", {})),
    ("the session is done", ("end_workout", {})),
])
def test_loose_session_talk_counts_only_as_the_reply_to_a_workout_line(said, call):
    assert coachintent.call_for(said, {"prompted": True}) == call
    assert coachintent.call_for(said, {"prompted": False}) is None, said


@pytest.mark.parametrize("said", [
    "the printer is busy", "the line is busy", "the coffee machine is broken", "the bar is full", "all the good seats are taken",
    "my session is over in ten minutes", "change the song", "what's next on my calendar",
])
def test_other_things_being_busy_or_over_never_touch_the_workout(said):
    assert coachintent.call_for(said, {"prompted": True}) is None, said
    assert coachintent.call_for(said, {"prompted": False}) is None, said


@pytest.mark.parametrize("said", [
    "make me a plan to lose weight at my job", "build me a plan to get stronger at chess", "make a study plan for muscle anatomy",
    "give me a plan to save money for a gym", "update the plan for the trip, 3 days a week", "my goal is to get fit for the wedding",
    "my goal is to cut costs", "change my plan for the trip to 3 days a week", "set my goal to a bigger house",
    "write me a meal plan to lose weight", "I'm training to be a stronger chess player",
])
def test_a_plan_or_goal_about_something_else_is_not_a_training_request(said):
    assert coachintent.call_for(said, None, True) is None, said
    assert coachintent.call_for(said, {"prompted": True}) is None, said


@pytest.mark.parametrize("said, name, args", [
    ("build me a plan to get stronger", "build_training_plan", {"goal": "strength"}),
    ("build me a training plan to lose weight, 4 days a week", "build_training_plan", {"goal": "fat_loss", "days_per_week": 4}),
    ("make me a plan for the gym, 3 days a week", "build_training_plan", {"days_per_week": 3, "equipment": "the gym"}),
    ("my goal is to get stronger", "set_training_goal", {"goal": "strength"}),
    ("my goal is fat loss, four days a week", "set_training_goal", {"goal": "fat_loss", "days_per_week": 4}),
    ("I'm training for the army fitness test", "set_training_goal", {"goal": "aft"}),
])
def test_plain_goal_and_plan_requests_still_work(said, name, args):
    assert coachintent.call_for(said, None, True) == (name, args)


# ---- goal and plan ---------------------------------------------------------------------------------------------------
def test_goal_builds_and_explains_a_plan_and_keeps_the_old_one():
    assert "What are you training for" in trainer.build_plan()
    out = _plan()
    assert out.startswith("Goal set: build muscle. Your plan: 8 weeks for build muscle. 4 days a week as upper and lower")
    assert "Today: upper body a. Say 'start my workout'" in out
    data = fitness._data()
    assert data["profile"]["goal"] == "muscle" and data["plan"]["generated"] and len(data["plan"]["weeks"]) == 8
    assert "Week 1" in fitness.today_workout() and "Bench press: 4x8-12" in fitness.today_workout()   # the old tool reads the new plan
    again = trainer.set_goal("muscle")
    assert "Your current plan stays as it is" in again
    changed = trainer.build_plan(days_per_week=3)
    assert "3 days a week as full body three times a week" in changed
    data = fitness._data()
    assert len(data["plan_history"]) == 1 and data["plan_history"][0]["days_per_week"] == 4
    assert registry.run("set_training_goal", {"goal": "juggling"})[0].startswith("I didn't catch the goal")


def test_a_plan_the_model_wrote_earlier_still_loads_and_is_not_replaced_by_a_goal():
    fitness.set_training_plan("AFT 500", "2026-11-02", [{"week": 1, "focus": "Base", "days": [
        {"day": "Mon", "title": "Strength", "items": [{"exercise": "Bench press", "sets": "3", "reps": "8-12", "target": "RIR 2"},
                                                      {"exercise": "run intervals", "sets": "6", "reps": "400m", "target": "1:40"}]}]}])
    assert "Your current plan stays as it is" in trainer.set_goal("strength")
    assert fitness._data()["plan"]["name"] == "AFT 500"
    out = trainer.start()
    assert out.startswith("Strength: 2 exercises") and "First, Bench press: 3 sets of 8 to 12." in out


def test_older_fitness_file_without_sections_still_loads():
    from core import storage
    storage.save("fitness.json", {"profile": {"age": "24"}, "plan": {}})
    assert fitness.log_workout("Run", [{"exercise": "run"}]).startswith("Logged 'Run'")
    assert fitness.app_data()["coach"]["today"]["status"] == "none"


# ---- the guided workout ----------------------------------------------------------------------------------------------
def test_guided_workout_from_start_to_summary(pinned):
    assert "no training plan yet" in trainer.start()
    _plan()
    assert not trainer.running()
    out = trainer.start()
    assert out.startswith("Upper body A: 6 exercises, about ") and "First, Bench press: 4 sets of 8 to 12." in out
    assert "No bench press logged yet" in out and trainer.running()
    assert trainer.report(8).startswith("What weight was that?")
    with trainer.by_voice():
        out = trainer.report(8, 135)
    assert out == "Logged 8 at 135, set 1 of 4. Rest 2 minutes."
    assert pinned["rests"] == [(120, "Rest is over. Set 2 of 4: bench press.")]
    assert trainer.report() == "Logged 8 at 135, set 2 of 4. Rest 2 minutes."       # "done": same as the set before
    assert pinned["rests"] == [(120, "Rest is over. Set 2 of 4: bench press.")]     # the page runs its own timer
    assert "Take 5 pounds off for the next set." in trainer.report(6, 135, failed=True)
    out = trainer.report(8, 130)
    assert out.startswith("Logged 8 at 130, set 4 of 4. That's bench press done. Next, Bent-over row: 3 sets of 8 to 12.")
    assert "Set 1 of 3. Bent-over row" in trainer.status() and "left today" in trainer.status()
    assert trainer.skip().startswith("Skipped bent-over row. Next, Overhead press")
    out = trainer.end()
    assert out.startswith("Workout done: 4 sets across 1 exercise, 4,010 pounds moved.")
    assert "Not finished: overhead press" in out and "Next session: lower body a on Tuesday." in out
    assert not trainer.running()
    w = fitness._data()["workouts"]
    assert len(w) == 1 and w[0]["coach"] and w[0]["items"] == [{"exercise": "Bench press", "sets": 4, "reps": 8, "weight_lbs": 135.0}]
    from tools import life
    assert life.achievements(fitness._data(), NOW.date())["streak"] >= 1 or life.achievements(fitness._data(), NOW.date())["total"] == 1


def test_next_session_uses_what_was_logged_and_says_why(pinned):
    _plan()
    trainer.start()
    for _ in range(4):
        trainer.report(12, 135)
    trainer.end()
    pinned["now"] = NOW + timedelta(days=4)
    trainer.log_set_text("curl", 30, 12)                     # he trained on Friday too: no week-long gap
    pinned["now"] = NOW + timedelta(days=7)
    out = trainer.start()
    assert "First, Bench press: 4 sets of 8 to 12 at 140 pounds. Last time: 135 x 12, 12, 12, 12." in out
    assert "you hit 12 on every set at 135, so the load goes up to 140" in out
    assert "the load goes up to 140" in trainer.why("bench")
    assert "Changed: Last time you hit 12 on every set at 135" in fitness.today_workout()
    assert trainer.report(feel="easy").startswith("Logged 12 at 140, set 1 of 4.")


def test_poor_sleep_trims_today_and_he_can_ask_why(pinned):
    _plan()
    fitness.log_checkin(5, 2, 2, date=NOW.date().isoformat())
    out = trainer.start()
    assert "You slept 5 hours and energy is 2 out of 5, so I took one set off each exercise and dropped" in out
    assert "First, Bench press: 3 sets of 8 to 12." in out
    assert trainer.why("sets").startswith("You slept 5 hours and energy is 2 out of 5")
    page = trainer.page_data()
    assert page["today"]["adjustments"][0]["kind"] == "readiness" and page["today"]["items"][0]["planned_sets"] == "4"
    assert page["changes"][0]["kind"] == "readiness"


def test_swap_keeps_the_prescription_and_is_explained(pinned):
    _plan()
    trainer.start()
    out = trainer.swap()
    assert out.startswith("Swapped bench press for incline dumbbell press. Incline dumbbell press trains the same movement")
    assert "Incline dumbbell press: 4 sets of 8 to 12." in out
    assert trainer.report(10, 50).startswith("Logged 10 at 50, set 1 of 4.")
    assert trainer.view()["items"][0]["key"] == "incline_press" and len(trainer.view()["items"][0]["done"]) == 1
    assert "Chest dip" in trainer.swap("dips", "rows") and "not in today's session" in trainer.swap("", "deadlift")
    assert "trains the same movement" in trainer.why()


def test_a_workout_left_alone_closes_itself_and_survives_a_restart(pinned):
    _plan()
    trainer.start()
    trainer.report(8, 135)
    assert trainer.running() and trainer.view()["session"]["current"] == 0      # rebuilt from the event log every time
    pinned["now"] = NOW + timedelta(hours=5)
    assert not trainer.running()
    assert "No workout is running" in trainer.report(8, 135)


def test_logging_a_set_outside_a_workout_and_progress(pinned):
    assert trainer.log_set_text("bench", 185, 5) == "Logged bench press, 5 at 185. That's 1 set of it today. Estimated max about 216 pounds."
    assert "I need the reps" in trainer.log_set_text("bench", 185, 0)
    pinned["now"] = NOW + timedelta(days=3)
    registry.run("log_set", {"exercise": "bench press", "weight_lbs": 195, "reps": 5})
    out = fitness.fitness_progress()
    assert "Bench press: estimated max about 228 pounds, up 11.7 since you started logging it." in out
    assert "Hard sets this week: chest 2" in out
    p = trainer.page_data()["progress"]
    assert len(p["lifts"][0]["points"]) == 2 and p["volume"]["chest"] == 2 and p["range"] == [6, 12]
    assert "I have not changed anything" in trainer.why()
    assert "New best on bench press: an estimated max of about 228 pounds, up from 216." in trainer.end()


def test_events_are_per_machine_and_a_broken_file_is_skipped(pinned):
    from core import storage
    trainer.log_set_text("bench", 185, 5)
    folder = storage.user_dir() / "fitness"
    assert [p.name for p in folder.glob("events-*.json")] == [f"events-{trainer._machine()}.json"]
    (folder / "events-other.json").write_text('[{"type": "set", "t": "2026-11-01T10:00:00", "date": "2026-11-01", "key": "row", '
                                              '"exercise": "Bent-over row", "weight": 95, "reps": 10}]', encoding="utf-8")
    (folder / "events-bad.json").write_text("{not json", encoding="utf-8")
    assert {e.get("key") for e in trainer.events()} == {"row", "bench_press"}


def test_spoken_form_of_screen_text():
    assert trainer.speakable("Week 1 (Base): Strength\n- deadlift: 5x3 80% 3RM\n- Bench press: 4x8-12 RIR 2") == \
        "Week 1 (Base): Strength. deadlift: 5 sets of 3 80% 3RM. Bench press: 4 sets of 8 to 12 RIR 2."


# ---- the Brain -------------------------------------------------------------------------------------------------------
@pytest.fixture
def brain():
    from brain import llm

    b = llm.Brain.__new__(llm.Brain)
    b.uid, b.messages = "owner", []
    return b


def test_the_brain_runs_a_workout_in_code_and_keeps_listening_to_everything_else(brain, pinned):
    said = []
    assert brain._trainer_request("what time is it", said.append) is False and not said
    assert brain._trainer_request("[Sent from their phone] my goal is to build muscle, 4 days a week", said.append) is True
    assert said[0].startswith("Goal set: build muscle.")
    assert brain._trainer_request("done", said.append) is False                       # no workout: "done" is not his
    assert brain._trainer_request("start my workout", said.append) is True and trainer.running()
    assert brain._trainer_request("8 at 135", said.append) is True
    assert said[-1] == "Rest 2 minutes." and pinned["rests"][-1][0] == 120            # spoken rest timer for voice
    for line in ("what time is it", "play some music", "turn the volume to 8", "tell me a joke"):
        assert brain._trainer_request(line, said.append) is False, line
    assert trainer.running() and len(trainer.view()["items"][0]["done"]) == 1         # the workout stayed open
    assert brain._trainer_request("done", said.append) is False                       # after other talk "done" is not the workout's
    assert brain._trainer_request("what's next", said.append) is False                # after other talk that is his calendar's
    assert brain._trainer_request("what's the next exercise", said.append) is True
    assert brain._trainer_request("what's next", said.append) is True                 # as the reply to a workout line it is the workout's
    assert brain._trainer_request("done", said.append) is True and len(trainer.view()["items"][0]["done"]) == 2
    assert brain._trainer_request("end my workout", said.append) is True and not trainer.running()
    assert brain.messages[-1]["content"].startswith("Workout done: 2 sets across 1 exercise")


# ---- the phone page --------------------------------------------------------------------------------------------------
def test_trainer_page_is_in_the_design_system_and_cached():
    import re
    from pathlib import Path
    app = Path(__file__).resolve().parent.parent / "app"
    page = (app / "fitness.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/design-system.css">' in page
    assert not re.search(r"#[0-9a-fA-F]{6}\b", re.sub(r'<meta name="theme-color"[^>]*>', "", page).split("<script>")[0]), "colours come from the design tokens"
    assert "ds-empty" in page and "ds-spinner" in page and "ds-skeleton" in page          # empty and loading states
    for wanted in ('data-act="start"', 'data-act="set"', 'data-act="swap"', 'id="rest"', "Why it changed", "Estimated strength",
                   "Hard sets this week", "/api/fitness/coach", "/api/fitness/checkin", "const POSES = {", "Last time"):
        assert wanted in page, wanted
    assert not re.search(r"\b(?:alert|confirm|prompt)\(", page)
    sw = (app / "sw.js").read_text(encoding="utf-8")
    assert '"/fitness.html"' in sw and "jarvis-v34-languages" not in sw


def test_trainer_page_api_runs_a_workout(pinned):
    from fastapi.testclient import TestClient

    from core import appserver
    from tools import voice

    c = TestClient(appserver.AppServer(lambda uid: None).app)
    assert c.post("/api/fitness/coach", json={"action": "start"}).status_code in (401, 403)
    voice.pair_codes["444444"] = "owner"
    h = {"Authorization": "Bearer " + c.post("/api/pair", json={"code": "444444"}).json()["token"]}
    coach = c.get("/api/fitness", headers=h).json()["coach"]
    assert coach["today"]["status"] == "none" and coach["week"] == [] and coach["progress"]["lifts"] == [] and "muscle" in coach["goals"]
    out = c.post("/api/fitness/coach", headers=h, json={"action": "goal", "goal": "muscle", "days_per_week": 4, "equipment": "gym",
                                                         "training_months": 24}).json()
    assert out["ok"] and out["message"].startswith("Goal set: build muscle.")
    today = out["data"]["coach"]["today"]
    assert today["status"] == "train" and today["title"] == "Upper body A" and today["minutes"] > 20 and not today["session"]["open"]
    assert [d["day"] for d in out["data"]["coach"]["week"]] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert set(out["data"]) >= {"profile", "plan", "today", "garmin", "exercises", "workouts", "checkin", "achievements"}   # the old keys stay
    out = c.post("/api/fitness/coach", headers=h, json={"action": "start"}).json()
    assert out["data"]["coach"]["today"]["session"] == {"open": True, "started": "2026-11-02T18:00:00", "ended": False, "current": 0, "extra": []}
    out = c.post("/api/fitness/coach", headers=h, json={"action": "set", "exercise": "bench_press", "weight_lbs": 135, "reps": 9, "rir": 2}).json()
    item = out["data"]["coach"]["today"]["items"][0]
    assert out["message"] == "Logged 9 at 135, set 1 of 4. Rest 2 minutes." and item["done"] == [{"weight": 135.0, "reps": 9, "rir": 2, "failed": False}]
    assert not pinned["rests"]                                                         # the page shows its own rest timer
    assert c.post("/api/fitness/coach", headers=h, json={"action": "set", "reps": "abc"}).json()["message"].startswith("Logged 9 at 135, set 2 of 4")
    out = c.post("/api/fitness/coach", headers=h, json={"action": "end"}).json()
    assert out["message"].startswith("Workout done: 2 sets across 1 exercise") and out["data"]["workouts"][-1]["coach"]
    assert out["data"]["coach"]["progress"]["lifts"][0]["name"] == "Bench press" and out["data"]["coach"]["progress"]["volume"]["chest"] == 2
    assert c.post("/api/fitness/coach", headers=h, json={"action": "dance"}).json()["message"] == "I don't know that action."


# ---- the spoken rest timer ------------------------------------------------------------------------------------------
@pytest.fixture
def real_rest(monkeypatch):
    """The real rest timer on the real timer table, with the screen and the speaker replaced. Nothing here waits."""
    from core import hud, timers
    from tools import system
    said = []
    monkeypatch.setattr(hud, "_send", lambda *a, **k: None)
    monkeypatch.setattr(system, "announce", lambda line, *a, **k: said.append(line))
    monkeypatch.setattr(trainer, "REST", trainer._rest_timer)
    timers.clear()
    yield timers
    timers.clear()


def _labels(timers):
    return sorted(t["label"] for t in timers.active())


def test_the_rest_timer_never_cancels_his_other_timers(pinned, real_rest):
    for label in ("restaurant booking", "dough rest", "forest walk"):
        real_rest.add(9000, label, lambda _name: None)
    _plan()
    with trainer.by_voice():
        trainer.start()
        trainer.report(8, 135)
        assert _labels(real_rest) == ["dough rest", "forest walk", "rest", "restaurant booking"]
        trainer.report(8, 135)                                                         # the new rest replaces the old one only
        assert _labels(real_rest) == ["dough rest", "forest walk", "rest", "restaurant booking"]
        trainer.end()
    assert _labels(real_rest) == ["dough rest", "forest walk", "restaurant booking"]


@pytest.mark.parametrize("then", ["end", "skip", "swap"])
def test_no_rest_is_announced_after_the_workout_ends_or_the_exercise_changes(pinned, real_rest, then):
    _plan()
    with trainer.by_voice():
        trainer.start()
        trainer.report(8, 135)
        assert _labels(real_rest) == ["rest"]
        getattr(trainer, then)()
    assert _labels(real_rest) == []


def test_cancel_by_id_is_exact(real_rest):
    a = real_rest.add(9000, "tea", lambda _name: None)
    real_rest.add(9000, a["id"] + " cake", lambda _name: None)                        # a label that contains the other timer's id
    assert real_rest.cancel_id(a["id"]) is True
    assert _labels(real_rest) == [a["id"] + " cake"]
    assert real_rest.cancel_id(a["id"]) is False and real_rest.cancel_id("") is False


# ---- the restored Trainer sections (warm-up, cool-down, Garmin charts, all weeks) ------------------------------------
def test_page_has_the_restored_sections_in_design_tokens_only():
    import re
    from pathlib import Path
    page = (Path(__file__).resolve().parent.parent / "app" / "fitness.html").read_text(encoding="utf-8")
    for wanted in ("function warmupCard", "function cooldownCard", "Mobility flow", "tap a move for how-to", "function howTo", "function garminCards",
                   "Run pace", '"Sleep"', '"HRV"', "No Garmin data yet", "function lineChart", "function allWeeksCard", "The whole plan"):
        assert wanted in page, wanted
    style = page.split("<style>")[1].split("</style>")[0]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", style), "colours come from the design tokens"
    chart_js = page.split("function lineChart")[1].split("function allWeeksCard")[0]
    assert not re.search(r"#[0-9a-fA-F]{6}\b|stroke=\"#|fill=\"#", chart_js), "chart colours come from CSS tokens"
    assert 'data-tab="progress"' in page and "garminCards()" in page.split("function renderProgress")[1].split("function renderMore")[0]
    assert "allWeeksCard()" in page.split("function renderWeek")[1].split("function renderProgress")[0]
    assert "jarvis-v36-trainer-garmin" not in (Path(__file__).resolve().parent.parent / "app" / "sw.js").read_text(encoding="utf-8")


def test_page_api_carries_warmup_cooldown_garmin_and_the_whole_plan(pinned):
    from tools import fitness
    _plan()
    data = fitness.app_data()
    today = data["coach"]["today"]
    assert today["status"] == "train" and today["warmup"] and today["cooldown"]
    assert all(ph["phase"] and ph["items"] and all(i["exercise"] and i["dose"] for i in ph["items"]) for ph in today["warmup"])
    assert all(d["exercise"] and d["dose"] for d in today["cooldown"])
    assert len(data["plan"]["weeks"]) > 1 and "garmin" in data and "checkins" in data and data["exercises"]
    rest_day = fitness._session_parts({"items": []})
    assert rest_day["warmup"] == [] and rest_day["cooldown"]                       # a rest day gets the mobility flow


# ---- the glutes goal on the Trainer (another person in the house, a total beginner) ------------------------------------
def test_glute_goal_builds_her_plan_from_the_page_and_shows_in_the_goal_list():
    said = trainer.page_action({"action": "goal", "goal": "glutes", "days_per_week": 3, "session_minutes": 45,
                                "equipment": "home dumbbells bench bands", "training_months": 0})
    assert "glutes" in said.lower() and "3 glute sessions a week" in said
    page = trainer.page_data()
    assert page["goals"]["glutes"] and page["goal"]["key"] == "glutes" and page["goal"]["level"] == "novice"
    assert page["today"]["status"] == "train" and page["today"]["title"].startswith("Glutes A")
    assert any("doctor" in n for n in page["plan"]["notes"])
    assert trainer.start().lower().count("glute") or trainer.status()


def test_a_glute_goal_said_out_loud_is_routed():
    call = coachintent.call_for("my goal is a bigger butt, three days a week", None, True)
    assert call == ("set_training_goal", {"goal": "glutes", "days_per_week": 3})


def test_glute_days_get_a_beginner_warm_up_and_no_upper_body_nag(pinned):
    from tools import fitness_library as lib
    trainer.page_action({"action": "goal", "goal": "glutes", "days_per_week": 4, "session_minutes": 45, "equipment": "bodyweight only",
                         "training_months": 0})
    page = trainer.page_data()
    assert page["today"]["kind"] == "glute"
    text = str(page["today"]["warmup"])
    assert "85%" not in text and "bar," not in text and "no weight" in text
    assert lib.session_kind({"title": "Easy walk and core", "items": []}) == "walk"
    assert lib.session_kind({"title": "Lower body A", "items": [{"exercise": "Goblet squat"}]}) == "lower"       # unchanged
    pinned["now"] = NOW + timedelta(days=4)                    # Friday: the weekly volume line
    said = trainer.progress_text()
    assert "chest" not in said and "shoulders" not in said
