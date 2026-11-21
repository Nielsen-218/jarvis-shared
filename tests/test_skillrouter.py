"""The local model translates unrecognised speech into a stored skill; the menu is the real store; it can only choose from it."""
from types import SimpleNamespace

import pytest

from core import skillrouter
from tools import pcskills


def book(subject, title, *, tier="T0", params=(), stale=False, kind="shell"):
    return SimpleNamespace(subject=subject, title=title, keywords=[], tier=tier, params=list(params), stale=stale, kind=kind)


STORE = [
    book("pc_disk_free", "how much free space is on my C drive"),
    book("gfx_gpu_name", "which graphics card does this PC have"),
    book("conv_casual_space", "tell me whats left on my c drive pls"),
    book("pc_ram", "how much RAM does this computer have"),
    book("file_count", "count the files in a folder", params=["dir"]),
    book("delete_logs", "delete old logs", tier="T2"),
    book("old_gpu", "what is the old gpu name", stale=True),
]


@pytest.fixture(autouse=True)
def fake_store(monkeypatch):
    monkeypatch.setattr(pcskills, "skills", lambda: SimpleNamespace(playbooks=SimpleNamespace(all=lambda: STORE)))


def test_the_menu_is_only_proven_fresh_read_only_skills_that_need_no_value_and_no_casual_duplicates():
    assert [m[0] for m in skillrouter.menu()] == ["pc_disk_free", "gfx_gpu_name", "pc_ram"]


def test_the_models_choice_selects_the_matching_stored_playbook(monkeypatch):
    seen = {}

    def ask(text, options, cancelled):
        seen["ids"] = [o[0] for o in options]
        return "gfx_gpu_name"
    monkeypatch.setattr(skillrouter, "_ask", ask)
    monkeypatch.setattr(skillrouter, "_confirm", lambda *a: True)
    chosen = skillrouter.route("what's pushing the pixels in this machine")
    assert chosen.subject == "gfx_gpu_name"
    assert seen["ids"] == ["pc_disk_free", "gfx_gpu_name", "pc_ram"]


def test_a_none_choice_or_a_model_failure_means_normal_chat(monkeypatch):
    monkeypatch.setattr(skillrouter, "_ask", lambda *a: None)
    assert skillrouter.route("tell me a joke") is None


def test_the_phone_note_is_stripped_and_small_talk_costs_no_model_call(monkeypatch):
    monkeypatch.setattr(skillrouter, "_ask", lambda *a: (_ for _ in ()).throw(AssertionError("no model call for social talk")))
    assert skillrouter.route("thanks, that's great") is None
    seen = []
    monkeypatch.setattr(skillrouter, "_ask", lambda text, o, c: seen.append(text))
    skillrouter.route("[Sent from their Jarvis phone app. Keep replies short.] am I low on room")
    assert seen == ["am I low on room"]


def test_long_requests_and_links_are_left_to_normal_routing(monkeypatch):
    monkeypatch.setattr(skillrouter, "_ask", lambda *a: (_ for _ in ()).throw(AssertionError("must not be asked")))
    assert skillrouter.route("word " * 40) is None
    assert skillrouter.route("read https://example.com for me") is None


def test_the_model_can_only_pick_ids_that_are_on_the_menu(monkeypatch):
    class Reply:
        content = [SimpleNamespace(type="text", text='{"skill": "format_the_disk"}')]
    from core import intelligence
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    monkeypatch.setattr(intelligence, "request", lambda *a, **k: Reply())
    assert skillrouter._ask("wipe it", [("pc_ram", "how much RAM", None)], lambda: False) is None


def test_a_pick_the_check_rejects_is_dropped_and_the_next_best_is_tried(monkeypatch):
    picks = iter(["pc_ram", "pc_disk_free"])
    asked = []

    def ask(text, options, cancelled):
        asked.append([o[0] for o in options])
        return next(picks)
    monkeypatch.setattr(skillrouter, "_ask", ask)
    monkeypatch.setattr(skillrouter, "_confirm", lambda text, title, c: "free space" in title)
    assert skillrouter.route("am I low on room").subject == "pc_disk_free"
    assert "pc_ram" not in asked[1], "the rejected skill is not offered again"


def test_two_rejected_picks_mean_normal_chat_not_a_wrong_answer(monkeypatch):
    monkeypatch.setattr(skillrouter, "_ask", lambda text, options, c: options[0][0])
    monkeypatch.setattr(skillrouter, "_confirm", lambda *a: False)
    assert skillrouter.route("I need to crunch some numbers") is None


def test_a_strict_word_match_is_kept_unless_the_model_says_it_asks_something_else(monkeypatch):
    from core import intelligence
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    b = book("pc_service_state", "is the Windows Event Log service running")
    monkeypatch.setattr(skillrouter, "_confirm", lambda *a: False)
    assert skillrouter.vet("which edition of windows am I running", b) is False
    monkeypatch.setattr(skillrouter, "_confirm", lambda *a: None)
    assert skillrouter.vet("is the event log running", b) is True
    monkeypatch.setattr(skillrouter, "_confirm", lambda *a: True)
    assert skillrouter.vet("is the event log running", b) is True


@pytest.mark.parametrize("sentence", ["how do I free up disk space on windows", "should I buy more ram", "why is my disk so full", "what is the best graphics card"])
def test_advice_and_how_to_questions_never_reach_the_model(monkeypatch, sentence):
    monkeypatch.setattr(skillrouter, "_ask", lambda *a: (_ for _ in ()).throw(AssertionError("must not be asked")))
    assert skillrouter.route(sentence) is None


def test_polite_requests_still_reach_the_model(monkeypatch):
    monkeypatch.setattr(skillrouter, "_ask", lambda text, options, c: "pc_ram")
    monkeypatch.setattr(skillrouter, "_confirm", lambda *a: True)
    assert skillrouter.route("could you tell me how much memory this has").subject == "pc_ram"
