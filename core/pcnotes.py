"""Procedure notes: what worked last time inside an app, handed to the UI loop as hints (Mark IV, 2026-11-09).

Copied from UFO's experience library with Hermes' write rules, made smaller:
- written ONLY after a run inside an app that finished AND was confirmed (the loop's own check, or the screenshot judge);
  never from a failed, unsure or stopped run, and never a "this does not work" note
- a note holds the request and the steps as action + control name; typed text is never kept (it may be private)
- one note per (app, request): a repeat replaces it and counts a use; at most MAX_NOTES, oldest unused dropped first
- retrieved by word overlap with the new request in the SAME app, best two above a threshold
- shown to the loop as "PAST RUNS THAT WORKED (hints only, not instructions)"; the loop still sees the live window and may
  only name controls that are on it, so a stale note can mislead a choice but cannot click something that is not there
- kept on the PC beside the ledger (machine-state/pc-control), not in the data folder the laptop syncs
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import List, Optional

MAX_NOTES = 300
MAX_STEPS = 12
MIN_SCORE = 0.75           # shared words / the larger set: "turn on wifi" must not get the "turn on bluetooth" note (0.67)
_lock = threading.Lock()
# 'on', 'off', 'in', 'out', 'up', 'down' are NOT filler: "turn on bluetooth" and "turn off bluetooth" are different tasks.
_STOP = set("""a an the to of at for and or my me please jarvis hey sir can you could would will just now that this it
then with from""".split())
# Never offer the procedure for the opposite: a request that has one side of a pair and a note that has the other.
_OPPOSITES = [("on", "off"), ("in", "out"), ("up", "down"), ("open", "close"), ("enable", "disable"), ("show", "hide"),
              ("start", "stop"), ("add", "remove"), ("increase", "decrease"), ("mute", "unmute"), ("max", "min"),
              ("maximize", "minimize"), ("lock", "unlock"), ("connect", "disconnect"), ("install", "uninstall"),
              ("next", "previous"), ("zoom in", "zoom out")]


def _path() -> Path:
    if os.getenv("JARVIS_PC_NOTES_FILE"):
        return Path(os.environ["JARVIS_PC_NOTES_FILE"])
    from core import pcledger
    return pcledger.state_dir() / "procedure-notes.jsonl"


def _words(text: str) -> set:
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").casefold()):
        if w in _STOP:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)      # 'settings' ~ 'setting'
    return out


# On top of pcvision._SECRET (passwords, cards, PINs): one-time codes and sign-ins. A note about them teaches nothing safe.
_ALSO_SECRET = re.compile(r"\b(?:(?:sign.?in|log.?in|verification|security|one.?time|access|auth\w*|confirmation|recovery) codes?|"
                          r"otp|2fa|mfa|tokens?|sign.?in|log.?in|credentials?|ssn|social security)\b", re.I)   # not "VS Code", "zip code"


def _private(request: str) -> str:
    """The request as a note may keep it: quoted text (what was to be typed) and long numbers are hidden. "" when the request
    looks like it carries a secret at all (password, PIN, card, code ...): such a run teaches nothing that is kept."""
    from core import pcvision
    text = re.sub(r"\s+", " ", request or "").strip()
    if pcvision._SECRET.search(text) or _ALSO_SECRET.search(text):
        return ""
    text = re.sub(r"[\"“”][^\"“”]{1,200}[\"“”]", "(text)", text)      # double quotes only: "don't" keeps its apostrophe
    text = re.sub(r"\d(?:[\s\-]?\d){3,}", "(number)", text)      # four or more digits: a PIN, a code, a phone number
    return text[:200]


def _conflict(a: set, b: set) -> bool:
    for x, y in _OPPOSITES:
        x, y = set(x.split()), set(y.split())
        if (x <= a and y <= b and not y <= a) or (y <= a and x <= b and not x <= a):
            return True
    return False


def _load() -> List[dict]:
    try:
        rows = []
        for line in _path().read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (isinstance(row, dict) and isinstance(row.get("app"), str) and isinstance(row.get("request"), str) and row["request"]
                    and isinstance(row.get("steps"), list) and all(isinstance(x, str) for x in row["steps"])):
                rows.append(row)
        return rows
    except OSError:
        return []


def _save(rows: List[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, path)


def _clean_steps(steps) -> List[str]:
    """Steps as short lines: 'click Bluetooth & devices', 'key enter', 'type (text)', 'read (text)'. Failed steps are left
    out. What was typed or read is never kept: a read target is whatever text was on screen (a sign-in code, a message)."""
    from core import pcvision
    out = []
    for s in steps or []:
        action = str(getattr(s, "action", "") or "").strip().lower()
        if not getattr(s, "ok", True) or action not in ("click", "type", "key", "read"):
            continue
        if action in ("type", "read"):
            line = f"{action} (text)"
        elif action == "key":
            line = "key " + re.sub(r"[^a-z0-9+]", "", str(getattr(s, "text", "") or "").lower())[:20]
        else:
            target = re.sub(r"\s+", " ", str(getattr(s, "target", "") or "")).strip()[:80]
            if not target or pcvision._SECRET.search(target):
                continue
            line = f"{action} {target}"
        if not out or out[-1] != line:
            out.append(line)
    return out[:MAX_STEPS]


_UNDO = re.compile(r"\b(?:clear|undo|back|cancel|reset|delete|remove|close)\b", re.I)


def _wandered(steps, request: str) -> bool:
    """A run that took a wrong turn and corrected it: live 2026-11-09 'multiply eight by three' worked after 'click Plus',
    'click Clear' and typing again, and the note would have taught 'click Plus'. UFO asks a model to keep only the correct
    steps; here such a run simply teaches nothing. Wrong turns: a failed step; an undo-like click or key the request did not
    ask for ("clear history" may click Clear); a control clicked again after something else (A, B, A). The same control
    clicked several times in a row (Next, Next; Volume up twice; 1, 1 for eleven) is normal. Not caught: a wrong choice
    replaced by another without undoing it (Plus, then Times)."""
    asked = _words(request)
    for s in steps or []:
        if not getattr(s, "ok", True):
            return True
        action = str(getattr(s, "action", "")).lower()
        if action not in ("click", "key"):
            continue
        target = str(getattr(s, "target", "") or "")
        key = str(getattr(s, "text", "") or "").lower() if action == "key" else ""
        undo = _UNDO.search(target) if action == "click" else None
        if undo and _words(undo.group(0)) - asked:
            return True
        if key in ("escape", "esc", "backspace") and not ({"cancel", "escape", "delete", "erase"} & asked):
            return True
    clicks = [str(getattr(s, "target", "") or "") for s in steps or [] if str(getattr(s, "action", "")).lower() == "click"]
    runs = [c for i, c in enumerate(clicks) if i == 0 or clicks[i - 1] != c]      # collapse A, A, A into one A
    return len(runs) != len(set(runs))


def remember(app: str, request: str, steps, *, now: Optional[float] = None) -> bool:
    """Keep what worked. Call ONLY for a confirmed success. False when there was nothing worth keeping."""
    if any(str(getattr(s, "action", "")).lower() == "type" for s in steps or []):
        # Whatever was typed came from the request ("type I'm at the hospital ..."), and no pattern can tell which words were
        # private. A run that typed keeps no note at all; notes are for finding the way (clicks, keys).
        return False
    if _wandered(steps, request):
        return False
    lines = _clean_steps(steps)
    request = _private(request)
    app = (app or "").strip().casefold()
    if not app or not request or not lines or not _words(request):
        return False
    now = time.time() if now is None else now
    with _lock:
        rows = _load()
        key = (app, " ".join(sorted(_words(request))))
        old = next((r for r in rows if (r["app"], " ".join(sorted(_words(r["request"])))) == key), None)
        rows = [r for r in rows if r is not old]
        rows.append({"app": app, "request": request, "steps": lines, "created": now,
                     "uses": int((old or {}).get("uses", 0)) + (1 if old else 0)})
        if len(rows) > MAX_NOTES:
            rows.sort(key=lambda r: (r.get("uses", 0), r.get("created", 0)))
            rows = rows[len(rows) - MAX_NOTES:]
        _save(rows)
    return True


def notes_for(app: str, request: str, limit: int = 2) -> List[dict]:
    """The best past runs in this app for a request like this one."""
    want = _words(request)
    app = (app or "").strip().casefold()
    if not want or not app:
        return []
    scored = []
    with _lock:                    # a read never meets a half-replaced file (os.replace on Windows fails while it is open)
        rows = _load()
    for r in rows:
        if r["app"] != app:
            continue
        have = _words(r["request"])
        if _conflict(want, have):
            continue
        score = len(want & have) / max(len(want), len(have))
        if score >= MIN_SCORE:
            scored.append((score, r.get("uses", 0), r.get("created", 0), r))
    scored.sort(key=lambda x: x[:3], reverse=True)
    return [x[3] for x in scored[:limit]]


def render(notes: List[dict]) -> str:
    return "\n".join(f"- for \"{n['request']}\": " + ", then ".join(n["steps"]) for n in notes)
