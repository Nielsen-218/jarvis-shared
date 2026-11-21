"""Jarvis Link: the server side of the owner's Android helper app (android/jarvis-link), which controls and watches his
music player (Metrolist, com.metrolist.music) through Android's media-session API, without a tap.

The phone polls ONE server process (the laptop, over the Tailscale funnel); the PC may queue commands too (voice at
home). The person's data folder syncs between the machines with a delay (Syncthing), and Syncthing turns two machines
writing one file into conflict copies, so every file here has a single writer machine:

  fitness/link-queue-<machine>.json  commands this machine queued + "active_until" (written only by that machine)
  fitness/link-state-<machine>.json  what the phone told THIS machine: last contact, now playing, delivered command
                                     ids, results (written only by the machine the phone polls)

(<machine> is "pc" or "server", as core/thread.machine names them.) A poll reads every machine's queue and delivers each
command once; commands older than COMMAND_TTL (60 s) are never delivered (a late "next" is worse than none). Results
and delivery marks are kept RESULT_TTL (5 min).

Python API (the music picker / page / voice use these; none of them talks to the phone directly):
  send(name, **args) -> id          queue a command (validated; LinkError with a sentence when not)
  wait_result(id, timeout=6)        -> {"ok", "detail", "state"} or None when the phone did not answer in time
  state()                           -> the last now-playing report ({"playing", "title", ...,"age_s"}) or None
  connected()                       -> the phone polled or reported within CONNECTED_S (90 s)
  reachable()                       -> connected(): what the music picker asks before it sends anything
  active(seconds)                   tell the phone to poll continuously for the next N seconds (0 = stop)
  keep(seconds)                     the same, but only ever extends the window (the picker, the page, a workout)
  paired(uid)                       -> the person has a device paired as jarvis-link

Who may do what: only a device paired under the name "jarvis-link" may take commands or post state/results; any
other device of the same person (his phone browser) may read the status and download the APK. People never cross:
every file is under the person's own folder (users.acting_as)."""
import math
import re
import secrets
import time
from pathlib import Path

from core import storage
from core.config import ROOT

DEVICE = "jarvis-link"
PACKAGE = "com.metrolist.music"
APK = ROOT / "android" / "jarvis-link" / "dist" / "JarvisLink.apk"

COMMAND_TTL = 60          # s: an undelivered command expires
RESULT_TTL = 300          # s: results and delivery marks are kept this long
CONNECTED_S = 90          # s: "connected" = heard from the phone this recently
# s: the phone's pause between polls when nothing is going on. 45 s (was 300) so a workout or a music request is noticed
# within a minute even before anything marks the Link active. Battery cost: one small HTTPS GET (a few hundred bytes,
# answered at once) every 45 s while idle, about 80 an hour; the radio wakes briefly for each. The phone never long-polls
# while idle (only when active), so there is no held connection.
IDLE_SLEEP = 45
SEND_ACTIVE = 120         # s: a queued command keeps the phone polling at least this long
MAX_WAIT = 25             # s: the longest long poll
CONTACT_EVERY = 20        # s: last-contact is rewritten at most this often (it syncs to the other machine)
MAX_QUEUE = 20
MAX_TEXT = 300
MAX_MS = 24 * 3600 * 1000

COMMANDS = ("play", "pause", "toggle", "next", "previous", "seek", "like", "radio", "play_id", "play_search", "state",
            "stop_link")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clock() -> float:
    """Wall time in seconds (tests pin it)."""
    return time.time()


def machine() -> str:
    from core import thread
    return thread.machine()


class LinkError(ValueError):
    """A command or report that was refused; the message is a sentence for the person."""


# ---- validation -------------------------------------------------------------------------------------------------------
def _text(value, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    value = _CONTROL.sub(" ", value).strip()
    return value[:limit] or None


def _ms(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or value > MAX_MS:
        return None
    return int(value)


def _ts(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if 0 < value < 1e13 else None


def clean_state(body) -> dict | None:
    """A now-playing report from the phone, bounded: wrong types become None, never an error."""
    if not isinstance(body, dict):
        return None
    liked = body.get("liked")
    return {"playing": body.get("playing") is True,
            "title": _text(body.get("title")), "artist": _text(body.get("artist")), "album": _text(body.get("album")),
            "duration_ms": _ms(body.get("duration_ms")), "position_ms": _ms(body.get("position_ms")),
            "media_id": _text(body.get("media_id"), 200), "package": _text(body.get("package"), 100),
            "liked": liked if isinstance(liked, bool) else None,
            "session": body.get("session") is not False,       # False = Metrolist has no active session
            "ts": _ts(body.get("ts"))}


def _args(name: str, args: dict) -> dict:
    if name not in COMMANDS:
        raise LinkError(f"Jarvis Link has no command called {str(name)[:40]!r}.")
    if name == "seek":
        pos = _ms(args.get("position_ms"))
        if pos is None:
            raise LinkError("Seek needs a position in milliseconds (0 to 24 hours).")
        extra, out = set(args) - {"position_ms"}, {"position_ms": pos}
    elif name == "play_id":
        vid = args.get("video_id")
        if not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid):
            raise LinkError("That is not a YouTube video id (11 letters, digits, - or _).")
        for key in ("title", "artist"):
            if args.get(key) is not None and not isinstance(args.get(key), str):
                raise LinkError(f"The song's {key} must be text.")
        out = {"video_id": vid, "title": _text(args.get("title"), 200) or "", "artist": _text(args.get("artist"), 200) or ""}
        extra = set(args) - {"video_id", "title", "artist"}
    elif name == "play_search":
        query = _text(args.get("query"), 200)
        if not query:
            raise LinkError("Play-by-search needs some words to search for.")
        extra, out = set(args) - {"query"}, {"query": query}
    else:
        extra, out = set(args), {}
    if extra:
        raise LinkError(f"{name} does not take {', '.join(sorted(extra))}.")
    return out


# ---- files ------------------------------------------------------------------------------------------------------------
def _queue_name(m: str) -> str:
    return f"fitness/link-queue-{m}.json"


def _state_name(m: str) -> str:
    return f"fitness/link-state-{m}.json"


def _machines() -> tuple[str, ...]:
    return ("pc", "server")


def _load(name: str) -> dict:
    data = storage.load(name, {})
    return data if isinstance(data, dict) else {}


def _own_queue() -> dict:
    q = _load(_queue_name(machine()))
    q["commands"] = [c for c in q.get("commands", []) if isinstance(c, dict) and clock() - c.get("ts", 0) < RESULT_TTL]
    return q


def _own_state() -> dict:
    s = _load(_state_name(machine()))
    now = clock()
    s["delivered"] = {k: t for k, t in (s.get("delivered") or {}).items() if now - t < RESULT_TTL}
    s["results"] = {k: r for k, r in (s.get("results") or {}).items() if isinstance(r, dict) and now - r.get("t", 0) < RESULT_TTL}
    return s


# ---- the Python API ---------------------------------------------------------------------------------------------------
def send(name: str, **args) -> str:
    """Queue one command for the phone -> its id. Raises LinkError (a sentence) when the command or an argument is wrong."""
    clean = _args(name, args)
    now = clock()
    q = _own_queue()
    cid = secrets.token_hex(6)
    q["commands"] = (q["commands"] + [{"id": cid, "name": name, "args": clean, "ts": now, "origin": machine()}])[-MAX_QUEUE:]
    q["active_until"] = max(float(q.get("active_until") or 0), now + SEND_ACTIVE)
    storage.save(_queue_name(machine()), q)
    return cid


def result(cid: str) -> dict | None:
    for m in _machines():
        r = (_load(_state_name(m)).get("results") or {}).get(cid)
        if isinstance(r, dict):
            return {"ok": bool(r.get("ok")), "detail": r.get("detail") or "", "state": r.get("state")}
    return None


def wait_result(cid: str, timeout: float = 6) -> dict | None:
    """Block (a worker thread, never the event loop) until the phone reports on command cid, or timeout -> None."""
    end = time.monotonic() + max(0.0, min(float(timeout), 60.0))
    while True:
        r = result(cid)
        if r is not None or time.monotonic() >= end:
            return r
        time.sleep(0.25)


def _freshest() -> dict:
    best = {}
    for m in _machines():
        s = _load(_state_name(m))
        if float(s.get("contact") or 0) > float(best.get("contact") or 0):
            best = s
    return best


def state() -> dict | None:
    """The last now-playing report from the phone (any machine it reported to), with its age in seconds; None if none."""
    s = _freshest()
    st = s.get("state")
    if not isinstance(st, dict):
        return None
    return {**st, "age_s": max(0, round(clock() - float(s.get("state_at") or 0)))}


def last_contact() -> float | None:
    c = _freshest().get("contact")
    return float(c) if c else None


def connected() -> bool:
    c = last_contact()
    return c is not None and clock() - c < CONNECTED_S


def reachable() -> bool:
    """May Jarvis count on the phone right now? It polled or reported within CONNECTED_S (90 s): twice the idle sleep, so
    an idle phone that is fine stays reachable."""
    return connected()


def paired(uid: str | None = None) -> bool:
    """Has this person ever paired the Jarvis Link app (a device named jarvis-link)?"""
    from core import users
    try:
        return any(is_link_device(d) for d in users.get(uid or users.current()).get("devices", []))
    except Exception:                                        # noqa: BLE001 - an unknown person has no Link
        return False


def keep(seconds: float) -> bool:
    """Like active(), but only ever EXTENDS the active window (never shortens one another feature asked for), and writes
    the queue file only when the new end is at least 30 s later than the stored one (the file syncs between machines).
    -> True when it wrote."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(seconds) or seconds <= 0:
        return False
    q = _own_queue()
    until = clock() + min(seconds, 4 * 3600)
    if until - float(q.get("active_until") or 0) < 30:
        return False
    q["active_until"] = until
    storage.save(_queue_name(machine()), q)
    return True


def active(seconds: float) -> None:
    """Keep the phone polling continuously for the next `seconds` (a workout opened, music requested); 0 stops it."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        raise LinkError("active() needs a number of seconds.") from None
    if not math.isfinite(seconds):
        raise LinkError("active() needs a finite number of seconds.")
    q = _own_queue()
    now = clock()
    q["active_until"] = now + min(max(seconds, 0.0), 4 * 3600)
    storage.save(_queue_name(machine()), q)


def is_active() -> bool:
    now = clock()
    return any(float(_load(_queue_name(m)).get("active_until") or 0) > now for m in _machines())


# ---- the phone's side (the routes in core/appserver call these, acting as the person) --------------------------------
def _touch(s: dict, force: bool = False) -> bool:
    now = clock()
    if force or now - float(s.get("contact") or 0) >= CONTACT_EVERY:
        s["contact"] = now
        return True
    return False


def take() -> dict:
    """One poll: every undelivered, unexpired command (marked delivered here) and the sleep hint for the next poll."""
    now = clock()
    s = _own_state()
    delivered = set(s["delivered"])
    for m in _machines():
        if m != machine():
            delivered |= set((_load(_state_name(m)).get("delivered") or {}))
    out = []
    for m in _machines():
        for c in _load(_queue_name(m)).get("commands", []):
            if not isinstance(c, dict) or c.get("id") in delivered or c.get("name") not in COMMANDS:
                continue
            age = now - float(c.get("ts") or 0)
            if 0 <= age < COMMAND_TTL or -5 < age < 0:            # a few seconds of clock skew between the machines
                out.append({"id": c["id"], "name": c["name"], "args": c.get("args") or {}, "ts": c["ts"]})
    out.sort(key=lambda c: c["ts"])
    for c in out:
        s["delivered"][c["id"]] = now
    changed = _touch(s) or bool(out)
    if changed:
        storage.save(_state_name(machine()), s)
    return {"commands": out, "sleep": 0 if (out or is_active()) else IDLE_SLEEP, "now": now}


def report_state(body) -> dict:
    st = clean_state(body)
    if st is None:
        raise LinkError("A state report must be a JSON object.")
    s = _own_state()
    s["state"], s["state_at"] = st, clock()
    _touch(s, force=True)
    storage.save(_state_name(machine()), s)
    return {"ok": True, "sleep": 0 if is_active() else IDLE_SLEEP}


def report_result(body) -> dict:
    if not isinstance(body, dict):
        raise LinkError("A result must be a JSON object.")
    cid = body.get("id")
    s = _own_state()
    if not isinstance(cid, str) or cid not in s["delivered"]:
        raise LinkError("Jarvis did not send a command with that id (or it is too old).")
    st = clean_state(body.get("state")) if body.get("state") is not None else None
    s["results"][cid] = {"ok": body.get("ok") is True, "detail": _text(body.get("detail"), 500) or "", "state": st,
                         "t": clock()}
    if st is not None:
        s["state"], s["state_at"] = st, clock()
    _touch(s, force=True)
    storage.save(_state_name(machine()), s)
    return {"ok": True}


def status(uid: str) -> dict:
    """The page's view: is the app paired, is it connected, what is playing, the last command's result."""
    from core import users

    paired = any(d.get("name") == DEVICE for d in users.get(uid).get("devices", []))
    last = None
    for m in _machines():
        for cid, r in (_load(_state_name(m)).get("results") or {}).items():
            if isinstance(r, dict) and (last is None or r.get("t", 0) > last["t"]):
                last = {"id": cid, "ok": bool(r.get("ok")), "detail": r.get("detail") or "", "t": r.get("t", 0)}
    return {"paired": paired, "connected": connected(), "last_contact": last_contact(), "state": state(),
            "last_result": last, "apk": APK.exists()}


def is_link_device(device: dict) -> bool:
    return isinstance(device, dict) and device.get("name") == DEVICE


def apk_path() -> Path | None:
    return APK if APK.is_file() else None
