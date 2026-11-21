"""A game that ignores the polite close is ended (owner 2026-11-07). The 12 s turn budget for a plain one-app verb must not
cut that short: the close itself waits GAME_QUIT_S for the game, then ends it (up to 8 s more). Fakes only."""
from core import pcactions, pcagent, pcmethods
from tools import apps


def step(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


def fake_games(monkeypatch, running=("crimson desert",)):
    monkeypatch.setattr(apps, "resolve_app", lambda name: name.casefold())
    monkeypatch.setattr(apps, "game_processes", lambda name: [object()] if name in running else [])
    monkeypatch.setattr(apps, "_installed", lambda: {})
    monkeypatch.setattr(apps, "game_of_window", lambda hwnd: "crimson desert")


def test_closing_a_game_has_no_twelve_second_budget(monkeypatch):
    fake_games(monkeypatch)
    assert pcagent.turn_budget({"steps": [step("close_app", app="crimson desert")]}) is None


def test_closing_the_game_in_front_has_no_budget_either(monkeypatch):
    fake_games(monkeypatch)
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda *a, **k: [{"hwnd": 5, "title": "Crimson Desert", "process": "x.exe", "foreground": True}])
    assert pcagent.turn_budget({"steps": [step("close_foreground")]}) is None


def test_an_ordinary_app_keeps_its_budget(monkeypatch):
    fake_games(monkeypatch)
    from tools import pcops
    monkeypatch.setattr(apps, "game_of_window", lambda hwnd: "")
    monkeypatch.setattr(pcops, "windows", lambda *a, **k: [{"hwnd": 5, "title": "Untitled - Notepad", "process": "notepad.exe", "foreground": True}])
    assert pcagent.turn_budget({"steps": [step("close_app", app="notepad")]}) == pcagent.SIMPLE_TURN_S
    assert pcagent.turn_budget({"steps": [step("close_foreground")]}) == pcagent.SIMPLE_TURN_S


def test_with_no_budget_the_close_step_keeps_room_for_the_forced_end():
    with pcmethods.turn_deadline(None):
        assert pcmethods._step_limit("close_app") >= pcactions.GAME_QUIT_S + 8
