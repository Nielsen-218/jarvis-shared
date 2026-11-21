"""What Jarvis last observed, with when, from where and how sure: a small store the observers write to and the
skills read before observing again.

Observing costs: an OCR pass ~1-2 s of CPU, a screenshot for the vision model 10-20 s of GPU. The same question
twice in a row ("what's on my screen?" ... "and now?") re-ran all of it. Each observation is kept with its source
(ocr, uia, vision, camera), a confidence (the observer's own: OCR line count, UIA element count) and its age, so
a skill can answer from a fresh one, say how old it is, or decide it must look again. Unavailable states are
observations too ("no OCR engine") so the next caller doesn't retry a dead path every time. Nothing here reaches
disk: observations are about the screen and the room right now, and are gone with the process."""
from __future__ import annotations

import threading
import time
from typing import Any

FRESH = {"screen_text": 6.0, "screen_picture": 20.0, "camera": 10.0, "window": 3.0}   # seconds an observation stays current
_latest: dict[str, dict] = {}
_lock = threading.Lock()


def record(kind: str, value: Any, *, source: str, confidence: float, key: str = "", available: bool = True) -> dict:
    """Keep the newest observation of `kind` (optionally per `key`, e.g. the app read). confidence in [0, 1]."""
    entry = {"kind": kind, "key": key, "value": value, "source": source, "confidence": max(0.0, min(1.0, confidence)),
             "at": time.monotonic(), "wall": time.time(), "available": available}
    with _lock:
        _latest[(kind, key)] = entry
    return entry


def latest(kind: str, key: str = "", max_age: float | None = None) -> dict | None:
    """The newest observation of `kind`, if it is younger than `max_age` (default: FRESH for the kind)."""
    with _lock:
        entry = _latest.get((kind, key))
    if entry is None:
        return None
    age = time.monotonic() - entry["at"]
    limit = FRESH.get(kind, 5.0) if max_age is None else max_age
    if age > limit:
        return None
    return dict(entry, age=age)


def describe(entry: dict) -> str:
    """'(observed 4 s ago via ocr, confidence 0.8)' for a reply that reuses it."""
    return f"(observed {entry.get('age', 0):.0f} s ago via {entry['source']}, confidence {entry['confidence']:.1f})"


def forget(kind: str | None = None) -> None:
    with _lock:
        for k in [k for k in _latest if kind is None or k[0] == kind]:
            del _latest[k]


def snapshot() -> list[dict]:
    """Every current observation with its age (for the dashboard and tests)."""
    now = time.monotonic()
    with _lock:
        return [dict(e, age=round(now - e["at"], 1), value=str(e["value"])[:80]) for e in _latest.values()]
