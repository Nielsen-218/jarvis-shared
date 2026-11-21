"""Which windows belong to an app is main's rule, unchanged: the app's name (spaces dropped) is in the title or the process name,
or the window is one of the app's game processes. The ONLY differences are the desktop shell (never an app's window) and a row
that vanished while it was being listed (skipped). The table runs the branch function against a frozen copy of main's rule."""
import itertools
import sys
import types

import pytest

from tools import apps


def main_rule(app, windows, pids=()):
    """tools.apps.visible_windows_for_app as it is on main, with pcops.windows() replaced by `windows`."""
    needle = app.casefold().replace(" ", "")
    return [window for window in windows
            if needle in (window["title"] + window["process"]).casefold().replace(" ", "") or window.get("pid") in pids]


def win(pid, process, title, **more):
    return {"hwnd": pid * 10, "pid": pid, "process": process, "title": title, "foreground": False, "minimized": False, **more}


ROWS = [
    win(1, "notepad.exe", "Untitled - Notepad"), win(2, "chrome.exe", "Notepad - Google Search - Google Chrome"),
    win(3, "Code.exe", "notepad_test.py - Visual Studio Code"), win(4, "ApplicationFrameHost.exe", "Calculator"),
    win(5, "msedge.exe", "Document - Microsoft Edge"), win(6, "Notepad++.exe", "new 1 - Notepad++"),
    win(7, "chrome.exe", "Inbox - Google Chrome"), win(8, "Spotify.exe", "Spotify Premium"),
    win(9, "mspaint.exe", "Untitled - Paint"), win(10, "steam.exe", "Steam"), win(11, "WindowsTerminal.exe", "Windows PowerShell"),
    win(12, "explorer.exe", "Downloads"), win(13, "explorer.exe", "File Explorer"), win(14, "Teams.exe", "Chat | Microsoft Teams"),
    win(15, "AcroRd32.exe", "report.pdf - Adobe Acrobat Reader"), win(16, "obs64.exe", "OBS 30.0"), win(17, "WINWORD.EXE", "Letter - Word"),
    win(18, "Discord.exe", "#general - Discord"), win(19, "python.exe", "notepad helper"), win(20, "CalculatorApp.exe", "Calculator"),
    win(21, "msedge.exe", "Notepad online - Microsoft Edge"), win(22, "cmd.exe", "Command Prompt"),
    win(23, "UnrealEditor.exe", "Shadow World - Unreal Editor"), win(24, "firefox.exe", "Paint tutorial - Mozilla Firefox"),
]
SHELL = [
    win(101, "explorer.exe", "Program Manager", **{"class": "Progman"}), win(102, "explorer.exe", "Program Manager"),
    win(103, "explorer.exe", "", **{"class": "Shell_TrayWnd"}), win(104, "Explorer.EXE", "  program manager "),
    win(105, "explorer.exe", "Task Switching", **{"class": "WorkerW"}),
]
APPS = ["notepad", "calculator", "paint", "microsoft edge", "google chrome", "chrome", "spotify", "steam", "file explorer", "explorer",
        "files", "terminal", "command prompt", "microsoft teams", "teams", "obs studio", "word", "discord", "unreal", "program manager",
        "windows", "unreal editor", "visual studio code", "edge", "powershell", "acrobat", "manager", "fire", "x"]


def test_the_table_has_at_least_eighty_rows():
    assert len(list(itertools.product(APPS, [ROWS]))) * len(ROWS) >= 80 and len(ROWS) + len(SHELL) >= 29


@pytest.mark.parametrize("app", APPS)
def test_every_app_gets_exactly_what_main_gave_except_the_desktop_shell(app):
    rows = ROWS + SHELL
    expected = [w for w in main_rule(app, rows) if not (w["process"].casefold() == "explorer.exe" and w["title"].strip().casefold() in ("", "program manager"))
                and w.get("class") not in ("Shell_TrayWnd", "WorkerW", "Progman")]
    assert [w["pid"] for w in apps.windows_of(app, rows)] == [w["pid"] for w in expected]
    assert apps.windows_of(app, ROWS) == main_rule(app, ROWS), "with no shell row the answer is main's, row for row"


def test_game_process_ids_still_claim_a_window_whatever_its_title():
    rows = [win(30, "WeirdGame-Win64-Shipping.exe", "Hollow"), win(31, "other.exe", "Other")]
    assert apps.windows_of("zzz", rows, {30}) == main_rule("zzz", rows, {30}) == [rows[0]]


@pytest.mark.parametrize("row", SHELL)
def test_the_desktop_shell_is_never_an_app_window(row):
    for app in ("explorer", "program manager", "manager", "windows"):
        assert row not in apps.windows_of(app, [row])
    assert apps.is_shell_window(row["title"], row["process"], row.get("class", ""))


def test_main_would_have_matched_the_shell_so_the_exclusion_is_the_whole_difference():
    assert main_rule("explorer", SHELL) and apps.windows_of("explorer", SHELL) == []


def test_a_real_folder_window_of_explorer_is_still_an_explorer_window():
    folder = win(12, "explorer.exe", "Downloads")
    assert apps.windows_of("explorer", [folder]) == [folder] == main_rule("explorer", [folder])


def test_a_row_that_vanished_while_listing_is_skipped_not_fatal():
    rows = [{"hwnd": 1, "pid": 1}, None, win(1, "notepad.exe", "Untitled - Notepad")]
    assert apps.windows_of("notepad", rows) == [rows[2]]


def test_the_uia_close_never_asks_the_desktop_to_close(monkeypatch):
    closed = []

    class Window:
        def __init__(self, title, name):
            self.title, self.name = title, name

        def window_text(self):
            return self.title

        def process_id(self):
            return 1

        def close(self):
            closed.append(self.name)

    class Desktop:
        def __init__(self, backend=None):
            pass

        def windows(self):
            return [Window("Program Manager", "desktop"), Window("a - Program Manager Notes", "notes")]
    monkeypatch.setitem(sys.modules, "pywinauto", types.SimpleNamespace(Desktop=Desktop))
    monkeypatch.setattr(apps, "_uia_process", lambda window: "explorer.exe" if window.name == "desktop" else "notepad.exe")
    assert apps._close_visible_windows("program manager") == 1 and closed == ["notes"]
