"""The permission gate. Every action asks this before running.

Fail closed: no approver configured means T2 is denied; a crashing approver or
restore-point hook means denied; T3 is denied without ever asking.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .allowlist import PathAllowlist
from .audit import AuditLog
from .killswitch import KillSwitch
from .tiers import Classification, Tier, classify


@dataclass
class ApprovalRequest:
    command: str
    tier: Tier
    reasons: List[str]
    intent: str
    cwd: Optional[str]


@dataclass
class Decision:
    id: str
    command: str
    allowed: bool
    tier: Tier
    reasons: List[str] = field(default_factory=list)
    dry_run: bool = False
    needs_approval: bool = False
    approved: Optional[bool] = None
    plan: str = ""


Approver = Callable[[ApprovalRequest], bool]
PreChange = Callable[[ApprovalRequest], None]


class Guardrails:
    def __init__(
        self,
        allowlist_roots=(),
        audit_path: Optional[str] = None,
        killswitch: Optional[KillSwitch] = None,
        approver: Optional[Approver] = None,
        pre_change: Optional[PreChange] = None,
        dry_run: bool = True,
    ):
        self.allowlist = PathAllowlist(allowlist_roots)
        self.audit = AuditLog(audit_path)
        self.killswitch = killswitch or KillSwitch()
        self.approver = approver
        self.pre_change = pre_change
        self.dry_run = dry_run

    def authorize(self, command: str, *, cwd: Optional[str] = None, intent: str = "") -> Decision:
        self.killswitch.check()
        c: Classification = classify(command, cwd, self.allowlist)
        return self._decide(command, c, cwd, intent)

    def authorize_action(self, description: str, *, tier: Tier = Tier.T2, reasons=(),
                         intent: str = "") -> Decision:
        """Gate a non-shell action (download, install plan, delete) with the same rules."""
        self.killswitch.check()
        return self._decide(description, Classification(tier, list(reasons), []), None, intent)

    def _decide(self, command: str, c: Classification, cwd: Optional[str], intent: str) -> Decision:
        d = Decision(uuid.uuid4().hex[:12], command, False, c.tier, list(c.reasons))
        req = ApprovalRequest(command, c.tier, list(c.reasons), intent, cwd)
        d.plan = (
            f"[{c.tier.name}] {command}\n  cwd: {cwd}\n  why: {'; '.join(c.reasons)}\n"
            f"  asks first: {'yes' if c.tier == Tier.T2 else 'no'}\n"
            f"  restore point: {'yes' if c.tier in (Tier.T1, Tier.T2) else 'no'}"
        )

        if c.tier == Tier.T3:
            d.reasons.append("T3: never allowed")
            return self._finish(d, intent, cwd)

        if self.dry_run and c.tier >= Tier.T1:
            # Dry-run blocks anything that changes the PC. Read-only (T0) lookups still run,
            # because planning needs them (for example `winget show`).
            d.dry_run = True
            d.reasons.append("dry-run: not executed")
            return self._finish(d, intent, cwd)

        if c.tier == Tier.T2:
            d.needs_approval = True
            if self.approver is None:
                d.reasons.append("no approver configured (fail closed)")
                return self._finish(d, intent, cwd)
            try:
                d.approved = bool(self.approver(req))
            except Exception as e:  # noqa: BLE001
                d.approved = False
                d.reasons.append(f"approver error: {e}")
            if not d.approved:
                d.reasons.append("not approved")
                return self._finish(d, intent, cwd)
            self.killswitch.check()  # user may have hit stop during the prompt

        if c.tier in (Tier.T1, Tier.T2) and self.pre_change is not None and intent == "ui-app":
            # A click or keystroke inside one ordinary app window: a Windows restore point (system files and registry) cannot
            # undo it, and waiting for one cost ~10 s per click (measured 2026-11-09). System windows still get one.
            d.reasons.append("no restore point: an action inside one app window")
        elif c.tier in (Tier.T1, Tier.T2) and self.pre_change is not None:
            try:
                self.pre_change(req)
            except Exception as e:  # noqa: BLE001
                d.reasons.append(f"restore point failed: {e}")
                return self._finish(d, intent, cwd)

        d.allowed = True
        return self._finish(d, intent, cwd)

    def _finish(self, d: Decision, intent: str, cwd: Optional[str]) -> Decision:
        self.audit.record(
            "decision", id=d.id, command=d.command, cwd=cwd, intent=intent, tier=d.tier.name,
            allowed=d.allowed, approved=d.approved, dry_run=d.dry_run, reasons=d.reasons,
        )
        return d

    def record_result(self, d: Decision, **fields) -> None:
        self.audit.record("result", id=d.id, command=d.command, **fields)
