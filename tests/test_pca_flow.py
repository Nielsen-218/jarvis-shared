"""Project Captain America end to end (tools/pca + tools/trainer + core/pca_engine): onboarding, a blocked start, the
start, a guided session with a hold, tests, baselines, paces, a bad night, an override, progress, a reload, and an
old non-PCA plan that is left alone. Sandboxed storage (tests/conftest.py) and a pinned clock; no model, no network."""
from datetime import date, datetime, timedelta

import pytest

from core import pca_program as program
from tools import fitness, pca, trainer

MON = datetime(2026, 11, 23, 7, 0)         # a Monday morning: the program starts this week


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    box = {"now": MON}
    monkeypatch.setattr(trainer, "clock", lambda: box["now"])
    monkeypatch.setattr(trainer, "REST", lambda seconds, line, uid: None)
    return box


def at(box, week, weekday, hour=7):
    box["now"] = MON + timedelta(days=7 * (week - 1) + weekday, hours=hour - 7)
    return box["now"].date()


ANSWERS = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
           "equipment": "trap_bar,barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
           "runs_per_week": 1, "miles_per_week": 4, "weeks_since_last_run": 2, "weeks_since_last_heavy_lift": 1, "pain_level": 0,
           "medical_restriction": "no", "sickle_cell": "no", "sleep_hours": 7, "unit_pt_days": 3, "unit_pt_kind": "mixed"}


def _onboard(**over):
    for qid, value in {**ANSWERS, **over}.items():
        out = pca.page_action({"action": "answer", "id": qid, "value": value})
        assert out.startswith("Saved"), (qid, out)


def _today():
    return pca.page_data()["today"]


def _item(day, key=None, kind=None):
    return next(i for i in day["items"] if (key is None or i["key"] == key) and (kind is None or i["kind"] == kind))


def test_the_acceptance_path(pinned):
    trainer.set_goal("build muscle", 4, 60, "gym", 24)                 # an earlier plan he had
    first = pca.page_data()
    assert first["status"] == "onboarding" and first["program"] is None
    assert {q["id"] for q in first["onboarding"]["questions"]} >= {"days_per_week", "equipment", "pain_level", "music_service"}
    assert "disclaimer" in first and "not reviewed by a certified human coach" in first["disclaimer"]

    _onboard(pain_level=5)
    refused = pca.page_action({"action": "begin"})
    assert refused.startswith("I can't start") and "medic or physical therapist" in refused
    assert pca.page_data()["onboarding"]["blocking"]
    _onboard()
    assert pca.page_data()["status"] == "ready"
    began = pca.page_action({"action": "begin"})
    assert "starts today" in began

    data = fitness._data()
    assert data["plan"]["program"] == program.PROGRAM_ID and data["plan"]["start"] == "2026-11-23"
    assert data["plan_history"][-1]["goal"] == "muscle"                  # the earlier plan was kept

    page = pca.page_data()
    assert page["status"] == "active" and page["program"]["week"] == 1 and page["program"]["provisional"] is True
    today = page["today"]
    assert today["status"] == "train" and today["title"] == "Lower-body strength and power" and today["intensity"] == "high"
    assert today["warmup"] and today["cooldown"] and today["minutes"] >= 45
    assert today["briefing"].startswith("Today: lower-body strength and power.") and "deadlift" in today["briefing"].lower()
    assert page["baselines"]["paces"] is None and page["baselines"]["run"] is None
    assert "not confirmed" in page["baselines"]["historical"]["label"]

    # the guided session: the box jumps (power) then the deadlift with reps in reserve
    assert pca.page_action({"action": "start"}).startswith("Lower-body strength and power")
    for _ in range(3):
        trainer.report(3)
    for _ in range(3):
        out = trainer.report(5, 185, 4)
    assert "set 3 of 3" in out
    ended = pca.page_action({"action": "end"})
    assert ended.startswith("Workout done: 6 sets")
    sets = [r for r in trainer.events() if r["type"] == "set" and r["key"] == "deadlift"]
    assert sets[-1]["target_lo"] == 5 and sets[-1]["target_rir"] == 4

    # Wednesday: baseline tests, then an easy run
    at(pinned, 1, 2)
    wed = _today()
    assert {i.get("test") for i in wed["items"] if i["kind"] == "test"} == {"hrp", "plk"}
    pca.page_action({"action": "start"})
    assert "is a test: tell me the result" in pca.page_action({"action": "set", "reps": 40})   # a test is never logged as a set
    assert pca.page_action({"action": "test", "event": "hrp", "value": "40"}).startswith("Recorded your hand-release push-ups: 40 reps")
    assert "at least" in pca.page_action({"action": "test", "event": "plk", "value": "1:50"})
    assert pca.page_action({"action": "test", "event": "hrp", "value": "400"}).startswith("A hand-release push-ups of 400")
    assert pca.page_action({"action": "run", "minutes": 25, "rpe": 4}).startswith("Run logged: 25 minutes at RPE 4")
    page = pca.page_data()
    assert page["baselines"]["hrp"]["fresh"] is True and page["baselines"]["hrp"]["value"] == 40
    assert page["baselines"]["plk"]["text"] == "1:50"
    assert all(i["done"] for i in page["today"]["items"] if i["kind"] == "test" or i.get("run"))

    # Saturday: the plank holds are a share of the tested plank, and a hold is logged in seconds
    at(pinned, 1, 5)
    sat = _today()
    plank = _item(sat, "plank", "hold")
    assert plank["reps"] == "66 s" and plank["resolved"] is True                          # 60% of 110 s
    pca.page_action({"action": "start"})
    held = trainer.report(66, exercise="plank")
    assert held.startswith("Logged 66 seconds")
    pca.page_action({"action": "end"})

    # week 2: the push-up practice sets resolve to real numbers; the two-mile trial on Saturday
    at(pinned, 2, 0)
    pca.page_action({"action": "run", "minutes": 15, "rpe": 3})
    at(pinned, 2, 2)
    hrp = _item(_today(), "hand_release_pushup", "lift")
    assert hrp["reps"] == "20" and "50% of your tested 40" in hrp["target"]
    at(pinned, 2, 5)
    assert _item(_today(), kind="test")["test"] == "run"
    assert "Run paces now come from this result" in pca.page_action({"action": "test", "event": "run", "value": "18:00"})
    page = pca.page_data()
    assert page["baselines"]["paces"]["I"] == "8:55-9:16 /mi" and page["baselines"]["run"]["points_at_least"] == 72

    # keep training through weeks 3-5 (runs logged) so no gap rule fires
    for week, weekday in ((3, 0), (3, 2), (3, 5), (4, 0), (4, 2), (4, 5), (5, 0), (5, 1)):
        at(pinned, week, weekday)
        pca.page_action({"action": "run", "minutes": 20, "rpe": 4})

    # week 5 Wednesday: intervals with splits from the trial
    at(pinned, 5, 2)
    run = _item(_today(), "run")
    assert run["run"]["type"] == "intervals" and run["pace_lines"][0].startswith("6 x 400 m in 2:13-2:18")

    # a bad night makes the hard day easier, with a reason he can override
    fitness.log_checkin(4.5, 2, 3, date=pinned["now"].date().isoformat())
    day = _today()
    run = _item(day, "run")
    assert run["run"]["type"] == "easy" and run["reps"] == "27 min"                       # 70% of the planned 39 minutes
    ready = next(a for a in day["adjustments"] if a["kind"] == "readiness" and "easier" in a["reason"])
    assert ready["overridable"] is True and "4.5 hours" in ready["reason"]
    assert pca.page_action({"action": "override"}).startswith("Done: today runs as planned")
    day = _today()
    assert _item(day, "run")["run"]["type"] == "intervals"
    assert not [a for a in day["adjustments"] if a["kind"] == "readiness"]
    assert any(a["kind"] == "override" for a in day["adjustments"])

    # progress: only tests and logged sets, never a projection
    hist = pca.page_data()["history"]
    assert hist["tests"] and {t["source"] for t in hist["tests"]} == {"test"}
    assert {p["source"] for lift in hist["lifts"] for p in lift["points"]} == {"logged"}
    assert hist["weeks"][0]["run_min_done"] == 25 and hist["compliance"] is not None

    # everything survives a reload
    again = pca.page_data()
    assert again["status"] == "active" and again["program"]["week"] == 5 and again["baselines"]["run"]["value"] == 1080
    assert fitness._data()["pca"]["started"] == "2026-11-23"


def test_an_old_plan_without_the_program_is_untouched(pinned):
    trainer.set_goal("build muscle", 4, 60, "gym", 24)
    day = trainer.view()
    assert "session_kind" not in day and "baselines" not in day
    assert pca.page_data()["status"] == "onboarding"
    assert pca.page_action({"action": "test", "event": "hrp", "value": "40"}).startswith("Start Project Captain America first")
    assert fitness._data()["plan"]["goal"] == "muscle"


def test_begin_refuses_without_clearance_and_when_incomplete(pinned):
    assert pca.begin().startswith("Before I can start, I still need")
    _onboard(medical_restriction="yes", provider_cleared="no")
    out = pca.begin()
    assert out.startswith("I can't start") and "clearance" in out
    _onboard(medical_restriction="yes", provider_cleared="yes")
    assert "starts today" in pca.begin()


def test_start_date_snaps_to_monday(pinned):
    _onboard()
    at(pinned, 1, 3)                                           # a Thursday: the program waits for next Monday
    assert "Monday 2026-11-30" in pca.begin()
    assert pca.page_data()["today"]["status"] == "before"
    assert pca.begin("2026-11-01") == "Project Captain America is already set to start on 2026-11-30."   # never begun twice
    assert "past" in pca._start_date("2026-11-01", pinned["now"].date())[1]


def test_actions_refuse_nonsense_helpfully(pinned):
    _onboard()
    pca.begin()
    assert pca.page_action({"action": "run", "minutes": 0, "rpe": 4}).startswith("How many minutes")
    assert pca.page_action({"action": "run", "minutes": 20, "rpe": 14}).startswith("How hard")
    assert pca.page_action({"action": "pain", "area": "knee", "level": 11}).startswith("How bad")
    assert pca.page_action({"action": "external", "kind": "nap", "minutes": 20, "rpe": 3}).startswith("What was it")
    assert pca.page_action({"action": "rollback", "weeks": 9}).startswith("Roll back 1 to 4")
    assert pca.page_action({"action": "answer", "id": "days_per_week", "value": 3}).startswith("Training days a week: choose")
    assert pca.page_action({"action": "test", "event": "xyz", "value": "1"}).startswith("Which test")
    assert pca.page_action({"action": "fly"}) == "I don't know that action."
    assert pca.page_action({"action": "audio", "prefs": {"service": "Spotify", "playlist": "PT"}}) == "Music preferences saved."
    assert pca.page_data()["audio"]["prefs"]["service"] == "spotify"            # stored as the service id


def test_pain_changes_today_and_cannot_be_overridden(pinned):
    _onboard()
    pca.begin()
    assert "a provider can" in pca.page_action({"action": "pain", "area": "left knee", "level": 5})
    day = _today()
    keys = [i["key"] for i in day["items"]]
    assert "deadlift" not in keys and "back_squat" not in keys
    pain = next(a for a in day["adjustments"] if a["kind"] == "pain")
    assert pain["overridable"] is False
    pca.page_action({"action": "override"})
    assert "deadlift" not in [i["key"] for i in _today()["items"]]
    assert pca.page_action({"action": "pain_clear", "area": "knee"}).startswith("Cleared: left knee")
    assert "deadlift" in [i["key"] for i in _today()["items"]]


def test_rollback_moves_the_start_and_is_logged(pinned):
    _onboard()
    pca.begin()
    at(pinned, 3, 0)
    assert pca.page_data()["program"]["week"] == 3
    out = pca.page_action({"action": "rollback", "weeks": 1})
    assert "now counts from 2026-11-30" in out and pca.page_data()["program"]["week"] == 2
    assert any(c["kind"] == "rollback" for c in pca.page_data()["changes"])


def test_skills_are_registered_and_answer():
    from tools import registry
    for name in ("captain_status", "captain_onboarding_answer", "captain_begin", "log_run", "log_fitness_test", "report_pain",
                 "clear_pain", "log_unit_pt", "captain_override", "captain_progress"):
        assert name in registry.names(), name
    assert pca.captain_status().startswith("Project Captain America isn't started")


def test_the_page_api_runs_onboarding_and_a_session(pinned):
    import json

    from fastapi.testclient import TestClient

    from core import appserver
    from tools import voice

    c = TestClient(appserver.AppServer(lambda uid: None).app)
    assert c.get("/api/pca").status_code in (401, 403)
    voice.pair_codes["555555"] = "owner"
    h = {"Authorization": "Bearer " + c.post("/api/pair", json={"code": "555555"}).json()["token"]}
    page = c.get("/api/pca", headers=h).json()
    assert page["status"] == "onboarding" and page["onboarding"]["questions"][0]["id"] == "height"
    for qid, value in ANSWERS.items():
        out = c.post("/api/pca/action", headers=h, json={"action": "answer", "id": qid, "value": value}).json()
        assert out["ok"] and out["message"].startswith("Saved"), out["message"]
    out = c.post("/api/pca/action", headers=h, json={"action": "begin"}).json()
    assert "starts today" in out["message"] and out["data"]["status"] == "active"
    json.dumps(out["data"])                                            # the whole page is plain JSON
    assert set(out["data"]) >= {"status", "onboarding", "program", "today", "baselines", "history", "changes", "long_term", "exercises",
                                "audio", "disclaimer"}
    assert out["data"]["exercises"]["deadlift"]["alternatives"] and out["data"]["exercises"]["deadlift"]["cues"]
    out = c.post("/api/pca/action", headers=h, json={"action": "start"}).json()
    assert out["data"]["today"]["session"]["open"] is True and out["data"]["today"]["session"]["current"] == 0
    assert c.post("/api/pca/action", headers=h, json={"action": "nope"}).json()["message"] == "I don't know that action."
    assert c.get("/captain.html").status_code in (200, 404)           # served from app/ like fitness.html once the page exists
