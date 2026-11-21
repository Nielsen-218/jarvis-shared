"""The general UI loop: it only acts on controls that exist, never presses consequential ones, and claims success only with proof read back."""
from types import SimpleNamespace

from jarvis_pc.uiact import agent


class FakeApp:
    """A tiny calculator-like window: pressing buttons changes the shown text."""

    def __init__(self):
        self.shown = "Display is 0"
        self.lines = []

    def view(self, title):
        return {"click": ["Seven", "Plus", "Two", "Equals", "Delete account"], "fields": [], "shown": [self.shown]}

    def run(self, line):
        self.lines.append(line)
        if line.startswith("click"):
            self.shown = "Display is 9" if "Equals" in line else self.shown
        if line.startswith("read"):
            return SimpleNamespace(ok=True, stdout=self.shown, stderr="", error_kind=None)
        return SimpleNamespace(ok=True, stdout="", stderr="", error_kind=None)


def script(*choices):
    it = iter(choices)
    return lambda system, body, schema, cancelled: next(it, None)


def c(action, target="", text=""):
    return {"action": action, "target": target, "text": text}


def test_a_goal_is_reached_and_the_proof_is_read_back_from_the_window():
    app = FakeApp()
    out = agent.run("add 7 and 2", "Calculator", app, observe_fn=app.view, plan_first=False,
                    ask=script(c("click", "seven"), c("click", "Plus"), c("click", "Two"), c("click", "Equals"), c("done", "Display is 9")))
    assert out.ok and out.answer == "Display is 9"
    assert app.lines[:4] == ['click "Seven"', 'click "Plus"', 'click "Two"', 'click "Equals"'], "names are matched to the real control, any case"


def test_a_name_with_a_decoding_tail_still_means_the_observed_control():
    assert agent._match("Display is 42oki", ["Display is 42", "Display is 4"]) == "Display is 42"
    assert agent._match("seven}", ["Seven", "Seventeen"]) == "Seven"
    assert agent._match("Seve", ["Seven", "Seventeen"]) is None, "two candidates: never a guess"
    assert agent._match("Equa", ["Equals"]) == "Equals"
    assert agent._match("Fly", ["Seven"]) is None


def test_a_control_the_window_does_not_have_is_never_acted_on():
    app = FakeApp()
    out = agent.run("do something", "Calculator", app, observe_fn=app.view,
                    ask=script(c("click", "Format C drive"), c("click", "Launch missiles"), c("click", "Fly"), c("click", "Swim")))
    assert not out.ok and app.lines == []


def test_a_consequential_control_is_never_pressed_the_loop_asks_instead():
    app = FakeApp()
    out = agent.run("clean up", "Calculator", app, observe_fn=app.view, plan_first=False, ask=script(c("click", "Delete account")))
    assert not out.ok and out.needs_approval == "press 'Delete account'" and app.lines == []


def test_done_without_having_done_anything_or_without_visible_proof_is_not_success():
    app = FakeApp()
    out = agent.run("add", "Calculator", app, observe_fn=app.view, plan_first=False, ask=script(c("done", "Display is 0"), c("done", "Display is 0"), c("done", "x"), c("done", "x")))
    assert not out.ok and out.answer == ""


def test_a_window_that_stops_changing_ends_the_loop_instead_of_repeating():
    app = FakeApp()
    out = agent.run("add", "Calculator", app, observe_fn=app.view, plan_first=False, max_steps=12, ask=lambda *a: c("click", "Seven"))
    assert not out.ok and "stopped changing" in out.why and len(app.lines) <= 4


def test_no_model_means_an_honest_stop_not_a_guess():
    app = FakeApp()
    out = agent.run("add", "Calculator", app, observe_fn=app.view, plan_first=False, ask=lambda *a: None)
    assert not out.ok and "could not decide" in out.why


def test_a_blocked_step_ends_the_run():
    app = FakeApp()
    app.run = lambda line: SimpleNamespace(ok=False, stdout="", stderr="blocked by the permission gate", error_kind="blocked")
    out = agent.run("add", "Calculator", app, observe_fn=app.view, plan_first=False, ask=script(c("click", "Seven")))
    assert not out.ok and "permission gate" in out.why


def test_a_plan_runs_step_by_step_under_the_same_rules_and_a_consequential_step_stops_it():
    app = FakeApp()
    plan = {"steps": [c("click", "seven"), c("click", "Format the disk"), c("click", "Plus"), c("click", "Delete account"), c("click", "Two")]}
    out = agent.run("add 7 and 2", "Calculator", app, observe_fn=app.view, ask=script(plan))
    assert app.lines == ['click "Seven"', 'click "Plus"', 'click "Two"'], "invented and consequential controls are skipped, never pressed"
    assert out.needs_approval == "press 'Delete account'"


def test_a_clean_plan_then_the_model_confirms_with_proof_from_the_window():
    app = FakeApp()
    plan = {"steps": [c("click", "seven"), c("click", "Plus"), c("click", "Two"), c("click", "Equals")]}
    out = agent.run("add 7 and 2", "Calculator", app, observe_fn=app.view, ask=script(plan, c("done", "Display is 9")))
    assert out.ok and out.answer == "Display is 9" and len(app.lines) == 5


# --- live 2026-11-07 (Calculator, gemma4): the loop's honesty and recovery rules
def test_a_key_that_names_a_control_is_clicked():
    assert agent._mend(c("key", "Multiply by", "multiply")) == c("click", "Multiply by", "")
    assert agent._mend(c("key", "", "enter")) == c("key", "", "enter")


def test_only_the_windows_own_close_button_is_held_back():
    assert agent._closes_window("Close Calculator", "Calculator") and agent._closes_window("Close", "Calculator")
    assert not agent._closes_window("Close history flyout", "Calculator")
    app = FakeApp()
    app.view = lambda title: {"click": ["Seven", "Equals", "Close Calculator"], "fields": [], "shown": [app.shown]}
    out = agent.run("add", "Calculator", app, observe_fn=app.view, plan_first=False,
                    ask=script(c("click", "Equals"), c("click", "Close Calculator"), c("done", "Display is 9")))
    assert out.ok and 'click "Close Calculator"' not in app.lines


def test_reading_the_same_result_twice_after_real_work_is_done_and_still_read_back():
    app = FakeApp()
    out = agent.run("add 7 and 2", "Calculator", app, observe_fn=app.view, plan_first=False,
                    ask=script(c("click", "Equals"), c("read", "Display is 9"), c("read", "Display is 9")))
    assert out.ok and out.answer == "Display is 9" and app.lines[-1] == 'read "Display is 9"'


def test_a_success_the_checker_rejects_is_reported_as_a_failure():
    app = FakeApp()
    out = agent.run("add 7 and 2", "Calculator", app, observe_fn=app.view, plan_first=False,
                    ask=script(c("click", "Equals"), c("done", "Display is 9")), verify=lambda goal, proof: False)
    assert not out.ok and "does not show the goal" in out.why
    app2 = FakeApp()
    ok = agent.run("add 7 and 2", "Calculator", app2, observe_fn=app2.view, plan_first=False,
                   ask=script(c("click", "Equals"), c("done", "Display is 9")), verify=lambda goal, proof: None)
    assert ok.ok and ok.answer == "Display is 9", "no checker answer keeps the window's own proof"
