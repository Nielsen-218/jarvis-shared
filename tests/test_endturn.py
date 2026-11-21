"""End of turn (audio/endturn + audio/listener.record_command): a pause mid-thought no longer ends his command.

Live 2026-11-09: "Scan my PC and look for." / "Look for." / "Things that are wasting my memory..." became three turns.
The listener now asks Smart Turn v3.2 at the first pause and keeps listening while the words sound unfinished. Synthetic
80 ms frames and a stub model here: no microphone, no model file needed (one skippable test uses the real model)."""
import ast
import hashlib
import time
from pathlib import Path

import numpy as np
import pytest

from audio import endturn, listener as listener_module
from audio.listener import FRAME, FRAME_SECS, Listener

SPEECH, SILENCE = 1000, 0


def script(*parts):
    """('s', seconds) / ('q', seconds) -> frames whose first sample says speech (1000) or silence (0)."""
    frames = []
    for kind, seconds in parts:
        frames += [np.full(FRAME, SPEECH if kind == "s" else SILENCE, np.int16)] * int(round(seconds / FRAME_SECS))
    return frames


class Replay(Listener):
    """The real record_command on scripted frames (no microphone, no VAD model)."""

    def __init__(self, frames, model=None):     # noqa: super().__init__ would open the microphone
        self.frames, self.read = list(frames), 0
        self.end_of_turn = model
        self.last_endpoint = {}

    def _next(self):
        if self.read >= len(self.frames):
            return np.full(FRAME, SILENCE, np.int16)
        self.read += 1
        return self.frames[self.read - 1]

    def _is_speech(self, frame):
        return bool(frame[0])


class Stub:
    """Answers the scripted probabilities in turn (the last one repeats) and records what it was shown."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, audio):
        self.calls.append(audio)
        return self.answers[min(len(self.calls), len(self.answers)) - 1]


def speech_frames(clip):
    return int(sum(1 for i in range(0, len(clip), FRAME) if clip[i]))


HESITANT = script(("q", 0.4), ("s", 0.8), ("q", 0.72), ("s", 0.8), ("q", 0.72), ("q", 4.0))


def test_a_mid_sentence_pause_that_sounds_unfinished_is_waited_through():
    model = Stub(0.2, 0.9)                                   # "Scan my PC and look for" ... "things that are wasting my memory"
    lst = Replay(HESITANT, model)
    clip = lst.record_command(smart=True)
    assert speech_frames(clip) == 20                          # ONE clip with both halves
    assert lst.last_endpoint["checks"] == [0.2, 0.9] and lst.last_endpoint["waited"] is True
    assert lst.last_endpoint["how"] == "complete"
    old = Replay(HESITANT).record_command(smart=True)          # no model: today's rule cut it at the pause
    assert speech_frames(old) == 10


def test_a_finished_sentence_ends_at_the_first_pause_like_today():
    frames = script(("q", 0.4), ("s", 0.8), ("q", 4.0))
    lst = Replay(frames, Stub(0.9))
    clip = lst.record_command(smart=True)
    plain = Replay(frames)
    assert len(clip) == len(plain.record_command(smart=True))   # not one frame later than the pause rule
    assert lst.last_endpoint["silence_s"] == pytest.approx(0.64) and lst.last_endpoint["waited"] is False


def test_unfinished_forever_still_ends_at_three_seconds_of_silence():
    model = Stub(0.1)
    lst = Replay(script(("q", 0.4), ("s", 0.8), ("q", 6.0)), model)
    lst.record_command(smart=True)
    assert lst.last_endpoint["how"] == "max_silence"
    assert 3.0 <= lst.last_endpoint["silence_s"] < 3.0 + 2 * FRAME_SECS
    assert lst.last_endpoint["checks"] == [0.1, 0.1]            # asked at the first pause and again at 2 s


@pytest.mark.parametrize("smart, model", [(False, Stub(0.1)), (True, None)])
def test_kreyol_or_no_model_keeps_the_pause_rule(smart, model):
    """main.hear passes smart=False for Kreyol (not one of the model's languages); a missing model is None."""
    lst = Replay(script(("q", 0.4), ("s", 0.8), ("q", 4.0)), model)
    lst.record_command(smart=smart)
    assert lst.last_endpoint["silence_s"] == pytest.approx(0.64) and lst.last_endpoint["how"] == "pause"
    assert model is None or model.calls == []


def test_a_model_error_mid_turn_fails_open_to_the_pause_rule():
    lst = Replay(script(("q", 0.4), ("s", 0.8), ("q", 4.0)), Stub(None))
    lst.record_command(smart=True)
    assert lst.last_endpoint["how"] == "pause" and lst.last_endpoint["silence_s"] == pytest.approx(0.64)


def test_the_model_is_never_asked_while_he_is_speaking():
    model = Stub(0.2, 0.2, 0.9)
    lst = Replay(script(("q", 0.4), ("s", 0.8), ("q", 0.72), ("s", 1.2), ("q", 2.2), ("s", 0.4), ("q", 4.0)), model)
    lst.record_command(smart=True)
    assert len(model.calls) == 3 and all(audio[-1] == SILENCE for audio in model.calls)
    # the first check of a pause hears his words and only a short tail of it (what the model was trained on)
    first = model.calls[0].reshape(-1, FRAME)[:, 0]
    assert (len(first) - 1 - first.nonzero()[0][-1]) * FRAME_SECS == pytest.approx(listener_module.SMART_TAIL)
    # calls: pause 1 at 0.6 s, pause 2 at 0.6 s, pause 2 again at 2 s. The re-check hears the whole pause: that much
    # silence is itself a sign he has finished.
    recheck = model.calls[2].reshape(-1, FRAME)[:, 0]
    assert (len(recheck) - 1 - recheck.nonzero()[0][-1]) * FRAME_SECS >= listener_module.SMART_RECHECK
    assert lst.last_endpoint["checks"] == [0.2, 0.2, 0.9]


def test_pre_roll_is_one_second_but_never_reaches_back_into_his_own_voice():
    frames = script(("q", 2.0), ("s", 0.8), ("q", 1.0))
    clip = Replay(frames).record_command()
    assert clip.reshape(-1, FRAME)[:, 0].tolist().index(SPEECH) == 11                 # ~0.9 s before the first word
    clip = Replay(frames).record_command(not_before=time.monotonic() - 0.2)          # Jarvis stopped 0.2 s ago
    assert clip.reshape(-1, FRAME)[:, 0].tolist().index(SPEECH) <= 3


def test_on_speech_is_called_once_at_his_first_word_not_when_his_turn_ends():
    """main.hear counts what had been asked at his first word (Brain.ask heard_upto), not after his end-of-turn pause."""
    lst = Replay(script(("q", 0.4), ("s", 0.8), ("q", 0.4), ("s", 0.8), ("q", 1.0)))
    seen = []
    lst.record_command(on_speech=lambda: seen.append(lst.read))
    assert seen == [6], "the first speech frame, read after five frames of silence"
    seen.clear()
    Replay(script(("s", 0.8), ("q", 1.0))).record_command(initial=[np.full(FRAME, SPEECH, np.int16)],
                                                          on_speech=lambda: seen.append(1))
    assert seen == [], "a cut-in's speech began before the call (respond() counted then)"


def test_hear_uses_the_endpointer_except_for_kreyol_and_pads_whisper():
    loop = next(n for n in ast.parse(Path("main.py").read_text(encoding="utf-8")).body
                if isinstance(n, ast.FunctionDef) and n.name == "voice_loop")
    hear = next(n for n in loop.body if isinstance(n, ast.FunctionDef) and n.name == "hear")
    src = ast.unparse(hear)
    assert "smart=stt.last_language != 'ht'" in src
    assert "stt.transcribe(endturn.pad_tail(audio))" in src
    assert "not ended.get('waited') and sounds_unfinished(text)" in src


# -- the model's input ---------------------------------------------------------------------------------------------------
def test_features_are_80_mels_by_800_frames_of_the_last_8_seconds():
    assert endturn.features(np.zeros(3 * 16000, np.int16)).shape == (80, 800)
    short = endturn.window(np.full(1600, 16384, np.int16))
    assert short.shape == (128000,) and short[0] == 0.0 and short[-1] == pytest.approx(0.5)    # zeros on the left
    long = endturn.window(np.arange(10 * 16000, dtype=np.float32) / 1e6)
    assert long.shape == (128000,) and long[-1] == pytest.approx((10 * 16000 - 1) / 1e6)       # the END is kept


def test_whisper_gets_half_a_second_of_silence_after_the_clip():
    padded = endturn.pad_tail(np.ones(1600, np.int16))
    assert len(padded) == 1600 + 8000 and not padded[1600:].any() and padded.dtype == np.int16


# -- loading fails open --------------------------------------------------------------------------------------------------
def test_it_is_off_until_switched_on(monkeypatch, tmp_path):
    """The design's acceptance (short commands commit within 0.1 s of the pause rule; 20 live commands) is not met yet:
    endpoint_eval had 'Open Blender.' wait 2.4 s longer. Until JARVIS_SMART_TURN=on, a 0.6 s pause ends a command."""
    assert endturn.enabled() is False
    loads = []
    monkeypatch.setattr(endturn.Path, "read_bytes", lambda self: loads.append(self) or b"")
    assert endturn.SmartTurn.load() is None and loads == []          # not even read from disk
    monkeypatch.setenv("JARVIS_SMART_TURN", "on")
    assert endturn.enabled() is True


def test_a_missing_model_file_means_no_model(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SMART_TURN", "on")
    assert endturn.SmartTurn.load(tmp_path / "missing.onnx") is None


def test_a_different_file_is_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SMART_TURN", "on")
    fake = tmp_path / "model.onnx"
    fake.write_bytes(b"not the pinned model")
    assert endturn.SmartTurn.load(fake) is None                                     # wrong SHA-256
    assert endturn.SmartTurn.load(fake, sha256=hashlib.sha256(b"not the pinned model").hexdigest()) is None   # onnx error


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("JARVIS_SMART_TURN", "off")
    assert endturn.SmartTurn.load() is None


def test_a_failing_session_answers_none():
    class Broken:
        def get_inputs(self):
            return [type("I", (), {"name": "input_features"})()]

        def run(self, *a):
            raise RuntimeError("synthetic")
    assert endturn.SmartTurn(Broken()).complete(np.zeros(16000, np.int16)) is None


@pytest.mark.serial   # a timing test: other tests running beside it slow the model down
@pytest.mark.skipif(not endturn.MODEL.exists(), reason="the Smart Turn model file is not on this machine")
def test_the_real_model_answers_quickly_on_one_core(monkeypatch):
    monkeypatch.setenv("JARVIS_SMART_TURN", "on")
    model = endturn.SmartTurn.load()
    assert model is not None
    audio = (np.sin(np.arange(32000) * 0.05) * 3000).astype(np.int16)
    model.complete(audio)                                     # first run: onnxruntime's one-time setup, not a check
    best = min(_timed(model, audio) for _ in range(20))       # the best of 20: a busy PC (Jarvis, a game) adds noise
    p = model.complete(audio)
    assert 0.0 <= p <= 1.0
    assert best < 0.05, f"one end-of-turn check took {best * 1000:.0f} ms"


def _timed(model, audio):
    started = time.perf_counter()
    model.complete(audio)
    return time.perf_counter() - started
