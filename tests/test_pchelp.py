"""Failure that helps (core.pchelp): no dead-end policy sentences, a next step in every failure line, and a next method
tried before giving up. Synthetic paraphrases only; nothing here touches the real PC."""
import re

import pytest

from core import gateway, pchelp, pchybrid, pcmethods

POLICY = re.compile(r"paid|cloud|local (?:methods|intelligence|planning|tier|only)|escalat|budget|inference", re.I)


def test_a_failed_step_says_what_and_what_next_never_a_policy_sentence():
    line = pchelp.step_failed({"tool": "open_app", "arguments": {"app": "word"}}, [{"method": "native"}, {"method": "windows_builtin"}])
    assert line.startswith("I couldn't open") and "two ways" in line and "Start menu" in line
    assert not POLICY.search(line)
    for tool in ("close_app", "minimize_app", "type_text", "save_document"):
        text = pchelp.step_failed({"tool": tool, "arguments": {"app": "notepad", "text": "x"}}, [{}])
        assert text.startswith("I couldn't ") and not POLICY.search(text) and text.rstrip().endswith(".")


def test_pcmethods_never_surfaces_exception_text_and_gives_a_next_step(monkeypatch):
    for name in ("method_outcome", "usage", "latency"):
        monkeypatch.setattr(pcmethods.pcledger, name, lambda *a, **k: None)
    monkeypatch.setattr(pcmethods.pcledger, "method_order", lambda step, cands: list(cands))

    def internal(*a, **k):
        raise OSError(r"C:\Users\someone\hidden-thing.txt access denied")
    monkeypatch.setattr(pcmethods, "invoke", internal)
    out = pcmethods.execute({"tool": "open_app", "arguments": {"app": "calculator"}}, approved=True)
    assert out["verified"] is False and "hidden-thing" not in out["detail"]
    assert out["detail"].startswith("I couldn't open") and "Start menu" in out["detail"] and not POLICY.search(out["detail"])


def test_no_plan_lines_differ_for_a_silent_model_and_a_real_miss():
    silent = pchelp.no_plan("fast forward the video", "sir", ["advanced: unavailable"])
    missed = pchelp.no_plan("fast forward the video", "sir", ["advanced: steps do not cover the request"])
    assert "haven't touched anything" in silent and "Say it once more" in silent
    assert "haven't touched anything" in missed and "first step" in missed and silent != missed
    assert not POLICY.search(silent) and not POLICY.search(missed)
    assert pchelp.model_was_silent(["fast: lane miss", "advanced: TimeoutError"])
    assert not pchelp.model_was_silent([]) and not pchelp.model_was_silent(["advanced: unavailable", "advanced: bad plan"])
    assert not POLICY.search(pchelp.model_silent("sir"))


@pytest.mark.parametrize("said,task", [
    ("Could you open the word processor for me, please?", "open the word processor"),
    ("Jarvis, I need you to open settings.", "open settings"),
    ("Okay, go ahead and launch the notes app now", "launch the notes app"),
    ("I need to start the car", "I need to start the car"),          # his own plans are never unwrapped into a command
    ("Let's start fresh", "Let's start fresh"),
    ("open notepad", "open notepad"),
])
def test_unwrap_takes_the_polite_wrapper_off(said, task):
    assert pchelp.unwrap(said) == task


def test_next_plan_only_proposes_easy_to_undo_steps_and_never_from_a_question(monkeypatch):
    seen = []

    def simple(task, cached=True):
        seen.append((task, cached))
        low = task.casefold()
        if low.startswith("open "):
            return {"steps": [{"tool": "open_app", "arguments": {"app": low[5:]}}]}
        if low.startswith("close "):
            return {"steps": [{"tool": "close_app", "arguments": {"app": low[6:]}}]}
        return None
    monkeypatch.setattr(pchybrid, "simple_plan", simple)
    from core import pcsemantic
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: ["editor", "calculator"])
    task, plan = pchelp.next_plan("Could you open editor for me?")
    assert task == "open editor" and plan["steps"][0]["tool"] == "open_app"
    assert seen == [("open editor", False)]                      # the fixed wordings only, never a remembered model guess
    assert pchelp.next_plan("Could you open the editing thing for me?") is None   # not exactly an installed app's name
    assert pchelp.next_plan("Could you close editor for me?") is None         # a close is never guessed here
    assert pchelp.next_plan("open editor") is None               # nothing to unwrap: the exact lane already tried it
    assert pchelp.next_plan("please, is editor open") is None                 # still a question: never an action
    assert pchelp.next_plan("can you explain how editor works") is None


def test_gateway_tries_the_next_method_before_saying_it_failed(monkeypatch, tmp_path):
    from core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    ran = []
    monkeypatch.setattr(pchelp, "next_plan", lambda said: ("open the editor", {"steps": [{"tool": "open_app", "arguments": {"app": "editor"}}]}))
    monkeypatch.setattr(pchybrid, "run", lambda task, cancelled=None, *, plan=None, route=None, **k:
                        ran.append((task, plan["steps"][0]["tool"], route)) or {"state": "completed", "detail": "Opened the editor."})
    out = gateway.execute(gateway.Decision([], "none", covers=False, attempts=["advanced: unavailable"]), "Could you open the editor for me?")
    assert out == "Opened the editor." and ran == [("open the editor", "open_app", "gateway")]   # route gateway: core.authz still decides


def test_gateway_with_no_next_method_says_what_to_do_next(monkeypatch, tmp_path):
    from core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setattr(pchelp, "next_plan", lambda said: None)
    monkeypatch.setattr(pchybrid, "run", lambda *a, **k: pytest.fail("nothing may run"))
    gateway._MISSED.clear()
    out = gateway.execute(gateway.Decision([], "none", covers=False, attempts=["advanced: unavailable"]), "Fast forward the video a bit.")
    assert "haven't touched anything" in out and "Say it once more" in out and not POLICY.search(out)


def test_a_failed_type_text_sentence_ends_with_what_to_do_next(monkeypatch):
    """Audit-pc-control defect 7: the honest reading-back sentence left him with no next step."""
    from core import pcmethods
    monkeypatch.setattr(pcmethods.pcledger, "method_order", lambda step, candidates: list(candidates))
    monkeypatch.setattr(pcmethods.pcledger, "method_outcome", lambda *a, **k: None)
    monkeypatch.setattr(pcmethods.pcledger, "usage", lambda *a, **k: None)
    monkeypatch.setattr(pcmethods.pcledger, "latency", lambda *a, **k: None)

    def gone(*a, **k):
        raise RuntimeError("I pasted the text into Notepad, but reading the window back I don't see it (it shows '').")
    monkeypatch.setattr(pcmethods, "invoke", gone)
    detail = pcmethods.execute({"tool": "type_text", "arguments": {"app": "notepad", "text": "hi"}}, approved=True)["detail"]
    assert detail.startswith("I pasted the text into Notepad") and detail.endswith("Click where the text should go and ask me again.")
    assert detail.count("Click where") == 1


def test_with_next_adds_a_hint_only_for_kinds_that_have_one_and_never_twice():
    assert pchelp.with_next("find_project", "I did not find it.") == "I did not find it."
    once = pchelp.with_next("type_text", "It went wrong.")
    assert pchelp.with_next("type_text", once) == once


def test_a_planner_over_its_ceiling_is_said_as_slow_not_as_not_understood():
    said = pchelp.no_plan("rearrange everything", "sir", ["advanced: too slow (over 30 s)"])
    assert "slow" in said and "haven't touched anything" in said and "couldn't work out" not in said
