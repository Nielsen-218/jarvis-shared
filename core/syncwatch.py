"""Keep Syncthing running on the PC: it carries data/ between the PC and the laptop (the phone's journal, the thread
ledger, the Daily Awareness log, memories). Without it the phone and the PC each remember only their own half.

Root cause found 2026-11-11 (plan upgrade 10, "the phone saved nothing since Nov 6"): the laptop HAD journaled every
phone conversation (journal-server.json there: 13 new entries from 2026-11-10 to 2026-11-11, one per conversation, and
no phone turns at all from 2026-11-08 02:24 to 2026-11-10 06:21), but none of it reached the PC. The PC's Syncthing runs
from the scheduled task "Syncthing" (at logon, inside a PowerShell console). On 2026-11-09 at 18:06 a voice request to
close PowerShell, confirmed with a yes, closed that console: the task's last result is 0xC000013A (its console was
closed) and the laptop logged the PC's connection closed by the remote side at 18:06:30. A logon-only task never starts
it again, so nothing either machine wrote reached the other for two days.

So every WATCH_S, on the PC, when no Syncthing process runs and the task exists, the task is started once more: as the
owner set it up (his user, his session, his arguments). Never while a game runs (its first scan reads the whole folder).
JARVIS_SYNC_WATCH=off turns it off.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

TASK = "Syncthing"
FIRST_S = 90.0        # the first look, once the start-up work is done
WATCH_S = 600.0       # then every ten minutes
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_said: set[str] = set()     # outcomes already logged (a missing task is said once, not every ten minutes)


def enabled() -> bool:
    from core import pcbridge
    return (sys.platform == "win32" and pcbridge.local_pc()
            and os.getenv("JARVIS_SYNC_WATCH", "off").strip().lower() not in ("off", "0", "false", "no"))


def running() -> bool:
    import psutil
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() in ("syncthing.exe", "syncthing"):
            return True
    return False


def _gaming() -> bool:
    try:
        from core import warmth
        return bool(warmth.gaming())
    except Exception:  # noqa: BLE001  (no game probe: nothing to wait for)
        return False


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW)


def _say_once(outcome: str, line: str) -> None:
    if outcome not in _said:
        _said.add(outcome)
        print(line)


def check() -> str:
    """One look. 'off' | 'running' | 'gaming' (wait for the game to end) | 'no-task' | 'started' | 'failed'."""
    if not enabled():
        return "off"
    if running():
        _said.discard("started")
        return "running"
    if _gaming():
        return "gaming"
    if _schtasks("/Query", "/TN", TASK).returncode != 0:
        _say_once("no-task", f"[sync] Syncthing is not running and there is no scheduled task '{TASK}' to start it: the "
                             "phone's journal and thread cannot reach this PC")
        return "no-task"
    started = _schtasks("/Run", "/TN", TASK)
    if started.returncode != 0:
        print(f"[sync] Syncthing is not running and its task would not start: {(started.stderr or started.stdout).strip()[:200]}")
        return "failed"
    print(f"[sync] Syncthing was not running: started its scheduled task '{TASK}' again (the phone's journal and thread "
          "reach this PC through it)")
    return "started"


def start() -> None:
    """The watch, in the background (main.py on the PC)."""
    if not enabled():
        return

    def watch() -> None:
        time.sleep(FIRST_S)
        while True:
            try:
                check()
            except Exception as error:  # noqa: BLE001 - one failed look never ends the watch
                print(f"[sync] {type(error).__name__}: {error}")
            time.sleep(WATCH_S)
    threading.Thread(target=watch, daemon=True, name="sync-watch").start()
