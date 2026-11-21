"""A thinking build answers in the open: Ollama's 'think' field is sent, as False, only to models that reason hidden."""
from core import intelligence


def test_think_false_only_for_thinking_builds(monkeypatch):
    body = {"model": "gemma4:12b-it-qat", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.2, "max_tokens": 10}
    assert intelligence._ollama_body(dict(body))["think"] is False
    for plain in ("llama3.2:3b", "qwen3:4b-instruct", "qwen3-vl:8b-instruct", "llama3.1:8b"):
        assert "think" not in intelligence._ollama_body(dict(body, model=plain)), plain
    assert intelligence.thinks("qwen3:14b") and intelligence.thinks("gpt-oss:20b") and not intelligence.thinks("")
    monkeypatch.setenv("JARVIS_OLLAMA_THINKING", "mymodel:7b, other")
    assert intelligence.thinks("mymodel:7b") and not intelligence.thinks("mymodel:8b")
