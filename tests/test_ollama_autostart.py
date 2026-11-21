"""A closed Ollama is started by Jarvis himself, once, and waited for; a server that answers is left alone."""
from core import intelligence


def test_a_server_that_answers_is_not_restarted(monkeypatch):
    started = []
    monkeypatch.setattr(intelligence, "_ollama_started_at", 0.0)
    assert intelligence.ensure_ollama_up("http://127.0.0.1:11434/v1", None, probe=lambda: True, start=started.append, listening=lambda: False) is True
    assert started == []


def test_a_silent_server_is_started_and_waited_for(monkeypatch):
    monkeypatch.setattr(intelligence, "_ollama_started_at", 0.0)
    monkeypatch.setattr(intelligence.os, "name", "nt")
    monkeypatch.setattr(intelligence, "_ollama_exe", lambda: r"C:\x\ollama.exe")
    monkeypatch.setattr(intelligence.time, "sleep", lambda s: None)
    answers = iter([False, False, False, True])
    started = []
    ok = intelligence.ensure_ollama_up("http://127.0.0.1:11434/v1", None, wait=5, probe=lambda: next(answers, True), start=started.append, listening=lambda: False)
    assert ok is True and started == [r"C:\x\ollama.exe"]


def test_it_is_not_spawned_again_within_two_minutes(monkeypatch):
    monkeypatch.setattr(intelligence.os, "name", "nt")
    monkeypatch.setattr(intelligence, "_ollama_exe", lambda: r"C:\x\ollama.exe")
    monkeypatch.setattr(intelligence.time, "sleep", lambda s: None)
    monkeypatch.setattr(intelligence, "_ollama_started_at", 0.0)
    started = []
    assert intelligence.ensure_ollama_up("http://127.0.0.1:11434/v1", None, wait=0.1, probe=lambda: False, start=started.append, listening=lambda: False) is False
    assert intelligence.ensure_ollama_up("http://127.0.0.1:11434/v1", None, wait=0.1, probe=lambda: False, start=started.append, listening=lambda: False) is False
    assert started == [r"C:\x\ollama.exe"], "one spawn, then it waits two minutes before trying again"


def test_without_an_installed_ollama_nothing_is_run(monkeypatch):
    monkeypatch.setattr(intelligence.os, "name", "nt")
    monkeypatch.setattr(intelligence, "_ollama_started_at", 0.0)
    monkeypatch.setattr(intelligence, "_ollama_exe", lambda: None)
    started = []
    assert intelligence.ensure_ollama_up("http://127.0.0.1:11434/v1", None, probe=lambda: False, start=started.append, listening=lambda: False) is False
    assert started == []
