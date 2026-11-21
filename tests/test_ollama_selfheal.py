"""Ollama on Windows crashes its runner instead of evicting a model pinned in VRAM; the brain must recover."""
import io
import json
import urllib.error

from core import intelligence


class FakeOpener:
    def __init__(self):
        self.chat_calls, self.unloaded = 0, []

    def open(self, req, timeout=0):
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/models"):
            return io.BytesIO(json.dumps({"data": [{"id": "llama3.1:8b"}]}).encode())
        if url.endswith("/api/ps"):
            return io.BytesIO(json.dumps({"models": [{"name": "qwen2.5:7b-instruct"}, {"name": "llama3.1:8b"}]}).encode())
        if url.endswith("/api/generate"):
            self.unloaded.append(json.loads(req.data)["model"])
            return io.BytesIO(b"{}")
        if url.endswith("/api/chat"):
            self.chat_calls += 1
            if self.chat_calls == 1:
                raise urllib.error.HTTPError(url, 500, "x", {}, io.BytesIO(
                    b'{"error":"llama-server process has terminated: exit status 0xc0000005"}'))
            return io.BytesIO(json.dumps({"model": "llama3.1:8b", "message": {"role": "assistant", "content": "Hello, sir."},
                                          "done": True, "done_reason": "stop"}).encode())
        raise AssertionError(url)


def test_a_crashed_runner_evicts_the_squatter_and_retries(monkeypatch):
    opener = FakeOpener()
    monkeypatch.setattr(intelligence, "uses_ollama", lambda role: True)
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", opener))
    monkeypatch.setattr(intelligence, "_switch_local_provider", lambda role, cancelled: None)
    monkeypatch.setattr(intelligence, "_release_pressure", lambda role: False)
    monkeypatch.setattr(intelligence.time, "sleep", lambda s: None)
    monkeypatch.setenv("JARVIS_LOCAL_FAST_MODEL", "llama3.1:8b")
    out = intelligence.complete_local({"role": "fast", "messages": [{"role": "user", "content": "hi"}], "tools": []})
    assert opener.chat_calls == 2 and opener.unloaded == ["qwen2.5:7b-instruct"]
    assert out["choices"][0]["message"]["content"] == "Hello, sir."


def test_studio_warms_the_real_brain_model_not_a_pinned_stale_one(monkeypatch):
    from tools import studio
    calls = []
    monkeypatch.setattr(studio, "_ollama", lambda keep, model="qwen2.5:7b-instruct", timeout=10: calls.append((keep, model)))
    monkeypatch.setattr(intelligence, "uses_ollama", lambda role: True)
    monkeypatch.setenv("JARVIS_LOCAL_FAST_MODEL", "llama3.1:8b")
    studio._warm_brain()
    assert calls == [("15m", "llama3.1:8b")]
