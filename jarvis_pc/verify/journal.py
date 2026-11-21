"""Change journal: what JARVIS changed, and how to undo it.

Undo is gated like everything else: file restores stay inside the allowlist,
deleting something JARVIS created needs approval, and command undos run
through the normal permission gate.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..actions.shell import run
from ..guardrails.gate import ApprovalRequest, Guardrails
from ..guardrails.tiers import Tier


@dataclass
class UndoStep:
    kind: str  # restore_file | restore_move | delete_created | delete_tree | command | note
    path: Optional[str] = None
    snapshot: Optional[str] = None
    command: Optional[str] = None
    note: Optional[str] = None


@dataclass
class UndoOutcome:
    entry_id: str
    step: UndoStep
    ok: bool
    detail: str = ""


class ChangeJournal:
    def __init__(self, path: str, snapshot_dir: str):
        self.path = Path(path)
        self.snapshot_dir = Path(snapshot_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- recording -------------------------------------------------------------
    def snapshot_file(self, path: str) -> UndoStep:
        """Call BEFORE changing a file. Returns the undo step to pass to record()."""
        if os.path.isfile(path):
            dest = self.snapshot_dir / f"{uuid.uuid4().hex[:8]}-{os.path.basename(path)}"
            shutil.copy2(path, dest)
            return UndoStep("restore_file", path=path, snapshot=str(dest))
        return UndoStep("delete_created", path=path)

    def _append(self, rec: Dict[str, Any]) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    def log_event(self, description: str, **fields: Any) -> None:
        """Log something that cannot be undone (for example a permanent purge). Never shows up as pending."""
        self._append({"type": "event", "ts": datetime.now(timezone.utc).isoformat(),
                      "description": description, **fields})

    def record(self, description: str, undo: List[UndoStep], decision_id: Optional[str] = None,
               tier: Optional[str] = None, group: Optional[str] = None) -> str:
        eid = uuid.uuid4().hex[:10]
        self._append({
            "type": "change", "id": eid, "ts": datetime.now(timezone.utc).isoformat(),
            "description": description, "decision_id": decision_id, "tier": tier, "group": group,
            "undo": [asdict(s) for s in undo],
        })
        return eid

    # ---- reading ---------------------------------------------------------------
    def entries(self) -> List[Dict[str, Any]]:
        changes: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec["type"] == "change":
                    rec["undone"] = False
                    changes[rec["id"]] = rec
                    order.append(rec["id"])
                elif rec["type"] == "undone" and rec["id"] in changes:
                    changes[rec["id"]]["undone"] = True
        return [changes[i] for i in order]

    def undo_plan(self, n: int = 1, ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        pending = [e for e in self.entries() if not e["undone"]]
        if ids is not None:
            pending = [e for e in pending if e["id"] in set(ids)]
            return list(reversed(pending))
        return list(reversed(pending))[:n]

    def last_group_ids(self) -> List[str]:
        """Ids of the newest not-yet-undone change plus every pending change in its group.

        One install writes several entries (download, extract, ...) under one group id, so
        "undo the last thing" must revert all of them. An entry with no group is its own group.
        """
        pending = [e for e in self.entries() if not e["undone"]]
        if not pending:
            return []
        group = pending[-1].get("group")
        if not group:
            return [pending[-1]["id"]]
        return [e["id"] for e in pending if e.get("group") == group]

    def undo_last_group(self, guard: Guardrails) -> List[UndoOutcome]:
        """Undo the whole most recent install/change group (newest entry first). Same gate as undo()."""
        return self.undo(guard, ids=self.last_group_ids())

    # ---- undoing ---------------------------------------------------------------
    def undo(self, guard: Guardrails, n: int = 1, ids: Optional[Sequence[str]] = None) -> List[UndoOutcome]:
        outcomes: List[UndoOutcome] = []
        for entry in self.undo_plan(n, ids):
            all_ok = True
            for raw in reversed(entry["undo"]):
                guard.killswitch.check()
                step = UndoStep(**raw)
                ok, detail = self._do_step(guard, step)
                guard.audit.record("undo_step", entry=entry["id"], kind=step.kind, path=step.path,
                                   command=step.command, ok=ok, detail=detail)
                outcomes.append(UndoOutcome(entry["id"], step, ok, detail))
                all_ok = all_ok and ok
            if all_ok:
                self._append({"type": "undone", "id": entry["id"],
                              "ts": datetime.now(timezone.utc).isoformat()})
        return outcomes

    @staticmethod
    def _do_step(guard: Guardrails, step: UndoStep) -> "tuple[bool, str]":
        if step.kind == "restore_file":
            if not step.path or not step.snapshot:
                return False, "incomplete step"
            if not guard.allowlist.is_allowed(step.path):
                return False, "outside allowed folders; ask the user"
            if not os.path.isfile(step.snapshot):
                return False, "snapshot file is missing"
            shutil.copy2(step.snapshot, step.path)
            return True, "restored"
        if step.kind == "restore_move":
            # Undo of "moved path -> snapshot (quarantine)": move it back. Reversible, so no prompt,
            # but both ends must be inside the allowlist and nothing is ever overwritten.
            if not step.path or not step.snapshot:
                return False, "incomplete step"
            if not (guard.allowlist.is_allowed(step.path) and guard.allowlist.is_allowed(step.snapshot)):
                return False, "outside allowed folders; ask the user"
            if os.path.lexists(step.path):
                return False, "original location is occupied; not overwriting"
            if not os.path.lexists(step.snapshot):
                return False, "quarantined copy is missing (purged?)"
            os.makedirs(os.path.dirname(step.path) or ".", exist_ok=True)
            src_parent = os.path.dirname(step.snapshot)
            shutil.move(step.snapshot, step.path)
            # tidy the now-empty quarantine batch folders; os.rmdir only removes empty dirs, never an allowlist root
            roots = {os.path.normcase(os.path.realpath(r)) for r in guard.allowlist.roots}
            d = src_parent
            while d and os.path.normcase(os.path.realpath(d)) not in roots and guard.allowlist.is_allowed(d):
                try:
                    os.rmdir(d)
                except OSError:
                    break
                d = os.path.dirname(d)
            return True, "moved back"
        if step.kind == "delete_created":
            if not step.path:
                return False, "incomplete step"
            if not guard.allowlist.is_allowed(step.path):
                return False, "outside allowed folders; ask the user"
            if not os.path.exists(step.path):
                return True, "already gone"
            if guard.approver is None:
                return False, "no approver configured (fail closed)"
            req = ApprovalRequest(f"delete {step.path}", Tier.T2,
                                  ["undo: remove something JARVIS created"], "undo", None)
            try:
                approved = bool(guard.approver(req))
            except Exception as e:  # noqa: BLE001
                return False, f"approver error: {e}"
            if not approved:
                return False, "not approved"
            if os.path.isdir(step.path):
                os.rmdir(step.path)  # only empty folders; anything else stays and is reported
            else:
                os.remove(step.path)
            return True, "removed"
        if step.kind == "delete_tree":
            if not step.path:
                return False, "incomplete step"
            if not guard.allowlist.is_allowed(step.path):
                return False, "outside allowed folders; ask the user"
            if not os.path.exists(step.path):
                return True, "already gone"
            if any(os.path.normcase(os.path.realpath(step.path)) == os.path.normcase(os.path.realpath(r))
                   for r in guard.allowlist.roots):
                return False, "refusing to delete an allowlist root"
            if guard.approver is None:
                return False, "no approver configured (fail closed)"
            req = ApprovalRequest(f"delete folder {step.path} and everything in it", Tier.T2,
                                  ["undo: remove a folder JARVIS created"], "undo", None)
            try:
                approved = bool(guard.approver(req))
            except Exception as e:  # noqa: BLE001
                return False, f"approver error: {e}"
            if not approved:
                return False, "not approved"
            if os.path.isdir(step.path) and not os.path.islink(step.path):
                shutil.rmtree(step.path)
            else:
                os.remove(step.path)
            return True, "removed"
        if step.kind == "command":
            if not step.command:
                return False, "incomplete step"
            res = run(guard, step.command, intent="undo")
            if not res.ran:
                return False, "blocked by guardrails: " + "; ".join(res.decision.reasons[-2:])
            return res.ok, f"exit {res.exit_code}"
        if step.kind == "note":
            return False, f"manual step: {step.note}"
        return False, f"unknown step kind {step.kind}"
