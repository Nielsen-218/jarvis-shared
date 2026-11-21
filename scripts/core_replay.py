"""Core replay: the owner's own everyday commands, replayed through the REAL voice path with NOTHING executed, and scored.

    .venv\\Scripts\\python.exe scripts\\core_replay.py                    # every case, one run each
    .venv\\Scripts\\python.exe scripts\\core_replay.py --runs 3 --out runs\\core-replay-after.json
    .venv\\Scripts\\python.exe scripts\\core_replay.py --only c012,c034 --verbose

Why: the core loop (hear, understand, act, confirm) is what he meets every day. Every change that can move it is scored on
the same frozen lines before it ships (DEVELOPMENT-HANDOFF 2026-11-16, core scorecard).

The cases are his real lines (data/learning/core-replay.json, built by hand from data/jarvis.log, Nov 8-15 2026): what he
said as Whisper wrote it, what he meant (the expected action), and whether Jarvis got it right live on the first try. His
words stay in his data folder: this script prints case ids, kinds and verdicts, and writes details only to --out.

The path is the voice loop's (main.py): wake-word strip, fix_misheard, pre_brain_route (stop / dismiss / never mind), then
Brain.ask -> quick answers -> own screens (Command Center, World Intelligence) -> core.pcfast -> core.gateway.decide -> the
local brain. It reuses scripts/command_latency.py's sandbox: a temporary data folder, gateway.execute recorded instead of
run, registry tools and launches refused. In addition here: the Hub (hud.dashboard / hud.front / hud.world), the globe
(worldintel._world_action, open_full_app; the workspace is treated as open) and focus/minimize/close are recorded.
Reads of the PC (open windows, CPU, RAM) are real; nothing on the PC changes.

A case passes when the recorded action matches what he meant (or, for a question, the reply has the reading he asked for)
and nothing he did not ask for was decided (a close for a minimize fails). Model-routed lines vary run to run: use --runs 3
and compare sets, not a single run. Exit code 1 when --min is given and the pass rate is below it.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

os.environ["JARVIS_TEST_TRAFFIC"] = "1"           # nothing this replay says is the owner's (core.turntrace.test_traffic)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _main_checkout() -> Path:
    """The live checkout (the worktree's git common dir's parent): the cases live in its data folder."""
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, capture_output=True, text=True,
                                timeout=10).stdout.strip()
        if common:
            path = (ROOT / common).resolve() if not Path(common).is_absolute() else Path(common)
            return path.parent
    except Exception:  # noqa: BLE001
        pass
    return ROOT


DEFAULT_CASES = _main_checkout() / "data" / "learning" / "core-replay.json"
try:                        # read now: the replay's sandbox refuses to start programs (git included) once it is installed
    HEAD = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
except Exception:  # noqa: BLE001
    HEAD = ""

# Which tools count as which action (gateway steps and brain tool calls alike).
ACT_TOOLS = {
    "open": {"open_app", "launch_app", "open_path", "focus_app", "open_url", "browse_open"},
    "close": {"close_app", "close_foreground", "close_window"},
    "close_tab": {"close_browser_tab", "close_site_tab"},
    "minimize": {"minimize_app", "minimize_foreground", "minimize_window"},
    "focus": {"focus_app", "open_app", "bring_to_front"},
}
APP_KEYS = ("app", "process", "name", "target", "path", "title", "query", "url", "site")
# Tools that only read the PC (a reading he asked for): they really run in the replay and never count as acting.
READ_ONLY = {"get_system_status", "pc_status", "list_windows", "read_screen", "get_time", "get_date", "get_weather",
             "investigate_slowness", "top_processes", "where_am_i", "briefing_info", "get_location"}


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


# ------------------------------------------------------------------------------------------------ the sandbox (lazy)
H = None          # scripts.command_latency, imported only when the replay runs (it builds the sandbox on import)
EXTRA: dict = {"screen": [], "world": [], "front": 0}


_ORIG_RUN = None


def _install() -> None:
    global H, _ORIG_RUN
    if H is not None:
        return
    from tools import registry as _r0
    _ORIG_RUN = _r0.run                              # the real one, before command_latency stubs it
    from scripts import command_latency as cl       # noqa: E402  (sandbox, stubs, guard)
    H = cl
    from core import hud, worldintel

    def dashboard() -> bool:
        EXTRA["screen"].append("dashboard")
        getattr(hud, "_opened", lambda w: None)("the Command Center")
        return True

    def front() -> bool:
        EXTRA["front"] += 1
        return True

    def world(preset: str = "standard") -> bool:
        EXTRA["world"].append(("preset", {"preset": preset}))
        getattr(hud, "_opened", lambda w: None)("World Intelligence")
        return True

    def world_action(action: str, timeout: float = 10.0, **args):
        EXTRA["world"].append((action, args))
        return {"ok": True, "detail": f"(harness: {action})"}

    def full_app() -> dict:
        EXTRA["world"].append(("own_window", {}))
        return {"ok": True, "detail": "(harness: own window)"}

    from tools import registry as _reg
    fake = H._fake_run

    def run(name, args):
        if name in READ_ONLY and _ORIG_RUN is not None:
            H.RECORD["tools"].append({"tool": name, "arguments": args, "read_only": True})
            return _ORIG_RUN(name, args)
        return fake(name, args)
    _reg.run = run
    hud.dashboard = dashboard
    hud.front = front
    hud.world = world
    hud.running = lambda: True
    hud.in_front = lambda timeout=2.0: True          # the window check reads the real desktop: the replay scores the decision
    worldintel._world_action = world_action
    worldintel.workspace_open = lambda: True
    worldintel.open_full_app = full_app
    # A protocol's steps call the PC directly (core.pcactions, core.gameboost, Steam), not through the registry: replayed
    # 2026-11-21 ("Streaming Protocol.") it really closed Grammarly, lowered the coding agents' priority and asked Steam to
    # start a game. Every step is recorded instead, as a tool named protocol.<do>; nothing of it runs.
    from core import protocols

    def step_recorder(do):
        def record(step, ctx):
            H.RECORD["tools"].append({"tool": f"protocol.{do}", "arguments": dict(step)})
            return protocols.DONE, f"(harness: {do} not run)"
        return record
    for _do in list(protocols.ACTIONS):
        protocols.ACTIONS[_do] = step_recorder(_do)
    from core import obsstream                       # "go live" / "end the stream" drive the real OBS: recorded instead

    def stream_recorder(what):
        def record(wait=None):
            H.RECORD["tools"].append({"tool": f"obsstream.{what}", "arguments": {}})
            return f"(harness: {what} not run)"
        return record
    obsstream.go_live, obsstream.end = stream_recorder("go_live"), stream_recorder("end")
    try:
        from tools import interface
        interface.open_jarvis_page = lambda page: (EXTRA["screen"].append(page), f"(harness: {page} page)")[1]
    except Exception:  # noqa: BLE001
        pass
    try:                                             # the screen look (pcfast) is stubbed by command_latency: record it as a reading
        from tools import pcview
        _look = pcview.see_pc_screen

        def see(*a, **k):
            H.RECORD["tools"].append({"tool": "see_pc_screen", "arguments": {}, "read_only": True})
            return _look(*a, **k)
        pcview.see_pc_screen = see
    except Exception:  # noqa: BLE001
        pass
    if os.environ.get("CORE_REPLAY_SKILLS"):         # --skills: his learned PC skills, copied (read-only) into the sandbox
        import shutil
        dest = Path(os.environ["JARVIS_PC_ROOT"])
        dest.mkdir(parents=True, exist_ok=True)
        src = Path(os.environ["CORE_REPLAY_SKILLS"]) / "playbooks.jsonl"
        if src.is_file():
            shutil.copy2(src, dest / "playbooks.jsonl")


def replay_one(case: dict) -> dict:
    """One case, through a fresh Brain: its context lines first (not scored), then the line itself."""
    _install()
    import main as voice_loop
    brain = H.new_brain()
    for line in case.get("context") or []:
        _say(brain, voice_loop, line)
    EXTRA.update(screen=[], world=[], front=0)
    H.RECORD.update(steps=[], tools=[], calls=[])
    t0 = time.time()
    pre, reply = _say(brain, voice_loop, case["said"])
    took = time.time() - t0
    return {"pre": pre, "reply": reply[:300], "seconds": round(took, 2),
            "steps": [s for d in H.RECORD["steps"] for s in d["steps"]],
            "questions": [q for d in H.RECORD["steps"] for q in d["questions"]],
            "skills": [k for d in H.RECORD["steps"] for k in d["skills"] if k],
            "brain_tools": list(H.RECORD["tools"]), "screen": list(EXTRA["screen"]), "world": list(EXTRA["world"]),
            "front": EXTRA["front"], "models": len(H.RECORD["calls"])}


def _say(brain, voice_loop, said: str) -> tuple[str, str]:
    from core import approvals, pcsemantic
    pcsemantic._CACHE.clear()
    getattr(approvals, "_MEANT", {}).clear()
    strip = getattr(voice_loop, "without_wake", lambda t: voice_loop.WAKE_PREFIX.sub("", t).strip())   # main before 2026-11-16
    command = voice_loop.fix_misheard(strip(said))
    norm = command.lower().strip(" .!?,")
    if not norm:
        return "empty", ""
    pre = voice_loop.pre_brain_route(norm, [])
    if pre not in ("respond", "decline"):
        return pre, ""
    out: list = []
    brain.ask(command, lambda s: out.append(s) if isinstance(s, str) else None)
    return pre, " ".join(out)


# ------------------------------------------------------------------------------------------------ scoring (pure)
def _args_text(args: dict) -> str:
    if not isinstance(args, dict):
        return str(args).casefold()
    return " ".join(str(args.get(k, "")) for k in APP_KEYS).casefold()


def _acts(result: dict) -> list[tuple[str, str]]:
    """Every action decided or called: (tool, its target words)."""
    rows = [(s.get("tool") or "", _args_text(s.get("arguments") or {})) for s in result.get("steps", [])
            if s.get("tool") not in READ_ONLY]
    rows += [(str(k), "") for k in result.get("skills", [])]          # a learned skill (skill.close_site_tab ...)
    rows += [(t.get("tool") or "", _args_text(t.get("arguments") or {})) for t in result.get("brain_tools", [])
             if not t.get("read_only")]
    return rows


def _in(tool: str, fam: str) -> bool:
    """Is this tool (or learned skill id, 'skill.close_site_tab') one of the family's?"""
    return tool in ACT_TOOLS[fam] or any(tool.endswith("." + t) or tool.startswith(t) for t in ACT_TOOLS[fam] if "_" in t)


def _family(tool: str) -> str:
    for fam in ACT_TOOLS:
        if fam != "focus" and _in(tool, fam):
            return fam
    return ""


def _score_tools(want: dict, result: dict, acts: list) -> tuple[bool, str]:
    """Kinds with no action family (typing, media, web/YouTube search, saving, protocols): every regex in want["tools"]
    must match some recorded tool or skill id (read-only ones included), and want["arg"], when given, must appear in that
    call's arguments. Recorded, never run (the same sandbox as the families)."""
    calls = [(s.get("tool") or "", json.dumps(s.get("arguments") or {}, ensure_ascii=False).casefold())
             for s in result.get("steps", [])]
    calls += [(str(k), "") for k in result.get("skills", [])]
    calls += [(t.get("tool") or "", json.dumps(t.get("arguments") or {}, ensure_ascii=False).casefold())
              for t in result.get("brain_tools", [])]
    forbid = set(want.get("forbid") or [])
    for tool, _ in acts:
        if _family(tool) in forbid:
            return False, f"also decided {tool}"
    pats = want["tools"] if isinstance(want["tools"], list) else [want["tools"]]
    arg = (want.get("arg") or "").casefold()
    for pat in pats:
        if not any(re.search(pat, t, re.I) and (not arg or arg in a or arg in t.casefold()) for t, a in calls):
            if want.get("or_ask") and result.get("questions"):
                return True, "asked which"
            got = calls[0][0] if calls else ("asked: " + result["questions"][0][:40] if result.get("questions") else "nothing decided")
            return False, f"no {pat} ({got})"
    if want.get("say") and not re.search(want["say"], result.get("reply") or ""):
        return False, "no reading"
    return True, " + ".join(pats)


def score(case: dict, result: dict) -> tuple[bool, str]:
    """(passed, why). Pure: the same result always scores the same."""
    want = case["expect"]
    acts = _acts(result)
    reply = result.get("reply") or ""
    if want.get("none"):
        if want.get("pre") and result.get("pre") not in want["pre"]:
            return False, f"routed {result.get('pre')}"
        return (not acts and not result["screen"] and not result["world"]), "acted" if acts else "nothing done"
    if "tools" in want:
        return _score_tools(want, result, acts)
    if "say" in want:
        if acts and not want.get("or_read"):
            return False, f"acted: {acts[0][0]}"
        if re.search(want["say"], reply) and not acts:
            return True, "reading given"
        # gateway.execute is recorded, not run, so a reading the gateway decided (a learned fact skill, read_screen) has no
        # reply to score: want["or_read"] names the readings that answer the question; the decision is scored, marked so.
        reads = [s.get("tool") or "" for s in result.get("steps", [])] + [str(k) for k in result.get("skills", [])]
        reads += [t.get("tool") or "" for t in result.get("brain_tools", [])]
        if want.get("or_read") and any(re.search(want["or_read"], r, re.I) for r in reads) and \
                all(re.search(want["or_read"], t, re.I) for t, _ in acts):
            return True, "reading decided (not run)"
        return False, f"acted: {acts[0][0]}" if acts else "no reading"
    if "screen" in want:
        ok = want["screen"] in result["screen"] or (result["front"] and want["screen"] == "dashboard" and not acts)
        return bool(ok), "screen opened" if ok else f"no screen ({acts[0][0] if acts else 'nothing'})"
    if "world" in want:
        w, arg = want["world"], (want.get("arg") or "").casefold()
        done = result["world"]
        if w == "open":
            ok = any(a == "preset" for a, _ in done) or bool(result["front"])
        elif w == "goto":
            ok = any(a == "goto" and arg in str(x.get("title", "")).casefold() for a, x in done)
        elif w == "layers":
            ok = any(a == "layers" and (not arg or arg in " ".join(map(str, x.get("show") or [])).casefold()) for a, x in done)
        else:
            ok = any(a == w for a, _ in done) or (w == "preset" and any(a == "preset" for a, _ in done))
        if not ok and want.get("or_focus") and any(_in(t, "focus") for t, _ in acts):
            ok = True
        return bool(ok), "globe did it" if ok else f"globe: {done[:2] or 'nothing'}"
    act, app = want["act"], (want.get("app") or "").casefold()
    forbid = set(want.get("forbid") or [])
    for tool, _ in acts:
        if _family(tool) in forbid:
            return False, f"also decided {tool}"
    hit = [(t, a) for t, a in acts if _in(t, act) and (not app or app in a or app in t)]
    if hit:
        return True, hit[0][0]
    if want.get("or_ask") and result.get("questions"):
        return True, "asked which"
    return False, (f"decided {acts[0][0]} {acts[0][1][:20]}" if acts else ("asked: " + result["questions"][0][:40]
                                                                             if result.get("questions") else "nothing decided"))


# ------------------------------------------------------------------------------------------------ report
def summary(cases: list[dict], verdicts: dict) -> dict:
    by_kind: dict = collections.defaultdict(lambda: [0, 0])
    total = passed = mis_total = mis_passed = live = 0
    for c in cases:
        runs = verdicts.get(c["id"])
        if not runs:
            continue
        ok = sum(runs) * 2 > len(runs)            # the majority of runs
        total += 1
        passed += ok
        live += bool(c.get("live_first_try"))
        by_kind[c["kind"]][0] += ok
        by_kind[c["kind"]][1] += 1
        if c.get("mishear"):
            mis_total += 1
            mis_passed += ok
    return {"cases": total, "passed": passed, "rate": round(passed / total, 3) if total else None,
            "live_first_try": live, "mishears": [mis_passed, mis_total], "by_kind": {k: v for k, v in sorted(by_kind.items())}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--min", type=float, default=0.0)
    ap.add_argument("--skills", default="", help="copy his learned PC skills (playbooks.jsonl) from this folder, e.g. C:/Jarvis")
    ap.add_argument("--verbose", action="store_true", help="print his words and the replies (stays on this screen)")
    ap.add_argument("--save", action="store_true", help="write the summary for the Hub (data/learning/core-replay-score.json)")
    args = ap.parse_args()
    cases = load_cases(Path(args.cases))
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep or c["kind"] in keep]
    if args.skills:
        os.environ["CORE_REPLAY_SKILLS"] = args.skills
    _install()
    from core import gateway, intelligence
    print(f"{len(cases)} cases | model {intelligence.model('fast')} | gateway {gateway.mode()}", flush=True)
    verdicts, details = {}, {}
    for c in cases:
        for _ in range(args.runs):
            r = replay_one(c)
            ok, why = score(c, r)
            verdicts.setdefault(c["id"], []).append(ok)
            details.setdefault(c["id"], []).append(dict(r, passed=ok, why=why))
            line = f"{c['id']} {c['kind']:11s} {'PASS' if ok else 'FAIL'} {r['seconds']:5.1f}s  {why}"
            if args.verbose:
                line += f"\n      {c['said']!r} -> {r['reply'][:140]!r}"
            print(line, flush=True)
    s = summary(cases, verdicts)
    print(json.dumps(s, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": s, "details": details}, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.save and not args.only:
        out = dict(s, when=time.strftime("%Y-%m-%d %H:%M"), commit=HEAD, runs=args.runs)
        (Path(args.cases).parent / "core-replay-score.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 1 if args.min and (s["rate"] or 0) < args.min else 0


if __name__ == "__main__":
    sys.exit(main())
