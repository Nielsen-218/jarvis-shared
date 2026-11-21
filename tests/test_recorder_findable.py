"""The Creole voice recorder is easy to find: entries on the pages, and "record my voice" opens it in code (no lesson)."""
from pathlib import Path

import pytest

from core import langintent, recorderintent
from tests.test_typed_code_routes import route  # noqa: F401 - the fixture

APP = Path(__file__).resolve().parent.parent / "app"
YES = ["record my voice", "I want to record my voice", "let me record my Creole", "open the voice recorder",
       "start the voice recording", "record my Creole voice", "Jarvis, can you record my Creole voice?",
       "Hey Jarvis, I want to record my Kreyol voice please"]
NO = ["record a voice memo", "record my screen", "I recorded my voice yesterday", "record this meeting",
      "can you record my workout", "record my voice memo and send it to Anna", "how do I record my voice on iPhone",
      "teach me Creole", "practice Creole for 10 minutes"]


def test_pages_carry_the_entries():
    lang = (APP / "languages.html").read_text(encoding="utf-8")
    top = lang.index("Record my Creole voice")
    assert top < lang.index('id="pick"') and lang.count("/voice-record.html") == 1
    assert "Start recording" in lang and "clips kept" in lang and "/api/voice-record" in lang
    assert "Record my voice" in (APP / "index.html").read_text(encoding="utf-8")
    assert "voice-record.html" in (APP / "command-center.js").read_text(encoding="utf-8")
    assert 'data-go="record"' in (APP / "command-center.html").read_text(encoding="utf-8")
    sw = (APP / "sw.js").read_text(encoding="utf-8")
    assert '"/voice-record.html"' in sw and '"/languages.html"' in sw and "jarvis-v37-voice-record" not in sw


@pytest.mark.parametrize("line", YES)
def test_wanted(line):
    assert recorderintent.wants(line)
    assert langintent.call_for(line, None) is None          # the language teacher never takes it


@pytest.mark.parametrize("line", NO)
def test_not_wanted(line):
    assert not recorderintent.wants(line)


@pytest.mark.parametrize("line", YES[:6])
def test_spoken_opens_the_recorder_with_no_model_and_no_lesson(route, monkeypatch, line):
    from tools import interface
    opened = []
    monkeypatch.setattr(interface, "OPEN_RECORDER", lambda: opened.append(1) or True)
    reply, ran, model = route.spoken(line)
    assert opened and not ran and not model and reply == recorderintent.LINE


@pytest.mark.parametrize("line", YES[:6])
def test_typed_from_the_phone_says_where_to_tap(route, monkeypatch, line):
    from tools import interface
    opened = []
    monkeypatch.setattr(interface, "OPEN_RECORDER", lambda: opened.append(1) or True)
    reply, ran, model = route.typed(line)
    assert not opened and not ran and not model
    assert "Tap the red button" in reply and "Languages, then Start recording" in reply


@pytest.mark.parametrize("line", ["record my screen", "I recorded my voice yesterday"])
def test_ordinary_lines_still_reach_the_model(route, monkeypatch, line):
    from tools import interface
    monkeypatch.setattr(interface, "OPEN_RECORDER", lambda: pytest.fail("opened"))
    _, ran, model = route.typed(line)
    assert "start_language_lesson" not in ran
