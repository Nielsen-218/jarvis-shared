"""open_app on an app that already owns a visible window brings it to the front instead of launching a second copy
(audit-pc-control defect 3: "open Discord" with Discord open spawned another instance and claimed it had opened it)."""
import pytest

from core import pcactions
from tools import apps, pcops

_REAL_MATCH = apps._matching_process_pids      # the fixture stubs it; the loose-match tests need the real one


@pytest.fixture
def world(monkeypatch):
    w = {"launched": [], "focused": [], "windows": [], "focus_fails": False}
    monkeypatch.setattr(apps, "_find", lambda q: ("discord", "discord.exe"))
    monkeypatch.setattr(apps.subprocess, "Popen", lambda *a, **k: w["launched"].append(a))
    monkeypatch.setattr(apps.os, "startfile", lambda *a, **k: w["launched"].append(a), raising=False)
    monkeypatch.setattr(apps, "_matching_process_pids", lambda name: {10, 11})
    monkeypatch.setattr(apps, "_visible_window_pids", lambda: {10} if w["windows"] else set())
    monkeypatch.setattr(apps, "_ready", lambda name, before: bool(w["launched"]))
    monkeypatch.setattr(pcops, "windows", lambda visible_only=True: w["windows"])
    monkeypatch.setattr("tools.software.is_admin", lambda: False)

    def perform(name, args, approved=False, cancelled=lambda: False):
        w["focused"].append((name, args))
        if w["focus_fails"]:
            raise RuntimeError("Windows refused to focus the requested window.")
        return {"verified": True, "detail": "Focused a visible " + args["process"] + " window."}

    monkeypatch.setattr(pcactions, "perform", perform)
    return w


def test_an_app_that_is_already_open_is_brought_forward_not_launched_again(world):
    world["windows"] = [{"hwnd": 5, "title": "Friends - Discord", "process": "Discord.exe", "pid": 10, "minimized": False}]
    detail, ready = apps.launch_and_wait("discord", timeout=0.5)
    assert ready is True and world["launched"] == []
    assert world["focused"] == [("focus_app", {"process": "Discord.exe"})]
    assert "already open" in detail and not detail.startswith("Opened")      # it does not claim a launch it did not cause


def test_an_app_that_is_not_open_is_still_launched(world):
    detail, ready = apps.launch_and_wait("discord", timeout=0.5)
    assert ready is True and world["launched"] and world["focused"] == [] and detail.startswith("Opened")


def test_when_focusing_is_refused_it_launches_as_before(world):
    world["windows"] = [{"hwnd": 5, "title": "Discord", "process": "Discord.exe", "pid": 10, "minimized": False}]
    world["focus_fails"] = True
    detail, ready = apps.launch_and_wait("discord", timeout=0.5)
    assert world["launched"] and ready is True


def test_a_window_that_only_mentions_the_name_does_not_stop_a_launch(world):
    """A browser tab titled 'discord tips' is not Discord: only a window owned by the app's own process counts."""
    world["windows"] = [{"hwnd": 6, "title": "discord tips - Chrome", "process": "chrome.exe", "pid": 99, "minimized": False}]
    detail, ready = apps.launch_and_wait("discord", timeout=0.5)
    assert world["launched"] and world["focused"] == []


def test_a_packaged_apps_frame_window_is_raised_by_its_handle(world, monkeypatch):
    """Calculator's window belongs to ApplicationFrameHost.exe: 'open calculator' with it minimized must raise THAT window
    (owner 2026-11-15: 'he has it open but he needs to make sure they come in front')."""
    raised = []
    monkeypatch.setattr(apps, "_find", lambda q: ("calculator", "calculator:"))
    monkeypatch.setattr(pcactions, "raise_window", lambda h: raised.append(h) or True)
    world["windows"] = [{"hwnd": 41, "title": "Settings", "process": "ApplicationFrameHost.exe", "pid": 3, "minimized": False},
                        {"hwnd": 42, "title": "Calculator", "process": "ApplicationFrameHost.exe", "pid": 3, "minimized": True}]
    detail, ready = apps.launch_and_wait("calculator", timeout=0.5)
    assert ready and raised == [42] and world["launched"] == [] and "already open" in detail


def test_a_freshly_launched_window_is_brought_to_the_front(world, monkeypatch):
    """Started from the elevated background process, the new window could open behind his app."""
    def launch(*a, **k):
        world["launched"].append(a)
        world["windows"] = [{"hwnd": 5, "title": "Discord", "process": "Discord.exe", "pid": 10, "minimized": False}]
    monkeypatch.setattr(apps.subprocess, "Popen", launch)
    detail, ready = apps.launch_and_wait("discord", timeout=0.5)
    assert detail.startswith("Opened") and world["focused"] == [("focus_app", {"process": "Discord.exe"})]


def _launch_as(world, monkeypatch, name, target, windows):
    monkeypatch.setattr(apps, "_find", lambda q: (name, target))
    world["windows"] = windows
    return apps.launch_and_wait(name, timeout=0.5)


def _win(process, pid, title="x"):
    return {"hwnd": pid, "title": title, "process": process, "pid": pid, "minimized": False}


@pytest.mark.parametrize("name,others", [
    ("microsoft word", ["msedge.exe", "TextInputHost.exe", "WindowsTerminal.exe"]),
    ("windows terminal", ["explorer.exe", "ChatGPT.exe", "SystemSettings.exe"]),
    ("visual studio code", ["ChatGPT.exe", "devenv.exe"]),
    ("steam", ["hades.exe"]),
    ("google chrome", ["googledrivefs.exe"]),
])
def test_a_different_apps_window_whose_path_shares_a_name_word_does_not_count_as_open(world, monkeypatch, name, others):
    """The real token match (name or exe path contains 'microsoft', 'code', ...) says Edge is Word. It must still launch."""
    class Proc:
        def __init__(self, pid, exe):
            self.pid, self.info = pid, {"name": exe, "exe": r"C:\Program Files\Microsoft\Windows\Studio\Google\Steam\Code\\" + exe}

    procs = [Proc(100 + i, exe) for i, exe in enumerate(others)]
    monkeypatch.setattr(apps.psutil, "process_iter", lambda attrs=None: procs)
    monkeypatch.setattr(apps, "_matching_process_pids", _REAL_MATCH)
    windows = [_win(exe, 100 + i) for i, exe in enumerate(others)]
    assert apps._matching_process_pids(name)                      # the loose match does hit them: that is the trap
    detail, ready = _launch_as(world, monkeypatch, name, name.replace(" ", "") + ".exe", windows)
    assert world["launched"] and world["focused"] == []
    assert "already open" not in detail


def test_the_apps_own_process_window_counts_even_when_the_catalogue_name_has_more_words(world, monkeypatch):
    detail, ready = _launch_as(world, monkeypatch, "google chrome", r"C:\Start\Google Chrome.lnk", [_win("chrome.exe", 7)])
    assert world["launched"] == [] and world["focused"] == [("focus_app", {"process": "chrome.exe"})]
    assert "already open" in detail
    world["focused"].clear()
    detail, ready = _launch_as(world, monkeypatch, "word", r"C:\Program Files\Office\WINWORD.EXE", [_win("WINWORD.EXE", 8)])
    assert world["launched"] == [] and world["focused"] == [("focus_app", {"process": "WINWORD.EXE"})]
