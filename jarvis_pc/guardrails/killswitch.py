"""Kill switch: halt between (and during) steps.

Triggers: trigger() call, a STOP flag file, or an optional global hotkey.
"""
from __future__ import annotations

import os
import threading
from typing import Optional


class KillSwitchTriggered(Exception):
    pass


class KillSwitch:
    def __init__(self, flag_path: Optional[str] = None):
        self.flag_path = flag_path
        self._event = threading.Event()
        self.reason: Optional[str] = None

    def trigger(self, reason: str = "manual") -> None:
        self.reason = reason
        self._event.set()

    def reset(self) -> None:
        """Resume: clears the in-memory flag and deletes the STOP file."""
        self._event.clear()
        self.reason = None
        if self.flag_path and os.path.exists(self.flag_path):
            os.remove(self.flag_path)

    def is_set(self) -> bool:
        if self._event.is_set():
            return True
        if self.flag_path and os.path.exists(self.flag_path):
            self.reason = self.reason or f"flag file {self.flag_path}"
            return True
        return False

    def check(self) -> None:
        if self.is_set():
            raise KillSwitchTriggered(self.reason or "kill switch set")

    def start_hotkey(self, combo: str = "ctrl+alt+shift+k") -> bool:
        """Register a global hotkey. Needs the optional `keyboard` package.

        Returns False if the package is unavailable. NOT covered by the test
        suite: test it on the real PC (it may need admin on some systems).
        """
        try:
            import keyboard  # type: ignore
        except ImportError:
            return False
        keyboard.add_hotkey(combo, lambda: self.trigger(f"hotkey {combo}"))
        return True
