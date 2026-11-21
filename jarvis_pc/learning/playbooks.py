"""Learned skills: playbooks that are verified, stored locally, and run with no API at all.

The rule (owner, 2026-11-04): API credits may be spent to MASTER a skill, but nothing may RELY on them.

  1. A playbook is a short list of commands, each with an expected-output check. It runs through the same permission
     gate as everything else, so a playbook can never do more than the gate allows.
  2. A playbook is saved only after it actually worked: add() requires a PASS report. Unverified advice is not a skill.
  3. Finding a playbook is offline keyword matching. Running one is offline. Neither calls any model.
  4. A teacher (any paid or local model, passed in as a plain callable) is asked ONLY on a store miss, only while the
     daily budget lasts, and its answer is untrusted text: it is scanned for prompt-injection, every line is classified by
     the gate (T3 is rejected outright), and nothing is kept unless a verified run passes. After that the teacher is
     never needed for that problem again.
  5. Learned skills go stale (software and Windows change). After repeated verify failures a playbook is marked stale and
     stops being offered, so it can be relearned instead of quietly doing the wrong thing.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..actions.shell import run as shell_run  # not `run`: PlaybookStore.run would shadow it inside the class
from ..guardrails.gate import Guardrails
from ..guardrails.injection import scan_untrusted
from ..guardrails.tiers import Tier, classify
from ..verify.checks import CheckResult
from ..verify.report import Report

STOP_WORDS = {"a", "an", "the", "to", "my", "of", "and", "or", "is", "it", "in", "on", "for", "with", "how", "do", "i",
              "can", "you", "please", "jarvis", "fix", "get", "make", "this", "that", "me", "not", "are", "be"}
MAX_STEPS = 12
STALE_AFTER = 3  # consecutive failed runs before a playbook stops being offered

Teacher = Callable[[str, str], str]  # (task, context) -> text with one command per line


def keywords(text: str) -> List[str]:
    return sorted({w for w in re.findall(r"[a-z0-9][a-z0-9.+_-]{1,}", (text or "").lower()) if w not in STOP_WORDS})


@dataclass
class PlaybookStep:
    command: str
    expect: str = ""  # text that must appear in the output (empty = exit code 0 is enough)


@dataclass
class Playbook:
    id: str
    title: str
    keywords: List[str]
    steps: List[PlaybookStep]
    source: str  # taught | teacher | self
    created: float
    runs_ok: int = 0
    fails_in_a_row: int = 0
    last_run: float = 0.0
    stale: bool = False
    tier: str = "T0"  # the highest tier any step needs; shown so the owner knows what it will ask for
    params: List[str] = field(default_factory=list)  # names of {placeholders} the steps use; run() fills them in
    subject: str = ""  # set when the school taught it (a curriculum subject id)
    kind: str = "shell"  # "ui" skills need a bound window and are never run by pc_learned_skill


def fill(command: str, params: Dict[str, str]) -> str:
    """Put real values into a command's {placeholders}. A value with spaces is quoted unless the template already quotes it."""
    out = command
    for name, value in params.items():
        token = "{" + name + "}"
        pos = 0
        while True:
            i = out.find(token, pos)
            if i < 0:
                break
            quoted = i > 0 and out[i - 1] in "\"'"
            v = value if quoted or not re.search(r"\s", value) else '"' + value.replace('"', "") + '"'
            out = out[:i] + v + out[i + len(token):]
            pos = i + len(v)
    return out


def placeholders(steps: List["PlaybookStep"]) -> List[str]:
    return sorted({m for s in steps for m in re.findall(r"\{(\w+)\}", s.command)})


def _looks_like_prose(line: str) -> bool:
    return line.endswith(":") or (line.endswith(".") and not re.search(r"[\\/|$=-]", line))


def parse_steps(text: str) -> Tuple[List[PlaybookStep], List[str]]:
    """Turn untrusted teacher text into steps. One command per line; `# expect: ...` after a command is its check.
    Returns (steps, rejected_reasons). Prose, numbering and code fences are stripped; nothing is executed here."""
    steps: List[PlaybookStep] = []
    why: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        line = re.sub(r"^(\d+[.)]|[-*])\s+", "", line)
        if line.startswith("#") or line.lower().startswith(("note:", "explanation:", "step ")) or _looks_like_prose(line):
            continue
        expect = ""
        m = re.search(r"\s+#\s*expect:\s*(.+)$", line, re.I)
        if m:
            expect, line = m.group(1).strip(), line[:m.start()].strip()
        if not line:
            continue
        if len(steps) >= MAX_STEPS:
            why.append(f"more than {MAX_STEPS} steps; ignored the rest")
            break
        steps.append(PlaybookStep(line, expect))
    return steps, why


class PlaybookStore:
    def __init__(self, path: str, budget_path: Optional[str] = None, *, clock: Callable[[], float] = time.time):
        self.path = path
        self.budget_path = budget_path or os.path.join(os.path.dirname(path) or ".", "teacher_budget.json")
        self.clock = clock
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # ------------------------------------------------------------------ storage
    def all(self) -> List[Playbook]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    d = json.loads(line)
                    d["steps"] = [PlaybookStep(**s) for s in d["steps"]]
                    out.append(Playbook(**d))
        return out

    def _write(self, books: List[Playbook]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for b in books:
                f.write(json.dumps(asdict(b), sort_keys=True) + "\n")
        os.replace(tmp, self.path)

    def _update(self, book: Playbook) -> None:
        with self._lock:
            books = [book if b.id == book.id else b for b in self.all()]
            self._write(books)

    def add(self, title: str, steps: List[PlaybookStep], source: str, guard: Guardrails, proof: Report, *,
            params: Optional[List[str]] = None, extra_keywords: Optional[List[str]] = None, subject: str = "", kind: str = "shell") -> Playbook:
        """Save a playbook. Refuses without a PASS report (no proof, no skill), on T3 steps, or on injected text."""
        if proof.result != "PASS":
            raise ValueError(f"not saved: it has not been proven (result {proof.result})")
        if not steps:
            raise ValueError("not saved: no steps")
        if scan_untrusted(title + "\n" + "\n".join(s.command + " " + s.expect for s in steps)).suspicious:
            raise ValueError("not saved: the text tries to instruct an AI")
        top = Tier.T0
        for s in (steps if kind == "shell" else []):   # UI lines are not shell commands; the UI executor gates them
            c = classify(s.command, None, guard.allowlist)
            if c.tier == Tier.T3:
                raise ValueError(f"not saved: step is never allowed ({s.command}): {'; '.join(c.reasons[:1])}")
            top = max(top, c.tier)
        kws = sorted(set(keywords(re.sub(r"\{\w+\}", " ", title))) | set(keywords(" ".join(extra_keywords or []))))
        book = Playbook(uuid.uuid4().hex[:8], title, kws, steps, source, self.clock(), tier=top.name,
                        runs_ok=1, last_run=self.clock(), params=list(params or []), subject=subject, kind=kind)
        with self._lock:
            self._write(self.all() + [book])
        return book

    # ------------------------------------------------------------------ offline lookup
    def find(self, query: str, min_score: float = 0.5) -> Optional[Playbook]:
        """Best non-stale playbook for the query, by keyword overlap. No model involved."""
        q = set(keywords(query))
        best, best_score = None, 0.0
        for b in self.all():
            if b.stale or not b.keywords or b.kind != "shell":
                continue
            hit = q & set(b.keywords)
            score = len(hit) / len(set(b.keywords))  # how much of the playbook's topic the query covers
            if hit and score >= min_score and (score, b.runs_ok) > (best_score, best.runs_ok if best else 0):
                best, best_score = b, score
        return best

    # ------------------------------------------------------------------ running (offline, gated, verified)
    def run(self, book: Playbook, guard: Guardrails, *, run_fn: Callable = shell_run, cwd: Optional[str] = None,
            params: Optional[Dict[str, str]] = None, outputs: Optional[List[str]] = None) -> Report:
        """Run a learned playbook. `params` fills its {placeholders}; `outputs`, if given, collects each step's text so a
        caller (the school's exam) can check it against ground truth."""
        checks: List[CheckResult] = []
        ok_all = True
        missing = [n for n in book.params if n not in (params or {})]
        if missing:
            return Report(f"playbook: {book.title}", [CheckResult("needs values", False, "missing: " + ", ".join(missing))])
        for i, s in enumerate(book.steps, 1):
            guard.killswitch.check()
            command = fill(s.command, params or {})
            res = run_fn(guard, command, cwd=cwd, intent=f"playbook: {book.title}", timeout=300)
            name = f"step {i}: {s.command}"
            if not res.ran:
                checks.append(CheckResult(name, False, "blocked: " + "; ".join(res.decision.reasons[-2:])))
                ok_all = False
                break
            if not res.ok:
                checks.append(CheckResult(name, False, f"exit {res.exit_code} ({res.error_kind})", (res.stderr or "")[-300:]))
                ok_all = False
                break
            text = (res.stdout or "") + (res.stderr or "")
            if outputs is not None:
                outputs.append(res.stdout or "")
            if s.expect and s.expect.lower() not in text.lower():
                checks.append(CheckResult(name, False, f"output does not contain {s.expect!r}", text[-300:]))
                ok_all = False
                break
            checks.append(CheckResult(name, True, "ok" + (f" (found {s.expect!r})" if s.expect else "")))
        with self._lock:
            fresh = {b.id: b for b in self.all()}.get(book.id)
            if fresh:
                fresh.last_run = self.clock()
                if ok_all:
                    fresh.runs_ok += 1
                    fresh.fails_in_a_row = 0
                else:
                    fresh.fails_in_a_row += 1
                    fresh.stale = fresh.fails_in_a_row >= STALE_AFTER
                self._write([fresh if b.id == fresh.id else b for b in self.all()])
        notes = [] if ok_all else ["This learned skill failed; it will be set aside after "
                                   f"{STALE_AFTER} failures in a row so it can be relearned."]
        return Report(f"playbook: {book.title}", checks, [], "see each step's own undo/journal", notes)

    # ------------------------------------------------------------------ teacher budget
    def _budget(self) -> Dict[str, int]:
        try:
            with open(self.budget_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def teacher_calls_today(self) -> int:
        return int(self._budget().get(time.strftime("%Y-%m-%d", time.localtime(self.clock())), 0))

    def _spend(self) -> None:
        b = self._budget()
        day = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        b = {day: int(b.get(day, 0)) + 1}  # keep only today
        with open(self.budget_path, "w", encoding="utf-8") as f:
            json.dump(b, f)

    # ------------------------------------------------------------------ master: learn from a teacher, once
    def solve(self, task: str, guard: Guardrails, teacher: Optional[Teacher] = None, *, context: str = "",
              daily_budget: int = 3, run_fn: Callable = shell_run, params: Optional[Dict[str, str]] = None) -> "SolveResult":
        """Use a learned playbook if one exists (no model, no API). Otherwise, only if a teacher is provided and the
        daily budget allows, ask it once, vet its answer, run it through the gate, and keep it only if it verifies."""
        book = self.find(task)
        if book:
            missing = [n for n in book.params if n not in (params or {})]
            if missing:
                return SolveResult("needs_values", book, None,
                                   note="this learned skill needs: " + ", ".join(missing) + " (pass them as values)")
            outs: List[str] = []
            rep = self.run(book, guard, run_fn=run_fn, params=params, outputs=outs)
            return SolveResult("learned", book, rep, teacher_used=False, output=chr(10).join(o.strip() for o in outs if o.strip()))
        if teacher is None or daily_budget <= 0:
            return SolveResult("unknown", None, None, note="no learned skill for this yet, and no teacher is available")
        if self.teacher_calls_today() >= daily_budget:
            return SolveResult("unknown", None, None, note=f"teacher budget for today ({daily_budget}) is used up")
        self._spend()
        try:
            answer = teacher(task, context)
        except Exception as e:  # noqa: BLE001
            return SolveResult("unknown", None, None, teacher_used=True, note=f"teacher failed: {type(e).__name__}: {e}")
        steps, why = parse_steps(answer)
        if scan_untrusted(answer).suspicious:
            return SolveResult("rejected", None, None, teacher_used=True, note="teacher text tries to instruct an AI; ignored")
        if not steps:
            return SolveResult("rejected", None, None, teacher_used=True, note="teacher gave no usable commands")
        for s in steps:
            if classify(s.command, None, guard.allowlist).tier == Tier.T3:
                return SolveResult("rejected", None, None, teacher_used=True,
                                   note=f"teacher proposed something the gate never allows: {s.command}")
        trial = Playbook("trial", task, keywords(task), steps, "teacher", self.clock())
        report = self._try(trial, guard, run_fn)
        if report.result != "PASS":
            return SolveResult("unproven", None, report, teacher_used=True, note="ran it, but it did not verify; not saved. " + "; ".join(why))
        try:
            saved = self.add(task, steps, "teacher", guard, report)
        except ValueError as e:
            return SolveResult("rejected", None, report, teacher_used=True, note=str(e))
        return SolveResult("mastered", saved, report, teacher_used=True,
                           note="learned and saved; next time this runs offline with no teacher call")

    def _try(self, trial: Playbook, guard: Guardrails, run_fn: Callable) -> Report:
        """Run an unsaved playbook once (still through the gate) to see whether it verifies."""
        checks: List[CheckResult] = []
        for i, s in enumerate(trial.steps, 1):
            guard.killswitch.check()
            res = run_fn(guard, s.command, intent=f"learning: {trial.title}", timeout=300)
            name = f"step {i}: {s.command}"
            text = (res.stdout or "") + (res.stderr or "") if res.ran else ""
            if not res.ran:
                checks.append(CheckResult(name, False, "blocked: " + "; ".join(res.decision.reasons[-2:])))
                break
            if not res.ok or (s.expect and s.expect.lower() not in text.lower()):
                checks.append(CheckResult(name, False, f"exit {res.exit_code}" if not res.ok else f"missing {s.expect!r}"))
                break
            checks.append(CheckResult(name, True, "ok"))
        return Report(f"learning: {trial.title}", checks)


@dataclass
class SolveResult:
    status: str  # learned | needs_values | mastered | unknown | rejected | unproven
    playbook: Optional[Playbook]
    report: Optional[Report]
    teacher_used: bool = False
    note: str = ""
    output: str = ""  # what the skill's commands printed: the actual answer, not just "PASS"
