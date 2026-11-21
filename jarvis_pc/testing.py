"""Test-time safety net: no test may run a real winget/uninstall/delete on the machine that runs the tests.

Tests are supposed to use fake runners, but "supposed to" is a convention. This makes it a rule. While a test module is
active, launching a process whose command mentions winget, msiexec, uninstall, Remove-Item, schtasks, reg delete/add,
Set-MpPreference or Format-Volume raises, and so does deleting anything outside the system temp folder (os.remove,
os.unlink, os.rmdir, shutil.rmtree). Real read-only commands (a version probe, Get-CimInstance) and temp-folder work
(Expand-Archive into a temp dir, copying, the journal's undo) are unaffected.

Use it in a test module with two lines:

    from jarvis_pc.testing import module_guard
    setUpModule, tearDownModule = module_guard()
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Tuple

RISKY_COMMAND = re.compile(
    r"\bwinget(\.exe)?\b|\bmsiexec\b|(?<![\\/])\buninstall\b|\bremove-item\b|\bschtasks\b|\breg(\.exe)?\s+(delete|add)\b|"
    r"\bset-mppreference\b|\bformat-volume\b", re.I)


class UnsafeTestAction(RuntimeError):
    """A test tried to do something real and destructive on the host."""


# Resolved ONCE, at import, before anything is patched. tempfile.gettempdir() can create and delete a probe file while
# holding a non-reentrant lock; calling it from inside the guarded os.unlink would deadlock.
_TEMP_ROOT = os.path.normcase(os.path.realpath(tempfile.gettempdir()))


def _temp_root() -> str:
    return _TEMP_ROOT


def _inside_temp(path: str) -> bool:
    p = os.path.normcase(os.path.realpath(str(path)))
    root = _temp_root()
    return p == root or p.startswith(root + os.sep)


def _remove_item_inside_temp(text: str) -> bool:
    """True when the only risky thing is `Remove-Item` and every full path written after it is inside the temp folder (a sandbox)."""
    m = re.search(r"\bremove-item\b(.*)$", text, re.I | re.S)
    if m is None or RISKY_COMMAND.search(text[:m.start()] + re.sub(r"\bremove-item\b", "", m.group(1), flags=re.I)):
        return False
    paths = re.findall(r"[A-Za-z]:[\\/][^\s\"';|]*", m.group(1))
    return bool(paths) and all(_inside_temp(p) for p in paths)


class _Guard:
    def __init__(self) -> None:
        self.depth = 0
        self.saved: dict = {}

    def start(self) -> None:
        self.depth += 1
        if self.depth > 1:
            return
        orig_init = subprocess.Popen.__init__
        self.saved["popen"] = orig_init

        def popen_init(proc, args, *a, **k):
            text = args if isinstance(args, str) else " ".join(map(str, args))
            if RISKY_COMMAND.search(text) and not _remove_item_inside_temp(text):
                raise UnsafeTestAction(f"a test tried to launch a real risky command: {text[:200]}")
            return orig_init(proc, args, *a, **k)
        subprocess.Popen.__init__ = popen_init  # type: ignore[method-assign]

        for mod, name in ((os, "remove"), (os, "unlink"), (os, "rmdir"), (shutil, "rmtree")):
            orig = getattr(mod, name)
            self.saved[(mod, name)] = orig

            def make(orig_fn: Callable, label: str) -> Callable:
                def guarded(path, *a, **k):
                    if not _inside_temp(path):
                        raise UnsafeTestAction(f"a test tried to {label} outside the temp folder: {path}")
                    return orig_fn(path, *a, **k)
                return guarded
            setattr(mod, name, make(orig, f"{name}"))

    def stop(self) -> None:
        self.depth = max(0, self.depth - 1)
        if self.depth:
            return
        subprocess.Popen.__init__ = self.saved.pop("popen")  # type: ignore[method-assign]
        for key in [k for k in self.saved if isinstance(k, tuple)]:
            setattr(key[0], key[1], self.saved.pop(key))


_GUARD = _Guard()


def module_guard() -> Tuple[Callable[[], None], Callable[[], None]]:
    """(setUpModule, tearDownModule) for unittest and pytest: the net is active only while this module's tests run."""
    return _GUARD.start, _GUARD.stop
