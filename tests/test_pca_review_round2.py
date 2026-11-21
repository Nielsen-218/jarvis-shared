"""Project Captain America: regressions for the round-2 review defects (2026-11-20). Sandboxed storage, a pinned clock."""
from datetime import datetime, timedelta

import pytest

from core import storage
from tools import fitness, pca, trainer

MON = datetime(2026, 11, 23, 7, 0)
ANSWERS = {"height": "6'2\"", "weight_lbs": 195, "age": 24, "days_per_week": 5, "session_minutes": 60,
           "equipment": "barbell_rack,dumbbells,cables,pullup_bar,dip_bars,sled,kettlebells,track,box",
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


def _onboard():
    for qid, value in ANSWERS.items():
        assert pca.answer(qid, value).startswith("Saved"), qid


# 1 -------------------------------------------------------------------------------------------------------------------
def test_a_running_program_is_never_silently_replaced(pinned):
    _onboard()
    pca.begin()
    at(pinned, 2, 1)
    for out in (trainer.build_plan(), trainer.set_goal("fat loss", 4), trainer.page_action({"action": "plan"}),
                trainer.page_action({"action": "goal", "goal": "muscle", "days_per_week": 4})):
        assert out.startswith("Project Captain America is running (week 2)") and "end Captain America" in out, out
    assert fitness._data()["plan"]["program"].startswith("pca-")
    assert fitness.set_fitness_profile("weight_lbs", "196").startswith("Saved")


def test_a_program_waiting_to_start_also_refuses_a_new_plan(pinned):
    _onboard()
    at(pinned, 1, 4)
    pca.begin()
    assert "starts Monday 2026-11-30" in trainer.build_plan()


def test_end_program_archives_it_and_keeps_every_log(pinned):
    trainer.set_goal("build muscle", 4, 60, "gym", 24)
    _onboard()
    pca.begin()
    pca.log_run_text(20, 4)
    pca.log_test("hrp", "40")
    out = pca.page_action({"action": "end_program"})
    assert out.startswith("Project Captain America ended"), out
    data = fitness._data()
    assert not (data.get("plan") or {}).get("weeks")
    assert [h.get("program") for h in data["plan_history"]][-1].startswith("pca-")
    assert any(h.get("goal") == "muscle" for h in data["plan_history"])
    rows = trainer.events()
    assert {"run", "test"} <= {r["type"] for r in rows} and any(r["type"] == "pca" and r.get("kind") == "end" for r in rows)
    assert pca.page_data()["status"] == "ready"
    assert pca.end_program() == "Project Captain America is not running."
    assert "captain_end" in __import__("tools.registry", fromlist=["names"]).names()
    assert trainer.build_plan().startswith("Your plan")                             # now a new plan is allowed


# 2 -------------------------------------------------------------------------------------------------------------------
PRE = "Project Captain America starts Monday 2026-11-30. Until then: rest, or an easy 20 minute walk or jog if you feel like it. " \
      "First session: lower-body strength and power."


def test_before_the_start_everything_says_the_same_true_thing(pinned):
    _onboard()
    at(pinned, 1, 4)                                          # a Friday
    pca.begin()
    assert fitness.today_workout() == PRE
    assert trainer.start() == PRE
    assert pca.captain_status() == PRE
    today = pca.page_data()["today"]
    assert today["status"] == "before" and today["title"] == "Starts Monday 2026-11-30" and today["briefing"] == PRE
    pre = today["prestart"]
    assert pre["start"] == "2026-11-30" and pre["days_to_go"] == 3
    assert pre["first_session"]["title"] == "Lower-body strength and power" and pre["first_session"]["minutes"] > 0
    assert pre["first_session"]["items"] and "sleep 7-9 h" in pre["checklist"] and "eat normally" in pre["checklist"]
    assert any("trap" in c.lower() for c in pre["checklist"])                    # his answers have no trap bar


# 3 -------------------------------------------------------------------------------------------------------------------
def test_begin_says_so_when_the_log_cannot_be_written(pinned):
    _onboard()
    path = storage.path(f"fitness/events-{trainer._machine()}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert pca.begin().startswith("I could not save that")
    assert not pca._is_pca(fitness._data())


# 4 -------------------------------------------------------------------------------------------------------------------
def test_runs_from_before_the_start_never_feed_the_run_rules(pinned):
    for n, minutes in ((3, 10), (5, 10), (6, 10)):
        trainer.record({"type": "run", "minutes": minutes, "rpe": 4, "kind": "easy", "date": (MON - timedelta(days=n)).date().isoformat()})
    _onboard()
    pca.begin()
    at(pinned, 1, 1)                                          # Tuesday's 12-minute easy run would be capped at 11 by the old runs
    run = next(i for i in trainer.view()["items"] if i.get("run"))
    assert run["reps"] == "12 min" and not [a for a in trainer.view()["adjustments"] if a["kind"] == "run_cap"]


def test_the_page_shows_the_prestart_card_and_an_end_control_behind_a_confirm():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "app" / "captain.html").read_text(encoding="utf-8")
    assert "t.prestart" in html and "ps.checklist" in html and "first_session" in html
    assert 'data-act="end-ask"' in html and 'data-act="end-yes"' in html and 'act("end_program")' in html
    assert "confirm(" not in html                                   # the confirm step is on the page, never a native dialog
