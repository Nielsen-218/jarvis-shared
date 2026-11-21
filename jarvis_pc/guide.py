"""Follow instructions that came from outside: a ChatGPT or Claude answer, a README, a tutorial, a forum post.

The text is untrusted reference material, never authority. Jarvis:
  1. READS it: commands come from its code blocks (or, with no fences, from its command-looking lines); prose is dropped,
     prompts ($, PS>, C:\\>) are stripped, "# expect: ..." after a command is that step's check. An instruction that tries
     to talk to an AI, or a step the permission gate never allows (T3), blocks the whole guide.
  2. LOOKS AT THE PC first: a tool the guide installs that is already here is skipped; a command whose program is missing
     gets the matching winget install put in front of it (a fixed table of well-known tools, never a name from the text).
  3. SHOWS the plan with every step's tier. Nothing runs until the owner says yes to exactly that plan (jarvis_pc.brain).
  4. RUNS it through the same gate and shell runner as every other skill, with bounded recovery: a program not found right
     after an install means PATH is stale in this process (refreshed from the registry, retried once); a network error is
     retried once; a permission error stops with the real reason. At most two recoveries.
  5. VERIFIES with a functional check, not an exit code: the guide's own checks, or one derived from what was installed
     (winget list / pip show / npm ls / git rev-parse). With no check possible the result is PARTIAL and nothing is learned.
  6. KEEPS what worked as a learned playbook, so the same job runs offline next time.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .actions.shell import run as shell_run
from .guardrails.gate import Guardrails
from .guardrails.injection import scan_untrusted
from .guardrails.tiers import Tier, classify
from .learning.playbooks import MAX_STEPS, Playbook, PlaybookStep, PlaybookStore, keywords, parse_steps
from .security.script_review import extract_code_blocks, review_script
from .verify.checks import CheckResult
from .verify.report import Report

# Programs a guide may call that Jarvis can put in place with winget when they are missing. A fixed table: the package id
# never comes from the guide's text.
WINGET_FOR = {
    "git": "Git.Git", "node": "OpenJS.NodeJS.LTS", "npm": "OpenJS.NodeJS.LTS", "npx": "OpenJS.NodeJS.LTS",
    "python": "Python.Python.3.12", "pip": "Python.Python.3.12", "cargo": "Rustlang.Rustup", "rustup": "Rustlang.Rustup",
    "7z": "7zip.7zip", "code": "Microsoft.VisualStudioCode", "go": "GoLang.Go", "cmake": "Kitware.CMake",
    "ffmpeg": "Gyan.FFmpeg", "gh": "GitHub.cli", "uv": "astral-sh.uv", "docker": "Docker.DockerDesktop",
}
_PROMPT = re.compile(r"^(?:\$|>|PS(?:\s[^>]*)?>|[A-Za-z]:\\[^>]*>)\s*")
_INTENT = re.compile(r"\b(?:install|set\s*up|setup|follow|guide|instructions?|steps?|run (?:this|these)|do (?:this|these)|"
                     r"here'?s how|tutorial|configure|build)\b", re.I)
_CHECK = re.compile(r"(?:--version|\s-[vV]\b|\s-{1,2}help\b|\bversion\b|^(?:where|which|Get-Command)\b|-c\s+[\"']import\b|\s-e\s+[\"'])")
_WINGET_INSTALL = re.compile(r"^winget\s+install\s+(?:--id\s+|-e\s+|--exact\s+)*([\w.+-]+)", re.I)
_PIP_INSTALL = re.compile(r"^(?:python\s+-m\s+)?pip3?\s+install\s+(?:-U\s+|--upgrade\s+|--user\s+)*([A-Za-z0-9_.-]+)", re.I)
_NPM_INSTALL = re.compile(r"^npm\s+(?:install|i)\s+(?:-g|--global)\s+([@A-Za-z0-9_./-]+)", re.I)
_GIT_CLONE = re.compile(r"^git\s+clone\s+(?:\S+\s+)*?(\S+?)(?:\.git)?(?:\s+(\S+))?\s*$", re.I)
WINGET_INSTALL = "winget install --id {id} -e --accept-source-agreements --accept-package-agreements"


@dataclass
class GuidePlan:
    goal: str
    steps: List[PlaybookStep] = field(default_factory=list)       # what will run now, on THIS PC
    tiers: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    verify: List[PlaybookStep] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    blocked: str = ""
    complete: List[PlaybookStep] = field(default_factory=list)    # the whole procedure, skipped installs included: what is learned

    def render(self) -> str:
        if self.blocked:
            return f"I won't follow that guide: {self.blocked}"
        lines = [f"Goal: {self.goal}", f"Steps ({len(self.steps)}):"]
        for n, (s, t) in enumerate(zip(self.steps, self.tiers), 1):
            lines.append(f"  {n}. [{t}] {s.command}" + (f"   (expect: {s.expect})" if s.expect else ""))
        if self.skipped:
            lines.append("Already here, skipped: " + "; ".join(self.skipped))
        lines.append("Proof it worked: " + ("; ".join(v.command for v in self.verify) if self.verify
                                             else "none could be derived from this guide, so the best result is PARTIAL"))
        for w in self.warnings:
            lines.append("Warning: " + w)
        lines.append("Asks first: " + (", ".join(s.command for s, t in zip(self.steps, self.tiers) if t == "T2") or "nothing"))
        return "\n".join(lines)


def looks_like(text: str) -> bool:
    """A message that carries a guide: a code fence or three or more numbered/bulleted lines, and words that ask to follow
    it. The shape only; whether it is sane is for plan()."""
    text = text or ""
    if len(text) < 40 or "\n" not in text:
        return False
    fenced = "```" in text
    listed = len(re.findall(r"^\s*(?:\d+[.)]|[-*])\s+\S", text, re.M)) >= 3
    return (fenced or listed) and bool(_INTENT.search(text[:300]))


def goal_of(text: str) -> str:
    first = next((ln.strip() for ln in (text or "").splitlines() if ln.strip() and not ln.strip().startswith("```")), "")
    first = re.sub(r"^(?:hey\s+)?jarvis[,:\s]+", "", first, flags=re.I).rstrip(":").strip()
    return (first or "follow the guide")[:120]


def _clean(line: str) -> str:
    line = _PROMPT.sub("", line.strip())
    return line.rstrip(" \\")


def steps_from(text: str, limit: int = MAX_STEPS) -> Tuple[List[PlaybookStep], List[str]]:
    """Commands from the guide's code blocks; without fences, its command-looking lines (learning.parse_steps). Prose and
    comments are dropped. Returns (steps, notes)."""
    blocks = extract_code_blocks(text or "")
    notes: List[str] = []
    if not blocks:
        steps, why = parse_steps(text)
        return steps, why
    steps: List[PlaybookStep] = []
    for lang, code in blocks:
        if lang in ("json", "yaml", "yml", "toml", "ini", "xml", "html", "css", "env", "text", "txt", "diff", "output"):
            notes.append(f"a {lang} block was shown, not run")
            continue
        for raw in code.splitlines():
            line = _clean(raw)
            if not line or line.startswith("#") or line.lower().startswith("rem "):
                continue
            expect = ""
            m = re.search(r"\s+#\s*expect:\s*(.+)$", line, re.I)
            if m:
                expect, line = m.group(1).strip(), line[:m.start()].strip()
            if len(steps) >= limit:
                notes.append(f"more than {limit} steps; the rest were ignored")
                return steps, notes
            steps.append(PlaybookStep(line, expect))
    return steps, notes


def _first_token(command: str) -> str:
    tok = command.strip().split()[0] if command.strip() else ""
    return re.sub(r"\.exe$", "", os.path.basename(tok.strip("\"'")).lower())


def _winget_id(command: str) -> str:
    m = _WINGET_INSTALL.match(command.strip())
    return m.group(1) if m else ""


def derived_check(command: str) -> Optional[PlaybookStep]:
    """A read-only, functional check for what an install step put in place."""
    wid = _winget_id(command)
    if wid:
        return PlaybookStep(f"winget list --id {wid} -e", wid)
    m = _PIP_INSTALL.match(command.strip())
    if m:
        return PlaybookStep(f"pip show {m.group(1)}", "Name:")
    m = _NPM_INSTALL.match(command.strip())
    if m:
        return PlaybookStep(f"npm ls -g {m.group(1)}", m.group(1).split("@")[0].strip("@") or m.group(1))
    m = _GIT_CLONE.match(command.strip())
    if m:
        folder = m.group(2) or os.path.basename(m.group(1).rstrip("/"))
        return PlaybookStep(f"git -C {folder} rev-parse --short HEAD", "")
    return None


# --- reading a REAL README (how-to bench 2026-11-08: 4 of 10 popular READMEs were refused as "prompt injection" and the
# rest planned every install option for every OS at once) -----------------------------------------------------------
_HIDDEN_COMMENT = re.compile(r"<!--.*?-->", re.S)
_INVISIBLE = re.compile("[​-‏‪-‮⁠﻿]")
# Findings that mean the text talks to an AI. A dangerous COMMAND (curl | sh) is not that: it is skipped, never run.
_NOT_AI_DIRECTED = {"pipe-to-shell", "hidden-characters", "hidden-comment-directive"}
_PIPE_TO_SHELL = re.compile(r"\b(curl|wget|iwr|irm)\b[^|\n]{0,200}\|\s*(sudo\s+)?(sh|bash|zsh|iex|powershell|pwsh|python3?)\b", re.I)
_UNIX_ONLY = {"brew", "apt", "apt-get", "aptitude", "dnf", "yum", "pacman", "yay", "paru", "zypper", "emerge", "snap",
              "flatpak", "port", "nix-env", "nix", "pkg", "pkg_add", "xbps-install", "apk", "eopkg", "sudo", "doas",
              "chmod", "chown", "ln", "makepkg", "guix", "conda-forge", "opkg", "mise", "asdf"}
_INSTALLERS = ("winget", "scoop", "choco", "pip", "pip3", "pipx", "uv", "cargo", "npm", "pnpm", "yarn", "go", "dotnet")


def _visible(text: str) -> str:
    """What a person reading the page sees: HTML comments and invisible characters are removed, so nothing hidden can
    ever run (instead of every README with a linter comment or an emoji joiner being refused outright)."""
    return _INVISIBLE.sub("", _HIDDEN_COMMENT.sub("", text))


def _addressed_to_ai(text: str) -> bool:
    for f in scan_untrusted(text).findings:
        if f.rule in _NOT_AI_DIRECTED:
            continue
        if f.rule == "fake-authority":
            line = next((ln for ln in text.splitlines() if f.excerpt.split("\n")[0][:30] in ln), "")
            if line.lstrip().startswith(("#", "*", "-", "[", "|")) or re.search(r"\]\(#", line):
                continue                                  # a section title or a contents link ("### Developer Instructions")
        return True
    return False


def _windows_route(steps: list, warnings: list) -> list:
    """ONE way to install, for THIS PC: Linux/macOS-only lines and download-and-run-unseen lines are dropped (with a note),
    and when the README offers several installers for the same thing, the first available of winget > scoop > choco > pip
    > cargo > npm > go is kept, not all of them."""
    kept, dropped_os, dropped_pipe = [], [], []
    for s in steps:
        cmd = s.command.strip()
        tok = _first_token(cmd)
        if _PIPE_TO_SHELL.search(cmd):
            dropped_pipe.append(cmd)
        elif tok in _UNIX_ONLY:
            dropped_os.append(cmd)
        else:
            kept.append(s)
    if dropped_pipe:
        warnings.append("skipped (downloads a script and runs it unseen): " + "; ".join(dropped_pipe[:3]))
    if dropped_os:
        warnings.append("skipped (Linux/macOS only): " + "; ".join(dropped_os[:4]) + ("…" if len(dropped_os) > 4 else ""))
    order = {t: i for i, t in enumerate(_INSTALLERS)}
    groups: dict = {}
    for s in kept:
        name = _installs_what(s.command)
        if name:
            groups.setdefault(name, []).append(s)
    for name, alts in groups.items():
        if len(alts) < 2:
            continue
        best = min(alts, key=lambda s: (0 if which_cmd(_first_token(s.command)) else 50) + order.get(_first_token(s.command), 99))
        warnings.append(f"the guide offers {len(alts)} ways to install {name}; using: {best.command}")
        kept = [s for s in kept if s is best or s not in alts]
    return kept


def _installs_what(command: str) -> str:
    """'ripgrep' for 'winget install BurntSushi.ripgrep.MSVC', 'scoop install ripgrep', 'cargo install ripgrep'; '' when the
    line is not a package install (so different packages are never treated as alternatives of each other)."""
    tok = _first_token(command)
    if tok not in _INSTALLERS:
        return ""
    m = re.search(r"\b(?:install|add|i|get)\b\s+(.*)$", command, re.I)
    if not m:
        return ""
    args = [a for a in m.group(1).split() if not a.startswith("-")]
    if not args:
        return ""
    name = args[0].strip("\"'").lower()
    if tok == "winget":
        parts = [p for p in name.split(".") if p not in ("msvc", "gnu", "portable", "cli")]
        name = parts[1] if len(parts) > 1 else parts[0]
    name = re.split(r"[\[@=<>]", name)[0].rsplit("/", 1)[-1]
    return re.sub(r"[^a-z0-9]+", "", name)


def which_cmd(tok: str) -> bool:
    return bool(tok in ("winget",) and os.name == "nt") or shutil.which(tok) is not None


def plan(goal: str, text: str, guard: Guardrails, *, which: Callable[[str], Optional[str]] = shutil.which,
         installed: Optional[Callable[[str], bool]] = None) -> GuidePlan:
    """Read, look at the PC, decide the plan. Read-only: `installed(winget_id)` may run a T0 lookup; nothing changes."""
    p = GuidePlan(goal=goal)
    text = _visible(text or "")
    if _addressed_to_ai(text):
        p.blocked = "the text tries to instruct an AI (prompt injection), so none of it is trusted"
        return p
    steps, notes = steps_from(text, limit=400)        # the cap applies AFTER the other OSes' lines are dropped
    p.warnings.extend(notes)
    steps = _windows_route(steps, p.warnings)
    if len(steps) > MAX_STEPS:
        p.warnings.append(f"more than {MAX_STEPS} steps; the rest were ignored")
        steps = steps[:MAX_STEPS]
    if not steps:
        p.blocked = "I found no commands to run in it (paste the guide with its commands, or say what to do in your own words)"
        return p
    for f in review_script("\n".join(s.command for s in steps)):
        if f.severity == "HIGH":
            p.blocked = f"line {f.line_no} {f.rule}: {f.excerpt}"
            return p
        p.warnings.append(f"{f.rule}: {f.excerpt}")
    present: set = set()
    for s in steps:
        c = classify(s.command, None, guard.allowlist)
        if c.tier == Tier.T3:
            p.blocked = f"'{s.command}' is never allowed ({'; '.join(c.reasons[:1])})"
            return p
        wid = _winget_id(s.command)
        if wid and installed is not None and installed(wid):
            p.skipped.append(f"{s.command} ({wid} is already installed)")
            p.complete.append(s)
            present.add(wid)
            continue
        tok = _first_token(s.command)
        if tok in WINGET_FOR and not which(tok) and WINGET_FOR[tok] not in present and not any(_winget_id(x.command) == WINGET_FOR[tok] for x in p.steps):
            fix = WINGET_INSTALL.format(id=WINGET_FOR[tok])
            p.steps.append(PlaybookStep(fix))
            p.tiers.append(classify(fix, None, guard.allowlist).tier.name)
            p.complete.append(PlaybookStep(fix))
            p.warnings.append(f"'{tok}' is not on this PC; I will install {WINGET_FOR[tok]} first")
            present.add(WINGET_FOR[tok])
        p.steps.append(s)
        p.tiers.append(c.tier.name)
        p.complete.append(s)
    if not p.steps:
        p.blocked = "everything in it is already on this PC; nothing to do"
        return p
    p.verify = [s for s, t in zip(p.steps, p.tiers) if t == "T0" and (s.expect or _CHECK.search(s.command))]
    for s in p.complete:                 # proof covers what was skipped too: "already installed" is a claim to check
        d = derived_check(s.command)
        if d and not any(v.command == d.command for v in p.verify):
            p.verify.append(d)
    return p


def as_text(steps: List[PlaybookStep]) -> str:
    """A learned procedure back in guide form, so a replay goes through plan() and looks at the PC first."""
    return "```\n" + "\n".join(s.command + (f"   # expect: {s.expect}" if s.expect else "") for s in steps) + "\n```"


def _refresh_path() -> bool:
    """After an install, this process still has the PATH it started with. Read the current one from the registry."""
    if os.name != "nt":
        return False
    try:
        import winreg
        parts = []
        for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")):
            with winreg.OpenKey(hive, key) as k:
                parts.append(winreg.QueryValueEx(k, "Path")[0])
        fresh = os.path.expandvars(";".join(parts))
        if fresh and fresh != os.environ.get("PATH"):
            os.environ["PATH"] = fresh
            return True
    except OSError:
        pass
    return False


def follow(p: GuidePlan, guard: Guardrails, *, run_fn: Callable = shell_run, store: Optional[PlaybookStore] = None,
           cwd: Optional[str] = None, max_recoveries: int = 2, sleep: Callable[[float], None] = time.sleep,
           refresh_path: Callable[[], bool] = _refresh_path) -> Tuple[Report, Optional[Playbook]]:
    """Run the plan with bounded recovery and a functional verification; keep it as a playbook only on PASS."""
    checks: List[CheckResult] = []
    notes: List[str] = []
    ran: List[PlaybookStep] = []
    recoveries = 0
    installed_now = False
    i = 0
    while i < len(p.steps):
        s = p.steps[i]
        guard.killswitch.check()
        res = run_fn(guard, s.command, cwd=cwd, intent=f"guide: {p.goal}", timeout=600)
        name = f"step {i + 1}: {s.command}"
        if not res.ran:
            checks.append(CheckResult(name, False, "blocked: " + "; ".join(res.decision.reasons[-2:])))
            break
        text = (res.stdout or "") + (res.stderr or "")
        if res.ok and (not s.expect or s.expect.lower() in text.lower()):
            checks.append(CheckResult(name, True, "ok" + (f" (found {s.expect!r})" if s.expect else "")))
            ran.append(s)
            installed_now = installed_now or bool(_winget_id(s.command) or _PIP_INSTALL.match(s.command) or _NPM_INSTALL.match(s.command))
            i += 1
            continue
        kind = getattr(res, "error_kind", None) or ("missing expected output" if res.ok else "failed")
        if recoveries < max_recoveries:
            if kind == "not_found" and installed_now and refresh_path():
                recoveries += 1
                notes.append(f"'{s.command}': not found right after an install; refreshed PATH and tried again")
                continue
            if kind == "network":
                recoveries += 1
                notes.append(f"'{s.command}': network error; waited 5 s and tried again")
                sleep(5)
                continue
        detail = {"permissions": "needs a permission this process does not have",
                  "not_found": "a program or file it needs is not there"}.get(kind, kind)
        checks.append(CheckResult(name, False, f"exit {res.exit_code}: {detail}", (res.stderr or res.stdout or "")[-300:]))
        break
    else:
        for v in p.verify:
            guard.killswitch.check()
            res = run_fn(guard, v.command, cwd=cwd, intent=f"verify: {p.goal}", timeout=120)
            text = ((res.stdout or "") + (res.stderr or "")) if res.ran else ""
            ok = bool(res.ran and res.ok and (not v.expect or v.expect.lower() in text.lower()))
            checks.append(CheckResult(f"check: {v.command}", ok, ("found " + repr(v.expect)) if ok and v.expect else ("ok" if ok else "did not pass"), text[-200:]))
        if not p.verify:
            checks.append(CheckResult("functional check", False, "none could be derived from this guide; exit codes alone do not prove it works"))
    report = Report(f"guide: {p.goal}", checks, [], "each step's own undo (winget uninstall / pip uninstall / delete the folder)", notes)
    book = None
    if report.result == "PASS" and store is not None:
        try:                                 # the WHOLE procedure is kept (a skipped install included): a replay re-plans against the PC
            whole = (p.complete or ran) + [v for v in p.verify if v not in (p.complete or ran)]
            book = store.add(p.goal, whole, "guide", guard, report, extra_keywords=keywords(p.goal))
            report.notes.append("learned: next time this runs offline as a skill")
        except ValueError as e:
            report.notes.append(f"not kept as a skill: {e}")
    return report, book
