"""'Close Crimson Desert' (live 2026-11-07): named games run without a review question, get time to quit, and a game that
ignores the polite close is offered a force close by voice, run on a plain yes, never re-planned behind the owner's back."""
from types import SimpleNamespace

from core import authz, pcactions, pcagent
from tools import apps, pcskills


def test_an_edition_word_is_not_needed_to_name_the_game():
    step = {"tool": "close_app", "arguments": {"app": "crimson desert enhanced"}}
    assert authz.in_scope(step, "Closed Crimson Desert.") and authz.in_scope(step, "close crimson desert")
    assert not authz.in_scope(step, "close it") and not authz.in_scope(step, "close the desert")


def _game(monkeypatch, alive):
    proc = SimpleNamespace(pid=4242, name=lambda: "CrimsonDesert.exe")
    monkeypatch.setattr(pcactions, "GAME_QUIT_S", 0.2)
    monkeypatch.setattr(apps, "close_app", lambda name: "Asked CrimsonDesert.exe to close.")
    monkeypatch.setattr(apps, "game_processes", lambda name: [proc] if alive() else [])
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda name: [{"title": "Crimson Desert", "pid": 4242}] if alive() else [])
    monkeypatch.setattr(apps, "_installed", lambda: {"crimson desert enhanced": "steam://rungameid/1"})


def test_a_game_that_quits_is_closed(monkeypatch):
    calls = iter([True, True, False])
    _game(monkeypatch, lambda: next(calls, False))
    out = pcactions._close_catalog_app("crimson desert enhanced")
    assert out["verified"] and out["detail"] == "Closed crimson desert enhanced."


def test_a_game_that_ignores_the_close_is_ended_without_asking(monkeypatch):
    alive = {"on": True}
    _game(monkeypatch, lambda: alive["on"])
    killed = []

    def taskkill(cmd, **kw):
        killed.append(cmd)
        alive["on"] = False
        return SimpleNamespace(returncode=0, stdout="SUCCESS", stderr="")
    monkeypatch.setattr(apps.subprocess, "run", taskkill)
    out = pcactions._close_catalog_app("crimson desert enhanced")
    assert out["verified"] and out["forced"] and out["detail"] == "Crimson Desert Enhanced didn't quit when asked, so I ended it."
    assert killed == [["taskkill", "/F", "/PID", "4242"]]                                         # only the game's own pid


def test_an_ordinary_app_left_open_by_its_save_prompt_is_not_forced(monkeypatch):
    monkeypatch.setattr(apps, "close_app", lambda name: "Asked notepad to close.")
    monkeypatch.setattr(apps, "game_processes", lambda name: [])
    monkeypatch.setattr(apps, "_installed", lambda: {})
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda name: [{"title": "*notes - Notepad"}])
    monkeypatch.setattr(apps, "force_close_game", lambda name: (_ for _ in ()).throw(AssertionError("never forced")))
    out = pcactions._close_catalog_app("notepad")
    assert not out["verified"] and out["error_kind"] == "needs_owner" and "asking you to save" in out["detail"]


def test_close_this_names_the_app_in_front(monkeypatch):
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda: [{"hwnd": 9, "foreground": True, "title": "Crimson Desert", "process": "CrimsonDesert.exe"}])
    monkeypatch.setattr(apps, "resolve_app", lambda name: "crimson desert enhanced")
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda name: [{"hwnd": 9}])
    step = {"tool": "close_app", "arguments": {"app": "crimson desert enhanced"}}
    assert authz.in_scope(step, "Hey Jarvis, close this") and authz.in_scope(step, "close it")
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda name: [{"hwnd": 5}])          # not the one in front
    assert not authz.in_scope(step, "close this")


def test_a_game_running_as_administrator_is_explained(monkeypatch):
    _game(monkeypatch, lambda: True)
    monkeypatch.setattr(apps.subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="ERROR: Access is denied."))
    monkeypatch.setattr(apps, "time", SimpleNamespace(monotonic=iter(range(0, 100, 5)).__next__, sleep=lambda s: None), raising=False)
    out = apps.force_close_game("crimson desert enhanced")
    assert "running as administrator" in out


def test_a_question_for_the_owner_is_not_replanned(monkeypatch):
    replans = []
    out = pcagent.pursue("close crimson desert", {"steps": [{"tool": "close_app", "arguments": {"app": "crimson desert enhanced"}}]},
                         execute=lambda step, **kw: {"verified": False, "error_kind": "needs_owner", "detail": "Say yes"},
                         replan=lambda *a: replans.append(a) or {"steps": []})
    assert replans == [] and out["state"] == "stopped"
