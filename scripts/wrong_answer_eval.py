"""Wrong answers: the replies the owner cut off or corrected, replayed with their real context, NOTHING executed, and scored.

    .venv\\Scripts\\python.exe scripts\\wrong_answer_eval.py --cases data\\learning\\wrong-answers.json --runs 2 --out after.json
    .venv\\Scripts\\python.exe scripts\\wrong_answer_eval.py --rescore after.json          # score a saved run again (no model)

Owner 2026-11-21: wrong answers are the #1 reason he cuts Jarvis off. The cases are his real lines from data/jarvis.log
(Nov 8-21) that got a wrong reply (hand-labelled: wrong answer or action, a claim of work that never started, an answer to
an earlier topic), plus a control set of his lines that were answered right and must stay right. Each case carries the
exchange(s) before it exactly as they happened (his words and Jarvis's reply), put into the conversation as history, then
his line goes through the voice path (main.fix_misheard, pre_brain_route, Brain.ask -> quick -> pcfast -> gateway -> brain).

The sandbox is scripts/core_replay.py's (scripts/command_latency.py): a temporary data folder, gateway steps and tool calls
recorded instead of run, the Hub and the globe stubbed, no program started, no paid model (JARVIS_LOCAL_ONLY). His words
stay in his data folder: this script prints case ids, causes and verdicts, and writes the replies only to --out.

A case fails on any of (judged here, independently of the guards in core.intelligence):
  acted      a chat case decided or called an action
  wrong_act  an action case did not decide the action he meant (core_replay.score)
  guide      the reply is the "follow the instructions on your screen" route for a line that pointed at nothing
  work       claimed or promised work with no action recorded ("I am starting now", "I will fix your Jarvis")
  as_i_said  "As I said" (none of these lines repeated a question)
  cloud      says Jarvis runs on a paid or cloud model, or that its local intelligence is unavailable
  bad        the case's own pattern of the wrong reply it got live (an earlier topic, a canned line)
  no_good    the case's pattern of a right answer is missing (control cases mostly)
  silent     nothing said to a line that was answered (a dismissal route is not silence)

A case passes when MORE than half of its runs pass: with --runs 2 both must pass. Compare before/after with the same --runs.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scripts import core_replay as cr  # noqa: E402  (sets JARVIS_TEST_TRAFFIC; the sandbox itself is built lazily)

GUIDE = re.compile(r"reading your screen|instructions (?:on|in) (?:your|the) screen|window in front|step-by-step instructions|"
                   r"guide, readme|turn them into steps", re.I)
WORK = re.compile(
    r"\bi(?:'m| am) (?:now )?(?:starting|upgrading|fixing|improving|working on|generating|building|creating|making|setting up|"
    r"pulling|on it)\b|"
    r"\bi(?:'ll| will| am going to|'m going to) (?:now |go ahead and )?(?:fix|upgrade|improve|build|create|generate|start|begin|"
    r"work on|set up|research|pull|get (?:started|to work|on it)|add|teach)\b|"
    r"\bgive me a (?:moment|second|minute)\b|\bstarting now\b|\blet me (?:start|pull|get|begin|set)\b", re.I)
AS_I_SAID = re.compile(r"\bas i said\b", re.I)
CLOUD = re.compile(r"\bi(?:'m| am) (?:using|running on|on|thinking on) (?:a |the |my )?(?:paid|cloud|claude|anthropic|gemini|openai)\b|"
                   r"\b(?:paid|cloud) (?:model|brain|thinking) (?:is|for) (?:this|that|answering|you)\b|"
                   r"\blocal (?:intelligence|model|brain|thinking) (?:is |isn'?t |was )?(?:unavailable|offline|down|not available)\b", re.I)


def _offers_only(sentence: str) -> bool:
    """A work word inside a question or an offer that waits for him is not a claim ("Want me to start?")."""
    s = sentence.strip()
    return s.endswith("?") or bool(re.search(r"\b(?:if you|once you|when you|say the word|want me to|shall i|should i|"
                                             r"would you like)\b", s, re.I))


def judge(case: dict, r: dict) -> list[str]:
    """The case's failure flags (empty: passed). Pure."""
    want = case["expect"]
    reply = (r.get("reply") or "").strip()
    acts = cr._acts(r)
    acted = bool(acts or r.get("screen") or r.get("world"))
    flags = []
    if want.get("chat"):
        if acted:
            flags.append("acted")
    elif want.get("none"):
        if acted:
            flags.append("acted")
        if want.get("pre") and r.get("pre") not in want["pre"]:
            flags.append("wrong_route")
    elif not want.get("any"):
        ok, _why = cr.score(case, r)
        if not ok:
            flags.append("wrong_act")
    if GUIDE.search(reply):
        flags.append("guide")
    if not acted and any(WORK.search(s) and not _offers_only(s) for s in re.split(r"(?<=[.!?])\s+", reply)):
        flags.append("work")
    if AS_I_SAID.search(reply):
        flags.append("as_i_said")
    if CLOUD.search(reply):
        flags.append("cloud")
    if any(re.search(p, reply, re.I) for p in case.get("bad") or []) and reply:
        flags.append("bad")
    if case.get("good") and not re.search(case["good"], reply):
        flags.append("no_good")
    if not reply and r.get("pre") == "respond" and not acted and not want.get("none"):
        flags.append("silent")
    return flags


def replay(case: dict) -> dict:
    """One case through a fresh Brain: its real context put into history as it happened, then his line."""
    cr._install()
    import main as voice_loop
    brain = cr.H.new_brain()
    for you, jarvis in case.get("ctx") or []:
        brain._record(you, jarvis)
    brain._last_turn = time.monotonic()      # the context is the conversation of a moment ago, not an old one to archive
    cr.EXTRA.update(screen=[], world=[], front=0)
    cr.H.RECORD.update(steps=[], tools=[], calls=[])
    t0 = time.time()
    pre, reply = cr._say(brain, voice_loop, case["said"])
    return {"pre": pre, "reply": reply[:600], "seconds": round(time.time() - t0, 2),
            "steps": [s for d in cr.H.RECORD["steps"] for s in d["steps"]],
            "questions": [q for d in cr.H.RECORD["steps"] for q in d["questions"]],
            "skills": [k for d in cr.H.RECORD["steps"] for k in d["skills"] if k],
            "brain_tools": list(cr.H.RECORD["tools"]), "screen": list(cr.EXTRA["screen"]), "world": list(cr.EXTRA["world"]),
            "front": cr.EXTRA["front"]}


def summary(cases: list[dict], details: dict) -> dict:
    """Per cause: cases passed by the majority of runs, and every flag counted over all runs."""
    by: dict = collections.defaultdict(lambda: [0, 0])
    flags: collections.Counter = collections.Counter()
    for c in cases:
        runs = details.get(c["id"]) or []
        if not runs:
            continue
        ok = sum(not r["flags"] for r in runs) * 2 > len(runs)
        group = "control" if c["cause"] == "control" else "wrong"
        for key in (group, f"{group}:{c['cause']}"):
            by[key][0] += ok
            by[key][1] += 1
        for r in runs:
            flags.update(f"{group}:{f}" for f in r["flags"])
    return {"passed": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by.items())}, "flags": dict(sorted(flags.items()))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", default=str(cr._main_checkout() / "data" / "learning" / "wrong-answers.json"))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", default="", help="comma-separated case ids or causes")
    ap.add_argument("--out", default="")
    ap.add_argument("--rescore", default="", help="score a saved --out file again (no model calls)")
    args = ap.parse_args()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    bad = [c.get("id", "?") for c in cases if not all(k in c for k in ("id", "cause", "said", "expect"))]
    if bad:
        print(f"cases without id/cause/said/expect: {', '.join(map(str, bad))}")
        return 2
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep or c["cause"] in keep]
        if not cases:
            print(f"no case matches --only {args.only}")
            return 2
    if args.rescore:
        saved = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        index = {c["id"]: c for c in cases}
        details = {k: [dict(r, flags=judge(index[k], r)) for r in v] for k, v in saved["details"].items() if k in index}
        print(json.dumps(summary(cases, details), indent=1))
        return 0
    from core import acquire
    cr._install()
    from core import gateway, intelligence
    print(f"{len(cases)} cases | model {intelligence.model('fast')} | gateway {gateway.mode()} | head {cr.HEAD}", flush=True)
    details: dict = {}
    for k in range(args.runs):
        for c in cases:
            game = acquire.running_game()
            if game:
                print(f"STOPPED: a game is running ({game}); the GPU is his.", flush=True)
                return 3
            r = replay(c)
            r["flags"] = judge(c, r)
            details.setdefault(c["id"], []).append(r)
            print(f"[{k + 1}] {c['id']} {c['cause']:8s} {'PASS' if not r['flags'] else 'FAIL'} {r['seconds']:5.1f}s "
                  f"{','.join(r['flags'])}", flush=True)
    s = summary(cases, details)
    print(json.dumps(s, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps({"head": cr.HEAD, "summary": s, "details": details,
                                              "said": {c["id"]: c["said"] for c in cases}}, indent=1, ensure_ascii=False),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
