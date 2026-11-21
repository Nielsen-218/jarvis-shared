"""MISSES: the one place a failed request is written down so the learning loop can study it.

Every route a PC or skill request can take on a live turn ends in one of a few failure exits (a step that did not verify, a
plan nobody could make, a learned skill that did not pass, a tool that errored). Each of them calls `record` (directly, with the
sentence in hand) or `fail` (the turn knows the sentence: `turn()` is opened by core.pcfast.handle and Brain._ask). The two are
the SAME choke point: a failed request is one row, however many exits notice it.

  * one row per failed request: the key is a hash of the normalised sentence (no new copy of his words anywhere: the row's
    `said` is the field the learning queue already reads, written to the same file as before); a second report of the same
    key within DEDUP_SECONDS is the same failure seen at another exit and writes nothing.
  * a success is `ok()`: when the same sentence had an open miss, a RECOVERY is appended to learning/miss-recoveries.jsonl
    (key and time only: no words). The miss row stays; readers see it as recovered and no longer study it.
  * a chat line, a question or a success never reaches `record`: exits call it only on a real failure.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

MISSES = "learning/live-misses.jsonl"
RECOVERIES = "learning/miss-recoveries.jsonl"
DEDUP_SECONDS = 90.0            # the same request reported at a second exit of the same turn
RECOVER_WITHIN = 6 * 3600.0     # a later success of the same sentence this soon after the miss recovers it

_turn = threading.local()
_lock = threading.Lock()
_CHANNEL = re.compile(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*")


def clean(said: str) -> str:
    return _CHANNEL.sub("", said or "").strip()


def key(said: str, uid: str = "") -> str:
    words = re.sub(r"[^a-z0-9]+", " ", clean(said).casefold()).strip()
    return hashlib.sha1(f"{uid}|{words}".encode("utf-8")).hexdigest()[:16] if words else ""


def _uid() -> str:
    try:
        from core import users
        return users.current() or ""
    except Exception:  # noqa: BLE001
        return ""


def _read(name: str) -> List[dict]:
    from core import storage
    try:
        text = (storage.DATA / name).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            if line.strip():
                out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(name: str, row: dict) -> None:
    from core import storage
    path = storage.DATA / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _skip(said: str) -> bool:
    """Jarvis's own briefing turn, and a developer's verification turn, are not the owner's experience."""
    if re.match(r"\s*\[Automatic", said or "", re.I):
        return True
    from core import turntrace
    return turntrace.test_traffic()          # a 'claude-verify' turn, or a process marked JARVIS_TEST_TRAFFIC=1


# ------------------------------------------------------------------------------------------------ the turn's sentence
@contextmanager
def turn(said: str):
    """The sentence of the turn now running, so an exit deep inside (a skill, a tool) can report a failure without being handed
    his words. A nested turn (Brain._ask around core.pcfast.handle) shares the outer one's state when it is the same sentence."""
    prior = getattr(_turn, "state", None)
    if prior is not None and prior["said"] == clean(said):
        yield prior
        return
    _turn.state = {"said": clean(said), "failed": False, "ok": False}
    try:
        yield _turn.state
    finally:
        _turn.state = prior


def current() -> str:
    state = getattr(_turn, "state", None)
    return state["said"] if state else ""


def adopt(said: str) -> None:
    """The turn is an ANSWER to an earlier request ("yes" to a waiting plan, "the second one"): from here on the turn's
    sentence is the request being carried out, not the answer word. An empty sentence (the request is not known) means this
    turn writes and recovers nothing: a bare "yes" must never become a miss or recover one."""
    state = getattr(_turn, "state", None)
    if state is not None:
        state["said"] = clean(said)


def fail(reason: str, route: str = "", decision=None) -> bool:
    """A failure exit that does not hold the sentence: it belongs to the turn that is running (no turn: not an owner request,
    nothing is written)."""
    state = getattr(_turn, "state", None)
    if not state or not state["said"]:
        return False
    state["failed"] = True
    return record(state["said"], reason, decision, route)


def ok(route: str = "") -> bool:
    """A success exit. A turn that already failed somewhere (step 2 of 3) is not a success."""
    state = getattr(_turn, "state", None)
    if not state or not state["said"] or state["failed"]:
        return False
    state["ok"] = True
    return recovered(state["said"], route)


# ------------------------------------------------------------------------------------------------------ the choke point
def record(said: str, reason: str, decision=None, route: str = "") -> bool:
    """Write the miss unless this very request was already written a moment ago. True when a row was written. Best effort:
    never raises into a reply."""
    try:
        said = clean(said)
        if not said or _skip(said):
            return False
        k, now = key(said, _uid()), time.time()
        with _lock:
            recent = [r for r in _read(MISSES)[-40:] if r.get("key") == k and now - float(r.get("at") or 0) < DEDUP_SECONDS]
            if recent:
                return False
            row = {"at": now, "said": said[:200], "reason": str(reason)[:200], "key": k, "route": route,
                   "decision": (decision.brief() if decision is not None else None)}
            _append(MISSES, row)
        return True
    except Exception:  # noqa: BLE001
        return False


def recovered(said: str, route: str = "") -> bool:
    """The same sentence now worked after an open miss: mark the miss recovered (appended; nothing is deleted)."""
    try:
        said = clean(said)
        if not said or _skip(said):
            return False
        k, now = key(said, _uid()), time.time()
        with _lock:
            # A miss the API taught (core.apiassist, route "api") worked only BECAUSE the API planned it: the local brain
            # still has to learn it, so that success never counts as recovered.
            mine = [r for r in _read(MISSES) if r.get("key") == k and r.get("route") != "api"
                    and now - float(r.get("at") or 0) <= RECOVER_WITHIN]
            if not mine:
                return False
            last = max(float(r.get("at") or 0) for r in mine)
            if any(r.get("key") == k and float(r.get("at") or 0) >= last for r in _read(RECOVERIES)):
                return False
            _append(RECOVERIES, {"at": now, "key": k, "route": route})
        return True
    except Exception:  # noqa: BLE001
        return False


def rows(days: float = 7.0, limit: int = 20, *, include_recovered: bool = False) -> List[dict]:
    """The recent miss rows, newest last. A row whose sentence worked later carries `recovered_at`; those are left out unless
    asked for. Rows from before the key existed are matched by their sentence."""
    mine_rows = _read(MISSES)
    rec: Dict[str, float] = {}
    for r in _read(RECOVERIES):
        rec[r.get("key", "")] = max(rec.get(r.get("key", ""), 0.0), float(r.get("at") or 0))
    cutoff = time.time() - days * 86400
    out = []
    for r in mine_rows:
        if float(r.get("at") or 0) < cutoff:
            continue
        k = r.get("key") or key(r.get("said", ""), "")
        when = 0.0 if r.get("route") == "api" else rec.get(k, 0.0)   # an API-taught miss: closed only when learned locally
        if when and when >= float(r.get("at") or 0):
            r = {**r, "recovered_at": when}
        if r.get("recovered_at") and not include_recovered:
            continue
        out.append(r)
    return out[-limit:]
