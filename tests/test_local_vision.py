"""A screenshot or photo in the turn goes to the local VISION model; text turns never carry one.

Found live: "Take a look at my screen" ran look_at_screen, and the picture it returned was dropped on the way to
Ollama's text model (OpenAI-style tool messages carry text only), so llama described a screen it had never seen.
These pin the route: the picture rides as tool evidence right after the results, tagged so it never counts as
something they said; a turn with a picture goes natively to the vision model whichever tier asked; older pictures
are taken out for the text models; an LM Studio setup keeps sending images to its own model as before.
"""
import base64
import io
import json
import shutil

import pytest

import tools
from core import contextfit, intelligence
from core.intelligence import LocalFailure, complete_local, messages_for_local
from tools import registry

tools.load_all()
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 64).decode()
SHOT = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG.split(",", 1)[1]}}


def _looked(question="What is on my screen?"):
    """The history after look_at_screen ran: question, the call, its result (picture + caption)."""
    return [{"role": "user", "content": question},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "screen-1", "name": "look_at_screen",
                                               "input": {"all_monitors": False}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "screen-1",
                                          "content": [SHOT, {"type": "text", "text": "Screenshot (1568x882)."}]}]}]


# -- the picture reaches the model as tool evidence, not as their words ----------------------------------------------
def test_a_tool_picture_rides_right_after_its_result_as_tagged_evidence():
    messages = messages_for_local("You are JARVIS.", _looked())
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "user"]
    assert messages[3] == {"role": "tool", "tool_call_id": "screen-1", "content": json.dumps([{"type": "text", "text": "Screenshot (1568x882)."}])}
    evidence = messages[-1]["content"]
    assert evidence[0]["type"] == "text" and evidence[0]["text"].startswith("<tool-images>")
    assert "not something they sent" in evidence[0]["text"]
    assert evidence[1] == {"type": "image_url", "image_url": {"url": PNG}}


def test_the_evidence_note_is_not_what_they_said():
    history = _looked("Is there a browser open on my screen?")
    assert intelligence.last_user_text(history) == "Is there a browser open on my screen?"
    local = messages_for_local("sys", history)[1:]                      # as contextfit and select_tools see it
    assert contextfit._question(local[-1]) is False                      # the picture starts no new exchange
    assert len(contextfit._exchanges(local)) == 1
    assert "<tool-images>" not in intelligence.heard_in([{"role": "user", "content": [
        {"type": "text", "text": intelligence._TOOL_IMAGES_NOTE}]}])


def test_normal_text_turns_are_unchanged():
    assert messages_for_local("sys", [{"role": "user", "content": "Hello"}])[-1] == {"role": "user", "content": "Hello"}
    plain = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "10:42"}]}]
    assert messages_for_local("sys", plain)[1:] == [{"role": "tool", "tool_call_id": "t1", "content": "10:42"}]


# -- which model gets the request --------------------------------------------------------------------------------------
class _Ollama:
    """Stands in for the opener of either local server: answers the inventory and records every chat request."""

    def __init__(self, models):
        self.models, self.chats = models, []

    def open(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/api/v1/models"):                               # LM Studio's own inventory
            body = {"models": [{"type": "llm", "key": "gemma-4-12b-it-qat", "loaded_instances": [{"id": intelligence.FAST}]}]}
        elif url.endswith("/models"):
            body = {"data": [{"id": m} for m in self.models]}
        else:
            sent = json.loads(request.data)
            self.chats.append((url, sent))
            body = ({"model": sent["model"], "message": {"role": "assistant", "content": "A browser window."},
                     "done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 3}
                    if url.endswith("/api/chat") else
                    {"model": sent["model"], "choices": [{"finish_reason": "stop",
                                                          "message": {"content": "A browser window."}}]})
        return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_MODEL", "llama3.2:3b")
    monkeypatch.setenv("JARVIS_LOCAL_ADVANCED_MODEL", "llama3.1:8b")
    monkeypatch.setenv("JARVIS_LOCAL_VISION_MODEL", "qwen3-vl:8b-instruct")   # a separate eyes model (one brain: below)
    ollama = _Ollama(["llama3.2:3b", "llama3.1:8b", "qwen3-vl:8b-instruct"])
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", ollama))
    monkeypatch.setattr(intelligence, "_endpoint", lambda: ("http://127.0.0.1:11434/v1", ollama))
    monkeypatch.setattr(intelligence, "_release_pressure", lambda role: False)
    # never run this PC's real `lms` (a leftover CLI ran `lms unload` for real and failed the gate, 2026-11-19)
    real_which = shutil.which
    monkeypatch.setattr(intelligence.shutil, "which", lambda name, *a, **k: None if name == "lms" else real_which(name, *a, **k))
    return ollama


def _payload(role, history):
    return {"role": role, "messages": messages_for_local("sys", history), "tools": [
        {"type": "function", "function": {"name": "read_screen", "description": "", "parameters": {"type": "object", "properties": {}}}}]}


@pytest.mark.parametrize("role", ["fast", "advanced"])
def test_a_turn_with_a_picture_goes_to_the_vision_model_natively(local, role):
    reply = complete_local(_payload(role, _looked()))
    (url, sent), = local.chats
    assert url.endswith("/api/chat") and sent["model"] == "qwen3-vl:8b-instruct"
    assert sent["messages"][-1]["images"] == [PNG.split(",", 1)[1]]      # Ollama's native image field
    assert sent["messages"][-1]["content"].startswith("<tool-images>")
    assert sent["tools"] and sent["options"]["num_ctx"] == 16384       # it may still call read_screen for the text
    assert reply["choices"][0]["message"]["content"] == "A browser window."


def test_a_phone_photo_in_their_own_message_goes_to_the_vision_model_too(local):
    history = [{"role": "user", "content": [SHOT, {"type": "text", "text": "What's this a picture of?"}]}]
    complete_local(_payload("fast", history))
    (_, sent), = local.chats
    assert sent["model"] == "qwen3-vl:8b-instruct" and sent["messages"][-1]["images"]


def test_an_explicit_configured_ollama_text_model_is_allowed(local, monkeypatch):
    """Model choice is configuration plus installed-inventory validation, not a source allowlist."""
    monkeypatch.setenv("JARVIS_LOCAL_FAST_MODEL", "qwen3-vl:8b-instruct")
    complete_local(_payload("fast", [{"role": "user", "content": "Hello"}]))
    (_, sent), = local.chats
    assert sent["model"] == "qwen3-vl:8b-instruct"


def test_an_older_picture_is_taken_out_of_a_text_turn(local):
    history = _looked() + [{"role": "assistant", "content": "A browser window with a map, sir."},
                           {"role": "user", "content": "Thanks. What's the capital of Peru?"}]
    complete_local(_payload("fast", history))
    (_, sent), = local.chats
    assert sent["model"] == "llama3.2:3b"                                # the text model, not the vision one
    assert not any("images" in m for m in sent["messages"])
    gone = [m for m in sent["messages"] if m["role"] == "user"][-2]
    assert gone["content"] == intelligence._IMAGES_GONE                 # not "analyze the attached image"


def test_a_follow_up_about_the_picture_looks_at_it_again(local):
    """"Is there a web browser open in it?": the text model, picture gone, answered "Yes" from nothing."""
    history = _looked() + [{"role": "assistant", "content": "A code editor with a file list, sir."},
                           {"role": "user", "content": "Is there a web browser open in it?"}]
    complete_local(_payload("fast", history))
    (_, sent), = local.chats
    assert sent["model"] == "qwen3-vl:8b-instruct"
    pictured = [m for m in sent["messages"] if "images" in m]
    assert len(pictured) == 1 and pictured[0]["content"].startswith("<tool-images>")
    # much later it's gone for good, question about the view or not
    long_after = history + [{"role": "assistant", "content": "No, sir."}] * 4 + [{"role": "user", "content": "x"}] * 4 \
        + [{"role": "user", "content": "What colour is the window?"}]
    local.chats.clear()
    complete_local(_payload("fast", long_after))
    (_, sent), = local.chats
    assert sent["model"] == "llama3.2:3b" and not any("images" in m for m in sent["messages"])


def test_the_configured_vision_model_and_only_a_local_data_url_are_accepted(local, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_VISION_MODEL", "llava:7b")
    with pytest.raises(LocalFailure, match="configured_model_unavailable"):   # not installed on this Ollama
        complete_local(_payload("fast", _looked()))
    remote = {"type": "image_url", "image_url": {"url": "https://example.com/shot.png"}}
    payload = _payload("fast", _looked())
    payload["messages"][-1]["content"][1] = remote
    with pytest.raises(LocalFailure, match="remote_image_refused"):
        complete_local(payload)
    huge = dict(payload, messages=payload["messages"][:-1] + [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"\0" * (8 * 1024 * 1024 + 1)).decode()}}]}])
    with pytest.raises(LocalFailure, match="invalid_image_size"):
        complete_local(huge)


def test_an_lm_studio_setup_keeps_sending_pictures_to_its_own_model(local, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "lmstudio")
    lm = _Ollama([intelligence.FAST, intelligence.ADVANCED])
    monkeypatch.setattr(intelligence, "_endpoint", lambda: ("http://127.0.0.1:1234/v1", lm))
    monkeypatch.delenv("JARVIS_LOCAL_FAST_MODEL")
    assert intelligence.vision_model() is None
    complete_local(_payload("fast", _looked()))
    (url, sent), = lm.chats
    assert url.endswith("/chat/completions") and sent["model"] == intelligence.FAST
    assert sent["messages"][-1]["content"][1]["type"] == "image_url"   # OpenAI style, as before
    assert not local.chats


def test_the_advanced_tier_is_lm_studios_when_lm_studio_is_a_different_server(local, monkeypatch):
    lm = _Ollama(["llama3.1:8b"])
    monkeypatch.setattr(intelligence, "_endpoint", lambda: ("http://127.0.0.1:1234/v1", lm))
    assert not intelligence.uses_ollama("advanced") and intelligence.uses_ollama("fast")
    complete_local(_payload("advanced", [{"role": "user", "content": "Plan my week"}]))
    (url, sent), = lm.chats
    assert url.endswith("/chat/completions") and sent["model"] == "llama3.1:8b" and sent["reasoning_effort"] == "low"
    assert not local.chats


def test_the_advanced_tier_goes_native_when_lm_studio_is_the_ollama_server(local):
    """The owner's .env: JARVIS_LOCAL_URL is Ollama's own OpenAI endpoint. Through it the request carried LM Studio's
    reasoning_effort and Ollama answered 400 "does not support thinking" in 0.1 s: the advanced tier never ran."""
    assert intelligence.uses_ollama("advanced")
    complete_local(_payload("advanced", [{"role": "user", "content": "Plan my week"}]))
    (url, sent), = local.chats
    assert url.endswith("/api/chat") and sent["model"] == "llama3.1:8b"
    assert "reasoning_effort" not in sent and sent["options"]["num_ctx"] == 16384   # the whole prompt fits
    assert sent["options"]["num_predict"] == 3072


# -- the model's spelling of "no value" ----------------------------------------------------------------------------
def test_null_written_as_a_word_means_no_value_for_an_optional_argument():
    assert registry.coerce("read_screen", {"app": "null", "find_text": "None"}) == {}
    assert registry.coerce("read_screen", {"app": "notepad.exe", "find_text": "null"}) == {"app": "notepad.exe"}
    assert registry.coerce("open_app", {"app": "null"}) == {"app": "null"}   # required: never dropped or invented


def test_one_brain_sees_too(local, monkeypatch):
    """2026-11-09, owner: "one good brain". By default the brain is qwen3.5:9b and it also looks at the picture: same
    model, same num_ctx, thinking off, so a screenshot turn never loads a second model or reloads this one."""
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_LOCAL_VISION_MODEL"):
        monkeypatch.delenv(var, raising=False)
    local.models[:] = ["qwen3.5:9b"]
    made_room = []
    monkeypatch.setattr(intelligence, "_make_room", lambda *a: made_room.append(a) or [])
    assert intelligence.model("fast") == intelligence.model("advanced") == intelligence.vision_model() == "qwen3.5:9b"
    complete_local(_payload("fast", _looked()))
    complete_local(_payload("advanced", [{"role": "user", "content": "Hello"}]))
    (_, seen), (_, said) = local.chats
    assert seen["model"] == said["model"] == "qwen3.5:9b" and seen["messages"][-1]["images"]
    assert seen["think"] is False and said["think"] is False
    assert seen["options"]["num_ctx"] == said["options"]["num_ctx"] == 16384
    assert made_room == []                                       # nothing to swap: the eyes are the brain


def test_a_leftover_lms_that_fails_never_fails_the_turn(local, monkeypatch):
    """LM Studio uninstalled, its `lms` CLI left on PATH: `lms unload` fails, the vision turn still answers."""
    import subprocess
    monkeypatch.setattr(intelligence.shutil, "which", lambda name, *a, **k: "C:/fake/lms.exe" if name == "lms" else None)
    ran = []

    def failing(cmd, **kw):
        ran.append(cmd)
        raise subprocess.CalledProcessError(1, cmd, b"", b"LM Studio is not installed")
    monkeypatch.setattr(intelligence.subprocess, "run", failing)
    complete_local(_payload("fast", _looked()))
    assert local.chats and local.chats[0][1]["model"] == "qwen3-vl:8b-instruct"
