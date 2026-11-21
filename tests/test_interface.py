"""Offline tests for dictation logic and the HUD client (no real keyboard/window)."""
import time
from types import SimpleNamespace

import numpy as np


class FakeListener:
    def __init__(self):
        self.tapping = False

    def start_tap(self):
        self.tapping = True

    def stop_tap(self):
        self.tapping = False
        return np.zeros(16000, np.int16)


class FakeSTT:
    def transcribe(self, audio):
        return "Bonjou, mwen la."


def _run(monkeypatch, hold: float, other_key: bool = False):
    import keyboard
    import pyperclip

    from core import hotkeys

    sent, clip = [], {"v": "previous"}
    monkeypatch.setattr(keyboard, "send", lambda k: sent.append((k, clip["v"])))
    monkeypatch.setattr(pyperclip, "copy", lambda v: clip.__setitem__("v", v))
    monkeypatch.setattr(pyperclip, "paste", lambda: clip["v"])
    clock = {"t": 1000.0}  # a fake clock, so a busy machine can't change how long the key was "held"
    monkeypatch.setattr(hotkeys.time, "monotonic", lambda: clock["t"])
    hk = hotkeys.Hotkeys(FakeListener(), FakeSTT())
    hk._dictate_down(None)
    if other_key:
        hk._any_key(SimpleNamespace(event_type="down", name="c"))
    clock["t"] += hold
    hk._dictate_up(None)
    if hk.worker:
        hk.worker.join(10)  # wait for the typing thread, however busy the machine is
    return sent, clip["v"]


def test_dictation_pastes_text_and_restores_clipboard(monkeypatch):
    sent, clip_after = _run(monkeypatch, 0.6)
    assert sent == [("ctrl+v", "Bonjou, mwen la.")]
    assert clip_after == "previous"


def test_short_press_is_not_dictation(monkeypatch):
    sent, _ = _run(monkeypatch, 0.1)
    assert sent == []


def test_ctrl_shortcut_is_not_dictation(monkeypatch):
    sent, _ = _run(monkeypatch, 0.6, other_key=True)
    assert sent == []


def test_hud_calls_are_noops_when_not_running():
    from core import hud

    hud.state("thinking")
    hud.line("you", "hello")
    hud.badges(eyes=True)
    hud.media(image=None)  # bad input is ignored, never raises
    assert not hud.running()
