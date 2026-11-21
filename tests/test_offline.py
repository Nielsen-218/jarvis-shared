"""The offline fallback brain (local model) kicks in when the cloud API is unreachable, and returns clean text."""
import json

import anthropic
import pytest


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload

    def __iter__(self):
        return iter(self._payload.splitlines(keepends=True))


def _chat(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return ("data: " + json.dumps({"choices": [{"delta": msg, "finish_reason": None}]})
            + "\n\ndata: [DONE]\n").encode()


@pytest.fixture()
def local(monkeypatch):
    from core import offline

    offline._probe.update(at=0.0, up=False, model="")
    reply = {"body": _chat("We need new brakes. The mechanic quoted 340 dollars.")}

    def fake_urlopen(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/models"):
            return _Resp(json.dumps({"data": [{"id": "qwen/qwen3.5-9b"}]}).encode())
        return _Resp(reply["body"])

    monkeypatch.setattr(offline.urllib.request, "urlopen", fake_urlopen)
    return offline, reply


def test_offline_streams_sentences(local):
    offline, _ = local
    assert offline.available() and offline.model_name() == "qwen/qwen3.5-9b"
    said = []
    ok = offline.stream_reply("sys", [{"role": "user", "content": "what about the car?"}], said.append, lambda: False)
    assert ok and said == ["We need new brakes.", "The mechanic quoted 340 dollars."]


def test_unwraps_tool_call_json(local):
    offline, reply = local
    # Llama-with-tool-template wraps the answer in JSON; we pull the spoken text back out.
    reply["body"] = _chat('{"name": "speak", "parameters": {"text": "Of course, I can still help offline."}}')
    said = []
    assert offline.stream_reply("sys", [{"role": "user", "content": "hi"}], said.append, lambda: False)
    assert said == ["Of course, I can still help offline."]
    # answer delivered only as a tool_call
    reply["body"] = _chat(None, [{"function": {"name": "speak", "arguments": '{"message": "Hello there."}'}}])
    said = []
    assert offline.stream_reply("sys", [{"role": "user", "content": "hi"}], said.append, lambda: False)
    assert said == ["Hello there."]


def test_brain_falls_back_to_local_when_offline(local, monkeypatch):
    from brain import llm

    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    monkeypatch.setattr(b, "_run", lambda *a, **k: (_ for _ in ()).throw(anthropic.APIConnectionError(request=None)))
    said = []
    b.ask("what did the mechanic say about the car?", said.append)
    assert "local model on your PC" in said[0]
    assert "The mechanic quoted 340 dollars." in said
    assert b.messages[-1]["role"] == "assistant" and "340" in b.messages[-1]["content"]


def test_cloud_error_when_local_also_down(monkeypatch):
    from brain import llm

    monkeypatch.setattr(llm, "explain_error", lambda e: "CLOUD DOWN")
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    monkeypatch.setattr(b, "_run", lambda *a, **k: (_ for _ in ()).throw(anthropic.APIConnectionError(request=None)))
    from core import offline

    monkeypatch.setattr(offline, "available", lambda ttl=5.0: False)
    said = []
    b.ask("hello", said.append)
    assert said == ["CLOUD DOWN"]
