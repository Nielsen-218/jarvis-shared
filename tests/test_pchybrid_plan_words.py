"""A review request says WHAT would run: the phone and the voice path only get pchybrid's detail text, so the plan must be in it."""
from core import pchybrid


def step(tool, **args):
    return {"tool": tool, "arguments": args}


def test_plan_words_names_each_step_and_marks_the_sensitive_ones():
    words = pchybrid.plan_words({"steps": [step("open_app", app="paint"), step("uninstall_program", name="ripgrep")]})
    assert words == "Plan: 1) open paint. 2) uninstall the program 'ripgrep' (sensitive)."


def test_an_unknown_tool_still_reads_as_words_and_an_empty_plan_says_nothing():
    assert pchybrid.plan_words({"steps": [step("frobnicate_thing", target="x")]}) == "Plan: 1) frobnicate thing target=x (sensitive).", \
        "an unknown verb never runs unasked (core.authz), so it is marked"
    assert pchybrid.plan_words({"steps": []}) == "" and pchybrid.plan_words(None) == ""


def test_every_needs_review_result_carries_the_plan_in_its_words():
    """Owner 2026-11-08: the numbered readout made him sound like a robot. The question itself now says what would run
    in plain words; the numbered plan, sensitive steps marked, stays on the record for the phone screen and the logs."""
    plan = {"steps": [step("uninstall_program", name="ripgrep")]}
    first = pchybrid.result("needs_review", "Should I uninstall ripgrep?", plan=plan, approval_id="a")
    assert first["detail"] == "Should I uninstall ripgrep?"
    assert first["plan_words"] == "Plan: 1) uninstall the program 'ripgrep' (sensitive)."
    done = pchybrid.result("completed", "done", plan=plan)
    assert done["detail"] == "done" and "plan_words" not in done, "only a review request needs the plan spelled out"


def test_spoken_plan_reads_like_a_person():
    two = {"steps": [step("focus_app", process="msedge.exe"), step("close_browser_tab")]}
    assert pchybrid.spoken_plan(two) == "bring Edge to the front, then close the browser tab"
    assert pchybrid.spoken_plan({"steps": [step("use_app", app="paint", goal="open a new canvas")]}) == "open a new canvas in Paint"
    assert pchybrid.spoken_plan({"steps": [step("close_app", app="chatgpt")]}) == "close ChatGPT"
    assert "Plan" not in pchybrid.spoken_plan(two) and "1)" not in pchybrid.spoken_plan(two)
    assert pchybrid.spoken_plan({"steps": []}) == ""
