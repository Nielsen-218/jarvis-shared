"""Project Captain America by voice (core/coachintent + tools/pca + tools/trainer): his words routed in code, the spoken
briefing, holds, runs and tests with real numbers from a built plan, the one question asked once, the music hooks, and
silence for sessions started from the page. Sandboxed storage (tests/conftest.py), a pinned clock, timers replaced by
recorders; no model, no speaker, no network."""
from datetime import datetime, timedelta

import pytest

from core import coachintent
from tools import fitness, pca, trainer

MON = datetime(2026, 11, 23, 7, 0)         # a Monday morning: the program starts this week
ON = {"on": True, "current": None, "adjusted": False, "pain": []}

ANSWERS = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
           "equipment": "trap_bar,barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
           "runs_per_week": 1, "miles_per_week": 4, "weeks_since_last_run": 2, "weeks_since_last_heavy_lift": 1, "pain_level": 0,
           "medical_restriction": "no", "sickle_cell": "no", "sleep_hours": 7, "unit_pt_days": 3, "unit_pt_kind": "mixed"}


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    box = {"now": MON, "rests": [], "cues": [], "opened": []}
    monkeypatch.setattr(trainer, "clock", lambda: box["now"])
    monkeypatch.setattr(trainer, "REST", lambda seconds, line, uid: box["rests"].append((seconds, line)))
    monkeypatch.setattr(trainer, "SAY", lambda seconds, line, uid: box["cues"].append((seconds, line)))
    monkeypatch.setattr(pca, "_open_link", lambda url: box["opened"].append(url))
    monkeypatch.setattr(pca, "_ASK", {})
    monkeypatch.setattr(pca, "_audio_last", {})
    return box


def at(box, week, weekday, hour=7):
    box["now"] = MON + timedelta(days=7 * (week - 1) + weekday, hours=hour - 7)
    return box["now"].date()


def _begin():
    for qid, value in ANSWERS.items():
        assert pca.page_action({"action": "answer", "id": qid, "value": value}).startswith("Saved")
    assert "starts today" in pca.begin()


def _to(key=None, kind=None):
    """Skip through the open session until the current item is the one wanted."""
    for _ in range(12):
        day = trainer.view()
        n = day["session"]["current"]
        it = day["items"][n]
        if (key is None or it["key"] == key) and (kind is None or it.get("kind") == kind):
            return it
        trainer.skip()
    raise AssertionError("item not reached")


def _baselines(box):
    """Week 1 tests and the week-2 two-mile trial (18:00), with runs logged so no gap rule fires."""
    at(box, 1, 2)
    pca.log_test("hrp", "40")
    pca.log_test("plk", "1:50")
    at(box, 2, 0)
    pca.log_run_text(15, 3)
    at(box, 2, 5)
    pca.log_test("run", "18:00")
    for week, weekday in ((3, 0), (3, 2), (3, 5), (4, 0), (4, 2), (4, 5), (5, 0), (5, 1)):
        at(box, week, weekday)
        pca.log_run_text(20, 4)


# ---- routing -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, name, args", [
    ("what's my mission today", "captain_briefing", {}),
    ("Jarvis, brief me", "captain_briefing", {}),
    ("mission briefing", "captain_briefing", {}),
    ("start my mission", "captain_start", {}),
    ("log my run: 28 minutes, 2.6 miles, effort 4", "log_run", {"minutes": 28.0, "distance_mi": 2.6, "rpe": 4}),
    ("I ran two miles in 18:05", "log_run", {"minutes": 18.08, "distance_mi": 2}),
    ("that run was a 5 out of 10", "log_run", {"rpe": 5}),
    ("ran 25 minutes at RPE 4", "log_run", {"minutes": 25.0, "rpe": 4}),
    ("my two-mile was 17:52", "log_run", {"minutes": 17.87, "distance_mi": 2}),           # no 'test' said: an ordinary run
    ("my two-mile test was 17:52", "log_fitness_test", {"event": "run", "value": "17:52"}),
    ("I got 44 push-ups on the test", "log_fitness_test", {"event": "hrp", "value": "44"}),
    ("plank test 2:05", "log_fitness_test", {"event": "plk", "value": "2:05"}),
    ("sprint drag carry test 1:41", "log_fitness_test", {"event": "sdc", "value": "1:41"}),
    ("deadlift test 340 for 3", "log_fitness_test", {"event": "mdl", "value": "340"}),
    ("my left knee hurts, about a 5", "report_pain", {"area": "left knee", "level": 5}),
    ("hamstring feels tight, 3 out of 10", "report_pain", {"area": "hamstring", "level": 3}),
    ("my knee hurts", "report_pain", {"area": "knee"}),
    ("my ankle hurts and I'm limping", "report_pain", {"area": "ankle", "changes_movement": True}),
    ("we had unit PT this morning, a hard 3 mile run", "log_unit_pt", {"kind": "unit_run", "rpe": 7, "miles": 3.0}),
    ("I rucked 6 miles", "log_unit_pt", {"kind": "ruck", "miles": 6.0}),
    ("we did a 6 mile ruck, 95 minutes, an 8 out of 10", "log_unit_pt", {"kind": "ruck", "miles": 6.0, "minutes": 95.0, "rpe": 8}),
    ("unit leg day this morning, brutal", "log_unit_pt", {"kind": "unit_strength", "rpe": 9}),
    ("how am I progressing", "captain_progress", {}),
    ("how is my training going", "captain_progress", {}),
    ("how is my bench going", "fitness_progress", {}),                                    # one lift: the trainer's own answer
    ("why is today easier", "why_training_change", {"what": "today"}),
    ("why is my run shorter", "why_training_change", {"what": "run"}),
])
def test_his_program_words_are_routed_in_code(said, name, args):
    assert coachintent.call_for(said, None, True, ON) == (name, args)


@pytest.mark.parametrize("said", [
    "I ran into Mike today", "the test is tomorrow", "run it again", "I ran the numbers, it took 5 minutes", "I ran late",
    "I have a test at 5", "the test was hard", "my heart hurts", "it hurts", "brief me on the news", "what's the plan for tonight",
    "I did 8 loads of laundry", "we had a field day with the kids", "I rucked 6 miles yesterday", "run the dishwasher",
    "my knee is fine now", "run it as planned", "I feel fine", "give me the full story", "play some music", "go", "rep done",
    "I got 44", "17:52", "5",
])
def test_ordinary_sentences_are_not_taken(said):
    """With the program on but no session, no pain flag, no change today and no question waiting."""
    assert coachintent.call_for(said, None, True, ON) is None


@pytest.mark.parametrize("said", ["my left knee hurts, about a 5", "I ran two miles in 18:05", "brief me", "I rucked 6 miles",
                                  "plank test 2:05", "how am I progressing"])
def test_nothing_is_taken_for_the_program_when_it_is_not_his_plan(said):
    assert coachintent.call_for(said, None, True, {"on": False}) in (None, ("fitness_progress", {}))
    assert coachintent.call_for(said, None, False, None) is None


def test_a_sentence_that_names_the_program_is_routed_even_without_it():
    assert coachintent.call_for("what's my mission today", None, False, None) is None          # "my mission" is his only with the program
    assert coachintent.call_for("how's Captain America going", None, False, None) == ("captain_status", {})
    assert coachintent.call_for("start Captain America", None, False, None) == ("captain_begin", {})
    assert coachintent.call_for("start Captain America", None, True, ON) == ("captain_start", {})   # never rebuilds a running plan


def test_override_and_clear_need_something_to_undo():
    assert coachintent.call_for("run it as planned", None, True, dict(ON, adjusted=True)) == ("captain_override", {})
    assert coachintent.call_for("I feel fine, give me the full session", None, True, dict(ON, adjusted=True)) == ("captain_override", {})
    assert coachintent.call_for("run it again", None, True, dict(ON, adjusted=True)) is None
    assert coachintent.call_for("my knee is fine now", None, True, dict(ON, pain=["left knee"])) == ("clear_pain", {"area": "knee"})
    assert coachintent.call_for("the shoulder pain is gone", None, True, dict(ON, pain=["shoulder"])) == ("clear_pain", {"area": "shoulder"})


def test_inside_a_session_go_rep_and_a_bare_test_result():
    hold = dict(ON, current={"kind": "hold"})
    assert coachintent.call_for("go", {"prompted": True}, True, hold) == ("captain_go", {})
    assert coachintent.call_for("go", {"prompted": False}, True, hold) is None                     # a bare 'go' only as the reply
    assert coachintent.call_for("start the timer", {"prompted": False}, True, hold) == ("captain_go", {})
    assert coachintent.call_for("start the timer", {"prompted": False}, True, dict(ON, current={"kind": "lift"})) is None
    assert coachintent.call_for("66", {"prompted": True}, True, hold) == ("workout_set", {"reps": 66})   # the seconds held
    reps = dict(ON, current={"kind": "cardio", "run": True, "reps": True})
    assert coachintent.call_for("rep done", {"prompted": False}, True, reps) == ("captain_rep", {})
    assert coachintent.call_for("next rep", {"prompted": True}, True, reps) == ("captain_rep", {})
    assert coachintent.call_for("rep done", {"prompted": True}, True, dict(ON, current={"kind": "lift"})) is None
    test = dict(ON, current={"kind": "test", "test": "hrp"})
    assert coachintent.call_for("I got 44", {"prompted": True}, True, test) == ("log_fitness_test", {"event": "hrp", "value": "44"})
    assert coachintent.call_for("plank 1:55", {"prompted": True}, True, test) is None              # not THIS test's result shape
    assert coachintent.call_for("plank test 1:55", {"prompted": True}, True, test) == ("log_fitness_test", {"event": "plk", "value": "1:55"})
    assert coachintent.call_for("8 at 135", {"prompted": True}, True, test) == ("workout_set", {"reps": 8, "weight_lbs": 135.0})
    assert coachintent.call_for("my left knee hurts, 4", {"prompted": True}, True, test) == ("report_pain", {"area": "left knee", "level": 4})


def test_the_old_trainer_routing_is_unchanged_without_a_context():
    assert coachintent.call_for("8 at 135", {"prompted": True}, True) == ("workout_set", {"reps": 8, "weight_lbs": 135.0})
    assert coachintent.call_for("how am I progressing", None, True) == ("fitness_progress", {})
    assert coachintent.call_for("my knee hurts", None, True) is None


# ---- one question, asked once --------------------------------------------------------------------------------------------
def test_a_pain_report_without_a_level_asks_once(pinned):
    _begin()
    with trainer.by_voice():
        out = pca.report_pain("left knee")
    assert out.startswith("How bad is it, 0 to 10") and pca.asking()
    ctx = pca.voice_context()
    assert ctx["ask"]["need"] == "level" and not pca.asking()                           # handed over once
    call = coachintent.call_for("about a 5", None, True, ctx)
    assert call == ("report_pain", {"area": "left knee", "level": 5})
    assert coachintent.call_for("about a 5", None, True, pca.voice_context()) is None    # not asked twice
    page = pca.report_pain("knee")                                                        # from the page: nothing waits
    assert page.startswith("How bad") and not pca.asking()


def test_a_missing_number_for_a_run_or_a_ruck_is_asked_and_filled(pinned):
    _begin()
    with trainer.by_voice():
        assert pca.log_run(rpe=5).startswith("How many minutes")
        assert coachintent.call_for("about 30 minutes", None, True, pca.voice_context()) == ("log_run", {"rpe": 5, "minutes": 30.0})
        assert pca.log_unit_pt("ruck", miles=6).startswith("How many minutes")
        assert coachintent.call_for("an hour and a half, pretty hard", None, True, pca.voice_context()) == \
            ("log_unit_pt", {"kind": "ruck", "miles": 6, "minutes": 90.0, "rpe": 7})
        assert pca.log_unit_pt("ruck", 90, 0, 6).startswith("How hard")
        assert coachintent.call_for("I went to the store", None, True, pca.voice_context()) is None   # anything else drops it
    pinned["now"] += timedelta(minutes=5)
    with trainer.by_voice():
        pca.log_run(minutes=30)
    pinned["now"] += timedelta(minutes=10)
    assert not pca.asking()                                                               # a stale question is never answered


# ---- what he hears -----------------------------------------------------------------------------------------------------
def test_the_briefing_is_short_and_carries_real_numbers(pinned):
    _begin()
    text = pca.captain_briefing()
    assert text.startswith("Today's mission: lower-body strength and power.")
    assert "deadlift, 3 sets of 5, 4 in reserve" in text and "back squat, 3 sets of 6" in text and len(text.split()) <= 34


def test_the_briefing_names_a_change_and_why(pinned):
    _begin()
    _baselines(pinned)
    at(pinned, 5, 2)
    fitness.log_checkin(4.5, 2, 3, date=pinned["now"].date().isoformat())
    text = pca.captain_briefing()
    assert text.startswith("Today's mission: intervals: 6 by 400 metres")
    assert "You slept 4.5 hours and energy is 2 out of 5, so today is easier." in text and len(text.split()) <= 34
    assert "easy run, 22 minutes easy" in text                                           # the converted run, capped at 110%


def test_start_my_mission_briefs_then_starts_the_workout(pinned):
    _begin()
    with trainer.by_voice():
        out = pca.captain_start()
    brief, _, rest = out.partition(" Lower-body strength and power: ")
    assert brief.startswith("Today's mission") and rest and "First, " in rest
    assert trainer.view()["session"]["open"]


def test_a_hold_is_said_with_its_tested_seconds_and_timed_by_voice(pinned):
    _begin()
    at(pinned, 1, 2)
    pca.log_test("plk", "1:50")
    at(pinned, 1, 5)
    with trainer.by_voice():
        trainer.start()
        plank = _to("plank", "hold")
        assert plank["say"].startswith("Plank: ") and "of 66 seconds" in plank["say"] and plank["say"].endswith("Say go when you're set.")
        assert pca.captain_go() == "Go: hold 66 seconds."
        assert pinned["cues"] == [(33, "Halfway."), (56, "Ten seconds."), (66, "Time. Say done, or the seconds you held.")]
        out = trainer.report(66)
    assert out.startswith("Logged 66 seconds")
    assert pinned["rests"][-1][1].startswith("Rest is over. Hold 2 of") and pinned["rests"][-1][1].endswith("Say go when you're set.")


def test_intervals_are_said_once_and_counted_rep_by_rep(pinned):
    _begin()
    _baselines(pinned)
    at(pinned, 5, 2)
    with trainer.by_voice():
        start = trainer.start()
        assert ("Intervals: warm up 10 minutes easy, then six times 400 metres at two-mile pace, 2:13 to 2:18 each, "
                "with a 200 metre walk-jog between.") in start
        assert "Say 'rep done' after each one." in start
        first = pca.captain_rep()
        assert first == "Rep 1 of 6 done. Rest 1 minute 45 seconds, a 200 metre walk-jog. I'll say go."
        assert pinned["cues"][-1] == (105, "Go: rep 2 of 6.")
        for _ in range(4):
            pca.captain_rep()
        last = pca.captain_rep()
    assert last.startswith("Rep 6 of 6 done. That's every rep. Cool down 6 minutes easy")
    assert pca.captain_rep().startswith("That's every rep already.")


def test_a_timed_run_is_called_by_the_clock(pinned):
    _begin()
    for week, weekday in ((1, 2), (2, 0), (2, 2), (3, 0)):
        at(pinned, week, weekday)
        pca.log_run_text(20, 4)
    at(pinned, 3, 2)
    with trainer.by_voice():
        trainer.start()
        run = _to("run")
        assert "two times 8 minutes at tempo effort" in run["say"] and "Say go when you start the first one" in run["say"]
        assert pca.captain_go() == "Go: eight minutes hard."
    assert pinned["cues"][:2] == [(480, "Easy for three minutes."), (660, "Rep 2 of 2: eight minutes hard.")]
    assert pinned["cues"][-1][0] == 1140 and pinned["cues"][-1][1].startswith("Time. That was the last one. Cool down 6 minutes easy")


def test_a_test_is_announced_and_its_result_taken_by_voice(pinned):
    _begin()
    at(pinned, 1, 2)
    with trainer.by_voice():
        start = trainer.start()
        assert "This is a test: hand-release push-up test. As many test-legal reps as you can in 2 minutes" in start
        out = pca.log_fitness_test("hrp", "40")
    assert out.startswith("Recorded your hand-release push-ups: 40 reps") and "new best" not in out
    assert (" Next: Easy run and strides: 20 minutes easy, 3 to 4 out of 10, full sentences, then four times 80 metres at a fast, "
            "relaxed effort, with a walk back between. Say 'rep done' after each one.") in out
    at(pinned, 8, 2)
    assert "That's a new best: up from your 40 reps." in pca.log_test("hrp", "44")
    assert "new best" not in pca.log_test("hrp", "41")


def test_the_end_says_the_cool_down_and_a_first_full_week_once(pinned):
    _begin()
    for weekday in (0, 1, 2, 4):
        at(pinned, 1, weekday)
        pca.log_run_text(20, 4)
    at(pinned, 1, 5)
    with trainer.by_voice():
        trainer.start()
        pca.log_run_text(28, 4)
        out = trainer.end()
    assert "Cool down now: " in out and "That's every session of week 1 done: your first full week of the program." in out
    at(pinned, 2, 0)
    with trainer.by_voice():
        trainer.start()
        trainer.report(3)
        assert "first full week" not in trainer.end()


def test_why_answers_a_program_change(pinned):
    _begin()
    _baselines(pinned)
    at(pinned, 5, 0)
    pca.log_external("unit_run", 30, 8)
    at(pinned, 5, 2)
    assert "unit run on Monday was this week's hard run" in trainer.why("run")
    assert "unit run" in trainer.why("today")


# ---- the page stays silent -----------------------------------------------------------------------------------------------
def test_sessions_from_the_page_say_nothing(pinned):
    _begin()
    _baselines(pinned)
    at(pinned, 5, 2)
    pca.page_action({"action": "start"})
    assert pca.go() == "Go. Say 'rep done' after each one."
    assert pca.rep_done() == "Rep 1 of 6 done. Rest 1 minute 45 seconds, a 200 metre walk-jog. Go when you're ready."
    at(pinned, 5, 5)
    pca.page_action({"action": "start"})
    _to("plank", "hold")
    pca.go()
    trainer.report(77)
    assert pinned["cues"] == [] and pinned["rests"] == [] and pinned["opened"] == []
    assert pca.log_test("plk", "2:00").endswith("Plank holds now use it.")                # no 'Next:' for the page


# ---- Performance Audio -------------------------------------------------------------------------------------------------
def test_audio_phase_follows_the_day(pinned):
    _begin()
    assert pca.audio_phase(trainer.view()) == "warmup"
    pca.page_action({"action": "start"})
    assert pca.audio_phase(trainer.view()) == "lift"
    pca.page_action({"action": "end"})
    assert pca.audio_phase(trainer.view()) == "cooldown"
    assert pca.audio_phase({"status": "rest", "items": []}) == "rest"
    _baselines(pinned)
    at(pinned, 5, 2)
    pca.page_action({"action": "start"})
    assert pca.audio_phase(trainer.view()) == "hard"
    fitness.log_checkin(4.5, 2, 3, date=pinned["now"].date().isoformat())              # a bad night: the hard run becomes easy
    assert pca.audio_phase(trainer.view()) == "easy"


def test_music_is_off_until_he_turns_it_on_and_opens_his_own_link(pinned):
    _begin()
    with trainer.by_voice():
        pca.captain_start()
    assert pinned["opened"] == [] and pca.audio_cue("warmup") == "off"
    assert pca.audio_text({"auto_open": True, "playlist_warmup": "https://open.spotify.com/playlist/abc",
                           "playlist_hard": "http://example.com/x"}) == "Music preferences saved."
    assert pca.audio_cue("hard") == "no link"                                             # https only
    assert pca.audio_cue("lift") == "no link"
    at(pinned, 1, 1)
    with trainer.by_voice():
        pca.captain_start()
    assert pinned["opened"] == ["https://open.spotify.com/playlist/abc"]
    assert pca.audio_cue("warmup") == "same"                                              # never reopened twice in a row
    assert pca.duck_media_for_speech() is False                                            # named no-op: media.py has no music-only volume


# ---- review 2026-11-20: five confirmed defects ---------------------------------------------------------------------------
def _asked(need):
    skill = "log_unit_pt" if need == "kind" else "log_run"
    args = {"miles": 6} if need == "kind" else {"rpe": 5} if need == "minutes" else {"minutes": 30}
    return dict(ON, ask={"skill": skill, "args": args, "need": need})


@pytest.mark.parametrize("need, said", [
    ("minutes", "set a timer for 5 minutes"), ("minutes", "remind me in 10 minutes"), ("minutes", "the pizza needs 20 minutes"),
    ("minutes", "dinner in 30 minutes"), ("minutes", "call me in 5 mins"), ("minutes", "turn the volume to 30"),
    ("rpe", "turn on the light"), ("rpe", "that movie was brutal"), ("rpe", "the test was hard"), ("rpe", "play something hard"),
    ("rpe", "it was a light drizzle"), ("rpe", "set a timer for 5 minutes"),
    ("kind", "go run the dishwasher"), ("kind", "turn off the lights, I'm running late"), ("kind", "the weights are in the garage"),
])
def test_a_pending_question_never_swallows_an_ordinary_sentence(need, said):
    assert coachintent.call_for(said, None, True, _asked(need)) is None


@pytest.mark.parametrize("need, said, got", [
    ("minutes", "30", {"minutes": 30.0}), ("minutes", "about 30", {"minutes": 30.0}), ("minutes", "30 minutes", {"minutes": 30.0}),
    ("minutes", "an hour", {"minutes": 60.0}), ("minutes", "it took 45 minutes", {"minutes": 45.0}), ("minutes", "half an hour", {"minutes": 30.0}),
    ("minutes", "an hour and a half, pretty hard", {"minutes": 90.0, "rpe": 7}),
    ("rpe", "5", {"rpe": 5}), ("rpe", "a 5", {"rpe": 5}), ("rpe", "5 out of 10", {"rpe": 5}), ("rpe", "it was a 6", {"rpe": 6}),
    ("rpe", "easy", {"rpe": 3}), ("rpe", "it was hard", {"rpe": 7}), ("rpe", "pretty hard", {"rpe": 7}),
    ("kind", "a run", {"kind": "unit_run"}), ("kind", "it was a run", {"kind": "unit_run"}), ("kind", "strength", {"kind": "unit_strength"}),
    ("kind", "a ruck", {"kind": "ruck"}), ("kind", "field", {"kind": "field"}), ("kind", "mixed", {"kind": "field"}),
])
def test_a_pending_question_takes_only_an_answer_shape(need, said, got):
    ask = _asked(need)["ask"]
    assert coachintent.call_for(said, None, True, _asked(need)) == (ask["skill"], {**ask["args"], **got})


@pytest.mark.parametrize("said", ["1:55", "17:52", "3:00", "it was 2:10"])
def test_a_time_is_never_a_push_up_count(said):
    hit = coachintent.call_for(said, {"prompted": True}, True, dict(ON, current={"kind": "test", "test": "hrp"}))
    assert not (hit and hit[0] == "log_fitness_test")


@pytest.mark.parametrize("said", ["5", "60", "10", "I got 5"])
def test_a_bare_test_result_needs_the_prompt(said):
    hit = coachintent.call_for(said, {"prompted": False}, True, dict(ON, current={"kind": "test", "test": "hrp"}))
    assert not (hit and hit[0] == "log_fitness_test")
    assert coachintent.call_for(said, {"prompted": True}, True, dict(ON, current={"kind": "test", "test": "hrp"}))[0] == "log_fitness_test"


def test_a_timed_test_needs_a_time():
    plank = dict(ON, current={"kind": "test", "test": "plk"})
    assert coachintent.call_for("44", {"prompted": True}, True, plank) != ("log_fitness_test", {"event": "plk", "value": "44"})
    assert coachintent.call_for("1:55", {"prompted": True}, True, plank) == ("log_fitness_test", {"event": "plk", "value": "1:55"})


@pytest.mark.parametrize("event, bad, good", [("hrp", ["0", "151"], ["1", "150"]), ("mdl", ["90", "720"], ["95", "700"]),
                                              ("plk", ["0:09", "10:01"], ["0:10", "10:00"]), ("sdc", ["0:59", "6:01"], ["1:00", "6:00"]),
                                              ("run", ["8:59", "40:01"], ["9:00", "40:00"])])
def test_test_results_outside_sane_bounds_are_refused(event, bad, good):
    from core import pca_engine as engine
    for value in bad:
        number, error = engine.parse_value(event, value)
        assert number is None and "outside what I can record" in error, (event, value)
    for value in good:
        assert engine.parse_value(event, value)[1] == "", (event, value)


# main's behaviour for a user WITHOUT the program (scratchpad/main_table.py against `git show main:core/coachintent.py`):
# (sentence, call_for(.., active=True), call_for(.., active=False), maybe)
_FS = ("fitness_progress", {})
MAIN = [
    ("what's the mission", None, None, False), ("start my mission", None, None, False), ("start the mission", None, None, False),
    ("begin the mission", None, None, False), ("what's on the mission today", None, None, False), ("what's my mission today", None, None, False),
    ("brief me", None, None, False), ("mission briefing", None, None, False), ("why is my run shorter", None, None, False),
    ("how is my run going", None, None, False), ("I ran 3 miles in 25 minutes", None, None, False),
    ("log my run: 28 minutes, 2.6 miles, effort 4", None, None, False), ("I ran two miles in 18:05", None, None, False),
    ("that run was a 5 out of 10", None, None, False), ("my two-mile was 17:52", None, None, False), ("plank test 2:05", None, None, True),
    ("I got 44 push-ups on the test", None, None, False), ("deadlift test 340 for 3", None, None, True),
    ("my left knee hurts, about a 5", None, None, False), ("my knee hurts", None, None, False), ("my chest hurts", None, None, False),
    ("my knee is fine now", None, None, False), ("we had unit PT this morning, a hard 3 mile run", None, None, False),
    ("I rucked 6 miles", None, None, False), ("run it as planned", None, None, False), ("I feel fine, give me the full session", None, None, False),
    ("how am I progressing", _FS, None, True), ("why is today easier", None, None, False), ("the test is tomorrow", None, None, False),
    ("run it again", None, None, False), ("I ran into Mike today", None, None, False), ("set a timer for 5 minutes", None, None, True),
    ("go", None, None, False), ("rep done", None, None, True), ("start my workout", ("start_workout", {}), ("start_workout", {}), True),
    ("what's my workout for today", ("today_workout", {}), ("today_workout", {}), True), ("how is my bench going", _FS, _FS, True),
    ("why did you cut my sets", ("why_training_change", {"what": "sets"}), ("why_training_change", {"what": "sets"}), True),
    ("log a set of bench, 8 at 135", ("log_set", {"exercise": "bench", "weight_lbs": 135.0, "reps": 8}),
     ("log_set", {"exercise": "bench", "weight_lbs": 135.0, "reps": 8}), True),
    ("my goal is to build muscle", ("set_training_goal", {"goal": "muscle"}), ("set_training_goal", {"goal": "muscle"}), True),
    ("build me a training plan", ("build_training_plan", {}), ("build_training_plan", {}), True), ("play some music", None, None, False),
    ("how is my training going", _FS, _FS, True), ("why did my bench go down", ("why_training_change", {"what": "bench"}),
                                                    ("why_training_change", {"what": "bench"}), True),
    ("my back is killing me", None, None, False), ("the mission is to finish the report", None, None, False),
]


@pytest.mark.parametrize("said, active_call, idle_call, maybe", MAIN)
def test_a_user_without_the_program_routes_exactly_as_on_main(said, active_call, idle_call, maybe):
    for ctx in (None, {"on": False}):
        assert coachintent.call_for(said, None, True, ctx) == active_call
        assert coachintent.call_for(said, None, False, ctx) == idle_call
    assert coachintent.maybe(said) is maybe


def test_the_wider_listening_is_only_for_the_program():
    assert coachintent.maybe("why is my run shorter", pca_on=True) and not coachintent.maybe("why is my run shorter")
    assert coachintent.maybe("how's captain america going")                               # the name alone still reaches it
    assert coachintent.call_for("start Captain America", None, False, {"on": False}) == ("captain_begin", {})


@pytest.fixture
def real_cues(monkeypatch):
    from core import hud, timers
    from tools import system
    monkeypatch.setattr(hud, "_send", lambda *a, **k: None)
    monkeypatch.setattr(system, "announce", lambda line, *a, **k: None)
    monkeypatch.setattr(trainer, "SAY", trainer._say_after)
    timers.clear()
    yield lambda: [t for t in timers.active() if t["label"] == "workout cue"]
    timers.clear()


@pytest.mark.parametrize("then", ["run", "test", "external", "pain"])
def test_logging_the_run_stops_its_cues(pinned, real_cues, then):
    _begin()
    for week, weekday in ((1, 2), (2, 0), (2, 2), (3, 0)):
        at(pinned, week, weekday)
        pca.log_run_text(20, 4)
    at(pinned, 3, 2)
    with trainer.by_voice():
        trainer.start()
        _to("run")
        pca.captain_go()
        assert len(real_cues()) == 3
        {"run": lambda: pca.log_run_text(30, 7), "test": lambda: pca.log_test("plk", "2:00"),
         "external": lambda: pca.log_external("unit_run", 30, 8), "pain": lambda: pca.report_pain("left knee", 6)}[then]()
    assert real_cues() == []


@pytest.mark.parametrize("said", ["I had pt with my therapist", "we did pt this morning", "company field day was fun", "we had morning pt"])
def test_physical_therapy_and_chat_are_not_unit_pt(said):
    assert coachintent.call_for(said, None, True, ON) is None


@pytest.mark.parametrize("said, args", [
    ("we had unit PT this morning, a hard 3 mile run", {"kind": "unit_run", "rpe": 7, "miles": 3.0}),
    ("we did company pt, a 4 mile run", {"kind": "unit_run", "miles": 4.0}),
    ("we had morning pt, a 3 mile run", {"kind": "unit_run", "miles": 3.0}),
    ("we had pt with the platoon, legs", {"kind": "unit_strength"}),
])
def test_unit_pt_is_still_taken(said, args):
    assert coachintent.call_for(said, None, True, ON) == ("log_unit_pt", args)


def test_chest_pain_is_never_trained_through(pinned):
    _begin()
    at(pinned, 1, 1)                                                                     # Tuesday: bench press day
    assert coachintent.call_for("my chest hurts", None, True, ON) == ("report_pain", {"area": "chest"})
    with trainer.by_voice():
        out = pca.report_pain("chest")
    assert "not something to train through" in out and "shortness of breath" in out and "medical help" in out
    assert not pca.asking()                                                              # no 0-10 question for chest pain
    row = [r for r in trainer.events() if r["type"] == "pain"][-1]
    assert row["area"] == "chest" and row["level"] is None
    day = trainer.view()
    assert "bench_press" not in [i["key"] for i in day["items"]]
    pain = next(a for a in day["adjustments"] if a["kind"] == "pain")
    assert "out of 10" not in pain["reason"] and pain["overridable"] is False


# ---- review round 2 ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["20 minutes, turn on the light", "5, call mom", "30 minutes; set a timer for 10 minutes",
                                  "20 minutes, and my knee hurts", "1,5", "20 minutes, 5"])
def test_an_answer_with_any_other_tail_drops_the_question(said):
    assert coachintent.call_for(said, None, True, _asked("minutes")) is None


@pytest.mark.parametrize("said, got", [("20 minutes, rpe 6", {"minutes": 20.0, "rpe": 6}), ("20 minutes, a 6", {"minutes": 20.0, "rpe": 6}),
                                       ("20 minutes, pretty hard", {"minutes": 20.0, "rpe": 7}), ("20 minutes, 6 out of 10", {"minutes": 20.0, "rpe": 6})])
def test_an_answer_tail_that_is_the_effort_is_used(said, got):
    assert coachintent.call_for(said, None, True, _asked("minutes")) == ("log_run", got)


@pytest.mark.parametrize("said", ["I have chest pain", "chest pain", "I'm having chest pain", "I've got chest pains", "my chest hurts when I breathe",
                                  "pain in my chest", "tightness in my chest", "my chest hurts"])
def test_every_chest_pain_wording_gets_the_safety_path(said):
    assert coachintent.call_for(said, None, True, ON) == ("report_pain", {"area": "chest"})
    assert coachintent.call_for(said, None, True, {"on": False}) is None


@pytest.mark.parametrize("said", ["chest day", "my chest is sore from bench", "it's chest day today"])
def test_chest_training_talk_is_not_the_safety_path(said):
    assert coachintent.call_for(said, None, True, ON) != ("report_pain", {"area": "chest"})


@pytest.mark.parametrize("then", ["run", "test", "external"])
def test_an_entry_for_another_day_leaves_todays_cues_alone(pinned, real_cues, then):
    _begin()
    for week, weekday in ((1, 2), (2, 0), (2, 2), (3, 0)):
        at(pinned, week, weekday)
        pca.log_run_text(20, 4)
    at(pinned, 3, 2)
    with trainer.by_voice():
        trainer.start()
        _to("run")
        pca.captain_go()
        earlier = (pinned["now"].date() - timedelta(days=2)).isoformat()
        {"run": lambda: pca.log_run_text(30, 4, day=earlier), "test": lambda: pca.log_test("plk", "2:00", earlier),
         "external": lambda: pca.log_external("ruck", 90, 6, 6, earlier)}[then]()
    assert len(real_cues()) == 3


@pytest.mark.parametrize("current, said, prompted", [
    ("hrp", "I did a test at 4", True), ("hrp", "test 3 times", True), ("hrp", "2 mile run 18:05", True),
    ("hrp", "the test is at 5", True), ("plk", "plank 44", True), ("hrp", "44", False),
])
def test_on_a_test_item_only_its_result_shape_counts(current, said, prompted):
    hit = coachintent.call_for(said, {"prompted": prompted}, True, dict(ON, current={"kind": "test", "test": current}))
    assert not (hit and hit[0] == "log_fitness_test"), hit


@pytest.mark.parametrize("current, said, prompted, value", [
    ("hrp", "44", True, "44"), ("hrp", "I got 44", True, "44"), ("hrp", "44 push-ups", False, "44"), ("hrp", "44 reps", False, "44"),
    ("plk", "2:05", True, "2:05"), ("plk", "plank 2:05", False, "2:05"), ("run", "17:52", True, "17:52"),
    ("run", "2 mile run 18:05", False, "18:05"), ("mdl", "340 pounds", False, "340"),
])
def test_on_a_test_item_its_result_shape_is_logged(current, said, prompted, value):
    assert coachintent.call_for(said, {"prompted": prompted}, True, dict(ON, current={"kind": "test", "test": current})) == \
        ("log_fitness_test", {"event": current, "value": value})


def test_a_two_mile_time_is_a_test_only_when_it_says_so():
    assert coachintent.call_for("2 mile run 18:05", None, True, ON) is None
    assert coachintent.call_for("2 mile time trial 18:05", None, True, ON) == ("log_fitness_test", {"event": "run", "value": "18:05"})
