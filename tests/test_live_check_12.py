"""Live check 2026-11-15 (17:40, the laptop): "Are you working on it?" got "I'll start by opening Blender and creating a new humanoid
character for you." with no tool behind it, and the PC's own status never answered the laptop: the relayed probe set the
"controlling your PC" marker BEFORE quick._status looked, so the PC believed it was busy and said "not a PC request"."""
import threading

import pytest

from core import intelligence, pctasks, progress


@pytest.mark.parametrize("text", [
    "I'll start by opening Blender and creating a new humanoid character for you.",
    "I will begin rigging it now, sir.",
    "I'll start generating the humanoid.",
    "Let me start the render."])
def test_a_promise_to_start_with_no_tool_is_refused(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "I'll start by asking which one you mean, sir.",
    "I'll start by explaining how it works.",
    "Let's start with the one you like.",
    "The game will start at eight, sir."])
def test_starting_a_conversation_or_a_game_is_not_a_promise_of_work(text):
    assert not intelligence.promises_action(text)


def test_a_relayed_probe_does_not_look_like_work_in_progress(monkeypatch):
    seen = {}
    from core import gateway, quick
    from tools import computer, gaming
    monkeypatch.setattr(gaming, "game_mode", lambda: False)
    monkeypatch.setattr(gateway, "mode", lambda: "on")

    def fake_quick(said):
        seen["what"] = progress._now.get("what")
        return "Nothing is running that I know of, sir."

    monkeypatch.setattr(quick, "handle", fake_quick)
    monkeypatch.setattr(computer, "control_computer", lambda **kw: pytest.fail("a probe answered by quick must not control the PC"))
    progress._now["what"] = ""
    jobs = pctasks.Jobs()
    job = {"id": "j", "uid": "owner", "fingerprint": "f", "name": "control_computer", "state": "running", "detail": "", "updated": 0,
           "cancel_event": threading.Event(), "probe": True}
    jobs.jobs["j"] = job
    jobs.active = "j"
    try:
        jobs._execute("j", {"task": "Are you working on it?"}, "")
    finally:
        progress._now["what"] = ""
    assert seen["what"] == ""
    assert job["state"] != "failed"
    assert "Nothing is running" in str(job.get("detail"))


@pytest.mark.parametrize("text", ["I will keep you posted on the progress.", "I'll keep you updated, sir.", "I'll keep you in the loop."])
def test_a_promise_to_keep_him_posted_with_no_tool_is_refused(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", ["If you like, I can keep you posted.", "You can keep me posted on the plan, sir."])
def test_an_offer_or_his_own_updates_are_not_a_promise(text):
    assert not intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "I'll draw it from a description, rig it up, and run the generation locally on your PC. Give me a few minutes and I'll let you know when it's ready.",
    "Give me a couple of minutes and I'll let you know when it's ready.",
    "It is being built locally in Blender right now.",
    "It's being generated now, sir."])
def test_the_wordings_the_live_check_found_are_refused(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "I will let you know as soon as it is ready.", "I'll let you know once it's done, sir.", "It will be created locally and free of charge."])
def test_more_wordings_from_the_second_live_check_are_refused(text):
    assert intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "I'll let you know if I hear anything back from you, sir?", "It will be created once you approve the plan.",
    "It will be made in Blender if you say so."])
def test_an_offer_with_a_condition_is_not_a_promise(text):
    assert not intelligence.promises_action(text)


@pytest.mark.parametrize("text", [
    "Give me a few minutes to think about it, sir.",
    "The bridge is being built by the city, sir.",
    "I'll draw your attention to the second option."])
def test_other_uses_of_those_words_are_not_promises(text):
    assert not intelligence.promises_action(text)
