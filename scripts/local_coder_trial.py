"""Local coder trial for the studio manager (owner 2026-11-16): before JARVIS routes ANY game work to a local model, measure
it honestly on real past changes of Living Shadow World.

For each task (a small real commit on the game's history): the local model gets the commit's intent (its message) and the
files it touched as they were at the parent commit, and answers with SEARCH/REPLACE edits. The edits are applied in a
separate detached worktree (C:\\Users\\you\\LSW_wt\\studio-trial, never a branch, never the owner's checkout), then the
REAL Unreal editor build and the full LivingShadowWorld automation suite decide. Each result also records whether the
change matches the real commit (normalized lines), so a no-op that "passes" on an ini line is not counted as a pass.

    python -m scripts.local_coder_trial --model gpt-oss:20b            # all tasks, results in data/studio/trial/
    python -m scripts.local_coder_trial --model gpt-oss:20b --only 0c5dac0

The model is called with keep_alive=0 so JARVIS's own brain gets the GPU back between tasks.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from core.config import ROOT

TASKS = ["0c5dac0", "7b7606a", "e36ef69", "6f1739c", "6dea51e", "9ce5813", "9d9d6bb", "b2df816", "c76bc4f", "92d318e"]
FULL_FILE_LINES = 1500          # longer files: only a window around the real change (a generous hint, noted in the result)
WINDOW = 200

SYSTEM = """You are a senior Unreal Engine 5 C++ engineer making a small change to the game Living Shadow World.
You get the task and the current content of the files to change. Answer ONLY with edit blocks in exactly this format:

FILE: <path exactly as given>
<<<<<<< SEARCH
<lines copied EXACTLY from the current file, enough to be unique>
=======
<the replacement lines>
>>>>>>> REPLACE

Use as many blocks as needed. To add lines, include an existing anchor line in SEARCH and repeat it in REPLACE with the
new lines. No explanations outside the blocks."""

_BLOCK = re.compile(r"FILE:\s*(?P<path>[^\n]+)\n<<<<<<< SEARCH\n(?P<search>.*?)\n?=======\n(?P<replace>.*?)\n?>>>>>>> REPLACE",
                    re.S)


def git(repo, *args, check=True) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {out.stderr.strip()[:300]}")
    return out.stdout


def changed_lines(repo, sha: str, path: str):
    """Line numbers (in the parent) the real commit touched: used only to cut a window out of a very long file."""
    diff = git(repo, "diff", "-U0", f"{sha}^", sha, "--", path)
    return [int(m.group(1)) for m in re.finditer(r"^@@ -(\d+)", diff, re.M)]


def task_prompt(repo, sha: str, lessons: bool = False) -> dict:
    msg = git(repo, "log", "-1", "--format=%B", sha).strip()
    msg = re.sub(r"\n*Co-Authored-By:.*", "", msg, flags=re.S | re.I).strip()
    files = [f for f in git(repo, "show", "--name-only", "--format=", sha).split() if f]
    parts, hinted = [], False
    for f in files:
        try:
            text = git(repo, "show", f"{sha}^:{f}")
        except RuntimeError:
            parts.append(f"=== {f} (new file, does not exist yet) ===\n")
            continue
        lines = text.splitlines()
        if len(lines) > FULL_FILE_LINES:
            hinted = True
            at = changed_lines(repo, sha, f) or [1]
            lo, hi = max(0, min(at) - WINDOW), min(len(lines), max(at) + WINDOW)
            parts.append(f"=== {f} (lines {lo + 1}-{hi} of {len(lines)}) ===\n" + "\n".join(lines[lo:hi]))
        else:
            parts.append(f"=== {f} ===\n" + text)
    prompt = (f"TASK (the team's own description of the change):\n{msg}\n\nFILES:\n" + "\n\n".join(parts)
              + "\n\nMake exactly this change. Keep it minimal and compiling.")
    used = []
    if lessons:                                           # the studio's verified lessons (core/studiolearn), like a worker gets
        from core import studiolearn
        # never the answer itself: a lesson whose diff already adds the real commit's lines would contaminate the trial
        ref_added = {ln[1:].strip() for ln in git(repo, "diff", "-U0", f"{sha}^", sha).splitlines()
                     if ln.startswith("+") and not ln.startswith("+++") and len(ln[1:].strip()) > 8}
        past = [ls for ls in studiolearn.similar(msg, k=2)
                if not ref_added or not ref_added <= {ln[1:].strip() for ln in ls.get("diff", "").splitlines() if ln.startswith("+")}]
        if past:
            used = [ls["id"] for ls in past]
            prompt += "\n\nPAST VERIFIED LESSONS (similar changes that built and passed every test):\n" + "\n".join(
                f"- {ls['id']} \"{ls['note']}\": changed {', '.join(ls['files_touched'][:4])}. Diff:\n{ls['diff'][:3000]}"
                for ls in past)
    return {"prompt": prompt, "files": files, "hinted": hinted, "message": msg.splitlines()[0], "lessons_used": used}


def ask(model: str, prompt: str, timeout: int = 1800) -> tuple:
    body = {"model": model, "stream": False, "keep_alive": 0, "options": {"num_ctx": 32768, "temperature": 0.2},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
    t0 = time.time()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return (data.get("message") or {}).get("content", ""), round(time.time() - t0, 1)


def apply(wt: Path, answer: str, allowed) -> dict:
    blocks = list(_BLOCK.finditer(answer))
    applied, problems = 0, []
    for b in blocks:
        path = b.group("path").strip().strip("`")
        if path not in allowed:
            problems.append(f"edit to a file outside the task: {path}")
            continue
        p = wt / path
        cur = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        search, replace = b.group("search"), b.group("replace")
        if search.strip() == "":
            if cur:
                problems.append(f"{path}: empty SEARCH on an existing file")
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(replace + "\n", encoding="utf-8")
            applied += 1
            continue
        crlf = "\r\n" in cur
        norm = cur.replace("\r\n", "\n")
        if norm.count(search) != 1:
            problems.append(f"{path}: SEARCH text found {norm.count(search)} times")
            continue
        norm = norm.replace(search, replace, 1)
        p.write_text(norm.replace("\n", "\r\n") if crlf else norm, encoding="utf-8", newline="")
        applied += 1
    return {"blocks": len(blocks), "applied": applied, "problems": problems}


def _norm_set(text: str):
    return {re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()}


def matches_reference(repo, wt: Path, sha: str, files) -> bool:
    """The lines the real commit added are all present and the lines it removed are all gone (whitespace-normalized)."""
    for f in files:
        diff = git(repo, "diff", "-U0", f"{sha}^", sha, "--", f)
        added = {re.sub(r"\s+", " ", ln[1:]).strip() for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")}
        removed = {re.sub(r"\s+", " ", ln[1:]).strip() for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")}
        added.discard("")
        removed.discard("")
        got = _norm_set((wt / f).read_text(encoding="utf-8", errors="replace")) if (wt / f).exists() else set()
        if not added <= got or (removed - added) & got:
            return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-oss:20b")
    ap.add_argument("--only", default="")
    ap.add_argument("--lessons", action="store_true", help="give the model the studio's similar verified lessons")
    ap.add_argument("--repo", default=r"C:\Users\you\LivingShadowWorld")
    ap.add_argument("--wt", default=r"C:\Users\you\LSW_wt\studio-trial")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from core import studio, studiojob
    repo, wt = Path(args.repo), Path(args.wt)
    out_dir = studio.home() / "trial" / (args.model.replace(":", "_") + ("-lessons" if args.lessons else ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    if not (wt / ".git").exists():
        git(repo, "worktree", "add", "--detach", str(wt), TASKS[0] + "^")
    tasks = [args.only] if args.only else TASKS
    for sha in tasks:
        res_file = out_dir / f"{sha}.json"
        if res_file.exists() and not args.only:
            print(sha, "already measured", flush=True)
            continue
        studiojob.wait_for_quiet()
        t = task_prompt(repo, sha, lessons=args.lessons)
        git(wt, "checkout", "--detach", "-f", f"{sha}^")
        git(wt, "clean", "-fdq", "--", "Source", "Config")
        rec = {"sha": sha, "task": t["message"], "files": t["files"], "hinted_window": t["hinted"], "model": args.model,
               "lessons_used": t["lessons_used"]}
        try:
            answer, secs = ask(args.model, t["prompt"])
        except Exception as e:  # noqa: BLE001
            rec.update(error=f"model call failed: {type(e).__name__}: {e}", passed=False)
            res_file.write_text(json.dumps(rec, indent=1), encoding="utf-8")
            continue
        rec.update(model_seconds=secs, answer=answer[:20000])
        rec["apply"] = apply(wt, answer, set(t["files"]))
        rec["diff"] = git(wt, "diff", "--", "Source", "Config")[:20000]
        rec["noop"] = not rec["diff"].strip()
        rec["matches_reference"] = (not rec["noop"]) and matches_reference(repo, wt, sha, t["files"])
        if rec["noop"]:
            rec.update(build="not run (no change)", passed=False)
        else:
            tag = f"trial_{sha}"
            b = studiojob.digest_build(studiojob.engine(wt, "build", tag, target="LivingShadowWorldEditor"))
            rec["build"] = b["summary"]
            if b["ok"]:
                studiojob.wait_for_quiet()
                tr = studiojob.digest_tests(studiojob.engine(wt, "test", tag, timeout=60 * 60))
                rec["tests"] = tr["summary"]
                rec["tests_ok"] = tr["ok"]
                rec["failed_tests"] = tr["failed"]
            rec["passed"] = bool(b["ok"] and rec.get("tests_ok"))
        res_file.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        print(sha, "passed" if rec.get("passed") else "FAILED", "| ref match:", rec["matches_reference"], "|", rec.get("build", "")[:60],
              "|", rec.get("tests", "")[:40], flush=True)
    git(wt, "checkout", "--detach", "-f", TASKS[0] + "^")
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out_dir.glob("*.json"))]
    passed = sum(1 for r in rows if r.get("passed"))
    strict = sum(1 for r in rows if r.get("passed") and r.get("matches_reference"))
    print(f"\n{args.model}: {passed}/{len(rows)} build+suite green; {strict}/{len(rows)} green AND the same change as the real commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
