"""School subjects for virtual machines and test environments (blueprint area 21), graded against the real WSL2 Linux on this PC.

Read-only: the gate lets `wsl --list ...` and `wsl -d <distro> -- <one fixed read-only command>` run without asking (guardrails/tiers.py).
The harness asks WSL itself for the ground truth, so these lessons exist only on a PC that has a WSL distribution.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional

from .curriculum import Context, HIGH_SCHOOL, Subject, Task


def _wsl(*args: str) -> Optional[str]:
    exe = shutil.which("wsl")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *args], capture_output=True, timeout=60)
    except Exception:  # noqa: BLE001
        return None
    raw = r.stdout
    text = raw.decode("utf-16-le", "ignore") if b"\x00" in raw[:8] else raw.decode("utf-8", "ignore")
    text = text.replace("\x00", "").replace("﻿", "").strip()
    return text if r.returncode == 0 and text else None


def _distro() -> Optional[str]:
    out = _wsl("--list", "--quiet")
    for line in (out or "").splitlines():
        if line.strip():
            return line.strip()
    return None


def _name(c: Context) -> Task:
    d = _distro()
    return Task("which Linux distribution is installed in WSL on this PC", {}, [d] if d else [], None, 0)


def _release(c: Context) -> Task:
    d = _distro()
    out = _wsl("-d", d, "--", "cat", "/etc/os-release") if d else None
    ver = next((l.split("=", 1)[1].strip().strip('"') for l in (out or "").splitlines() if l.startswith("VERSION_ID=")), "")
    return Task("which release number of the Linux distribution {distro} is running in WSL", {"distro": d or ""}, [ver] if ver and d else [], None, 0)


def _kernel(c: Context) -> Task:
    d = _distro()
    out = _wsl("-d", d, "--", "uname", "-r") if d else None
    return Task("what Linux kernel version does the WSL distribution {distro} run", {"distro": d or ""}, [out] if out and d else [], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("wsl_distro_name", "name the Linux installed in WSL", hs, _name, ("installed linux", "wsl distribution"), needs_windows=True),
        Subject("wsl_linux_release", "read a WSL Linux's release number", hs, _release, ("linux release", "os version in wsl"), needs_windows=True),
        Subject("wsl_linux_kernel", "read a WSL Linux's kernel version", hs, _kernel, ("linux kernel", "kernel in wsl"), needs_windows=True),
    ]
