"""The plan -> act -> observe -> recover loop: bounded, honest, and never wider than what the owner approved."""
from core import pcagent


def step(tool, **args):
    return {"tool": tool, "arguments": args}


class Executor:
    """Fails the listed steps (by tool) the first time they are tried; verifies everything else."""

    def __init__(self, failing=()):
        self.failing = set(failing)
        self.ran = []

    def __call__(self, s, **kwargs):
        self.ran.append(s)
        if s["tool"] in self.failing:
            self.failing.discard(s["tool"])
            return {"verified": False, "detail": f"{s['tool']} did not work"}
        return {"verified": True, "detail": f"{s['tool']} ok"}


def planner(*plans):
    it = iter(plans)
    calls = []

    def replan(task, done, failed, reason, state):
        calls.append((failed["tool"], reason, state))
        return next(it, None)
    replan.calls = calls
    return replan


def test_a_failed_step_is_replaced_by_the_planners_alternative_and_the_task_completes():
    ex = Executor(failing={"open_app"})
    plan = {"steps": [step("open_app", app="paint"), step("set_volume", percent=20)]}
    alt = {"steps": [step("open_app", app="mspaint"), step("set_volume", percent=20)]}
    rp = planner(alt)
    out = pcagent.pursue("open paint and set the volume to 20", plan, execute=ex, replan=rp, observe=lambda: "PC STATE: x")
    assert out["state"] == "completed"
    assert [s["arguments"] for s in ex.ran] == [{"app": "paint"}, {"app": "mspaint"}, {"percent": 20}]
    assert rp.calls == [("open_app", "open_app did not work", "PC STATE: x")], "the planner saw what failed, why, and the PC"
    assert len(out["recoveries"]) == 1 and out["plan"]["steps"] == ex.ran
    assert [e["verified"] for e in out["evidence"]] == [False, True, True], "the failed attempt stays in the evidence"


def test_recoveries_are_bounded_and_the_report_says_stopped():
    ex = Executor(failing={"open_app", "focus_app", "pc_status"})
    plan = {"steps": [step("open_app", app="paint")]}
    rp = planner({"steps": [step("focus_app", process="paint")]}, {"steps": [step("pc_status")]}, {"steps": [step("get_volume")]})
    out = pcagent.pursue("open paint", plan, execute=ex, replan=rp, max_recoveries=2)
    assert out["state"] == "stopped" and len(out["recoveries"]) == 2 and len(rp.calls) == 2
    assert [s["tool"] for s in ex.ran] == ["open_app", "focus_app", "pc_status"]


def test_a_recovery_that_needs_a_sensitive_tool_is_proposed_for_review_not_run():
    ex = Executor(failing={"open_app"})
    plan = {"steps": [step("open_app", app="paint")]}
    rp = planner({"steps": [step("close_app", app="paint"), step("open_app", app="paint")]})
    out = pcagent.pursue("open paint", plan, execute=ex, replan=rp)
    assert out["state"] == "needs_review" and len(ex.ran) == 1
    # The review shows the whole recovery: the fix (needs the OK) and the retry after it. Retrying only AFTER a different
    # step is fixing the cause, not a blind repeat (2026-11-06: 'restore the minimized Notepad, then type' was cut to the restore).
    assert out["proposed"] == {"steps": [step("close_app", app="paint"), step("open_app", app="paint")]}
    assert "close Paint" in out["detail"] and "open" not in out["detail"], "only the step that needs the OK is named"


def test_a_recovery_may_not_touch_an_app_the_owner_never_mentioned():
    ex = Executor(failing={"open_app"})
    plan = {"steps": [step("open_app", app="paint")]}
    rp = planner({"steps": [step("open_app", app="photoshop")]})
    out = pcagent.pursue("open paint", plan, execute=ex, replan=rp)
    assert out["state"] == "needs_review" and [s["tool"] for s in ex.ran] == ["open_app"]


def test_an_app_named_in_the_sentence_or_the_reviewed_plan_is_fair_game():
    plan = {"steps": [step("open_app", app="paint")]}
    assert pcagent.allowed(step("focus_app", process="mspaint.exe"), "open paint and then mspaint", plan)
    assert pcagent.allowed(step("focus_app", process="paint"), "open it", plan)
    assert not pcagent.allowed(step("focus_app", process="chrome"), "open paint", plan)
    assert pcagent.allowed(step("close_app", app="paint"), "x", {"steps": [step("close_app", app="paint")]}), "an exact reviewed step"
    assert not pcagent.allowed(step("made_up_tool"), "x", plan)


def test_repeating_the_failed_step_is_not_a_recovery():
    ex = Executor(failing={"open_app"})
    plan = {"steps": [step("open_app", app="paint")]}
    rp = planner({"steps": [step("open_app", app="paint")]})
    out = pcagent.pursue("open paint", plan, execute=ex, replan=rp)
    assert out["state"] == "stopped" and len(ex.ran) == 1 and "repeat" in out["evidence"][-1]["detail"]


def test_fixing_the_cause_then_retrying_the_failed_step_is_a_recovery():
    ex = Executor(failing={"type_text"})
    plan = {"steps": [step("type_text", app="notepad", text="hi")]}
    rp = planner({"steps": [step("focus_app", process="notepad.exe"), step("type_text", app="notepad", text="hi")]})
    out = pcagent.pursue("type hi into notepad", plan, execute=ex, replan=rp)
    assert out["state"] == "completed" and [s["tool"] for s in ex.ran] == ["type_text", "focus_app", "type_text"]


def test_a_retry_first_is_dropped_and_only_retries_left_is_a_stop():
    ex = Executor(failing={"open_app"})
    plan = {"steps": [step("open_app", app="paint")]}
    rp = planner({"steps": [step("open_app", app="paint"), step("open_app", app="paint")]})
    out = pcagent.pursue("open paint", plan, execute=ex, replan=rp)
    assert out["state"] == "stopped" and len(ex.ran) == 1


def test_no_alternative_means_an_honest_stop():
    ex = Executor(failing={"open_app"})
    out = pcagent.pursue("open paint", {"steps": [step("open_app", app="paint")]}, execute=ex, replan=planner(None))
    # Honest: the failed step's own reason is the last thing said; a second "I could not find another way" is only added
    # when nothing was said yet (owner 2026-11-07: "he says too much").
    last = out["evidence"][-1]
    assert out["state"] == "stopped" and last["verified"] is False and last["detail"]


def test_a_cancel_a_block_or_a_take_over_is_never_recovered_from():
    rp = planner({"steps": [step("get_volume")]})
    out = pcagent.pursue("x", {"steps": [step("get_volume")]}, execute=lambda s, **k: {"verified": True}, replan=rp, cancelled=lambda: True)
    assert out["state"] == "cancelled" and rp.calls == []
    blocked = lambda s, **k: {"verified": False, "detail": "blocked by the permission gate", "error_kind": "blocked"}  # noqa: E731
    out = pcagent.pursue("x", {"steps": [step("get_volume")]}, execute=blocked, replan=rp)
    assert out["state"] == "stopped" and rp.calls == []
    took = lambda s, **k: {"verified": False, "detail": "the user took over the mouse"}  # noqa: E731
    out = pcagent.pursue("x", {"steps": [step("get_volume")]}, execute=took, replan=rp)
    assert out["state"] == "stopped" and rp.calls == []


def test_an_exception_from_a_typed_action_is_a_reason_to_replan_not_a_crash():
    calls = []

    def execute(s, **kwargs):
        calls.append(s["tool"])
        if s["tool"] == "open_app":
            raise RuntimeError("No such app in the catalog.")
        return {"verified": True, "detail": "ok"}
    out = pcagent.pursue("open paint", {"steps": [step("open_app", app="paint")]}, execute=execute,
                         replan=planner({"steps": [step("focus_app", process="paint")]}))
    assert out["state"] == "completed" and calls == ["open_app", "focus_app"]
    assert out["evidence"][0] == {"step": 1, "verified": False, "detail": "No such app in the catalog."}
