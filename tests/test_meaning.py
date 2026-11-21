"""Jarvis understands what the owner MEANS where he used to need exact lines (owner 2026-11-07: "I will not remember exact
lines; I'm a human, not a robot"). The model is faked here; scratchpad meaning_probe.py measures the real one (28/29)."""
import pytest

from core import approvals, meaning, saveprompt


def test_choose_only_returns_an_offered_meaning():
    opts = {"yes": "do it", "no": "don't"}
    assert meaning.choose("sounds good", opts, ask=lambda b, s, c: {"meaning": "yes"}) == "yes"
    assert meaning.choose("what time is it", opts, ask=lambda b, s, c: {"meaning": "other"}) is None
    assert meaning.choose("x", opts, ask=lambda b, s, c: {"meaning": "delete everything"}) is None
    assert meaning.choose("x", opts, ask=lambda b, s, c: (_ for _ in ()).throw(RuntimeError("model down"))) is None
    assert meaning.choose("word " * 30, opts, ask=lambda b, s, c: {"meaning": "yes"}) is None           # too long: not asked


@pytest.fixture
def fake_model(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_PC_STATE_DIR", str(tmp_path / "pc-control"))      # never the real PC-plan ledger
    said = {}

    def choose(text, meanings, context="", cancelled=None, ask=None, max_words=25):
        said.setdefault("asked", []).append(text)
        return said.get("answers", {}).get(text)
    monkeypatch.setattr(meaning, "choose", choose)
    return said


def test_a_yes_in_their_own_words_approves_only_while_something_waits(fake_model):
    fake_model["answers"] = {"sounds good": "yes", "hold off on that": "no"}
    uid = "meaning-test"
    assert approvals.meant(uid, "sounds good") is None and fake_model.get("asked") is None      # nothing waiting: no model call
    approvals.request(uid, "k1", "close microsoft edge")
    approvals.on_user_message(uid, "sounds good")
    assert approvals.consume(uid, "k1")
    approvals.request(uid, "k2", "close paint")
    approvals.on_user_message(uid, "hold off on that")
    assert not approvals.consume(uid, "k2")                                                    # their no cancelled it
    assert approvals.meant(uid, "yes") == "yes" and approvals.meant(uid, "no") == "no"          # the lists still answer first


def test_save_answers_in_their_own_words(fake_model, monkeypatch):
    fake_model["answers"] = {"keep it": "save", "nah toss it": "dont_save"}
    monkeypatch.setattr(saveprompt, "answer", lambda app, save, hwnd=0: "SAVED" if save else "DISCARDED")
    for said, out in (("keep it", "SAVED"), ("nah toss it", "DISCARDED")):
        saveprompt.remember("meaning-test", "paint")
        assert saveprompt.reply("meaning-test", said) == out


def test_close_and_save_said_any_way(fake_model, monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda n: "paint" if n.casefold() == "paint" else None)
    fake_model["answers"] = {"shut paint down but keep my drawing": "close_save", "close it, I don't need it saved": "close_dont_save"}
    assert saveprompt.command("shut paint down but keep my drawing") == (True, "paint")
    assert saveprompt.command("close it, I don't need it saved") == (False, "")
    fake_model["asked"] = []
    assert saveprompt.command("close the file explorer") is None and fake_model["asked"] == []   # no keep/lose words: no model call


def test_the_voice_loop_understands_stop_and_goodbye(fake_model):
    import main
    fake_model["answers"] = {"zip it": "stop", "alright that's all for now": "dismiss"}
    assert main.echo_free_meaning("zip it") == "stop"
    assert main.echo_free_meaning("alright that's all for now") == "dismiss"
    fake_model["asked"] = []
    for request in ("open youtube", "what's on my calendar", "play some music", "stop", "thanks"):
        assert main.echo_free_meaning(request) is None
    assert fake_model["asked"] == []                                                            # requests never wait on the model


def test_go_on_never_waits_on_the_model_before_he_picks_up(fake_model):
    """'go on' / 'continue' after a cut-off reply is answered by the brain at once (Brain._resume): asking the local
    model first whether it means 'stop' was dead air before every resume."""
    import main
    fake_model["asked"] = []
    for said in ("go on", "okay go on", "continue", "what were you saying", "kontinye"):
        assert main.echo_free_meaning(said) is None
    assert fake_model["asked"] == []


def test_a_no_in_their_own_words_declines_a_plan_shown_for_review(fake_model, monkeypatch, tmp_path):
    from core import pcledger
    pcledger.create("plan", {"task": "take over settings and go to the bluetooth page", "plan": {"steps": []}})
    assert pcledger.pending()
    fake_model["answers"] = {"nah leave it": "no", "sounds good": "yes"}
    uid = "meaning-ledger"
    assert approvals.meant(uid, "nah leave it") == "no" and approvals.meant(uid, "sounds good") == "yes"
    pcledger.decline(pcledger.pending()[0]["id"])
    assert pcledger.pending() == [] and approvals.meant(uid, "maybe later") is None     # nothing waits: no model call
