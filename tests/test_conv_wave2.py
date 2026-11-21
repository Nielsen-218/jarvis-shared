"""Conversation wave 2 (2026-11-14): the first thing he says after a start does not pay for first-use work.
Measured: first spoken turn after 10 restarts 7.2 s (later turns 2.9 s); the recogniser's first run 1.36 s vs 0.4 s."""
import threading

import numpy as np
import pytest

from audio import stt as sttmod
from core import warmth


class _Model:
    """Stands in for faster-whisper: records how it was asked, decodes only when its segments are read."""

    def __init__(self, fail: bool = False):
        self.calls, self.read, self.detected, self.fail = [], 0, 0, fail

    def detect_language(self, samples):
        self.detected += 1
        return "en", 0.9, []

    def transcribe(self, samples, **kw):
        if self.fail:
            raise RuntimeError("CUDA error: device-side assert")
        self.calls.append((len(samples), float(np.abs(samples).max()), kw))

        def segments():
            self.read += 1
            yield type("S", (), {"text": "you"})()

        return segments(), None


def _transcriber(model, multilingual=True):
    t = object.__new__(sttmod.Transcriber)
    t.model, t.multilingual, t.on_gpu, t._lock = model, multilingual, True, threading.Lock()
    t.last_language, t.last_confidence, t.forced_language, t.extra_language = "fr", 0.5, None, None
    return t


def test_warm_runs_the_decoder_and_the_voice_filter_once_each_and_keeps_nothing():
    model = _Model()
    t = _transcriber(model)
    took = t.warm()
    assert took > 0 and model.detected == 1 and model.read == 2               # both generators were consumed
    (n1, peak1, kw1), (n2, _peak2, kw2) = model.calls
    assert n1 == n2 == 24000 and peak1 < 0.05                                 # 1.5 s of quiet noise, never speech level
    assert kw1["vad_filter"] is False and kw1["beam_size"] == 5 and kw2["vad_filter"] is True
    assert "hotwords" not in kw1                                              # reads no window list, names no app
    assert (t.last_language, t.last_confidence) == ("fr", 0.5)                # nothing was "heard"


def test_warm_on_an_english_only_model_skips_the_language_pass():
    model = _Model()
    assert _transcriber(model, multilingual=False).warm() > 0 and model.detected == 0


def test_a_failing_warm_changes_nothing_and_does_not_raise():
    t = _transcriber(_Model(fail=True))
    assert t.warm() == 0.0 and t.model.fail and not t._lock.locked()          # no heal, no reload, lock released


def test_warm_waits_for_a_transcription_in_progress():
    t = _transcriber(_Model())
    with t._lock:
        done = []
        th = threading.Thread(target=lambda: done.append(t.warm()), daemon=True)
        th.start()
        th.join(0.2)
        assert not done                                                       # his words go first
    th.join(5)
    assert done and done[0] > 0


def test_rehearsal_only_decides(monkeypatch):
    """No model is asked, no skill runs and nothing is said or written while the deciders are walked."""
    from core import intelligence
    from tools import registry

    def never(*a, **k):
        raise AssertionError("the rehearsal must not do this")

    monkeypatch.setattr(intelligence, "complete_local", never)
    monkeypatch.setattr(intelligence, "request", never)
    monkeypatch.setattr(registry, "run", never)
    walked = []
    from core import chatturn
    real = chatturn.plan
    monkeypatch.setattr(chatturn, "plan", lambda *a, **k: (walked.append(a[1][-1]["content"]), real(*a, **k))[1])
    took = warmth.rehearse()
    assert took >= 0 and walked == list(warmth._REHEARSAL)                    # every stock line reached the deciders


def test_a_broken_decider_does_not_stop_the_rehearsal(monkeypatch):
    from core import chatturn, speech
    monkeypatch.setattr(chatturn, "plain_talk", lambda line: 1 / 0)
    polished = []
    real = speech.Polisher.finish
    monkeypatch.setattr(speech.Polisher, "finish", lambda self: (polished.append(1), real(self))[1])
    assert warmth.rehearse() >= 0 and len(polished) == len(warmth._REHEARSAL)


def test_first_turn_runs_the_rehearsal(monkeypatch):
    from core import gateway_context, intelligence
    ran = []
    monkeypatch.setattr(intelligence, "prewarm_chat", lambda *a, **k: None)
    monkeypatch.setattr(gateway_context, "build", lambda *a, **k: None)
    monkeypatch.setattr(warmth, "rehearse", lambda: ran.append(1) or 0.0)
    warmth.first_turn()
    assert ran == [1]
