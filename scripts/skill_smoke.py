"""Safe smoke test of EVERY registered Jarvis skill (tools/*.py @tool functions).

Calls each skill through the real dispatch path (tools.registry.run: validate -> call -> exceptions become
"TypeError: ..." errors) with schema-valid synthetic arguments, and classifies the outcome:

    ok        returned a normal result
    handled   returned a friendly error (is_error) — e.g. "not found", "needs approval", "offline"
    crash     an uncaught exception escaped the skill (a real bug: "KeyError: ...", "TypeError: ...")
    timeout   didn't return within the time limit (likely blocking on something)

It is SAFE to run on the owner's machine:
  * data goes to a throwaway temp folder (core.storage.DATA), never the real memories/wardrobe/budgets;
  * the network is blocked (sockets refuse), so nothing is fetched, posted, downloaded or synced;
  * subprocess / os.startfile / webbrowser / os.system are faked (no app launches, installs or shutdowns);
  * mouse/keyboard injection, the clipboard, the camera, screen capture and the speaker volume are faked;
  * destructive filesystem calls are only allowed inside the temp folder;
  * pending approvals are cleared, so risky actions only *request* approval and never run.

Run:  python scripts/skill_smoke.py [--json out.json] [--only name,name]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TIMEOUT_S = 20
ATTEMPTS: list[tuple[str, str]] = []          # (channel, detail) side effects a skill TRIED (all faked)
_current = {"skill": None}


def _note(channel: str, detail: str = "") -> None:
    ATTEMPTS.append((_current["skill"] or "?", f"{channel}: {detail}"[:160]))


# --------------------------------------------------------------------------------------------- sandbox
def _install_sandbox(tmp: Path) -> None:
    # 1) network: refuse every connection / lookup — with the SAME exception types a real outage raises
    #    (connection refused, DNS failure), so skills are judged on how they handle a real one.
    def _refused(*_a, **_k):
        raise ConnectionRefusedError(10061, "network blocked (skill smoke test)")

    def _no_dns(*_a, **_k):
        raise socket.gaierror(11001, "getaddrinfo failed (skill smoke test: network blocked)")
    socket.socket.connect = lambda self, *a, **k: _refused()            # type: ignore[assignment]
    socket.socket.connect_ex = lambda self, *a, **k: 10061              # type: ignore[assignment]
    socket.socket.sendto = lambda self, *a, **k: (_note("udp", "sendto"), _refused())[1]   # e.g. Wake-on-LAN
    socket.socket.send = lambda self, *a, **k: _refused()               # type: ignore[assignment]
    socket.create_connection = _refused                                 # type: ignore[assignment]
    socket.getaddrinfo = _no_dns                                        # type: ignore[assignment]
    # Explicit MCP child brokers must not evade the smoke process's socket stubs.
    from core import research_mcp
    research_mcp.call = _refused

    # 2) processes, shell, browser, file associations
    import subprocess

    class _Proc:
        returncode, pid, stdout, stderr = 0, 0, "", ""
        def __init__(self, *a, **k): _note("subprocess", " ".join(map(str, a[0])) if a and isinstance(a[0], (list, tuple)) else str(a[0] if a else ""))
        def communicate(self, *a, **k): return ("", "")
        def wait(self, *a, **k): return 0
        def poll(self): return 0
        def kill(self): pass
        def terminate(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False

    # Network CLIs fail the way they really do when the far end is unreachable (exit 255 + connection stderr),
    # so skills are tested on the REAL failure path — not handed a fake "success with no output".
    _NET_CLIS = {"ssh", "scp", "rsync", "mcporter", "curl", "wget", "yt-dlp", "ping"}

    def _argv0(a) -> str:
        cmd = a[0] if a else ""
        first = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd).split(" ", 1)[0]
        return Path(str(first)).stem.lower()

    def _run(*a, **k):
        _note("subprocess", " ".join(map(str, a[0])) if a and isinstance(a[0], (list, tuple)) else str(a[0] if a else ""))
        txt = bool(k.get("text") or k.get("universal_newlines") or k.get("encoding"))
        empty = "" if txt else b""
        if _argv0(a) in _NET_CLIS:
            err = f"{_argv0(a)}: connect to host 192.0.2.10 port 22: Connection refused (skill smoke test)"
            return subprocess.CompletedProcess(a[0] if a else "", 255, stdout=empty, stderr=err if txt else err.encode())
        return subprocess.CompletedProcess(a[0] if a else "", 0, stdout=empty, stderr=empty)

    def _check_output(*a, **k):
        _note("subprocess", str(a[0] if a else ""))
        return "" if (k.get("text") or k.get("universal_newlines") or k.get("encoding")) else b""

    subprocess.run = _run                                               # type: ignore[assignment]
    subprocess.Popen = _Proc                                            # type: ignore[assignment,misc]
    subprocess.call = lambda *a, **k: (_note("subprocess", str(a[0] if a else "")), 0)[1]     # type: ignore[assignment]
    subprocess.check_call = lambda *a, **k: (_note("subprocess", str(a[0] if a else "")), 0)[1]  # type: ignore[assignment]
    subprocess.check_output = _check_output                             # type: ignore[assignment]
    os.system = lambda cmd: (_note("os.system", cmd), 0)[1]              # type: ignore[assignment]
    # Registry smoke tests should not scan the owner's live processes. Querying every
    # process's cwd can also exceed the per-skill timeout on a busy PC.
    import psutil
    psutil.process_iter = lambda *a, **k: iter(())                       # type: ignore[assignment]
    if hasattr(os, "startfile"):
        os.startfile = lambda p, *a, **k: _note("os.startfile", str(p))  # type: ignore[assignment]
    import webbrowser
    for fn in ("open", "open_new", "open_new_tab"):
        setattr(webbrowser, fn, lambda url, *a, _fn=fn, **k: (_note("webbrowser", str(url)), True)[1])

    # 3) destructive filesystem ops: only inside the temp sandbox
    def _inside(p) -> bool:
        try:
            return Path(p).resolve().is_relative_to(tmp.resolve())
        except Exception:
            return False
    _rmtree, _move, _unlink, _rmdir = shutil.rmtree, shutil.move, Path.unlink, Path.rmdir
    shutil.rmtree = lambda p, *a, **k: _rmtree(p, *a, **k) if _inside(p) else _note("rmtree", str(p))     # type: ignore[assignment]
    shutil.move = lambda s, d, *a, **k: _move(s, d, *a, **k) if _inside(s) and _inside(d) else _note("move", f"{s} -> {d}")  # type: ignore[assignment]
    Path.unlink = lambda self, *a, **k: _unlink(self, *a, **k) if _inside(self) else _note("unlink", str(self))  # type: ignore[assignment]
    Path.rmdir = lambda self, *a, **k: _rmdir(self, *a, **k) if _inside(self) else _note("rmdir", str(self))     # type: ignore[assignment]
    _os_remove = os.remove
    os.remove = lambda p, *a, **k: _os_remove(p) if _inside(p) else _note("os.remove", str(p))              # type: ignore[assignment]
    os.unlink = os.remove                                                                                    # type: ignore[assignment]

    # 4) input injection, clipboard, camera, screen capture
    import ctypes
    if hasattr(ctypes, "windll"):
        u32 = ctypes.windll.user32
        for fn in ("keybd_event", "mouse_event", "SendInput", "SetCursorPos", "LockWorkStation", "ExitWindowsEx"):
            setattr(u32, fn, lambda *a, _fn=fn, **k: (_note("user32", _fn), 1)[1])

    # The speakers. tools.media reaches the REAL audio endpoint through pycaw, and until 2026-11-11 the smoke run called
    # volume(percent=3) on it (3 is the sample for an integer whose name contains "n"): every test run, so every
    # deploy, left the owner's PC at 3% ("every time Jarvis starts, it always put my volume down"). The skills now
    # talk to a stand-in that keeps a level and a mute flag, so they are still exercised end to end.
    class _Endpoint:
        level, mute = 0.5, 0

        def GetMasterVolumeLevelScalar(self):
            return self.level

        def SetMasterVolumeLevelScalar(self, value, _context=None):
            _note("audio", f"set volume {round(float(value) * 100)}%")
            self.level = float(value)

        def GetMute(self):
            return self.mute

        def SetMute(self, mute, _context=None):
            _note("audio", f"set mute {int(bool(mute))}")
            self.mute = int(bool(mute))
    _speakers = types.SimpleNamespace(EndpointVolume=_Endpoint(), FriendlyName="Speakers (skill smoke test)")
    _pycaw, _pycaw_inner = types.ModuleType("pycaw"), types.ModuleType("pycaw.pycaw")
    _pycaw_inner.AudioUtilities = types.SimpleNamespace(GetSpeakers=lambda: _speakers, GetAllSessions=lambda: [])
    _pycaw.pycaw = _pycaw_inner
    sys.modules["pycaw"], sys.modules["pycaw.pycaw"] = _pycaw, _pycaw_inner

    class _Any(types.ModuleType):
        """A module whose every attribute is a harmless callable returning a neutral value."""
        def __init__(self, name, **fixed):
            super().__init__(name)
            self.__dict__.update(fixed)
        def __getattr__(self, item):
            if item.startswith("__"):
                raise AttributeError(item)
            return lambda *a, **k: (_note(self.__name__, item), None)[1]

    sys.modules["pyautogui"] = _Any("pyautogui", FAILSAFE=True, PAUSE=0.0,
                                    size=lambda: (1920, 1080), position=lambda: (0, 0),
                                    FailSafeException=type("FailSafeException", (Exception,), {}))
    # The keyboard library and pywinauto's key sending call Windows through their OWN handles, past the user32 stubs above:
    # found 2026-11-08 when a test run's keystrokes reset the owner's idle clock (and could land in his front window).
    sys.modules["keyboard"] = _Any("keyboard")
    try:
        import pywinauto.keyboard as _pk
        _pk.send_keys = lambda *a, **k: (_note("pywinauto", "send_keys"), None)[1]
        import pywinauto.mouse as _pm
        for _fn in ("click", "double_click", "right_click", "move", "press", "release", "scroll", "wheel_click"):
            if hasattr(_pm, _fn):
                setattr(_pm, _fn, lambda *a, _f=_fn, **k: (_note("pywinauto.mouse", _f), None)[1])
        from pywinauto import base_wrapper as _bw
        _bw.BaseWrapper.type_keys = lambda self, *a, **k: (_note("pywinauto", "type_keys"), self)[1]
        _bw.BaseWrapper.click_input = lambda self, *a, **k: (_note("pywinauto", "click_input"), self)[1]
    except Exception:
        pass
    # Keep optional DXGI capture inside the no-desktop sandbox too.
    sys.modules["dxcam"] = _Any("dxcam", create=lambda *a, **k: None)
    _clip = {"v": ""}
    sys.modules["pyperclip"] = _Any("pyperclip", paste=lambda: (_note("clipboard", "paste"), _clip["v"])[1],
                                    copy=lambda t: (_note("clipboard", "copy"), _clip.__setitem__("v", t))[1])

    # The screen reader's OCR engine. read_screen loaded the REAL RapidOCR models here (about 14 s on a quiet PC, well
    # past the 20 s limit when the GPU and CPU are busy, and one run died inside the native runtime), so the gate
    # depended on what the owner was doing. The stand-in notes the read and finds no text.
    class _NoOcr:
        def __call__(self, image, *a, **k):
            _note("ocr", "read")
            return [], 0.0
    sys.modules["rapidocr_onnxruntime"] = _Any("rapidocr_onnxruntime", RapidOCR=_NoOcr)

    try:
        from PIL import Image, ImageGrab
        ImageGrab.grab = lambda *a, **k: (_note("screen", "grab"), Image.new("RGB", (64, 48), (20, 20, 30)))[1]  # type: ignore[assignment]
    except Exception:
        pass
    try:
        import cv2  # noqa: F401

        class _Cam:
            def __init__(self, *a, **k): _note("camera", "open")
            def isOpened(self): return False
            def read(self): return (False, None)
            def release(self): pass
            def set(self, *a): return False
            def get(self, *a): return 0
        sys.modules["cv2"].VideoCapture = _Cam                   # type: ignore[attr-defined]
        # Exercise calibration's normal cancel response without opening an
        # interactive selector or waiting for owner input during smoke tests.
        cv2.selectROI = lambda *a, **k: (_note("cv2", "selectROI cancelled"), (0, 0, 0, 0))[1]
        cv2.destroyAllWindows = lambda: None
    except Exception:
        pass
    try:
        import mss  # noqa: F401
        sys.modules["mss"].mss = lambda *a, **k: (_ for _ in ()).throw(OSError("screen capture faked"))  # type: ignore[attr-defined]
    except Exception:
        pass


def _isolate_data(tmp: Path) -> None:
    from core import storage, users
    storage.DATA = tmp
    # The memory vault lives OUTSIDE storage.DATA (else ROOT/vault = the owner's real vault): pin it to the sandbox,
    # so recall/delete_memory/remember_note can never read or delete real memories (SQL deletes bypass fs guards).
    os.environ["JARVIS_VAULT"] = str(tmp / "vault")
    os.environ["JARVIS_PC_ROOT"] = str(tmp / "pcroot")   # the PC-control skills (and the school) must never read or write the real C:\Jarvis
    os.environ["JARVIS_SCHOOL"] = "off"                    # and a smoke run must never start a real study session (models, windows, paid API)
    for v in ("JARVIS_SERVER_SSH", "JARVIS_ROLE", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN"):
        os.environ.pop(v, None)
    users.set_current("owner")


# --------------------------------------------------------------------------------------------- args
_TODAY = date.today()
_NAMED = {
    "date": _TODAY.isoformat(), "start": _TODAY.isoformat(), "end": (_TODAY + timedelta(days=3)).isoformat(),
    "start_date": _TODAY.isoformat(), "end_date": (_TODAY + timedelta(days=3)).isoformat(),
    "depart": (_TODAY + timedelta(days=10)).isoformat(), "return_date": (_TODAY + timedelta(days=14)).isoformat(),
    "when": "tomorrow at 9am", "time": "09:00", "at": "09:00",
    "city": "Fayetteville, NC", "location": "Fayetteville, NC", "place": "Fayetteville, NC",
    "origin": "FAY", "destination": "MIA", "from_": "FAY", "to": "MIA",
    "url": "https://example.com", "link": "https://example.com", "urls": ["https://example.com"],
    "email": "test@example.com", "phone": "+15555550100",
    "name": "Test Item", "title": "Test Title", "label": "Test", "person": "owner",
    "query": "test", "q": "test", "text": "test note", "note": "test note", "message": "hello",
    "category": "groceries", "item": "milk", "food": "banana", "unit": "g", "store": "Walmart",
    "language": "French", "words": ["bonjour"], "path": "Movies", "folder": "Movies",
    "app": "notepad", "program": "notepad", "action": "status", "command": "status",
}


def _value(key: str, spec: dict):
    if "enum" in spec and spec["enum"]:
        return spec["enum"][0]
    t = spec.get("type")
    k = key.lower()
    if t == "string":
        for frag, v in _NAMED.items():
            if frag == k and isinstance(v, str):
                return v
        for frag, v in _NAMED.items():
            if frag in k and isinstance(v, str):
                return v
        return "test"
    if t == "integer":
        if any(s in k for s in ("minute", "min")): return 5
        if any(s in k for s in ("second", "sec")): return 30
        if "year" in k: return _TODAY.year
        if any(s in k for s in ("day", "days")): return 3
        if any(s in k for s in ("limit", "count", "n", "top", "max")): return 3
        return 1
    if t == "number":
        if any(s in k for s in ("amount", "price", "cost", "budget", "total")): return 12.5
        if any(s in k for s in ("lat",)): return 35.05
        if any(s in k for s in ("lon", "lng")): return -78.88
        return 1.0
    if t == "boolean":
        return False
    if t == "array":
        items = spec.get("items")
        if isinstance(items, dict) and items.get("type"):
            return [_value(k.rstrip("s") or "item", items)]          # one schema-valid element (objects recurse)
        named = _NAMED.get(k)
        return named if isinstance(named, list) else ["test"]
    if t == "object":
        props = spec.get("properties")
        if isinstance(props, dict):
            req = spec.get("required", list(props))
            return {pk: _value(pk, pv) for pk, pv in props.items() if pk in req}
        return {}
    return "test"


def _args_for(schema: dict) -> dict:
    s = schema["input_schema"]
    return {k: _value(k, v) for k, v in s["properties"].items() if k in s.get("required", [])}


# --------------------------------------------------------------------------------------------- run
_CRASH = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit|Interrupt|StopIteration)\s*(:|$)")


def _classify(result, is_error: bool) -> str:
    """A crash is a result that STARTS with an exception name (an uncaught error reached the registry as-is).
    A friendly message that merely mentions one in parentheses ("…isn't reachable (ConnectionRefusedError…)")
    was handled by the skill or the registry."""
    if not is_error:
        return "ok"
    return "crash" if _CRASH.match(str(result).strip()) else "handled"


def _input_clock() -> int:
    """Windows' last-input tick: it moves when real keyboard/mouse input reaches the desktop (the sandbox must never)."""
    import ctypes
    if not hasattr(ctypes, "windll"):
        return 0

    class _LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = _LII(ctypes.sizeof(_LII), 0)
    ctypes.WinDLL("user32").GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


RECHECK_BUDGET_S = 150.0          # all re-checks of one smoke run together (the test gives the whole run 600 s)
_recheck = {"until": None}


def _quiet_for(seconds: float, limit: float) -> bool:
    """Wait up to `limit` s for a moment when he has not touched keyboard or mouse for `seconds`."""
    import ctypes
    if not hasattr(ctypes, "windll"):
        return True
    end = time.monotonic() + limit
    while True:
        idle_ms = (ctypes.windll.kernel32.GetTickCount() - _input_clock()) & 0xFFFFFFFF
        if idle_ms >= seconds * 1000:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.25)


def _touches(name: str, args: dict, seconds: float, tries: int = 3):
    """Whether the skill itself moved the input clock: True, False, or "inconclusive". The clock also moves when HE
    types or moves the mouse while a skill runs: gates during his work flagged wait_for_window (an 8 s wait that sends
    nothing) and open_app, 2026-11-11, even with a single quiet window after a re-run (he moves the mouse on and off).
    So each re-try first waits for a moment he is not touching anything; one re-run that leaves the clock alone clears
    the skill; it counts only when two re-runs move the clock while the windows as long right after them stay quiet.
    When he is too busy for that (or the shared re-check budget is spent) the answer is "inconclusive": reported, and
    left to the next gate run (the sandbox's own stubs above are the first line; this clock is the backstop)."""
    from core import approvals
    from tools import registry
    if _recheck["until"] is None:
        _recheck["until"] = time.monotonic() + RECHECK_BUDGET_S
    span = max(0.5, min(float(seconds), TIMEOUT_S))
    confirmed = 0
    for _ in range(tries):
        left = _recheck["until"] - time.monotonic()
        if left <= span or not _quiet_for(1.5, min(20.0, left - span)):
            break
        approvals._pending.clear(); approvals._direct.clear()
        before = _input_clock()
        th = threading.Thread(target=lambda: registry.run(name, args), daemon=True)
        th.start(); th.join(TIMEOUT_S)
        if _input_clock() == before:
            return False
        before = _input_clock()
        time.sleep(span)
        if _input_clock() == before:
            confirmed += 1
            if confirmed >= 2:
                return True
    return "inconclusive"


def run_all(only: set[str] | None = None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="jarvis_skill_smoke_"))
    _recheck["until"] = None
    _install_sandbox(tmp)
    _isolate_data(tmp)
    from core import approvals
    from tools import load_all, registry
    load_all()
    report = {"total": 0, "counts": {}, "skills": {}}
    for name in sorted(registry.names()):
        if only and name not in only:
            continue
        approvals._pending.clear(); approvals._direct.clear()
        args = _args_for(registry._TOOLS[name]["schema"])
        box = {}
        _current["skill"] = name
        t0 = time.perf_counter()

        def _go():
            try:
                box["res"] = registry.run(name, args)
            except BaseException as e:            # run() catches Exception; this catches the rest
                box["res"] = (f"{type(e).__name__}: {e}", True)
        before = _input_clock()
        th = threading.Thread(target=_go, daemon=True)
        th.start(); th.join(TIMEOUT_S)
        dt = round(time.perf_counter() - t0, 2)
        touched = _input_clock() != before
        if touched and "res" in box:
            touched = _touches(name, args, dt)
        if "res" not in box:
            status, text = "timeout", f"no result after {TIMEOUT_S}s"
        else:
            res, is_err = box["res"]
            status = _classify(res, is_err)
            text = res if isinstance(res, str) else f"<{type(res).__name__} of {len(res)} blocks>"
        report["skills"][name] = {"status": status, "seconds": dt, "args": args,
                                   "module": registry._TOOLS[name]["fn"].__module__.replace("tools.", ""),
                                   "result": str(text)[:400],
                                   "attempted": sorted({d for s, d in ATTEMPTS if s == name}),
                                   "touched_input": touched}
        report["counts"][status] = report["counts"].get(status, 0) + 1
        report["total"] += 1
    _current["skill"] = None
    import gc
    gc.collect()                                   # release SQLite handles (the sandbox vault) so Windows can delete
    for _ in range(3):
        shutil.rmtree(tmp, ignore_errors=True)
        if not tmp.exists():
            break
        time.sleep(0.3)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    import io, contextlib
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        rep = run_all({s.strip() for s in a.only.split(",") if s.strip()} or None)
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"total": rep["total"], "counts": rep["counts"]}))
    bad = {n: s for n, s in rep["skills"].items() if s["status"] in ("crash", "timeout")}
    for n, s in sorted(bad.items()):
        print(f"  {s['status'].upper():8} {s['module']:12} {n:26} {s['result'][:150]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
