"""One line in the log for a failure that used to vanish into `except Exception: pass`.

48 such sites were counted (2026-10-30). Most are right to carry on — a tray icon that can't notify, a HUD that
isn't up yet — but silently, the same fault repeats unseen for weeks (the advanced tier did). `swallowed(where, e)`
keeps the behaviour (nothing raised) and writes the first occurrence, then one line per `every` seconds per site."""
from __future__ import annotations

import threading
import time

_last: dict[str, float] = {}
_count: dict[str, int] = {}
_lock = threading.Lock()


def swallowed(where: str, error: BaseException, every: float = 300.0) -> None:
    with _lock:
        now = time.monotonic()
        _count[where] = _count.get(where, 0) + 1
        if now - _last.get(where, -1e9) < every:
            return
        _last[where] = now
        n = _count[where]
    text = str(error).replace("\n", " ")[:160]
    print(f"[diag] {where}: {type(error).__name__}: {text}" + (f" (x{n} so far)" if n > 1 else ""))


def counts() -> dict[str, int]:
    """How often each site swallowed a failure since start (for the dashboard / tests)."""
    with _lock:
        return dict(_count)
