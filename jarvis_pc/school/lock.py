"""One study session at a time, across processes (the scheduler thread, a manual pc_school_study, a second JARVIS).

A lock file made with O_EXCL. A lock older than `stale` seconds belongs to a crashed session and is taken over.
"""
from __future__ import annotations

import os
import time
from typing import Optional


class SessionLock:
    def __init__(self, path: str, stale: float = 1800.0):
        self.path, self.stale = path, stale
        self._held = False

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > self.stale:
                        os.remove(self.path)  # a crashed session left it behind
                        continue
                except OSError:
                    continue
                return False
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()} {time.time()}\n")
            self._held = True
            return True
        return False

    def release(self) -> None:
        if self._held:
            self._held = False
            try:
                os.remove(self.path)
            except OSError:
                pass

    def __enter__(self) -> "SessionLock":
        return self

    def __exit__(self, *exc) -> None:
        self.release()
