"""Turn a symptom into a read-only investigation: what changed, gather evidence, rank likely causes.

Method (from skills/troubleshoot): describe the symptom, check what changed, gather evidence cheapest
first, report ranked causes with a next step, and let the caller re-run the same checks to verify.
This module only reads. Fixes go through the gated skills (install, uninstall, and later the maintenance
changes), so a diagnosis can never change the PC.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List

from ..guardrails.killswitch import KillSwitchTriggered
from .collectors import Diagnostics
from .findings import DiagnosisReport, Finding

# symptom keywords -> checks, listed cheapest / least invasive first within each topic
_ROUTES = [
    (r"wi-?fi|internet|network|dns|connect|offline|ping|website|browser can't|no signal", ["network"]),
    (r"slow|lag|sluggish|performance|boot|startup|start up|memory|\bram\b|fan|hot|freez|hang|unresponsive",
     ["memory", "disk", "startup", "events"]),
    (r"crash|blue ?screen|bsod|restart|reboot|shut ?down|kernel|stopped working|driver", ["crashes", "events", "drivers"]),
    (r"path|not recognized|command not found|can't find (python|node|git)|environment", ["path"]),
    (r"disk|space|full|storage", ["disk"]),
    (r"update|upgrade|outdated|old version", ["updates", "restore"]),
]
DEFAULT_CHECKS = ["disk", "memory", "events", "crashes", "network", "restore"]
ALL_CHECKS = ["disk", "memory", "startup", "network", "events", "crashes", "path", "restore", "updates", "drivers"]


def plan_checks(symptom: str) -> List[str]:
    """Pick the checks for a symptom, in order, without duplicates. Unknown symptoms get a quick general check."""
    s = (symptom or "").lower()
    names: List[str] = []
    for pattern, checks in _ROUTES:
        if re.search(pattern, s):
            names.extend(c for c in checks if c not in names)
    return names or list(DEFAULT_CHECKS)


def run_checks(diag: Diagnostics, names: List[str], symptom: str = "") -> DiagnosisReport:
    report = DiagnosisReport(symptom)
    unknown = [n for n in names if not hasattr(diag, n) or n.startswith("_")]
    if unknown:
        report.notes.append("skipped unknown checks: " + ", ".join(unknown))
    ordered = ["changes"] + [n for n in names if n not in unknown]  # "what changed" first, always
    for name in ordered:
        if name == "changes" and diag.journal is None:
            continue
        try:
            report.findings.extend(getattr(diag, name)())
            report.checked.append(name)
        except KillSwitchTriggered:
            raise  # the user hit stop: never swallow it
        except Exception as e:  # noqa: BLE001
            report.findings.append(Finding("warning", name, f"check '{name}' crashed", f"{type(e).__name__}: {e}",
                                           check_error=True))
            report.checked.append(name)
    return report


def diagnose(diag: Diagnostics, symptom: str = "") -> DiagnosisReport:
    return run_checks(diag, plan_checks(symptom), symptom)
