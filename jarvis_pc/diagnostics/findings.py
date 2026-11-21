"""Findings and the diagnosis report. Read-only: these describe problems, they never fix them."""
from __future__ import annotations

from ..text import plural

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

SEVERITIES = ("critical", "warning", "info", "ok")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass
class Finding:
    severity: str  # critical | warning | info | ok
    area: str  # disk | network | events | crashes | startup | path | memory | updates | drivers | restore | changes
    title: str
    detail: str = ""
    evidence: str = ""
    hint: str = ""  # suggested next step, as text. Fixing goes through the gated skills, never from here.
    check_error: bool = False  # True when the check itself could not run (blocked, timed out, bad output)

    def key(self) -> str:
        return f"{self.area}:{self.title}"


_DATE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")


def parse_ps_date(value: Any) -> Optional[datetime]:
    """Windows PowerShell 5.1 writes dates as /Date(ms)/; PowerShell 7 writes ISO text. Returns UTC or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    m = _DATE.search(str(value))
    if m:
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=int(m.group(1)))
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_json_rows(text: str) -> List[Dict[str, Any]]:
    """ConvertTo-Json gives nothing for no rows, an object for one row, a list for several."""
    text = (text or "").strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        return [data]
    return [r for r in data if isinstance(r, dict)]


@dataclass
class DiagnosisReport:
    symptom: str
    findings: List[Finding] = field(default_factory=list)
    checked: List[str] = field(default_factory=list)  # collectors that ran
    notes: List[str] = field(default_factory=list)

    @property
    def problems(self) -> List[Finding]:
        return sorted((f for f in self.findings if f.severity in ("critical", "warning")),
                      key=lambda f: _RANK[f.severity])

    @property
    def result(self) -> str:
        """No checks, no proof. If every check failed to run the diagnosis is a FAIL, not a clean bill of health."""
        ran = [f for f in self.findings if not f.check_error]
        if not self.checked or not ran:
            return "FAIL"
        if any(f.check_error for f in self.findings):
            return "PARTIAL"
        return "PASS"

    def render(self) -> str:
        lines = [f"Diagnosis: {self.symptom or '(general health check)'}", f"Result: {self.result} "
                 f"({plural(len(self.checked), 'check')}: {', '.join(self.checked) or 'none'})"]
        changes = [f for f in self.findings if f.area == "changes"]
        if changes:
            lines.append("What changed recently:")
            lines.extend(f"  - {f.title}: {f.detail}" for f in changes)
        probs = self.problems
        if probs:
            lines.append("Likely causes, most serious first:")
            for i, f in enumerate(probs, 1):
                lines.append(f"  {i}. [{f.severity.upper()}] {f.title}")
                if f.detail:
                    lines.append(f"     {f.detail}")
                if f.hint:
                    lines.append(f"     next step: {f.hint}")
        else:
            lines.append("No problems found by these checks.")
        errs = [f for f in self.findings if f.check_error]
        if errs:
            lines.append("Checks that could not run (so these areas are unproven):")
            lines.extend(f"  - {f.area}: {f.detail or f.title}" for f in errs)
        oks = [f for f in self.findings if f.severity in ("ok", "info") and f.area != "changes" and not f.check_error]
        if oks:
            lines.append("Looked fine:")
            lines.extend(f"  - {f.title}" + (f": {f.detail}" if f.detail else "") for f in oks)
        lines.extend(f"Note: {n}" for n in self.notes)
        return "\n".join(lines)


def compare(before: DiagnosisReport, after: DiagnosisReport) -> Dict[str, List[Finding]]:
    """Verify step: did the symptom go away? Compares problem findings between two runs."""
    b = {f.key(): f for f in before.problems}
    a = {f.key(): f for f in after.problems}
    return {
        "resolved": [b[k] for k in b if k not in a],
        "remaining": [a[k] for k in a if k in b],
        "new": [a[k] for k in a if k not in b],
    }
