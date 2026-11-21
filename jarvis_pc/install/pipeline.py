"""Install pipeline: link or package name in, verified install (or clean rollback) out.

Flow: plan (read-only) -> you approve the plan -> download (asks) -> hash + virus scan
-> install -> verify. Any failure rolls back through the change journal.
Nothing from a README is ever executed; commands are built from the detected build
system, and every step still passes the permission gate.
"""
from __future__ import annotations

import hashlib
import os
import uuid
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from ..actions.shell import make_runner, run
from ..guardrails.gate import Guardrails
from ..guardrails.killswitch import KillSwitchTriggered
from ..guardrails.tiers import Tier
from ..security.defender import windows_scanner
from ..security.hash_check import compare_hash, parse_checksums
from ..security.repo_trust import assess_repo
from ..security.script_review import review_readme
from ..verify.checks import CheckResult, command_check, dir_check, file_check, launch_check
from ..verify.journal import ChangeJournal, UndoStep
from ..verify.report import Report
from .github import GitHubClient, GitHubError, host_allowed, parse_github_url
from .plan import (
    _SILENT_ARGS, InstallPlan, Step, VerifySpec, choose_asset, detect_build_system,
    find_checksum_asset, path_module, quote, safe_filename, source_build_commands, winget_plan,
)

Downloader = Callable[[str, str, int], str]  # (url, dest, max_bytes) -> sha256
Scanner = Callable[[str], List[CheckResult]]


def default_download(url: str, dest: str, max_bytes: int) -> str:
    if not host_allowed(url):
        raise RuntimeError(f"refusing to download from {url}")
    h = hashlib.sha256()
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-pc-installer"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if not host_allowed(r.geturl()):
            raise RuntimeError(f"download redirected to a host that is not allowed: {r.geturl()}")
        size = 0
        with open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    f.close()
                    os.remove(part)
                    raise RuntimeError("download exceeds the size limit")
                h.update(chunk)
                f.write(chunk)
    os.replace(part, dest)
    return h.hexdigest()


@dataclass
class InstallResult:
    report: Report
    executed: bool
    rolled_back: bool = False
    halted: bool = False
    plan_text: str = ""
    pending_undo: List[str] = field(default_factory=list)


class InstallPipeline:
    def __init__(self, guard: Guardrails, journal: ChangeJournal, *, apps_dir: str = r"C:\Jarvis\apps",
                 platform: str = "windows", github: Optional[GitHubClient] = None,
                 downloader: Downloader = default_download, scanner: Optional[Scanner] = None,
                 known_names: Iterable[str] = ()):
        self.guard = guard
        self.journal = journal
        self.apps_dir = apps_dir
        self.platform = platform
        self.pm = path_module(platform)
        self.github = github or GitHubClient()
        self.downloader = downloader
        self.known_names = list(known_names)
        if scanner is None:
            def no_scanner(path: str) -> List[CheckResult]:
                return [CheckResult(f"antivirus scan {path}", False, "no scanner available on this platform")]
            scanner = windows_scanner(make_runner(guard)) if platform == "windows" else no_scanner
        self.scanner = scanner

    # ------------------------------------------------------------------ planning
    def plan_winget(self, package_id: str, verify: Sequence[VerifySpec] = ()) -> InstallPlan:
        plan = winget_plan(package_id, verify)
        show = run(self.guard, f"winget show --id {package_id} --exact --accept-source-agreements",
                   intent="plan: look up package")
        if show.ran and show.ok:
            plan.notes.append("winget lists this package:\n    " + "\n    ".join(show.stdout.strip().splitlines()[:8]))
        else:
            plan.blocked_reason = "winget could not find that exact package id (or winget is unavailable)"
        return plan

    def plan_github(self, spec: str, *, silent_args: Optional[str] = None,
                    verify: Sequence[VerifySpec] = ()) -> InstallPlan:
        owner, repo = parse_github_url(spec)
        meta = self.github.repo(owner, repo)
        releases = [r for r in self.github.releases(owner, repo) if not r.get("draft")]
        readme = self.github.readme(owner, repo)
        trust = assess_repo(meta, has_releases=bool(releases), has_readme=bool(readme),
                            known_names=self.known_names)
        plan = InstallPlan(f"{owner}/{repo}", "unresolved", trust=trust, readme_review=review_readme(readme))
        plan.verify.extend(verify)
        if trust.verdict == "STOP":
            plan.warnings.append("Trust check says STOP. Only continue if you know this repo is the real one.")
        if plan.readme_review.high_risk:
            plan.warnings.append("README has high-risk content. Review it yourself before approving.")

        stable = [r for r in releases if not r.get("prerelease")]
        release = stable[0] if stable else None
        asset = choose_asset(release) if release else None
        dl = self.pm.join(self.apps_dir, "_downloads")
        tag = safe_filename(release.get("tag_name", "latest")) if release else "src"
        if asset and self.platform == "windows":
            fname = safe_filename(asset["name"])
            dest = self.pm.join(dl, fname)
            expected = None
            csum = find_checksum_asset(release, asset["name"])
            if csum:
                try:
                    expected = parse_checksums(self.github.text(csum["browser_download_url"]), asset["name"])
                except GitHubError as e:
                    plan.notes.append(f"could not read published checksums: {e}")
            if expected is None:
                plan.warnings.append("No published checksum found; the file cannot be checked against the publisher's hash.")
            plan.steps.append(Step("download", "download", url=asset["browser_download_url"], dest=dest,
                                   sha256=expected, undo=[UndoStep("delete_created", path=dest)]))
            plan.steps.append(Step("scan download", "scan", path=dest))
            ext = self.pm.splitext(fname)[1].lower()
            if ext == ".zip":
                target = self.pm.join(self.apps_dir, f"{repo}-{tag}")
                plan.method = "release-zip"
                plan.steps.append(Step(
                    "extract", "command", cwd=self.apps_dir,
                    command=f"Expand-Archive -Path {quote(dest)} -DestinationPath {quote(target)}",
                    undo=[UndoStep("delete_tree", path=target)]))
                plan.verify.insert(0, VerifySpec("dir", path=target))
                plan.steps.append(Step("scan extracted files", "scan", path=target))
            elif ext == ".msi":
                plan.method = "release-msi"
                plan.warnings.append("MSI installs system-wide and usually needs admin approval (UAC).")
                plan.steps.append(Step(
                    "install msi", "command", cwd=dl,
                    command=f"msiexec /i {quote(dest)} /qn /norestart",
                    undo=[UndoStep("command", command=f"msiexec /x {quote(dest)} /qn /norestart")]))
            else:
                plan.method = "release-exe"
                if not silent_args:
                    plan.blocked_reason = (f"{fname} is an .exe installer; its silent-install flag is not known. "
                                           "Check the README/installer docs and re-plan with silent_args=...")
                elif not _SILENT_ARGS.match(silent_args):
                    plan.blocked_reason = "silent_args contains unsupported characters"
                else:
                    plan.warnings.append("EXE installers can't be undone automatically; uninstall is a manual step.")
                    plan.steps.append(Step(
                        "run installer", "command", cwd=dl,
                        command=f"Start-Process -FilePath {quote(dest)} -ArgumentList {quote(silent_args)} -Wait",
                        undo=[UndoStep("note", note=f"uninstall {repo} from Settings > Apps")]))
        else:
            files = self.github.root_listing(owner, repo)
            system = detect_build_system(files)
            if system is None:
                plan.blocked_reason = "no Windows release asset and no recognised build system; needs a human look"
                return plan
            plan.method = "source-build"
            target = self.pm.join(self.apps_dir, f"{repo}-src")
            plan.warnings.append("Building from source runs the project's own build code. Higher risk than a signed release.")
            plan.steps.append(Step(
                "clone", "command", cwd=self.apps_dir,
                command=f"git clone --depth 1 https://github.com/{owner}/{repo} {quote(target)}",
                undo=[UndoStep("delete_tree", path=target)]))
            for i, cmd in enumerate(source_build_commands(system, files, self.pm), 1):
                plan.steps.append(Step(f"build {i} ({system})", "command", command=cmd, cwd=target, timeout=1800))
        if not plan.verify:
            plan.warnings.append("No verification defined: the result can be at best PARTIAL until you give a check "
                                 "(for example a --version command).")
        return plan

    # ------------------------------------------------------------------ executing
    def execute(self, plan: InstallPlan) -> InstallResult:
        text = plan.render(self.guard)
        if plan.blocked_reason:
            return InstallResult(Report(plan.title, [CheckResult("plan", False, plan.blocked_reason)]), False,
                                 plan_text=text)
        reasons = [f"method: {plan.method}"]
        if plan.trust:
            reasons.append(f"repo trust {plan.trust.score}/100 ({plan.trust.verdict})")
        reasons.extend(plan.warnings)
        d = self.guard.authorize_action(f"install {plan.title}", tier=Tier.T2, reasons=reasons, intent="install")
        if not d.allowed:
            return InstallResult(Report(plan.title, [CheckResult("plan approval", False, "; ".join(d.reasons[-2:]))]),
                                 False, plan_text=text)

        made: List[str] = []
        group = uuid.uuid4().hex[:10]  # ties this install's journal entries together for undo
        checks: List[CheckResult] = []
        try:
            for step in plan.steps:
                self.guard.killswitch.check()
                ok, new_checks = self._run_step(step, made, group)
                checks.extend(new_checks)
                if not ok:
                    return self._rollback(plan, made, checks, text)
            self.guard.killswitch.check()
            verify_checks = [self._verify(v) for v in plan.verify]
        except KillSwitchTriggered as e:
            checks.append(CheckResult("kill switch", False, f"halted: {e}"))
            rep = Report(plan.title, checks, made, self._undo_hint(made),
                         ["Halted by the kill switch. Nothing was rolled back automatically; review the journal entries."])
            return InstallResult(rep, True, halted=True, plan_text=text, pending_undo=list(made))

        checks.extend(verify_checks)
        if any(not c.passed for c in verify_checks):
            return self._rollback(plan, made, checks, text)
        if not plan.verify:
            checks.append(CheckResult("install verification", False, "no verification defined; install is unproven"))
        return InstallResult(Report(plan.title, checks, made, self._undo_hint(made), plan.warnings), True,
                             plan_text=text)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _undo_hint(made: Sequence[str]) -> str:
        return f"journal.undo(guard, ids={list(made)})" if made else "nothing changed"

    def _run_step(self, step: Step, made: List[str], group: Optional[str] = None) -> Tuple[bool, List[CheckResult]]:
        name = f"step: {step.name}"
        if step.kind == "note":
            return True, []
        if step.kind == "scan":
            results = self.scanner(step.path or "")
            return all(r.passed for r in results), results
        if step.kind == "command":
            res = run(self.guard, step.command or "", cwd=step.cwd, timeout=step.timeout, intent=f"install: {step.name}")
            if res.ran and step.undo:
                made.append(self.journal.record(step.name, step.undo, decision_id=res.decision.id, tier=res.decision.tier.name, group=group))
            if not res.ran:
                return False, [CheckResult(name, False, "blocked: " + "; ".join(res.decision.reasons[-2:]))]
            if not res.ok:
                return False, [CheckResult(name, False, f"exit {res.exit_code} ({res.error_kind})", res.stderr[-400:])]
            return True, [CheckResult(name, True, "ok")]
        if step.kind == "download":
            url, dest = step.url or "", step.dest or ""
            if not host_allowed(url):
                return False, [CheckResult(name, False, f"download host not allowed: {url}")]
            d = self.guard.authorize_action(
                f"download {os.path.basename(dest)} from {url}", tier=Tier.T2,
                reasons=["file download", f"destination: {dest}"], intent="install: download")
            if not d.allowed:
                return False, [CheckResult(name, False, "download not approved: " + "; ".join(d.reasons[-2:]))]
            if not self.guard.allowlist.is_allowed(dest):
                return False, [CheckResult(name, False, f"destination outside allowed folders: {dest}")]
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                actual = self.downloader(url, dest, step.max_bytes)
            except Exception as e:  # noqa: BLE001
                if os.path.exists(dest):
                    made.append(self.journal.record(step.name, step.undo, decision_id=d.id, tier="T2", group=group))
                return False, [CheckResult(name, False, f"download failed: {e}")]
            made.append(self.journal.record(step.name, step.undo, decision_id=d.id, tier="T2", group=group))
            self.guard.record_result(d, ran=True, sha256=actual, dest=dest)
            check = compare_hash(dest, step.sha256)
            return check.passed, [CheckResult(name, True, f"saved {dest}"), check]
        return False, [CheckResult(name, False, f"unknown step kind {step.kind}")]

    def _verify(self, v: VerifySpec) -> CheckResult:
        if v.kind == "command":
            return command_check(self.guard, v.command or "", v.expect, cwd=v.cwd)
        if v.kind == "launch":
            return launch_check(self.guard, v.command or "", cwd=v.cwd, expect_alive=v.expect_alive)
        if v.kind == "file":
            return file_check(v.path or "")
        if v.kind == "dir":
            return dir_check(v.path or "")
        return CheckResult(f"verify {v.kind}", False, "unknown verification kind")

    def _rollback(self, plan: InstallPlan, made: List[str], checks: List[CheckResult], text: str) -> InstallResult:
        outcomes = self.journal.undo(self.guard, ids=made) if made else []
        failed = [o for o in outcomes if not o.ok]
        notes = list(plan.warnings)
        if failed:
            notes.append("Rollback was incomplete: " + "; ".join(f"{o.step.kind} {o.step.path or o.step.command}: {o.detail}"
                                                                for o in failed))
        elif made:
            notes.append("Rolled back: everything this install changed was undone.")
        remaining = [e["id"] for e in self.journal.undo_plan(ids=made)] if made else []
        rep = Report(plan.title, checks, made, self._undo_hint(remaining) if remaining else "nothing left to undo", notes)
        return InstallResult(rep, True, rolled_back=not failed, plan_text=text, pending_undo=remaining)
