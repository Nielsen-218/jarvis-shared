"""The Captain America page contract (tools/pca.page_data / page_action, as app/captain.html reads it): question groups,
item keys, tests and bodyweight flags, open pain flags, recent check-ins, compliance as a 0-1 share, audio service ids, a
logged run closing the run item, and the page itself (no model, no network; sandboxed storage from tests/conftest.py)."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core import pca_engine as engine
from core import pca_program as program
from tools import fitness, pca, trainer

MON = datetime(2026, 11, 23, 7, 0)
ANSWERS = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
           "equipment": "trap_bar,barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
           "runs_per_week": 1, "miles_per_week": 4, "weeks_since_last_run": 2, "weeks_since_last_heavy_lift": 1, "pain_level": 0,
           "medical_restriction": "no", "sickle_cell": "no", "sleep_hours": 7, "unit_pt_days": 3, "unit_pt_kind": "mixed"}
APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    box = {"now": MON}
    monkeypatch.setattr(trainer, "clock", lambda: box["now"])
    monkeypatch.setattr(trainer, "REST", lambda seconds, line, uid: None)
    return box


def _begin(**over):
    for qid, value in {**ANSWERS, **over}.items():
        assert pca.page_action({"action": "answer", "id": qid, "value": value}).startswith("Saved"), qid
    assert "starts today" in pca.page_action({"action": "begin"})


def test_every_question_has_a_known_group_and_the_screens_are_all_used():
    ids = [g[0] for g in engine.GROUPS]
    page = pca.page_data()
    groups = [q["group"] for q in page["onboarding"]["questions"]]
    assert set(groups) == set(ids)
    music = next(q for q in page["onboarding"]["questions"] if q["id"] == "music_service")
    assert music["options"] == ["none", "spotify", "youtube_music", "metrolist", "apple_music", "local"] and music["labels"]["spotify"] == "Spotify"


def test_before_the_start_only_onboarding_is_filled():
    page = pca.page_data()
    assert page["status"] == "onboarding"
    assert page["program"] is None and page["today"] is None and page["baselines"] is None and page["history"] is None
    assert page["pain"] == [] and page["checkins"] == []
    assert page["historical"]["values"]["mdl"] and "date unknown" in page["historical"]["label"]   # shown on the assessment too
    assert "date unknown" in engine.HISTORICAL["label"] and "not confirmed" in engine.HISTORICAL["label"]


def test_items_carry_key_test_and_bodyweight():
    _begin()
    page = pca.page_data()
    week1 = page["program"]["weeks"][0]["days"]
    items = [i for d in week1 for i in d["items"]]
    assert all("key" in i and "test" in i and "bodyweight" in i for i in items)
    tests = {i["test"] for i in items if i["kind"] == "test"}
    assert tests >= {"hrp", "plk"} and None not in tests
    deadlift = next(i for i in week1[0]["items"] if i["key"] == "deadlift")
    assert deadlift["bodyweight"] is False and deadlift["test"] is None
    today = page["today"]["items"]
    assert all("bodyweight" in i and "test" in i for i in today)
    assert next(i for i in today if i["key"] == "deadlift")["bodyweight"] is False
    assert set(program.BODYWEIGHT) >= {"hand_release_pushup", "pull_up"}


def test_open_pain_flags_and_checkins_are_on_the_page(pinned):
    _begin()
    pca.page_action({"action": "pain", "area": "knee", "level": 5})
    pinned["now"] = MON + timedelta(days=2)
    fitness.log_checkin(5, 3, 2, date=pinned["now"].date().isoformat())
    page = pca.page_data()
    assert page["pain"] == [{"area": "knee", "level": 5, "since": "2026-11-23", "days_open": 2, "note": ""}]
    assert page["checkins"][-1]["date"] == "2026-11-25" and page["checkins"][-1]["sleep_hours"] == 5
    pca.page_action({"action": "pain_clear", "area": "knee"})
    assert pca.page_data()["pain"] == []


def test_compliance_is_a_share_and_a_logged_run_closes_the_run_item(pinned):
    _begin()
    pinned["now"] = MON + timedelta(days=1)                              # Tuesday: upper body, then an easy run
    assert pca.page_data()["history"]["compliance"] == 0.0               # Monday was due and nothing was logged
    pca.page_action({"action": "start"})
    day = pca.page_data()["today"]
    run_at = next(n for n, i in enumerate(day["items"]) if i.get("run"))
    for n, it in enumerate(day["items"][:run_at]):
        pca.page_action({"action": "skip"})
    assert pca.page_data()["today"]["session"]["current"] == run_at
    out = pca.page_action({"action": "run", "minutes": 15, "rpe": 3})
    assert out.startswith("Run logged")
    after = pca.page_data()["today"]
    assert after["items"][run_at]["done"] and after["session"]["current"] != run_at


def test_audio_service_is_stored_as_an_id():
    for said, want in (("YouTube Music", "youtube_music"), ("Apple Music", "apple_music"), ("local files", "local"),
                       ("spotify", "spotify"), ("Metrolist", "metrolist"), ("metrolist", "metrolist"), ("a tape deck", "none")):
        pca.page_action({"action": "audio", "prefs": {"service": said}})
        assert pca.page_data()["audio"]["prefs"]["service"] == want, said


def test_the_page_reads_the_backend_answers_instead_of_guessing():
    html = (APP / "captain.html").read_text(encoding="utf-8")
    assert "<title>Project Captain America</title>" in html
    assert "q.group" in html and "it.bodyweight" in html and "it.test" in html
    for guess in ("const BODYWEIGHT", "EV_PAT", "normService", "exByName"):
        assert guess not in html, guess
    assert "captain-mock.js" in html and "PCA_MOCK" in html                 # the fixture loads only with ?mock=
    assert '/captain.html' in (APP / "index.html").read_text(encoding="utf-8")
    assert '"/captain.html"' in (APP / "sw.js").read_text(encoding="utf-8")


def test_run_segments_carry_effort_and_once_tested_their_pace_and_split(pinned):
    _begin()
    pinned["now"] = MON + timedelta(days=2)                              # week 1 Wednesday: no two-mile test yet
    seg = next(i for i in pca.page_data()["today"]["items"] if i.get("run"))["run"]["segments"][0]
    assert seg["rpe"] and seg["talk"] and "pace" not in seg and "split" not in seg
    pca.page_action({"action": "test", "event": "run", "value": "17:58"})
    pinned["now"] = MON + timedelta(days=7 * 4 + 2)                      # week 5 Wednesday: 6 x 400 m at two-mile pace
    for back in range(1, 15):                                            # keep the log recent, so no re-entry week changes the day
        day = (pinned["now"] - timedelta(days=back)).date().isoformat()
        trainer.record({"type": "run", "minutes": 20, "rpe": 3, "kind": "easy", "date": day})
    pca.page_action({"action": "test", "event": "run", "value": "17:58"})
    items = pca.page_data()["today"]["items"]
    reps400 = next(s for i in items if i.get("run") for s in i["run"]["segments"] if s.get("m") == 400)
    assert reps400["pace"].endswith("/mi") and reps400["split"].startswith("400 m in ")


def test_a_pain_swap_reads_as_one_sentence():
    day = {"status": "train", "title": "Intervals", "items": [
        {"exercise": "Intervals: 6 x 400 m", "key": "run", "kind": "cardio", "sets": "1", "run": {"type": "intervals", "segments": [], "total_min": 39}},
        {"exercise": "Box jump", "key": "box_jump", "kind": "power", "sets": "3"}], "adjustments": []}
    out = engine.adapt(day, {"today": "2026-12-23", "week": 5, "pain": [{"area": "knee", "level": 5, "since": "2026-12-23"}]})
    reason = next(a["reason"] for a in out["adjustments"] if a["kind"] == "pain")
    assert "running becomes the bike or rower" in reason and "rower is out" not in reason and "rower are out" not in reason
