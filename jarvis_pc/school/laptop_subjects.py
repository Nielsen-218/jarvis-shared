"""School subjects for cross-device control (blueprint area 16): ask the owner's Lenovo laptop (which also runs Jarvis) a read-only question.

The gate lets `ssh -i <key> -o BatchMode=yes jarvis@jarvis-laptop <one fixed read-only command>` run without asking (see
guardrails/tiers.py `_laptop_read`). The harness asks the laptop the same question itself to get the ground truth, so the lesson is
only available while the laptop answers; with the laptop off there is no truth and the lesson simply cannot be passed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Optional

from ..guardrails.tiers import LAPTOP_HOST
from .curriculum import Absent, Context, HIGH_SCHOOL, Num, Subject, Task

KEY = os.path.join(os.path.expanduser("~"), ".ssh", "jarvis_laptop")


def _ask_laptop(remote: str) -> Optional[str]:
    if not shutil.which("ssh") or not os.path.isfile(KEY):
        return None
    try:
        r = subprocess.run(["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", LAPTOP_HOST, remote],
                           capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


PARAMS = {"key": KEY, "host": LAPTOP_HOST}


def _hostname(c: Context) -> Task:
    ask = "what is the name of the laptop {host} (login key {key})"
    out = _ask_laptop("hostname")
    return Task(ask, dict(PARAMS), [out] if out else [], None, 0)


def _service(c: Context) -> Task:
    ask = "is the jarvis service running on the laptop {host} (login key {key})"
    out = _ask_laptop("systemctl is-active jarvis.service")
    truths = (["active", Absent(("inactive", "failed"))] if out == "active" else [out]) if out else []
    return Task(ask, dict(PARAMS), truths, None, 0)


def _disk(c: Context) -> Task:
    ask = "what percent of the disk is used on the laptop {host} (login key {key})"
    out = _ask_laptop("df -h /")
    pct = None
    if out:
        for tok in out.split():
            if tok.endswith("%") and tok[:-1].isdigit():
                pct = int(tok[:-1])
    return Task(ask, dict(PARAMS), [Num(pct, 0.0)] if pct is not None else [], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("laptop_hostname", "read the laptop's name over ssh", hs, _hostname, ("laptop name", "other device name"), needs_windows=True),
        Subject("laptop_service_state", "check a service on the laptop", hs, _service, ("laptop service", "is jarvis up on the laptop"), needs_windows=True),
        Subject("laptop_disk_used", "read the laptop's disk usage", hs, _disk, ("laptop disk", "other device storage"), needs_windows=True),
    ]
