"""Round-4 findings on the narrowed pc-live-defects branch. Fakes only: no window, process, catalogue or model is touched.

1. The fact lane never says "closed" because a window list had nothing: the app matcher is blind to apps whose window text does not
   hold their name (File Explorer is "Downloads" in explorer.exe), so a miss says what was seen, not what is so.
2. The fact lane never says "open" / "in front" for a window that merely mentions the app (a Chrome tab about Notepad).
3. The turn's clock never cuts a later original close short of its own wait, and a close a re-plan re-issues is a re-plan step.
4. A launch that was not verified is not "Opened X." because some other window's title contains the app's name.
"""
import pytest

from core import pcactions, pcagent, pcfacts, pchybrid, pcmethods
from tools import apps, pcops

CATALOGUE = set(apps.BUILTINS) | {"steam", "google chrome", "microsoft edge", "discord", "visual studio code", "word"}


@pytest.fixture
def pc(monkeypatch):
    box = type("Box", (), {"windows": [], "processes": set()})()
    monkeypatch.setattr(pcfacts, "_catalogue", lambda: set(CATALOGUE))
    monkeypatch.setattr(pcops, "windows", lambda visible_only=True: box.windows)
    monkeypatch.setattr(apps, "game_processes", lambda app: [])
    monkeypatch.setattr(apps, "_process_stems", lambda: set(box.processes))
    monkeypatch.setattr(apps, "resolve_app", lambda name: name.casefold().strip() if name.casefold().strip() in CATALOGUE else None)
    return box


def win(process, title, foreground=False, **more):
    return {"hwnd": 1, "process": process, "title": title, "pid": 7, "foreground": foreground, "minimized": False, **more}


def ask(app, want="open", did=""):
    return pcfacts.answer({"app": app, "want": want, "did": did})


# ---------------------------------------------------------------------------------- 1. a miss is not "closed"
def test_file_explorer_on_a_folder_is_open(pc):
    pc.windows = [win("explorer.exe", "Downloads", **{"class": "CabinetWClass"})]
    assert ask("file explorer") == "Yes, sir, File Explorer is open."


def test_edge_with_an_invisible_character_in_its_title_is_open(pc):
    pc.windows = [win("msedge.exe", "Inbox - Personal - Microsoft​ Edge")]
    assert ask("microsoft edge") == "Yes, sir, Microsoft Edge is open."


@pytest.mark.parametrize("app", ["notepad", "file explorer", "microsoft edge", "steam"])
def test_no_window_is_not_a_claim_that_it_is_closed(pc, app):
    reply = ask(app)
    assert "closed" not in reply and reply.startswith("No, sir, I don't see a "), reply
    assert "closed" not in ask(app, "closed") and "closed" not in ask(app, "closed", "did") and "closed" not in ask(app, "open", "did")


def test_a_tray_only_app_is_said_to_be_running(pc):
    pc.processes = {"steam"}
    assert ask("steam") == "Steam is running, sir, but I don't see a window."
    assert ask("steam", "closed") == "No, sir, Steam is still running, though I don't see a window."


def test_the_always_running_shell_is_no_evidence(pc):
    pc.processes = {"explorer"}
    assert ask("file explorer").startswith("No, sir, I don't see a File Explorer window")


def test_the_desktop_is_not_an_open_app(pc):
    pc.windows = [win("explorer.exe", "Program Manager")]
    assert ask("file explorer").startswith("No, sir, I don't see")


# --------------------------------------------------------------------------- 2. a window that mentions it is not the app
def test_a_tab_about_notepad_is_not_notepad_and_not_in_front(pc):
    pc.windows = [win("chrome.exe", "notepad tutorial - Google Search - Google Chrome", foreground=True)]
    reply = ask("notepad")
    assert "is open" not in reply and "in front" not in reply, reply


def test_a_file_named_notepad_in_an_editor_is_not_notepad(pc):
    pc.windows = [win("Code.exe", "notepad.py - Visual Studio Code", foreground=True)]
    reply = ask("notepad")
    assert "is open" not in reply and "in front" not in reply, reply


def test_an_alias_is_not_an_app_and_a_profiles_page_is_not_files(pc):
    assert pcfacts.classify("is files open")[0] == "other"
    pc.windows = [win("chrome.exe", "Profiles - Settings - Google Chrome", foreground=True)]
    reply = ask("files")
    assert "is open" not in reply and "in front" not in reply, reply


def test_the_apps_own_window_is_open_and_in_front(pc):
    pc.windows = [win("chrome.exe", "notepad tutorial - Google Chrome"), win("Notepad.exe", "Untitled - Notepad", foreground=True)]
    assert ask("notepad") == "Yes, sir, Notepad is open. It's in front."
    pc.windows = [win("chrome.exe", "notepad tutorial - Google Chrome", foreground=True), win("Notepad.exe", "Untitled - Notepad")]
    assert ask("notepad") == "Yes, sir, Notepad is open."


@pytest.mark.parametrize("app, process, title", [
    ("notepad", "Notepad.exe", "Untitled - Notepad"), ("calculator", "CalculatorApp.exe", "Calculator"),
    ("paint", "mspaint.exe", "Untitled - Paint"), ("settings", "SystemSettings.exe", "Settings"), ("word", "WINWORD.EXE", "Letter - Word"),
    ("google chrome", "chrome.exe", "Inbox - Google Chrome"), ("discord", "Discord.exe", "#general - Discord"),
    ("visual studio code", "Code.exe", "main.py - ECC - Visual Studio Code"), ("steam", "steam.exe", "Steam"),
    ("task manager", "Taskmgr.exe", "Task Manager"), ("command prompt", "cmd.exe", "Command Prompt"),
])
def test_an_apps_own_window_is_recognised(pc, app, process, title):
    pc.windows = [win(process, title)]
    assert ask(app).startswith("Yes, sir, ") and "is open" in ask(app)


@pytest.mark.parametrize("app, process, title", [
    ("word", "chrome.exe", "Crossword - Google Chrome"), ("word", "WORDPAD.EXE", "Document - WordPad"),
    ("paint", "firefox.exe", "Paint tutorial - Mozilla Firefox"), ("notepad", "Notepad++.exe", "new 1 - Notepad++"),
    ("steam", "chrome.exe", "Steam sale - Google Chrome"), ("calculator", "python.exe", "calculator helper"),
])
def test_a_window_that_merely_holds_the_name_is_not_the_app(pc, app, process, title):
    pc.windows = [win(process, title, foreground=True)]
    assert "is open" not in ask(app) and "in front" not in ask(app)


def test_the_strict_rule_never_counts_the_desktop_shell_and_keeps_its_own_rows():
    rows = [win("chrome.exe", "Crossword - Google Chrome"), win("WINWORD.EXE", "Letter - Word"), win("Notepad.exe", "a - Notepad"),
            win("explorer.exe", "Program Manager"), win("explorer.exe", "", **{"class": "Shell_TrayWnd"})]
    assert [w["process"] for w in apps.own_windows("word", rows)] == ["WINWORD.EXE"]
    assert [w["process"] for w in apps.own_windows("notepad", rows)] == ["Notepad.exe"]
    assert apps.own_windows("file explorer", rows) == []


# ------------------------------------------------------------------------------------------- 3. the turn clock
def step(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


def test_a_later_original_close_is_not_cut_below_its_own_wait(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])
    seen = {}

    def execute(s, **kwargs):
        seen[s["tool"]] = pcmethods._step_limit(s["tool"])
        now[0] += 9.0 if s["tool"] == "open_app" else 0.5
        return {"verified": True, "detail": "ok"}
    with pcmethods.turn_deadline(112.0):
        out = pcagent.pursue("open blender and close notepad", {"steps": [step("open_app", app="blender"), step("close_app", app="notepad")]},
                             execute=execute, replan=lambda *a: None, deadline=112.0)
    assert out["state"] == "completed"
    assert seen["close_app"] >= pcactions.CLOSE_WAIT_S + 1.0, seen


def test_a_close_the_replan_reissues_is_a_replan_step_with_its_own_ceiling(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pcagent.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(pcactions, "validate", lambda plan: None)
    calls = []

    def execute(s, **kwargs):
        calls.append((s["tool"], pcagent.replanned_step(), pcmethods._step_limit(s["tool"])))
        now[0] += 1.0
        return {"verified": s["tool"] != "close_app" or len([c for c in calls if c[0] == "close_app"]) > 1, "detail": "I couldn't close Notepad."}
    with pcmethods.turn_deadline(112.0):
        out = pcagent.pursue("close notepad", {"steps": [step("close_app", app="notepad")]}, execute=execute, deadline=112.0,
                             replan=lambda *a: {"steps": [step("focus_app", app="notepad"), step("close_app", app="notepad")]},
                             allowed_fn=lambda *a: True, dialog_open=lambda s: False)
    assert out["state"] == "completed", out
    closes = [c for c in calls if c[0] == "close_app"]
    assert closes[0][1] is False and closes[1][1] is True, calls
    assert closes[1][2] == pcmethods.step_timeout("close_app"), "the re-issued close keeps its own ceiling"


# ------------------------------------------------------------------------------------ 4. an unverified launch stays unverified
LAUNCH = "Launch was requested for word, but no visible application window was verified yet."


def test_a_window_that_only_mentions_the_app_does_not_verify_its_launch(pc, monkeypatch):
    monkeypatch.setattr(pchybrid.time, "sleep", lambda s: None)
    pc.windows = [win("chrome.exe", "Crossword - Google Chrome")]
    assert pchybrid._settled(step("open_app", app="word")) is None
    out = pcagent.pursue("open word", {"steps": [step("open_app", app="word")]},
                         execute=lambda s, **k: {"verified": False, "detail": LAUNCH},
                         replan=lambda *a: {"steps": [step("open_app", app="word")]}, settle=pchybrid._settled, approved=True)
    assert out["state"] == "stopped"
    assert pchybrid.compose_detail(out["evidence"], out["state"]) == LAUNCH


def test_the_apps_own_late_window_does_verify_its_launch(pc, monkeypatch):
    monkeypatch.setattr(pchybrid.time, "sleep", lambda s: None)
    pc.windows = [win("chrome.exe", "Crossword - Google Chrome"), win("WINWORD.EXE", "Document1 - Word")]
    settled = pchybrid._settled(step("open_app", app="word"))
    assert settled and settled["verified"] and settled["detail"] == "Opened word."


def test_a_close_is_still_settled_by_mains_matcher(pc, monkeypatch):
    monkeypatch.setattr(pchybrid.time, "sleep", lambda s: None)
    pc.windows = [win("chrome.exe", "Crossword - Google Chrome")]
    assert pchybrid._settled(step("close_app", app="word")) is None             # a window main's matcher counts is still there
    pc.windows = []
    assert pchybrid._settled(step("close_app", app="word"))["verified"] is True
