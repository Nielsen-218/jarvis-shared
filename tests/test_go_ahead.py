"""'Sure, go ahead.' with nothing waiting got 'I have your authorization, sir.' (live 2026-11-16, the laptop)."""
import pytest

from core import chatturn


def _chat(*pairs):
    out = []
    for role, text in pairs:
        out.append({"role": role, "content": text})
    return out


@pytest.mark.parametrize("said", ["Sure, go ahead.", "Go ahead.", "Yeah, do it.", "Okay, proceed, sir.", "Yes, go ahead please.", "Go for it."])
def test_a_go_ahead_with_nothing_waiting_is_asked_about(said, monkeypatch):
    from core import approvals, pcledger
    monkeypatch.setattr(approvals, "pending", lambda uid: [])
    monkeypatch.setattr(pcledger, "pending", lambda: [])
    history = _chat(("user", "Are you working on it?"), ("assistant", "Nothing is running that I know of, sir."), ("user", said))
    assert chatturn.nothing_to_go_ahead_with(said, history) == "Go ahead with what, sir? I don't have anything waiting."
    assert chatturn.nothing_to_go_ahead_with("[Sent from their Jarvis phone app.] " + said, history, "boss").startswith("Go ahead with what, boss?")
    assert chatturn.nothing_to_go_ahead_with(said, _chat(("user", said))) is not None          # the very first line


@pytest.mark.parametrize("reply", [
    "Shall I open Blender and start on the scene?", "I can open Blender for you if you'd like.", "Which one, sir?",
    "If you want, I can pull up the price book."])
def test_a_go_ahead_that_answers_an_offer_is_left_to_the_normal_path(reply, monkeypatch):
    from core import approvals, pcledger
    monkeypatch.setattr(approvals, "pending", lambda uid: [])
    monkeypatch.setattr(pcledger, "pending", lambda: [])
    history = _chat(("user", "Make a scene."), ("assistant", reply), ("user", "Sure, go ahead."))
    assert chatturn.nothing_to_go_ahead_with("Sure, go ahead.", history) is None


def test_a_waiting_plan_or_approval_keeps_the_go_ahead(monkeypatch):
    from core import approvals, pcledger
    monkeypatch.setattr(approvals, "pending", lambda uid: [{"id": 1}])
    monkeypatch.setattr(pcledger, "pending", lambda: [])
    history = _chat(("user", "Delete the old logs."), ("assistant", "That will remove 40 files."), ("user", "Go ahead."))
    assert chatturn.nothing_to_go_ahead_with("Go ahead.", history) is None


@pytest.mark.parametrize("said", ["Open Notepad.", "Go ahead and open Notepad.", "Do it again.", "Yes.", "Go.", "I will go ahead tomorrow."])
def test_other_lines_are_not_go_aheads(said):
    assert chatturn.nothing_to_go_ahead_with(said, _chat(("user", said))) is None


def _hist(*pairs):
    return [{"role": r, "content": t} for r, t in pairs]


@pytest.mark.parametrize("said", [
    "Why can't you work on the one I have open right now?", "Well, why can't you do that?", "Why won't it open?", "Why don't you remember?",
    "Why can't you see it?"])
def test_a_why_cant_you_with_no_cause_given_is_not_explained_by_a_made_up_reason(said):
    history = _hist(("user", "Make a scene."), ("assistant", "I haven't started that, sir."), ("user", said))
    answer = chatturn.why_cant_you_unknown(said, history)
    assert answer.startswith("I'm not sure why, sir, and I won't make up a reason.")
    assert chatturn.why_cant_you_unknown("[Sent from their Jarvis phone app.] " + said, history, "boss").startswith("I'm not sure why, boss,")


def test_when_he_just_gave_a_real_cause_the_model_explains_it():
    history = _hist(("user", "Make a character."), ("assistant", "The job failed because only 9791 MB of graphics memory is free."),
                    ("user", "Why can't you do it?"))
    assert chatturn.why_cant_you_unknown("Why can't you do it?", history) is None


@pytest.mark.parametrize("said", ["Why is the sky blue?", "Why do cats purr?", "Why did Rome fall?", "Can you tell me why?", "Why can't I sleep?"])
def test_other_why_questions_are_left_to_the_model(said):
    assert chatturn.why_cant_you_unknown(said, _hist(("user", said))) is None
