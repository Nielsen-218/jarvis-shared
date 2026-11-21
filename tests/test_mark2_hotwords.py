"""Mark II: Whisper is given the owner's app names (faster-whisper hotwords) so "Claude" is not heard as "Cloud"/"clod" and
"ChatGPT" not as "chat TVT". Measured with scripts/stt_hotwords_bench.py before deploy."""
import numpy as np
import pytest

from audio import stt


class FakeModel:
    def __init__(self, text="Close ChatGPT."):
        self.calls, self.text = [], text

    def transcribe(self, samples, **kw):
        self.calls.append(kw)
        return [type("Seg", (), {"text": self.text})()], None


def _transcriber(model):
    t = stt.Transcriber.__new__(stt.Transcriber)
    t.model, t.on_gpu = model, True
    return t


@pytest.fixture(autouse=True)
def no_desktop(monkeypatch):
    from core import targets
    monkeypatch.setattr(targets, "open_apps", lambda windows=None: [{"app": "chatgpt", "process": "ChatGPT.exe", "title": "ChatGPT"},
                                                                   {"app": "obsidian", "process": "Obsidian.exe", "title": "Obsidian"}])
    monkeypatch.delenv("JARVIS_HOTWORDS", raising=False)


def test_names_are_period_separated_core_first_then_owner_then_open_apps(monkeypatch):
    monkeypatch.setenv("JARVIS_HOTWORDS", "Kokoro, Living Shadow World")
    words = stt.hotwords()
    assert words.startswith("Jarvis. ChatGPT. Claude.") and words.endswith(".")
    assert "Kokoro. Living Shadow World." in words and "Obsidian." in words
    assert words.count("ChatGPT") == 1                              # an open app already in the core list is not repeated
    assert len(words) <= stt.HOTWORDS_MAX_CHARS and "," not in words


def test_the_list_stays_short_however_many_apps_are_open(monkeypatch):
    from core import targets
    monkeypatch.setattr(targets, "open_apps", lambda windows=None: [{"app": f"app number {i}", "process": "", "title": ""} for i in range(80)])
    assert len(stt.hotwords()) <= stt.HOTWORDS_MAX_CHARS


def test_english_gets_the_names_french_and_kreyol_do_not():
    model = FakeModel()
    t = _transcriber(model)
    t._run(np.zeros(16000, np.float32), "en")
    t._run(np.zeros(16000, np.float32), "fr")
    t._run(np.zeros(16000, np.float32), "ht")
    assert "hotwords" in model.calls[0] and "Claude." in model.calls[0]["hotwords"]
    assert "hotwords" not in model.calls[1] and "hotwords" not in model.calls[2]


def test_off_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_HOTWORDS", "off")
    model = FakeModel()
    _transcriber(model)._run(np.zeros(16000, np.float32), "en")
    assert "hotwords" not in model.calls[0]
    assert "off" not in stt.hotwords().casefold().split(". ")


def test_a_desktop_that_cannot_be_read_still_gives_the_core_names(monkeypatch):
    from core import targets
    def broken(windows=None):
        raise OSError("no desktop")
    monkeypatch.setattr(targets, "open_apps", broken)
    assert stt.hotwords().startswith("Jarvis. ChatGPT.")


@pytest.mark.parametrize("heard, fixed", [
    ("OpenShadowWorld", "Open Shadow World"), ("CloseGithub", "Close Github"), ("OpenClaude.", "Open Claude"),
    ("MinimizeChatGPT", "Minimize Chat GPT"),
    ("Close ChatGPT", "Close ChatGPT"), ("ChatGPT", "ChatGPT"), ("Openness", "Openness"), ("Hello there", "Hello there"),
    ("", ""),
])
def test_a_command_glued_to_its_name_is_split(heard, fixed):
    assert stt.unglue(heard) == fixed


def test_run_returns_the_unglued_text():
    assert _transcriber(FakeModel("OpenShadowWorld"))._run(np.zeros(16000, np.float32), "en") == "Open Shadow World"
