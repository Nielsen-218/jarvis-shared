"""LIVE proof (Mark VI C8, 2026-11-10): background hands type into Notepad WITHOUT taking the foreground.

OPT-IN ONLY. It opens real windows (a scratch Notepad and a cmd window), so it is skipped unless JARVIS_LIVE=1. Run it by
hand, on the PC, with the owner present and NOT touching the keyboard or mouse while it runs (about 20 s):

    set JARVIS_LIVE=1
    .venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider tests/live/test_background_hands_notepad.py

Criteria (OpenClaw's proof runner, docs/plans/mark6-build-plan.md "D. Proof test"):
  (a) the result is verified, OR a structured BackgroundUnavailable; either way 0 calls to uia.focus / SetForegroundWindow;
  (b) the front window is unchanged;
  (c) the cursor did not move;
  (d) GetLastInputInfo is unchanged (no SendInput was used);
  (e) when verified: an independent UIA re-read of the Notepad text area contains the sentinel;
  (f) when verified: grab_window_bg after != before (md5 of the pixels).
Evidence (before.png, after.png, result.json) is kept in %TEMP%\\bg_proof_<id>\\ and the path is printed.
If Windows 11 Notepad refuses (a structured refusal with 0 focus calls), the test still passes; record it in KNOWN-ISSUES.

Owner safety: it refuses to start while ANY Notepad window is open (Windows 11 Notepad opens files as tabs in an existing
window, and the owner's tabs must never be typed into), it types only into its own scratch file in %TEMP%, and it closes
only the windows it opened.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

pytestmark = [
    pytest.mark.skipif(os.environ.get("JARVIS_LIVE") != "1", reason="live proof: opens Notepad and cmd; set JARVIS_LIVE=1 to run by hand"),
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
]


def _windows_of(process: str):
    from tools import pcops
    return [w for w in pcops.windows() if (w.get("process") or "").casefold() == process.casefold()]


def _wait_for(fn, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = fn()
        if found:
            return found
        time.sleep(0.2)
    return None


def _close_fixture_notepad(hwnd: int) -> None:
    """WM_CLOSE on OUR Notepad window, answer its save prompt with Don't save, and end its process if it still lingers."""
    import psutil
    import win32con
    import win32gui
    import win32process
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:  # noqa: BLE001
        pid = 0
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(1.0)
    try:
        from core import saveprompt
        if saveprompt.buttons("notepad", hwnd):
            saveprompt.answer("notepad", False, hwnd)
    except Exception:  # noqa: BLE001
        pass
    if pid and win32gui.IsWindow(hwnd):
        try:
            psutil.Process(pid).terminate()             # only the Notepad this test started (no other was open)
        except Exception:  # noqa: BLE001
            pass


def test_typing_into_notepad_behind_another_window_never_takes_the_foreground(monkeypatch):
    import win32api
    import win32gui
    from core import pcbridge, pcvision, pcverbs
    from jarvis_pc.guardrails.stepfence import BackgroundUnavailable, background_only
    from jarvis_pc.uiact import executor, uia

    if _windows_of("notepad.exe"):
        pytest.skip("a Notepad window is open: close it first (its tabs must never be typed into)")
    run_id = uuid.uuid4().hex[:10]
    out_dir = os.path.join(tempfile.gettempdir(), f"bg_proof_{run_id}")
    os.makedirs(out_dir, exist_ok=True)
    scratch = os.path.join(tempfile.gettempdir(), f"bg_proof_{run_id}.txt")
    open(scratch, "w", encoding="utf-8").close()
    sentinel = f"W3-WIN CONFIRMED {run_id}"
    result = {"id": run_id, "sentinel": sentinel}
    front_proc = notepad_hwnd = None
    try:
        # 1. the fixture: Notepad on a new scratch file
        subprocess.Popen(["notepad.exe", scratch])
        stem = os.path.basename(scratch)
        found = _wait_for(lambda: [w for w in _windows_of("notepad.exe") if stem.casefold() in w["title"].casefold()])
        assert found, "the scratch Notepad window did not appear"
        notepad = found[0]
        notepad_hwnd = int(notepad["hwnd"])
        # 2. a second window on top (allowed in setup)
        front_proc = subprocess.Popen(["cmd.exe", "/k", "title BGPROOF_FRONT"], creationflags=subprocess.CREATE_NEW_CONSOLE)
        front = _wait_for(lambda: [w for w in _windows_of("cmd.exe") + _windows_of("WindowsTerminal.exe")
                                   if "BGPROOF_FRONT" in w["title"]])
        assert front, "the front cmd window did not appear"
        front_hwnd = int(front[0]["hwnd"])
        try:
            win32gui.SetForegroundWindow(front_hwnd)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.8)
        # 3. the start state
        fg0 = int(win32gui.GetForegroundWindow())
        if fg0 == notepad_hwnd:
            pytest.skip("could not put another window in front of Notepad, so the proof would mean nothing")
        cur0 = win32api.GetCursorPos()
        tick0 = executor.last_input_tick()
        before = pcvision.grab_window_bg(notepad_hwnd)
        before.save(os.path.join(out_dir, "before.png"))

        # spies: any attempt to take the foreground is recorded (and still refused by the fence)
        focus_calls = []
        real_focus, real_sfw = uia.focus, win32gui.SetForegroundWindow
        monkeypatch.setattr(uia, "focus", lambda w: (focus_calls.append("uia.focus"), real_focus(w))[1])
        monkeypatch.setattr(win32gui, "SetForegroundWindow", lambda h: (focus_calls.append("SetForegroundWindow"), real_sfw(h))[1])
        # only OUR window is ever chosen (never another Notepad), the rest of type_text runs for real
        monkeypatch.setattr(pcverbs, "_one_window", lambda app, cancelled=None, wait=0: ("notepad", {"title": notepad["title"], "hwnd": notepad_hwnd}))

        # 4. act
        outcome, refusal = None, None
        with pcbridge.user_message("owner", f"type into {stem} in notepad"), background_only():
            try:
                outcome = pcverbs.type_text({"app": "notepad", "text": sentinel})
            except BackgroundUnavailable as e:
                refusal = {"what": e.what, "reason": e.reason, "kind": e.kind}
        time.sleep(0.5)
        result.update(outcome=outcome, refusal=refusal, focus_calls=list(focus_calls))

        # 5. the checks
        result["fg_same"] = int(win32gui.GetForegroundWindow()) == fg0
        result["cursor_same"] = win32api.GetCursorPos() == cur0
        result["input_tick_same"] = executor.last_input_tick() == tick0
        assert (outcome and outcome.get("verified")) or refusal, f"neither verified nor a structured refusal: {outcome}"
        assert focus_calls == [], f"something tried to take the foreground: {focus_calls}"
        assert result["fg_same"], "the front window changed"
        assert result["cursor_same"], "the cursor moved"
        assert result["input_tick_same"], "GetLastInputInfo changed: real input was injected (or someone touched the PC)"
        if outcome:
            back = pcverbs._text_of(pcverbs._editable(uia.window(notepad["title"])))
            result["read_back"] = back[:200]
            assert sentinel in back, "an independent UIA re-read does not show the sentinel"
            after = pcvision.grab_window_bg(notepad_hwnd)
            after.save(os.path.join(out_dir, "after.png"))
            result["md5_before"] = hashlib.md5(before.tobytes()).hexdigest()
            result["md5_after"] = hashlib.md5(after.tobytes()).hexdigest()
            assert result["md5_before"] != result["md5_after"], "the window's pixels did not change"
        result["passed"] = True
    finally:
        with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"[bg proof] evidence: {out_dir}")
        if notepad_hwnd:
            _close_fixture_notepad(notepad_hwnd)
        if front_proc is not None:
            try:
                front_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        try:
            os.remove(scratch)
        except Exception:  # noqa: BLE001
            pass
