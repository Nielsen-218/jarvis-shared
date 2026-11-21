"""Notifications: a desktop pop-up (through the tray icon), the PC companion's bubble, and other channels such as
phone push, which deliver to the right person.

Each notification has a category the person can switch off in the phone app, and quiet hours hold back the
non-urgent ones overnight. Everything sent is kept in a short history (the bell on the phone, the dashboard).
On the PC (JARVIS_PUSH=off) phone delivery goes through the always-on laptop, so a timer finishing or a badge
earned at the desk still reaches the phone.
"""
import json
import os
import threading
import time
import uuid
from core import diag
from pathlib import Path
from datetime import datetime
from typing import Callable

tray_icon = None  # pystray.Icon, set by core.tray
_channels: list[Callable[[str, str, str | None], None]] = []

CATEGORIES = {   # id: (label, what it covers, urgent by default)
    "reminders": ("Reminders & timers", "Your reminders, household reminders and timers finishing", True),
    "alerts": ("Alerts", "Homelab problems, a broken Jarvis link, price drops", False),
    "training": ("Training", "Morning check-in, an unlogged workout in the evening, new badges", False),
    "weekly": ("Weekly report", "Your week in numbers, Sunday evening", False),
    # 2026-11-10 Mark VI C3: mission reports and "needs your OK" get their own switch in the phone's settings
    "missions": ("Missions", "Jobs you handed to Jarvis: done, blocked, failed, or waiting for your OK", False),
    # 2026-11-16 Storm Watch (core/stormwatch): an urgent (DANGER) one goes through quiet hours whatever this switch's default
    "weather": ("Storm watch", "Hurricanes, strong earthquakes and severe weather alerts near your pinned places", False),
    # 2026-11-18 house cameras (core/homecam): people, pets, cars and sounds, only when he turns camera alerts on
    "house": ("House cameras", "People, pets, cars and sounds the home cameras catch (when camera alerts are on)", False),
    "jarvis": ("Jarvis", "Everything else Jarvis wants you to know", False),
}
PREFS = "notify_prefs.json"
DEFAULT_PREFS = {"categories": {k: True for k in CATEGORIES}, "quiet": {"on": True, "start": "22:00", "end": "07:00"}}
HISTORY_MAX = 100


def add_channel(fn: Callable[[str, str, str | None], None]) -> None:
    """fn(title, message, uid): uid is the person it's for, or None for everyone."""
    _channels.append(fn)


def notify(title: str, message: str, uid: str | None = None, category: str = "jarvis", urgent: bool | None = None,
           extra: dict | None = None) -> None:
    """extra (2026-11-10 Mark VI C3): {url, tag, mission, kind} for the phone push - a mission's own tag and a link that opens
    its sheet. Only channels marked wants_extra get it, so older channels keep working unchanged."""
    category = category if category in CATEGORIES else "jarvis"
    urgent = CATEGORIES[category][2] if urgent is None else urgent
    if tray_icon is not None:
        try:
            tray_icon.notify(message[:250], title[:60])      # Windows rejects longer balloon text (seen live: 'string too long (400, maximum length 256)')
        except Exception as error:
            diag.swallowed("tray notify", error)
    try:
        from core import hud
        hud._send({"type": "notify", "title": title, "body": message, "category": category})
    except Exception as error:
        diag.swallowed("hud notify", error)
    for fn in _channels:
        try:
            if extra and getattr(fn, "wants_extra", False):
                fn(title, message, uid, category, urgent, extra=extra)
            elif getattr(fn, "wants_category", False):
                fn(title, message, uid, category, urgent)
            else:
                fn(title, message, uid)
        except Exception as e:
            print(f"[notify] {e}")


# -- preferences ------------------------------------------------------------------------------------
def prefs(uid: str) -> dict:
    from core import storage, users
    with users.acting_as(uid):
        p = storage.load(PREFS, {})
    out = {"categories": {**DEFAULT_PREFS["categories"], **(p.get("categories") or {})},
           "quiet": {**DEFAULT_PREFS["quiet"], **(p.get("quiet") or {})}}
    return out


def set_prefs(uid: str, categories: dict | None = None, quiet: dict | None = None) -> dict:
    import re

    from core import storage, users
    p = prefs(uid)
    for k, v in (categories or {}).items():
        if k in CATEGORIES:
            p["categories"][k] = bool(v)
    if quiet:
        if "on" in quiet:
            p["quiet"]["on"] = bool(quiet["on"])
        for k in ("start", "end"):
            if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(quiet.get(k, ""))):
                p["quiet"][k] = quiet[k]
    with users.acting_as(uid):
        storage.save(PREFS, p)
    return p


def in_quiet_hours(p: dict, now: datetime | None = None) -> bool:
    q = p["quiet"]
    if not q.get("on"):
        return False
    from core import ownertime
    hm = (now or ownertime.now()).strftime("%H:%M")        # the OWNER's clock: the laptop may run in UTC
    start, end = q["start"], q["end"]
    return (start <= hm < end) if start < end else (hm >= start or hm < end)


def allowed(uid: str, category: str, urgent: bool, now: datetime | None = None) -> bool:
    """Should this reach that person's phone right now?"""
    p = prefs(uid)
    if not p["categories"].get(category, True):
        return False
    return urgent or not in_quiet_hours(p, now)


# -- history ----------------------------------------------------------------------------------------
def _history_file() -> str:
    from core import pcbridge
    return "notifications-pc.json" if pcbridge.local_pc() else "notifications-server.json"   # one writer per host


def record(title: str, body: str, uid: str | None, category: str, delivered: bool) -> str:
    from core import storage, users
    targets = [uid] if uid else list(users.all_users())
    row = {"id": uuid.uuid4().hex[:8], "t": time.time(), "title": title[:120], "body": body[:600],
           "category": category, "pushed": delivered}
    for u in targets:
        with users.acting_as(u):
            rows = storage.load(_history_file(), [])
            rows.append(row)
            storage.save(_history_file(), rows[-HISTORY_MAX:])
    return row["id"]


def history(uid: str, limit: int = 50) -> list[dict]:
    from core import storage, users
    with users.acting_as(uid):
        rows = storage.load("notifications-pc.json", []) + storage.load("notifications-server.json", [])
    seen, out = set(), []
    # newest first; two sent in the same instant keep their order (reverse insertion, then a stable sort)
    for r in sorted(rows[::-1], key=lambda r: r.get("t", 0), reverse=True):
        if r.get("id") not in seen:
            seen.add(r.get("id"))
            out.append(r)
    return out[:limit]


# -- the PC hands phone delivery to the laptop ------------------------------------------------------
# -- the PC's outbox (2026-11-13, cross-device audit D) ----------------------------------------------------------------
# A notification raised on the PC while the laptop is rebooting or off the tailnet used to be written to the PC's own
# history and never reach the phone. It now also waits in a small per-machine outbox (machine-state, NOT the synced data
# folder: each machine's queue is its own) and is sent on the next successful contact with the laptop. One hour, then it
# is dropped (a stale "your tea timer is done" is noise); at most OUTBOX_MAX waiting. An entry is taken out of the file
# BEFORE it is sent and put back only if the send failed, so it is never delivered twice.
OUTBOX_TTL = 3600.0
OUTBOX_MAX = 20
_outbox_lock = threading.RLock()


def outbox_path() -> Path:
    from core.config import ROOT
    return Path(os.getenv("JARVIS_OUTBOX_DIR", str(ROOT / "machine-state" / "outbox"))) / "notify-outbox.json"


def outbox() -> list[dict]:
    """What is waiting: [{"id", "t", "row", "body"}] oldest first; anything past the expiry is not returned."""
    try:
        rows = json.loads(outbox_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    now = time.time()
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("body"), dict)
            and isinstance(r.get("t"), (int, float)) and now - r["t"] <= OUTBOX_TTL][-OUTBOX_MAX:]


def _save_outbox(rows: list[dict]) -> None:
    path = outbox_path()
    try:
        if not rows:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as error:
        diag.swallowed("notify outbox", error)


def enqueue(body: dict, row: str = "") -> None:
    with _outbox_lock:
        _save_outbox((outbox() + [{"id": uuid.uuid4().hex[:8], "t": time.time(), "row": row, "body": body}])[-OUTBOX_MAX:])


def flush_outbox() -> int:
    """Send what is waiting, oldest first; stop at the first failure and keep it and everything after it."""
    from tools import vault
    sent = 0
    with _outbox_lock:
        while True:
            rows = outbox()
            if not rows or not vault._remote():
                _save_outbox(rows)                                   # also drops the expired
                return sent
            first, rest = rows[0], rows[1:]
            _save_outbox(rest)                                       # out of the file before it goes: never twice
            try:
                vault._call("POST", "/api/notify", first["body"])
            except Exception as error:  # noqa: BLE001
                _save_outbox([first] + rest)
                diag.swallowed("notify outbox flush", error, every=600)
                return sent
            sent += 1
            _forget_local(first)


def _forget_local(entry: dict) -> None:
    """The laptop now holds the notification in its own history: drop the PC's 'not pushed' copy so the bell shows it once."""
    row = entry.get("row")
    if not row:
        return
    try:
        from core import storage, users
        uid = (entry.get("body") or {}).get("uid")
        for u in ([uid] if uid else list(users.all_users())):
            with users.acting_as(u):
                rows = storage.load(_history_file(), [])
                kept = [r for r in rows if r.get("id") != row]
                if len(kept) != len(rows):
                    storage.save(_history_file(), kept)
    except Exception as error:  # noqa: BLE001
        diag.swallowed("notify outbox history", error)


def flush_outbox_soon() -> None:
    """After a successful call to the laptop (tools.vault._call): empty the outbox in the background, never in the caller."""
    if outbox_path().exists():
        threading.Thread(target=flush_outbox, daemon=True, name="notify-outbox").start()


def _worth_retrying(error: Exception) -> bool:
    import urllib.error
    code = getattr(error, "code", None)
    return not (isinstance(error, urllib.error.HTTPError) and isinstance(code, int) and 400 <= code < 500 and code not in (408, 429))


def forward_to_server(title: str, message: str, uid: str | None, category: str = "jarvis", urgent: bool = False,
                      extra: dict | None = None) -> None:
    """On the PC (no phone push of its own): ask the laptop to deliver and record it. Falls back to a local
    history entry when the laptop can't be reached, so the dashboard still shows it - and keeps it in the outbox to send
    when the laptop answers again."""
    from tools import vault
    body = {"title": title, "body": message, "uid": uid, "category": category, "urgent": urgent}
    if extra:
        body["extra"] = extra          # 2026-11-10 C3: the laptop re-checks it (whitelisted keys, mission urls only)
    try:
        if not vault._remote():
            raise RuntimeError("no server")
        vault._call("POST", "/api/notify", body)
    except Exception as e:
        print(f"[notify] laptop unreachable, kept locally: {type(e).__name__}")
        row = record(title, message, uid, category, False)
        if vault._remote() and _worth_retrying(e):
            enqueue(body, row)
        return
    flush_outbox_soon()


forward_to_server.wants_category = True
forward_to_server.wants_extra = True
