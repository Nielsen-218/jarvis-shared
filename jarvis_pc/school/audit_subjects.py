"""School subjects for accountability (blueprint area 25): reading an audit trail to say what was done and what the gate refused.

The harness writes a small audit log (one JSON record per line, like the real one) and the answer is how it was built. Read-only.
"""
from __future__ import annotations

import json
import os
from typing import List

from .curriculum import Context, HIGH_SCHOOL, Num, Subject, Task


def _log(c: Context, ran: int, refused: int) -> str:
    rows = [{"event": "decision", "verdict": "ran", "command": "Get-Item " + c.word(8)} for _ in range(ran)]
    rows += [{"event": "decision", "verdict": "refused", "command": "Remove-Item " + c.word(8)} for _ in range(refused)]
    c.rng.shuffle(rows)
    return c.write(os.path.join(c.folder(c.word(6)), "audit.jsonl"), "\n".join(json.dumps(r) for r in rows) + "\n")


def _refused_count(c: Context) -> Task:
    ask = "how many of the actions in the audit log {file} did the gate refuse"
    refused = c.rng.randint(1, 5)
    path = _log(c, c.rng.randint(4, 9), refused)
    return Task(ask, {"file": path}, [Num(refused, 0.0)], None, 0)


def _total_count(c: Context) -> Task:
    ask = "how many actions are recorded in the audit log {file}"
    ran, refused = c.rng.randint(3, 8), c.rng.randint(1, 4)
    path = _log(c, ran, refused)
    return Task(ask, {"file": path}, [Num(ran + refused, 0.0)], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("audit_refused_count", "count what the gate refused in an audit log", hs, _refused_count, ("blocked actions", "denied commands"), needs=("file_search_text",)),
        Subject("audit_total_count", "count the actions in an audit log", hs, _total_count, ("how much was done", "log entries"), needs=("file_line_count",)),
    ]
