"""use_app launches the app itself, needs exactly one window, and reports the loop's real result or reason."""
from types import SimpleNamespace

import pytest

from core import pcactions, pcuse


def run(outcome, found=None, ready=True, resolved="calculator"):
    return pcuse.operate("the calc", "work out 17 times 23", resolve=lambda a: resolved, launch=lambda n: ("calculator has a visible application window.", ready),
                         windows=lambda n: found if found is not None else [{"title": "Calculator"}], make_executor=lambda t: object(),
                         run_agent=lambda goal, title, ex: outcome, settle_s=0.0)


def test_a_verified_result_is_returned_with_the_text_read_from_the_window():
    out = run(SimpleNamespace(ok=True, answer="Display is 391", why="", steps=[1, 2], needs_approval=""))
    assert out["verified"] and out["detail"] == "In calculator: Display is 391"


def test_failure_raises_with_the_real_reason_instead_of_claiming_success():
    with pytest.raises(RuntimeError, match="ran out of steps"):
        run(SimpleNamespace(ok=False, answer="", why="ran out of steps before the goal was verified", steps=[], needs_approval=""))


def test_a_consequential_control_stops_the_run_and_says_what_needs_approval():
    with pytest.raises(RuntimeError, match="press 'Delete'"):
        run(SimpleNamespace(ok=False, answer="", why="x", steps=[], needs_approval="press 'Delete'"))


def test_it_refuses_unknown_apps_unready_launches_and_ambiguous_windows():
    ok = SimpleNamespace(ok=True, answer="x", why="", steps=[], needs_approval="")
    with pytest.raises(RuntimeError, match="catalog"):
        run(ok, resolved=None)
    with pytest.raises(RuntimeError, match="visible application window"):
        run(ok, ready=False, found=[])
    with pytest.raises(RuntimeError, match="exactly one"):
        run(ok, found=[{"title": "Calculator"}, {"title": "Calculator"}])


def test_the_typed_tool_cannot_run_without_exact_plan_approval():
    with pytest.raises(RuntimeError, match="approval"):
        pcactions.perform("use_app", {"app": "calculator", "goal": "add 1 and 1"}, approved=False)


def test_an_overlong_goal_is_rejected_by_the_plan_validator():
    with pytest.raises(ValueError):
        pcactions.validate({"steps": [{"tool": "use_app", "arguments": {"app": "calculator", "goal": "x" * 301}}]})


def test_an_app_already_open_is_used_as_it_is_and_not_launched_again():
    launched = []
    ok = SimpleNamespace(ok=True, answer="Display is 391", why="", steps=[1], needs_approval="")
    out = pcuse.operate("calc", "17 times 23", resolve=lambda a: "calculator", launch=lambda n: launched.append(n) or ("", True),
                        windows=lambda n: [{"title": "Calculator"}], make_executor=lambda t: object(), run_agent=lambda g, t, e: ok)
    assert out["verified"] and launched == []


def test_the_brief_second_frame_of_a_starting_store_app_is_waited_out():
    counts = iter([[{"title": "Calculator"}] * 2, [{"title": "Calculator"}] * 2, [{"title": "Calculator"}]])
    ok = SimpleNamespace(ok=True, answer="391", why="", steps=[1], needs_approval="")
    out = pcuse.operate("calc", "17 times 23", resolve=lambda a: "calculator", launch=lambda n: ("", True),
                        windows=lambda n: next(counts, [{"title": "Calculator"}]), make_executor=lambda t: object(),
                        run_agent=lambda g, t, e: ok)
    assert out["verified"]
