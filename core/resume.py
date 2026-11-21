"""Notice when the PC comes back from sleep (or was frozen for a while) and let the parts that hold GPU state start
over: the Kokoro voice (CUDA context), the local model's warm cache. Windows gives no event a background Python
thread can hear without a window message pump, but a thread that sleeps five seconds and wakes to find a minute
gone has slept through a suspend. Hooks are plain callables; each is isolated from the others and never raises."""
from __future__ import annotations

import threading
import time
from typing import Callable

GAP = 60.0          # seconds of missing time that count as a resume (a busy machine never stalls a thread this long)
TICK = 5.0
_hooks: list[tuple[str, Callable[[], None]]] = []
_lock = threading.Lock()
resumed_at: list[float] = []    # wall-clock times of detected resumes (diagnostics)


def on_resume(name: str, fn: Callable[[], None]) -> None:
    with _lock:
        _hooks.append((name, fn))


def fire(gap: float = 0.0) -> list[str]:
    """Run every hook (also called by tests and the tray's manual recovery). Returns the names that ran."""
    ran = []
    with _lock:
        hooks = list(_hooks)
    for name, fn in hooks:
        try:
            fn()
            ran.append(name)
        except Exception as error:
            print(f"[resume] {name} failed: {type(error).__name__}: {error}")
    if gap:
        print(f"[resume] back after {gap:.0f} s away; reset {', '.join(ran) or 'nothing'}")
    return ran


def _watch(clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    last = clock()
    while True:
        sleep(TICK)
        now = clock()
        if now - last > TICK + GAP:
            resumed_at.append(now)
            fire(now - last)
        last = now


def start(clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> threading.Thread:
    thread = threading.Thread(target=_watch, args=(clock, sleep), daemon=True, name="resume-watch")
    thread.start()
    return thread
