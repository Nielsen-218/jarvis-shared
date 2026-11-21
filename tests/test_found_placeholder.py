"""The plan marker "<found>" (a path not known yet) was spoken aloud on 2026-11-15: "Should I open Blender, create a new project in Blender,
open <found>, then open Blender?"."""
import pytest

from brain import llm


@pytest.mark.parametrize("said, heard", [
    ("Should I open Blender, create a new project in Blender, open <found>, then open Blender?",
     "Should I open Blender, create a new project in Blender, open the file it finds, then open Blender?"),
    ("blender 5.2 is showing '(Unsaved)', not a new document for <found>, so I did not press Save.",
     "blender 5.2 is showing '(Unsaved)', not a new document for the file it finds, so I did not press Save."),
    ("Nothing to change here.", "Nothing to change here."),
    ("Use a < b and c > d.", "Use a < b and c > d."),
])
def test_the_marker_is_said_as_words(said, heard):
    assert llm.plain_markers(said) == heard


def test_non_text_is_left_alone():
    assert llm.plain_markers(None) is None


def test_a_quick_reply_with_the_marker_is_said_without_it(monkeypatch):
    from core import pcfast, quick
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: "Should I open <found>?")
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    said = []
    b.ask("Open the file.", said.append)
    assert "<found>" not in " ".join(said) and "the file it finds" in " ".join(said)
    assert "<found>" not in str(b.messages)
