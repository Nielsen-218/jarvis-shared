"""School subjects for gaming and graphics systems (blueprint area 19), graded against what Windows reports about this PC's graphics hardware.

Read-only. The harness asks Windows (WMI and the display metrics) itself for the ground truth, without the commands being tested.
These lessons are the observe level of the area: knowing the GPU, its driver and the screen. They do not launch or control games.
"""
from __future__ import annotations

import ctypes
import subprocess
from typing import List, Optional

from .curriculum import Context, HIGH_SCHOOL, Num, Subject, Task


def _wmi(prop: str) -> Optional[str]:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty {prop}"],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:  # noqa: BLE001
        return None
    rows = [line.strip() for line in out.splitlines() if line.strip() and "basic" not in line.lower()]
    return rows[0] if rows else None


def _gpu_name(c: Context) -> Task:
    n = _wmi("Name")
    return Task("which graphics card does this PC have", {}, [n] if n else [], None, 0)


def _gpu_driver(c: Context) -> Task:
    v = _wmi("DriverVersion")
    return Task("what version of the graphics driver is installed", {}, [v] if v else [], None, 0)


def _screen_width(c: Context) -> Task:
    try:
        w = int(ctypes.windll.user32.GetSystemMetrics(0))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        w = 0
    return Task("how many pixels wide is the main screen", {}, [Num(w, 0.0)] if w else [], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("gfx_gpu_name", "name the graphics card", hs, _gpu_name, ("gpu", "video card"), needs_windows=True),
        Subject("gfx_driver_version", "read the graphics driver version", hs, _gpu_driver, ("gpu driver", "nvidia driver"), needs_windows=True),
        Subject("gfx_screen_width", "read the screen width in pixels", hs, _screen_width, ("resolution", "display size"), needs_windows=True),
    ]
