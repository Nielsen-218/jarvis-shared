"""Free, read-only PC awareness: what is open, what is in front, what is running, is the GPU busy.

Fills the gap between "look at a screenshot" (paid vision) and "typed approved action": JARVIS can now
check application state cheaply before and after acting, and wait for a window instead of guessing.
No clicks, keystrokes, writes or network. Windows only (ctypes + built-in tools).
"""
import ctypes
import json
import subprocess
import time
from ctypes import wintypes

from tools.registry import tool

_u32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
_k32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None


def _process_name(pid):
    h = _k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        ok = _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return buf.value.rsplit("\\", 1)[-1] if ok else ""
    finally:
        _k32.CloseHandle(h)


def windows(visible_only=True):
    """Top-level windows with a title: [{title, process, pid, foreground, minimized, rect}]."""
    if _u32 is None:
        return []
    fg = _u32.GetForegroundWindow()
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if visible_only and not _u32.IsWindowVisible(hwnd):
            return True
        n = _u32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = wintypes.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append({"hwnd": int(hwnd), "title": buf.value, "process": _process_name(pid.value), "pid": pid.value,
                    "foreground": hwnd == fg, "minimized": bool(_u32.IsIconic(hwnd)),
                    "rect": [r.left, r.top, r.right, r.bottom]})
        return True

    _u32.EnumWindows(cb, 0)
    return out


def _run(args, timeout=8):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=0x08000000).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def gpu():
    out = _run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"])
    if not out.strip():
        return None
    n, used, total, util = [x.strip() for x in out.strip().splitlines()[0].split(",")]
    return {"name": n, "memory_used_mb": int(used), "memory_total_mb": int(total), "utilization_pct": int(util),
            "free_mb": int(total) - int(used)}


def processes_matching(*names):
    """Running processes whose image name contains any of `names`: [{name, pid, command_line}]."""
    q = " or ".join(f"Name like '%{n}%'" for n in names if n.replace(".", "").replace("-", "").isalnum())
    if not q:
        return []
    ps = f"Get-CimInstance Win32_Process -Filter \"{q}\" | Select-Object Name,ProcessId,CommandLine | ConvertTo-Json -Compress"
    raw = _run(["powershell", "-NoProfile", "-Command", ps], 15).strip()
    if not raw:
        return []
    data = json.loads(raw)
    data = data if isinstance(data, list) else [data]
    return [{"name": d["Name"], "pid": d["ProcessId"], "command_line": (d.get("CommandLine") or "")[:300]} for d in data]


# Words a model sends when it means "no filter" (live 2026-11-06: contains='all' -> 0 windows -> an invented answer).
_EVERYTHING = {"", "all", "any", "*", "everything", "every", "open", "apps", "all apps", "windows", "all windows", "none"}


@tool("List the windows open on this PC right now (title, app, which one is in front, minimized). Free and read-only; "
      "use it to know what is running before acting, or to confirm an action opened what it should.",
      {"contains": {"type": "string"}})
def list_windows(contains: str = "") -> str:
    want = (contains or "").strip().casefold()
    if want in _EVERYTHING:
        want = ""
    ws = [w for w in windows() if want in (w["title"] + " " + w["process"]).casefold()]
    out = {"count": len(ws), "windows": ws[:40]}
    if not ws and want:
        out["note"] = f"No open window matches '{contains}'. Say only that; call list_windows with contains='' to see every window."
    return json.dumps(out)


@tool("Observe the PC cheaply without a screenshot: the front window, all open windows, GPU memory/load, and "
      "any Blender or Jarvis-Blender-worker processes with their command lines (GUI vs headless). Read-only.", {})
def observe_pc() -> str:
    ws = windows()
    front = next((w for w in ws if w["foreground"]), None)
    return json.dumps({"foreground": front, "window_count": len(ws), "windows": [f"{w['process']}: {w['title'][:80]}" for w in ws[:25]],
                       "gpu": gpu(), "blender": processes_matching("blender")})


@tool("Wait up to a timeout (max 120 s) for a window whose title or app contains some text to appear. Returns the "
      "window if it did, or says it did not. Use after launching an app instead of guessing it is ready.",
      {"title_contains": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, required=["title_contains"], slow=True)
def wait_for_window(title_contains: str, timeout_seconds: int = 8) -> str:
    deadline = time.monotonic() + max(1, min(int(timeout_seconds), 120))
    needle = title_contains.casefold().strip()
    if not needle:
        return "Say what window to wait for."
    while True:
        for w in windows():
            if needle in (w["title"] + " " + w["process"]).casefold():
                return json.dumps({"found": True, "window": w})
        if time.monotonic() >= deadline:
            return json.dumps({"found": False, "waited_seconds": max(1, min(int(timeout_seconds), 120))})
        time.sleep(0.5)
