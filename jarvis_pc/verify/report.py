"""Task report. No checks means no proof, and no proof means FAIL."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .checks import CheckResult


@dataclass
class Report:
    task: str
    checks: List[CheckResult] = field(default_factory=list)
    changes: List[str] = field(default_factory=list)  # journal entry ids
    undo: str = "nothing changed"
    notes: List[str] = field(default_factory=list)

    @property
    def result(self) -> str:
        if not self.checks:
            return "FAIL"
        passed = sum(c.passed for c in self.checks)
        if passed == len(self.checks):
            return "PASS"
        return "FAIL" if passed == 0 else "PARTIAL"

    def render(self) -> str:
        lines = [f"Task: {self.task}", f"Result: {self.result}"]
        if not self.checks:
            lines.append("Evidence: none (no verification was run, so this cannot count as done)")
        else:
            lines.append("Evidence:")
            for c in self.checks:
                lines.append(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
        lines.append(f"Changes made: {', '.join(self.changes) if self.changes else 'none'}")
        lines.append(f"Undo: {self.undo}")
        for n in self.notes:
            lines.append(f"Note: {n}")
        return "\n".join(lines)
