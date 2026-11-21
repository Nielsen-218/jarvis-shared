"""No PC step may hold a turn forever (2026-11-06: a 5 h acceptance hang in a UI Automation call).

* every step has a hard ceiling; a step that passes it fails in words and the worker is told to stop;
* the step runs with the caller's acting user and approval context;
* a just-launched app's window is waited for (bounded), and with several windows the one in front is used;
* pywinauto's UI Automation object is created in the MTA, so a worker's UI call never waits on a blocked STA thread.
"""
import os
import threading
import time

import pytest

from core import pcactions, pcmethods, pcverbs, users


def test_a_step_that_never_returns_fails_in_words_and_is_told_to_stop(monkeypatch):
    seen = {}

    def stuck(name, args, approved=False, cancelled=lambda: False):
        while not cancelled():
            time.sleep(0.02)
        seen["stopped"] = True
        raise InterruptedError("stopped")

    monkeypatch.setattr(pcactions, "perform", stuck)
    monkeypatch.setitem(pcmethods.STEP_TIMEOUTS, "focus_app", 0.3)
    t = time.monotonic()
    with pytest.raises(RuntimeError, match="did not finish within"):
        pcmethods.invoke("native", {"tool": "focus_app", "arguments": {"app": "notepad"}}, False, lambda: False)
    assert time.monotonic() - t < 2
    time.sleep(0.2)
    assert seen.get("stopped"), "the abandoned worker sees cancelled() turn true"


def test_execute_reports_a_timed_out_step_as_unverified_with_the_reason(monkeypatch):
    monkeypatch.setattr(pcactions, "perform", lambda *a, **k: time.sleep(5))
    monkeypatch.setitem(pcmethods.STEP_TIMEOUTS, "focus_app", 0.2)
    monkeypatch.setattr(pcmethods.pcledger, "method_order", lambda step, c: c)
    out = pcmethods.execute({"tool": "focus_app", "arguments": {"process": "notepad.exe"}})
    assert out["verified"] is False and "did not finish within" in out["detail"]


def _recording_keyboard(monkeypatch):
    import pyautogui
    import pyperclip
    pressed = []
    monkeypatch.setattr(pyautogui, "hotkey", lambda *k: pressed.append(k))
    monkeypatch.setattr(pyperclip, "paste", lambda: "")
    monkeypatch.setattr(pyperclip, "copy", lambda t: None)
    return pressed


def test_stopping_the_wait_is_not_enough_a_woken_up_step_must_not_type(monkeypatch):
    """The difference the review asked to see. A step hangs past its limit, the caller stops WAITING; then the hang ends
    and the step goes on to paste text. Without the fence it would type into whatever is in front; with it, nothing."""
    from tools import computer
    pressed = _recording_keyboard(monkeypatch)
    release, finished = threading.Event(), {}

    def hanging_then_types(name, args, approved=False, cancelled=lambda: False):
        release.wait(5)                          # stuck in an OS call that ignores cancellation
        try:
            computer._type_text("hello")         # ...then wakes up and carries on
            finished["typed"] = True
        except InterruptedError as e:
            finished["refused"] = str(e)

    monkeypatch.setattr(pcactions, "perform", hanging_then_types)
    monkeypatch.setitem(pcmethods.STEP_TIMEOUTS, "focus_app", 0.3)
    with pytest.raises(RuntimeError, match="stopped it: nothing more will be typed"):
        pcmethods.invoke("native", {"tool": "focus_app", "arguments": {"process": "notepad.exe"}}, False, lambda: False)
    release.set()
    time.sleep(0.3)
    assert pressed == [] and "typed" not in finished and "passed its time limit" in finished["refused"]
    computer._type_text("hello")                 # the same call outside an abandoned step works as before
    assert pressed == [("ctrl", "v")]


def test_a_terminal_step_past_its_limit_has_its_process_tree_killed(monkeypatch):
    """A real child process: the step's limit trips the gate's kill switch and the runner kills the command's tree."""
    import psutil
    from jarvis_pc.guardrails.tiers import PathAllowlist, classify
    cmd = "ping -n 30 127.0.0.3"
    assert int(classify(cmd, cwd=os.path.expanduser("~"), allowlist=PathAllowlist(pcverbs.user_roots())).tier) == 0
    monkeypatch.setitem(pcmethods.STEP_TIMEOUTS, "run_terminal", 2.0)
    t = time.monotonic()
    with pytest.raises(RuntimeError, match="stopped it"):
        pcmethods.invoke("native", {"tool": "run_terminal", "arguments": {"command": cmd}}, True, lambda: False)   # authorized plan
    deadline = time.monotonic() + 5
    alive = True
    while time.monotonic() < deadline and alive:
        alive = any("127.0.0.3" in " ".join(p.info["cmdline"] or []) for p in psutil.process_iter(["cmdline"]))
        time.sleep(0.2)
    assert not alive, "the ping kept running after its step was stopped"
    assert time.monotonic() - t < 9


@pytest.mark.parametrize("call", ["launch", "volume", "media_key", "keys"])
def test_every_fenced_primitive_works_outside_a_step_and_refuses_in_an_expired_one(monkeypatch, call):
    """Each primitive is exercised for real (with the OS call faked): the fence must not break normal use (a missing import
    in launch_as_user once did) and must stop an abandoned step."""
    import subprocess as sp
    from jarvis_pc.guardrails import stepfence
    from tools import media, software
    import pyautogui
    done = []
    monkeypatch.setattr(software.subprocess, "Popen", lambda *a, **k: done.append("launch"))
    monkeypatch.setattr(pyautogui, "hotkey", lambda *k: done.append("keys"))
    monkeypatch.setattr(pyautogui, "press", lambda *k: done.append("keys"))
    monkeypatch.setattr(media, "_endpoint", lambda: type("E", (), {"SetMasterVolumeLevelScalar": lambda s, v, n: done.append("volume"),
                                                                     "GetMute": lambda s: 0})())
    import ctypes
    monkeypatch.setattr(ctypes.windll.user32, "keybd_event", lambda *a: done.append("media_key"))
    act = {"launch": lambda: software.launch_as_user(r"C:\x\notes.txt"), "volume": lambda: media.set_volume(10),
           "media_key": lambda: media._press(0xAD), "keys": lambda: pcverbs._keys("ctrl", "s")}[call]
    act()
    assert done, "outside a step the primitive acts"
    done.clear()
    token = stepfence.StepToken("x")
    token.expire()
    stepfence.bind(token)
    try:
        with pytest.raises(stepfence.StepAbandoned):
            act()
    finally:
        stepfence.bind(None)
    assert done == [] and token.blocked, "inside an expired step it does nothing"


def test_too_many_stuck_steps_stop_new_ones_from_starting(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(pcactions, "perform", lambda *a, **k: gate.wait(10))
    monkeypatch.setitem(pcmethods.STEP_TIMEOUTS, "focus_app", 0.2)
    monkeypatch.setattr(pcmethods, "_ABANDONED", [])
    step = {"tool": "focus_app", "arguments": {"process": "notepad.exe"}}
    for _ in range(pcmethods.MAX_ABANDONED):
        with pytest.raises(RuntimeError, match="stopped it"):
            pcmethods.invoke("native", step, False, lambda: False)
    with pytest.raises(RuntimeError, match="still stuck"):
        pcmethods.invoke("native", step, False, lambda: False)
    gate.set()
    time.sleep(0.3)
    assert pcmethods.abandoned() == [], "once they end, steps start again"


def test_the_step_worker_acts_as_the_same_user_with_the_same_owner_message(monkeypatch):
    from core import pcbridge
    got = {}

    def look(name, args, approved=False, cancelled=lambda: False):
        got["uid"], got["msg"], got["thread"] = users.current(), pcbridge.original_message(), threading.current_thread().name
        return {"verified": True, "detail": "ok"}

    monkeypatch.setattr(pcactions, "perform", look)
    uid = next(iter(users.all_users()))
    with users.acting_as(uid), pcbridge.user_message(uid, "open notepad"):
        pcmethods.invoke("native", {"tool": "focus_app", "arguments": {"app": "notepad"}}, False, lambda: False)
    assert got == {"uid": uid, "msg": "open notepad", "thread": "pcstep-focus_app"}


def test_step_ceilings_are_bounded_and_ui_steps_are_twenty_seconds():
    assert pcmethods.step_timeout("type_text") == 20 and pcmethods.step_timeout("open_app") == 20
    assert pcmethods.step_timeout("run_terminal") > pcverbs.TERMINAL_TIMEOUT_S == 30
    assert all(0 < v <= 600 for v in pcmethods.STEP_TIMEOUTS.values())


def test_one_window_waits_for_a_window_that_appears_late(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda a: "notepad")
    shown = iter([[], [], [{"hwnd": 7, "title": "Untitled - Notepad", "minimized": False}]])
    last = [None]

    def windows(app):
        try:
            last[0] = next(shown)
        except StopIteration:
            pass
        return last[0]

    monkeypatch.setattr(apps, "visible_windows_for_app", windows)
    monkeypatch.setattr(pcverbs.time, "sleep", lambda s: None)
    assert pcverbs._one_window("notepad")[1]["hwnd"] == 7


def test_one_window_gives_up_in_words_and_honours_cancel(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda a: "notepad")
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda app: [])
    with pytest.raises(RuntimeError, match="appeared within"):
        pcverbs._one_window("notepad", wait=0.3)
    with pytest.raises(InterruptedError):
        pcverbs._one_window("notepad", cancelled=lambda: True, wait=5)


def test_with_several_windows_the_one_in_front_is_used_otherwise_ask(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda a: "notepad")
    two = [{"hwnd": 1, "title": "a - Notepad", "minimized": False}, {"hwnd": 2, "title": "b - Notepad", "minimized": False}]
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda app: two)
    monkeypatch.setattr(pcverbs, "_foreground_hwnd", lambda: 2)
    assert pcverbs._one_window("notepad")[1]["hwnd"] == 2
    monkeypatch.setattr(pcverbs, "_foreground_hwnd", lambda: 99)
    with pytest.raises(RuntimeError, match="none is in front"):
        pcverbs._one_window("notepad")


def test_uia_object_lives_in_the_mta_so_a_worker_call_does_not_wait_on_a_blocked_thread():
    """The real repro, in a child process (COM apartments are per process): STA main thread, then a worker's UI call while
    the main thread blocks without pumping messages. Bounded at 20 s."""
    import subprocess
    import sys
    from pathlib import Path
    code = (
        "import sys, threading, time; sys.path.insert(0, %r)\n"
        "import comtypes; comtypes.CoInitialize()\n"                       # the main thread becomes STA, as tools.media does
        "from jarvis_pc.uiact import uia; assert uia.ensure_mta()\n"
        "uia._desktop().windows()\n"                                        # main-thread UI use still works
        "done = {}\n"
        "def w():\n"
        "    import comtypes; comtypes.CoInitializeEx(0); done['n'] = len(uia._desktop().windows())\n"
        "threading.Thread(target=w, daemon=True).start()\n"
        "lock = threading.Lock(); lock.acquire(); lock.acquire(timeout=15)\n"  # blocked, not pumping
        "print('OK' if 'n' in done else 'HUNG', flush=True)\n"
        "import os; os._exit(0)\n"
    ) % str(Path(__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=40)
    assert "OK" in out.stdout, out.stdout + out.stderr


def test_a_worker_stuck_for_good_stops_blocking_new_steps_after_ten_minutes(monkeypatch):
    """Audit-pc-control defect 11: two truly stuck workers blocked every PC step until Jarvis was restarted."""
    gate = threading.Event()
    clock = [100.0]
    monkeypatch.setattr(pcmethods, "_now", lambda: clock[0])
    monkeypatch.setattr(pcactions, "perform", lambda *a, **k: gate.wait(10))
    monkeypatch.setitem(pcmethods.STEP_TIMEOUTS, "focus_app", 0.2)
    monkeypatch.setattr(pcmethods, "_ABANDONED", [])
    step = {"tool": "focus_app", "arguments": {"process": "notepad.exe"}}
    for _ in range(pcmethods.MAX_ABANDONED):
        with pytest.raises(RuntimeError, match="stopped it"):
            pcmethods.invoke("native", step, False, lambda: False)
    with pytest.raises(RuntimeError, match="still stuck"):
        pcmethods.invoke("native", step, False, lambda: False)
    clock[0] += pcmethods.ABANDONED_FORGOTTEN_S + 1            # they are still blocked (gate not set), but long fenced
    assert pcmethods.abandoned() == []
    with pytest.raises(RuntimeError, match="stopped it"):      # a new step starts again
        pcmethods.invoke("native", step, False, lambda: False)
    gate.set()
    time.sleep(0.3)
