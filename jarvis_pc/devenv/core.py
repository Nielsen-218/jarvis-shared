"""Dev environment checks: which tools exist, and a backup of PATH before anyone changes it. All version probes are T0."""
from __future__ import annotations

from ..text import plural

import json
import os
import time
from typing import Callable, Dict, List, Optional

from ..actions.shell import run
from ..diagnostics.findings import Finding
from ..guardrails.gate import Guardrails
from ..guardrails.tiers import Tier
from ..verify.checks import CheckResult
from ..verify.journal import ChangeJournal, UndoStep
from ..verify.report import Report

PROBES = {"python": "python --version", "node": "node --version", "git": "git --version", "docker": "docker --version",
          "ssh": "ssh -V", "npm": "npm --version", "pip": "pip --version"}


def tool_versions(guard: Guardrails, run_fn: Callable = run, names: Optional[List[str]] = None) -> List[Finding]:
    out: List[Finding] = []
    for name in names or list(PROBES):
        cmd = PROBES.get(name)
        if not cmd:
            out.append(Finding("warning", "devenv", f"unknown tool {name}", check_error=True))
            continue
        res = run_fn(guard, cmd, intent="devenv: version", timeout=30)
        if not res.ran:
            out.append(Finding("warning", "devenv", f"could not check {name}", "blocked by the gate", check_error=True))
        elif res.ok:
            out.append(Finding("ok", "devenv", f"{name} is installed", " ".join((res.stdout + res.stderr).split())[:120]))
        elif res.error_kind == "not_found":
            out.append(Finding("info", "devenv", f"{name} is not installed or not on PATH",
                               hint="install it through the gated installer, or fix PATH (back it up first)"))
        else:
            out.append(Finding("warning", "devenv", f"{name} did not run cleanly", f"exit {res.exit_code} ({res.error_kind})",
                               check_error=True))
    return out


def backup_path(guard: Guardrails, journal: ChangeJournal, dest_dir: str, path_reader: Callable[[], Dict[str, str]],
                clock: Callable[[], float] = time.time) -> Report:
    """Write the user/machine/process PATH values to a JSON file before any PATH change. T1, journaled, never overwrites."""
    task = "back up PATH"
    dest_dir = os.path.abspath(dest_dir)
    if not guard.allowlist.is_allowed(dest_dir):
        return Report(task, [CheckResult("destination", False, f"outside allowed folders: {dest_dir}")])
    path = os.path.join(dest_dir, "path-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(clock())) + ".json")
    if os.path.exists(path):
        return Report(task, [CheckResult("destination", False, "already exists; not overwriting")])
    d = guard.authorize_action(f"write PATH backup {path}", tier=Tier.T1, reasons=["writes one JSON file"], intent="devenv")
    if not d.allowed:
        return Report(task, [CheckResult("permission", False, "; ".join(d.reasons[-2:]))])
    values = path_reader()
    os.makedirs(dest_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(values, f, indent=2, sort_keys=True)
    eid = journal.record("PATH backup", [UndoStep("delete_created", path=path)], decision_id=d.id, tier="T1")
    with open(path, encoding="utf-8") as f:
        back = json.load(f)
    return Report(task, [CheckResult("backup readable", back == values, f"{plural(len(values), 'scope')} saved to {path}")], [eid],
                  f"journal.undo(guard, ids=['{eid}'])")
