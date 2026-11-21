"""One approved studio work order, carried out (scripts/studio_job.py runs this as a detached process).

JARVIS does the non-thinking work itself: the branch and worktree, the Unreal build, the automation suite, the lane's
headless scenarios (the same command lines as the coordinator's Scripts/integration_verify.ps1, real logs under
Documentation/BuildEvidence/), and a DIGEST of each log. A worker (Claude Code / Codex / Claude on the API credit) does
the coding and, when the build or a test goes red, gets the digest - a few lines, not the logs - for one fix round.

Order of work: worktree -> worker -> build -> (fix round) -> automation suite + scenarios -> (fix round) -> commit on the
order's branch -> report. Heavy steps wait while the owner plays (core.acquire.running_game) and one studio job runs at a
time; every Unreal build uses -WaitMutex.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

from core import studio
from core.words import plural

MAX_FIX_ROUNDS = int(os.getenv("JARVIS_STUDIO_FIX_ROUNDS", "2"))
WORKER_TIMEOUT_S = int(os.getenv("JARVIS_STUDIO_WORKER_S", str(90 * 60)))
BUILD_TIMEOUT_S = 90 * 60
GAME_POLL_S = 60
CREDIT_MODEL = os.getenv("JARVIS_STUDIO_MODEL", "claude-opus-5-5")


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ------------------------------------------------------------------------------------------------ digests (no model)
_BUILD_ERR = re.compile(r"(error C\d{4}.*|error LNK\d{4}.*|: (fatal )?error\b.*|Unable to build.*|ConflictingInstance.*)", re.I)


def digest_build(text: str) -> Dict:
    errors: List[str] = []
    for line in text.splitlines():
        m = _BUILD_ERR.search(line)
        if m:
            short = re.sub(r"^.*?Source[\\/]", "Source/", line.strip()).replace(chr(92), "/")[:240]
            if short not in errors:
                errors.append(short)
    ok = bool(re.search(r"Result: Succeeded", text)) and bool(re.search(r"BUILD_EXIT=0(?!\d)", text)) and "LSW_RUNNER: TIMEOUT" not in text
    lines = ["build: " + ("Succeeded" if ok else "FAILED")] + errors[:15]
    if len(errors) > 15:
        lines.append(f"... and {len(errors) - 15} more errors")
    return {"ok": ok, "errors": len(errors), "summary": "\n".join(lines)}


_QUEUE_DONE = re.compile(r"\*\*\*\* TestExit: Automation Test Queue Empty|Automation Test Queue Empty \d+ tests performed")
_TEST = re.compile(r"Test Completed\. Result=\{(\w+)\}\s*Name=\{([^}]*)\}\s*Path=\{([^}]*)\}")


def digest_tests(text: str) -> Dict:
    results = _TEST.findall(text)
    passed = sum(1 for r in results if r[0] == "Success")
    failed = [r[2] or r[1] for r in results if r[0] != "Success"]
    # the real end-of-queue log events, never the command line (it contains -TestExit="Automation Test Queue Empty")
    finished = bool(_QUEUE_DONE.search(text)) and "LSW_RUNNER: TIMEOUT" not in text
    errors = []
    for line in text.splitlines():
        if "LogAutomationController: Error" in line and len(errors) < 12:
            errors.append(re.sub(r"^\[[^\]]*\]\[[^\]]*\]", "", line).strip()[:240])
    ok = finished and not failed and passed > 0
    lines = [f"automation: {passed}/{len(results)} passed" + ("" if finished else " (the queue never finished: crash or timeout)")]
    lines += [f"FAILED {f}" for f in failed[:15]] + errors
    return {"ok": ok, "passed": passed, "total": len(results), "failed": failed, "summary": "\n".join(lines)}


def digest_scenario(name: str, text: str) -> Dict:
    ok = "RESULT=PASS" in text
    fails = [ln.strip()[:240] for ln in text.splitlines() if "[LSW]" in ln and re.search(r"\bFAIL", ln)][:10]
    counts = re.findall(r"RESULT=\w+ \((\d+) checks, (\d+) failed\)", text)
    tail = f" ({int(counts[-1][0]) - int(counts[-1][1])}/{counts[-1][0]} checks)" if counts else ""
    if not ok and not fails:
        fails = [ln.strip()[:240] for ln in text.splitlines() if re.search(r"Fatal|LSW_RUNNER|RESULT=", ln)][:5]
    return {"ok": ok, "name": name, "summary": "\n".join([f"scenario {name}: {'PASS' if ok else 'FAIL'}{tail}"] + fails)}


# ------------------------------------------------------------------------------------------------ the engine (same calls as the coordinator)
UE = Path(os.getenv("JARVIS_UE_ROOT", r"C:\Program Files\Epic Games\UE_5.8"))


def _editor_cmd() -> str:
    return str(UE / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe")


def wait_slot(sleep: Callable[[float], None] = time.sleep) -> None:
    """The team's etiquette: never a 4th UnrealEditor-Cmd at once (Documentation/TEAM.md, integration_verify.ps1)."""
    try:
        import psutil
    except ImportError:
        return
    while sum(1 for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == "unrealeditor-cmd.exe") >= 3:
        sleep(20)


def kill_tree(pid: int) -> None:
    try:
        import psutil
        parent = psutil.Process(pid)
        for c in parent.children(recursive=True):
            try:
                c.kill()
            except psutil.Error:
                pass
        parent.kill()
    except Exception:  # noqa: BLE001 - already gone
        pass


def _run_logged(argv: List[str], logfile: Path, timeout: int) -> str:
    """One engine command, its whole output into logfile (Documentation/BuildEvidence/ in the order's worktree, like every
    team run); a run past its time is killed and says so in the log."""
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with open(logfile, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("CMD: " + " ".join(f'"{a}"' if " " in a else a for a in argv) + "\n")
        fh.flush()
        p = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=studio.NO_WINDOW if os.name == "nt" else 0)
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)                              # UBT/dotnet children would keep the global build mutex
            code = -1
            fh.write(f"\nLSW_RUNNER: TIMEOUT after {timeout} s - process killed\n")
        fh.write(f"\nEXIT={code}\nBUILD_EXIT={code}\n")
    return logfile.read_text(encoding="utf-8", errors="replace")


def engine(wt: Path, kind: str, tag: str, target: str = "", scenario: Optional[Dict] = None, timeout: int = BUILD_TIMEOUT_S) -> str:
    """kind = build (target LivingShadowWorldEditor / LivingShadowWorld), test (the full suite) or scenario. -> log text.
    The same command lines as the coordinator's Scripts/integration_verify.ps1."""
    ev = wt / "Documentation" / "BuildEvidence"
    u = str(wt / "LivingShadowWorld.uproject")
    if kind == "build":
        argv = ["cmd", "/c", str(UE / "Engine" / "Build" / "BatchFiles" / "Build.bat"), target, "Win64", "Development",
                f"-Project={u}", "-WaitMutex"]
        return _run_logged(argv, ev / f"{tag}_build_{target}.log", timeout)
    wait_slot()
    if kind == "test":
        argv = [_editor_cmd(), u, "-ExecCmds=Automation RunTests LivingShadowWorld", "-TestExit=Automation Test Queue Empty",
                f"-ReportExportPath={ev / (tag + '_report')}", "-unattended", "-nullrhi", "-nosplash", "-nop4", "-stdout"]
        return _run_logged(argv, ev / f"{tag}_automation.log", timeout)
    s = scenario or {}
    argv = [_editor_cmd(), u] + ([s["map"]] if s.get("map") else []) + ["-game"]
    argv += ["-RenderOffscreen", "-ResX=1280", "-ResY=720"] if s.get("render") else ["-nullrhi"]
    argv += ["-unattended", "-nosplash", "-nop4", "-stdout", f"-LSWScenario={s['name']}"]
    return _run_logged(argv, ev / f"{tag}_scn_{s['name']}.log", timeout)


def wait_for_quiet(sleep: Callable[[float], None] = time.sleep, game: Optional[Callable[[], str]] = None) -> None:
    """Heavy steps never run while he plays (the build loads the CPU of the machine he games on)."""
    if game is None:
        from core.acquire import running_game as game
    told = False
    while True:
        g = game()
        if not g:
            return
        if not told:
            log(f"waiting: {g} is running")
            told = True
        sleep(GAME_POLL_S)


# ------------------------------------------------------------------------------------------------ workers
RULES = """You are a senior Unreal Engine 5.8 C++ engineer on the game Living Shadow World. You work ONLY inside this git
worktree ({wt}) on branch {branch}. Rules from the team charter (Documentation/TEAM.md):
- Never commit, merge, rebase, push or switch branches. JARVIS commits your work on this branch after it is green.
- The worktree is 6 GB of binary game content: search ONLY Source/, Config/ and Scripts/ (use `git grep -n <word> --
  Source`), never Content/, Binaries/, Intermediate/ or the whole tree. Read the listed files first.
- Do NOT run Build.bat, UnrealEditor or any Scripts/*.ps1: JARVIS builds and tests after you finish and will send you a
  short digest if anything is red. Read code instead; keep the change compiling by careful reading.
- Stay inside the lane's folders where possible: {dirs}. Shared files (LivingShadowWorld.uproject, *.Build.cs,
  Config/*.ini, input mapping) only additively and minimally - list every such change in your final message.
- Premium bar: data-driven tunables (UPROPERTY/DataAsset, demo values marked 'demo default, not canon'), descriptive names
  (no generic helpers at namespace scope), comments explain why, no dead code, runtime code must compile for the Game
  target (editor-only code behind #if WITH_EDITOR). Automation tests: IMPLEMENT_SIMPLE_AUTOMATION_TEST with
  EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter, guarded by #if WITH_DEV_AUTOMATION_TESTS,
  named LivingShadowWorld.<Area>.<Name>. Scenarios register from your own files via ALSWScenarioRunner::RegisterScenario.
- Locked canon (never change): EXACTLY three soul-extraction attempts; the opening line "I put my life in your hands."
  verbatim; controller-first; injury changes real capability. Never "fix" a scenario by freezing poses.
- End with a short plain report: what you changed (files), the measurable before/after, the test that proves it, any
  shared-file change, and anything you could not do. No praise, no filler."""


def prompt_for(order: Dict) -> str:
    files = "\n".join(f"  - {f['path']}" for f in order["files"]) or "  (none found by keyword - search the lane folders)"
    crit = "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(order["acceptance"]))
    return (RULES.format(wt=order["worktree"], branch=order["branch"], dirs=", ".join(studio.LANES[order["lane"]]["dirs"]))
            + f"\n\nWORK ORDER {order['id']} (approved by the director)\nDirector's note, his words: \"{order['note']}\"\n"
            f"Lane: {order['role']}\nLikely files (from a keyword search; verify):\n{files}\nAcceptance criteria:\n{crit}\n"
            "Read the relevant code first, find the real cause or the right knob, make the smallest premium change that "
            "satisfies every criterion, and add or update the test that proves it."
            + _learned(order))


def _learned(order: Dict) -> str:
    try:
        from core import studiolearn
        return studiolearn.prompt_section(order)
    except Exception:  # noqa: BLE001 - lessons are context; the order stands without them
        return ""


def fix_prompt(order: Dict, digest: str) -> str:
    return (f"Work order {order['id']} on branch {order['branch']}: JARVIS built and tested your change and something is red. "
            f"Digest (not the full log):\n{digest}\n\nFix the cause in the code (never weaken or delete a test to make it "
            "pass, never freeze poses). Same rules as before: no builds, no commits. End with a short report of what you "
            "changed and why.")


# An ALLOW-list (review round 2: a deny-list let QBIT_PASS through). Windows needs these to run node/git/compilers;
# the CLIs log in from their own files (~/.claude, ~/.codex/auth.json) under USERPROFILE/APPDATA.
_ENV_KEEP = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "USERPROFILE", "USERNAME", "USERDOMAIN",
             "HOMEDRIVE", "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
             "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432", "TEMP", "TMP",
             "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "OS", "COMPUTERNAME", "PUBLIC",
             "ALLUSERSPROFILE", "PSMODULEPATH", "LANG", "TERM"}


def worker_env(keep_api_key: bool) -> Dict[str, str]:
    """The worker gets the system basics and nothing from JARVIS's .env (loaded into this process): only the Anthropic
    key, and only for credit runs. OPENAI_API_KEY is never passed (Codex must use his ChatGPT login, not API money)."""
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}
    if keep_api_key and os.getenv("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
    return env


def program(name: str) -> Optional[str]:
    import shutil
    return shutil.which(name)


def worker_cmd(order: Dict, prompt: str, first: bool, budget_left: float, out_file: Path) -> tuple:
    """-> (argv, env). The prompt goes in on stdin (Windows command lines are limited to 32k)."""
    w = order["worker"]
    if w == "codex":
        argv = [program("codex") or "codex", "exec", "-C", order["worktree"], "-s", "workspace-write", "--skip-git-repo-check",
                "-o", str(out_file), "-"]
        return argv, worker_env(False)
    argv = [program("claude") or "claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions",
            "--disallowedTools", "Bash(git commit:*)", "Bash(git push:*)", "Bash(git checkout:*)", "Bash(git switch:*)",
            "Bash(git merge:*)", "Bash(git rebase:*)", "Bash(git reset:*)"]
    if not first and order.get("session_id"):
        argv += ["--resume", order["session_id"]]
    if w == "credit":
        argv += ["--bare", "--model", CREDIT_MODEL, "--max-budget-usd", f"{max(budget_left, 0.5):.2f}"]
        return argv, worker_env(True)
    argv += ["--model", "opus"]
    return argv, worker_env(False)                    # his Claude Code login, never the API money


def _run_worker_process(argv, prompt, cwd, env, timeout) -> SimpleNamespace:
    """Popen so a timeout kills the whole tree (a killed parent must not leave a child spending)."""
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
                         text=True, encoding="utf-8", errors="replace",
                         creationflags=studio.NO_WINDOW if os.name == "nt" else 0)
    try:
        out, err = p.communicate(prompt, timeout=timeout)
        return SimpleNamespace(returncode=p.returncode, stdout=out, stderr=err, timed_out=False)
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        try:
            out, err = p.communicate(timeout=30)
        except Exception:  # noqa: BLE001
            out, err = "", ""
        return SimpleNamespace(returncode=-1, stdout=out or "", stderr=err or "", timed_out=True)


def timed_out_flag(p) -> bool:
    return bool(getattr(p, "timed_out", False))


def run_worker(order: Dict, prompt: str, first: bool, runner=None) -> Dict:
    """One worker turn. -> {"ok", "text", "cost"}. A credit turn whose cost is unknown (timeout, crash, unreadable reply)
    is charged at the budget it was given: the cap must never under-count."""
    spent = float(order.get("spent_usd") or 0.0)
    left = float(order.get("budget_usd") or 0.0) - spent
    if order["worker"] == "credit":
        c = studio.credit_state()
        left = min(left, c["left"])
        if not c["ok"] or left < 0.5:
            return {"ok": False, "text": f"the API credit can't pay for more work ({c['why'] or f'${left:.2f} left for this order'})",
                    "cost": 0.0}
    name = "codex" if order["worker"] == "codex" else "claude"
    if runner is None and program(name) is None:
        return {"ok": False, "text": f"the worker program {name} is not installed on this PC", "cost": 0.0}
    out_file = Path(studio.job_log(order)).with_suffix(f".last-{int(time.time())}.txt")
    argv, env = worker_cmd(order, prompt, first, left, out_file)
    started = time.time()
    try:
        if runner is None:
            p = _run_worker_process(argv, prompt, order["worktree"], env, WORKER_TIMEOUT_S)
        else:
            p = runner(argv, input=prompt, cwd=order["worktree"], env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=WORKER_TIMEOUT_S, creationflags=studio.NO_WINDOW if os.name == "nt" else 0)
    except FileNotFoundError as e:
        return {"ok": False, "text": f"the worker program is missing ({e})", "cost": 0.0}
    timed_out = timed_out_flag(p)
    if order["worker"] == "codex":
        text = out_file.read_text(encoding="utf-8", errors="replace") if out_file.is_file() else (p.stdout or "")[-3000:]
        if timed_out:
            return {"ok": False, "text": "the worker ran out of time (" + plural(WORKER_TIMEOUT_S // 60, "minute") + ")", "cost": 0.0}
        return {"ok": p.returncode == 0, "text": text.strip()[:4000] or (p.stderr or "")[-600:], "cost": 0.0}
    data = {}
    for line in reversed((p.stdout or "").strip().splitlines()):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            data = parsed
            break
    known = "total_cost_usd" in data
    # Unknown cost: a run that worked for a while may have spent its whole budget, so it is charged that (the cap must never
    # under-count). A launch that died within seconds with nothing on stdout (bad flag, no login) spent nothing.
    ran_a_while = timed_out_flag(p) or (time.time() - started) > 30 or bool((p.stdout or "").strip())
    cost = (float(data.get("total_cost_usd") or 0.0) if known
            else (left if order["worker"] == "credit" and ran_a_while else 0.0))
    if order["worker"] == "credit" and cost:
        studio.record_cost(cost, CREDIT_MODEL, order["id"])
    if data.get("session_id"):
        order["session_id"] = data["session_id"]
    order["spent_usd"] = round(spent + (cost if order["worker"] == "credit" else 0.0), 4)
    if timed_out:
        return {"ok": False, "text": "the worker ran out of time (" + plural(WORKER_TIMEOUT_S // 60, "minute") + ")"
                + ("" if known else "; its cost is unknown, so the whole budget it had is counted"), "cost": cost}
    ok = p.returncode == 0 and not data.get("is_error") and data.get("subtype", "success") == "success" and bool(data)
    text = str(data.get("result") or "").strip() or (p.stderr or p.stdout or "")[-800:]
    if not ok and data.get("subtype"):
        text = f"{data['subtype']}: {text}"
    return {"ok": ok, "text": text[:4000], "cost": cost}


# ------------------------------------------------------------------------------------------------ the job
def ensure_worktree(order: Dict) -> None:
    """The order's own worktree on its own branch, cut from the base commit. A worktree left half-made by a crash is pruned
    and the branch reused."""
    wt = Path(order["worktree"])
    if (wt / ".git").exists():
        return
    wt.parent.mkdir(parents=True, exist_ok=True)
    studio.git("worktree", "prune")
    try:
        studio.git("rev-parse", "--verify", "--quiet", f"refs/heads/{order['branch']}")
    except RuntimeError:
        studio.git("worktree", "add", "-b", order["branch"], str(wt), order["base_sha"], timeout=3600)
        return
    studio.git("worktree", "add", str(wt), order["branch"], timeout=3600)


def scenario_plan(order: Dict) -> List[Dict]:
    """The order's scenarios plus any NEW one the worker added to the coordinator's table in its worktree
    (Scripts/integration_verify.ps1): a knockdown scenario the worker wrote is run too, never silently skipped.
    A scenario the worker removed from the table is still run (the order's plan is the floor)."""
    plan = list(order["tests"]["scenarios"])
    try:
        text = (Path(order["worktree"]) / "Scripts" / "integration_verify.ps1").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return plan
    have = {s["name"] for s in plan}
    for n, m, r in studio._SCEN_ROW.findall(text):
        if n not in have:
            plan.insert(sum(1 for s in plan if s.get("lane")), {"name": n, "map": m, "render": r == "r", "lane": True, "added": True})
            have.add(n)
    return plan


def verify(order: Dict, tag: str, runner=engine) -> Dict:
    """Both build targets, then (only if they built) the full suite and every integration scenario, the lane's first.
    -> {"ok", "stage", "digest", "results"}"""
    wt = Path(order["worktree"])
    studio.set_state(order, "building")
    builds = []
    for target in order["tests"].get("build") or ["LivingShadowWorldEditor"]:
        wait_for_quiet()
        b = digest_build(runner(wt, "build", tag, target=target))
        builds.append(f"{target} " + b["summary"])
        if not b["ok"]:
            return {"ok": False, "stage": "build", "digest": "\n".join(builds), "results": {"build": "\n".join(builds)}}
    studio.set_state(order, "testing")
    wait_for_quiet()
    t = digest_tests(runner(wt, "test", tag, timeout=60 * 60))
    results = {"build": "\n".join(builds), "automation": t["summary"], "scenarios": []}
    scen_ok = True
    for s in scenario_plan(order):
        wait_for_quiet()
        d = digest_scenario(s["name"], runner(wt, "scenario", tag, scenario=s, timeout=20 * 60))
        results["scenarios"].append(d["summary"])
        scen_ok = scen_ok and d["ok"]
    red = [x for x in results["scenarios"] if ": FAIL" in x.splitlines()[0]]
    return {"ok": t["ok"] and scen_ok, "stage": "tests", "digest": "\n".join([t["summary"]] + red), "results": results,
            "tests": t}


# never staged, whatever the worker left behind: secrets, agent folders, logs (the logs stay in the worktree as evidence)
_NEVER_STAGE = ("Documentation/BuildEvidence/**", "**/.codex/**", "**/.claude/**", "**/.mcp.json", "**/.env", "**/*.env",
                "**/.env.*", "**/*.log", "**/*.pem", "**/*.key", "**/Saved/**", "**/Intermediate/**", "**/Binaries/**",
                "**/DerivedDataCache/**")


def commit(order: Dict, green: bool, summary: str) -> str:
    """Commit on the order's branch only: if the worktree is on any other branch nothing is committed."""
    wt = Path(order["worktree"])
    head = studio.git("symbolic-ref", "--short", "HEAD", cwd=wt).strip()
    if head != order["branch"] or head in ("main", "integration"):
        raise RuntimeError(f"the worktree is on {head}, not {order['branch']}: nothing committed")
    rep = wt / "Documentation" / "StudioOrders" / f"{order['id']}.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(report_md(order), encoding="utf-8")
    specs = [f":(exclude,glob){x}" for x in _NEVER_STAGE]
    studio.git("add", "-A", "--", ".", *specs, cwd=wt)
    staged = studio.git("diff", "--cached", "--name-only", cwd=wt)
    if not staged.strip():
        return studio.git("rev-parse", "--short", "HEAD", cwd=wt).strip()
    msg = (f"studio({order['lane']}): {order['id']} {order['note'][:60]}" + ("" if green else " [NOT GREEN]")
           + f"\n\nDirector's note: {order['note']}\nWorker: {order['worker']}\n{summary[:1500]}\n\n"
           "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    studio.git("commit", "-q", "-m", msg, cwd=wt)
    return studio.git("rev-parse", "--short", "HEAD", cwd=wt).strip()


def report_md(order: Dict) -> str:
    r = order.get("results") or {}
    lines = [f"# {order['id']}: {order['note']}", "", f"- Lane: {order['role']}", f"- Branch: `{order['branch']}` from "
             f"`{order['base']}` {order['base_sha']}", f"- Worker: {order['worker']} ({order.get('worker_why', '')})",
             f"- Cost on the API credit: ${float(order.get('spent_usd') or 0):.2f}", "", "## Acceptance criteria"]
    lines += [f"{i + 1}. {c}" for i, c in enumerate(order["acceptance"])]
    lines += ["", "## JARVIS's verification (digests of the real logs in Documentation/BuildEvidence/)", "```",
              r.get("build", "not run"), r.get("automation", "not run")] + list(r.get("scenarios") or []) + ["```", "",
              "## Worker's report", "", order.get("worker_text", "")[:4000], ""]
    return "\n".join(lines)


def spoken_report(order: Dict) -> str:
    t = (order.get("results") or {})
    auto = re.search(r"automation: (\d+/\d+)", t.get("automation", "") or "")
    scen = t.get("scenarios") or []
    passed = sum(1 for s in scen if ": PASS" in s.splitlines()[0])
    what = (order.get("worker_text") or "").strip().split("\n")[0][:220]
    s = (f"{'Green' if order['state'] == 'green' else 'Not green'}: "
         f"build {'succeeded' if 'Succeeded' in (t.get('build') or '') else 'failed'}"
         + (f", tests {auto.group(1)}" if auto else "") + (f", scenarios {passed} of {len(scen)} passed" if scen else "")
         + f". Branch {order['branch']} at {order.get('commit', '?')}.")
    if what:
        s += f" The worker says: {what}"
    if order["state"] == "green":
        s += (f" To play it: run {order['worktree']}\\Scripts\\play.bat, or have the coordinator merge the branch into "
              "integration.")
    if order.get("spent_usd"):
        s += f" It cost ${float(order['spent_usd']):.2f} of the API credit."
    return s


def changed_source(order: Dict) -> bool:
    """Did the worker change anything under Source/, Config/ or Scripts/ (tracked or new files)? An unreadable worktree
    counts as changed: the build and tests then decide."""
    try:
        out = studio.git("status", "--porcelain", "--", "Source", "Config", "Scripts", "*.uproject", cwd=Path(order["worktree"]))
    except Exception:  # noqa: BLE001
        return True
    return bool(out.strip())


def _lock_path() -> Path:
    return studio.home() / "jobs" / "active.lock"


def take_lock(oid: str) -> str:
    """One studio job at a time, taken atomically (O_EXCL). -> "mine", "busy" (another live job holds it) or "same" (a live
    job for THIS order already runs: this process must not run it twice). A lock whose holder is dead is cleared."""
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                raw = lock.read_text(encoding="utf-8")
                cur = json.loads(raw)
            except (OSError, ValueError):
                time.sleep(0.2)                            # being written right now: look again, never judge a torn read
                continue
            if cur.get("pid") == os.getpid():
                return "mine"
            if studio.alive(cur.get("pid"), float(cur.get("created") or 0.0)):
                return "same" if cur.get("order") == oid else "busy"
            try:                                           # its holder is gone: remove THAT lock only, not a newer one
                if lock.read_text(encoding="utf-8") == raw:
                    lock.unlink()
            except OSError:
                pass
            continue
        created = 0.0
        try:
            import psutil
            created = psutil.Process(os.getpid()).create_time()
        except Exception:  # noqa: BLE001
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"order": oid, "pid": os.getpid(), "created": created, "t": time.time()}))
        return "mine"
    return "busy"


def drop_lock() -> None:
    try:
        cur = json.loads(_lock_path().read_text(encoding="utf-8"))
        if cur.get("pid") == os.getpid():
            _lock_path().unlink()
    except (OSError, ValueError):
        pass


def run(oid: str, worker=run_worker, verifier=verify, committer=commit, sleep=time.sleep) -> Dict:
    order = studio.load(oid)
    if order is None:
        raise SystemExit(f"no work order {oid}")
    if order["state"] in studio.FINAL:
        return order
    while True:
        got = take_lock(oid)
        if got == "mine":
            break
        if got == "same":
            log("a live job already runs this order; this copy stops")
            return order
        log("another studio job is running; waiting")
        sleep(GAME_POLL_S)
    try:
        return _run_locked(order, worker, verifier, committer)
    finally:
        drop_lock()


def _run_locked(order: Dict, worker, verifier, committer) -> Dict:
    studio.note_pid(order["id"], os.getpid())
    try:
        studio.set_state(order, "preparing")
        ensure_worktree(order)
        if order.get("worker_done"):                       # a restarted job: the first turn already ran (and was paid)
            w = {"ok": True, "text": order.get("worker_text", "")}
        else:
            studio.set_state(order, "working")
            log(f"worker {order['worker']} starts")
            prompt = prompt_for(order)
            order["prompt_sent"] = prompt[:40000]          # the exact prompt, for the dataset (never re-generated later)
            studio.save(order)
            w = worker(order, prompt, True)
            if w["ok"] and not changed_source(order):     # 2026-11-16: Codex stalled, changed nothing, and said so
                w = {"ok": False, "text": "the worker changed no source files: " + w["text"][:600], "cost": w.get("cost", 0)}
            order["worker_text"] = w["text"]
            order["worker_done"] = bool(w["ok"])
            studio.save(order)
        if not w["ok"]:
            order["report"] = f"The worker stopped: {w['text'][:300]}"
            committer(order, False, w["text"])
            done = studio.set_state(order, "failed", why=w["text"][:300])
            _keep(done)
            return done
        v = verifier(order, f"{order['id'].lower().replace('-', '')}r0")
        rounds = 0
        while not v["ok"] and rounds < MAX_FIX_ROUNDS:
            rounds += 1
            log(f"red at {v['stage']}; fix round {rounds}")
            studio.set_state(order, "working")
            w = worker(order, fix_prompt(order, v["digest"]), False)
            order["worker_text"] = (order.get("worker_text", "") + f"\n\n--- fix round {rounds} ---\n" + w["text"]).strip()
            studio.save(order)
            if not w["ok"]:
                break
            v = verifier(order, f"{order['id'].lower().replace('-', '')}r{rounds}")
        order["results"], order["fix_rounds"] = v["results"], rounds
        state = "green" if v["ok"] else "red"
        order["state"] = state
        order["commit"] = committer(order, v["ok"], v["digest"])
        order["report"] = spoken_report(order)
        done = studio.set_state(order, state)
        _keep(done)
        return done
    except Exception as e:  # noqa: BLE001 - the job must always leave a final state and a reason
        order["report"] = f"The job hit a problem: {type(e).__name__}: {str(e)[:300]}"
        done = studio.set_state(order, "failed", why=f"{type(e).__name__}: {str(e)[:300]}")
        _keep(done)
        return done


def _keep(order: Dict) -> None:
    """Green -> a verified lesson (+ dataset row, map update); anything else -> a recorded failure (core/studiolearn)."""
    try:
        from core import studiolearn
        studiolearn.record(order)
    except Exception as e:  # noqa: BLE001
        log(f"could not record the lesson: {type(e).__name__}: {e}")
