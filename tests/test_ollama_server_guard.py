"""A busy model server is not a dead one (live 2026-11-13: six "not running; started ollama serve" lines while the
same ollama.exe, started days before, kept serving). Only a port nobody listens on is restarted; the restart's
output is kept in machine-state/ollama; no test can stop or start the real server."""
import socket

import pytest

from core import intelligence

URL = "http://127.0.0.1:11434/v1"


@pytest.fixture
def nt(monkeypatch):
    monkeypatch.setattr(intelligence.os, "name", "nt")
    monkeypatch.setattr(intelligence, "_ollama_started_at", 0.0)
    monkeypatch.setattr(intelligence, "_busy_noted_at", -1e9)
    monkeypatch.setattr(intelligence, "_ollama_exe", lambda: r"C:\x\ollama.exe")
    monkeypatch.setattr(intelligence.time, "sleep", lambda s: None)
    rows = []
    monkeypatch.setattr(intelligence, "audit", lambda role, status, *a, **k: rows.append((role, status)))
    return rows


def test_a_busy_server_that_still_accepts_connections_is_not_restarted(nt):
    started, slept = [], []
    ok = intelligence.ensure_ollama_up(URL, None, probe=lambda: False, listening=lambda: True, start=started.append)
    assert ok is True and started == [], "someone is listening: a second `ollama serve` is never spawned"
    assert ("local", "ollama_busy_not_restarted") in nt


def test_a_busy_server_costs_one_probe_not_a_wait_loop(nt):
    probes = []
    intelligence.ensure_ollama_up(URL, None, probe=lambda: probes.append(1) or False, listening=lambda: True, start=lambda e: None)
    assert len(probes) == 1, "the request waits for a busy server itself; no 12 s polling first"


def test_a_closed_port_is_restarted_and_the_reason_is_recorded(nt):
    started = []
    answers = iter([False, False, True])
    ok = intelligence.ensure_ollama_up(URL, None, probe=lambda: next(answers, True), listening=lambda: False,
                                       start=started.append)
    assert ok is True and started == [r"C:\x\ollama.exe"]
    assert ("local", "ollama_started:port_closed") in nt


def test_a_port_that_opens_while_confirming_is_left_alone(nt):
    seen = iter([False, True])             # the first look says closed, the second (0.3 s later) says someone is there
    started = []
    ok = intelligence.ensure_ollama_up(URL, None, probe=lambda: False, listening=lambda: next(seen), start=started.append)
    assert ok is True and started == []


def test_the_wait_ends_as_soon_as_the_new_server_answers(nt):
    answers = iter([False, True])
    naps = []
    intelligence.time.sleep = naps.append
    intelligence.ensure_ollama_up(URL, None, probe=lambda: next(answers, True), listening=lambda: False, start=lambda e: None)
    assert sum(naps) <= 1.0


def test_listening_is_false_for_a_closed_port_and_true_for_an_open_one():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert intelligence._listening(f"http://127.0.0.1:{port}/v1") is True
    finally:
        server.close()
    assert intelligence._listening(f"http://127.0.0.1:{port}/v1") is False


def test_the_server_output_goes_to_a_rotating_log(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_OLLAMA_LOG_DIR", str(tmp_path))
    seen = {}

    class Proc:
        pass

    def popen(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        kw["stdout"].write(b"server output\n")
        return Proc()

    monkeypatch.setattr(intelligence, "_spawn_ollama", intelligence._REAL_SPAWN_OLLAMA)
    monkeypatch.setattr(intelligence.subprocess, "Popen", popen)
    intelligence._spawn_ollama(r"C:\x\ollama.exe", reason="port_closed")
    log = tmp_path / "server.log"
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "port_closed" in text and "server output" in text
    assert seen["cmd"] == [r"C:\x\ollama.exe", "serve"]
    assert seen["stdout"] is not intelligence.subprocess.DEVNULL and seen["stderr"] is intelligence.subprocess.STDOUT


def test_the_log_rotates_when_big(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_OLLAMA_LOG_DIR", str(tmp_path))
    (tmp_path / "server.log").write_bytes(b"x" * (intelligence.OLLAMA_LOG_LIMIT + 1))
    (tmp_path / "server.log.1").write_bytes(b"older")
    intelligence._rotate_ollama_log()
    assert (tmp_path / "server.log.1").stat().st_size > 1000 and (tmp_path / "server.log.2").read_bytes() == b"older"
    assert not (tmp_path / "server.log").exists()


def test_tests_cannot_start_or_stop_the_real_server():
    """conftest swaps the real spawn and kill for ones that refuse."""
    with pytest.raises(OSError):
        intelligence._spawn_ollama(r"C:\x\ollama.exe")
    with pytest.raises(OSError):
        intelligence._kill_ollama()


def test_a_failed_local_request_leaves_a_row_with_its_time(monkeypatch):
    rows = []
    monkeypatch.setattr(intelligence, "audit", lambda role, status, elapsed=0, tokens=None: rows.append((role, status, elapsed)))
    intelligence._audit_failure("fast", TimeoutError("timed out"), 18.2)
    assert rows == [("fast", "failed:TimeoutError", 18.2)]


def test_linux_host_does_not_spend_a_tcp_timeout_when_the_probe_fails(monkeypatch):
    monkeypatch.setattr(intelligence.os, "name", "posix")
    calls = []
    assert intelligence.ensure_ollama_up(URL, None, probe=lambda: False, listening=lambda: calls.append(1) or True) is False
    assert calls == []


def test_a_live_server_log_is_trimmed_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_OLLAMA_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(intelligence, "_log_checked_at", -1e9)
    log = tmp_path / "server.log"
    log.write_bytes(b"x" * (intelligence.OLLAMA_LOG_LIMIT + 1))
    monkeypatch.setattr(intelligence.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("held open")))
    intelligence._trim_live_ollama_log()
    assert log.stat().st_size == 0 and (tmp_path / "server.log.1").stat().st_size > intelligence.OLLAMA_LOG_LIMIT
