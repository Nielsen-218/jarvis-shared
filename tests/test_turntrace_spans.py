"""The voice turn's latency ledger (core/turntrace stages, the model's first token and prompt reads, the meaning checks
before the brain) and scripts/latency_report.py."""
import io
import json
import time

import pytest

from core import intelligence, storage, turntrace


@pytest.fixture(autouse=True)
def fresh_spans():
    turntrace._spans.clear()
    yield
    turntrace._spans.clear()


def test_stages_take_the_turns_marks_and_spans_and_drop_stale_timestamps():
    began = time.time() - 5.0                     # his last word
    commit = began + 0.6
    turntrace.span("model", "fast", commit - 30, commit - 29)        # an earlier turn's call: not this turn's
    turntrace.span("meaning", "stop/dismiss", commit + 0.5, commit + 1.3)
    turntrace.span("decide", "gateway", commit + 1.3, commit + 3.3)
    turntrace.span("model", "advanced", commit + 1.4, commit + 3.2)
    turntrace.span("ttft", "fast", commit + 3.4, commit + 3.9)
    marks = {"endpoint": (began, commit), "stt": (commit, commit + 0.4),
             "first_sentence": (commit, commit + 4.2), "tts_first_audio": (commit + 4.2, commit + 4.5),
             "total": (commit, commit + 4.5),
             "stale_first_audio": (began - 20, commit + 4.5),       # left over from the previous reply
             "backwards": (commit + 2, commit + 1)}
    stages = turntrace.stages(began, marks, turntrace.breakdown(commit), commit)
    assert stages == {"endpoint": 0.6, "stt": 0.4, "first_sentence": 4.2, "tts_first_audio": 0.3, "total": 4.5,
                      "prefilter": 0.8, "decide": 2.0, "model": 1.8, "first_token": 3.9}


def test_a_voice_turn_row_carries_its_stages(tmp_path):
    commit = time.time() - 2.0
    turntrace.span("decide", "gateway", commit + 0.1, commit + 0.6)
    text = turntrace.record("voice", "Close ChatGPT.", commit, 1.0, 1.4,
                            marks={"endpoint": (commit - 0.64, commit), "total": (commit, commit + 1.4)},
                            began=commit - 0.64)
    rows = [json.loads(line) for f in (tmp_path / "learning").glob("turn-timing-*.jsonl") for line in f.read_text().splitlines()]
    assert rows[-1]["stages"] == {"endpoint": 0.64, "total": 1.4, "decide": 0.5}
    assert rows[-1]["said"] == "Close ChatGPT." and "endpoint 0.64s" in text
    turntrace.record("phone", "hi", commit, 0.5, 0.5)                 # the phone path writes what it always wrote
    rows = [json.loads(line) for f in (tmp_path / "learning").glob("turn-timing-*.jsonl") for line in f.read_text().splitlines()]
    assert "stages" not in rows[-1]


# -- the local model: what it had to read, and its first token --------------------------------------------------------
class _Ollama:
    def __init__(self):
        self.chats = []

    def open(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/models"):
            return io.BytesIO(json.dumps({"data": [{"id": "qwen3.5:9b"}]}).encode())
        sent = json.loads(request.data)
        self.chats.append(sent)
        final = {"model": "qwen3.5:9b", "message": {"role": "assistant", "content": "" if sent["stream"] else "Hello."},
                 "done": True, "done_reason": "stop", "prompt_eval_count": 37, "eval_count": 3,
                 "prompt_eval_duration": 41_000_000}
        if not sent["stream"]:
            return io.BytesIO(json.dumps(final).encode())
        piece = {"message": {"role": "assistant", "content": "Hello."}, "done": False}
        return io.BytesIO((json.dumps(piece) + "\n" + json.dumps(final) + "\n").encode())


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
    return fake


def _payload():
    return {"role": "fast", "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "Hi."}],
            "tools": []}


def _audit_rows(tmp_path):
    return [r for name in ("intelligence_usage-pc.json", "intelligence_usage-server.json")
            for r in storage.load(name, [])]


def test_complete_local_stores_what_ollama_had_to_read(ollama, tmp_path):
    intelligence.complete_local(_payload())
    row = [r for r in _audit_rows(tmp_path) if r["status"] == "received"][-1]
    assert row["tokens"]["prompt_tokens"] == 37 and row["tokens"]["prompt_eval_ms"] == 41
    assert row["tokens"]["completion_tokens"] == 3
    assert not any(kind == "ttft" for _, _, kind, _ in turntrace._spans)      # nothing streamed: no first token


def test_a_streamed_reply_marks_the_models_first_token(ollama):
    heard = []
    before = time.time()
    intelligence.complete_local(_payload(), on_text=heard.append)
    assert heard == ["Hello."] and ollama.chats[-1]["stream"] is True
    (start, end, kind, role), = [s for s in turntrace._spans if s[2] == "ttft"]
    assert role == "fast" and before - 1 <= start <= end
    assert turntrace.first_end("ttft", before - 1) == end


def test_the_meaning_check_before_the_brain_is_timed_as_prefilter():
    from core import meaning
    assert meaning.choose("zip it", {"stop": "stop talking", "dismiss": "done talking"},
                          ask=lambda body, schema, cancelled: {"meaning": "stop"}) == "stop"
    assert [s[2:] for s in turntrace._spans] == [("meaning", "stop/dismiss")]


# -- the report ------------------------------------------------------------------------------------------------------------
def test_latency_report_prints_p50_and_p90_per_stage(tmp_path, capsys):
    from scripts import latency_report
    path = tmp_path / "turn-timing-pc.jsonl"
    rows = [{"channel": "voice", "total": 6.3, "thinking": 5.1, "spans": {}},                 # an old row: no stages
            {"channel": "phone", "total": 1.5, "thinking": 1.5, "spans": {}}]
    for n in range(1, 11):
        rows.append({"channel": "voice", "total": n, "thinking": n - 0.5, "spans": {},
                     "stages": {"endpoint": 0.64, "stt": 0.1 * n, "decide": float(n), "total": float(n)}})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps([{"role": "fast", "status": "received", "tokens": {"prompt_tokens": t, "prompt_eval_ms": t // 10}}
                                 for t in (9000, 40, 35, 60)]), encoding="utf-8")
    assert latency_report.main(["--file", str(path), "--usage", "--usage-file", str(usage)]) == 0
    out = capsys.readouterr().out
    assert "11 voice turns" in out
    table = latency_report.table(latency_report.rows(path, "voice"))
    assert table["total"]["n"] == 11 and table["total"]["p50"] == 6.0 and table["total"]["p90"] == 9.0
    assert table["decide"] == {"n": 10, "p50": 5.0, "p90": 9.0, "max": 10.0}
    assert list(table)[:3] == ["endpoint", "stt", "decide"]
    assert "fast" in out and "p50 40" in out                                              # warm turns read little
    assert latency_report.percentile([3.0], 90) == 3.0


def test_a_pc_turn_row_says_which_tool_failed_and_how_the_run_ended(tmp_path):
    """audit-pc-control defect 4: the timing rows had no success or failure, so slow or failing tools could not be found."""
    started = time.time() - 2.0
    turntrace._outcomes.clear()
    turntrace.outcome("gateway", "stopped", False, ["open_app", "type_text"], "type_text")
    turntrace.record("phone", "do the thing", started, 1.0, 1.4)
    rows = [json.loads(line) for f in (tmp_path / "learning").glob("turn-timing-*.jsonl") for line in f.read_text().splitlines()]
    assert rows[-1]["pc"] == [{"route": "gateway", "state": "stopped", "verified": False,
                               "tools": ["open_app", "type_text"], "failed": "type_text"}]


def test_a_turn_without_a_pc_run_has_no_pc_field_and_an_earlier_turns_run_is_not_carried(tmp_path):
    turntrace._outcomes.clear()
    turntrace.outcome("gateway", "completed", True, ["open_app"])
    turntrace._outcomes[0] = (time.time() - 60, turntrace._outcomes[0][1])
    turntrace.record("phone", "hello", time.time() - 2.0, 1.0, 1.4)
    rows = [json.loads(line) for f in (tmp_path / "learning").glob("turn-timing-*.jsonl") for line in f.read_text().splitlines()]
    assert "pc" not in rows[-1]
