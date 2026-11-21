"""Core foundation (2026-10-30 audit): VRAM budget, GPU-voice self-heal, resume after sleep, memory de-duplication,
visible swallowed failures, one run per identical call. Each pins a weakness verified on the owner's PC."""
import io
import json

import pytest

import tools
from core import diag, intelligence, resume
from tools import memory, registry

tools.load_all()


# -- VRAM: the tiers' models take turns; what the tiers never use goes -----------------------------------------------
class _Ollama:
    def __init__(self, resident):
        self.resident, self.unloaded = dict(resident), []

    def open(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/api/ps"):
            body = {"models": [{"name": n, "size_vram": s} for n, s in self.resident.items()]}
        elif url.endswith("/api/generate"):
            sent = json.loads(request.data)
            assert sent["keep_alive"] == 0
            self.unloaded.append(sent["model"])
            self.resident.pop(sent["model"], None)
            body = {"done": True}
        else:
            raise AssertionError(url)
        return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def tiers(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_MODEL", "llama3.2:3b")
    monkeypatch.setenv("JARVIS_LOCAL_ADVANCED_MODEL", "llama3.1:8b")
    monkeypatch.delenv("JARVIS_LOCAL_VISION_MODEL", raising=False)
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)


def test_models_the_tiers_never_use_are_unloaded(tiers):
    """The planner's qwen2.5:7b stayed pinned (4.4 GB) on a 16 GB card beside three tier models: 15.2 GB in use."""
    ollama = _Ollama({"qwen2.5:7b-instruct": 4.4e9, "llama3.2:3b": 3.7e9})
    assert intelligence.reclaim_vram("http://127.0.0.1:11434/v1", ollama) == ["qwen2.5:7b-instruct"]
    assert set(ollama.resident) == {"llama3.2:3b"}


def test_reclaim_is_only_for_local_first_with_ollama(tiers, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "lmstudio")
    ollama = _Ollama({"qwen2.5:7b-instruct": 4.4e9})
    assert intelligence.reclaim_vram("http://127.0.0.1:11434/v1", ollama) == [] and ollama.unloaded == []


def test_the_advanced_and_vision_models_take_turns_and_the_fast_one_stays(tiers, monkeypatch):
    monkeypatch.setattr(intelligence, "_free_vram_gb", lambda: 9.0)      # room on the card: only the alternates move
    url = "http://127.0.0.1:11434/v1"
    ollama = _Ollama({"llama3.2:3b": 3.7e9, "llama3.1:8b": 6.6e9})
    assert intelligence._make_room("qwen3-vl:8b-instruct", url, ollama) == ["llama3.1:8b"]
    assert "llama3.2:3b" in ollama.resident
    ollama = _Ollama({"llama3.2:3b": 3.7e9, "qwen3-vl:8b-instruct": 5.8e9, "qwen2.5:7b-instruct": 4.4e9})
    assert set(intelligence._make_room("llama3.1:8b", url, ollama)) == {"qwen3-vl:8b-instruct", "qwen2.5:7b-instruct"}
    ollama = _Ollama({"llama3.2:3b": 3.7e9, "llama3.1:8b": 6.6e9})
    assert intelligence._make_room("llama3.1:8b", url, ollama) == []          # already loaded: nothing moves
    assert intelligence._make_room("llama3.1:8b", url, _Ollama({})) == []        # nothing resident / unreachable


# -- the GPU voice heals itself -------------------------------------------------------------------------------------------
def test_a_cuda_fault_reloads_the_voice_once_then_cools_down(monkeypatch):
    from audio import tts
    import numpy as np
    calls = []

    def parts(text, name, rate):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("CUDA error: unknown error")
        return [np.zeros(2400, dtype=np.float32)]
    monkeypatch.setattr(tts, "_kokoro_parts", parts)
    monkeypatch.setattr(tts, "_kokoro_down_until", 0.0)
    tts._kokoro_pipes["a"] = object()
    out = tts.kokoro_say("Ready.", "kokoro:am_michael")
    assert len(calls) == 2 and "a" not in tts._kokoro_pipes and isinstance(out, np.ndarray)   # reloaded, answered

    def broken(text, name, rate):
        calls.append(text)
        raise RuntimeError("CUDA error: unknown error")
    monkeypatch.setattr(tts, "_kokoro_parts", broken)
    with pytest.raises(RuntimeError):
        tts.kokoro_say("Hello.", "kokoro:am_michael")                    # fails twice -> circuit opens
    n = len(calls)
    with pytest.raises(RuntimeError, match="cooling down"):
        tts.kokoro_say("Hello again.", "kokoro:am_michael")              # not even tried while cooling
    assert len(calls) == n
    tts.kokoro_reset()
    assert tts._kokoro_down_until == 0.0

    def other(text, name, rate):
        raise ValueError("bad phonemes")
    monkeypatch.setattr(tts, "_kokoro_parts", other)
    with pytest.raises(ValueError):                                       # a non-GPU error is just raised
        tts.kokoro_say("x", "kokoro:am_michael")


def test_kokoro_warmup_only_loads_the_active_voice(monkeypatch):
    from audio import tts
    calls = []
    monkeypatch.setattr(tts, "kokoro_say", lambda text, voice: calls.append((text, voice)))
    tts.warm_up_kokoro("kokoro:bm_george")
    assert calls == [("Ready.", "kokoro:bm_george")]


def test_edge_voice_403_opens_a_per_voice_circuit_breaker(monkeypatch):
    from audio import tts
    speaker = tts.Speaker("kokoro:bm_george", muted=True)
    tts.edge_reset()
    async def failed(text, voice):
        raise RuntimeError("403 Invalid response status")
    monkeypatch.setattr(speaker, "_edge_tts", failed)
    with pytest.raises(RuntimeError, match="403"):
        speaker._say_with("Hello", "en-US-AndrewMultilingualNeural")
    with pytest.raises(RuntimeError, match="cooling down"):
        speaker._say_with("Again", "en-US-AndrewMultilingualNeural")
    assert tts._edge_down_until["en-US-AndrewMultilingualNeural"] > tts.time.monotonic()


# -- back from sleep ------------------------------------------------------------------------------------------------------
def test_a_long_gap_between_ticks_fires_the_resume_hooks(monkeypatch):
    monkeypatch.setattr(resume, "_hooks", [])
    monkeypatch.setattr(resume, "resumed_at", [])
    fired = []
    resume.on_resume("voice", lambda: fired.append("voice"))
    resume.on_resume("broken", lambda: 1 / 0)
    resume.on_resume("cache", lambda: fired.append("cache"))
    clock = iter([0.0, 5.0, 10.0, 10.0 + 3600, 3615.0, 3620.0])
    ticks = []

    def sleep(seconds):
        ticks.append(seconds)
        if len(ticks) > 5:
            raise StopIteration
    with pytest.raises(StopIteration):
        resume._watch(lambda: next(clock), sleep)
    assert fired == ["voice", "cache"] and len(resume.resumed_at) == 1         # one resume; the broken hook isolated
    assert resume.fire() == ["voice", "cache"]


# -- memory: one fact, once ----------------------------------------------------------------------------------------------
def test_the_same_fact_worded_differently_is_not_stored_again():
    assert memory.remember("Their wife is Maya.", "family", False).startswith("Noted")
    assert memory.remember("Owner's wife is Maya", "family", False) == "Already noted, sir."
    assert memory.remember("Wife's name is Maya", "family", False) == "Already noted, sir."
    assert memory.remember("Their sister Marie lives in Miami.", "family", False).startswith("Noted")
    assert memory.remember("Marie lives in Miami", "family", False) == "Already noted, sir."
    assert memory.remember("Their sister Marie is a nurse.", "family", False).startswith("Noted")   # new information
    assert len(memory._all()) == 3
    # a fact that says MORE replaces the narrower one it widens (the id and count stay)
    assert memory.remember("Their wife Maya is a nurse at Memorial.", "family", False).startswith("Noted")
    facts = [m["fact"] for m in memory._all()]
    assert "Their wife Maya is a nurse at Memorial." in facts and "Their wife is Maya." not in facts and len(facts) == 3
    assert memory.compare_facts("Likes spaghetti and meatballs.", "Likes spaghetti.") == "wider"
    assert memory.compare_facts("Likes spaghetti.", "Likes spaghetti and meatballs.") == "narrower"
    assert memory.compare_facts("Likes dogs.", "Likes cats.") is None


# -- swallowed failures are seen -----------------------------------------------------------------------------------------
def test_swallowed_failures_are_logged_once_then_rate_limited(capsys, monkeypatch):
    monkeypatch.setattr(diag, "_last", {})
    monkeypatch.setattr(diag, "_count", {})
    for _ in range(5):
        diag.swallowed("test site", OSError("refused"))
    out = capsys.readouterr().out
    assert out.count("[diag] test site: OSError: refused") == 1 and diag.counts()["test site"] == 5


def test_core_sites_no_longer_swallow_silently():
    import pathlib
    for file, sites in {"core/appserver.py": 4, "core/notify.py": 2, "core/scheduler.py": 2, "main.py": 1}.items():
        assert pathlib.Path(file).read_text(encoding="utf-8").count("diag.swallowed(") >= sites, file


# -- one run per identical call in a reply --------------------------------------------------------------------------------
def test_the_same_call_twice_in_one_reply_runs_once(monkeypatch):
    from types import SimpleNamespace
    from anthropic.types import TextBlock, ToolUseBlock
    from brain import llm
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    b._local_role, b._cloud_calls, b._failed_calls = "fast", 0, set()      # what ask() sets up before _loop
    ran = []
    monkeypatch.setattr(registry, "run", lambda name, args: (ran.append((name, args)) or ("Added to shopping (1 open item).", False)))
    turns = iter([
        SimpleNamespace(content=[ToolUseBlock(type="tool_use", id="a", name="add_note", input={"list_name": "shopping", "text": "milk"}),
                                 ToolUseBlock(type="tool_use", id="b", name="add_note", input={"list_name": "shopping", "text": "milk"})],
                        stop_reason="tool_use"),
        SimpleNamespace(content=[TextBlock(type="text", text="Milk is on the list, sir.")], stop_reason="end_turn")])
    monkeypatch.setattr(b, "_stream_turn", lambda emit: next(turns))
    b.messages = [{"role": "user", "content": "Add milk to my shopping list."}]
    said = []
    b._loop(said.append, 0)
    assert ran == [("add_note", {"list_name": "shopping", "text": "milk"})]       # once
    results = b.messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"] and results[0]["content"] == results[1]["content"]


# -- durable state under concurrency and across restarts -----------------------------------------------------------------
def test_concurrent_saves_of_one_file_never_corrupt_it(tmp_path):
    import threading
    from core import storage
    errors = []

    def writer(n):
        try:
            for i in range(40):
                storage.save_global("race.json", {"writer": n, "i": i, "pad": "x" * 2000})
                data = storage.load_global("race.json", None)
                assert isinstance(data, dict) and set(data) == {"writer", "i", "pad"}
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors and isinstance(storage.load_global("race.json", None), dict)


def test_memory_and_journal_survive_a_restart(monkeypatch):
    """A new Brain (as after a restart) sees the facts and the journal written by the old one: both live in files."""
    import importlib
    from core import journal, storage, users
    users.set_current("owner")
    memory.remember("Their dog is Biscuit.", "family", False)
    journal.add_lesson("Say the time without a tool.", "owner")
    importlib.reload(memory)                                   # module state gone, like a fresh process
    assert any("Biscuit" in m["fact"] for m in memory._all())
    assert "Say the time without a tool." in journal.lessons()
    assert storage.load(memory.FILE, None) is not None


# -- the local server's circuit breaker ------------------------------------------------------------------------------------
class _Dead:
    """An Ollama that refuses every connection."""
    def __init__(self):
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        raise OSError("connection refused")


def test_three_transport_failures_open_the_circuit_and_an_answer_closes_it(tiers, monkeypatch):
    monkeypatch.setattr(intelligence, "_circuits", {})
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", _Dead()))
    monkeypatch.setattr(intelligence, "_endpoint", lambda: ("http://127.0.0.1:11434/v1", _Dead()))
    monkeypatch.setattr(intelligence, "_release_pressure", lambda role: False)
    payload = {"role": "fast", "messages": [{"role": "user", "content": "hi"}], "tools": []}
    for _ in range(3):
        with pytest.raises(intelligence.LocalFailure, match="local_request_failed"):
            intelligence.complete_local(payload)
    status = intelligence.circuit_status()["http://127.0.0.1:11434/v1"]
    assert status["open"] and status["trips"] == 1 and 0 < status["seconds_left"] <= 30
    with pytest.raises(intelligence.LocalFailure, match="local_circuit_open"):      # fails in ms now, no waiting
        intelligence.complete_local(payload)
    # the server answers its model list again: closed at once, the turn proceeds to the real request
    alive = _Ollama({})

    def open_(request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/models"):
            return io.BytesIO(json.dumps({"data": [{"id": "llama3.2:3b"}]}).encode())
        return alive.open(request, timeout)
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", type("O", (), {"open": staticmethod(open_)})()))
    with pytest.raises(intelligence.LocalFailure):                                   # (the fake can't chat; fine)
        intelligence.complete_local(payload)
    assert not intelligence.circuit_status()["http://127.0.0.1:11434/v1"]["open"]


# -- outcomes are read back, not believed ---------------------------------------------------------------------------------
def test_a_write_whose_stored_state_disagrees_is_marked_unverified(monkeypatch):
    from core import verify
    from tools import notes
    assert verify.check("add_note", {"text": "milk", "list_name": "shopping"}, notes.add_note("milk", "shopping")) is None
    assert verify.check("add_note", {"text": "bread", "list_name": "shopping"}, "Added to shopping (2 open items).") \
        == "'bread' is not on the shopping list after the write"
    assert verify.check("remember", {"fact": "Their cat is Mochi."}, memory.remember("Their cat is Mochi.", "family", False)) is None
    assert verify.check("remember", {"fact": "Their cat is Tiger."}, "Noted, sir: their cat is Tiger.") == "the fact is not in memory after the write"
    assert verify.check("get_time", {}, "10:42") is None                           # no verifier: trusted as before
    monkeypatch.setitem(verify._VERIFIERS, "add_note", lambda a, o: 1 / 0)
    assert verify.check("add_note", {}, "Added to x (1 open item).") == "verifier failed: ZeroDivisionError"


def test_the_brain_never_reports_an_unverified_write_as_done(monkeypatch):
    from types import SimpleNamespace
    from anthropic.types import TextBlock, ToolUseBlock
    from brain import llm
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    b._local_role, b._cloud_calls, b._failed_calls = "fast", 0, set()
    monkeypatch.setattr(registry, "run", lambda name, args: ("Added to shopping (1 open item).", False))   # lies: nothing stored
    turns = iter([SimpleNamespace(content=[ToolUseBlock(type="tool_use", id="a", name="add_note",
                                                        input={"list_name": "shopping", "text": "milk"})], stop_reason="tool_use"),
                  SimpleNamespace(content=[TextBlock(type="text", text="I couldn't add it, sir.")], stop_reason="end_turn")])
    monkeypatch.setattr(b, "_stream_turn", lambda emit: next(turns))
    b.messages = [{"role": "user", "content": "Add milk to my shopping list."}]
    b._loop(lambda s: None, 0)
    result = b.messages[2]["content"][0]
    assert result["is_error"] and "UNVERIFIED" in result["content"]
    assert ("add_note", json.dumps({"list_name": "shopping", "text": "milk"}, sort_keys=True)) in b._failed_calls


# -- observations: fresh, sourced, confident --------------------------------------------------------------------------------
def test_a_fresh_screen_read_is_reused_and_an_old_one_is_not(monkeypatch):
    from core import observation
    from tools import screenread
    observation.forget()
    reads = []

    def fake_read(process=None):
        reads.append(process)
        return {"text": "Visible text: Save | Cancel", "ocr": ["Save", "Cancel"], "uia": [], "sources": ["ocr"], "notes": []}
    from core import pcvision
    monkeypatch.setattr(pcvision, "read_screen", fake_read)
    first = screenread.read_screen("", "")
    second = screenread.read_screen("", "")
    assert reads == [None] and "observed 0 s ago via ocr" in second and "Save" in first
    entry = observation.latest("screen_text")
    assert entry["source"] == "ocr" and 0 < entry["confidence"] < 1 and entry["available"]
    monkeypatch.setattr(observation.time, "monotonic", lambda: entry["at"] + 100)
    assert observation.latest("screen_text") is None                                # stale: observed again next time
    screenread.read_screen("", "")
    assert len(reads) == 2
    observation.record("camera", None, source="camera", confidence=0.0, available=False)
    assert observation.latest("camera")["available"] is False                      # an unavailable state is remembered too


def test_with_the_circuit_open_a_turn_fails_in_milliseconds_not_a_minute(tiers, monkeypatch):
    """Offline operation: Ollama down for the whole hour must not cost 60 s of silence per question."""
    import time as _time
    monkeypatch.setattr(intelligence, "_circuits", {"http://127.0.0.1:11434/v1": {"failures": 0, "open_until": _time.monotonic() + 30, "trips": 1, "last": "OSError"}})
    monkeypatch.setattr(intelligence, "_ollama_endpoint", lambda: ("http://127.0.0.1:11434/v1", _Dead()))
    monkeypatch.setattr(intelligence, "_endpoint", lambda: ("http://127.0.0.1:11434/v1", _Dead()))
    t = _time.monotonic()
    with pytest.raises(intelligence.LocalFailure, match="local_circuit_open"):
        intelligence.request("fast", "sys", [{"role": "user", "content": "What's the weather?"}], registry.schemas())
    assert _time.monotonic() - t < 1.0


# -- the ear heals itself too ---------------------------------------------------------------------------------------------
def test_a_cuda_fault_in_whisper_reloads_the_model_then_falls_back_to_cpu(monkeypatch):
    import sys as _sys
    import types
    import numpy as np
    loads = []

    class FakeModel:
        def __init__(self, name, device="cpu", compute_type="int8"):
            loads.append((name, device))
            self.name, self.device, self.calls = name, device, 0

        def transcribe(self, samples, **kw):
            self.calls += 1
            if self.device == "cuda" and self.name == "large-v3-turbo" and len(loads) == 1 and self.calls == 1:
                raise RuntimeError("CUDA error: unknown error")
            return [types.SimpleNamespace(text=f"heard on {self.device}")], None

        def detect_language(self, samples):
            return None, None, {"en": 1.0}
    monkeypatch.setitem(_sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    from audio import stt
    t = stt.Transcriber("large-v3-turbo", "cuda")
    t.multilingual = False
    assert t.transcribe(np.zeros(16000, dtype=np.int16)) == "heard on cuda"      # faulted once, reloaded, answered
    assert loads == [("large-v3-turbo", "cuda"), ("large-v3-turbo", "cuda")] and t.on_gpu

    class Dying(FakeModel):
        def transcribe(self, samples, **kw):
            if self.device == "cuda":
                raise RuntimeError("CUDA error: unknown error")
            return [types.SimpleNamespace(text="heard on cpu")], None
    monkeypatch.setitem(_sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Dying))
    t2 = stt.Transcriber("large-v3-turbo", "cuda")
    t2.multilingual = False
    assert t2.transcribe(np.zeros(16000, dtype=np.int16)) == "heard on cpu" and not t2.on_gpu   # GPU gone: CPU small
    with pytest.raises(ValueError):                                                        # a non-GPU error is raised
        class Bad(FakeModel):
            def transcribe(self, samples, **kw):
                raise ValueError("bad audio")
        monkeypatch.setitem(_sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Bad))
        t3 = stt.Transcriber("large-v3-turbo", "cuda"); t3.multilingual = False
        t3.transcribe(np.zeros(16000, dtype=np.int16))


def test_a_background_job_that_has_not_finished_is_not_started_again():
    import threading
    from core import scheduler
    s = scheduler.Scheduler(say=lambda t: None, ask=lambda t: None)
    s._jobs = {}
    gate = threading.Event()
    assert s._spawn("homelab", gate.wait) is True
    assert s._spawn("homelab", gate.wait) is False           # still running: no second thread
    assert s._spawn("prices", lambda: None) is True          # another job is unaffected
    gate.set()
    s._jobs["homelab"].join(2)
    assert s._spawn("homelab", lambda: None) is True         # finished: runs again


def test_the_fast_model_also_goes_when_even_without_the_alternate_the_card_lacks_room(tiers, monkeypatch):
    """Live: 15.9 of 16.3 GB in use (Whisper and the voice hold ~2 GB too); a 6 s vision turn took 110-180 s and timed
    out because the driver was paging VRAM. With room, the fast model stays."""
    url = "http://127.0.0.1:11434/v1"
    monkeypatch.setattr(intelligence, "_free_vram_gb", lambda: 2.0)                  # nearly full card
    ollama = _Ollama({"llama3.2:3b": 3.8 * 2 ** 30, "llama3.1:8b": 6.6 * 2 ** 30})
    gone = intelligence._make_room("qwen3-vl:8b-instruct", url, ollama)
    assert gone == ["llama3.1:8b"]                                                      # 2.0 + 6.6 freed >= 7.5 + 1: enough
    monkeypatch.setattr(intelligence, "_free_vram_gb", lambda: 1.2)
    ollama = _Ollama({"llama3.2:3b": 3.8 * 2 ** 30, "llama3.1:8b": 6.6 * 2 ** 30})
    assert intelligence._make_room("qwen3-vl:8b-instruct", url, ollama) == ["llama3.1:8b", "llama3.2:3b"]   # 7.8 < 8.5
    monkeypatch.setattr(intelligence, "_free_vram_gb", lambda: 0.4)
    ollama = _Ollama({"llama3.2:3b": 3.8 * 2 ** 30})
    assert intelligence._make_room("qwen3-vl:8b-instruct", url, ollama) == ["llama3.2:3b"]   # 0.4 GB free: fast goes too
    monkeypatch.setattr(intelligence, "_free_vram_gb", lambda: 9.0)
    ollama = _Ollama({"llama3.2:3b": 3.8 * 2 ** 30})
    assert intelligence._make_room("qwen3-vl:8b-instruct", url, ollama) == []              # room: nothing moves
    monkeypatch.setattr(intelligence, "_free_vram_gb", lambda: None)                        # no nvidia-smi: old policy
    ollama = _Ollama({"llama3.2:3b": 3.8 * 2 ** 30})
    assert intelligence._make_room("qwen3-vl:8b-instruct", url, ollama) == []


# -- durable learning: a corrected single-valued fact replaces the old one ------------------------------------------------
def test_a_new_value_for_a_single_valued_slot_replaces_the_old_fact_and_says_so():
    assert memory.remember("Their wife is Maya.", "family", False).startswith("Noted")
    out = memory.remember("Their wife is Marie.", "family", False)
    assert out.startswith("Noted, sir: Their wife is Marie.") and "this replaces: Their wife is Maya." in out
    facts = [m["fact"] for m in memory._all()]
    assert facts == ["Their wife is Marie."]
    assert memory._all()[0]["replaced"] == ["Their wife is Maya."]
    # two sisters can coexist; a widening is not a correction; the car is a slot too
    assert memory.remember("Their sister is Anna.", "family", False).startswith("Noted")
    assert memory.remember("Their sister is Lucy.", "family", False).startswith("Noted, sir: Their sister is Lucy.")
    assert "replaces" not in memory.remember("Their wife Marie is a nurse.", "family", False)
    memory.remember("Their car is a Honda Civic.", "other", False)
    assert "replaces" in memory.remember("Their car is a red Corvette.", "other", False)
    assert len([f for f in memory._all() if "car" in f["fact"]]) == 1


def test_a_valid_fact_is_never_overwritten_on_uncertain_or_historical_information():
    memory.remember("Their wife is Maya.", "family", False)
    for hedged in ("Their wife is probably Marie.", "I think their wife is Marie.", "Their wife is not Maya.",
                   "Their wife is Marie or Anna.", "Their wife was Marie.", "Their wife used to be Marie."):
        memory.remember(hedged, "family", False)
    facts = [m["fact"] for m in memory._all()]
    assert "Their wife is Maya." in facts                     # still on file, untouched
    assert not any(m.get("replaced") for m in memory._all())
    # several family members of one kind coexist; a confirmed present-tense new value does replace
    memory.remember("Their son is Noah.", "family", False)
    memory.remember("Their son is Max.", "family", False)
    assert sum("son is" in f for f in [m["fact"] for m in memory._all()]) == 2
    out = memory.remember("Wife's name is Marie", "family", False)
    assert "this replaces: Their wife is Maya." in out
    assert "Their wife is Maya." not in [m["fact"] for m in memory._all()]


def test_corrections_survive_a_restart():
    import importlib
    memory.remember("Their car is a Honda Civic.", "other", False)
    memory.remember("Their car is a red Corvette.", "other", False)
    importlib.reload(memory)
    cars = [m for m in memory._all() if "car" in m["fact"]]
    assert len(cars) == 1 and cars[0]["fact"] == "Their car is a red Corvette." and cars[0]["replaced"] == ["Their car is a Honda Civic."]


def test_a_correction_reaches_a_fact_stored_in_the_other_store():
    """Live: the first fact went to the household store (shared=True), the correction to the personal one."""
    memory.remember("My wife's name is Maya.", "people", True)
    out = memory.remember("My wife's name is Marie.", "people", False)
    assert "this replaces: your wife's name is Maya." in out
    facts = [m["fact"] for m in memory._all()]
    assert facts == ["My wife's name is Marie."] and memory.recall("wife").count("Marie") == 1
    # and the other way round, with a widening across stores
    memory.remember("Their dog is Biscuit.", "people", False)
    memory.remember("Their dog Biscuit is a beagle.", "people", True)
    dogs = [m for m in memory._all() if "dog" in m["fact"]]
    assert [d["fact"] for d in dogs] == ["Their dog Biscuit is a beagle."] and dogs[0]["id"].startswith("h")


# -- final regression for repeated corrections, conflicts, cross-store, history, restart -------------------------------
def test_repeated_corrections_keep_exactly_one_current_value_with_full_history():
    import importlib
    memory.remember("My wife's name is Maya.", "people", False)
    memory.remember("My wife's name is Marie.", "people", True)            # correction lands in the other store
    memory.remember("My wife's name is Anna.", "people", False)            # and back again
    memory.remember("My wife's name is Anna.", "people", True)             # repeated: nothing changes
    wives = [m for m in memory._all() if "wife" in m["fact"]]
    assert [w["fact"] for w in wives] == ["My wife's name is Anna."]
    assert wives[0]["replaced"] == ["My wife's name is Marie."]           # one hop of history per record...
    memory.remember("My wife was Maya.", "people", False)               # a historical statement: no change
    out = memory.remember("Maybe my wife's name is Lucy.", "people", False)  # a guess beside a confirmed value: not stored
    assert out.startswith("That sounded uncertain") and "Anna" in out
    wives = [m for m in memory._all() if "wife" in m["fact"].lower() and "is" in m["fact"]]
    assert "My wife's name is Anna." in [w["fact"] for w in wives]
    importlib.reload(memory)
    assert [m["fact"] for m in memory._all() if "wife's name is" in m["fact"]] == ["My wife's name is Anna."]
    assert "Anna" in memory.recall("wife") and "Marie" not in memory.recall("wife").replace("replaces", "")


# -- what failed is remembered across conversations and restarts ---------------------------------------------------------
def test_a_failed_call_is_remembered_for_a_day_and_forgotten_when_it_works(monkeypatch):
    import importlib
    from core import feedback
    args = {"place": "Springfield, IL", "days": 1}
    assert feedback.warning("get_weather", args) == ""
    feedback.record("get_weather", args, "The weather service isn't reachable right now.\nDetails...")
    feedback.record("get_weather", args, "The weather service isn't reachable right now.")
    w = feedback.warning("get_weather", args)
    assert "failed 2 times recently" in w and "weather service isn't reachable" in w and "looks temporary" in w
    importlib.reload(feedback)                                             # a restart: still remembered (a file)
    assert feedback.recent("get_weather", args)["count"] == 2
    assert feedback.warning("get_weather", {"place": "Miami", "days": 1}) != ""   # an outage is the service's, whatever the arguments
    feedback.cleared("get_weather", args)                                  # it worked: forgotten
    assert feedback.warning("get_weather", args) == ""
    feedback.record("homelab_health", {}, "SSH key missing")
    monkeypatch.setattr(feedback.time, "time", lambda: time_now + 25 * 3600) if (time_now := __import__("time").time()) else None
    assert feedback.warning("homelab_health", {}) == ""                    # a day later: expired


def test_the_brain_tells_the_model_when_the_same_call_failed_before(monkeypatch):
    from types import SimpleNamespace
    from anthropic.types import TextBlock, ToolUseBlock
    from brain import llm
    from core import feedback
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    feedback.record("homelab_health", {}, "No SSH key for the Pi: run ssh-keygen on the laptop first.")
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    b._local_role, b._cloud_calls, b._failed_calls = "fast", 0, set()
    monkeypatch.setattr(registry, "run", lambda name, args: ("No SSH key for the Pi: run ssh-keygen on the laptop first.", True))
    turns = iter([SimpleNamespace(content=[ToolUseBlock(type="tool_use", id="a", name="homelab_health", input={})], stop_reason="tool_use"),
                  SimpleNamespace(content=[TextBlock(type="text", text="The Pi is down, sir; shall I try the drive mounts instead?")], stop_reason="end_turn")])
    monkeypatch.setattr(b, "_stream_turn", lambda emit: next(turns))
    b.messages = [{"role": "user", "content": "How's the homelab?"}]
    b._loop(lambda s: None, 0)
    result = b.messages[2]["content"][0]["content"]
    assert "failed once in the last day" in result and feedback.recent("homelab_health", {})["count"] == 2
    monkeypatch.setattr(registry, "run", lambda name, args: ("All 14 apps up.", False))
    turns = iter([SimpleNamespace(content=[ToolUseBlock(type="tool_use", id="b", name="homelab_health", input={})], stop_reason="tool_use"),
                  SimpleNamespace(content=[TextBlock(type="text", text="All up, sir.")], stop_reason="end_turn")])
    b.messages = [{"role": "user", "content": "And now?"}]
    b._failed_calls = set()                                               # a new turn (ask() starts each one fresh)
    b._loop(lambda s: None, 0)
    assert feedback.recent("homelab_health", {}) is None                 # worked: the memory of failing is gone


def test_transient_failures_are_told_apart_and_never_discourage_a_retry_after_recovery(monkeypatch):
    from core import feedback
    assert feedback.kind("The weather service isn't reachable right now.") == "transient"
    assert feedback.kind("The homelab Pi timed out.") == "transient"
    assert feedback.kind("Ollama is cooling down after 3 failures (circuit open).") == "transient"
    assert feedback.kind("Invalid call to open_app: missing argument(s): app.") == "failure"
    assert feedback.kind("Nothing installed matches 'Blender'.") == "failure"
    args = {"place": "Springfield, IL", "days": 1}
    feedback.record("get_weather", args, "The weather service isn't reachable right now.")
    assert feedback.warning("get_weather", args) == ""                            # one outage: not even mentioned
    feedback.record("get_weather", args, "The weather service isn't reachable right now.")
    w = feedback.warning("get_weather", args)
    assert "looks temporary" in w and "one retry is fine" in w and "don't retry" not in w
    now = feedback.time.time()
    monkeypatch.setattr(feedback.time, "time", lambda: now + 16 * 60)
    assert feedback.recent("get_weather", args) is None                           # 15 minutes later: forgotten
    monkeypatch.setattr(feedback.time, "time", lambda: now)                       # (never monkeypatch.undo(): it would
    feedback.record("open_app", {"app": "Blendr"}, "Invalid call to open_app: no such app 'Blendr'.")   # drop the sandbox)
    w = feedback.warning("open_app", {"app": "Blendr"})
    assert "failed once in the last day" in w and "don't retry" in w             # a reproducible one: from the first
    monkeypatch.setattr(feedback.time, "time", lambda: now + 23 * 3600)
    assert feedback.recent("open_app", {"app": "Blendr"}) is not None             # still remembered after 23 h
    monkeypatch.setattr(feedback.time, "time", lambda: now + 25 * 3600)
    assert feedback.recent("open_app", {"app": "Blendr"}) is None


def test_failure_records_hold_no_credentials_or_blobs_and_stay_bounded():
    from core import feedback, storage
    row = feedback.record("pair_phone", {"person": "me", "token": "ghp_abcdefghijklmnop12345", "password": "hunter2",
                                         "image": "data:image/png;base64," + "A" * 500}, "Bearer ghp_abcdefghijklmnop12345 rejected")
    assert "hunter2" not in row["key"] and "ghp_" not in row["key"] and "AAAA" not in row["key"]
    assert "[redacted]" in row["key"] and "[" in row["key"] and row["error"] == "[error text withheld: credential-like]"
    for i in range(250):
        feedback.record("open_app", {"app": f"app{i}"}, "Invalid call: no such app.")
    rows = storage.load(feedback._file(), [])
    assert len(rows) <= feedback.MAX_ROWS and any("app249" in r["key"] for r in rows)
