"""Production assembly: the real local models and the real cloud teachers behind the School.

Kept apart from school.py so the engine itself has no idea which models exist. Everything optional degrades to "not available":
no Ollama means no high school session runs; no keys (or JARVIS_SCHOOL_API=off) means college waits, and the report says why.
"""
from __future__ import annotations

import os
from typing import List, Tuple

from . import cloud, local
from .school import School
from .teachers import Teacher, api_teacher, local_models, local_teacher


def _cap(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def build_teachers() -> Tuple[List[Teacher], List[Tuple[str, Teacher, int]], str]:
    """(local teachers smallest first, college teachers [(label, teacher, daily cap)] free one first, why college is limited)."""
    models = [local_teacher(local.ask, m, writes=True) for m in local_models()]
    college: List[Tuple[str, Teacher, int]] = []
    notes: List[str] = []
    if not cloud.why_not():
        label = "gemini:" + os.getenv("JARVIS_GEMINI_MODEL", "gemini-3.5-flash")
        college.append((label, api_teacher(cloud.ask, label), _cap("JARVIS_SCHOOL_GEMINI_DAILY", 30)))
    else:
        notes.append("Gemini: " + cloud.why_not())
    if not cloud.claude_why_not():
        label = "claude:" + os.getenv("JARVIS_SCHOOL_CLAUDE_MODEL", cloud.CLAUDE_MODEL)
        cap = _cap("JARVIS_SCHOOL_CLAUDE_DAILY", 40)
        if cap:
            college.append((label, api_teacher(cloud.ask_claude, label), cap))
    else:
        notes.append("Claude: " + cloud.claude_why_not())
    return models, college, "; ".join(notes)


def build_school(root: str, playbooks=None) -> School:
    models, college, reason = build_teachers()
    return School(os.path.join(root, "school"), playbooks=playbooks, local_teachers=models, api_teachers=college,
                  api_unavailable=reason)
