"""The owner's corrections: a few of his real lines a day, and his verdict on what Jarvis did (2026-11-16).

Why: Nijon (Jarvis's own trainable student brain, docs/nijon/STUDENT-BRAIN-V0.md) may only learn from permitted, verified
examples. The strongest permitted source is the owner himself: his own words, and his own judgement of whether Jarvis got
it right. The dataset census found 0 such examples; this collects them. It also gives the truest accuracy number we have:
"Jarvis was right N of M times, by your judgement".

Where the lines come from: core.scorecard's real-turn log (core-turns-<machine>.jsonl: his words, the command kind, the
outcome and the first 60 characters of the reply). Test traffic never reaches it. Up to DAILY lines a day are offered,
mistakes first (said twice, complained, not done, misheard), then everyday commands, then conversation.

What is stored (data/users/<owner>/corrections-<machine>.jsonl, his own folder, append-only, one file per machine):
  his words, what Jarvis did, his verdict (right / wrong / skip), and when wrong what he MEANT (a kind from a fixed list and
  the target in his own words). Provenance is recorded per row:
    input   always "owner"
    target  "owner"            when he wrote what he meant (wrong)
            "jarvis_local"     when he confirmed Jarvis's own reply, on a day Jarvis used no cloud model
            "unknown"          when he confirmed a reply on a day a cloud model was used (excluded from training)
Nothing here runs, opens or changes anything else; nothing trains a model.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime

DAILY = 5
MEANT_KINDS = {
    "open": "Open an app or file",
    "close": "Close an app, window or tab",
    "minimize": "Minimize a window",
    "focus": "Bring a window to the front",
    "screen": "Show one of Jarvis's screens (Command Center, World Intelligence)",
    "globe": "Move or change the globe",
    "pc_question": "Answer a question about the PC (CPU, RAM, what's running)",
    "info": "Answer an everyday question (time, weather, where am I)",
    "talk": "Just talk back, no action",
    "other": "Something else",
}
VERDICTS = ("right", "wrong", "skip")
_lock = threading.Lock()


def key(turn: dict) -> str:
    raw = f"{turn.get('host', '')}|{turn.get('ts', '')}|{turn.get('said', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _host() -> str:
    from core import scorecard
    return scorecard._host()


def _path(uid: str, host: str | None = None):
    """One file per machine: the phone writes through the laptop, the Hub on the PC, and data/ is Syncthing-synced, so
    two writers of one file would make sync conflicts. Each machine appends only to its own file; reads take all."""
    from core import storage
    return storage.user_dir(uid) / f"corrections-{host or _host()}.jsonl"


def reviews(uid: str) -> list[dict]:
    from core import storage
    rows = []
    for path in sorted(storage.user_dir(uid).glob("corrections*.jsonl")):
        if ".sync-conflict" in path.name:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
    return rows


def _priority(t: dict) -> int:
    if t.get("first_try") is False or t.get("mishear") or t.get("repeated") or t.get("complained"):
        return 0
    if t.get("first_try") is True:
        return 1
    return 2


def _turns() -> list[dict]:
    from core import scorecard
    return scorecard.judge(scorecard.turns())


def queue(uid: str, n: int | None = None, now: float | None = None, turns: list[dict] | None = None) -> dict:
    """Up to `n` unreviewed lines for today (minus those already reviewed today), mistakes first, newest first within a band."""
    n = DAILY if n is None else n
    now = time.time() if now is None else now
    done = reviews(uid)
    seen = {r["key"] for r in done}
    today = datetime.fromtimestamp(now).date().isoformat()
    left = max(0, n - sum(1 for r in done if str(r.get("reviewed_at", ""))[:10] == today))
    rows = [t for t in (turns if turns is not None else _turns()) if t.get("said")]
    rows = [t for t in rows if key(t) not in seen]
    rows.sort(key=lambda t: (_priority(t), -float(t.get("ts", 0))))
    items = [{"key": key(t), "said": t["said"], "when": datetime.fromtimestamp(float(t.get("ts", 0))).strftime("%b %d, %I:%M %p"),
              "channel": t.get("channel", ""), "kind": t.get("kind", "other"), "outcome": t.get("outcome", ""),
              "reply": t.get("reply_head", ""), "repeated": bool(t.get("repeated")), "complained": bool(t.get("complained"))}
             for t in rows[:left]]
    return {"items": items, "left_today": left, "waiting": len(rows), "kinds": MEANT_KINDS, "stats": stats(uid, done)}


def _cloud_used(day: str) -> bool:
    """Did any cloud model answer on that day? (data/usage.json keeps a cost row per day with cloud requests.)"""
    from core import storage
    try:
        usage = json.loads((storage.DATA / "usage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True  # can't tell: treat as used (conservative)
    row = usage.get(day) or {}
    return bool(row.get("requests"))


def record(uid: str, body: dict, turns: list[dict] | None = None, now: float | None = None) -> dict:
    """Store one verdict. Raises ValueError with a sentence the page can show."""
    k = str(body.get("key", ""))
    verdict = str(body.get("verdict", ""))
    if verdict not in VERDICTS:
        raise ValueError("Pick right, wrong or skip.")
    turn = next((t for t in (turns if turns is not None else _turns()) if key(t) == k), None)
    if turn is None:
        raise ValueError("That line isn't in the log any more.")
    if any(r["key"] == k for r in reviews(uid)):
        raise ValueError("You already reviewed that one.")
    meant = None
    if verdict == "wrong":
        mk = str(body.get("meant_kind", ""))
        target = " ".join(str(body.get("meant", "")).split())[:120]
        if mk not in MEANT_KINDS:
            raise ValueError("Pick what you meant.")
        if not target and mk not in ("talk",):
            raise ValueError("Say what you meant, in a few words.")
        meant = {"kind": mk, "target": target}
    day = datetime.fromtimestamp(float(turn.get("ts", 0))).date().isoformat()
    target_prov = {"wrong": "owner", "skip": None}.get(verdict, "unknown" if _cloud_used(day) else "jarvis_local")
    row = {"key": k, "turn_ts": turn.get("ts"), "host": turn.get("host", ""), "said": turn["said"],
           "jarvis": {"kind": turn.get("kind"), "outcome": turn.get("outcome"), "reply": turn.get("reply_head", "")},
           "verdict": verdict, "meant": meant, "note": " ".join(str(body.get("note", "")).split())[:300],
           "reviewed_at": datetime.fromtimestamp(time.time() if now is None else now).isoformat(timespec="seconds"),
           "via": str(body.get("via", "hub"))[:12], "provenance": {"input": "owner", "target": target_prov}}
    path = _path(uid)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"ok": True, "stats": stats(uid)}


def stats(uid: str, rows: list[dict] | None = None) -> dict:
    rows = reviews(uid) if rows is None else rows
    judged = [r for r in rows if r.get("verdict") in ("right", "wrong")]
    right = sum(r["verdict"] == "right" for r in judged)
    by_kind: dict[str, list[int]] = {}
    for r in judged:
        k = (r.get("jarvis") or {}).get("kind") or "other"
        by_kind.setdefault(k, [0, 0])
        by_kind[k][0] += r["verdict"] == "right"
        by_kind[k][1] += 1
    return {"reviewed": len(judged), "right": right, "wrong": len(judged) - right, "skipped": len(rows) - len(judged),
            "by_kind": {k: {"right": a, "of": b} for k, (a, b) in sorted(by_kind.items())}}
