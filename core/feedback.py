"""What failed, durably: a tool call that errored is remembered across conversations and restarts.

The brain already refuses to repeat a call that failed earlier in the SAME conversation (`_failed_calls`); that memory
died with the conversation, so after a restart the same broken method was tried again, failed again (often after a
long timeout) and the user heard the same apology. This keeps, per machine, the last failures by call fingerprint
(skill + arguments) with the error's first line and a count. It is advice to the brain, not a lock: `warning()` gives
one sentence to put beside the tool result, so the model can choose another method; `cleared()` forgets a
fingerprint when the call later succeeds. Nothing here changes what runs.

Two kinds of failure, told apart by the result text (the skill's exception is already a friendly sentence here):
  transient  - "isn't reachable", "timed out", "rate limited", "busy", "out of memory": a service or the network, not
               the skill. Kept 15 minutes, mentioned only from the second failure, worded as "may be temporary: one
               retry is fine" - recovery must never be discouraged.
  failure    - "Invalid call", "not installed", "no such", "permission denied", "unknown", "unsupported": the method
               itself. Kept a day, mentioned from the first failure, worded as "don't retry; offer another way".
Arguments that look like credentials, and long blobs (images, base64), are never written down."""
from __future__ import annotations

import json
import re
import threading
import time

from core import storage

FILE_PC, FILE_SERVER = "tool_failures-pc.json", "tool_failures-server.json"
KEEP = {"transient": 15 * 60, "failure": 24 * 3600}
MENTION_FROM = {"transient": 2, "failure": 1}
MAX_ROWS = 200
_lock = threading.Lock()

_TRANSIENT = re.compile(r"isn'?t reachable|not reachable|unreachable|can'?t reach|timed? ?out|timeout|connection "
                        r"(?:refused|reset|closed|aborted|lost)|no connection|offline|rate.?limit|too many requests|"
                        r"temporar|try again (?:in|later|shortly)|busy|unavailable right now|service is down|"
                        r"having a moment|insufficient (?:memory|headroom)|out of memory|headroom|5\d\d\b|"
                        r"circuit|cooling down|not responding", re.I)
_SECRET_KEY = re.compile(r"pass(?:word|code|phrase)?|secret|token|api.?key|\bkey\b|pin\b|ssn|card|cvv|credential|"
                         r"auth|cookie|session|bearer", re.I)
_SECRET_TEXT = re.compile(r"(?:sk|ghp|gho|xox[abp]|AKIA)[-_A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
                          r"\b\d{12,19}\b|Bearer\s+\S+", re.I)


def kind(error_text: str) -> str:
    return "transient" if _TRANSIENT.search(error_text or "") else "failure"


def _redacted(value):
    """Arguments as remembered: credential-like keys and long blobs replaced, everything else as given."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if _SECRET_KEY.search(str(k)) else _redacted(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redacted(v) for v in value[:20]]
    if isinstance(value, str):
        if len(value) > 120 or value.startswith("data:") or _SECRET_TEXT.search(value):
            return f"[{len(value)} chars]"
        return value
    return value


def fingerprint(name: str, args: dict, transient: bool = False) -> str:
    """A reproducible failure is about this call (skill + arguments); an outage is about the skill's service, whatever
    the arguments (live: two outages with days=1 and days=2 counted as two different calls)."""
    if transient:
        return name + " *"
    return name + " " + json.dumps(_redacted(args or {}), sort_keys=True, ensure_ascii=False)[:300]


def _file() -> str:
    from core import pcbridge
    return FILE_PC if pcbridge.local_pc() else FILE_SERVER


def _rows() -> list[dict]:
    now = time.time()
    return [r for r in storage.load(_file(), []) if isinstance(r, dict)
            and now - r.get("at", 0) < KEEP.get(r.get("kind", "failure"), KEEP["failure"])]


def record(name: str, args: dict, error_text: str) -> dict:
    """A failed call (is_error result): remembered with its kind and the error's first line; the same call again bumps
    the count. A credential-like fragment in the error text is withheld."""
    first = (error_text or "").strip().splitlines()[0][:160] if error_text else ""
    if _SECRET_TEXT.search(first):
        first = "[error text withheld: credential-like]"
    key = fingerprint(name, args, transient=kind(first) == "transient")
    with _lock:
        rows = _rows()
        row = next((r for r in rows if r.get("key") == key), None)
        if row is None:
            row = {"key": key, "skill": name, "count": 0, "first_at": time.time()}
            rows.append(row)
        row.update(count=row["count"] + 1, at=time.time(), error=first, kind=kind(first))
        storage.save(_file(), rows[-MAX_ROWS:])
    return row


def cleared(name: str, args: dict) -> None:
    """The call worked: whatever was remembered about it is forgotten."""
    keys = {fingerprint(name, args), fingerprint(name, args, transient=True)}   # the call, and the service's outage
    with _lock:
        rows = _rows()
        kept = [r for r in rows if r.get("key") not in keys]
        if len(kept) != len(rows):
            storage.save(_file(), kept)


def recent(name: str, args: dict) -> dict | None:
    keys = {fingerprint(name, args), fingerprint(name, args, transient=True)}
    rows = [r for r in _rows() if r.get("key") in keys]
    return max(rows, key=lambda r: r.get("at", 0)) if rows else None


def warning(name: str, args: dict) -> str:
    """One sentence for the model, or "" (no recent failure of this call; or a single transient one)."""
    row = recent(name, args)
    if not row or row["count"] < MENTION_FROM.get(row.get("kind", "failure"), 1):
        return ""
    ago = time.time() - row["at"]
    when = f"{int(ago // 60)} min ago" if ago < 3600 else f"{int(ago // 3600)} h ago"
    times = f"{row['count']} times" if row["count"] > 1 else "once"
    if row.get("kind") == "transient":
        return (f"(This {name} call failed {times} recently, last {when}: \"{row['error']}\". That looks temporary: "
                f"one retry is fine; if it still fails, say so and offer another way rather than keep trying.)")
    return (f"(This exact {name} call failed {times} in the last day, last {when}: \"{row['error']}\". "
            f"If it fails again, don't retry it; say what failed and offer another way.)")


def summary(limit: int = 10) -> list[dict]:
    """For the dashboard and tests: the most repeated recent failures."""
    return sorted(_rows(), key=lambda r: (-r.get("count", 0), -r.get("at", 0)))[:limit]
