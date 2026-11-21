"""Stopping the OPERATION, not only the wait.

core.pcmethods runs every PC step on a worker thread with a hard ceiling. Python cannot kill a thread, so when a step passes
its ceiling the caller stops waiting, but the worker may still be inside a slow call (UI Automation, a window that does not
answer) and could wake up later and press keys, paste, launch or close something nobody is waiting for any more.

This module closes that gap:
  * each bounded step binds a StepToken to its worker thread;
  * when the step passes its ceiling the token is EXPIRED;
  * every primitive that changes the PC (keystrokes, clipboard paste, launching, closing or focusing a window, setting the
    volume) calls fence() first, which raises StepAbandoned on an expired token, so a woken-up worker changes nothing;
  * child processes a step registers (and a shell step's kill switch) are terminated with their whole process tree.

Outside a bounded step (no token bound) fence() does nothing, so the legacy paths behave exactly as before.

Background mode (Mark VI, 2026-11-10): a mission NEVER takes the foreground. Inside `with background_only():` every primitive that
would focus a window, move the real mouse or send real keys calls require_foreground() first, which raises BackgroundUnavailable
instead of acting. A path nobody converted to a background method therefore refuses, it never steals the owner's front window.
Outside background mode require_foreground() is exactly fence().
"""
from __future__ import annotations

import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional

_local = threading.local()


class StepAbandoned(InterruptedError):
    """A step that passed its time limit tried to change the PC; the change was not made."""


class BackgroundUnavailable(RuntimeError):
    """A background (mission) step would have needed the foreground, the real mouse or real keys; nothing was done.
    Never retried in the foreground: the owner does it at the PC (2026-11-10, Mark VI part 3)."""
    kind = "background_unavailable"

    def __init__(self, what: str, reason: str = "needs you at the PC"):
        self.what, self.reason = what, reason
        super().__init__(f"{reason}: {what}")


class UncertainOutcome(RuntimeError):
    """A write WAS sent but could not be confirmed by reading it back (2026-11-10 review). Never 'nothing was done', and
    never followed by another rung or method that writes again (that is how text got typed twice): the owner checks."""
    kind = "uncertain"


class StepToken:
    def __init__(self, tool: str):
        self.tool = tool
        self.background = False               # set when the step runs for a mission (background_only on the caller)
        self.expired = threading.Event()
        self._lock = threading.Lock()
        self._pids: List[int] = []
        self._on_expire: List[Callable[[], None]] = []
        self.blocked: List[str] = []          # what a woken-up worker tried to do after the limit (evidence)

    def register_pid(self, pid: int) -> None:
        with self._lock:
            self._pids.append(int(pid))

    def on_expire(self, fn: Callable[[], None]) -> None:
        """Run fn when the step expires (a shell step trips its kill switch here)."""
        with self._lock:
            if self.expired.is_set():
                fn()
                return
            self._on_expire.append(fn)

    def expire(self) -> List[int]:
        """Mark the step abandoned, run the expiry hooks and kill registered process trees. Returns the pids killed."""
        with self._lock:
            self.expired.set()
            hooks, pids = list(self._on_expire), list(self._pids)
        for fn in hooks:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        killed = []
        for pid in pids:
            if _kill_tree(pid):
                killed.append(pid)
        return killed


def _kill_tree(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
            return r.returncode == 0
        import os
        import signal
        os.kill(pid, signal.SIGKILL)
        return True
    except Exception:  # noqa: BLE001
        return False


def bind(token: Optional[StepToken]) -> None:
    _local.token = token


def current() -> Optional[StepToken]:
    return getattr(_local, "token", None)


def fence(what: str) -> None:
    """Call immediately before changing the PC. Raises StepAbandoned if this thread's step passed its time limit."""
    token = current()
    if token is not None and token.expired.is_set():
        token.blocked.append(what)
        raise StepAbandoned(f"the {token.tool} step had passed its time limit, so '{what}' was not done")


@contextmanager
def background_only() -> Iterator[None]:
    """Everything this thread does inside the block must leave the owner's foreground, cursor and keyboard alone
    (2026-11-10). core.pcmethods carries the flag onto its step worker thread."""
    prev = getattr(_local, "background", False)
    _local.background = True
    try:
        yield
    finally:
        _local.background = prev


def is_background() -> bool:
    token = current()
    return bool(getattr(_local, "background", False) or (token is not None and getattr(token, "background", False)))


def require_foreground(what: str) -> None:
    """Call immediately before focusing a window, moving the real mouse or sending real keys: fence(), and in background
    mode refuse with BackgroundUnavailable (nothing was done)."""
    fence(what)
    if is_background():
        raise BackgroundUnavailable(what)


def register_process(proc) -> None:
    """A child process this step started: killed with its tree if the step passes its limit."""
    token = current()
    if token is not None:
        token.register_pid(getattr(proc, "pid", proc))
        if token.expired.is_set():
            token.expire()
