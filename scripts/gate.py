"""The JARVIS engineering gate (docs/ENGINEERING-PROTOCOL.md): `main` only receives a commit whose FULL test suite passed,
run alone, on that exact commit.

    python -m scripts.gate check [--force]   run the full suite on HEAD and record the result (.git/jarvis-gate/<sha>.json, shared by every worktree)
    python -m scripts.gate status            show the recorded result for HEAD
    python -m scripts.gate merge BRANCH      merge BRANCH into main only if its HEAD has a passing record; tags a rollback

Why it exists (2026-11-10): a merge went in right after a chained, FAILING suite; another time the suite ran while the
owner was using the PC and failed on his real keyboard input. The gate makes "read the result before merging" a check,
not a habit. It never pushes, deploys or restarts anything.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _shared_records() -> Path:
    """Records live in the git dir that every worktree shares, so a branch checked in its worktree can be merged from
    the main checkout (builds happen in worktrees; main is the live JARVIS and is never switched to a branch)."""
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode == 0 and out.stdout.strip():
            common = Path(out.stdout.strip())
            return (common if common.is_absolute() else ROOT / common).resolve() / "jarvis-gate"
    except OSError:
        pass
    return ROOT / "runs" / "gate"


RECORDS = _shared_records()
IGNORED_DIRTY = ("teaching-test-work/",)          # a tracked test fixture repo that git always reports as modified
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WORKERS = max(1, int(os.getenv("JARVIS_GATE_WORKERS", "8")))   # 1 = the old single-process run


class GateError(SystemExit):
    """A refusal: the message says why, the exit code says which rule."""

    def __init__(self, code: int, message: str):
        print(f"gate: {message}", file=sys.stderr)
        super().__init__(code)


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if out.returncode != 0:
        raise GateError(2, f"git {' '.join(args)} failed: {out.stderr.strip()[:300]}")
    return out.stdout.rstrip()     # never lstrip: `status --porcelain` lines start with a meaningful space


def dirty_paths() -> list[str]:
    """Tracked files with uncommitted changes (a result must belong to a commit, not to a working tree)."""
    paths = []
    for line in _git("status", "--porcelain", "--untracked-files=no").splitlines():
        path = line[3:].strip().strip('"')
        if path and not path.startswith(IGNORED_DIRTY):
            paths.append(path)
    return paths


def parse_summary(output: str) -> dict:
    """Counts from pytest's last summary line ('3897 passed, 40 skipped, 5 warnings in 883.92s')."""
    tail = [ln for ln in output.strip().splitlines() if re.search(r"\b(passed|failed|error|errors|no tests ran)\b", ln)]
    line = tail[-1] if tail else ""
    count = lambda word: int(m.group(1)) if (m := re.search(rf"(\d+) {word}\b", line)) else 0  # noqa: E731
    return {"summary": line.strip("= ").strip(), "passed": count("passed"), "failed": count("failed"),
            "errors": count("errors") + count("error"), "skipped": count("skipped")}


def record_path(sha: str) -> Path:
    return RECORDS / f"{sha}.json"


def read_record(sha: str) -> dict | None:
    try:
        return json.loads(record_path(sha).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _running_game() -> str:
    try:
        from core import acquire
        return acquire.running_game() or ""
    except Exception:  # noqa: BLE001 - unknown is not a game
        return ""


def check(force: bool = False, runner=subprocess.run) -> dict:
    dirty = dirty_paths()
    if dirty:
        raise GateError(3, f"commit first - the result must belong to a commit. Uncommitted: {', '.join(dirty[:8])}")
    game = _running_game()
    if game and not force:
        raise GateError(4, f"{game} is running: the full suite would disturb the owner's game (use --force only if he said so)")
    sha, branch = _git("rev-parse", "HEAD"), _git("branch", "--show-current")
    print(f"gate: full suite on {branch} @ {sha[:10]} - run nothing else on this PC until it finishes")
    t0 = time.monotonic()
    base = [sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"]
    # Phase 1 runs the suite on WORKERS processes (pytest-xdist; 2026-11-20: 12.9k tests in ~4 min instead of ~35). Phase 2
    # runs the tests marked `serial` alone: timing tests and tests that share files on disk with each other.
    phases = ([("parallel", base + ["-n", str(WORKERS), "-m", "not serial"]), ("serial", base + ["-m", "serial"])]
              if WORKERS > 1 else [("all", base)])
    texts, codes, totals = [], [], {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for name, cmd in phases:
        out = runner(cmd, cwd=ROOT, capture_output=True, text=True, creationflags=_NO_WINDOW)
        text = (out.stdout or "") + (out.stderr or "")
        part = parse_summary(text)
        texts.append(f"===== gate phase: {name} =====\n{text}")
        # pytest exits 5 when no test was collected: fine for the serial phase (no serial tests), never for the whole run
        codes.append(0 if (name == "serial" and out.returncode == 5) else out.returncode)
        for k in totals:
            totals[k] += part[k]
    text = "\n".join(texts)
    summary = " + ".join(parse_summary(t)["summary"] for t in texts)
    rec = {"sha": sha, "branch": branch, "at": datetime.now().isoformat(timespec="seconds"),
           "seconds": round(time.monotonic() - t0, 1), "returncode": max(codes), "summary": summary, **totals,
           "workers": WORKERS, "failures": [ln for ln in text.splitlines() if ln.startswith(("FAILED ", "ERROR "))][:50]}
    rec["ok"] = all(c == 0 for c in codes) and rec["failed"] == 0 and rec["errors"] == 0 and rec["passed"] > 0
    RECORDS.mkdir(parents=True, exist_ok=True)
    record_path(sha).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    if not rec["ok"]:       # keep the whole run: the summary lines alone never said WHICH skill hung or what the assertion read
        record_path(sha).with_suffix(".log").write_text(text, encoding="utf-8", errors="replace")
    print("\n".join(text.strip().splitlines()[-15:]))
    print(f"gate: {'PASS' if rec['ok'] else 'FAIL'} - {rec['summary']} (recorded {record_path(sha)})")
    return rec


def status() -> dict | None:
    sha = _git("rev-parse", "HEAD")
    rec = read_record(sha)
    print(f"gate: {sha[:10]} " + (f"{'PASS' if rec['ok'] else 'FAIL'} - {rec['summary']} at {rec['at']}" if rec
                                   else "has no recorded full-suite run"))
    return rec


def merge(branch: str, now: datetime | None = None) -> str:
    """Merge BRANCH into main if its HEAD passed the gate. Returns the rollback tag."""
    sha = _git("rev-parse", branch)
    rec = read_record(sha)
    if not rec:
        raise GateError(5, f"{branch} @ {sha[:10]} has no full-suite record: run `python -m scripts.gate check` on it first")
    if not rec.get("ok"):
        raise GateError(5, f"{branch} @ {sha[:10]} FAILED its full suite ({rec.get('summary')}): fix it, commit, check again")
    if dirty_paths():
        raise GateError(3, "the working tree has uncommitted changes: commit or stash them before merging")
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", branch)
    tag = f"rollback/pre-{safe}-{stamp}"
    existing = set(_git("tag", "--list", f"{tag}*").split())
    n = 2
    while tag in existing:
        tag, n = f"rollback/pre-{safe}-{stamp}-{n}", n + 1
    _git("tag", tag, "main")
    _git("checkout", "main")
    _git("merge", "--no-ff", "-m", f"Merge {branch} (gate: {rec['summary']})", branch)
    print(f"gate: merged {branch} into main; rollback tag {tag}")
    print("gate: next - deploy the LAPTOP first, then the PC (check acquire.running_game() before restart-jarvis.bat), "
          "then verify the running result (docs/ENGINEERING-PROTOCOL.md section 5)")
    return tag


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "check":
        return 0 if check(force="--force" in rest)["ok"] else 1
    if cmd == "status":
        rec = status()
        return 0 if rec and rec.get("ok") else 1
    if cmd == "merge" and rest:
        merge(rest[0])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
