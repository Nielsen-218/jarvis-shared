"""Jarvis's models stay warm, step aside for a game, and never fight practice for the graphics card (core/warmth)."""
import pytest

from core import warmth


@pytest.fixture
def card(monkeypatch):
    state = {"resident": {}, "free": 12.9, "posts": []}   # the real card with only Whisper loaded
    monkeypatch.setattr(warmth, "jarvis_models", lambda: ["llama3.2:3b", "gemma4:12b-it-qat"])
    monkeypatch.setattr(warmth, "resident", lambda: dict(state["resident"]))
    monkeypatch.setattr(warmth, "room_for", lambda gb: state["free"] >= gb + warmth.HEADROOM_GB)
    monkeypatch.setattr(warmth, "_note", lambda text: None)

    def post(path, body, timeout=120):
        state["posts"].append((body["model"], body.get("keep_alive")))
        if body.get("keep_alive") == 0:
            state["resident"].pop(body["model"], None)
            state["free"] += {"gpt-oss:20b": 8.0}.get(body["model"], 8.0 if "gemma" in body["model"] else 4.1)
        else:
            state["resident"][body["model"]] = 1
            state["free"] -= 8.0 if "gemma" in body["model"] else 4.1
        return True
    monkeypatch.setattr(warmth, "_post", post)
    monkeypatch.setitem(warmth._state, "game", False)
    return state


def test_missing_models_are_loaded_back_and_kept_for_hours(card):
    assert warmth.warm() == ["llama3.2:3b", "gemma4:12b-it-qat"]
    assert all(k == warmth.KEEP for _, k in card["posts"]) and warmth.KEEP == "8h"
    assert warmth.warm() == []                                      # already warm: nothing to do


def test_a_game_gets_the_memory_and_only_the_fast_model_stays(card):
    warmth.warm()
    warmth.game_started()
    assert "gemma4:12b-it-qat" not in card["resident"] and "llama3.2:3b" in card["resident"]
    assert warmth.keep_alive("gemma4:12b-it-qat") == warmth.IN_GAME_KEEP and warmth.keep_alive("llama3.2:3b") == "8h"
    assert warmth.warm() == []                                      # in game: the planner is not loaded back
    warmth.game_ended()
    assert "gemma4:12b-it-qat" in card["resident"] and warmth.keep_alive("gemma4:12b-it-qat") == "8h"


def test_practice_holding_the_card_is_never_fought_and_handback_reloads_at_once(card):
    card["resident"] = {"gpt-oss:20b": 1}
    card["free"] = 1.5                                              # the learner fills the card
    assert warmth.warm() == [] and card["posts"] == []
    card["free"] = 4.9                                              # learner (12.7 GB) unloads -> 12.9 free again
    loaded = warmth.handback("gpt-oss:20b")                         # owner back: learner unloaded, Jarvis loaded
    assert ("gpt-oss:20b", 0) in card["posts"] and loaded == ["llama3.2:3b", "gemma4:12b-it-qat"]


def test_requests_carry_the_keepers_keep_alive(monkeypatch):
    from core import intelligence
    monkeypatch.setattr(warmth, "jarvis_models", lambda: ["llama3.2:3b", "gemma4:12b-it-qat"])
    monkeypatch.setitem(warmth._state, "game", True)
    body = {"model": "gemma4:12b-it-qat", "messages": [{"role": "user", "content": "hi"}], "temperature": 0, "max_tokens": 5}
    assert intelligence._ollama_body(body)["keep_alive"] == warmth.IN_GAME_KEEP
    monkeypatch.setitem(warmth._state, "game", False)
    assert intelligence._ollama_body(body)["keep_alive"] == "8h"


@pytest.mark.parametrize("said,chat", [
    ("How you doing?", True), ("Hey Jarvis, how are you?", True), ("Good morning, Jarvis", True), ("Thank you, sir.", True),
    ("lol", True), ("ok", False), ("all right", False), ("yes", False), ("cool", False), ("thanks, now close notepad", False),
    ("how are you doing on the download", False), ("what's up with my wifi", False), ("hey open steam", False)])
def test_plain_small_talk_skips_the_planner(said, chat):
    """2026-11-08: 'How you doing?' spent 2.9 s warm / 8.8 s cold in the planner to be told it was chat."""
    from core import gateway
    hit = gateway.exact(said)
    assert (hit is not None and hit.kind == "chat") is chat, said


def test_whats_on_my_screen_answers_from_the_window_list_and_reads_text_in_the_background(monkeypatch):
    """2026-11-08: 'which apps are open' waited 27.5 s for a full-screen OCR that was never spoken."""
    import threading
    from core import pcstate, pcverbs
    from tools import screenread
    done = threading.Event()
    monkeypatch.setattr(pcstate, "spoken", lambda: "In front: Claude. Also open: Edge, Discord.")
    monkeypatch.setattr(screenread, "read_screen", lambda app="", find_text="": done.set() or "text")
    out = pcverbs.read_screen({"app": ""})
    assert out["detail"] == "In front: Claude. Also open: Edge, Discord." and out["verified"]
    assert done.wait(2)                                              # the OCR still runs, off the turn, for follow-ups


def test_a_sentence_the_planner_judged_to_be_talk_gets_no_pc_tools():
    """Live 2026-11-08: 'I could have bought about six. I ain't saying I'm rich.' -> chat -> the reply model still called
    list_windows and said 'Claude is open on your PC'."""
    from core import gateway, intelligence
    from tools import load_all, registry
    load_all()
    tools = [{"name": n, "input_schema": {}} for n in ("list_windows", "read_screen", "open_app", "get_weather", "search_web_free")]
    said = "But you know I could have bought about six. I ain't saying I'm rich."
    assert {t["name"] for t in intelligence._without_pc_tools_if_chat(said, tools)} == {t["name"] for t in tools}   # not judged yet
    gateway.note_chat("[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] " + said)
    kept = {t["name"] for t in intelligence._without_pc_tools_if_chat(said, tools)}
    assert kept == {"get_weather", "search_web_free"} & set(registry._TOOLS) | ({"get_weather", "search_web_free"} - set(registry._TOOLS))
    assert "list_windows" not in kept and "open_app" not in kept
    assert len(intelligence._without_pc_tools_if_chat("open notepad", tools)) == len(tools)          # another sentence: untouched


def test_the_keeper_leaves_the_card_to_a_running_practice_cycle(card, monkeypatch, tmp_path):
    """2026-11-08: the learner's model unloads between calls, the keeper saw free room and reloaded 12 GB; they took turns
    evicting each other. The watcher's marker stops that; the handback after the cycle still loads at once."""
    from core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    (tmp_path / "learning").mkdir()
    marker = tmp_path / "learning" / warmth.PRACTICE_MARKER
    marker.write_text("now")
    assert warmth.practising() and warmth.warm("reloaded") == [] and card["posts"] == []
    assert warmth.warm("practice stopped") == ["llama3.2:3b", "gemma4:12b-it-qat"]     # the handback is not blocked
    import os
    os.utime(marker, (0, 0))                                                          # a crash leftover: ignored
    assert not warmth.practising()
