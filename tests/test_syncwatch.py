"""Syncthing carries data/ between the PC and the laptop; on 2026-11-09 its console on the PC was closed and nothing
started it again, so for two days nothing the phone said reached the PC (core/syncwatch). Faked processes and schtasks:
nothing here looks at or starts anything real."""
import subprocess
import sys

import pytest

from core import pcbridge, syncwatch


@pytest.fixture
def pc(monkeypatch):
    """The PC with Syncthing gone, its task registered, no game: every schtasks call recorded, none run."""
    calls = []
    state = {"running": False, "task": True, "gaming": False}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(syncwatch, "running", lambda: state["running"])
    monkeypatch.setattr(syncwatch, "_gaming", lambda: state["gaming"])
    monkeypatch.setattr(syncwatch, "_said", set())

    def schtasks(*args):
        calls.append(args)
        ok = args[0] == "/Run" or state["task"]
        return subprocess.CompletedProcess(["schtasks", *args], 0 if ok else 1, "", "" if ok else "ERROR: not found")
    monkeypatch.setattr(syncwatch, "_schtasks", schtasks)
    return state, calls


def test_syncthing_gone_is_started_again_from_its_own_task(pc):
    state, calls = pc
    assert syncwatch.check() == "started"
    assert calls == [("/Query", "/TN", "Syncthing"), ("/Run", "/TN", "Syncthing")]


def test_running_syncthing_is_left_alone(pc):
    state, calls = pc
    state["running"] = True
    assert syncwatch.check() == "running" and calls == []


def test_never_while_a_game_runs(pc):
    state, calls = pc
    state["gaming"] = True
    assert syncwatch.check() == "gaming" and calls == []


def test_no_task_is_said_once_and_nothing_is_started(pc, capsys):
    state, calls = pc
    state["task"] = False
    assert syncwatch.check() == "no-task" and syncwatch.check() == "no-task"
    assert ("/Run", "/TN", "Syncthing") not in calls
    assert capsys.readouterr().out.count("no scheduled task") == 1


def test_off_by_switch_and_never_on_the_laptop(pc, monkeypatch):
    state, calls = pc
    monkeypatch.setenv("JARVIS_SYNC_WATCH", "off")
    assert syncwatch.check() == "off"
    monkeypatch.delenv("JARVIS_SYNC_WATCH")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    assert syncwatch.check() == "off" and calls == []


def test_the_pc_process_starts_the_watch():
    import ast
    from pathlib import Path

    import main
    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "start"
               and getattr(n.func.value, "id", "") == "syncwatch" for n in ast.walk(fn))
