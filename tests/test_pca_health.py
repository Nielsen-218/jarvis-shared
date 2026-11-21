"""Garmin through Android Health Connect (tools/pca_health + the /api/link/health and /api/health/status routes): the
validation of every field, per-person and per-machine storage, bounded growth, the old data["garmin"] shape and its
merge, the effective morning check-in, the watch-run offer and its one-tap log, the time-trial offer, the progress
series, the "health" flag in the Link poll, route auth, and the skills. Sandboxed storage (tests/conftest.py), a pinned
clock, no phone, no network, no Garmin login."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core import coach_engine, users
from tools import fitness, pca, pca_health, pca_link, registry, trainer

MON = datetime(2026, 11, 23, 7, 0)          # a Monday morning: the program starts this week
TZ = "-05:00"
GARMIN = pca_health.GARMIN_PACKAGE


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    box = {"now": MON, "machine": "server"}
    monkeypatch.setattr(trainer, "clock", lambda: box["now"])
    monkeypatch.setattr(trainer, "REST", lambda seconds, line, uid: None)
    monkeypatch.setattr(pca_health, "clock", lambda: box["now"].timestamp())
    monkeypatch.setattr(pca_health, "machine", lambda: box["machine"])
    monkeypatch.setattr(pca_link, "clock", lambda: box["now"].timestamp())
    monkeypatch.setattr(pca_link, "machine", lambda: box["machine"])
    return box


@pytest.fixture
def client(monkeypatch):
    from core import appserver

    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    server = appserver.AppServer(lambda uid: None)
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    return TestClient(server.app)


def pair(client, uid="owner", device="jarvis-link"):
    from tools import voice

    voice.pair_codes["333333"] = uid
    r = client.post("/api/pair", json={"code": "333333", "device": device})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def iso(d: date, hh=0, mm=0) -> str:
    return f"{d.isoformat()}T{hh:02d}:{mm:02d}:00{TZ}"


def night(wake: date, hours: float, deep=None, rem=None):
    start = datetime.combine(wake, datetime.min.time()) + timedelta(hours=6) - timedelta(hours=hours)
    r = {"start": start.strftime("%Y-%m-%dT%H:%M:%S") + TZ, "end": iso(wake, 6), "src": GARMIN}
    if deep is not None:
        r.update(deep_min=deep, rem_min=rem, light_min=60 * hours - deep - rem - 20, awake_min=20)
    return r


def run(day: date, minutes=28, seconds=10, miles=2.6, avg=148, mx=171, hh=17):
    total = minutes * 60 + seconds
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=hh)
    return {"start": start.strftime("%Y-%m-%dT%H:%M:%S") + TZ,
            "end": (start + timedelta(seconds=total)).strftime("%Y-%m-%dT%H:%M:%S") + TZ,
            "type": "running", "type_id": 33, "title": "Springfield Running", "distance_m": miles * 1609.344,
            "avg_hr": avg, "max_hr": mx, "kcal": 310, "avg_speed_mps": miles * 1609.344 / total, "src": GARMIN}


def payload(days=10, last_sleep=7.4, rhr_today=52, today=MON.date(), **extra):
    d0 = today
    body = {"v": 1, "app": "1.2", "final": True, "garmin_found": True, "sources": [GARMIN],
            "sleep": [night(d0 - timedelta(days=i), 7.2 if i else last_sleep, deep=70, rem=95) for i in range(days)],
            "resting_hr": [{"date": (d0 - timedelta(days=i)).isoformat(), "bpm": 50 + (i % 3) if i else rhr_today} for i in range(days)],
            "hr_daily": [{"date": (d0 - timedelta(days=i)).isoformat(), "min": 48, "avg": 66, "max": 160} for i in range(days)],
            "steps": [{"date": (d0 - timedelta(days=i)).isoformat(), "count": 9000 + i} for i in range(days)],
            "hrv": [{"date": (d0 - timedelta(days=i)).isoformat(), "rmssd_ms": 40 + i} for i in range(days)],
            "exercise": [run(d0 - timedelta(days=3)), run(d0 - timedelta(days=5), 40, 0, 3.5)],
            "weight": [{"time": iso(d0 - timedelta(days=1), 7), "kg": 88.5}],
            "vo2max": [{"time": iso(d0 - timedelta(days=2), 8), "value": 49.0}]}
    body.update(extra)
    return body


ANSWERS = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
           "equipment": "trap_bar,barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
           "runs_per_week": 1, "miles_per_week": 4, "weeks_since_last_run": 2, "weeks_since_last_heavy_lift": 1, "pain_level": 0,
           "medical_restriction": "no", "sickle_cell": "no", "sleep_hours": 7, "unit_pt_days": 3, "unit_pt_kind": "mixed"}


def begin():
    for qid, value in ANSWERS.items():
        assert pca.page_action({"action": "answer", "id": qid, "value": value}).startswith("Saved")
    assert "starts today" in pca.page_action({"action": "begin"})


# ---- validation -------------------------------------------------------------------------------------------------------
D = MON.date()
BAD = [
    ("sleep", {"start": iso(D, 6), "end": iso(D, 5)}),                          # ends before it starts
    ("sleep", {"start": iso(D - timedelta(days=2), 0), "end": iso(D, 6)}),      # 54 h: over 16 h
    ("sleep", {"start": "2026-11-22T23:00:00", "end": "2026-11-23T06:00:00"}),  # no zone offset
    ("sleep", {"start": 5, "end": True}),
    ("resting_hr", {"date": D.isoformat(), "bpm": 20}), ("resting_hr", {"date": D.isoformat(), "bpm": 130}),
    ("resting_hr", {"date": D.isoformat(), "bpm": "55"}), ("resting_hr", {"date": D.isoformat(), "bpm": True}),
    ("resting_hr", {"date": "23-11-2026", "bpm": 55}),
    ("hr_daily", {"date": D.isoformat(), "min": 60, "avg": 50, "max": 150}),    # min above avg
    ("hr_daily", {"date": D.isoformat(), "min": 20, "avg": 60, "max": 150}),
    ("hr_daily", {"date": D.isoformat(), "min": 40, "avg": 60, "max": 250}),
    ("hrv", {"date": D.isoformat(), "rmssd_ms": 0.5}), ("hrv", {"date": D.isoformat(), "rmssd_ms": 400}),
    ("exercise", {"start": iso(D, 6), "end": iso(D + timedelta(days=2), 7), "type": "running"}),  # over 24 h (and future)
    ("exercise", {"start": iso(D, 6), "end": iso(D, 6), "type": "running"}),
    ("steps", {"date": D.isoformat(), "count": -1}), ("steps", {"date": D.isoformat(), "count": 300_000}),
    ("calories", {"date": D.isoformat(), "kcal": 50_000}),
    ("weight", {"time": iso(D, 7), "kg": 20}), ("weight", {"time": iso(D, 7), "kg": 240}),   # 44 lb / 529 lb
    ("vo2max", {"time": iso(D, 7), "value": 5}), ("vo2max", {"time": iso(D, 7), "value": 95}),
    ("resting_hr", {"date": (D + timedelta(days=3)).isoformat(), "bpm": 55}),  # the future beyond a day
    ("resting_hr", {"date": (D - timedelta(days=401)).isoformat(), "bpm": 55}),  # older than 400 days
    ("vo2max", {"time": iso(D + timedelta(days=3), 7), "value": 50}),
    ("steps", "not a record"),
]


@pytest.mark.parametrize("kind,rec", BAD)
def test_each_bad_value_is_dropped_alone(kind, rec):
    good = {"date": D.isoformat(), "count": 5000}
    clean, dropped = pca_health.clean_payload({kind: [rec], "steps": [good] if kind != "steps" else [rec, good]})
    assert dropped == 1
    assert clean.get(kind, []) == [] or kind == "steps" and [r["count"] for r in clean["steps"]] == [5000]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), None, [], {}])
def test_nan_infinity_and_wrong_types_never_store(value):
    clean, dropped = pca_health.clean_payload({"resting_hr": [{"date": D.isoformat(), "bpm": value}],
                                               "hrv": [{"date": D.isoformat(), "rmssd_ms": value}]})
    assert clean == {"resting_hr": [], "hrv": []} and dropped == 2


def test_good_values_are_cleaned_and_converted():
    clean, dropped = pca_health.clean_payload(payload(days=2, extra_field="dropped", unknown=[1, 2]))
    assert dropped == 0 and "extra_field" not in clean and "unknown" not in clean
    w = clean["weight"][0]
    assert w["lb"] == round(88.5 * 2.20462, 1) and "kg" not in w                       # kg -> lb, stated in the module
    ex = clean["exercise"][0]
    assert ex["miles"] == 2.6 and ex["seconds"] == 28 * 60 + 10 and ex["type"] == "running" and ex["avg_hr"] == 148
    s = clean["sleep"][0]
    assert s["date"] == D.isoformat() and s["deep_h"] == round(70 / 60, 2) and s["hours"] == 7.4
    assert clean["sleep"][0]["id"].startswith("sleep|2026-11-23T")                        # stable id = type + start (UTC)


def test_refused_as_a_whole_only_for_a_non_object_or_unknown_version():
    for body in ([1, 2], "x", None):
        with pytest.raises(pca_health.HealthError):
            pca_health.clean_payload(body)
    with pytest.raises(pca_health.HealthError):
        pca_health.clean_payload({"v": 2})
    clean, dropped = pca_health.clean_payload({"sleep": "nope", "steps": None})
    assert clean == {} and dropped == 1


def test_duplicates_merge_by_type_and_start():
    a, b = night(D, 7.0), night(D, 7.0)
    b["minutes"] = 380
    clean, dropped = pca_health.clean_payload({"sleep": [a, b]})
    assert len(clean["sleep"]) == 1 and dropped == 1 and clean["sleep"][0]["hours"] == round(380 / 60, 2)
    pca_health.ingest({"sleep": [a], "final": True})
    pca_health.ingest({"sleep": [b], "final": True})                                     # a later post replaces it
    rec = pca_health.records()["sleep"]
    assert len(rec) == 1 and next(iter(rec.values()))["hours"] == round(380 / 60, 2)


def test_too_many_items_in_one_list_are_cut(monkeypatch):
    monkeypatch.setattr(pca_health, "MAX_ITEMS", 5)
    rows = [{"date": (D - timedelta(days=i)).isoformat(), "count": i} for i in range(8)]
    clean, dropped = pca_health.clean_payload({"steps": rows})
    assert len(clean["steps"]) == 5 and dropped == 3


# ---- storage ----------------------------------------------------------------------------------------------------------
def test_per_machine_files_one_writer_each_and_readers_merge(pinned):
    from core import storage

    pca_health.ingest(payload(days=3))
    assert storage.path("fitness/health-server.json").exists() and not storage.path("fitness/health-pc.json").exists()
    pinned["machine"] = "pc"
    assert len(pca_health.records()["sleep"]) == 3 and pca_health.connected()             # the PC reads the server's file
    pinned["now"] += timedelta(minutes=5)
    pca_health.ingest({"sleep": [night(D, 6.0)], "final": True})                         # a newer copy on the PC wins
    assert pca_health.records()["sleep"]["sleep|" + pca_health._key(datetime.fromisoformat(night(D, 6.0)["start"]))]["hours"] == 6.0
    pinned["machine"] = "server"
    assert len(storage.load("fitness/health-server.json", {})["records"]["sleep"]) == 3   # never rewritten by the PC


def test_people_are_separate():
    wife = users.add("Marie", {"en": "madame"}, "wife")
    with users.acting_as(wife):
        pca_health.ingest(payload(days=2))
        assert pca_health.connected()
    assert not pca_health.connected() and pca_health.records()["sleep"] == {}


def test_growth_is_bounded(pinned, monkeypatch):
    monkeypatch.setattr(pca_health, "CAP", {**pca_health.CAP, "steps": 30})
    for chunk in range(4):
        pca_health.ingest({"steps": [{"date": (D - timedelta(days=chunk * 25 + i)).isoformat(), "count": 1} for i in range(25)]})
    steps = pca_health.records()["steps"]
    assert len(steps) == 30 and min(r["date"] for r in steps.values()) == (D - timedelta(days=29)).isoformat()
    pca_health.ingest({"steps": [{"date": (D - timedelta(days=5)).isoformat(), "count": 2}]})
    pinned["now"] += timedelta(days=60)                                                  # 400-day window moves on too
    monkeypatch.setattr(pca_health, "KEEP_DAYS", 30)
    pca_health.ingest({"steps": []})
    assert all(r["date"] >= (pinned["now"].date() - timedelta(days=30)).isoformat() for r in pca_health.records()["steps"].values())


def test_final_marks_the_sync_and_counts_add_up_over_chunks():
    pca_health.ingest({"sleep": [night(D, 7)], "final": False})
    assert pca_health.last_sync() is None
    pca_health.ingest({"steps": [{"date": D.isoformat(), "count": 10}], "final": True})
    s = pca_health.page_status()
    assert s["connected"] and s["counts"] == {"sleep": 1, "steps": 1} and s["garmin_found"] is None   # the phone did not say


# ---- the old data["garmin"] shape ---------------------------------------------------------------------------------------
def test_garmin_view_has_the_old_shape():
    pca_health.ingest(payload(days=10))
    g = pca_health.garmin_view()
    assert set(g) >= {"runs", "sleep", "hrv", "daily", "vo2max", "synced", "source"} and g["source"] == "health_connect"
    r = g["runs"][-1]
    assert set(r) >= {"id", "date", "name", "type", "miles", "seconds", "pace_s_per_mi", "avg_hr", "max_hr"}
    assert r["pace_s_per_mi"] == int((28 * 60 + 10) / 2.6) and r["name"] == "Springfield Running"
    s = g["sleep"][-1]
    assert s["date"] == D.isoformat() and s["hours"] == 7.4 and s["score"] is None and s["deep_h"] and s["rem_h"]
    h = g["hrv"][-1]
    assert h["last_night"] == 40 and h["status"] is None and h["weekly"] == round(sum(40 + i for i in range(7)) / 7, 1)
    d = g["daily"][-1]
    assert d["resting_hr"] == 52 and d["steps"] == 9000 and d["body_battery"] is None and d["readiness"] is None
    assert g["vo2max"] == 49.0


def test_merge_with_old_sync_health_connect_measures_old_keeps_its_scores(pinned):
    data = fitness._data()
    data["garmin"] = {"synced": (MON - timedelta(days=1)).isoformat(timespec="minutes"),
                      "sleep": [{"date": "2026-11-01", "hours": 8.0, "score": 81, "deep_h": 1, "rem_h": 2},
                                {"date": D.isoformat(), "hours": 9.9, "score": 90, "deep_h": 1, "rem_h": 2}],
                      "daily": [{"date": "2026-11-01", "resting_hr": 55, "body_battery": 70, "steps": 100, "readiness": 66}],
                      "race_predictions": {"5k": "22:00", "2mile_est": "13:40"}}
    fitness._save(data)
    assert fitness.garmin_data(fitness._data()) == fitness._data()["garmin"]             # nothing from Health Connect yet
    pca_health.ingest(payload(days=3))
    g = fitness.garmin_data(fitness._data())
    by = {s["date"]: s for s in g["sleep"]}
    assert by["2026-11-01"]["score"] == 81                                                # an old-only day stays as it was
    assert by[D.isoformat()]["hours"] == 7.4 and by[D.isoformat()]["score"] == 90         # measured hours, Garmin's own score
    assert g["race_predictions"]["5k"] == "22:00" and g["source"] == "health_connect"
    assert fitness._data()["garmin"]["sleep"][1]["hours"] == 9.9                          # data["garmin"] itself is untouched
    text = fitness.fitness_progress()
    assert "VO2 max 49.0" in text and "Last 2 runs" in text


def test_texts_never_mention_values_garmin_does_not_share():
    pca_health.ingest(payload(days=3))
    text = fitness._readiness(fitness._data())
    for word in ("body battery", "readiness", "score", "HRV"):
        assert word.lower() not in text.lower(), (word, text)


# ---- the effective morning check-in ------------------------------------------------------------------------------------
def test_measured_sleep_becomes_todays_checkin_and_nothing_is_written():
    pca_health.ingest(payload(days=10, last_sleep=5.2))
    data = fitness._data()
    eff = pca_health.effective_checkin(data, D)
    assert eff == {"date": D.isoformat(), "sleep_hours": 5.2, "source": "garmin", "resting_hr": 52}
    assert coach_engine.low_signs(eff) == ["you slept 5.2 hours (from your Garmin)"]
    assert "checkins" not in fitness._data() or fitness._data()["checkins"] == {}
    assert fitness._readiness(data).startswith("From your Garmin: slept 5.2 h.") and "keep today easy" in fitness._readiness(data)


def test_manual_checkin_wins():
    pca_health.ingest(payload(days=10, last_sleep=4.5))
    fitness.log_checkin(sleep_hours=8, energy=4, soreness=1, date=D.isoformat())
    eff = pca_health.effective_checkin(fitness._data(), D)
    assert eff["sleep_hours"] == 8 and "source" not in eff
    assert pca_health.effective_checkins(fitness._data(), D)[D.isoformat()]["sleep_hours"] == 8


def test_resting_hr_rule_uses_the_14_day_median():
    # 10 days at 50-52 before today: median 51; today 58 is 7 above -> a low sign
    pca_health.ingest(payload(days=11, last_sleep=7.5, rhr_today=58))
    eff = pca_health.effective_checkin(fitness._data(), D)
    assert eff["rhr_above"] == 7 and eff["resting_hr"] == 58
    assert coach_engine.low_signs(eff) == ["your resting heart rate is 7 beats above your usual (from your Garmin)"]
    pca_health.ingest({"resting_hr": [{"date": D.isoformat(), "bpm": 57}], "final": True})
    assert "rhr_above" not in pca_health.effective_checkin(fitness._data(), D)            # 6 above: not a sign


def test_resting_hr_needs_five_days_of_history():
    pca_health.ingest(payload(days=4, last_sleep=7.5, rhr_today=70))
    assert "rhr_above" not in pca_health.effective_checkin(fitness._data(), D)


def test_no_sleep_on_file_means_no_derived_checkin():
    pca_health.ingest({"resting_hr": [{"date": D.isoformat(), "bpm": 80}], "final": True})
    assert pca_health.effective_checkin(fitness._data(), D) is None


def test_short_night_changes_a_hard_day_from_your_garmin():
    begin()
    before = pca.page_data()["today"]
    assert before["intensity"] == "high" and not [a for a in before["adjustments"] if a["kind"] == "readiness"]
    pca_health.ingest(payload(days=11, last_sleep=5.1, rhr_today=60))
    after = pca.page_data()["today"]
    reasons = [a["reason"] for a in after["adjustments"] if a["kind"] == "readiness"]
    assert reasons and "from your Garmin" in reasons[0] and "You slept 5.1 hours" in reasons[0], after["adjustments"]
    assert fitness._data().get("checkins", {}) == {}


# ---- runs: offered, never assumed --------------------------------------------------------------------------------------
def test_watch_run_is_offered_and_one_tap_logs_it(pinned):
    begin()
    tue = MON.date() + timedelta(days=1)
    pinned["now"] = datetime.combine(tue, datetime.min.time()) + timedelta(hours=18)
    assert pca.page_data()["today"]["garmin_run"] is None
    pca_health.ingest({"exercise": [run(tue)], "final": True})
    offer = pca.page_data()["today"]["garmin_run"]
    assert offer == {"date": tue.isoformat(), "minutes": 28.2, "distance_mi": 2.6, "pace": "10:50 /mi", "avg_hr": 148,
                     "time": "28:10", "start": offer["start"]}
    assert not [r for r in trainer.events() if r["type"] == "run"]                       # nothing logged by itself
    out = pca.page_action({"action": "run", "minutes": offer["minutes"], "distance_mi": offer["distance_mi"], "rpe": 4,
                           "notes": "Recorded by your Garmin watch"})
    assert out.startswith("Run logged: 28.2 minutes at RPE 4.")
    page = pca.page_data()
    assert page["today"]["garmin_run"] is None                                           # logged: no second offer
    assert [r["distance_mi"] for r in trainer.events() if r["type"] == "run"] == [2.6]


def test_two_mile_trial_is_offered_never_saved(pinned):
    begin()
    sat2 = MON.date() + timedelta(days=12)
    pinned["now"] = datetime.combine(sat2, datetime.min.time()) + timedelta(hours=10)
    pca_health.ingest({"exercise": [run(sat2, 17, 52, 2.01, hh=8)], "final": True})
    today = pca.page_data()["today"]
    assert today["garmin_trial"] == {"date": sat2.isoformat(), "seconds": 17 * 60 + 52, "time": "17:52", "distance_mi": 2.01,
                                     "avg_hr": 148}
    assert not [r for r in trainer.events() if r["type"] == "test"]
    assert pca.page_data()["baselines"]["run"] is None


def test_progress_series_are_labelled_garmin(pinned):
    begin()
    pinned["now"] = MON + timedelta(days=6, hours=12)                                    # Sunday of week 1
    tue, sat = MON.date() + timedelta(days=1), MON.date() + timedelta(days=5)
    pca_health.ingest({"exercise": [run(tue), run(sat, 40, 0, 3.6)], "sleep": [night(sat, 7)],
                       "resting_hr": [{"date": sat.isoformat(), "bpm": 51}], "final": True})
    pca.page_action({"action": "run", "minutes": 40, "rpe": 3, "date": sat.isoformat()})     # Saturday logged by hand, no distance
    w = pca.page_data()["history"]["watch"]
    assert [p["source"] for p in w["easy_pace"]] == ["garmin", "garmin"]                    # Tue easy + Sat long (no logged pace)
    assert w["easy_pace"][0] == {"date": tue.isoformat(), "value": round((28 * 60 + 10) / 2.6), "source": "garmin"}
    assert {p["source"] for p in w["avg_hr"] + w["resting_hr"] + w["sleep"]} == {"garmin"}
    week1 = pca.page_data()["history"]["weeks"][0]
    assert week1["run_min_watch"] == 28                                                  # Tue only: Sat has a manual log


# ---- the Link poll and the routes --------------------------------------------------------------------------------------
def test_health_flag_morning_window_requests_and_workouts(pinned):
    assert pca_health.sync_wanted()                                                      # 07:00, never synced
    pinned["now"] = MON - timedelta(hours=2)                                             # 05:00: synced
    pca_health.ingest({"steps": [], "final": True})
    assert not pca_health.sync_wanted()
    pinned["now"] = MON + timedelta(hours=1, minutes=1)                                  # 08:01: 3 h old, in the window
    assert pca_health.sync_wanted()
    pinned["now"] = MON + timedelta(hours=3, minutes=1)                                  # 10:01: outside the window
    assert not pca_health.sync_wanted()
    pinned["now"] = MON + timedelta(hours=7)                                             # 14:00
    assert not pca_health.sync_wanted()
    pca_health.request_sync("page")
    assert pca_health.sync_wanted()
    pca_health.ingest({"steps": [], "final": True})
    assert not pca_health.sync_wanted()
    pinned["now"] += timedelta(minutes=1)
    pca_health.request_sync("workout")
    pinned["now"] += timedelta(hours=1, minutes=1)
    assert not pca_health.sync_wanted()                                                  # a request expires after an hour


def test_starting_a_workout_asks_for_a_sync(pinned):
    begin()
    pca_health.ingest({"steps": [], "final": True})
    pinned["now"] = MON + timedelta(hours=5)                                             # 12:00, outside the morning window
    assert not pca_health.sync_wanted()
    assert pca.page_action({"action": "start"})
    assert pca_health.sync_wanted()


def test_cmd_reply_carries_the_health_flag(client, pinned):
    h = pair(client)
    assert client.get("/api/link/cmd", headers=h).json()["health"] is True
    assert client.post("/api/link/health", headers=h, json=payload(days=2)).status_code == 200
    assert client.get("/api/link/cmd", headers=h).json()["health"] is False


def test_page_sync_button(client, pinned):
    assert "pair Jarvis Link" in pca.page_action({"action": "health_sync"})
    pair(client)
    pca_health.ingest({"steps": [], "final": True})
    pinned["now"] = MON + timedelta(hours=6)
    msg = pca.page_action({"action": "health_sync"})
    assert msg.startswith("Asked your phone for a sync.") and pca_health.sync_wanted() and pca_link.is_active()


def test_routes_auth_and_limits(client):
    link, phone = pair(client), pair(client, device="iPhone")
    assert client.post("/api/link/health", json=payload()).status_code == 401
    assert client.post("/api/link/health", headers=phone, json=payload()).status_code == 403
    r = client.post("/api/link/health", headers=link, json=payload(days=10))
    assert r.status_code == 200 and r.json()["stored"]["sleep"] == 10 and r.json()["dropped"] == 0
    assert client.post("/api/link/health", headers=link, content=b"x" * (300 * 1024)).status_code == 413
    assert client.post("/api/link/health", headers=link, content=b"{not json").status_code == 400
    assert client.post("/api/link/health", headers=link, content=b"[" * 50 + b"]" * 50).status_code == 400
    bad = client.post("/api/link/health", headers=link, json=[1])
    assert bad.status_code == 400 and bad.json()["detail"].endswith(".")
    nan = client.post("/api/link/health", headers=link, content=b'{"resting_hr": [{"date": "2026-11-23", "bpm": NaN}]}')
    assert nan.status_code == 200 and nan.json()["dropped"] == 1
    s = client.get("/api/health/status", headers=phone).json()                           # any device of his may read
    assert s["connected"] and s["last_sleep"]["date"] == D.isoformat() and s["newest_run_date"] == (D - timedelta(days=3)).isoformat()
    assert s["resting_hr"]["bpm"] == 52 and s["counts"]["sleep"] == 10 and s["garmin_found"] is True
    assert client.get("/api/health/status").status_code == 401


def test_status_shows_only_what_was_sent():
    s = pca_health.page_status()
    assert s["connected"] is False and s["last_sleep"] is None and s["last_run"] is None and s["vo2max"] is None
    pca_health.ingest({"sleep": [night(D, 7)], "final": True, "garmin_found": False})
    s = pca_health.page_status()
    assert s["garmin_found"] is False and s["resting_hr"] is None and s["weight_lb"] is None and s["steps"] is None


# ---- skills -----------------------------------------------------------------------------------------------------------
def test_skills_are_registered_and_honest():
    assert {"health_status", "last_sleep"} <= set(registry.names())
    assert "not connected" in pca_health.health_status() and "Android 14" in pca_health.health_status()
    assert "No sleep is on file" in pca_health.last_sleep()
    pca_health.ingest(payload(days=5, last_sleep=5.5))
    said = pca_health.last_sleep()
    assert said.startswith("Your watch recorded 5 hours 30 minutes of sleep for the night ending 2026-11-23.")
    assert "under 6 hours" in said and "score" not in said
    assert "last reached Jarvis just now" in pca_health.health_status()


@pytest.mark.skipif(__import__("importlib").util.find_spec("garminconnect") is None, reason="optional garminconnect package not installed")
def test_fitness_messages_point_to_jarvis_link_first():
    with pytest.raises(RuntimeError) as e:
        fitness._garmin()
    text = str(e.value)
    assert text.index("Jarvis Link") < text.index("garmin-login.bat")


# ---- review round 1 ----------------------------------------------------------------------------------------------------
def s(start: str, end: str, at=0.0, **extra) -> dict:
    """A stored sleep record: 'YYYY-MM-DDTHH:MM' local wall times with an offset (EST unless given)."""
    tz = lambda t: t if len(t) > 16 else t + ":00" + TZ          # noqa: E731
    return {"start": tz(start), "end": tz(end), "at": at, **extra}


NOW = datetime.fromisoformat("2026-11-23T09:00:00" + TZ)
NIGHTS = [  # (what, sessions, date, now, hours or None, usable)
    ("a revised record is not double counted", [s("2026-11-22T22:00", "2026-11-23T06:00"), s("2026-11-22T22:20", "2026-11-23T06:10")],
     "2026-11-23", NOW, 8.2, True),
    ("a split night adds both parts, not the gap", [s("2026-11-22T21:00", "2026-11-22T23:30"), s("2026-11-23T00:10", "2026-11-23T06:00")],
     "2026-11-23", NOW, 8.3, True),
    ("a nap is not night sleep", [s("2026-11-22T23:00", "2026-11-23T06:30"), s("2026-11-23T10:30", "2026-11-23T11:30")],
     "2026-11-23", NOW.replace(hour=13), 7.5, True),
    ("a nap alone is never last night", [s("2026-11-23T13:00", "2026-11-23T14:30")], "2026-11-23", NOW.replace(hour=18), None, False),
    ("synced at 03:05 while still asleep", [s("2026-11-22T23:00", "2026-11-23T03:05")], "2026-11-23",
     datetime.fromisoformat("2026-11-23T03:50:00" + TZ), 4.1, False),
    ("still in progress (ended under 45 min ago)", [s("2026-11-22T23:00", "2026-11-23T06:00")], "2026-11-23",
     datetime.fromisoformat("2026-11-23T06:30:00" + TZ), 7.0, False),
    ("45 minutes after waking it counts", [s("2026-11-22T23:00", "2026-11-23T06:00")], "2026-11-23",
     datetime.fromisoformat("2026-11-23T06:45:00" + TZ), 7.0, True),
    ("under 3 hours is a failed recording", [s("2026-11-23T02:00", "2026-11-23T04:30")], "2026-11-23", NOW, 2.5, False),
    ("DST fall back: 9 real hours", [s("2026-10-31T22:00:00-04:00", "2026-11-01T06:00:00-05:00")], "2026-11-01",
     datetime.fromisoformat("2026-11-01T09:00:00-05:00"), 9.0, True),
    ("DST spring forward: 7 real hours", [s("2026-03-07T22:00:00-05:00", "2026-03-08T06:00:00-04:00")], "2026-03-08",
     datetime.fromisoformat("2026-03-08T09:00:00-04:00"), 7.0, True),
    ("a shift worker's day sleep is his main sleep when no night sleep exists", [s("2026-11-23T08:00", "2026-11-23T15:00")],
     "2026-11-23", NOW.replace(hour=16), 7.0, True),
    ("but not when there is a night interval", [s("2026-11-23T00:00", "2026-11-23T02:00"), s("2026-11-23T08:00", "2026-11-23T15:00")],
     "2026-11-23", NOW.replace(hour=16), 2.0, False),
    ("waking briefly and sleeping on joins the night (gap under 90 min)",
     [s("2026-11-22T23:00", "2026-11-23T05:30"), s("2026-11-23T06:15", "2026-11-23T08:00")], "2026-11-23", NOW, 8.2, True),
]


@pytest.mark.parametrize("what,sessions,day,now,hours,usable", NIGHTS, ids=[n[0] for n in NIGHTS])
def test_night_sleep(what, sessions, day, now, hours, usable):
    n = pca_health.night_sleep(date.fromisoformat(day), sessions, now)
    if hours is None:
        assert n is None or n["hours"] == 0
        return
    assert round(n["hours"], 1) == hours and n["usable"] is usable, n


def test_naps_are_kept_apart_and_never_added():
    rows = [s("2026-11-22T23:00", "2026-11-23T06:30"), s("2026-11-23T13:00", "2026-11-23T14:00")]
    n = pca_health.night_sleep(date(2026, 11, 23), rows, NOW.replace(hour=18))
    assert n["hours"] == 7.5 and [round(x["hours"], 1) for x in n["naps"]] == [1.0]


def test_a_revision_in_a_later_post_replaces_the_old_record(pinned):
    pca_health.ingest({"sleep": [night(D, 8.0)], "final": True})
    revised = night(D, 7.0)
    revised["end"] = iso(D, 6, 10)
    pinned["now"] += timedelta(minutes=30)
    pca_health.ingest({"sleep": [revised], "final": True})
    assert len(pca_health.records()["sleep"]) == 1
    assert pca_health.garmin_view()["sleep"][-1]["hours"] == round(7 + 10 / 60, 2)


def test_early_sync_then_later_sync_extends_the_night(pinned):
    pinned["now"] = MON.replace(hour=3, minute=10)
    pca_health.ingest({"sleep": [s("2026-11-22T23:00", "2026-11-23T03:05")], "final": True})
    assert pca_health.effective_checkin(fitness._data(), D) is None                     # still asleep: nothing claimed
    pinned["now"] = MON.replace(hour=7, minute=30)
    pca_health.ingest({"sleep": [s("2026-11-22T23:00", "2026-11-23T06:30")], "final": True})
    assert pca_health.effective_checkin(fitness._data(), D)["sleep_hours"] == 7.5


def test_a_failed_short_recording_is_never_claimed():
    pca_health.ingest({"sleep": [s("2026-11-23T03:00", "2026-11-23T05:00")], "final": True})
    assert pca_health.effective_checkin(fitness._data(), D) is None
    assert pca_health.garmin_view()["sleep"] == [] and "2 hours" not in pca_health.last_sleep()


NUMERIC = [("sleep", "minutes"), ("sleep", "deep_min"), ("resting_hr", "bpm"), ("hr_daily", "min"), ("hrv", "rmssd_ms"),
           ("steps", "count"), ("calories", "kcal"), ("exercise", "distance_m"), ("exercise", "avg_hr"), ("exercise", "kcal"),
           ("exercise", "avg_speed_mps"), ("weight", "kg"), ("vo2max", "value")]


@pytest.mark.parametrize("kind,field", NUMERIC)
@pytest.mark.parametrize("value", [10 ** 400, -(10 ** 400), 10 ** 20], ids=["1e400", "-1e400", "1e20"])
def test_huge_numbers_are_dropped_never_a_crash(kind, field, value):
    base = {"sleep": night(D, 7), "resting_hr": {"date": D.isoformat(), "bpm": 50},
            "hr_daily": {"date": D.isoformat(), "min": 40, "avg": 60, "max": 150}, "hrv": {"date": D.isoformat(), "rmssd_ms": 40},
            "steps": {"date": D.isoformat(), "count": 10}, "calories": {"date": D.isoformat(), "kcal": 10}, "exercise": run(D - timedelta(days=1)),
            "weight": {"time": iso(D, 7), "kg": 80}, "vo2max": {"time": iso(D, 7), "value": 50}}[kind]
    clean, dropped = pca_health.clean_payload({kind: [dict(base, **{field: value})]})
    rec = clean[kind][0] if clean[kind] else {}
    assert dropped == 1 or all(abs(v) < 1e7 for v in rec.values() if isinstance(v, (int, float)))


def test_the_route_never_answers_500(client, monkeypatch):
    h = pair(client)
    big = b'{"resting_hr": [{"date": "2026-11-23", "bpm": 1' + b"0" * 400 + b'}]}'
    r = client.post("/api/link/health", headers=h, content=big)
    assert r.status_code == 200 and r.json()["dropped"] == 1
    monkeypatch.setattr(pca_health, "ingest", lambda body: 1 / 0)
    r = client.post("/api/link/health", headers=h, json={"steps": []})
    assert r.status_code == 400 and r.json()["detail"].endswith(".")


MAIN_TEXT = "Recovery looks low: keep today easy (cut volume about 30%, no max efforts)."
LEGACY = [  # old direct-sync data only -> the exact sentence main gave
    ({"sleep": [{"date": D.isoformat(), "hours": 4.2, "score": 55}], "hrv": [{"date": D.isoformat(), "status": "LOW"}],
      "daily": [{"date": D.isoformat(), "readiness": 35, "body_battery": 30}]},
     "Garmin: slept 4.2 h (score 55), HRV low, training readiness 35, body battery 30. " + MAIN_TEXT),
    ({"sleep": [{"date": D.isoformat(), "hours": 7.6, "score": 82}], "hrv": [{"date": D.isoformat(), "status": "BALANCED"}],
      "daily": [{"date": D.isoformat(), "readiness": 78, "body_battery": 65}]},
     "Garmin: slept 7.6 h (score 82), HRV balanced, training readiness 78, body battery 65."),
    ({"sleep": [{"date": D.isoformat(), "hours": 8.0}]}, "Garmin: slept 8.0 h."),
    ({"daily": [{"date": D.isoformat(), "readiness": 30}]}, "Garmin: training readiness 30. " + MAIN_TEXT),
]


@pytest.mark.parametrize("old,expected", LEGACY)
def test_legacy_garmin_text_is_unchanged_and_never_an_automatic_checkin(old, expected):
    data = fitness._data()
    data["garmin"] = dict(old, synced="2026-11-23T05:00")
    fitness._save(data)
    assert fitness._readiness(fitness._data()) == expected
    assert pca_health.effective_checkin(fitness._data(), D) is None
    assert pca_health.effective_checkins(fitness._data(), D) == {}


def test_both_sources_measured_sleep_plus_old_proprietary_signals():
    data = fitness._data()
    data["garmin"] = {"synced": "2026-11-23T05:00", "sleep": [{"date": D.isoformat(), "hours": 9.0, "score": 70}],
                      "hrv": [{"date": D.isoformat(), "status": "LOW"}],
                      "daily": [{"date": D.isoformat(), "readiness": 35, "body_battery": 30, "resting_hr": 49}]}
    fitness._save(data)
    pca_health.ingest(payload(days=10, last_sleep=7.4))
    text = fitness._readiness(fitness._data())
    assert text == "From your Garmin: slept 7.4 h, HRV low, training readiness 35, body battery 30. " + MAIN_TEXT
    g = fitness.garmin_data(fitness._data())
    today = next(r for r in g["daily"] if r["date"] == D.isoformat())
    assert today["resting_hr"] == 52 and today["readiness"] == 35 and today["body_battery"] == 30


def test_resting_hr_rule_needs_seven_prior_days():
    pca_health.ingest(payload(days=7, last_sleep=7.5, rhr_today=70))                    # 6 prior days
    assert "rhr_above" not in pca_health.effective_checkin(fitness._data(), D)
    pca_health.ingest(payload(days=8, last_sleep=7.5, rhr_today=70))                    # 7 prior days
    assert pca_health.effective_checkin(fitness._data(), D)["rhr_above"] >= 7


def test_the_sync_flag_clears_after_ten_minutes(pinned):
    pinned["now"] = MON + timedelta(hours=7)                                            # 14:00
    pca_health.ingest({"steps": [], "final": True})
    pca_health.request_sync("page")
    pinned["now"] += timedelta(minutes=1)
    pca_health.ingest({"steps": [], "final": False})                                    # the phone started, never finished
    assert not pca_health.sync_wanted()
    pinned["now"] += timedelta(minutes=10)
    assert not pca_health.sync_wanted()                                                 # cleared, not held for an hour
