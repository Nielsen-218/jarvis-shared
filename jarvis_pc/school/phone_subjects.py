"""School subjects for phone and cross-device control (blueprint area 16, the phone half): the owner's phone is paired with Jarvis.

Two lessons, both read-only and both graded on the real state of this PC:
  * phone_paired_count: how many paired devices are phones. The harness copies the real pairing record into the sandbox with every secret
    removed (no push endpoints, no token hashes, no user names: only device names, ids and dates), so a teacher model never sees a credential.
  * phone_server_up: whether the phone server on this PC answers (the PWA the phone talks to). The gate already allows a plain GET of a page
    on this PC.
These prove Jarvis can see which phones are linked and that the link's server is alive. They do not control the phone.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import List, Optional

from .curriculum import Context, HIGH_SCHOOL, Num, Subject, Task

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
USERS = os.path.join(ROOT, "data", "users.json")
URL = "http://127.0.0.1:8765/"


def _devices() -> Optional[List[dict]]:
    try:
        with open(USERS, encoding="utf-8") as f:
            users = json.load(f)["users"]
    except (OSError, ValueError, KeyError):
        return None
    rows = users.values() if isinstance(users, dict) else users
    return [d for u in rows for d in (u.get("devices") or []) if isinstance(d, dict)]


def _paired_count(c: Context) -> Task:
    ask = "how many of the devices in the pairing record {file} are phones"
    devs = _devices()
    if not devs:
        return Task(ask, {"file": ""}, [], None, 0)
    safe = {"devices": [{"id": d.get("id", ""), "name": d.get("name", ""), "added": d.get("added", "")} for d in devs]}
    path = c.write(os.path.join(c.folder(c.word(6)), "paired_devices.json"), json.dumps(safe, indent=1))
    phones = sum(1 for d in devs if "phone" in str(d.get("name", "")).lower())
    return Task(ask, {"file": path}, [Num(phones, 0.0)] if phones else [], None, 0)


def _server_up(c: Context) -> Task:
    ask = "what HTTP status code does the Jarvis phone server answer with at {url}"
    try:
        with urllib.request.urlopen(URL, timeout=5) as r:
            code = r.status
    except Exception:  # noqa: BLE001
        return Task(ask, {"url": URL}, [], None, 0)
    return Task(ask, {"url": URL}, [str(code)], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("phone_paired_count", "count the phones paired with Jarvis", hs, _paired_count, ("paired phones", "linked devices"), needs=("file_read",), needs_windows=True),
        Subject("phone_server_up", "check the phone server answers", hs, _server_up, ("phone server", "pwa up"), needs=("web_local_heading",), needs_windows=True),
    ]
