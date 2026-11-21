"""Where a turn's "thinking" time goes: every model call, PC action and planning step, timed, so speed work is measured.

Spans are kept in one small ring by wall-clock time (no per-thread bookkeeping: the brain, the gateway and the PC executor
run on different threads). A turn's breakdown is every span that started inside that turn's window. Operational timing only:
no conversation text beyond the first words of what was said, kept in data/learning/turn-timing-<pc|server>.jsonl (one file per machine: the folder is synced).
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from contextlib import contextmanager

_lock = threading.Lock()
_spans: deque = deque(maxlen=600)          # (start, end, kind, name)
_outcomes: deque = deque(maxlen=100)       # (when, {route, state, verified, tools, failed}) one per finished PC run


def outcome(route: str, state: str, verified: bool, tools=(), failed: str | None = None) -> None:
    """What a PC run came to, so the turn-timing row can say which tool failed (timing alone never said)."""
    row = {"route": str(route)[:20], "state": str(state)[:20], "verified": bool(verified), "tools": [str(t)[:30] for t in tools][:8]}
    if failed:
        row["failed"] = str(failed)[:30]
    with _lock:
        _outcomes.append((time.time(), row))


def span(kind: str, name: str, start: float, end: float) -> None:
    with _lock:
        _spans.append((start, end, kind, str(name)[:40]))


_open: dict = {}                           # id -> (start, kind): spans begun and not yet ended (a tool still running)
ACTING = ("tool", "pc", "relay")           # spans that may have DONE something (a skill, a PC step, a call to the PC)


@contextmanager
def timed(kind: str, name: str):
    start = time.time()
    token = object()
    with _lock:
        _open[id(token)] = (start, kind)
    try:
        yield
    finally:
        with _lock:
            _open.pop(id(token), None)
        span(kind, name, start, time.time())


def acted_since(since: float, kinds=ACTING) -> bool:
    """A tool, PC step or relay began at or after wall-clock `since`, finished or still running (main.py: a reply he cut
    off may already have acted, so his words are never asked again). Any thread counts: errs toward True."""
    with _lock:
        return (any(start >= since and kind in kinds for start, _end, kind, _name in _spans)
                or any(start >= since and kind in kinds for start, kind in _open.values()))


def breakdown(since: float, until: float | None = None) -> dict:
    """{kind: {"n", "s", "parts": {name: seconds}}} for the spans that started in [since, until]."""
    until = until or time.time()
    out: dict = {}
    with _lock:
        rows = [r for r in _spans if since <= r[0] <= until]
    for start, end, kind, name in rows:
        k = out.setdefault(kind, {"n": 0, "s": 0.0, "parts": {}})
        k["n"] += 1
        k["s"] += end - start
        k["parts"][name] = round(k["parts"].get(name, 0.0) + end - start, 2)
    for k in out.values():
        k["s"] = round(k["s"], 2)
    return out


# What a turn was busy with, as opposed to "other": the kinds whose seconds the log line names.
BUSY = ("model", "pc", "tool", "relay", "stt", "voice")


def busy(since: float, until: float | None = None, kinds: tuple = BUSY) -> float:
    """Seconds of [since, until] covered by at least one span of `kinds`. Spans overlap (a tool that asks the PC is a
    tool span with relay spans inside it), so their sum would count that time twice and hide the turn's "other"."""
    until = until or time.time()
    with _lock:
        rows = sorted((r[0], min(r[1], until)) for r in _spans if r[2] in kinds and since <= r[0] <= until)
    total, edge = 0.0, since
    for start, end in rows:
        if end > edge:
            total += end - max(start, edge)
            edge = end
    return round(total, 2)


def first_end(kind: str, since: float, until: float | None = None) -> float | None:
    """When the earliest span of `kind` that started in [since, until] ended (the model's first streamed token)."""
    until = until or time.time()
    with _lock:
        rows = [r for r in _spans if r[2] == kind and since <= r[0] <= until]
    return min(rows, key=lambda r: r[0])[1] if rows else None


def stages(began: float, marks: dict, spans: dict | None = None, since: float | None = None) -> dict:
    """{stage: seconds} for one voice turn (the latency ledger).

    marks: {stage: (start, end)} in wall-clock seconds. A mark that starts before the turn began (`began`, his last word)
    or ends before it starts is a timestamp left over from an earlier turn and is dropped (stale-timestamp guard).
    From the spans recorded since the turn committed (`since`): prefilter (the local meaning checks before the brain:
    the stop/dismiss guess, a yes/no to a waiting plan), decide (the gateway), model (every model call) and first_token
    (commit -> the first token the local model streamed)."""
    out: dict = {}
    for name, pair in (marks or {}).items():
        try:
            start, end = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if start >= began - 0.05 and end >= start:
            out[name] = round(end - start, 2)
    # relay: every hop to the PC (the laptop's thinking is one); tool: each skill that ran; stt and voice: the phone
    # server's transcription of a voice note and the making of the reply's audio (core/appserver).
    for stage, kind in (("prefilter", "meaning"), ("decide", "decide"), ("model", "model"), ("relay", "relay"),
                        ("tool", "tool"), ("stt", "stt"), ("voice", "voice")):
        if spans and kind in spans and stage not in out:
            out[stage] = round(spans[kind]["s"], 2)
    if since is not None:
        token = first_end("ttft", since)
        if token is not None and token >= since:
            out["first_token"] = round(token - since, 2)
    return out


def words(b: dict, thinking: float, other: float | None = None) -> str:
    """'model 3x 2.4s (advanced 2.2, fast 0.2) · pc 4.1s (close_site_tab 4.1) · other 1.0s'. `other`: the seconds no
    span covers, when the caller measured them (record() does, with busy()); else `thinking` less each kind's sum."""
    parts = []
    for kind in ("model", "pc", "decide", "tool", "relay", "stt", "voice"):
        if kind in b:
            k = b[kind]
            top = ", ".join(f"{n} {s:.1f}" for n, s in sorted(k["parts"].items(), key=lambda kv: -kv[1])[:3])
            parts.append(f"{kind} {k['n']}x {k['s']:.1f}s ({top})")
    if other is None:
        other = thinking - sum(b.get(kind, {}).get("s", 0) for kind in BUSY)
    if other > 0.3:
        parts.append(f"other {other:.1f}s")
    return " · ".join(parts)


def test_traffic(test: bool | None = None) -> bool:
    """Is this turn test traffic, not the owner? Said by the caller (the app server: a 'claude-verify' device), or the
    whole process (JARVIS_TEST_TRAFFIC=1: harnesses and live-check scripts). The core scorecard counts only his turns:
    most phone rows of Nov 8-16 were live checks and replays, not him (2026-11-16)."""
    if test is not None:
        return bool(test)
    import os
    if os.environ.get("JARVIS_TEST_TRAFFIC") == "1":
        return True
    try:
        from core import gateway
        return bool(getattr(gateway.TEST_TURN, "on", False))
    except Exception:  # noqa: BLE001
        return False


def record(channel: str, said: str, started: float, thinking: float, total: float,
           marks: dict | None = None, began: float | None = None, test: bool | None = None, device: str = "") -> str:
    """Log one turn's breakdown; returns the words for the log line. With `marks` (the voice loop's stage timestamps)
    the row also carries the per-stage ledger, "stages" (see stages(); scripts/latency_report.py reads it). Test traffic
    (test_traffic) is marked "test": true at the source; `device`: the phone app device's short id (not its name)."""
    b = breakdown(started)
    text = words(b, thinking, max(0.0, thinking - busy(started)))
    row = {"at": round(started, 1), "channel": channel, "said": (said or "")[:80], "total": round(total, 2),
           "thinking": round(thinking, 2), "spans": b}
    if test_traffic(test):
        row["test"] = True
    if device:
        row["device"] = str(device)[:12]
    with _lock:
        pc = [o for at, o in _outcomes if at >= started]
    if pc:
        row["pc"] = pc
    if marks is not None:
        row["stages"] = stages(began if began is not None else started, marks, b, started)
        text += (" | " if text else "") + " · ".join(f"{k} {v:.2f}s" for k, v in row["stages"].items())
    try:
        from core import storage
        from core import pcbridge
        host = "pc" if pcbridge.local_pc() else "server"     # synced folder: one writer per file (no conflict copies)
        path = storage.DATA / "learning" / f"turn-timing-{host}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001  (timing must never break a turn)
        pass
    return text
