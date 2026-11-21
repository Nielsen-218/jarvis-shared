"""The phone path's stage ledger on the laptop: where a phone turn's seconds go (speech to text, each hop to the PC, each
tool, making the voice), the voice warmed when the phone server starts, and what the model's prompt cache could reuse.

Measured 2026-11-19: his three real phone turns took 16.6 / 17.4 / 25.0 s and the log said only "other 17.4s". The
first two were the first spoken reply after a laptop restart (the voice model loading inside the turn)."""
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from core import intelligence, pcbridge, storage, turntrace, users


@pytest.fixture(autouse=True)
def fresh_spans():
    turntrace._spans.clear()
    yield
    turntrace._spans.clear()


def _rows(tmp_path):
    return [json.loads(line) for f in (tmp_path / "learning").glob("turn-timing-*.jsonl") for line in f.read_text().splitlines()]


# -- every hop to the PC is a span, failures too ----------------------------------------------------------------------
@pytest.fixture
def laptop(monkeypatch):
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example.ts.net")
    monkeypatch.setenv("JARVIS_PC_TOKEN", "token")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(pcbridge, "_down", {"until": 0.0, "healthy_until": 0.0})


class _Connection:
    def close(self):
        pass


class _Response:
    def __init__(self, data=b'{"ok": true}', ctype="application/json"):
        self.data, self.headers = data, {"Content-Type": ctype}

    def read(self, size=-1):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(monkeypatch, respond):
    class Opener:
        def open(self, req, timeout):
            return respond(req, timeout)
    monkeypatch.setattr(pcbridge.socket, "create_connection", lambda address, timeout: _Connection())
    monkeypatch.setattr(pcbridge.urllib.request, "build_opener", lambda *a: Opener())


def test_a_relay_to_the_pc_is_timed_by_route(laptop, monkeypatch):
    def slow(req, timeout):
        time.sleep(0.08)
        return _Response()
    _opener(monkeypatch, slow)
    since = time.time() - 0.01
    assert pcbridge.json_request("/api/pc/intelligence", "POST", {}) == {"ok": True}
    pcbridge.json_request("/api/pc/tasks/0123456789abcdef0123456789abcdef")
    b = turntrace.breakdown(since)
    assert b["relay"]["n"] == 2 and set(b["relay"]["parts"]) == {"intelligence", "tasks"}
    assert b["relay"]["s"] >= 0.1


def test_a_relay_that_fails_leaves_its_time_too(laptop, monkeypatch):
    def refuses(address, timeout):
        raise OSError("no route")
    monkeypatch.setattr(pcbridge.socket, "create_connection", refuses)
    since = time.time() - 0.01
    with pytest.raises(pcbridge.PCError):
        pcbridge.json_request("/api/pc/intelligence", "POST", {})
    assert list(turntrace.breakdown(since)["relay"]["parts"]) == ["intelligence failed"]


def test_the_background_pull_of_the_pcs_thread_is_not_a_turns_relay(laptop, monkeypatch):
    _opener(monkeypatch, lambda req, timeout: _Response())
    since = time.time() - 0.01
    pcbridge.json_request("/api/pc/thread")
    assert "relay" not in turntrace.breakdown(since)


# -- the phone turn's row ---------------------------------------------------------------------------------------------
class _Brain:
    def __init__(self, uid):
        self.uid = uid

    def ask(self, content, on_sentence):
        with turntrace.timed("relay", "intelligence"):               # the thinking, on the PC
            time.sleep(0.13)
        on_sentence("Hello sir.")


@pytest.fixture
def phone(monkeypatch):
    from core import appserver

    monkeypatch.setattr(appserver, "CACHE", storage.DATA / "cache")
    (storage.DATA / "cache").mkdir(parents=True, exist_ok=True)

    def mp3(text):
        time.sleep(0.08)
        return b"ID3fake"
    monkeypatch.setattr("audio.tts.mp3_for", mp3)

    class _Ears:
        def transcribe(self, samples):
            time.sleep(0.08)
            return "what time is it"
    server = appserver.AppServer(lambda uid: _Brain(uid), _Ears())
    return TestClient(server.app)


def _pair(client, device="iPhone"):
    from tools import voice

    voice.pair_codes["111111"] = "owner"
    r = client.post("/api/pair", json={"code": "111111", "device": device})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_a_typed_phone_turn_says_where_its_time_went(phone, tmp_path):
    out = phone.post("/api/chat", json={"text": "hi", "speak": True}, headers=_pair(phone)).json()
    assert out["audio"]
    row = _rows(tmp_path)[-1]
    assert row["channel"] == "phone" and row["said"] == "hi"
    stages = row["stages"]
    assert stages["relay"] >= 0.1 and stages["voice"] >= 0.05
    assert stages["total"] >= stages["relay"] + stages["voice"] - 0.02          # each rounded to a hundredth
    assert "stt" not in stages                                    # typed: nothing was transcribed
    assert row["spans"]["voice"]["parts"].keys() == {"local"}


def test_a_spoken_phone_turn_is_recorded_with_its_transcription(phone, tmp_path, monkeypatch):
    import numpy as np

    from core import phone as phone_audio
    monkeypatch.setattr(phone_audio, "decode_to_16k", lambda data: np.zeros(16000, dtype=np.float32))
    out = phone.post("/api/voice", files={"audio": ("note.webm", b"x" * 400, "audio/webm")}, data={"speak": "1"},
                     headers=_pair(phone)).json()
    assert out["heard"] == "what time is it" and out["reply"] == "Hello sir."
    row = _rows(tmp_path)[-1]                                     # /api/voice wrote no row at all before 2026-11-19
    assert row["channel"] == "phone" and row["said"] == "what time is it"
    assert row["stages"]["stt"] >= 0.05 and row["stages"]["relay"] >= 0.1 and row["stages"]["voice"] >= 0.05
    assert row["stages"]["total"] >= row["stages"]["stt"] + row["stages"]["relay"] + row["stages"]["voice"] - 0.03
    assert "test" not in row


def test_a_spoken_turn_from_a_verify_device_is_test_traffic_on_its_own_brain(monkeypatch, tmp_path):
    import numpy as np

    from core import appserver, phone as phone_audio
    monkeypatch.setattr(appserver, "CACHE", storage.DATA / "cache")
    (storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: b"ID3fake")
    monkeypatch.setattr(phone_audio, "decode_to_16k", lambda data: np.zeros(16000, dtype=np.float32))
    made = []

    class Ears:
        def transcribe(self, samples):
            return "what time is it"

    def his(uid):
        made.append("his")
        return _Brain(uid)

    def verify(uid):
        made.append("verify")
        return _Brain(uid)
    client = TestClient(appserver.AppServer(his, Ears(), verify_brain=verify).app)
    client.post("/api/voice", files={"audio": ("note.webm", b"x" * 400, "audio/webm")}, data={"speak": "0"},
                headers=_pair(client, "claude-verify-partner"))
    assert made == ["verify"]                                     # never part of his conversation
    assert _rows(tmp_path)[-1]["test"] is True


def test_the_log_line_names_the_stages_instead_of_other(phone, tmp_path, capsys):
    phone.post("/api/chat", json={"text": "hi", "speak": True}, headers=_pair(phone))
    import re
    line = [text for text in capsys.readouterr().out.splitlines() if "[timing] phone turn" in text][-1]
    # which stage took the seconds, by name (how many seconds depends on the machine: never asserted)
    assert re.search(r"relay 1x [0-9.]+s [(]intelligence [0-9.]+[)]", line), line
    assert re.search(r"voice 1x [0-9.]+s [(]local [0-9.]+[)]", line), line


def test_a_tool_the_brain_ran_is_timed_by_name():
    since = time.time() - 0.01
    with turntrace.timed("tool", "search_internet"):
        time.sleep(0.02)
    b = turntrace.breakdown(since)
    assert b["tool"]["parts"].keys() == {"search_internet"}
    assert "tool 1x" in turntrace.words(b, 0.02)


def test_words_take_relay_and_voice_out_of_other():
    b = {"relay": {"n": 1, "s": 2.0, "parts": {"intelligence": 2.0}}, "voice": {"n": 1, "s": 3.0, "parts": {"local": 3.0}}}
    assert turntrace.words(b, 5.1) == "relay 1x 2.0s (intelligence 2.0) · voice 1x 3.0s (local 3.0)"
    assert turntrace.words(b, 6.0).endswith("other 1.0s")


def test_the_report_orders_the_phone_stages(tmp_path):
    from scripts import latency_report
    path = tmp_path / "turn-timing-server.jsonl"
    rows = [{"channel": "phone", "total": 4.0, "thinking": 2.0, "spans": {},
             "stages": {"stt": 0.6, "relay": 1.9, "voice": 1.4, "total": 4.0}} for _ in range(3)]
    rows.append({"channel": "phone", "total": 9.0, "thinking": 9.0, "spans": {}, "test": True,
                 "stages": {"relay": 9.0, "total": 9.0}})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    table = latency_report.table(latency_report.rows(path, "phone"))
    assert list(table)[:4] == ["stt", "relay", "voice", "total"]
    his = latency_report.table(latency_report.rows(path, "phone", real_only=True))
    assert his["total"]["n"] == 3 and his["relay"]["max"] == 1.9           # test traffic left out when asked


# -- the voice is warm before the first reply -------------------------------------------------------------------------
def test_the_phone_server_warms_the_voice_at_start(monkeypatch):
    from core import appserver, pchelp
    made = []
    monkeypatch.setattr("audio.tts.mp3_voiced", lambda text: made.append(text) or b"ID3" + text.encode())
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(appserver, "_ready_audio", {})
    thread = appserver.warm_voice()
    thread.join(5)
    # what it voices to warm up is the one line needed while the PC cannot help, in the owner's wording
    with users.acting_as("owner"):
        line = pchelp.pc_out_of_reach(users.title("owner"))
    assert made == [line] and line.startswith("I can't reach the PC right now")
    assert appserver._ready_audio == {line: b"ID3" + line.encode()}


def test_the_pc_off_line_is_said_from_the_audio_made_at_start(monkeypatch, tmp_path):
    from core import appserver
    monkeypatch.setattr(appserver, "CACHE", storage.DATA / "cache")
    (storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example.ts.net")
    monkeypatch.setattr(appserver, "_ready_audio", {"I can't reach the PC right now, sir.": b"ID3ready"})
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: pytest.fail("made again on the laptop's CPU"))
    monkeypatch.setattr(pcbridge, "speak", lambda text: pytest.fail("asked the PC that is off"))
    server = appserver.AppServer(lambda uid: None)
    since = time.time() - 0.01
    url = server._speak("I can't reach the PC right now, sir.")
    assert (storage.DATA / "cache" / url.rsplit("/", 1)[1]).read_bytes() == b"ID3ready"
    assert turntrace.breakdown(since)["voice"]["parts"].keys() == {"ready"}


def test_with_no_owner_known_the_voice_is_still_warmed(monkeypatch):
    from core import appserver
    made = []
    monkeypatch.setattr("audio.tts.mp3_voiced", lambda text: made.append(text) or b"ID3")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(appserver, "_ready_audio", {})
    monkeypatch.setattr(appserver, "_line_for_a_silent_pc", lambda: "")
    appserver.warm_voice().join(5)
    assert made == ["Ready."] and appserver._ready_audio == {}


def test_the_warm_up_is_the_laptops_only_and_has_a_switch(monkeypatch):
    from core import appserver
    monkeypatch.setattr("audio.tts.mp3_voiced", lambda text: pytest.fail("warmed where it should not"))
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)       # the PC's voice loop warms its own voice
    assert appserver.warm_voice() is None
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setenv("JARVIS_VOICE_WARM", "off")
    assert appserver.warm_voice() is None


def test_a_voice_that_cannot_load_does_not_stop_the_server(monkeypatch, capsys):
    from core import appserver

    def broken(text):
        raise RuntimeError("no model files")
    monkeypatch.setattr("audio.tts.mp3_voiced", broken)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(appserver, "_ready_audio", {})
    appserver.warm_voice().join(5)
    assert "voice warm-up failed" in capsys.readouterr().out
    assert appserver._ready_audio == {}                    # nothing is kept from a voice that did not speak


# -- what the model's prompt cache could reuse ------------------------------------------------------------------------
class _Ollama:
    def __init__(self):
        self.chats = []

    def open(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/models"):
            return io.BytesIO(json.dumps({"data": [{"id": "qwen3.5:9b"}]}).encode())
        sent = json.loads(request.data)
        self.chats.append(sent)
        return io.BytesIO(json.dumps({"model": "qwen3.5:9b", "message": {"role": "assistant", "content": "Hello."},
                                      "done": True, "done_reason": "stop", "prompt_eval_count": 37, "eval_count": 3,
                                      "prompt_eval_duration": 41_000_000}).encode())


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("JARVIS_LOCAL_ADVANCED_MODEL", "qwen3.5:9b")
    fake = _Ollama()
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", fake))
    monkeypatch.setattr(intelligence, "_endpoint", lambda: ("http://127.0.0.1:11434/v1", fake))
    monkeypatch.setattr(intelligence, "_release_pressure", lambda role: False)
    monkeypatch.setattr(intelligence, "_switch_local_provider", lambda role, cancelled: None)
    intelligence._sent.clear()
    return fake


def _ask(system, *turns):
    messages = [{"role": "system", "content": system}]
    for n, text in enumerate(turns):
        messages.append({"role": "user" if n % 2 == 0 else "assistant", "content": text})
    intelligence.complete_local({"role": "fast", "messages": messages, "tools": []})
    return [r for r in storage.load("intelligence_usage-pc.json", []) + storage.load("intelligence_usage-server.json", [])
            if r["status"] == "received"][-1]["tokens"]


def test_each_call_records_how_much_of_its_prompt_an_earlier_one_shared(ollama):
    manual = "You are Jarvis. " * 400
    first = _ask(manual, "What time is it?")
    assert first["prompt_chars"] > len(manual) and first["shared"] == 0 and "back" not in first
    # the next turn of the same conversation: everything before its new part was sent before
    second = _ask(manual, "What time is it?", "Ten past nine.", "And the date?")
    assert second["shared"] >= len(manual) and second["shared"] <= second["prompt_chars"]
    # ...and how far before the END of that earlier prompt the two part ways (the cache resumes only near its end)
    assert 0 <= second["back"] < 200


def test_a_change_early_in_the_prompt_shows_as_a_long_way_back(ollama):
    manual = "You are Jarvis. " * 400
    _ask(manual + " It is 9:10.", "What time is it?")
    changed = _ask(manual + " It is 9:11.", "What time is it?")
    assert changed["shared"] >= len(manual) and changed["back"] < 200          # the clock was at the end: close
    early = _ask("It is 9:12. " + manual, "What time is it?")
    assert early["shared"] < len(manual) // 10                                 # the clock in front: only the header is shared


def test_the_best_match_among_recent_prompts_is_the_one_recorded(ollama):
    manual, persona = "You are Jarvis. " * 400, "You are a friend. " * 100
    _ask(manual, "Open Notepad.")
    _ask(persona, "How are you?")                                              # another kind of call in between
    again = _ask(manual, "Open Notepad.", "Opened.", "Close it.")
    assert again["shared"] >= len(manual)


def test_only_sizes_are_kept_never_the_words(ollama, tmp_path):
    _ask("You are Jarvis.", "My password is hunter2.")
    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("intelligence_usage-*.json"))
    assert "hunter2" not in text and "prompt_chars" in text


def test_only_the_pulls_own_route_is_background(laptop, monkeypatch):
    _opener(monkeypatch, lambda req, timeout: _Response())
    since = time.time() - 0.01
    pcbridge.json_request("/api/pc/threadbare")            # another route that merely begins the same
    assert list(turntrace.breakdown(since)["relay"]["parts"]) == ["threadbare"]
