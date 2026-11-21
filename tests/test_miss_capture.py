"""A failed live request is written down ONCE (core.misses), whichever route it took, and a success or a chat line is not.
All inputs are synthetic sentences; rows are the production format (core.misses rows in data/learning/live-misses.jsonl)."""
import json
import time

import pytest

from core import gateway, misses, pcfast, pchybrid, storage


def step(tool, **args):
    return {"tool": tool, "arguments": args}


def rows():
    return misses.rows(days=1, limit=50, include_recovered=True)


@pytest.fixture
def pc(monkeypatch):
    from core import quick
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast.pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(gateway, "_MISSED", {})
    gateway._CHOICES.clear()


def decision(*intents, covers=True):
    return gateway.Decision(list(intents), "advanced", covers=covers)


def run_with(state, detail):
    def run(task, cancelled=None, *, plan=None, route=None, observed=(), on_start=None):
        return pchybrid.result(state, detail, route=route or "gateway")
    return run


def brain():
    from brain import llm
    return llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")


def ask(b, text):
    heard = []
    b.ask(text, heard.append)
    return " ".join(heard)


# ------------------------------------------------------------------------------------------------- the choke point
def test_two_exits_reporting_one_request_write_one_row(monkeypatch):
    assert gateway.record_miss("[Sent from their Jarvis phone app] close the foo window", "step not verified: x") is None
    misses.record("close the foo window.", "part of the request was not understood", None, "pc")
    got = rows()
    assert len(got) == 1 and got[0]["said"] == "close the foo window" and got[0]["key"]


def test_a_second_failure_long_after_is_a_second_miss(monkeypatch):
    misses.record("close the foo window", "step not verified: x")
    monkeypatch.setattr(time, "time", lambda t=time.time(): t + misses.DEDUP_SECONDS + 5)
    misses.record("close the foo window", "step not verified: x")
    assert len(rows()) == 2


def test_the_turn_sentence_is_not_a_new_place_for_his_words():
    with misses.turn("do the foo thing"):
        misses.fail("step not verified: x", "skill")
    assert list(storage.DATA.rglob("*.jsonl")) == [storage.DATA / "learning" / "live-misses.jsonl"]


def test_a_failure_outside_an_owner_turn_writes_nothing():
    assert misses.fail("step not verified: x") is False and rows() == []


# ------------------------------------------------------------------------------------------------- route: gateway plan (voice and phone)
def test_a_gateway_plan_that_stops_is_exactly_one_miss(monkeypatch, pc):
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: decision(
        gateway.Intent("actions", "open foo", steps=[step("open_app", app="foo")])))
    monkeypatch.setattr(pchybrid, "run", run_with("stopped", "Foo did not open."))
    ask(brain(), "open the foo program")
    got = rows()
    assert len(got) == 1 and got[0]["reason"].startswith("step not verified")


def test_a_gateway_plan_that_works_is_no_miss(monkeypatch, pc):
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: decision(
        gateway.Intent("actions", "open foo", steps=[step("open_app", app="foo")])))
    monkeypatch.setattr(pchybrid, "run", run_with("completed", "Opened Foo."))
    assert "Opened Foo." in ask(brain(), "open the foo program")
    assert rows() == []


@pytest.mark.parametrize("line", ["tell me a joke", "what is the capital of France?"])
def test_a_chat_line_or_a_question_is_never_a_miss(monkeypatch, pc, line):
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: decision(gateway.Intent("chat", "talk")))
    monkeypatch.setattr(pchybrid, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    b = brain()
    monkeypatch.setattr(type(b), "_ask", lambda self, text, on: None)    # the model's answer is not under test
    b.ask(line, lambda s: None)
    assert rows() == []


def test_a_plan_nobody_could_make_is_one_miss(monkeypatch, pc):
    from core import pcsemantic
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: [])
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: gateway.Decision([], "none", covers=False))
    monkeypatch.setattr(pchybrid, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    ask(brain(), "frobnicate the quux")
    assert len(rows()) == 1


def test_the_planner_gave_up_then_the_fallback_plan_stopped_is_one_miss(monkeypatch, pc):
    from core import pchelp
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: gateway.Decision([], "none", covers=False))
    monkeypatch.setattr(pchelp, "next_plan", lambda said: ("open foo", {"steps": [step("open_app", app="foo")]}))
    monkeypatch.setattr(pchybrid, "run", run_with("stopped", "Foo did not open."))
    ask(brain(), "get foo going please")
    assert len(rows()) == 1


# ------------------------------------------------------------------------------------------------- route: "which one?" answer
def test_a_chosen_candidate_that_fails_is_a_miss(monkeypatch, pc):
    monkeypatch.setattr(gateway, "choose", lambda uid, raw: {"task": "launch the zed game", "plan": {"steps": [step("open_path", path="C:\\z")]}, "observed": []})
    monkeypatch.setattr(pchybrid, "run", run_with("stopped", "Could not open that."))
    ask(brain(), "the first one")
    got = rows()
    assert len(got) == 1 and got[0]["said"] == "launch the zed game"      # the request, not the answer word


# ------------------------------------------------------------------------------------------------- route: legacy planner (gateway off)
def test_the_legacy_route_records_a_stopped_run_and_a_success_recovers_it(monkeypatch, pc):
    monkeypatch.setenv("JARVIS_GATEWAY", "off")
    from tools import pcview
    monkeypatch.setattr(pchybrid, "is_request", lambda raw: True)
    outcome = {"state": "stopped", "detail": "Foo did not open."}
    monkeypatch.setattr(pcview, "control_pc", lambda task: [{"type": "text", "text": pchybrid.result(outcome["state"], outcome["detail"], route="local_ai")["detail"]}])
    pcfast.handle("open the foo program")
    assert len(rows()) == 1 and not rows()[0].get("recovered_at")
    outcome.update(state="completed", detail="Opened Foo.")
    pcfast.handle("open the foo program")
    got = rows()
    assert len(got) == 1 and got[0]["recovered_at"] and misses.rows(days=1) == []      # kept, but no longer open


# ------------------------------------------------------------------------------------------------- route: a learned skill
class FakeBook:
    title = "show the thing in {dir}"
    subject = "pc_show_thing"
    params = ["dir"]
    tier = "T0"
    steps = []


def fake_skills(result):
    class Playbooks:
        def run(self, book, guard, run_fn=None, params=None, outputs=None):
            from types import SimpleNamespace
            if outputs is not None:
                outputs.append("fine")
            return SimpleNamespace(result=result, render=lambda: "boom")
    return type("S", (), {"playbooks": Playbooks(), "_guard": lambda self: None, "run_fn": None})()


@pytest.mark.parametrize("result,expected", [("FAIL", 1), ("PASS", 0)])
def test_a_parameterized_skill_counts_its_use_and_only_a_failure_is_a_miss(monkeypatch, result, expected):
    from core import independence
    from tools import pcskills
    monkeypatch.setattr(pcskills, "skills", lambda: fake_skills(result))
    monkeypatch.setattr(gateway, "_filled_tier", lambda book, values: 0)
    card = type("C", (), {"id": "pc_show_thing", "book": FakeBook()})()
    from core import gateway_context
    monkeypatch.setattr(gateway_context, "skill_candidates", lambda *a, **k: [card])
    it = gateway.Intent("skill", "show the thing", skill="pc_show_thing", values={"dir": "C:\\x"})
    with misses.turn("show me the thing in my x folder"):
        gateway._run_skill(it, gateway.Decision([it], "advanced"))
    used = independence.uses()
    assert len(used) == 1 and used[0]["kind"] == "playbook" and used[0]["passed"] == (result == "PASS") and used[0]["source"] == "owner"
    assert len(rows()) == expected


def test_a_lab_run_of_the_parameterized_skill_is_counted_as_lab(monkeypatch):
    from core import independence, gateway_context
    from tools import pcskills
    monkeypatch.setenv("JARVIS_SKILL_LAB", "1")
    monkeypatch.setattr(pcskills, "skills", lambda: fake_skills("PASS"))
    monkeypatch.setattr(gateway, "_filled_tier", lambda book, values: 0)
    card = type("C", (), {"id": "pc_show_thing", "book": FakeBook()})()
    monkeypatch.setattr(gateway_context, "skill_candidates", lambda *a, **k: [card])
    it = gateway.Intent("skill", "show the thing", skill="pc_show_thing", values={"dir": "C:\\x"})
    gateway._run_skill(it, gateway.Decision([it], "advanced"))
    assert [u["source"] for u in independence.uses()] == ["lab"]


def test_a_plain_playbook_that_fails_is_one_miss_through_the_brains_turn(monkeypatch):
    from tools import pcskills
    monkeypatch.setattr(pcskills, "skills", lambda: fake_skills("FAIL"))
    with misses.turn("tell me about the thing"):
        assert "did not verify" in pcskills.answer_with_playbook(FakeBook())
    assert len(rows()) == 1


# ------------------------------------------------------------------------------------------------- the status sentence
def test_the_status_sentence_counts_tasks_not_steps_and_keeps_planning_failures_in(monkeypatch):
    from core import independence, pcledger
    now = time.time()
    for _ in range(3):
        pcledger.create("run", {"state": "completed", "route": "gateway", "cloud_usd": 0}, "completed")
    pcledger.create("run", {"state": "completed", "route": "cloud_ai", "cloud_usd": 0.1}, "completed")
    for _ in range(40):
        pcledger.usage("local_automation", tool="open_app")           # steps: many per task
    pcledger.usage("local_planning_failure", category="model_unavailable")
    data = independence.report(now + 1)
    assert data["pc_tasks"]["local"] == 3 and data["pc_tasks"]["outside"] == 1 and data["pc_tasks"]["unplanned"] == 1
    said = independence.spoken(data)
    assert "percent of 5 PC tasks ran with no outside call, and 1 could not be planned (40 PC steps in all)" in said
    assert "60 percent" in said


# ------------------------------------------------------------------------------------------ follow-up answers keep the request
def _yes_to_waiting_plan(monkeypatch, task, state, detail):
    monkeypatch.setattr(pcfast.approvals, "meant", lambda uid, raw: "yes")
    monkeypatch.setattr(pcfast, "_yes_runs", lambda uid: (None, {"id": "p1", "kind": "plan", "data": {"task": task}}))
    monkeypatch.setattr(pcfast, "_grant", lambda *a, **k: None)

    def consent(t, control_pc, *, allow):
        pchybrid.result(state, detail, route="gateway")
        return [{"type": "text", "text": detail}]
    monkeypatch.setattr(pcfast, "_run_with_consent", consent)


def test_a_failed_yes_is_recorded_under_the_request_it_approved(monkeypatch, pc):
    _yes_to_waiting_plan(monkeypatch, "close the foo window", "stopped", "Foo is still open.")
    pcfast.handle("yes")
    got = rows()
    assert len(got) == 1 and got[0]["said"] == "close the foo window"


def test_an_unrelated_yes_does_not_recover_a_miss_and_two_failed_yeses_are_two_rows(monkeypatch, pc):
    misses.record("close the foo window", "step not verified: x")
    _yes_to_waiting_plan(monkeypatch, "open the bar program", "completed", "done")
    pcfast.handle("yes")                      # answers a DIFFERENT request
    assert [r["said"] for r in misses.rows(days=1)] == ["close the foo window"]
    _yes_to_waiting_plan(monkeypatch, "open the bar program", "stopped", "Bar did not open.")
    pcfast.handle("yes")
    _yes_to_waiting_plan(monkeypatch, "rename the baz file", "stopped", "Baz is locked.")
    pcfast.handle("yes")
    assert sorted(r["said"] for r in rows()) == ["close the foo window", "open the bar program", "rename the baz file"]


def test_a_yes_whose_request_is_unknown_writes_nothing(monkeypatch, pc):
    from tools import pcskills
    monkeypatch.setattr(pcfast.approvals, "meant", lambda uid, raw: "yes")
    monkeypatch.setattr(pcfast, "_yes_runs", lambda uid: ({"id": "g"}, None))
    monkeypatch.setattr(pcskills, "run_pending", lambda uid: pchybrid.result("stopped", "Step three failed.", route="pc")["detail"])
    pcfast.handle("yes")
    assert rows() == []


# ------------------------------------------------------------------------------------------- stops that are not failures
@pytest.mark.parametrize("why", ["needs_owner", "blocked", "user_took_over", "cancelled"])
def test_a_stop_to_ask_or_by_his_word_is_no_miss(monkeypatch, pc, why):
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: decision(
        gateway.Intent("actions", "close foo", steps=[step("close_app", app="foo")])))
    monkeypatch.setattr(pchybrid, "run", lambda task, cancelled=None, **k: pchybrid.result(
        "stopped", "Do you want to save?", route="gateway", stop_reason=why))
    ask(brain(), "close the foo window")
    assert rows() == []


def test_pcagent_names_why_it_stopped(monkeypatch):
    from core import pcagent
    for kind in ("needs_owner", "blocked", "user_took_over"):
        out = pcagent.pursue("t", {"steps": [step("close_app", app="foo")]},
                             execute=lambda s, **k: {"verified": False, "detail": "save?", "error_kind": kind},
                             replan=lambda *a: None, observe=lambda: "", approved=True, cancelled=lambda: False,
                             execution_id="e", progress=lambda m: None)
        assert out["state"] == "stopped" and out["stop_reason"] == kind
    out = pcagent.pursue("t", {"steps": [step("open_app", app="foo")]},
                         execute=lambda s, **k: {"verified": False, "detail": "nope"},
                         replan=lambda *a: None, observe=lambda: "", approved=True, cancelled=lambda: False,
                         execution_id="e", progress=lambda m: None)
    assert out["state"] == "stopped" and out["stop_reason"] == ""


# ------------------------------------------------------------------------------------------------ status sentence, one request
def test_a_cloud_planned_request_is_counted_once():
    from core import independence, pcledger
    pcledger.usage("local_planning_failure", task_fingerprint="a", billable_usd=0, status="failed", category="x", code="c", stage="s", detail="d")
    pcledger.create("run", {"state": "completed", "route": "cloud_ai", "cloud_usd": 0.02}, "completed")
    assert independence._pc_tasks(0)[:3] == (0, 1, 0)


def test_a_cancelled_planning_attempt_and_a_cancelled_run_are_not_tasks_and_a_stopped_run_is_not_local():
    from core import independence, pcledger
    pcledger.usage("local_planning_failure", task_fingerprint="a", billable_usd=0, status="cancelled", category="x", code="cancelled", stage="s", detail="d")
    pcledger.create("run", {"state": "cancelled", "route": "gateway", "cloud_usd": 0}, "cancelled")
    pcledger.create("run", {"state": "stopped", "route": "gateway", "cloud_usd": 0}, "stopped")
    pcledger.create("run", {"state": "completed", "route": "gateway", "cloud_usd": 0}, "completed")
    local, outside, unplanned, steps, stopped = independence._pc_tasks(0)
    assert (local, outside, unplanned, stopped) == (1, 0, 0, 1)
    said = independence.spoken(independence.report())
    assert "50 percent of 2 PC tasks" in said and "1 stopped before they finished" in said
