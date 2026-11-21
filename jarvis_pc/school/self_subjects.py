"""School subjects for autonomous learning and self-improvement (blueprint area 23): read the school's OWN learning record.

The harness writes a transcript in exactly the shape the real school keeps (`school_transcript.json`: per subject a state, who taught it,
how many tries) with known contents. Jarvis answers questions about what has been learned, who taught it and what is stuck, which is how
it decides what to study next. Read-only; the same commands work on the real transcript.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

from .curriculum import Absent, Context, HIGH_SCHOOL, Num, Subject, Task

TEACHERS = ["local:llama3.2:3b", "local:qwen3:4b-instruct", "gemini:gemini-3.5-flash", "claude:claude-haiku-5-5"]


def _transcript(c: Context, n: int, stuck: int = 0) -> Tuple[str, Dict[str, dict]]:
    names = [f"{c.word(4)}_{c.word(5)}" for _ in range(n)]
    subjects: Dict[str, dict] = {}
    for i, name in enumerate(names):
        state = "stuck" if i < stuck else ("mastered" if i % 3 != 2 else "learning")
        subjects[name] = {"api_tries": 0, "exams": 2 if state == "mastered" else 0, "failures": 0, "last_note": "note",
                          "learned_by": c.rng.choice(TEACHERS), "state": state}
    path = os.path.join(c.folder(c.word(6)), "school_transcript.json")
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump({"api_spend": {}, "subjects": subjects}, f, indent=1, sort_keys=True)
    return path, subjects


def _mastered_count(c: Context) -> Task:
    path, subj = _transcript(c, c.rng.randint(6, 12))
    n = sum(1 for v in subj.values() if v["state"] == "mastered")
    return Task("how many subjects does the school record {file} list as mastered", {"file": path}, [Num(n, 0.0)], None, 0)


def _learned_by(c: Context) -> Task:
    path, subj = _transcript(c, c.rng.randint(5, 9))
    name = c.rng.choice(sorted(subj))
    who = subj[name]["learned_by"]
    decoys = tuple(t for t in TEACHERS if t != who)
    return Task("which teacher taught the subject {subject} according to {file} (the file lists each subject as a block: its name on one line, then 5 lines below it learned_by names the teacher)", {"file": path, "subject": name}, [who, Absent(decoys)], None, 0)


def _stuck(c: Context) -> Task:
    path, subj = _transcript(c, c.rng.randint(6, 10), stuck=1)
    name = next(k for k, v in subj.items() if v["state"] == "stuck")
    others = tuple(k for k in subj if k != name)
    return Task("which subject in the school record {file} is stuck", {"file": path}, [name, Absent(others)], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("self_mastered_count", "count what the school has mastered", hs, _mastered_count, ("learning progress", "mastered subjects"), needs=("file_read",)),
        Subject("self_learned_by", "find who taught a skill", hs, _learned_by, ("teacher record", "who taught it"), needs=("file_read",)),
        Subject("self_stuck_subject", "find the skill that is stuck", hs, _stuck, ("stuck lesson", "what to study next"), needs=("file_read",)),
    ]
