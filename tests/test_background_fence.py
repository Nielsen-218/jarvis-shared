"""Mark VI part 3 (2026-11-10): a mission NEVER takes the foreground.

Inside stepfence.background_only() every primitive that would focus a window, move the real mouse or send real keys refuses
with BackgroundUnavailable before it touches anything; the spies (window focus, SetForegroundWindow, pyautogui, the pywinauto
mouse, the clipboard) record no call. Outside background mode the same primitives behave as before. All fakes: no app opens.
"""
import threading
from types import SimpleNamespace

import pytest

from jarvis_pc.guardrails import stepfence
from jarvis_pc.guardrails.stepfence import BackgroundUnavailable, background_only


class _Win:
    """A window that records every attempt to bring it forward or type into it."""

    def __init__(self, calls):
        self.calls = calls
        self.handle = 4242

    def wrapper_object(self):
        return self

    def set_focus(self):
        self.calls.append("set_focus")

    def type_keys(self, *a, **k):
        self.calls.append("type_keys")

    def descendants(self, **k):
        return []

    def window_text(self):
        return "README.TXT - Notepad"


@pytest.fixture
def spies(monkeypatch):
    """Every way the PC could take the foreground or inject input, recorded."""
    calls = []
    import pyautogui
    import pyperclip
    import win32gui
    # pywinauto.mouse is NOT imported here: importing pywinauto on the main thread fixes its COM apartment for the whole run and
    # breaks later tests that need STA (test_antistuck's volume case). uia.click_at refuses before it imports the mouse.
    for name in ("press", "hotkey", "click", "moveTo", "typewrite", "write", "keyDown"):
        monkeypatch.setattr(pyautogui, name, lambda *a, _n=name, **k: calls.append("pyautogui." + _n))
    monkeypatch.setattr(win32gui, "SetForegroundWindow", lambda *a: calls.append("SetForegroundWindow"))
    monkeypatch.setattr(pyperclip, "copy", lambda *a: calls.append("clipboard.copy"))
    monkeypatch.setattr(pyperclip, "paste", lambda *a: calls.append("clipboard.paste") or "")
    from jarvis_pc.uiact import uia
    real_focus = uia.focus

    def focus_spy(win):
        calls.append("uia.focus")
        return real_focus(win)
    monkeypatch.setattr(uia, "_in_front", lambda hwnd: False)
    monkeypatch.setattr(uia, "rect", lambda win: (100, 50, 700, 450))
    return SimpleNamespace(calls=calls, win=_Win(calls), focus_spy=focus_spy, real_focus=real_focus)


def test_the_flag_is_per_thread_nests_and_require_foreground_is_fence_outside_it():
    assert not stepfence.is_background()
    stepfence.require_foreground("anything")                 # no token, no background: nothing happens
    seen = {}
    with background_only():
        assert stepfence.is_background()
        with background_only():
            assert stepfence.is_background()
        assert stepfence.is_background()                     # the inner block restores, not clears
        t = threading.Thread(target=lambda: seen.setdefault("other", stepfence.is_background()))
        t.start()
        t.join()
        with pytest.raises(BackgroundUnavailable) as caught:
            stepfence.require_foreground("press ctrl+s")
    assert not stepfence.is_background()
    assert seen["other"] is False, "another thread (the owner's voice turn) is never background"
    e = caught.value
    assert e.kind == "background_unavailable" and e.what == "press ctrl+s" and "needs you at the PC" in str(e)
    assert isinstance(e, RuntimeError)
    token = stepfence.StepToken("x")                         # an expired step still raises StepAbandoned first
    stepfence.bind(token)
    try:
        token.expire()
        with background_only(), pytest.raises(stepfence.StepAbandoned):
            stepfence.require_foreground("press")
    finally:
        stepfence.bind(None)


def test_uia_focus_keys_and_click_refuse_in_background(spies):
    # (uia.screen_labels reads a background capture instead of refusing since C6: tests/test_background_capture.py)
    from jarvis_pc.uiact import uia
    with background_only():
        for call in (lambda: uia.focus(spies.win), lambda: uia.send_keys(spies.win, "^s"),
                     lambda: uia.click_at(spies.win, 200, 200)):
            with pytest.raises(BackgroundUnavailable):
                call()
    assert spies.calls == []


def test_agent_observe_of_a_suspended_app_refuses_instead_of_focusing(spies, monkeypatch):
    from jarvis_pc.uiact import agent, uia
    monkeypatch.setattr(uia, "window", lambda title: spies.win)
    monkeypatch.setattr(uia, "focus", spies.focus_spy)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    with background_only(), pytest.raises(BackgroundUnavailable, match="suspended"):
        agent.observe("Settings")
    assert spies.calls == []


def test_screenjudge_grab_window_never_focuses_in_background(spies, monkeypatch):
    from core import pcvision, screenjudge
    from jarvis_pc.uiact import uia
    monkeypatch.setattr(uia, "window", lambda title: spies.win)
    monkeypatch.setattr(uia, "focus", spies.focus_spy)

    def occluded(hwnd):
        raise BackgroundUnavailable("background_occluded")
    monkeypatch.setattr(pcvision, "grab_window_bg", occluded)       # (C6: the capture; here it cannot see the window)
    with background_only():
        assert screenjudge.grab_window("Untitled - Notepad") is None   # not judged, and nothing was focused
    assert spies.calls == []


def test_pcverbs_keys_typing_and_saving_refuse_in_background(spies, monkeypatch, tmp_path):
    from core import pcverbs
    from jarvis_pc.uiact import uia
    monkeypatch.setattr(pcverbs, "_one_window", lambda app, cancelled=None: ("notepad", {"title": "README.TXT - Notepad", "hwnd": 1}))
    monkeypatch.setattr(uia, "window", lambda title: spies.win)
    monkeypatch.setattr(pcverbs, "_in_user_space", lambda p: True)
    monkeypatch.setattr(pcverbs.time, "sleep", lambda s: None)
    with background_only():
        with pytest.raises(BackgroundUnavailable):
            pcverbs._keys("ctrl", "n")
        with pytest.raises(BackgroundUnavailable):
            pcverbs.type_text({"app": "notepad", "text": "hello"})
        with pytest.raises(BackgroundUnavailable):
            pcverbs.save_document({"app": "notepad", "path": str(tmp_path / "x" / "hello.txt")})
    assert spies.calls == []


def test_computer_paste_and_the_pyautogui_loop_refuse_in_background(spies):
    from tools import computer
    with background_only():
        with pytest.raises(BackgroundUnavailable):
            computer._type_text("hello")
        with pytest.raises(BackgroundUnavailable):
            computer.Screen()
    assert spies.calls == [], "not even the clipboard was touched"


def test_pcactions_focus_keys_tabs_and_minimize_refuse_in_background(spies, monkeypatch):
    from core import pcactions
    sites = [lambda: pcactions._send_browser_search("cats"), lambda: pcactions._scroll_foreground("down"),
             lambda: pcactions._close_foreground_browser_tab(), lambda: pcactions._close_named_site_tab("youtube"),
             lambda: pcactions._uia_focus(4242), lambda: pcactions._minimize("notepad"), lambda: pcactions._minimize(""),
             lambda: pcactions.perform("focus_app", {"process": "notepad.exe"}, approved=True)]
    with background_only():
        for site in sites:
            with pytest.raises(BackgroundUnavailable):
                site()
    assert spies.calls == []


def test_the_save_prompt_never_falls_back_to_the_real_mouse_in_background(spies, monkeypatch):
    from core import saveprompt

    class Btn:
        def invoke(self):
            raise RuntimeError("no Invoke pattern")

        def click_input(self):
            spies.calls.append("click_input")
    monkeypatch.setattr(saveprompt, "buttons", lambda app, hwnd=0: {"save": Btn(), "dont": Btn()})
    with background_only(), pytest.raises(BackgroundUnavailable):
        saveprompt.answer("notepad", True, wait_s=0)
    assert spies.calls == []
    assert saveprompt.answer("", True, wait_s=0) == "Saved and closed it."        # outside background: as before
    assert spies.calls == ["click_input"]


def test_pcmethods_execute_maps_the_refusal_without_retrying_and_carries_the_flag_to_its_worker(spies, monkeypatch):
    from core import pcactions, pcmethods
    seen = []
    real = pcactions.perform

    def perform(name, args, **k):
        seen.append(stepfence.is_background())
        return real(name, args, **k)
    monkeypatch.setattr(pcactions, "perform", perform)
    monkeypatch.setattr(pcmethods, "candidates", lambda step: ["native", "native"])
    for fn in ("method_outcome", "usage", "latency"):
        monkeypatch.setattr(pcmethods.pcledger, fn, lambda *a, **k: None)
    monkeypatch.setattr(pcmethods.pcledger, "method_order", lambda step, c: c)
    with background_only():
        out = pcmethods.execute({"tool": "focus_app", "arguments": {"process": "notepad.exe"}})
    assert out["verified"] is False and out["error_kind"] == "background_unavailable"
    assert out["detail"].startswith("needs you at the PC: focus notepad.exe")
    assert seen == [True], "ran on the step worker thread, in background, exactly once (no second method)"
    assert spies.calls == []


def test_outside_background_the_primitives_still_act(spies, monkeypatch):
    """Unchanged behaviour: the same calls reach the window / keyboard when no mission is running."""
    from core import pcverbs
    from jarvis_pc.uiact import uia
    monkeypatch.setattr(uia.time, "sleep", lambda s: None)
    pcverbs._keys("ctrl", "s")
    assert spies.calls == ["pyautogui.hotkey"]
    with pytest.raises(Exception):
        uia.focus(spies.win)                              # _in_front is faked False: it tries, then says it could not
    assert spies.calls.count("set_focus") == 4
    spies.calls.clear()
    monkeypatch.setattr(uia, "_in_front", lambda hwnd: True)
    uia.send_keys(spies.win, "abc")
    assert spies.calls == ["type_keys"]
