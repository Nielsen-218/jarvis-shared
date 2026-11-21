"""Uninstall and cleanup, through the same gate, journal and verify pattern as installs.

Nothing here deletes anything on the first pass. Removing an app or clearing leftovers MOVES
it into a quarantine folder (inside the allowlist), which is reversible, so the journal can put it
back. Only `plan_purge` deletes for real: it is T2 (always asks), lists what will go and how much
space it frees, and cannot be undone.

Flow: plan (read-only) -> you approve the plan (T2) -> each move passes the gate (T1) and is
journaled under one group id -> verify (gone from the old place, present in quarantine, same file
count) -> report. Any failure restores everything the run moved.
"""
from __future__ import annotations

from ..text import plural

import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Tuple

from ..actions.shell import run
from ..guardrails.gate import Guardrails
from ..guardrails.killswitch import KillSwitchTriggered
from ..guardrails.tiers import Tier
from ..install.plan import SAFE_ID
from ..verify.checks import CheckResult
from ..verify.journal import ChangeJournal, UndoStep
from ..verify.report import Report

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,100}$")
DOWNLOADS = "_downloads"


def _is_link(path: str) -> bool:
    isj = getattr(os.path, "isjunction", lambda p: False)
    return os.path.islink(path) or isj(path)


def _tree_stats(path: str) -> Tuple[int, int]:
    """(file count, total bytes) of a file or folder. Does not follow links."""
    if os.path.isfile(path) or _is_link(path):
        try:
            return 1, os.lstat(path).st_size
        except OSError:
            return 1, 0
    files = size = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            files += 1
            try:
                size += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return files, size


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n} B"


@dataclass
class MoveItem:
    src: str
    dst: str
    files: int
    size: int
    why: str


@dataclass
class UninstallPlan:
    title: str
    method: str  # folder | winget | cleanup | purge
    moves: List[MoveItem] = field(default_factory=list)
    winget_id: Optional[str] = None
    purge: List[MoveItem] = field(default_factory=list)  # src = quarantine batch, dst unused
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    blocked_reason: Optional[str] = None

    @property
    def empty(self) -> bool:
        return not (self.moves or self.winget_id or self.purge)

    def render(self) -> str:
        out = [f"PLAN: {self.title}", f"Method: {self.method}"]
        if self.blocked_reason:
            out.append(f"BLOCKED: {self.blocked_reason}")
        out.append("Steps:")
        n = 0
        for m in self.moves:
            n += 1
            out.append(f"  {n}. [T1] move {m.src} -> quarantine ({plural(m.files, 'file')}, {_human(m.size)}): {m.why}")
        if self.winget_id:
            n += 1
            out.append(f"  {n}. [T1] winget uninstall --id {self.winget_id} --exact --silent")
        for p in self.purge:
            n += 1
            out.append(f"  {n}. [T2] PERMANENTLY delete {p.src} ({plural(p.files, 'file')}, {_human(p.size)}): {p.why}")
        if self.purge:
            out.append(f"Frees {_human(sum(p.size for p in self.purge))}. This cannot be undone.")
        out.extend(f"Warning: {w}" for w in self.warnings)
        out.extend(f"Note: {x}" for x in self.notes)
        return "\n".join(out)


@dataclass
class UninstallResult:
    report: Report
    executed: bool
    rolled_back: bool = False
    halted: bool = False
    plan_text: str = ""
    pending_undo: List[str] = field(default_factory=list)


class UninstallPipeline:
    def __init__(self, guard: Guardrails, journal: ChangeJournal, *, apps_dir: str,
                 quarantine_dir: Optional[str] = None, run_fn: Callable = run,
                 keep_days: float = 0.0):
        self.guard = guard
        self.journal = journal
        self.apps_dir = os.path.abspath(apps_dir)
        self.quarantine = os.path.abspath(quarantine_dir or os.path.join(os.path.dirname(self.apps_dir), "quarantine"))
        self.run_fn = run_fn
        self.keep_days = keep_days  # default age floor for cleanup candidates (0 = any age)

    # ------------------------------------------------------------------ read-only
    def inventory(self) -> List[MoveItem]:
        """T0: what is installed under apps_dir, with sizes. Changes nothing."""
        items: List[MoveItem] = []
        if not os.path.isdir(self.apps_dir):
            return items
        for name in sorted(os.listdir(self.apps_dir)):
            p = os.path.join(self.apps_dir, name)
            files, size = _tree_stats(p)
            items.append(MoveItem(p, "", files, size, "downloads cache" if name == DOWNLOADS else "installed app"))
        return items

    def _new_batch(self) -> str:
        return os.path.join(self.quarantine, time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4])

    def _movable(self, path: str) -> Optional[str]:
        """Return a reason this path must NOT be moved, or None if it is safe to move."""
        if not self.guard.allowlist.is_allowed(path):
            return "outside the allowed folders"
        if not self.guard.allowlist.is_allowed(self.quarantine):
            return "quarantine folder is outside the allowed folders"
        if _is_link(path):
            return "is a link/junction; refusing to follow it"
        rp = os.path.normcase(os.path.realpath(path))
        for root in list(self.guard.allowlist.roots) + [self.apps_dir, self.quarantine]:
            if rp == os.path.normcase(os.path.realpath(root)):
                return "is a protected root folder"
        return None

    def _downloads_for(self, stem: str) -> List[str]:
        dl = os.path.join(self.apps_dir, DOWNLOADS)
        if not os.path.isdir(dl):
            return []
        return [os.path.join(dl, f) for f in sorted(os.listdir(dl))
                if f.lower().startswith(stem.lower()) and os.path.isfile(os.path.join(dl, f))]

    # ------------------------------------------------------------------ planning
    def plan_uninstall(self, name: str) -> UninstallPlan:
        plan = UninstallPlan(f"uninstall {name}", "folder")
        if not SAFE_NAME.match(name) or name.lower() == DOWNLOADS:
            plan.blocked_reason = "not a valid app name"
            return plan
        if not os.path.isdir(self.apps_dir):
            plan.blocked_reason = f"{self.apps_dir} does not exist"
            return plan
        folders = [f for f in os.listdir(self.apps_dir)
                   if f != DOWNLOADS and os.path.isdir(os.path.join(self.apps_dir, f))]
        exact = [f for f in folders if f.lower() == name.lower()]
        matches = exact or [f for f in folders if f.lower().startswith(name.lower() + "-")]
        if not matches:
            plan.blocked_reason = f"nothing named {name!r} under {self.apps_dir}"
            return plan
        if len(matches) > 1:
            plan.blocked_reason = "more than one match, name one exactly: " + ", ".join(sorted(matches))
            return plan
        folder = os.path.join(self.apps_dir, matches[0])
        why = self._movable(folder)
        if why:
            plan.blocked_reason = f"{folder} {why}"
            return plan
        batch = self._new_batch()
        files, size = _tree_stats(folder)
        plan.moves.append(MoveItem(folder, os.path.join(batch, matches[0]), files, size, "the installed app"))
        for z in self._downloads_for(matches[0]):
            if self._movable(z) is None:
                zf, zs = _tree_stats(z)
                plan.moves.append(MoveItem(z, os.path.join(batch, DOWNLOADS, os.path.basename(z)), zf, zs,
                                           "its downloaded installer"))
        plan.notes.append(f"Nothing is deleted. Everything goes to {batch} and `--undo` puts it back.")
        return plan

    def plan_winget(self, package_id: str) -> UninstallPlan:
        plan = UninstallPlan(f"uninstall {package_id} (winget)", "winget")
        if not SAFE_ID.match(package_id):
            plan.blocked_reason = "not a valid winget package id"
            return plan
        res = self.run_fn(self.guard, f"winget list --id {package_id} --exact --accept-source-agreements",
                          intent="plan: is it installed")
        if not (res.ran and res.ok and package_id.lower() in res.stdout.lower()):
            plan.blocked_reason = "winget does not list that exact package as installed (or winget is unavailable)"
            return plan
        plan.winget_id = package_id
        plan.warnings.append("winget removes the program itself; its own user data may remain and is NOT touched. "
                             "Undo reinstalls the package.")
        return plan

    def plan_cleanup(self, min_age_days: Optional[float] = None) -> UninstallPlan:
        """Leftovers JARVIS itself created: installers already extracted, partial downloads, empty folders."""
        age = self.keep_days if min_age_days is None else min_age_days
        plan = UninstallPlan("clean up leftovers", "cleanup")
        if not os.path.isdir(self.apps_dir):
            plan.blocked_reason = f"{self.apps_dir} does not exist"
            return plan
        now = time.time()
        batch = self._new_batch()
        installed = [f for f in os.listdir(self.apps_dir)
                     if f != DOWNLOADS and os.path.isdir(os.path.join(self.apps_dir, f))]

        def old_enough(p: str) -> bool:
            # age 0 means any age. A file's mtime can sit slightly AHEAD of time.time() on Windows, which made brand-new
            # items look "too young" and randomly dropped them from the plan.
            return age <= 0 or (now - os.path.getmtime(p)) >= age * 86400

        dl = os.path.join(self.apps_dir, DOWNLOADS)
        if os.path.isdir(dl):
            for f in sorted(os.listdir(dl)):
                p = os.path.join(dl, f)
                if not os.path.isfile(p) or self._movable(p) or not old_enough(p):
                    continue
                if f.lower().endswith(".part"):
                    why = "unfinished download"
                elif any(f.lower().startswith(i.lower()) for i in installed):
                    why = "installer for an app that is already extracted"
                else:
                    continue  # an installer with no matching app might still be wanted; leave it
                fc, sz = _tree_stats(p)
                plan.moves.append(MoveItem(p, os.path.join(batch, DOWNLOADS, f), fc, sz, why))
        for f in sorted(installed):
            p = os.path.join(self.apps_dir, f)
            if self._movable(p) is None and old_enough(p) and _tree_stats(p)[0] == 0:
                plan.moves.append(MoveItem(p, os.path.join(batch, f), 0, 0, "empty folder"))
        if plan.empty:
            plan.notes.append("Nothing to clean up.")
        else:
            plan.notes.append(f"Nothing is deleted. Everything goes to {batch} and `--undo` puts it back.")
        return plan

    def plan_purge(self, older_than_days: float = 0.0) -> UninstallPlan:
        """The only step that really deletes: empties quarantine batches. T2, irreversible."""
        plan = UninstallPlan("permanently delete quarantined items", "purge")
        if not os.path.isdir(self.quarantine):
            plan.notes.append("Quarantine is empty.")
            return plan
        if not self.guard.allowlist.is_allowed(self.quarantine):
            plan.blocked_reason = "quarantine folder is outside the allowed folders"
            return plan
        now = time.time()
        for name in sorted(os.listdir(self.quarantine)):
            p = os.path.join(self.quarantine, name)
            if _is_link(p) or not os.path.isdir(p):
                continue
            if older_than_days > 0 and (now - os.path.getmtime(p)) < older_than_days * 86400:
                continue
            fc, sz = _tree_stats(p)
            plan.purge.append(MoveItem(p, "", fc, sz, "quarantine batch " + name))
        if plan.empty:
            plan.notes.append("Nothing in quarantine old enough to purge.")
        plan.warnings.append("Purged items cannot be recovered or undone.")
        return plan

    # ------------------------------------------------------------------ executing
    def execute(self, plan: UninstallPlan) -> UninstallResult:
        text = plan.render()
        if plan.blocked_reason:
            return UninstallResult(Report(plan.title, [CheckResult("plan", False, plan.blocked_reason)]), False,
                                   plan_text=text)
        if plan.empty:
            return UninstallResult(Report(plan.title, [CheckResult("plan", True, "nothing to do")], notes=plan.notes),
                                   False, plan_text=text)
        if plan.method == "purge":
            return self._do_purge(plan, text)  # asks per batch (T2), no separate plan prompt
        reasons = [f"method: {plan.method}"] + plan.warnings
        reasons += [f"{m.src} ({_human(m.size)})" for m in (plan.moves + plan.purge)][:8]
        d = self.guard.authorize_action(plan.title, tier=Tier.T2, reasons=reasons, intent="uninstall")
        if not d.allowed:
            return UninstallResult(Report(plan.title, [CheckResult("plan approval", False, "; ".join(d.reasons[-2:]))]),
                                   False, plan_text=text)
        made: List[str] = []
        checks: List[CheckResult] = []
        group = uuid.uuid4().hex[:10]
        try:
            for m in plan.moves:
                self.guard.killswitch.check()
                ok, c = self._move(m, made, group)
                checks.extend(c)
                if not ok:
                    return self._rollback(plan, made, checks, text)
            if plan.winget_id:
                self.guard.killswitch.check()
                ok, c = self._winget_remove(plan.winget_id, made, group)
                checks.extend(c)
                if not ok:
                    return self._rollback(plan, made, checks, text)
            self.guard.killswitch.check()
            verify = self._verify(plan)
        except KillSwitchTriggered as e:
            checks.append(CheckResult("kill switch", False, f"halted: {e}"))
            rep = Report(plan.title, checks, made, self._undo_hint(made),
                         ["Halted by the kill switch. Nothing was rolled back automatically; review the journal entries."])
            return UninstallResult(rep, True, halted=True, plan_text=text, pending_undo=list(made))
        checks.extend(verify)
        if any(not c.passed for c in verify):
            return self._rollback(plan, made, checks, text)
        return UninstallResult(Report(plan.title, checks, made, self._undo_hint(made), plan.warnings + plan.notes),
                               True, plan_text=text)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _undo_hint(made: Iterable[str]) -> str:
        made = list(made)
        return f"journal.undo(guard, ids={made})" if made else "nothing changed"

    def _move(self, m: MoveItem, made: List[str], group: str) -> Tuple[bool, List[CheckResult]]:
        name = f"move {os.path.basename(m.src)}"
        why = self._movable(m.src)
        if why or not self.guard.allowlist.is_allowed(m.dst):
            return False, [CheckResult(name, False, why or f"destination outside allowed folders: {m.dst}")]
        if os.path.lexists(m.dst):
            return False, [CheckResult(name, False, "quarantine destination already exists")]
        if not os.path.lexists(m.src):
            return False, [CheckResult(name, False, "source vanished since the plan was made")]
        d = self.guard.authorize_action(f"move {m.src} to quarantine {m.dst}", tier=Tier.T1,
                                        reasons=[m.why, "reversible: kept in quarantine"], intent="uninstall: move")
        if not d.allowed:
            return False, [CheckResult(name, False, "blocked: " + "; ".join(d.reasons[-2:]))]
        try:
            os.makedirs(os.path.dirname(m.dst), exist_ok=True)
            shutil.move(m.src, m.dst)
        except OSError as e:
            if os.path.lexists(m.dst) and not os.path.lexists(m.src):  # moved fine, then errored: still record it
                made.append(self.journal.record(name, [UndoStep("restore_move", path=m.src, snapshot=m.dst)],
                                                decision_id=d.id, tier="T1", group=group))
            return False, [CheckResult(name, False, f"move failed: {e} (files in use? close the app first)")]
        made.append(self.journal.record(name, [UndoStep("restore_move", path=m.src, snapshot=m.dst)],
                                        decision_id=d.id, tier="T1", group=group))
        self.guard.record_result(d, ran=True, src=m.src, dst=m.dst)
        return True, [CheckResult(name, True, f"moved to {m.dst}")]

    def _winget_remove(self, pid: str, made: List[str], group: str) -> Tuple[bool, List[CheckResult]]:
        name = f"winget uninstall {pid}"
        res = self.run_fn(self.guard, f"winget uninstall --id {pid} --exact --silent", intent="uninstall: winget")
        if res.ran:
            made.append(self.journal.record(
                name, [UndoStep("command", command=f"winget install --id {pid} --exact --silent "
                                                   "--accept-package-agreements --accept-source-agreements")],
                decision_id=res.decision.id, tier=res.decision.tier.name, group=group))
        if not res.ran:
            return False, [CheckResult(name, False, "blocked: " + "; ".join(res.decision.reasons[-2:]))]
        if not res.ok:
            return False, [CheckResult(name, False, f"exit {res.exit_code} ({res.error_kind})", res.stderr[-400:])]
        return True, [CheckResult(name, True, "ok")]

    def _verify(self, plan: UninstallPlan) -> List[CheckResult]:
        out: List[CheckResult] = []
        for m in plan.moves:
            gone = not os.path.lexists(m.src)
            out.append(CheckResult(f"gone {m.src}", gone, "removed from its old place" if gone else "still there"))
            files = _tree_stats(m.dst)[0] if os.path.lexists(m.dst) else -1
            ok = files == m.files
            out.append(CheckResult(f"quarantined {m.dst}", ok,
                                   f"{plural(files, 'file')}, as planned" if ok else f"{plural(files, 'file')}, expected {m.files}"))
        if plan.winget_id:
            res = self.run_fn(self.guard, f"winget list --id {plan.winget_id} --exact --accept-source-agreements",
                              intent="verify")
            still = bool(res.ran and res.ok and plan.winget_id.lower() in res.stdout.lower())
            out.append(CheckResult(f"winget no longer lists {plan.winget_id}", res.ran and not still,
                                   "still installed" if still else "not listed" if res.ran else "could not check"))
        return out

    def _rollback(self, plan: UninstallPlan, made: List[str], checks: List[CheckResult], text: str) -> UninstallResult:
        outcomes = self.journal.undo(self.guard, ids=made) if made else []
        failed = [o for o in outcomes if not o.ok]
        notes = list(plan.warnings)
        if failed:
            notes.append("Rollback was incomplete: " + "; ".join(
                f"{o.step.kind} {o.step.path or o.step.command}: {o.detail}" for o in failed))
        elif made:
            notes.append("Rolled back: everything this run moved was put back.")
        remaining = [e["id"] for e in self.journal.undo_plan(ids=made)] if made else []
        rep = Report(plan.title, checks, made, self._undo_hint(remaining) if remaining else "nothing left to undo", notes)
        return UninstallResult(rep, True, rolled_back=not failed, plan_text=text, pending_undo=remaining)

    def _do_purge(self, plan: UninstallPlan, text: str) -> UninstallResult:
        checks: List[CheckResult] = []
        for p in plan.purge:
            self.guard.killswitch.check()
            name = f"purge {os.path.basename(p.src)}"
            rp = os.path.normcase(os.path.realpath(p.src))
            qroot = os.path.normcase(os.path.realpath(self.quarantine))
            inside = os.path.dirname(rp) == qroot  # only direct children (batches) of quarantine
            if not inside or _is_link(p.src) or not self.guard.allowlist.is_allowed(p.src):
                checks.append(CheckResult(name, False, "not a quarantine batch; refusing"))
                continue
            d = self.guard.authorize_action(f"permanently delete {p.src} ({_human(p.size)})", tier=Tier.T2,
                                            reasons=["purge: cannot be undone", p.why], intent="uninstall: purge")
            if not d.allowed:
                checks.append(CheckResult(name, False, "not approved: " + "; ".join(d.reasons[-2:])))
                continue
            try:
                shutil.rmtree(p.src)
            except OSError as e:
                checks.append(CheckResult(name, False, f"delete failed: {e}"))
                continue
            self.journal.log_event(f"purged {p.src}", files=p.files, bytes=p.size, decision_id=d.id)
            self.guard.record_result(d, ran=True, purged=p.src, bytes=p.size)
            gone = not os.path.lexists(p.src)
            checks.append(CheckResult(f"gone {p.src}", gone, f"deleted, freed {_human(p.size)}" if gone else "still there"))
        rep = Report(plan.title, checks, [], "cannot be undone (purged)", plan.warnings)
        return UninstallResult(rep, True, plan_text=text)
