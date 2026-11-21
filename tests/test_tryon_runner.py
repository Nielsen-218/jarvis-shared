"""The try-on child runner and the VRAM hand-over, with fakes: no GPU, no model, no Ollama."""
import io

import pytest

from core import warmth
from imaging import person as imaging_person
from tools import studio


class FakeProc:
    """A child process that prints `out`, then exits (or never does, when `hang`)."""
    def __init__(self, out="", err="", hang=False):
        self.stdout, self.stderr, self.hang, self.killed = io.StringIO(out), io.StringIO(err), hang, False
        self.returncode = None

    def poll(self):
        return None if (self.hang and not self.killed) else 0

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def runner(monkeypatch):
    log = {"warm": 0, "lease": []}
    monkeypatch.setattr(studio, "_evict_brain_while", lambda stop: None)
    monkeypatch.setattr(studio, "_warm_brain", lambda: log.__setitem__("warm", log["warm"] + 1))
    monkeypatch.setattr(warmth, "render_lease", lambda on: log["lease"].append(on))
    return log


def _popen(monkeypatch, proc):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)


def test_the_runner_streams_progress_and_returns_the_path(monkeypatch, runner):
    _popen(monkeypatch, FakeProc("PROGRESS::Loading the image model\nPROGRESS::Drawing, step 1 of 8\nRENDERED::C:/x/out.png\n"))
    seen = []
    assert str(studio._run_child("f()", progress=seen.append)).endswith("out.png")
    assert seen == ["Loading the image model", "Drawing, step 1 of 8"]
    assert runner["lease"] == [True, False] and runner["warm"] == 1     # the card was held, then the brain loaded back


def test_a_silent_child_is_stopped_by_the_stall_limit_not_a_blind_clock(monkeypatch, runner):
    proc = FakeProc(hang=True)
    _popen(monkeypatch, proc)
    with pytest.raises(RuntimeError, match="stopped responding"):
        studio._run_child("f()", timeout=60, stall=1)
    assert proc.killed and runner["lease"] == [True, False] and runner["warm"] == 1


def test_a_child_that_runs_past_the_hard_limit_is_stopped_and_says_so(monkeypatch, runner):
    proc = FakeProc(hang=True)
    _popen(monkeypatch, proc)
    import time
    t = iter(range(0, 10_000, 400))
    monkeypatch.setattr(time, "monotonic", lambda: next(t))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="longer than"):
        studio._run_child("f()", timeout=600, stall=10_000)
    assert proc.killed


def test_runner_errors_are_translated(monkeypatch, runner):
    _popen(monkeypatch, FakeProc(err="boom GPU_BUSY: x"))
    with pytest.raises(RuntimeError, match="GPU is busy"):
        studio._run_child("f()")
    _popen(monkeypatch, FakeProc(err="NO_GARMENT::That wardrobe item (x) has no photo to try on."))
    with pytest.raises(ValueError, match="no photo"):
        studio._run_child("f()")


def test_the_keeper_does_not_load_the_brain_back_under_a_render(monkeypatch):
    loaded = []
    monkeypatch.setattr(warmth, "jarvis_models", lambda: ["brain"])
    monkeypatch.setattr(warmth, "resident", lambda: {})
    monkeypatch.setattr(warmth, "practising", lambda: False)
    monkeypatch.setattr(warmth, "room_for", lambda gb: True)
    monkeypatch.setattr(warmth, "load", lambda name: loaded.append(name) or True)
    monkeypatch.setattr(warmth, "_note", lambda text: None)
    warmth.render_lease(True)
    try:
        assert warmth.warm("tick") == [] and loaded == []
    finally:
        warmth.render_lease(False)
    assert warmth.warm("render finished") == ["brain"] and loaded == ["brain"]


def test_a_forgotten_render_lease_expires(monkeypatch):
    import time
    warmth.render_lease(True)
    try:
        assert warmth.rendering()
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now + warmth.RENDER_LEASE_S + 1)
        assert not warmth.rendering()
    finally:
        warmth.render_lease(False)


def test_the_brain_is_evicted_while_the_render_runs(monkeypatch):
    import threading
    stop, calls = threading.Event(), []
    monkeypatch.setattr(studio, "_EVICT_EVERY_S", 0.01)
    monkeypatch.setattr(studio, "_loaded_ollama_models", lambda: ["qwen3.5:9b"])
    monkeypatch.setattr(studio, "_ollama", lambda keep, model="": calls.append((keep, model)) or stop.set())
    studio._evict_brain_while(stop)
    assert calls and calls[0] == (0, "qwen3.5:9b")


# -- the person crop: a head-to-toe phone photo is not cut off at the knees ---------------------------------
def _img(w, h):
    from PIL import Image
    return Image.new("RGB", (w, h), (120, 120, 120))


def test_a_tall_full_body_photo_is_kept_whole(monkeypatch):
    monkeypatch.setattr(imaging_person, "find_face", lambda img, within=None: (300, 150, 200, 270))
    monkeypatch.setattr(imaging_person, "_find_body", lambda img: None)
    assert imaging_person.crop_to_person(_img(772, 2048)).size == (772, 2048)


def test_a_wide_photo_is_still_cropped_to_the_person(monkeypatch):
    monkeypatch.setattr(imaging_person, "find_face", lambda img, within=None: (900, 300, 120, 160))
    monkeypatch.setattr(imaging_person, "_find_body", lambda img: None)
    assert imaging_person.crop_to_person(_img(1920, 1080)).size != (1920, 1080)


def test_the_real_face_beats_a_tiny_decoy_with_higher_confidence(monkeypatch):
    import cv2
    import numpy as np

    class Det:
        def detect(self, bgr):
            return None, np.array([[2542, 1304, 38, 50, *([0] * 10), 0.75], [778, 774, 578, 582, *([0] * 10), 0.63]], dtype=float)
    monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(lambda *a, **k: Det()))
    face = imaging_person.find_face(_img(1335, 3540))
    assert face[2] > 200
