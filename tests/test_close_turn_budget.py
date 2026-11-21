"""The bounded turn: a one-app verb has a wall-clock budget, but only the ORIGINAL plan's app verbs are cut by it; a step a re-plan
added keeps its own ceiling and is not started when the budget is gone; every out-of-time exit looks at the PC once more first;
the reply says the real reason. Fakes only: no window, process, catalogue, model or PC tool is touched."""
import pytest

from core import pcactions, pcagent, pchybrid, pcmethods


def step(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


LAUNCH = "Launch was requested for blender, but no visible application window was verified yet."


# ------------------------------------------------------------------------------- the real reason is said, bookkeeping follows
def test_a_bookkeeping_row_never_replaces_the_real_reason():
    real = {"step": 1, "verified": False, "detail": LAUNCH}
    repeat = {"verified": False, "detail": "The only idea was to repeat what just failed; I stopped instead.", "meta": True}
    invalid = {"verified": False, "detail": "The alternative plan failed validation, so nothing more ran.", "meta": True}
    late = {"verified": False, "detail": pcagent.OUT_OF_TIME, "error_kind": "out_of_time", "meta": True}
    assert pchybrid.compose_detail([real, repeat], "stopped") == LAUNCH
    assert pchybrid.compose_detail([real, invalid], "stopped") == LAUNCH
    assert pchybrid.compose_detail([real, late], "stopped") == LAUNCH + " " + pcagent.OUT_OF_TIME
    assert pchybrid.compose_detail([late], "stopped") == pcagent.OUT_OF_TIME
    assert pchybrid.compose_detail([repeat], "stopped") == repeat["detail"]


def test_pursue_tags_its_own_bookkeeping_rows():
    plan = {"steps": [step("open_app", app="blender")]}
    outcome = pcagent.pursue("open blender", plan, execute=lambda s, **k: {"verified": False, "detail": LAUNCH},
                             replan=lambda *a: {"steps": [step("open_app", app="blender")]},
                             observe=lambda: "", approved=True, cancelled=lambda: False, progress=lambda s: None)
    assert outcome["state"] == "stopped"
    assert pchybrid.compose_detail(outcome["evidence"], outcome["state"]) == LAUNCH


# ---------------------------------------------------------------------------------------- the budget cuts only the plan's own verbs
def test_a_replanned_step_that_is_not_an_app_verb_keeps_its_own_ceiling(monkeypatch):
    monkeypatch.setattr(pcmethods.time, "monotonic", lambda: 50.0)
    with pcmethods.turn_deadline(52.0):
        for tool in ("open_path", "use_app", "find_project"):
            assert pcmethods._step_limit(tool) == pcmethods.step_timeout(tool), tool
        assert pcmethods._step_limit("close_app") == pcmethods._CLOSE_FLOOR_S      # a close is never cut below its own wait
        assert pcmethods._step_limit("focus_app") == 2.0                  # the plan's own verbs are cut to what is left


def test_a_replanned_app_verb_keeps_its_own_ceiling_too(monkeypatch):
    monkeypatch.setattr(pcmethods.time, "monotonic", lambda: 50.0)
    seen = {}

    def execute(s, **kwargs):
        seen[s["tool"]] = pcmethods._step_limit(s["tool"])
        failed = s["tool"] == "open_app"
        return {"verified": not failed, "detail": "no" if failed else "ok"}
    monkeypatch.setattr(pcactions, "validate", lambda plan: None)
    with pcmethods.turn_deadline(60.0):
        out = pcagent.pursue("open paint", {"steps": [step("open_app", app="paint")]}, execute=execute, deadline=60.0,
                             replan=lambda *a: {"steps": [step("close_app", app="x"), step("focus_app", app="paint")]},
                             allowed_fn=lambda *a: True)
    assert out["state"] == "completed"
    assert seen["open_app"] == 10.0, "the plan's own verb is cut to what was left, with the launch floor"
    assert seen["close_app"] == pcmethods.step_timeout("close_app"), "a step the re-plan added keeps its own ceiling"
    assert seen["focus_app"] == pcmethods.step_timeout("focus_app")


def test_opening_an_app_is_never_cut_below_its_own_launch_wait(monkeypatch):
    monkeypatch.setattr(pcmethods.time, "monotonic", lambda: 50.0)
    with pcmethods.turn_deadline(51.0):
        assert pcmethods._step_limit("open_app") >= 10.0                  # launch_and_wait waits up to 8 s
        assert pcmethods._step_limit("close_app") == pcmethods._CLOSE_FLOOR_S >= pcactions.CLOSE_WAIT_S + 1.0
    with pcmethods.turn_deadline(70.0):
        assert pcmethods._step_limit("open_app") == 20.0                  # nothing to cut: the ordinary ceiling


def test_a_replanned_step_is_not_started_with_almost_no_budget_left(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])
    ran = []
    monkeypatch.setattr(pcactions, "validate", lambda plan: None)

    def execute(s, **kwargs):
        ran.append(s["tool"])
        now[0] += 10.5                                # the first attempt leaves 1.5 s of the 12 s turn
        return {"verified": False, "detail": "I couldn't close Notepad."}
    out = pcagent.pursue("close notepad", {"steps": [step("close_app", app="notepad")]}, execute=execute, deadline=112.0,
                         replan=lambda *a: {"steps": [step("use_app", app="notepad", goal="x")]}, settle=lambda s: None,
                         dialog_open=lambda s: True, allowed_fn=lambda *a: True)
    assert ran == ["close_app"], "never started and then cut after a second or two"
    assert out["state"] == "stopped" and out["evidence"][-1]["detail"] == pcagent.OUT_OF_TIME


def test_a_replanned_step_with_enough_budget_left_starts_and_runs(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])
    ran = []
    monkeypatch.setattr(pcactions, "validate", lambda plan: None)

    def execute(s, **kwargs):
        ran.append(s["tool"])
        now[0] += 1.0
        return {"verified": s["tool"] != "close_app", "detail": "x"}
    out = pcagent.pursue("close notepad", {"steps": [step("close_app", app="notepad")]}, execute=execute, deadline=112.0,
                         replan=lambda *a: {"steps": [step("use_app", app="notepad", goal="x")]}, settle=lambda s: None,
                         dialog_open=lambda s: True, allowed_fn=lambda *a: True)
    assert ran == ["close_app", "use_app"] and out["state"] == "completed"


def test_a_simple_one_app_turn_has_a_budget_and_a_bigger_plan_does_not(monkeypatch):
    monkeypatch.setattr(pcagent, "_closes_a_game", lambda steps: False)      # no real process scan in a test
    assert pcagent.turn_budget({"steps": [step("close_app", app="notepad")]}) == pcagent.SIMPLE_TURN_S == 12.0
    assert pcagent.turn_budget({"steps": [step("open_app", app="notepad"), step("type_text", app="notepad", text="x")]}) is None
    assert pcagent.turn_budget({"steps": []}) is None
    monkeypatch.setenv("JARVIS_SIMPLE_TURN_S", "5")
    assert pcagent.turn_budget({"steps": [step("focus_app", process="notepad.exe")]}) == 5.0


def test_past_the_budget_nothing_new_starts_and_it_says_so_plainly(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])
    ran = []

    def execute(s, **kwargs):
        ran.append(s["tool"])
        now[0] += 13.0                              # the first attempt used the whole budget
        return {"verified": False, "detail": "I couldn't close Notepad."}
    out = pcagent.pursue("close notepad", {"steps": [step("close_app", app="notepad")]}, execute=execute,
                         replan=lambda *a: pytest.fail("no re-plan past the budget"), deadline=112.0)
    assert ran == ["close_app"] and out["state"] == "stopped"
    assert out["evidence"][-1]["detail"] == pcagent.OUT_OF_TIME
    assert pchybrid.compose_detail(out["evidence"], out["state"]) == "I couldn't close Notepad. I ran out of time on that, so I stopped."


def test_a_step_is_cut_to_what_is_left_of_the_turn(monkeypatch):
    monkeypatch.setattr(pcmethods.time, "monotonic", lambda: 50.0)
    with pcmethods.turn_deadline(53.0):
        assert pcmethods._step_limit("focus_app") == 3.0
        assert pcmethods._step_limit("close_app") == pcmethods._CLOSE_FLOOR_S      # more than is left: a close started must be able to finish
        assert pcmethods._step_limit("use_app") == 180.0
    with pcmethods.turn_deadline(50.2):
        assert pcmethods._step_limit("focus_app") == 1.0     # never below a second
    assert pcmethods._step_limit("close_app") == 20.0


def test_close_waits_long_enough_for_a_packaged_window_to_go():
    assert pcactions.CLOSE_WAIT_S >= 4.0


# ------------------------------------------------------------- every out-of-time exit looks at the PC once more first
LATE_LAUNCH = {"verified": True, "detail": "Opened steam."}


def test_a_window_that_appeared_during_the_replan_is_a_success_not_a_stop(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])
    settled = []

    def execute(s, **kwargs):
        now[0] += 8.0
        return {"verified": False, "detail": LAUNCH}

    def settle(s):
        settled.append(s["tool"])
        return None if len(settled) == 1 else LATE_LAUNCH             # the window shows up later

    def replan(*a):
        now[0] += 5.0                                  # the planner takes its time, and the deadline passes
        return {"steps": [step("focus_app", app="steam")]}
    monkeypatch.setattr(pcactions, "validate", lambda plan: None)
    out = pcagent.pursue("open steam", {"steps": [step("open_app", app="steam")]}, execute=execute, replan=replan,
                         deadline=110.0, settle=settle)
    assert len(settled) == 2 and out["state"] == "completed" and out["evidence"][-1]["verified"] is True


def test_the_exit_straight_after_a_failed_step_has_looked_at_the_pc(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])
    looked = []

    def execute(s, **kwargs):
        now[0] += 13.0
        return {"verified": False, "detail": LAUNCH}

    def settle(s):
        looked.append(s["tool"])
        return LATE_LAUNCH
    out = pcagent.pursue("open steam", {"steps": [step("open_app", app="steam")]}, execute=execute,
                         replan=lambda *a: pytest.fail("no re-plan"), deadline=112.0, settle=settle)
    assert looked == ["open_app"] and out["state"] == "completed"


def test_the_exit_before_a_replanned_step_looks_at_the_pc_again(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])
    looked, ran = [], []
    monkeypatch.setattr(pcactions, "validate", lambda plan: None)

    def execute(s, **kwargs):
        ran.append(s["tool"])
        now[0] += 7.0
        return {"verified": False, "detail": "I couldn't close Notepad."}

    def settle(s):
        looked.append(now[0])
        return None if len(looked) == 1 else {"verified": True, "detail": "Closed notepad."}

    def replan(*a):
        now[0] += 2.0                                  # leaves 1 s: too little to start a re-planned step
        return {"steps": [step("use_app", app="notepad", goal="x")]}
    out = pcagent.pursue("close notepad", {"steps": [step("close_app", app="notepad")]}, execute=execute, deadline=110.0,
                         replan=replan, settle=settle, dialog_open=lambda s: True, allowed_fn=lambda *a: True)
    assert ran == ["close_app"] and len(looked) == 2 and out["state"] == "completed", out


def test_an_unverified_launch_out_of_time_says_it_may_still_be_starting(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])

    def execute(s, **kwargs):
        now[0] += 13.0
        return {"verified": False, "detail": LAUNCH}
    out = pcagent.pursue("open steam", {"steps": [step("open_app", app="steam")]}, execute=execute,
                         replan=lambda *a: pytest.fail("no re-plan past the budget"), deadline=112.0, settle=lambda s: None)
    assert out["state"] == "stopped"
    spoken = pchybrid.compose_detail(out["evidence"], out["state"])
    assert "Launch was requested for blender" in spoken
    assert pcagent.OUT_OF_TIME not in spoken and "still be starting" in spoken, spoken


def test_a_failed_close_out_of_time_still_says_it_stopped(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])

    def execute(s, **kwargs):
        now[0] += 13.0
        return {"verified": False, "detail": "I couldn't close Notepad."}
    out = pcagent.pursue("close notepad", {"steps": [step("close_app", app="notepad")]}, execute=execute,
                         replan=lambda *a: pytest.fail("no re-plan past the budget"), deadline=112.0, settle=lambda s: None)
    assert pchybrid.compose_detail(out["evidence"], out["state"]) == "I couldn't close Notepad. " + pcagent.OUT_OF_TIME
