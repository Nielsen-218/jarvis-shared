"""Newly learned skills always approve (owner decision 2026-11-12): core/learned. Fake learner, fake lab, fake notifier."""
import json

import pytest

from core import acquire, learned, procedures
from tests.test_acquire import GOOD, MISS, WEAK, fake_lab, fake_learner


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LEARNED_DIR", str(tmp_path / "learned"))
    monkeypatch.setenv("JARVIS_ACQUIRE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("JARVIS_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.delenv("JARVIS_AUTO_APPROVE", raising=False)
    told = []
    from core import notify
    monkeypatch.setattr(notify, "notify", lambda title, message, *a, **k: told.append(message))
    class Store:
        pass
    out = Store()
    out.told, out.path = told, tmp_path
    return out


def _learn(store, sources=None, exam_passes=True):
    cand = acquire.discover([MISS])[0]
    return acquire.learn(cand, learner=fake_learner(sources or [GOOD]), lab=fake_lab(exam_passes), escalate=False)


def test_a_skill_that_passed_its_exam_is_approved_at_once_and_the_owner_is_told_once(store):
    rec = _learn(store)
    assert rec["status"] == "learned" and rec["method"]["approved_by"] == "auto"
    man = acquire.learned_methods()[0]
    assert acquire.approved(man) and acquire.candidates_for("close_site_tab") == ["learned:" + man["id"]]   # in use, no waiting
    row = json.loads(acquire._approvals_file().read_text(encoding="utf-8"))[man["id"]]
    assert row["by"] == "auto" and row["source_sha"] == man["source_sha"]                 # who approved, bound to the code
    assert row["exam"]["passed"] == row["exam"]["total"] == 12 and row["exam"]["judge"]   # ... and on what evidence
    assert store.told == ["I learned how to close a website's tab that is not in front; say undo that skill to remove it."]
    assert learned.tell(man["id"]) == "" and learned.tell_pending() == 0 and len(store.told) == 1      # once
    assert "approved on its exam" in acquire.summary(rec)


def test_a_failed_exam_is_never_approved(store):
    rec = _learn(store, [WEAK, GOOD], exam_passes=False)
    assert rec["status"] == "not_learned" and acquire.candidates_for("close_site_tab") == [] and store.told == []
    assert learned.auto_approve("method", "pcmethod.x", "x", 1, {"passed": 11, "total": 12}) is None
    assert learned.auto_approve("method", "pcmethod.x", "x", 1, {"passed": 0, "total": 0}) is None


def test_the_switch_off_keeps_the_old_review_gate(store, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTO_APPROVE", "off")
    rec = _learn(store)
    assert rec["status"] == "learned" and rec["method"]["approved_by"] == ""
    assert acquire.candidates_for("close_site_tab") == [] and store.told == []


def test_a_notifier_that_fails_is_tried_again_and_still_tells_only_once(store, monkeypatch):
    from core import notify
    calls = []

    def broken(*a, **k):
        calls.append(1)
        raise OSError("no phone")
    monkeypatch.setattr(notify, "notify", broken)
    _learn(store)
    assert calls and acquire.candidates_for("close_site_tab")          # approved even though the note did not go out
    sent = []
    assert learned.tell_pending(lambda t, m, *a: sent.append(m)) == 1 and learned.tell_pending(lambda t, m, *a: sent.append(m)) == 0
    assert len(sent) == 1


def test_auto_approval_never_skips_the_permission_check(store):
    """core/authz decides before any method runs; an auto-approved learned method only adds a candidate for that step."""
    from core import authz, pcmethods
    _learn(store)
    assert authz.EFFECTS["close_site_tab"] == authz.REVERSIBLE
    step = {"tool": "close_site_tab", "arguments": {"site": "github"}}
    assert pcmethods.candidates(step)[-1] == "native" and pcmethods.candidates(step)[0].startswith("learned:")
    assert pcmethods.candidates({"tool": "delete_file", "arguments": {}}) == ["native"]     # nothing learned leaks to other tools
    import inspect
    assert "authz" not in inspect.getsource(learned).split('"""', 2)[2]      # this module holds no permission logic to bypass


def test_the_owner_lists_and_removes_a_learned_skill_by_voice(store):
    _learn(store)
    said = learned.spoken_recent(now=None)
    assert "1 learned skill:" in said and "in use" in said and "12 exam tasks" in said and "undo that skill" in said
    assert learned.voice("Undo that skill.") == ("remove_learned_skill", {"name": ""})
    assert learned.voice("remove the skill for closing apps") == ("remove_learned_skill", {"name": "closing apps"})
    assert learned.voice("what did you learn recently?") == ("learned_skills", {})
    assert learned.voice("undo that") is None and learned.voice("what did you learn about Rome") is None
    assert learned.remove("juggling").startswith("I have no learned skill like that")
    assert acquire.candidates_for("close_site_tab")                      # a name that matches nothing removes nothing
    out = learned.remove("closing website tabs")
    assert out.startswith("Removed.") and acquire.candidates_for("close_site_tab") == []
    assert "removed" in learned.spoken_recent() and learned.remove() == "There is no learned skill in use to remove."
    # removed by the owner: the weakness is not learned again behind his back, and never auto-approved again
    assert acquire.discover([MISS])[0]["status"] == "removed_by_owner"
    assert learned.auto_approve("method", "pcmethod.close_site_tab", "close_site_tab", 1, {"passed": 12, "total": 12}) is None


def test_nothing_learned_yet_is_said_plainly(store):
    assert learned.spoken_recent() == "I have not learned any new skill lately."
    assert learned.remove() == "There is no learned skill in use to remove."


def test_the_voice_tools_are_registered(store):
    import tools
    from tools import selfinfo
    assert "selfinfo" in tools.ALL
    assert selfinfo.learned_skills() == "I have not learned any new skill lately."
    assert selfinfo.remove_learned_skill("") == "There is no learned skill in use to remove."


def test_a_retest_failure_still_demotes_an_auto_approved_skill(store):
    _learn(store)
    rec = acquire.retest_learned(run=lambda item: {"status": "done", "passed": False, "detail": "0/3 fresh worlds"}, now=10.0 ** 10)
    assert rec["demoted"] and acquire.candidates_for("close_site_tab") == []
    assert "failed its retest" in learned.spoken_recent(now=None)
    assert isinstance(procedures.Library(acquire._root()).ids(), list)


# ------------------------------------------------------------------ review 2026-11-12: a lab run that did not finish is not an exam

def _broken_exam(status, keep):
    """A lab whose every trial passes, but whose exam job ends early: `keep` results come back with `status`."""
    inner = fake_lab()

    def lab(trials, label):
        out = inner(trials, label)
        if label.startswith("exam"):
            return {"status": status, "results": out["results"][:keep]}
        return out
    return lab


@pytest.mark.parametrize("status, keep", [("crashed", 3), ("timeout", 3), ("crashed", 0), ("timeout", 12)])
def test_an_exam_the_lab_did_not_finish_is_a_failed_exam_and_nothing_is_approved(store, status, keep):
    cand = acquire.discover([MISS])[0]
    rec = acquire.learn(cand, learner=fake_learner([GOOD]), lab=_broken_exam(status, keep), escalate=False)
    assert rec["status"] == "not_learned"
    assert acquire.learned_methods() == [] and acquire.candidates_for("close_site_tab") == [] and store.told == []


def test_lost_lab_results_are_failed_rows_never_dropped():
    worlds = [{"seed": i, "params": {"site": "github"}, "windows": []} for i in range(4)]
    one = {"result": {"ok": True}, "judge": {"passed": True, "checks": {}, "diff": []}}
    status, rows = acquire._practice(None, GOOD, worlds, lambda t, l: {"status": "crashed", "results": [one]}, "x")
    assert status == "crashed" and [r["passed"] for r in rows] == [True, False, False, False]
    assert "no result" in rows[1]["diff"][0]


def test_auto_approval_needs_the_whole_exam_not_a_shortened_one(store):
    full = len(acquire.HELD_OUT_SEEDS)
    assert learned.auto_approve("method", "pcmethod.x", "x", 1, {"passed": 3, "total": 3, "judge": "j"}) is None
    assert learned.auto_approve("skill", "skill.x", "x", 1, {"passed": 3, "total": 3, "judge": "j", "unseen": "24/24"}) is None
    assert learned.auto_approve("skill", "skill.x", "x", 1, {"passed": full, "total": full, "judge": "j", "unseen": "5/5"}) is None
    assert learned.auto_approve("skill", "skill.x", "x", 1, {"passed": full, "total": full, "judge": "j"}) is None
    assert store.told == []


@pytest.mark.parametrize("said", [
    "How independent are you?", "jarvis, how independent are you now", "Give me the independence report", "independence report",
    "How much do you do on your own?", "how much can you do without the cloud", "How much do you do without paid models?",
    "How many paid calls did you make?", "how many paid calls have you made this month",
])
def test_clear_independence_requests_reach_the_report(said):
    assert learned.voice(said) == ("independence_report", {})


@pytest.mark.parametrize("said", [
    "I want to be more independent", "how independent is Scotland", "how independent are you from your parents",
    "I like how independent you are", "the independence report on Scotland", "how much do you do on your own computer",
    "how many calls did you make", "how independent are the voters",
])
def test_ordinary_sentences_about_independence_are_not_taken(said):
    assert learned.voice(said) is None
