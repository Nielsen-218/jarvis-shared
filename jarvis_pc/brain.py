"""The adapter that lets JARVIS's brain use these skills without weakening the gate.

Read-only skills (diagnose, find files, find duplicates, backup) just answer. Anything that installs, removes, deletes
or undoes is TWO-PHASE:

  prepare_*()  builds the plan (read-only, runs the gate in dry-run) and returns a Prepared with
               - a `summary` that is exactly what the person is shown and asked to approve,
               - a `key` that changes if the plan changes, so a yes can never apply to a different plan,
               - a `run()` that executes with a ScopedApprover: it approves only the prompts that plan declared.
  The caller (tools/pcskills.py) records the question with core.approvals; only the person's next real message can
  approve it, and the model cannot approve for them.

Nothing here calls a model. Learned playbooks are offline too (see jarvis_pc.learning).
"""
from __future__ import annotations

from .text import plural

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from .actions.shell import run
from .diagnostics import Diagnostics, default_path_reader, diagnose, plan_checks
from .files import FileTools, find_duplicates, find_files
from .guardrails import Guardrails, ScopedApprover
from .install import InstallPipeline
from .learning import PlaybookStore
from .uninstall import UninstallPipeline
from .verify import ChangeJournal

KNOWN = ["ripgrep", "jq", "git", "7zip", "python", "nodejs", "curl", "wget", "fzf", "bat", "fd"]
SUMMARY_MAX = 1600


@dataclass
class Prepared:
    key: str
    summary: str
    blocked: Optional[str]
    run: Callable[[], str]
    allows_direct: bool = False  # True only for reversible actions the person can request outright ("uninstall X")
    plan: Any = None  # the plan the person was shown (kept for inspection and tests)
    scope: Any = ()  # the exact gate prompts that plan declared; run() approves nothing else


def _key(kind: str, text: str) -> str:
    return f"pc-{kind}:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _clip(text: str) -> str:
    return text if len(text) <= SUMMARY_MAX else text[:SUMMARY_MAX] + "\n..."


class PCSkills:
    def __init__(self, root: str, *, github: Any = None, downloader: Optional[Callable] = None,
                 scanner: Optional[Callable] = None, run_fn: Callable = run, pre_change: Optional[Callable] = None,
                 path_reader: Callable[[], Dict[str, str]] = default_path_reader, platform: str = "windows",
                 known_names: Iterable[str] = KNOWN):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.apps = os.path.join(self.root, "apps")
        self.quarantine = os.path.join(self.root, "quarantine")
        self.github, self.downloader, self.scanner = github, downloader, scanner
        self.run_fn, self.pre_change, self.path_reader = run_fn, pre_change, path_reader
        self.platform, self.known = platform, list(known_names)
        self.journal = ChangeJournal(os.path.join(self.root, "journal.jsonl"), os.path.join(self.root, "snapshots"))
        self.playbooks = PlaybookStore(os.path.join(self.root, "playbooks.jsonl"))

    # ------------------------------------------------------------------ guards
    def _guard(self, approver: Optional[Callable] = None, dry_run: bool = False) -> Guardrails:
        return Guardrails(allowlist_roots=[self.root], audit_path=os.path.join(self.root, "audit.jsonl"),
                          approver=approver, pre_change=self.pre_change if not dry_run else None, dry_run=dry_run)

    def _install_pipe(self, guard: Guardrails) -> InstallPipeline:
        kw: Dict[str, Any] = {}
        if self.github is not None:
            kw["github"] = self.github
        if self.downloader is not None:
            kw["downloader"] = self.downloader
        if self.scanner is not None:
            kw["scanner"] = self.scanner
        return InstallPipeline(guard, self.journal, apps_dir=self.apps, platform=self.platform, known_names=self.known, **kw)

    def _un_pipe(self, guard: Guardrails) -> UninstallPipeline:
        return UninstallPipeline(guard, self.journal, apps_dir=self.apps, quarantine_dir=self.quarantine, run_fn=self.run_fn)

    # ------------------------------------------------------------------ read-only skills (no approval needed)
    def diagnose(self, symptom: str = "", everything: bool = False) -> str:
        from .diagnostics import ALL_CHECKS, run_checks
        d = Diagnostics(self._guard(), run_fn=self.run_fn, journal=self.journal, path_reader=self.path_reader)
        rep = run_checks(d, ALL_CHECKS, symptom) if everything else diagnose(d, symptom)
        return rep.render()

    @staticmethod
    def find_files(folder: str, pattern: str = "*") -> str:
        hits = find_files(folder, pattern, max_results=40)
        return ("\n".join(hits) + f"\n{len(hits)} match(es) shown") if hits else "no matches"

    @staticmethod
    def find_duplicates(folder: str) -> str:
        groups = find_duplicates(folder)
        if not groups:
            return "no duplicate files found"
        lines = [f"{g.wasted / 1048576:.1f} MB wasted by {len(g.paths)} copies: " + "; ".join(g.paths[:4]) for g in groups[:10]]
        return "\n".join(lines) + f"\n{plural(len(groups), 'group')}, {sum(g.wasted for g in groups) / 1048576:.1f} MB reclaimable. Nothing was deleted."

    def backup(self, source: str) -> str:
        r = FileTools(self._guard(), self.journal).backup(source, os.path.join(self.root, "backups"))
        return r.report.render()

    def restore_test(self, backup_path: str) -> str:
        return FileTools(self._guard(), self.journal).restore_test(backup_path, os.path.join(self.root, "scratch")).render()

    # ------------------------------------------------------------------ two-phase skills
    def prepare_install(self, source: str) -> Prepared:
        source = (source or "").strip()
        dry = self._install_pipe(self._guard(dry_run=True))
        try:
            plan = dry.plan_github(source) if "github.com" in source.lower() else dry.plan_winget(source)
        except Exception as e:  # noqa: BLE001
            return Prepared("", "", f"I could not plan that install: {type(e).__name__}: {e}", lambda: "")
        text = plan.render(dry.guard)
        if plan.blocked_reason:
            return Prepared("", "", f"I won't install that: {plan.blocked_reason}", lambda: "")
        exact = {f"install {plan.title}"}
        for s in plan.steps:
            if s.kind == "download":
                exact.add(f"download {os.path.basename(s.dest or '')} from {s.url}")

        def go() -> str:
            appr = ScopedApprover(exact=exact)
            res = self._install_pipe(self._guard(appr)).execute(plan)
            out = res.report.render()
            if appr.refused:
                out += "\nRefused (not part of the approved plan): " + "; ".join(appr.refused)
            return out
        return Prepared(_key("install", text), _clip(text), None, go, plan=plan, scope=tuple(sorted(exact)))

    def prepare_uninstall(self, target: str, winget: bool = True) -> Prepared:
        """winget=False restricts this to apps JARVIS itself installed (the brain tool uses that: the existing
        uninstall_program already handles ordinary Windows programs and winget packages)."""
        target = (target or "").strip()
        pipe = self._un_pipe(self._guard(dry_run=True))
        plan = pipe.plan_uninstall(target)  # an app JARVIS installed into its own apps folder comes first
        if winget and plan.blocked_reason and self._looks_like_winget_id(target):
            plan = pipe.plan_winget(target)  # otherwise a winget package id such as Git.Git
        if plan.blocked_reason:
            return Prepared("", "", f"I won't do that: {plan.blocked_reason}", lambda: "")
        text = plan.render()

        def go() -> str:
            appr = ScopedApprover(exact={plan.title})
            res = self._un_pipe(self._guard(appr)).execute(plan)
            return res.report.render() + (("\nRefused: " + "; ".join(appr.refused)) if appr.refused else "")
        return Prepared(_key("uninstall", text), _clip(text), None, go, allows_direct=True, plan=plan, scope=(plan.title,))

    @staticmethod
    def _looks_like_winget_id(t: str) -> bool:
        parts = t.split(".")
        return len(parts) >= 2 and all(p for p in parts) and t[0].isalpha()

    def prepare_cleanup(self, min_age_days: float = 0.0) -> Prepared:
        plan = self._un_pipe(self._guard(dry_run=True)).plan_cleanup(min_age_days)
        if plan.blocked_reason or plan.empty:
            return Prepared("", "", plan.blocked_reason or "Nothing to clean up.", lambda: "")
        text = plan.render()

        def go() -> str:
            appr = ScopedApprover(exact={plan.title})
            return self._un_pipe(self._guard(appr)).execute(plan).report.render()
        return Prepared(_key("cleanup", text), _clip(text), None, go, allows_direct=True)

    def prepare_purge(self, older_than_days: float = 0.0) -> Prepared:
        plan = self._un_pipe(self._guard(dry_run=True)).plan_purge(older_than_days)
        if plan.blocked_reason or plan.empty:
            return Prepared("", "", plan.blocked_reason or "Nothing in quarantine to purge.", lambda: "")
        text = plan.render()

        def go() -> str:
            appr = ScopedApprover(prefixes=[f"permanently delete {p.src} " for p in plan.purge])
            return self._un_pipe(self._guard(appr)).execute(plan).report.render()
        return Prepared(_key("purge", text), _clip(text), None, go, plan=plan,  # never "direct": it cannot be undone
                        scope=tuple(f"permanently delete {p.src} " for p in plan.purge))

    def prepare_undo(self) -> Prepared:
        ids = self.journal.last_group_ids()
        if not ids:
            return Prepared("", "", "There is nothing to undo.", lambda: "")
        entries = self.journal.undo_plan(ids=ids)
        exact, lines = set(), []
        for e in entries:
            lines.append(f"- {e['description']}")
            for u in e["undo"]:
                if u["kind"] == "delete_tree":
                    exact.add(f"delete folder {u['path']} and everything in it")
                elif u["kind"] == "delete_created":
                    exact.add(f"delete {u['path']}")
        text = "UNDO the last change:\n" + "\n".join(lines)

        def go() -> str:
            appr = ScopedApprover(exact=exact)
            outs = self.journal.undo_last_group(self._guard(appr))
            res = "\n".join(f"[{'ok' if o.ok else 'FAILED'}] {o.step.kind} {o.step.path or o.step.command}: {o.detail}" for o in outs)
            return res + (("\nRefused: " + "; ".join(appr.refused)) if appr.refused else "")
        return Prepared(_key("undo", text + "|" + ",".join(ids)), text, None, go)

    def prepare_guide(self, goal: str, text: str) -> Prepared:
        """Instructions from outside (a pasted ChatGPT/Claude answer, README, tutorial): read as untrusted text, checked against
        the PC, shown as a tiered plan; run only on the owner's yes to exactly that plan (jarvis_pc.guide)."""
        from . import guide
        dry = self._guard(dry_run=True)

        def installed(package_id: str) -> bool:        # T0 lookup; dry-run lets read-only commands through
            try:
                res = self.run_fn(dry, f"winget list --id {package_id} -e", intent="guide: look at the PC first", timeout=60)
                return bool(res.ran and res.ok and package_id.lower() in (res.stdout or "").lower())
            except Exception:  # noqa: BLE001
                return False
        p = guide.plan((goal or "").strip() or guide.goal_of(text), text, dry, installed=installed)
        shown = p.render()
        if p.blocked:
            return Prepared("", "", shown, lambda: "")
        exact = {s.command for s, t in zip(p.steps, p.tiers) if t == "T2"}

        def go() -> str:
            appr = ScopedApprover(exact=exact)
            report, _book = guide.follow(p, self._guard(appr), run_fn=self.run_fn, store=self.playbooks)
            out = report.render()
            if appr.refused:
                out += "\nRefused (not part of the approved plan): " + "; ".join(appr.refused)
            return out
        return Prepared(_key("guide", shown), _clip(shown), None, go, plan=p, scope=tuple(sorted(exact)))

    def prepare_replay(self, book) -> Prepared:
        """A procedure Jarvis learned before (a followed guide): shown as its steps, replayed only on the owner's yes, through
        the gate with a scoped approver for its T2 lines. No model, no teacher. A guide-learned procedure is re-planned
        against the PC as it is now (jarvis_pc.guide.plan): what is already installed is skipped, what is missing is put in."""
        from .guardrails.tiers import Tier, classify
        if getattr(book, "source", "") == "guide":
            from . import guide
            p = self.prepare_guide(book.title, guide.as_text(book.steps))
            if not p.blocked:
                p = Prepared(p.key, "Learned skill, checked against this PC now:\n" + p.summary, None, p.run, allows_direct=False, plan=p.plan, scope=p.scope)
            return p
        dry = self._guard(dry_run=True)
        lines = [f"Learned skill: {book.title}", f"Steps ({len(book.steps)}):"]
        exact = set()
        for n, s in enumerate(book.steps, 1):
            tier = classify(s.command, None, dry.allowlist).tier
            if tier == Tier.T2:
                exact.add(s.command)
            lines.append(f"  {n}. [{tier.name}] {s.command}" + (f"   (expect: {s.expect})" if s.expect else ""))
        shown = "\n".join(lines)

        def go() -> str:
            appr = ScopedApprover(exact=exact)
            out = self.playbooks.run(book, self._guard(appr), run_fn=self.run_fn).render()
            if appr.refused:
                out += "\nRefused (not part of the approved plan): " + "; ".join(appr.refused)
            return out
        return Prepared(_key("replay", book.id + shown), _clip(shown), None, go, plan=book, scope=tuple(sorted(exact)))

    # ------------------------------------------------------------------ learned skills (offline)
    def solve(self, task: str, teacher: Optional[Callable[[str, str], str]] = None, daily_budget: int = 3,
              params: Optional[Dict[str, str]] = None) -> str:
        """Run a learned playbook if there is one (no model). Otherwise optionally ask the teacher once, and keep
        the answer only if it verifies. Commands still go through the gate; nothing needing approval runs unasked."""
        r = self.playbooks.solve(task, self._guard(), teacher, daily_budget=daily_budget, run_fn=self.run_fn, params=params)
        head = {"learned": "Used a learned skill (no AI call): ", "mastered": "Learned a new skill: "}.get(r.status, f"{r.status}: ")
        body = r.report.render() if r.report else ""
        answer = ("\nOutput:\n" + (r.output if len(r.output) <= 3000 else r.output[:3000] + "\n...")) if getattr(r, "output", "") else ""
        return f"{head}{r.playbook.title if r.playbook else ''}\n{body}{answer}\n{r.note}".strip()
