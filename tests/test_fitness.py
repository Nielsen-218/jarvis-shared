import pytest
"""AFT trainer: profile, tests, plan, workouts, Garmin sync (faked) and the phone app API."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from tools import fitness


def _monday() -> str:
    t = date.today()
    return (t - timedelta(days=t.weekday())).isoformat()


def test_aft_tests_and_status():
    assert "No AFT results yet" in fitness.aft_status()
    fitness.set_fitness_profile("test_date", (date.today() + timedelta(days=30)).isoformat())
    fitness.log_aft_test("2026-08-01", 275, 40, "2:05", "2:30", "16:10", {"mdl": 82, "hrp": 70, "sdc": 72, "plk": 75, "run": 68})
    out = fitness.log_aft_test("2026-10-01", 315, 46, "1:58", "3:05", "15:30",
                               {"mdl": 90, "hrp": 76, "sdc": 80, "plk": 86, "run": 74}, True)
    assert "Total 406/500" in out and "Weakest: run, hrp" in out
    assert "2-mile run: 15:30 = 74 pts (better: 40 s)" in out and "Next AFT in 30 days" in out


def test_plan_today_and_logging():
    day = date.today().strftime("%a")
    fitness.set_training_plan("AFT 500", _monday(), [{"week": 1, "focus": "Base", "days": [
        {"day": day, "title": "Strength + intervals", "items": [
            {"exercise": "deadlift", "sets": "5", "reps": "3", "target": "80% 3RM"},
            {"exercise": "run intervals", "sets": "6", "reps": "400m", "target": "1:40"}]}]}])
    today = fitness.today_workout()
    assert today.startswith("Week 1 (Base): Strength + intervals") and "deadlift: 5x3 80% 3RM" in today
    assert "Logged 'Strength + intervals'. 1 workout in the last 7 days." == fitness.log_workout(
        "Strength + intervals", [{"exercise": "Deadlift", "sets": 5, "reps": 3, "weight_lbs": 255}], 7)
    assert "Heaviest logged deadlift: 255 lbs." in fitness.fitness_progress()


class FakeGarmin:
    def login(self, tokenstore=None):
        pass

    def get_activities_by_date(self, a, b):
        return [{"activityId": 1, "startTimeLocal": f"{date.today()} 06:00", "activityName": "Morning run",
                 "activityType": {"typeKey": "running"}, "distance": 4828.0, "duration": 1500, "averageHR": 150}]

    def get_sleep_data(self, d):
        return {"dailySleepDTO": {"sleepTimeSeconds": 5 * 3600, "sleepScores": {"overall": {"value": 61}}}}

    def get_hrv_data(self, d):
        return {"hrvSummary": {"lastNightAvg": 38, "weeklyAvg": 52, "status": "LOW"}}

    def get_stats(self, d):
        return {"restingHeartRate": 56, "bodyBatteryMostRecentValue": 35, "totalSteps": 8000}

    def get_training_readiness(self, d):
        return [{"score": 32}]

    def get_max_metrics(self, d):
        return [{"generic": {"vo2MaxPreciseValue": 51.2}}]

    def get_race_predictions(self):
        return {"time5K": 1260}


def test_garmin_sync_and_readiness(monkeypatch):
    monkeypatch.setattr(fitness, "_garmin", lambda: FakeGarmin())
    out = fitness.garmin_sync(2)
    assert "Synced Garmin: 1 activities" in out and "Recovery looks low" in out
    g = fitness._data()["garmin"]
    assert g["runs"][0]["pace_s_per_mi"] == 500 and g["vo2max"] == 51.2
    assert g["race_predictions"]["5k"] == "21:00" and g["race_predictions"]["2mile_est"].startswith("13:")


@pytest.mark.skipif(__import__("importlib").util.find_spec("garminconnect") is None, reason="optional garminconnect package not installed")
def test_garmin_not_connected_message():
    try:
        fitness._garmin()
        raise AssertionError("should need the one-time login")
    except RuntimeError as e:
        assert "garmin-login.bat" in str(e)


def test_phone_fitness_api():
    from core import appserver
    from tools import voice

    server = appserver.AppServer(lambda uid: None)
    c = TestClient(server.app)
    voice.pair_codes["333333"] = "owner"
    h = {"Authorization": "Bearer " + c.post("/api/pair", json={"code": "333333"}).json()["token"]}
    assert set(c.get("/api/fitness", headers=h).json()) >= {"profile", "plan", "today", "garmin", "exercises"}
    r = c.post("/api/fitness/log", headers=h, json={"title": "Push day", "items": [
        {"exercise": "hand_release_pushup", "sets": 5, "reps": 15}], "rpe": 8}).json()
    assert r["ok"] and r["data"]["workouts"][-1]["title"] == "Push day"
    assert c.get("/api/fitness").status_code in (401, 403)  # no phone token, no data


def test_pc_page_link_is_signed_in_and_reused():
    from core import appserver, users

    url = appserver.pc_page_url("owner", "fitness.html")
    assert url.startswith(f"http://127.0.0.1:{appserver.PORT}/fitness.html#t=")
    token = url.split("#t=")[1]
    assert appserver.pc_page_url("owner", "kitchen.html").endswith(token)  # same PC token, not a new device each time
    c = TestClient(appserver.AppServer(lambda uid: None).app)
    assert c.get("/api/fitness", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert sum(d["name"] == "This PC (browser)" for d in users.get("owner")["devices"]) == 1


def test_every_session_gets_warmup_and_cooldown():
    from tools import fitness_library as lib

    assert lib.session_kind({"title": "Upper A: chest priority", "items": [{"exercise": "Incline dumbbell press"},
                                                                           {"exercise": "Chest-supported row"}]}) == "upper"
    assert lib.session_kind({"title": "Run: Intervals", "items": [{"exercise": "Intervals: 400 m repeats"}]}) == "run"
    fitness.set_training_plan("Pro", _monday(), [{"week": 1, "focus": "Base", "days": [
        {"day": d, "title": "Upper A: chest priority", "why": "Chest first.",
         "items": [{"exercise": "incline_press", "sets": "4", "reps": "6-10", "rest": "2 min"}]}
        for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")]}])
    today = fitness.app_data()["today"]
    assert today["kind"] == "upper" and today["why"] == "Chest first."
    assert [p["phase"] for p in today["warmup"]] == ["Raise", "Activate & mobilize", "Potentiate"]
    assert any(c["exercise"] == "doorway_pec_stretch" for c in today["cooldown"])
    text = fitness.today_workout()
    assert "Warm-up: Jumping jacks" in text and "Cool-down: Doorway chest stretch" in text


def test_heritage_health_notes_and_exercise_details():
    assert fitness.app_data()["health"] == []
    fitness.set_fitness_profile("heritage", "Haitian")
    data = fitness.app_data()
    assert [n["title"] for n in data["health"]][:2] == ["Sickle cell trait", "Vitamin D"]
    assert data["exercises"]["incline_press"]["tall"] and data["exercises"]["couch_stretch"]["cues"]
    assert "RAMP" in fitness.training_science()


def test_morning_checkin_drives_readiness():
    out = fitness.log_checkin(sleep_hours=5.5, energy=2, soreness=3)
    assert "slept 5.5 h" in out and "Recovery looks low" in out
    fitness.log_checkin(sleep_hours=8, energy=4)  # same day: updates, keeps soreness
    data = fitness.app_data()
    assert data["checkin"]["sleep_hours"] == 8 and data["checkin"]["soreness"] == 3
    assert "Recovery looks low" not in data["readiness"] and "energy 4/5" in data["readiness"]


def test_phone_checkin_api():
    from fastapi.testclient import TestClient

    from core import appserver, users

    token = appserver.add_device("owner", "phone")
    c = TestClient(appserver.AppServer(lambda uid: None).app)
    r = c.post("/api/fitness/checkin", headers={"Authorization": f"Bearer {token}"},
               json={"sleep_hours": 7, "energy": 3, "soreness": 2, "readiness": "62", "resting_hr": ""})
    assert r.status_code == 200 and r.json()["data"]["checkin"]["readiness"] == 62
    assert c.post("/api/fitness/checkin", json={"sleep_hours": 7}).status_code in (401, 403)
    assert users.get("owner")
