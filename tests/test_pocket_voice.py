"""The Pocket TTS English voice (audio/pocketvoice.py) and how audio.tts uses it: streamed on its first chunk, Kokoro
whenever it cannot speak. No real model is loaded: a fake model stands in (conftest marks the real one not installed)."""
import sys
import threading
import time
import types
from unittest.mock import Mock

import numpy as np
import pytest

from audio import pocketvoice, tts


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_torch(monkeypatch):
    threads = {"n": 20}
    stub = types.SimpleNamespace(get_num_threads=lambda: threads["n"], no_grad=_NoGrad,
                                 set_num_threads=lambda n: threads.__setitem__("n", n))
    monkeypatch.setitem(sys.modules, "torch", stub)
    return threads


class FakeModel:
    """Yields float chunks like Pocket TTS's generate_audio_stream (numpy instead of torch tensors)."""
    sample_rate = 24000

    def __init__(self, chunks=3, size=2400, delay=0.0, fail=None, first_delay=0.0):
        self.chunks, self.size, self.delay, self.fail, self.first_delay = chunks, size, delay, fail, first_delay
        self.said = []

    def get_state_for_audio_prompt(self, name):
        return {"voice": name}

    def generate_audio_stream(self, state, text, stop=None):
        self.said.append(text)
        if self.fail is not None:
            raise self.fail
        time.sleep(self.first_delay)
        for _ in range(self.chunks):
            if stop is not None and stop.is_set():
                return
            time.sleep(self.delay)
            yield np.full(self.size, 0.1, dtype=np.float32)


@pytest.fixture
def model(monkeypatch, fake_torch):
    fake = FakeModel()
    monkeypatch.setattr(pocketvoice, "installed", lambda: True)
    monkeypatch.setattr(pocketvoice, "_model", fake)
    monkeypatch.setattr(pocketvoice, "_states", {})
    monkeypatch.setattr(pocketvoice, "_gain", {})
    return fake


def test_voice_names():
    assert pocketvoice.is_pocket("pocket:charles") and not pocketvoice.is_pocket("kokoro:bm_lewis")
    assert not pocketvoice.is_pocket(None)
    assert pocketvoice.voice_name("pocket:paul") == "paul"
    assert pocketvoice.voice_name("pocket:") == pocketvoice.DEFAULT_VOICE
    assert tts.kokoro_voice("pocket:charles") == tts.KOKORO_ENGLISH_FALLBACK    # its fallback is the Kokoro voice


def test_money_and_percent_are_words_for_this_model():
    said = pocketvoice.lm_spoken("about $2.49 a dozen, $1.01, $1,200 or $5.00; 75% & 12.5 %")
    assert said == ("about 2 dollars and 49 cents a dozen, 1 dollar and 1 cent, 1200 dollars or 5 dollars; "
                    "75 percent and 12.5 percent")
    assert pocketvoice.lm_spoken("It's 62 degrees at 9:30 PM.") == "It's 62 degrees at 9:30 PM."


def test_voice_gain_brings_a_voice_to_kokoros_loudness():
    quiet = np.full(24000, 0.032, dtype=np.float32)
    assert pocketvoice.voice_gain(quiet) == pytest.approx(2.0)
    assert pocketvoice.voice_gain(np.full(24000, 0.5, dtype=np.float32)) == 0.5          # never below half
    assert pocketvoice.voice_gain(np.full(24000, 0.011, dtype=np.float32)) == 4.0        # never above 4x
    assert pocketvoice.voice_gain(np.zeros(24000, dtype=np.float32)) == 1.0              # silence: unknown


def test_stream_yields_int16_chunks_with_the_voice_gain(model, fake_torch):
    pocketvoice._gain["charles"] = 2.0
    chunks = list(pocketvoice.stream("Total $4.29.", "pocket:charles"))
    assert [c.dtype for c in chunks] == [np.int16] * 3
    assert int(chunks[0][0]) == int(0.2 * 32767)
    assert model.said == ["Total 4 dollars and 29 cents."]
    assert fake_torch["n"] == 20                       # the process's thread setting is back after the sentence
    assert pocketvoice._lock.acquire(blocking=False)   # and the lock is free
    pocketvoice._lock.release()


def test_warm_up_measures_the_gain_once(model):
    pocketvoice.warm_up("pocket:charles")
    assert model.said == [pocketvoice.WARM_LINE]
    assert pocketvoice._gain["charles"] == pytest.approx(0.64, rel=1e-3)    # 0.064 / 0.1 (int16 rounding)


def test_no_first_sound_in_time_raises_and_cools_down(model, monkeypatch):
    monkeypatch.setattr(pocketvoice, "FIRST_CHUNK_TIMEOUT", 0.05)
    model.first_delay = 0.3
    with pytest.raises(TimeoutError):
        list(pocketvoice.stream("Hello there.", "pocket:charles", first_timeout=0.05))
    assert not pocketvoice.usable()
    with pytest.raises(RuntimeError):
        next(pocketvoice.stream("Hello again.", "pocket:charles"))


def test_a_model_error_before_the_first_chunk_raises_and_cools_down(model):
    model.fail = RuntimeError("synthetic")
    with pytest.raises(RuntimeError, match="synthetic"):
        list(pocketvoice.stream("Hello.", "pocket:charles"))
    assert not pocketvoice.usable()


def test_a_load_failure_cools_down(monkeypatch, fake_torch):
    monkeypatch.setattr(pocketvoice, "installed", lambda: True)
    monkeypatch.setattr(pocketvoice, "_states", {})
    monkeypatch.setattr(pocketvoice, "_load", Mock(side_effect=OSError("no weights")))
    with pytest.raises(OSError):
        list(pocketvoice.stream("Hello.", "pocket:charles"))
    assert not pocketvoice.usable()


def test_two_slow_sentences_in_a_row_hand_over_to_kokoro(model, monkeypatch):
    monkeypatch.setattr(pocketvoice, "SLOW_RTF", 0.0)           # every sentence counts as slow
    model.delay = 0.02                                          # (Windows' clock ticks every 15 ms)
    list(pocketvoice.stream("One.", "pocket:charles"))
    assert pocketvoice.usable()
    list(pocketvoice.stream("Two.", "pocket:charles"))
    assert not pocketvoice.usable()


def test_alive_false_stops_the_sentence_and_frees_the_lock(model):
    model.chunks = 50
    got = list(pocketvoice.stream("A long sentence.", "pocket:charles", alive=lambda: False))
    assert got == []
    assert pocketvoice._lock.acquire(timeout=1)
    pocketvoice._lock.release()


def test_not_installed_is_not_usable():
    assert not pocketvoice.usable()               # conftest: the real model counts as not installed


# -- audio.tts ----------------------------------------------------------------------------------------------------------
def test_say_with_uses_pocket_when_it_can(monkeypatch):
    speaker = tts.Speaker("pocket:charles", "+22%", muted=True)
    monkeypatch.setattr(pocketvoice, "usable", lambda: True)
    monkeypatch.setattr(pocketvoice, "say", lambda text, voice: np.ones(10, dtype=np.int16))
    monkeypatch.setattr(tts, "kokoro_say", Mock(side_effect=AssertionError("not Kokoro")))
    assert len(speaker._say_with("Short line.", "pocket:charles")) == 10


def test_say_with_falls_back_to_kokoro_while_pocket_cools_down(monkeypatch):
    speaker = tts.Speaker("pocket:charles", "+12%", muted=True)
    calls = []
    monkeypatch.setattr(pocketvoice, "usable", lambda: False)
    monkeypatch.setattr(tts, "kokoro_say", lambda text, voice, rate: calls.append((voice, rate)) or np.ones(3, np.int16))
    speaker._say_with("Hello.", "pocket:charles")
    assert calls == [("kokoro:bm_lewis", "+12%")]


def test_a_long_phone_reply_is_kokoros(monkeypatch):
    speaker = tts.Speaker("pocket:charles", "+12%", muted=True)
    monkeypatch.setattr(pocketvoice, "usable", lambda: True)
    monkeypatch.setattr(pocketvoice, "say", Mock(side_effect=AssertionError("too long for the PC hop")))
    calls = []
    monkeypatch.setattr(tts, "kokoro_say", lambda text, voice, rate: calls.append(voice) or np.ones(3, np.int16))
    speaker._say_with("word " * 60, "pocket:charles")
    assert calls == ["kokoro:bm_lewis"]


def test_a_failing_pocket_sentence_is_said_by_kokoro(monkeypatch):
    speaker = tts.Speaker("pocket:charles", "+12%", muted=True)
    monkeypatch.setattr(pocketvoice, "usable", lambda: True)
    monkeypatch.setattr(pocketvoice, "say", Mock(side_effect=RuntimeError("synthetic")))
    monkeypatch.setattr(tts, "kokoro_say", lambda text, voice, rate: np.full(5, 7, np.int16))
    assert list(speaker._synthesize("Good evening, sir.")) == [7] * 5


def test_local_only_mode_still_uses_the_local_pocket_voice(monkeypatch):
    speaker = tts.Speaker("pocket:charles", "+12%", muted=True)
    monkeypatch.setenv("JARVIS_LOCAL_ONLY", "1")
    monkeypatch.setattr(pocketvoice, "usable", lambda: True)
    monkeypatch.setattr(pocketvoice, "say", lambda text, voice: np.full(4, 3, np.int16))
    monkeypatch.setattr(tts, "kokoro_say", Mock(side_effect=AssertionError("not Kokoro")))
    assert list(speaker._synthesize("Good evening, sir.")) == [3] * 4


def _streaming(monkeypatch, chunks, fail=None):
    def stream(text, voice, alive=lambda: True):
        if fail is not None:
            raise fail
        for c in chunks:
            if not alive():
                return
            yield c
    monkeypatch.setattr(pocketvoice, "usable", lambda: True)
    monkeypatch.setattr(pocketvoice, "stream", stream)


def test_an_english_sentence_is_queued_on_its_first_chunk(monkeypatch):
    speaker = tts.Speaker("pocket:charles", muted=True)
    _streaming(monkeypatch, [np.full(100, 1, np.int16), np.full(50, 2, np.int16)])
    assert speaker._stream_english(0, "It's 62°F in Springfield.")
    gen, text, clip = speaker._audio_q.get_nowait()
    assert (gen, text) == (0, "It's 62°F in Springfield.")
    assert isinstance(clip, tts.LiveClip) and clip.done and len(clip) == 150
    assert speaker._synthesizing is None


def test_other_languages_and_failures_take_the_usual_path(monkeypatch):
    speaker = tts.Speaker("pocket:charles", muted=True)
    _streaming(monkeypatch, [np.full(10, 1, np.int16)])
    assert not speaker._stream_english(0, "Bonjour, comment allez-vous aujourd'hui ?")
    _streaming(monkeypatch, [], fail=TimeoutError("no first sound"))
    assert not speaker._stream_english(0, "Good evening, sir.")
    assert speaker._audio_q.empty()
    assert not tts.Speaker("kokoro:bm_lewis", muted=True)._stream_english(0, "Good evening, sir.")


def test_a_stop_while_streaming_ends_the_clip(monkeypatch):
    speaker = tts.Speaker("pocket:charles", muted=True)
    chunks = [np.full(10, 1, np.int16)] * 5

    seen = []

    def stream(text, voice, alive=lambda: True):
        yield chunks[0]
        speaker._generation += 1
        for c in chunks[1:]:
            seen.append(alive())
            if not alive():
                return
            yield c
    monkeypatch.setattr(pocketvoice, "usable", lambda: True)
    monkeypatch.setattr(pocketvoice, "stream", stream)
    assert speaker._stream_english(0, "Good evening, sir.")
    _, _, clip = speaker._audio_q.get_nowait()
    assert clip.done and len(clip) == 10 and seen == [False]


def test_the_play_loop_plays_a_clip_that_is_still_growing(monkeypatch):
    speaker = tts.Speaker("unused", muted=True)
    clip = tts.LiveClip("Good evening, sir.")
    clip.add(np.full(1200, 5, np.int16))
    speaker._audio_q.put((0, "Good evening, sir.", clip))

    def fill():
        for _ in range(4):
            time.sleep(0.02)
            clip.add(np.full(1200, 5, np.int16))
        clip.finish()
    threading.Thread(target=fill, daemon=True).start()

    class Done(Exception):
        pass
    original_get = speaker._audio_q.get

    def get():
        if speaker._audio_q.empty():
            raise Done()
        return original_get()
    monkeypatch.setattr(speaker._audio_q, "get", get)
    stream = Mock()
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=lambda **kw: stream))
    with pytest.raises(Done):
        speaker._play_loop()
    written = sum(len(c.args[0]) for c in stream.write.call_args_list)
    assert written == 6000
    assert speaker.heard == ["Good evening, sir."]
    assert speaker._playing is None and not speaker.busy()


def test_a_cut_growing_clip_counts_only_what_played(monkeypatch):
    speaker = tts.Speaker("unused", muted=True)
    clip = tts.LiveClip("one two three four five six seven eight nine ten")
    clip.add(np.full(2400, 5, np.int16))
    with speaker._ledger:
        speaker._clip = [0, "one two three four five six seven eight nine ten", clip.estimate() // 2, clip.estimate()]
    speaker.muted = False                      # (made muted so no audio threads start; stop() skips muted speakers)
    speaker.stop()
    assert speaker.heard and speaker.heard[0].endswith("…")
    assert speaker.unsaid == ["one two three four five six seven eight nine ten"]


def test_live_clip_estimate_and_growth():
    clip = tts.LiveClip("word " * 33)                     # about 10 s expected
    assert clip.estimate() == 10 * 24000
    clip.add(np.ones(24000 * 5, np.int16))               # grows past its first buffer
    assert len(clip) == 24000 * 5 and clip.view()[-1] == 1
    clip.finish()
    assert clip.estimate() == 24000 * 5
    assert clip.wait_for(10 ** 9, timeout=0.01) == 24000 * 5


def test_an_unmuted_speaker_warms_the_pocket_voice(monkeypatch):
    targets = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            targets.append((getattr(target, "__name__", ""), args))

        def start(self):
            pass
    monkeypatch.setattr(tts.threading, "Thread", FakeThread)
    tts.Speaker("pocket:charles")
    assert ("warm_up", ("pocket:charles",)) in targets
    assert ("warm_up_kokoro", ("kokoro:bm_lewis",)) in targets   # Kokoro, the fallback, stays warm too


# -- the recommended Kokoro blend ---------------------------------------------------------------------------------------
def test_a_kokoro_blend_reaches_the_pipeline_as_one_british_voice(monkeypatch):
    seen = {}

    class Pipe:
        def __call__(self, text, voice, speed):
            seen.update(voice=voice, speed=speed)
            return [(None, None, np.full(2400, 0.5, np.float32))]

    monkeypatch.setattr(tts, "_kokoro_down_until", 0.0)
    monkeypatch.setattr(tts, "_kokoro_pipe", lambda lang: seen.setdefault("lang", lang) and Pipe())
    out = tts.kokoro_say("Good evening, sir.", "kokoro:bm_lewis,bm_fable", "+22%")
    assert seen == {"lang": "b", "voice": "bm_lewis,bm_fable", "speed": pytest.approx(1.22)}
    assert out.dtype == np.int16 and len(out)
    assert tts.kokoro_voice("kokoro:bm_lewis,bm_fable") == "kokoro:bm_lewis,bm_fable"


def test_voice_clarity_keeps_a_blend_as_one_voice():
    import re
    assert re.split(r",(?=\w+:)", "kokoro:bm_lewis,bm_fable,pocket:charles,kokoro:bm_george") == [
        "kokoro:bm_lewis,bm_fable", "pocket:charles", "kokoro:bm_george"]
    src = open("scripts/voice_clarity.py", encoding="utf-8").read()
    assert 're.split(r",(?=' + chr(92) + 'w+:)", a.voices)' in src


def test_a_slow_cold_load_is_not_counted_as_slowness(model, monkeypatch):
    def slow_state(name):
        time.sleep(0.5)                       # longer than the 0.3 s of speech the fake makes
        return {"voice": name}
    monkeypatch.setattr(pocketvoice, "_state", slow_state)
    list(pocketvoice.stream("One.", "pocket:charles"))
    assert pocketvoice._slow == 0


def test_a_sentence_still_inside_the_model_makes_the_next_one_kokoros(model, monkeypatch):
    stuck = threading.Event()
    worker = threading.Thread(target=stuck.wait, daemon=True)
    worker.start()
    monkeypatch.setattr(pocketvoice, "_worker", worker)
    try:
        with pytest.raises(TimeoutError):
            next(pocketvoice.stream("Hello.", "pocket:charles"))
        assert pocketvoice._lock.acquire(blocking=False)      # the lock was given back
        pocketvoice._lock.release()
    finally:
        stuck.set()
    worker.join()
    assert len(list(pocketvoice.stream("Hello.", "pocket:charles"))) == 3
