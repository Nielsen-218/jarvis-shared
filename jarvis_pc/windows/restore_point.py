"""Windows restore points for the permission gate's pre_change hook.

Creating a restore point needs admin. JARVIS may run elevated or not, so the
one-time setup script (setup_restore_task.ps1, run by YOU as admin) registers
a scheduled task that runs a fixed restore-point script with highest
privileges under your own account. JARVIS only triggers that task; it never
needs to be elevated itself and cannot change what the task runs.

NOT TESTED ON WINDOWS. The wait/parse logic is unit-tested; the schtasks call
and the PowerShell script need a first run on the real PC.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Callable, Optional

TASK_NAME = "JarvisRestorePoint"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def default_result_path() -> str:
    return os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Jarvis", "restore_result.json")


def wait_for_result(result_path: str, since: datetime, timeout: float = 180.0, poll: float = 1.0,
                    sleep: Callable[[float], None] = time.sleep) -> None:
    """Block until the task writes a result newer than `since`. Raises on failure/timeout."""
    since = since.replace(microsecond=0)
    deadline = time.monotonic() + timeout
    while True:
        try:
            with open(result_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            finished = datetime.strptime(data["finished_utc"], _TS_FORMAT).replace(tzinfo=timezone.utc)
            if finished >= since:
                if data.get("ok"):
                    return
                raise RuntimeError(f"restore point failed: {data.get('error') or 'unknown error'}")
        except (OSError, ValueError, KeyError):
            pass  # not written yet or half-written; keep polling
        if time.monotonic() >= deadline:
            raise TimeoutError("restore point did not finish in time")
        sleep(poll)


def _trigger_task(task: str) -> None:
    r = subprocess.run(["schtasks", "/run", "/tn", task], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"could not start task {task}: {(r.stderr or r.stdout).strip()}. "
                           "Has setup_restore_task.ps1 been run?")


def make_pre_change(timeout: float = 180.0, min_interval: float = 600.0, task: str = TASK_NAME,
                    result_path: Optional[str] = None,
                    trigger: Callable[[str], None] = _trigger_task):
    """Return a pre_change hook for Guardrails, or None when not on Windows.

    min_interval: skip creating another restore point if one succeeded this
    many seconds ago, so a burst of small changes doesn't take minutes.
    """
    if os.name != "nt":
        return None
    path = result_path or default_result_path()
    last_ok = [0.0]

    def hook(_request) -> None:
        if last_ok[0] and time.monotonic() - last_ok[0] < min_interval:
            return
        started = datetime.now(timezone.utc)
        trigger(task)
        wait_for_result(path, started, timeout)
        last_ok[0] = time.monotonic()

    return hook
