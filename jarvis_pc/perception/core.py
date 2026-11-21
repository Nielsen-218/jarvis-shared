"""Perception, in the README's order: UI Automation first, screenshots last. Read-only except the screenshot file.

- windows(): open windows (title + process) from a T0 PowerShell query, filtered in Python.
- foreground(): the window in front, via user32 (no shell).
- ui_tree(): UI Automation text of a window, only if the optional `uiautomation` package is installed. If it is not,
  this says so instead of guessing; install it through the gated installer (pip in a venv is T1).
- screenshot(): PNG of the screen into an allowlisted folder (T1, journaled, undoable), verified by re-opening it.
"""
from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ..actions.shell import run
from ..diagnostics.findings import parse_json_rows
from ..guardrails.gate import Guardrails
from ..guardrails.tiers import Tier
from ..verify.checks import CheckResult
from ..verify.journal import ChangeJournal, UndoStep
from ..verify.report import Report

CMD_WINDOWS = "Get-Process | Select-Object Name,Id,MainWindowTitle | ConvertTo-Json"


@dataclass
class WindowInfo:
    title: str
    process: str
    pid: int


def windows(guard: Guardrails, run_fn: Callable = run) -> List[WindowInfo]:
    res = run_fn(guard, CMD_WINDOWS, intent="perception: windows", timeout=60)
    if not (res.ran and res.ok) or getattr(res, "truncated", False):
        raise RuntimeError("could not list windows: " + ("blocked by the gate" if not res.ran else f"exit {res.exit_code}"))
    return [WindowInfo(str(r["MainWindowTitle"]), str(r.get("Name")), int(r.get("Id") or 0))
            for r in parse_json_rows(res.stdout) if str(r.get("MainWindowTitle") or "").strip()]


def foreground(user32: Any = None) -> Optional[WindowInfo]:
    """The focused window, or None when there is none (locked screen, desktop) or off Windows."""
    if user32 is None:
        if os.name != "nt":
            return None
        user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return WindowInfo(buf.value, "", int(pid.value))


def uia_available(module: Any = None) -> bool:
    if module is not None:
        return True
    try:
        import uiautomation  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def ui_tree(title_part: str, *, module: Any = None, max_items: int = 200, max_depth: int = 6) -> List[str]:
    """Lines like 'ButtonControl: Save' for the window whose title contains title_part. Needs `uiautomation`."""
    if not uia_available(module):
        raise RuntimeError("UI Automation is not available: `pip install uiautomation` (a T1 install in the JARVIS venv)")
    auto = module
    if auto is None:
        import uiautomation as auto  # type: ignore[no-redef]
    win = auto.WindowControl(searchDepth=1, SubName=title_part)
    if not win.Exists(1, 0):
        return []
    out: List[str] = []

    def walk(ctrl: Any, depth: int) -> None:
        if len(out) >= max_items or depth > max_depth:
            return
        name = (getattr(ctrl, "Name", "") or "").strip()
        if name:
            out.append(f"{'  ' * depth}{getattr(ctrl, 'ControlTypeName', 'Control')}: {name[:120]}")
        for child in ctrl.GetChildren():
            walk(child, depth + 1)
    walk(win, 0)
    return out


def default_grabber():
    from PIL import ImageGrab
    return ImageGrab.grab(all_screens=True)


def screenshot(guard: Guardrails, journal: ChangeJournal, dest_dir: str, grabber: Callable = default_grabber,
               clock: Callable[[], float] = time.time) -> Report:
    """Save a screenshot into dest_dir (must be inside the allowlist). Verified by re-opening the PNG."""
    task = "screenshot"
    dest_dir = os.path.abspath(dest_dir)
    if not guard.allowlist.is_allowed(dest_dir):
        return Report(task, [CheckResult("destination", False, f"outside allowed folders: {dest_dir}")])
    path = os.path.join(dest_dir, "screen-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(clock())) + ".png")
    if os.path.exists(path):
        return Report(task, [CheckResult("destination", False, "already exists; not overwriting")])
    d = guard.authorize_action(f"save screenshot to {path}", tier=Tier.T1, reasons=["writes one image file"], intent="perception")
    if not d.allowed:
        return Report(task, [CheckResult("permission", False, "; ".join(d.reasons[-2:]))])
    os.makedirs(dest_dir, exist_ok=True)
    try:
        grabber().save(path, "PNG")
    except Exception as e:  # noqa: BLE001
        return Report(task, [CheckResult("capture", False, f"{type(e).__name__}: {e}")])
    eid = journal.record("screenshot", [UndoStep("delete_created", path=path)], decision_id=d.id, tier="T1")
    checks = [CheckResult("file written", os.path.isfile(path) and os.path.getsize(path) > 0, path)]
    try:
        from PIL import Image
        with Image.open(path) as im:
            checks.append(CheckResult("image opens", im.width > 0 and im.height > 0, f"{im.width}x{im.height}"))
    except Exception as e:  # noqa: BLE001
        checks.append(CheckResult("image opens", False, f"{type(e).__name__}: {e}"))
    return Report(task, checks, [eid], f"journal.undo(guard, ids=['{eid}'])")
