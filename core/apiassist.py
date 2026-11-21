"""Every time Jarvis leans on the paid API (Claude), it is written down, and what the API taught is kept to learn from.

Owner 2026-11-21: "use the API credit as one of Jarvis's brains for harder questions and tasks ... whenever Jarvis has to
call on the API for PC control or anything, the API and Jarvis will log it, and Jarvis will have to get those skills and
practice them when the PC is idle".

- `log(kind, why, said, ...)`: one row per API call in data/learning/api-assists-<machine>.jsonl (what was asked, why the
  API was used: "hard" or the local failure, the model, the cost).
- `taught(said, decision)`: a PC request the local planner could not plan and the API planned, which then RAN and was
  verified. Kept in data/learning/api-taught-<machine>.jsonl. From then on:
    * the same request (same normalised words) is planned from the taught steps with NO model call (`plan_for`);
    * the local planner is shown the taught examples closest to a new request (`examples_for`), so similar wordings
      are learned, not just the exact sentence;
    * a miss row "taught by the API" stays open for the nightly re-study (core.misslessons / core.acquire.discover):
      the local model must learn to plan it by itself.
Rows hold his words and the steps only (no prompts, no API text beyond the plan).
"""
import json
import re
import threading
import time
from pathlib import Path
from typing import List, Optional

from core import storage

_lock = threading.Lock()
MAX_EXAMPLES = 3
TAUGHT_REASON = "taught by the API: the local planner could not plan this"


def _machine() -> str:
    import os
    return "server" if os.getenv("JARVIS_ROLE", "all") == "server" else "pc"


def _file(name: str) -> Path:
    return Path(storage.DATA) / "learning" / f"{name}-{_machine()}.jsonl"


def _append(name: str, row: dict) -> None:
    path = _file(name)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rows(name: str) -> List[dict]:
    out: List[dict] = []
    for machine in ("pc", "server"):
        path = Path(storage.DATA) / "learning" / f"{name}-{machine}.jsonl"
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            continue
    return out


def norm(said: str) -> str:
    text = re.sub(r"^\s*(?:hey\s+)?jarvis[,\s]+", "", (said or "").lower())
    text = re.sub(r"\b(?:please|for me|can you|could you|would you|i want you to)\b", " ", text)
    return " ".join(re.findall(r"[a-z0-9']+", text))


def log(kind: str, why: str, said: str = "", *, model: str = "", cost: float = 0.0, detail: str = "") -> None:
    """One API use. Best effort: never breaks a turn."""
    try:
        _append("api-assists", {"at": round(time.time(), 1), "kind": kind, "why": why[:80], "said": (said or "")[:200],
                                "model": model, "usd": round(float(cost or 0), 6), "detail": (detail or "")[:200]})
    except Exception:  # noqa: BLE001
        pass


def replayable(said: str, decision) -> bool:
    """May this plan be replayed for the same words later? The same bar as the nightly lessons' stored shortcuts
    (core.misslessons): the whole request covered, actions only, every step low-risk, no word whose meaning is the moment
    ("close it", "do that again"), and no target the owner never said. Anything else is still logged and practised as a
    miss, but always planned fresh."""
    from core import misslessons
    return (bool(getattr(decision, "covers", False)) and bool(decision.intents)
            and all(i.kind == "actions" for i in decision.intents) and misslessons._low_risk(decision)
            and not misslessons.moment_bound(said) and not misslessons._invents(decision, said))


def taught(said: str, decision) -> bool:
    """The API planned a PC request the local planner could not, and it ran verified: keep it to learn from. The miss row
    (route "api") is always written, for the nightly re-study; the plan is kept for replay only when replayable()."""
    try:
        steps = decision.steps()
        if not said or not steps:
            return False
        from core import misses
        row = {"at": time.time(), "said": misses.clean(said)[:200], "reason": TAUGHT_REASON, "key": misses.key(said, misses._uid()),
               "route": "api", "decision": decision.brief()}
        with misses._lock:
            misses._append(misses.MISSES, row)          # not deduplicated: the local miss written a moment ago is a different lesson
        key = norm(said)
        if not key or not replayable(said, decision) or any(r.get("key") == key for r in _rows("api-taught")):
            return False
        _append("api-taught", {"at": round(time.time(), 1), "said": said[:200], "key": key, "steps": steps})
        return True
    except Exception:  # noqa: BLE001
        return False


def plan_for(said: str) -> Optional[List[dict]]:
    """The taught steps for exactly this request (normalised words), newest teaching first; None if never taught."""
    key = norm(said)
    if not key:
        return None
    for row in reversed(_rows("api-taught")):
        if row.get("key") == key and row.get("steps"):
            return row["steps"]
    return None


def examples_for(said: str, k: int = MAX_EXAMPLES) -> List[dict]:
    """The taught requests sharing the most words with this one (for the local planner's prompt)."""
    words = set(norm(said).split())
    if not words:
        return []
    scored = []
    for row in _rows("api-taught"):
        shared = words & set(str(row.get("key", "")).split())
        if len(shared) >= 2:
            scored.append((len(shared), row.get("at", 0), row))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [r for _n, _at, r in scored[:k]]


def recent(days: float = 7.0) -> List[dict]:
    since = time.time() - days * 86400
    return [r for r in _rows("api-assists") if float(r.get("at") or 0) >= since]


def report(days: float = 7.0) -> dict:
    rows = recent(days)
    by = {}
    for r in rows:
        by[r.get("kind", "?")] = by.get(r.get("kind", "?"), 0) + 1
    return {"calls": len(rows), "usd": round(sum(float(r.get("usd") or 0) for r in rows), 4), "by_kind": by,
            "taught": len([r for r in _rows("api-taught") if float(r.get("at") or 0) >= time.time() - days * 86400])}
