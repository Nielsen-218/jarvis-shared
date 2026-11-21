"""'Close PowerShell' closes the PowerShell windows he sees, never a hidden background one (2026-11-09: the hidden console
that ran Syncthing from a scheduled task was closed with them, and the phone's journal stopped reaching the PC)."""
from types import SimpleNamespace

import psutil
import pytest

from tools import apps


class _Proc:
    def __init__(self, pid, name, cmdline, readable=True):
        self.info = {"pid": pid, "name": name}
        self._cmd, self._readable = cmdline, readable

    def name(self):
        return self.info["name"]

    def cmdline(self):
        if not self._readable:
            raise psutil.AccessDenied(self.info["pid"])
        return self._cmd


@pytest.fixture
def desk(monkeypatch):
    from jarvis_pc.guardrails import stepfence
    procs, ran = [], []
    monkeypatch.setattr(apps.psutil, "process_iter", lambda attrs=None: list(procs))
    monkeypatch.setattr(apps, "_close_visible_windows", lambda app: 0)
    monkeypatch.setattr(apps, "_post_close", lambda app: None)
    monkeypatch.setattr(apps, "game_processes", lambda app: [])
    monkeypatch.setattr(stepfence, "require_foreground", lambda what: None)
    monkeypatch.setattr(apps.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or SimpleNamespace(returncode=0))
    return SimpleNamespace(procs=procs, ran=ran)


SYNC_HOST = ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", "& $e serve --no-browser"]


def test_close_powershell_leaves_the_hidden_one_that_runs_a_background_job(desk):
    desk.procs += [_Proc(10, "powershell.exe", SYNC_HOST), _Proc(11, "powershell.exe", ["powershell.exe"]),
                   _Proc(12, "powershell.exe", ["powershell.exe", "-NoExit"])]
    out = apps.close_app("powershell")
    assert desk.ran == [["taskkill", "/PID", "11"], ["taskkill", "/PID", "12"]]      # graceful, his windows only
    assert "powershell.exe" in out


def test_only_hidden_ones_running_means_nothing_of_his_to_close(desk):
    desk.procs += [_Proc(10, "powershell.exe", SYNC_HOST),
                   _Proc(13, "powershell.exe", ["powershell", "-w", "hidden", "-File", "backup.ps1"])]
    out = apps.close_app("powershell")
    # never "Nothing called powershell is running.": one is (he may see it in Task Manager); it was left alone on purpose
    assert out.startswith("Only a background powershell is running") and "left it alone" in out
    assert desk.ran == []


def test_nothing_running_at_all_is_still_said_so(desk):
    assert apps.close_app("powershell") == "Nothing called powershell is running."


def test_an_app_with_no_hidden_process_is_closed_by_name_as_before(desk):
    desk.procs += [_Proc(20, "notepad.exe", ["notepad.exe"]), _Proc(21, "notepad.exe", ["notepad.exe", "a.txt"])]
    out = apps.close_app("notepad")
    assert desk.ran == [["taskkill", "/IM", "notepad.exe"]] and out == "Asked notepad.exe to close."


def test_a_command_line_it_may_not_read_counts_as_his_window(desk):
    desk.procs += [_Proc(30, "powershell.exe", [], readable=False)]
    apps.close_app("powershell")
    assert desk.ran == [["taskkill", "/IM", "powershell.exe"]]


@pytest.mark.parametrize("cmd, hidden", [
    ("powershell -WindowStyle Hidden -Command x", True),
    ("powershell.exe -windowstyle hidden -file a.ps1", True),
    ("powershell -w hidden -c x", True),
    ("pwsh -Window Hidden -c x", True),
    ("powershell -WindowStyle 1 -c x", True),
    ("powershell -WindowStyle Minimized -c x", False),
    ("powershell -NoExit", False),
    ("powershell -Command Write-Host 'the window hidden behind'", False),
])
def test_what_counts_as_started_hidden(cmd, hidden):
    assert bool(apps._HIDDEN_START.search(cmd)) is hidden
