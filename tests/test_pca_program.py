"""Project Captain America, the authored 12-week program (core/pca_program): it passes its own structural review in every
layout, every prescription is complete, the tests sit where the program says, and nothing comes from old results."""
import json
from datetime import date

import pytest

from core import pca_program as program

MON = date(2026, 11, 23)
LAYOUTS = [{"days_per_week": 5}, {"days_per_week": 6}, {"days_per_week": 5, "running_now": True},
           {"days_per_week": 5, "session_minutes": 75}, {"days_per_week": 6, "session_minutes": 75, "running_now": True}]
ITEM_FIELDS = ("exercise", "key", "kind", "sets", "reps", "target", "progression", "purpose", "equipment", "alternatives", "priority")
DAY_FIELDS = ("day", "title", "session", "intensity", "optional", "why", "stimulus", "warmup", "cooldown", "minutes", "trimmed", "notes", "items")


@pytest.mark.parametrize("answers", LAYOUTS)
def test_every_layout_passes_its_own_review(answers):
    plan = program.build(answers, MON)
    assert program.verify(plan) == []
    assert plan["program"] == program.PROGRAM_ID and len(plan["weeks"]) == 12


@pytest.mark.parametrize("answers", LAYOUTS)
def test_every_day_and_item_carries_the_required_fields(answers):
    plan = program.build(answers, MON)
    for wk in plan["weeks"]:
        for d in wk["days"]:
            for f in DAY_FIELDS:
                assert f in d, (wk["week"], d["day"], f)
            assert d["intensity"] in ("high", "moderate", "low")
            for it in d["items"]:
                for f in ITEM_FIELDS:
                    assert it.get(f) not in (None, "", []), (wk["week"], d["day"], it.get("exercise"), f)
                assert it["kind"] in ("lift", "hold", "power", "carry", "cardio", "test")
                if it["kind"] == "lift":
                    assert it.get("cues") and it.get("rir") is not None
                if it["kind"] == "test":
                    assert it["test"] in ("mdl", "hrp", "sdc", "plk", "run")
                if it.get("run"):
                    assert {"type", "purpose", "segments", "total_min", "hard_min"} <= set(it["run"])


@pytest.mark.parametrize("answers, trial", [({"days_per_week": 5}, 2), ({"days_per_week": 5, "running_now": True}, 1)])
def test_tests_sit_in_weeks_1_or_2_then_4_8_and_12(answers, trial):
    plan = program.build(answers, MON)
    tests = {}
    for wk in plan["weeks"]:
        for d in wk["days"]:
            for it in d["items"]:
                if it["kind"] == "test":
                    tests.setdefault(wk["week"], set()).add(it["test"])
    assert {"hrp", "plk"} <= tests[1] and "run" in tests[trial]
    assert "sdc" in tests[4]
    assert {"hrp", "plk", "sdc", "run"} <= tests[8]
    assert tests[12] == {"mdl", "hrp", "sdc", "plk", "run"}


@pytest.mark.parametrize("answers", LAYOUTS)
def test_no_number_in_the_built_plan_comes_from_the_old_results(answers):
    text = json.dumps(program.build(answers, MON))
    for old in ("340", "18:50", "16:37", "1:43", "1:52", "41-43"):
        assert old not in text, old


def test_build_is_pure():
    assert program.build({"days_per_week": 6}, MON) == program.build({"days_per_week": 6}, MON)


def test_summary_counts_every_week():
    rows = program.summary(program.build({"days_per_week": 5}, MON))
    assert [r["week"] for r in rows] == list(range(1, 13))
    assert rows[3]["lift_sets"] < rows[2]["lift_sets"] and rows[7]["lift_sets"] < rows[6]["lift_sets"]   # deload weeks are lighter
