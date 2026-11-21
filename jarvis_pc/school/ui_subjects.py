"""School subjects for mouse, keyboard and window control (blueprint areas 1, 2 and 4), graded against a practice window's own log.

Each attempt launches a fresh native practice window (jarvis_pc/uiact/practice_app.py) with a shuffled layout and random
labels, so a skill that works must use control NAMES, never positions. The window appends every event it receives to a log; that
log, or the window's real rectangle, is the ground truth. The teacher writes the UI action language (jarvis_pc/uiact/lang.py),
which is parsed, gated and run by uiact.executor against that one window.

Only available on a Windows PC with pywinauto (the Facts.ui_available() check); elsewhere these subjects have no ground truth
and are skipped, never failed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import List, Tuple

from .curriculum import Context, Subject, Task, HIGH_SCHOOL

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def available() -> bool:
    if os.name != "nt":
        return False
    try:
        import pywinauto  # noqa: F401
        import win32gui  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _lines(log: str) -> List[str]:
    try:
        with open(log, encoding="utf-8") as f:
            return [ln.split(" ", 1)[1].strip() for ln in f if " " in ln]
    except OSError:
        return []


def _spawn(c: Context, **over) -> Tuple[str, str, dict]:
    """Start a practice window; returns (window title, log path, its config). Killed when the attempt ends."""
    title = "Practice " + c.word(6)
    slots = [10, 130, 250, 370]
    c.rng.shuffle(slots)
    cfg = {"title": title, "buttons": [c.word(5).title() for _ in range(3)], "bx": slots[:3], "field": c.word(5).title(),
           "items": [c.word(5) for _ in range(3)], "check": c.word(6).title() + " option", "status": "Status: " + c.word(6),
           "go_delay": 4.0, "x": 100 + c.rng.randint(0, 40), "y": 100 + c.rng.randint(0, 40), "w": 560, "h": 440}
    cfg.update(over)
    d = c.folder("ui")
    cfg_path, log = os.path.join(d, "cfg.json"), os.path.join(d, "events.log")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    proc = subprocess.Popen([sys.executable, "-m", "jarvis_pc.uiact.practice_app", "--config", cfg_path, "--log", log],
                            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c.on_cleanup(lambda: _stop(proc))
    for _ in range(50):
        if "ready" in _lines(log):
            break
        time.sleep(0.2)
    return title, log, cfg


def _stop(proc) -> None:
    proc.kill()
    try:
        proc.wait(timeout=5)       # the folder cannot be deleted while the process still holds it
    except Exception:  # noqa: BLE001
        pass


def _none(ask: str, params: dict) -> Task:
    return Task(ask, params, [], None, 1, kind="ui", ui_title="")


def _window_rect(title: str):
    import win32gui
    h = win32gui.FindWindow("JarvisPracticeWindow", title)
    return win32gui.GetWindowRect(h) if h else None


def _click_button(c: Context) -> Task:
    if not available():
        return _none("click the button called {label}", {"label": "x"})
    title, log, cfg = _spawn(c)
    label = cfg["buttons"][c.rng.randrange(3)]

    def check():
        clicks = [ln for ln in _lines(log) if ln.startswith("click:")]
        ok = clicks == ["click:" + label]
        return ok, "exactly the right button was pressed" if ok else f"expected only click:{label}, the window saw {clicks or 'no clicks'}"
    return Task("click the button called {label}", {"label": label}, [], check, 1, kind="ui", ui_title=title)


def _fill_form(c: Context) -> Task:
    if not available():
        return _none("type {text} into the {field} field and press Submit", {"field": "x", "text": "x"})
    title, log, cfg = _spawn(c)
    text = c.word(7)

    def check():
        want = f"submit:{cfg['field']}:{text}"
        ok = want in _lines(log)
        return ok, "the form was submitted with the right text" if ok else f"expected {want}, the window saw {_lines(log)[1:] or 'nothing'}"
    return Task("type {text} into the {field} field and press Submit", {"field": cfg["field"], "text": text}, [], check, 1, kind="ui", ui_title=title)


def _pick_item(c: Context) -> Task:
    if not available():
        return _none("choose {item} from the list", {"item": "x"})
    title, log, cfg = _spawn(c)
    item = cfg["items"][c.rng.randrange(3)]

    def check():
        sel = [ln for ln in _lines(log) if ln.startswith("select:")]
        ok = bool(sel) and sel[-1] == "select:" + item
        return ok, "the right list item is selected" if ok else f"expected select:{item}, the window saw {sel or 'no selection'}"
    return Task("choose {item} from the list", {"item": item}, [], check, 1, kind="ui", ui_title=title)


def _toggle_option(c: Context) -> Task:
    if not available():
        return _none("turn on the option called {label}", {"label": "x"})
    title, log, cfg = _spawn(c)

    def check():
        marks = [ln for ln in _lines(log) if ln.startswith("check:")]
        ok = bool(marks) and marks[-1] == f"check:{cfg['check']}:1"
        return ok, "the option is on" if ok else f"expected {cfg['check']} on, the window saw {marks or 'no change'}"
    return Task("turn on the option called {label}", {"label": cfg["check"]}, [], check, 1, kind="ui", ui_title=title)


def _wait_then_go(c: Context) -> Task:
    if not available():
        return _none("press the Go button as soon as it becomes available", {})
    title, log, _cfg = _spawn(c)

    def check():
        ok = "click:Go" in _lines(log)
        return ok, "Go was pressed once it was available" if ok else "Go was never pressed (it is greyed out for the first few seconds)"
    return Task("press the Go button as soon as it becomes available", {}, [], check, 1, kind="ui", ui_title=title)


def _hotkey_save(c: Context) -> Task:
    if not available():
        return _none("save the document with the keyboard shortcut Ctrl+S", {})
    title, log, _cfg = _spawn(c)

    def check():
        ok = "hotkey:ctrl+s" in _lines(log)
        return ok, "the window received Ctrl+S" if ok else "the window never received Ctrl+S"
    return Task("save the document with the keyboard shortcut Ctrl+S", {}, [], check, 1, kind="ui", ui_title=title)


def _read_status(c: Context) -> Task:
    if not available():
        return _none("what does the status line of the window say", {})
    word = c.word(8)
    title, _log, _cfg = _spawn(c, status="Status: " + word)
    return Task("what does the status line of the window say", {}, [word], None, 1, kind="ui", ui_title=title)


def _move_window(c: Context) -> Task:
    if not available():
        return _none("move the window so its top-left corner is at x {x} and y {y}", {"x": "0", "y": "0"})
    title, _log, _cfg = _spawn(c)
    x, y = c.rng.randint(60, 500), c.rng.randint(60, 300)

    def check():
        r = _window_rect(title)
        ok = bool(r) and abs(r[0] - x) <= 12 and abs(r[1] - y) <= 12
        return ok, "the window is where it was asked to be" if ok else f"expected top-left near ({x},{y}), the window is at {r[:2] if r else 'gone'}"
    return Task("move the window so its top-left corner is at x {x} and y {y}", {"x": str(x), "y": str(y)}, [], check, 1, kind="ui", ui_title=title)


def _resize_window(c: Context) -> Task:
    if not available():
        return _none("make the window {w} pixels wide and {h} pixels tall", {"w": "0", "h": "0"})
    title, _log, _cfg = _spawn(c)
    w, h = c.rng.randint(480, 760), c.rng.randint(380, 560)

    def check():
        r = _window_rect(title)
        ok = bool(r) and abs((r[2] - r[0]) - w) <= 16 and abs((r[3] - r[1]) - h) <= 16
        return ok, "the window has the requested size" if ok else f"expected {w}x{h}, the window is {(r[2] - r[0], r[3] - r[1]) if r else 'gone'}"
    return Task("make the window {w} pixels wide and {h} pixels tall", {"w": str(w), "h": str(h)}, [], check, 1, kind="ui", ui_title=title)


def _close_window(c: Context) -> Task:
    if not available():
        return _none("close the window", {})
    title, log, _cfg = _spawn(c)

    def check():
        ok = "closed" in _lines(log)
        return ok, "the window was closed" if ok else "the window is still open"
    return Task("close the window", {}, [], check, 1, kind="ui", ui_title=title)


def subjects() -> List[Subject]:
    return practice_subjects() + notepad_subjects()


def practice_subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    mk = lambda i, t, f, al, **k: Subject(i, t, hs, f, al, kind="ui", needs_windows=True, **k)  # noqa: E731
    return [
        mk("ui_click_button", "click a button by its name", _click_button, ("press button", "click")),
        mk("ui_fill_form", "type into a field and submit", _fill_form, ("fill form", "enter text")),
        mk("ui_pick_item", "choose an item from a list", _pick_item, ("select from list", "choose option"), needs=("ui_click_button",)),
        mk("ui_toggle_option", "turn a checkbox on", _toggle_option, ("tick checkbox", "enable option"), needs=("ui_click_button",)),
        mk("ui_wait_then_go", "wait for a control to become available", _wait_then_go, ("wait until enabled", "greyed out button"), needs=("ui_click_button",)),
        mk("ui_hotkey_save", "use a keyboard shortcut", _hotkey_save, ("keyboard shortcut", "ctrl s"), needs=("ui_click_button",)),
        mk("ui_read_status", "read text from a window", _read_status, ("read screen text", "status line")),
        mk("ui_move_window", "move a window", _move_window, ("reposition window", "drag window")),
        mk("ui_resize_window", "resize a window", _resize_window, ("window size", "make window bigger")),
        mk("ui_close_window", "close a window", _close_window, ("shut window", "exit program"), needs=("ui_move_window",)),
    ]


# ------------------------------------------------------------------------------------------------ a REAL application: Notepad
# The school launches Notepad itself on a temp file, so the window is the school's. Rules: never when any Notepad window is already
# open (Windows 11 Notepad is single-instance, so the new file would open inside the person's window); only that window is acted
# on; the file's contents on disk are the ground truth; only that window is closed afterwards.
from .curriculum import Num  # noqa: E402


def _notepad_windows() -> List[str]:
    from pywinauto import Desktop
    out = []
    for w in Desktop(backend="uia").windows():
        try:
            t = w.window_text() or ""
        except Exception:  # noqa: BLE001
            continue
        if t.endswith("Notepad") or " - Notepad" in t:
            out.append(t)
    return out


def _close_notepad(filename: str) -> bool:
    """Close the Notepad window showing the school's temp file. True when it is gone.

    Safety: Windows 11 Notepad restores earlier tabs (possibly the person's own) into a newly opened window. So this NEVER kills the
    process and NEVER answers "Don't save" to a prompt unless that prompt is about the school's own file; if the window cannot be
    closed politely it is left for the person, and the school will not use Notepad again until it is closed."""
    import re

    from pywinauto import Desktop
    pattern = r"^\*?" + re.escape(filename) + r" - Notepad$"

    def mine():
        return [w for w in Desktop(backend="uia").windows() if re.match(pattern, w.window_text() or "")]
    try:
        for _attempt in range(4):
            found = mine()
            if not found:
                return True
            for w in found:
                try:
                    w.close()
                    time.sleep(0.7)
                    # Windows 11 Notepad asks "Do you want to save changes to <file>?" INSIDE its own window (or as a dialog). Answer
                    # Don't save only when the prompt names the school's own file.
                    pid = w.process_id()
                    for top in [x for x in Desktop(backend="uia").windows() if x.process_id() == pid]:
                        texts = [(t.element_info.name or "") for t in top.descendants(control_type="Text")]
                        asks = [t for t in texts if re.search(r"save changes", t, re.I)]
                        if asks and all(filename in t for t in asks):
                            for btn in top.descendants(control_type="Button"):
                                if (btn.element_info.name or "").lower().startswith("don"):
                                    btn.invoke()
                                    break
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.8)
        return not mine()
    except Exception:  # noqa: BLE001
        return False


def _spawn_notepad(c: Context, initial: str = ""):
    """(title, file path) of a fresh Notepad window the school opened, or ("", path) when it must not run."""
    path = os.path.join(c.folder(c.word(6)), c.word(6) + ".txt")
    if os.environ.get("JARVIS_SCHOOL_REAL_APPS", "off").lower() != "on":
        # Windows 11 Notepad reopens every tab from its previous session, so each school run adds tabs to the PERSON's Notepad.
        # Real-application subjects are therefore opt-in (JARVIS_SCHOOL_REAL_APPS=on) until the owner decides.
        return "", path
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(initial)
    try:
        for _ in range(12):                      # a window the school just closed can take a moment to disappear
            if not _notepad_windows():
                break
            time.sleep(0.5)
        else:
            return "", path                      # somebody's Notepad is already open: not our window to use
    except Exception:  # noqa: BLE001
        return "", path
    subprocess.Popen(["notepad.exe", path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    name = os.path.basename(path)
    c.on_cleanup(lambda: _close_notepad(name))        # registered at launch: a window that opens late must still be closed
    for _ in range(60):
        time.sleep(0.25)
        try:
            hit = [t for t in _notepad_windows() if t.startswith(name)]
        except Exception:  # noqa: BLE001
            hit = []
        if hit:
            title = hit[0]
            time.sleep(0.8)
            return title, path
    return "", path


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _notepad_type_save(c: Context) -> Task:
    ask = "type {text} into Notepad and save the file"
    if not available():
        return _none(ask, {"text": "x"})
    title, path = _spawn_notepad(c)
    text = c.word(6) + " " + c.word(6)
    if not title:
        return Task(ask, {"text": text}, [], None, 1, kind="ui")

    def check():
        got = _read_text(path).strip()
        return got == text, "the saved file has exactly the typed text" if got == text else f"expected {text!r} in the file, found {got!r}"
    return Task(ask, {"text": text}, [], check, 1, kind="ui", ui_title=title, ui_owned=True)


def _notepad_replace_save(c: Context) -> Task:
    ask = "replace everything in Notepad with {text} and save"
    if not available():
        return _none(ask, {"text": "x"})
    title, path = _spawn_notepad(c, initial="old " + c.word(10) + " content")
    text = c.word(6) + " " + c.word(6)
    if not title:
        return Task(ask, {"text": text}, [], None, 1, kind="ui")

    def check():
        got = _read_text(path).strip()
        return got == text, "the old text is gone and the new text is saved" if got == text else f"expected {text!r}, found {got!r}"
    return Task(ask, {"text": text}, [], check, 1, kind="ui", ui_title=title, ui_owned=True)


def _notepad_count_chars(c: Context) -> Task:
    ask = "how many characters does Notepad say the document has"
    if not available():
        return _none(ask, {})
    body = c.word(c.rng.randint(12, 40))
    title, _path = _spawn_notepad(c, initial=body)
    if not title:
        return Task(ask, {}, [], None, 1, kind="ui")
    return Task(ask, {}, [Num(len(body), 0.0)], None, 1, kind="ui", ui_title=title, ui_owned=True)


def notepad_subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    mk = lambda i, t, f, al, **k: Subject(i, t, hs, f, al, kind="ui", needs_windows=True, real_app=True, **k)  # noqa: E731
    return [
        mk("app_notepad_type_save", "type into Notepad and save", _notepad_type_save, ("notepad write file", "edit text file"), needs=("ui_hotkey_save", "ui_fill_form")),
        mk("app_notepad_replace_save", "replace Notepad's text and save", _notepad_replace_save, ("overwrite text", "select all and type"), needs=("app_notepad_type_save",)),
        mk("app_notepad_read_count", "read Notepad's character count", _notepad_count_chars, ("status bar", "document length"), needs=("ui_read_status",)),
    ]
