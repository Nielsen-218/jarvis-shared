"""When is it worth interrupting? ONE judge on the PC for everything Jarvis says without being asked.

Before (2026-11-08): reminders, price drops and the laptop's announcements each called say() themselves: Jarvis talked to an
empty room, talked over a game, and never told the owner what happened while he was out. Now every unprompted sentence is
an Event handed to deliver(), which decides from the owner's situation:

  * he is at the PC (keyboard, mouse or controller in the last 5 min), no game -> say it now (at most RATE per 10 min)
  * a game is running                                                         -> hold it, unless it is CRITICAL
  * he is away                                                                -> hold it (the phone already got the push)
Held events are said as ONE short "while you were away" summary when he is back (and the game is over). Old or repeated
events are dropped. Nothing here decides WHAT is worth knowing; the jobs that create events do. This only decides WHEN.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

CRITICAL, HIGH, NORMAL, LOW = "critical", "high", "normal", "low"
PRESENT_SECONDS = 5 * 60
RATE, RATE_WINDOW = 3, 10 * 60
HOLD_HOURS = 12
MAX_DIGEST = 4

_lock = threading.Lock()
_held: list = []                 # {"text", "urgency", "key", "at"}
_spoken: list = []               # times of recent unprompted sentences
_voice: Optional[Callable[[str], None]] = None


def set_voice(say: Callable[[str], None]) -> None:
    global _voice
    _voice = say


def _say(text: str) -> None:
    try:   # core/notice: his next reply answers THIS line now, not an offer said before it
        from core import notice
        notice.unprompted_said(text)
    except Exception:  # noqa: BLE001
        pass
    if _voice is not None:
        _voice(text)
        return
    from tools import system
    speak = getattr(system, "speak", None)
    if speak is not None:
        speak(text)


def situation() -> dict:
    """What the owner is doing right now, as far as interrupting goes."""
    out = {"present": False, "gaming": "", "idle": 0.0}
    try:
        from core.scheduler import idle_seconds
        out["idle"] = idle_seconds()
        out["present"] = out["idle"] < PRESENT_SECONDS
    except Exception:  # noqa: BLE001
        pass
    try:
        from tools import gaming
        out["gaming"] = gaming.state.get("game") or ""
    except Exception:  # noqa: BLE001
        pass
    if not out["gaming"]:
        try:
            from core import acquire
            out["gaming"] = acquire.running_game()
        except Exception:  # noqa: BLE001
            pass
    return out


def decide(urgency: str, sit: dict, recent: int) -> str:
    """'say' | 'hold' for one event."""
    if sit.get("gaming"):
        return "say" if urgency == CRITICAL else "hold"
    if not sit.get("present"):
        return "hold"
    if urgency == LOW:
        return "hold"                                    # low news waits for the next summary
    if recent >= RATE and urgency != CRITICAL:
        return "hold"
    return "say"


def deliver(text: str, urgency: str = NORMAL, key: str = "", sit: Optional[dict] = None) -> str:
    """Say it now or hold it for when the owner is back. -> 'said' | 'held' | 'duplicate'."""
    text = (text or "").strip()
    if not text:
        return "duplicate"
    key = key or text.casefold()
    now = time.time()
    sit = situation() if sit is None else sit
    with _lock:
        _spoken[:] = [t for t in _spoken if now - t < RATE_WINDOW]
        if any(h["key"] == key for h in _held):
            return "duplicate"
        verdict = decide(urgency, sit, len(_spoken))
        if verdict == "hold":
            _held.append({"text": text, "urgency": urgency, "key": key, "at": now})
            return "held"
        _spoken.append(now)
    _say(text)
    return "said"


def withdraw(key: str) -> bool:
    """A condition that recovered (core/notice): its held sentence is no longer news. -> whether one was held."""
    with _lock:
        before = len(_held)
        _held[:] = [h for h in _held if h["key"] != key]
        return len(_held) != before


def held() -> list:
    now = time.time()
    with _lock:
        _held[:] = [h for h in _held if now - h["at"] < HOLD_HOURS * 3600]
        return list(_held)


def digest(items: list) -> str:
    """'While you were away: A. B. And 2 more things, ask me for them.'"""
    order = {CRITICAL: 0, HIGH: 1, NORMAL: 2, LOW: 3}
    items = sorted(items, key=lambda h: (order.get(h["urgency"], 2), h["at"]))
    said = [h["text"].rstrip(".") + "." for h in items[:MAX_DIGEST]]
    more = len(items) - len(said)
    tail = f" And {more} more, ask me what I held for you." if more > 0 else ""
    return "While you were away: " + " ".join(said) + tail


INBOX = "news-inbox.jsonl"     # data/learning/: other processes (the practice watcher) leave news here


def post(text: str, urgency: str = LOW, key: str = "") -> None:
    """Leave news for the owner from ANY process (it is judged when Jarvis's PC process reads the inbox)."""
    import json
    from core import storage
    path = storage.DATA / "learning" / INBOX
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"text": text, "urgency": urgency, "key": key or text.casefold(), "at": time.time()},
                           ensure_ascii=False) + "\n")


def read_inbox() -> int:
    """Take the inbox's news into the held list (they are said in the next summary). -> how many."""
    import json
    from core import storage
    path = storage.DATA / "learning" / INBOX
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        path.unlink()
    except (OSError, ValueError):
        return 0
    now = time.time()
    with _lock:
        for r in rows:
            fresh = now - float(r.get("at") or now) < HOLD_HOURS * 3600
            if r.get("text") and fresh and not any(h["key"] == r.get("key") for h in _held):
                _held.append({"text": str(r["text"])[:300], "urgency": r.get("urgency", LOW),
                              "key": r.get("key") or r["text"], "at": float(r.get("at") or now)})
    return len(rows)


_PREFIX = {"away": "While you were away: ", "game": "Now that your game is over: ", "later": "Also, "}


def welcome_back(sit: Optional[dict] = None, reason: str = "away") -> str:
    """Called when the owner is back, a game ended, or the rate limit allows again: say the held events as one short
    summary. -> what was said."""
    sit = situation() if sit is None else sit
    if sit.get("gaming") or not sit.get("present"):
        return ""
    items = held()
    if not items:
        return ""
    with _lock:
        keys = {h["key"] for h in items}
        _held[:] = [h for h in _held if h["key"] not in keys]
        _spoken.append(time.time())
    text = _PREFIX.get(reason, _PREFIX["away"]) + digest(items).removeprefix("While you were away: ")
    _say(text)
    return text


def recall() -> str:
    """'What did you hold for me?' — everything still held, without clearing it."""
    items = held()
    return digest(items).replace("While you were away: ", "Held for you: ") if items else "Nothing is waiting for you."
