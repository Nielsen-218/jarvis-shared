"""Timers you can see and cancel, focus mode, countdowns, achievements, the weekly report and their APIs."""
import threading
import time
from datetime import date, timedelta

import pytest

from core import focus, hud, storage, timers, users
from tools import fitness, life


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(hud, "_send", lambda m: None)
    yield
    timers.clear()
    focus.stop()


# -- timers --------------------------------------------------------------------------------------
def test_timers_are_listed_cancelled_by_name_and_fire_once():
    fired = []
    timers.add(60, "pasta", fired.append)
    timers.add(120, "laundry", fired.append)
    assert [t["label"] for t in timers.active()] == ["pasta", "laundry"]
    assert timers.cancel("pasta") == ["pasta"] and [t["label"] for t in timers.active()] == ["laundry"]
    done = threading.Event()
    timers.add(0.05, "tea", lambda name: (fired.append(name), done.set()))
    assert done.wait(2) and fired == ["tea"]
    assert "tea" not in [t["label"] for t in timers.active()]


def test_cancel_timer_tool_speaks_plainly():
    timers.add(300, "oven", lambda n: None)
    assert "oven" in life.list_timers()
    assert life.cancel_timer("oven") == "Cancelled the oven timer."
    assert life.cancel_timer("oven") == "No timers are running."


def test_set_timer_goes_through_the_registry(monkeypatch):
    from tools import system
    said = []
    monkeypatch.setattr(system, "announce", said.append)
    assert system.set_timer(30, "eggs") == "30 seconds on the eggs timer. I'll tell you when it's done."
    assert [t["label"] for t in timers.active()] == ["eggs"]


# -- focus ---------------------------------------------------------------------------------------
def test_focus_cycles_focus_break_and_finishes(monkeypatch):
    said = []
    monkeypatch.setattr(focus, "announce", said.append)
    s = focus.start(25, 5, 2)
    assert s["phase"] == "focus" and focus.active() and 1490 <= s["left"] <= 1500
    focus._next()
    assert focus.status()["phase"] == "break" and "Take a 5-minute break" in said[-1]
    focus._next()
    assert focus.status()["phase"] == "focus" and "Block 2 of 2" in said[-1]
    focus._next()
    assert focus.status()["phase"] == "off" and "50 minutes" in said[-1]


def test_focus_tool_start_status_stop(monkeypatch):
    monkeypatch.setattr(focus, "announce", lambda t: None)
    assert "Focus mode on: 4 blocks of 25 minutes" in life.focus_mode("start")
    assert life.focus_mode("status").startswith("Focus:")
    assert life.focus_mode("stop") == "Focus mode off."
    assert life.focus_mode("status") == "Focus mode is off."


# -- countdowns ----------------------------------------------------------------------------------
def test_countdowns_merge_custom_aft_and_trips_and_drop_the_past():
    today = date.today()
    d = fitness._data()
    d["profile"]["test_date"] = (today + timedelta(days=20)).isoformat()
    fitness._save(d)
    storage.save(f"trips/{'miami'}.json", {"name": "Miami", "destination": "Miami", "start": (today + timedelta(days=5)).isoformat()})
    life.add_countdown("Christmas", f"{today.year if today.month < 12 or today.day < 25 else today.year + 1}-12-25")
    life.add_countdown("Old thing", (today - timedelta(days=3)).isoformat())
    titles = [c["title"] for c in life.countdowns()]
    assert titles[:2] == ["Miami", "AFT test"] and "Christmas" in titles and "Old thing" not in titles
    assert life.remove_countdown("christ") == "Removed." and "Christmas" not in [c["title"] for c in life.countdowns()]
    assert life.add_countdown("x", "next friday") == "Give the date as YYYY-MM-DD."


# -- achievements --------------------------------------------------------------------------------
def _w(d, items=None):
    return {"date": d.isoformat(), "title": "W", "items": items or [{"exercise": "Bench", "sets": 3, "reps": 8}], "rpe": 7}


def test_streak_allows_one_rest_day_and_breaks_after_two():
    today = date(2026, 10, 22)
    days = [today - timedelta(days=n) for n in (0, 2, 3, 5, 9)]          # 0,2,3,5 chain; 9 is broken off
    a = life.achievements({"workouts": [_w(d) for d in days]}, today)
    assert a["streak"] == 4 and a["best_streak"] == 4 and a["total"] == 5
    old = life.achievements({"workouts": [_w(today - timedelta(days=5))]}, today)
    assert old["streak"] == 0 and old["best_streak"] == 1


def test_badges_for_volume_strength_and_aft_goal():
    today = date(2026, 10, 22)
    mon = today - timedelta(days=today.weekday())
    week = [_w(mon + timedelta(days=i), [{"exercise": "Trap-bar deadlift", "sets": 20, "reps": 3, "weight_lbs": 335}])
            for i in range(6)]
    a = life.achievements({"workouts": week, "aft_tests": [{"total": 512}], "profile": {"goal_total": "500"}}, today)
    got = {b["id"] for b in a["badges"] if b["earned"]}
    assert {"first", "week4", "week6", "streak5", "sets100", "plates3", "aft", "aftgoal"} <= got
    assert "century" not in got and a["this_week"] == 6


# -- weekly report -------------------------------------------------------------------------------
def test_weekly_report_compares_with_last_week():
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    d = fitness._data()
    d["workouts"] = [_w(mon), _w(mon - timedelta(days=3)), _w(mon - timedelta(days=4))]
    fitness._save(d)
    w = life.weekly(today)
    assert w["training"]["workouts"] == 1 and w["training"]["last_week"] == 2 and w["training"]["sets"] == 3
    assert "1 workout, down from 2 last week, 3 working sets at an average effort of 7 out of 10." in life.weekly_report()


# -- API -----------------------------------------------------------------------------------------
from tests.test_app import client, pair  # noqa: E402,F401


def test_api_timers_focus_lists_countdowns(client, monkeypatch):
    from tools import system
    monkeypatch.setattr(system, "announce", lambda t: None)
    monkeypatch.setattr(focus, "announce", lambda t: None)
    h = pair(client)
    assert client.post("/api/timers", json={"seconds": 90, "label": "plank"}, headers=h).json()["timers"][0]["label"] == "plank"
    assert client.post("/api/timers", json={"seconds": 0}, headers=h).status_code == 400
    assert client.post("/api/timers/cancel", json={"which": "plank"}, headers=h).json()["cancelled"] == ["plank"]
    assert client.post("/api/focus", json={"action": "start", "focus_minutes": 50}, headers=h).json()["focus_min"] == 50
    assert client.get("/api/focus", headers=h).json()["phase"] == "focus"
    items = client.post("/api/lists/add", json={"text": "printer ink", "list": "shopping"}, headers=h).json()["items"]
    ink = next(i for i in items if i["text"] == "printer ink")
    assert ink["id"] not in [i["id"] for i in client.post("/api/lists/done", json={"ids": [ink["id"]]}, headers=h).json()["items"]]
    assert client.post("/api/lists/add", json={"text": "x", "list": "secret"}, headers=h).status_code == 400
    future = (date.today() + timedelta(days=9)).isoformat()
    cds = client.post("/api/countdowns", json={"title": "Leave", "date": future}, headers=h).json()["countdowns"]
    leave = next(c for c in cds if c["title"] == "Leave")
    assert leave["days"] == 9
    assert "Leave" not in [c["title"] for c in client.post("/api/countdowns/remove", json={"id": leave["id"]}, headers=h).json()["countdowns"]]
    assert "training" in client.get("/api/weekly", headers=h).json()


def test_api_household_members_cannot_drive_the_pc(client):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    h = pair(client, wife)
    assert client.post("/api/timers", json={"seconds": 60}, headers=h).status_code == 403
    assert client.post("/api/focus", json={"action": "start"}, headers=h).status_code == 403
    assert client.get("/api/media", headers=h).status_code == 403


def test_logging_a_workout_reports_new_badges(client):
    h = pair(client)
    r = client.post("/api/fitness/log", json={"title": "Upper", "items": [{"exercise": "Bench", "sets": 3, "reps": 8}], "rpe": 7},
                    headers=h).json()
    assert [b["id"] for b in r["new_badges"]] == ["first"]
    again = client.post("/api/fitness/log", json={"title": "Upper", "items": [{"exercise": "Bench", "sets": 3, "reps": 8}]},
                        headers=h).json()
    assert again["new_badges"] == [] and again["data"]["achievements"]["total"] == 2
