"""Closing something that asks to save (owner 2026-11-07): ask "Save it before I close it?" and act on yes / no, or follow
"save it and close it" / "don't save it, just close it" said up front. Only the app's own Save / Don't save are pressed."""
import pytest

from core import pcactions, saveprompt
from tools import apps


@pytest.mark.parametrize("said, expected", [
    ("save it and close it", (True, "")), ("close it and save it", (True, "")), ("save it, then close it", (True, "")),
    ("don't save it, just close it", (False, "")), ("close it without saving", (False, "")),
    ("Hey Jarvis close paint and save it", (True, "paint")), ("close notepad without saving", (False, "notepad")),
    ("close it", None), ("save the file", None), ("close this window", None), ("open paint", None),
])
def test_close_and_save_commands(said, expected):
    assert saveprompt.command(said) == expected


class Button:
    def __init__(self, name, log):
        self.name, self.log = name, log

    def invoke(self):
        self.log.append(self.name)


def _app_with_prompt(monkeypatch, log, closes=True):
    state = {"open": True}
    monkeypatch.setattr(saveprompt, "buttons", lambda app="", hwnd=0: {"save": Button("Save", log), "dont": Button("Don't save", log)} if state["open"] else {})
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda app: [{"title": "Untitled - Paint"}] if state["open"] else [])
    orig = Button.invoke

    def press(self):
        orig(self)
        state["open"] = not closes
    monkeypatch.setattr(Button, "invoke", press)
    return state


def test_a_close_without_a_choice_asks_and_the_owner_answers_no(monkeypatch):
    log = []
    _app_with_prompt(monkeypatch, log)
    out = pcactions._save_prompt("paint")
    assert out["detail"] == "Paint has unsaved changes. Save it before I close it?" and out["error_kind"] == "needs_owner" and log == []
    from core import users
    uid = users.current()
    assert saveprompt.reply(uid, "what time is it") is None                 # not an answer: the question stays
    assert saveprompt.reply(uid, "No") == "Closed Paint without saving." and log == ["Don't save"]
    assert saveprompt.pending(uid) is None


def test_yes_saves_then_closes(monkeypatch):
    log = []
    _app_with_prompt(monkeypatch, log)
    from core import users
    pcactions._save_prompt("paint")
    assert saveprompt.reply(users.current(), "yes, save it") == "Saved and closed Paint." and log == ["Save"]


def test_a_choice_said_with_the_close_is_followed_without_asking(monkeypatch):
    log = []
    _app_with_prompt(monkeypatch, log)
    out = pcactions._save_prompt("paint", save=False)
    assert out["verified"] and out["detail"] == "Closed Paint without saving." and log == ["Don't save"]


def test_an_untitled_save_leaves_save_as_for_the_owner(monkeypatch):
    log = []
    _app_with_prompt(monkeypatch, log, closes=False)
    monkeypatch.setattr(saveprompt, "_save_as_open", lambda app: True)
    assert saveprompt.answer("paint", True) == "Paint has no file name yet, so I left the Save as window open for you to name it."


def test_no_prompt_means_nothing_is_pressed(monkeypatch):
    monkeypatch.setattr(saveprompt, "buttons", lambda app="", hwnd=0: {})
    assert pcactions._save_prompt("paint") is None
