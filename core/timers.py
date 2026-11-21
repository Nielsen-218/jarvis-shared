"""Running timers, kept in one place so they can be shown (PC companion, dashboard), listed and cancelled by name.

set_timer used to start a bare threading.Timer that nothing could see or stop."""
import threading
import time
import uuid

_lock = threading.Lock()
_timers: dict[str, dict] = {}   # id -> {"id", "label", "seconds", "ends", "_t": threading.Timer}


def _publish() -> None:
    from core import hud
    hud._send({"type": "timers", "value": active()})


def active() -> list[dict]:
    """Running timers, soonest first, without the thread handles."""
    now = time.time()
    with _lock:
        rows = [{k: v for k, v in t.items() if not k.startswith("_")} | {"left": max(0, round(t["ends"] - now))}
                for t in _timers.values()]
    return sorted(rows, key=lambda r: r["ends"])


def add(seconds: int, label: str, on_done) -> dict:
    """Start a timer; on_done(label) runs when it finishes (not when cancelled)."""
    tid = uuid.uuid4().hex[:6]
    label = (label or "timer").strip()[:60] or "timer"

    def fire():
        with _lock:
            gone = _timers.pop(tid, None)
        if gone is not None:
            _publish()
            on_done(label)
    t = threading.Timer(seconds, fire)
    t.daemon = True
    row = {"id": tid, "label": label, "seconds": int(seconds), "ends": time.time() + seconds, "_t": t}
    with _lock:
        _timers[tid] = row
    t.start()
    _publish()
    return {k: v for k, v in row.items() if not k.startswith("_")}


def cancel(which: str = "") -> list[str]:
    """Cancel by id or (part of) the label; '' or 'all' cancels everything. Returns the labels cancelled.
    'the tea timer' finds the timer labelled 'tea'."""
    import re
    w = re.sub(r"\b(?:the|my|a|an|timers?|for|please)\b", " ", (which or "").lower())
    w = re.sub(r"\s+", " ", w).strip()
    if w in ("every", "all of them", "everything"):
        w = "all"
    with _lock:
        hits = [t for t in _timers.values() if not w or w == "all" or w == t["id"]
                or w in t["label"].lower() or t["label"].lower() in w]
        if w and w != "all" and len(hits) > 1:   # "cancel the timer" with several running: the soonest one
            exact = [t for t in hits if t["label"].lower() == w]
            hits = exact or [min(hits, key=lambda t: t["ends"])]
        for t in hits:
            t["_t"].cancel()
            _timers.pop(t["id"], None)
    if hits:
        _publish()
    return [t["label"] for t in hits]


def cancel_id(tid: str) -> bool:
    """Cancel exactly the timer with this id (as add() returned it) and nothing else. cancel() also matches labels,
    so code that owns a timer stops it through here: its label may be part of another timer's label."""
    with _lock:
        t = _timers.pop(tid, None) if tid else None
        if t is not None:
            t["_t"].cancel()
    if t is not None:
        _publish()
    return t is not None


def clear() -> None:
    """Tests: stop everything."""
    cancel("all")
