"""The owner's own conversation failures of 2026-11-14, replayed on the REAL local model, sandboxed, chat only.

    .venv\\Scripts\\python.exe scripts\\master_convo_eval.py --runs 3 [--label after]

Each scenario seeds the conversation the way it stood (his lines and what Jarvis really said, from data/jarvis.log) and then
puts the failing line to the real brain (Brain.ask: the polisher, the one more try and the chat path all run). Scored per run:

  loop       a reply that repeats an earlier reply of the scenario (chatturn.same_reply) for a different line of his
  reask      after his yes: the question he said yes to asked again
  unbound    after his yes: a reply that acts on nothing and answers nothing (an acknowledgement, 4 words or fewer, no tool)
  invented   a reply that names a project of his ('Shadow World', 'Shadow Vanguard') as something being worked on now
  handback   a reply that is the hand-back line (the guard fired: counted separately, never as a loop)
  lost       'I lost my train of thought' (the model gave nothing usable)
  acted      after his yes / complaint: a tool was used (the PC and the web are stubbed: nothing real is touched)

Sandboxed like scripts/latest_answer_eval.py: a temporary data folder, every PC action refused (pcbridge.require_owner), only the
read-only skills run, no cloud (JARVIS_LOCAL_ONLY). The GPU is shared with the live JARVIS: one run at a time, and it stops
when a game is running. Run it from each checkout (the branch's base and the branch) alternately; the model is not
deterministic, so compare runs taken in turn, never one run against an old number.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PC_BLOCKED = "(eval) PC actions are blocked here."
ALLOWED = {"remember", "recall", "recall_conversations", "recall_memories", "search_notes", "learn_lesson", "get_time", "read_list"}
PROJECT = re.compile(r"shadow\s*(?:world|vanguard)", re.I)
HAND_BACK = re.compile(r"same answer|won't ask you again|cannot place it", re.I)

# (name, [(his line, what Jarvis said)] already in the conversation, the failing lines put to the brain)
SCENARIOS = [
    ("dashboard_yes",
     [("Open Dashboard.", "The Command Center is up on your screen, sir."),
      ("I don't see it.", "I don't see a Dashboard on your screen, sir. Would you like me to open it now?")],
     ["Yes."]),
    ("dashboard_unseen",
     [("Open Dashboard.", "The Command Center is up on your screen, sir."),
      ("Bring it forward.", "I brought the Command Center to the front for you.")],
     ["I don't see it."]),
    ("headlines_yes",
     [("Do your research and tell me what the news is saying about that.",
       "Those claims are circulating on social media, not official guidance. If you’d like, I can show you the actual "
       "headlines from trusted sources so you can see what’s really being reported.")],
     ["Yes, I would like that very much."]),
    ("step_four_loop",
     [("Can you help me follow this step four?", "Which guide are you following, sir? A Blender plugin install, a tutorial page, "
                                                  "or something else?")],
     ["Open. Control. Center.", "Okay, it's a blender plug-in.", "Okay. In Blender.", "Yeah, in Blender, the plug-in."]),
    ("status_question",
     [("What's up?", "Not much, sir."), ("ChatGPT.", "ChatGPT is open, sir.")],
     ["What's the status on the thing that you were just working on?"]),
]
# What the tools that would act on the PC or the web answer here (nothing is done): enough for the reply that follows to be judged.
STUBS = {"open_jarvis_page": "Opened the Command Center page on the PC.", "see_pc_screen": "The screen shows the Jarvis Command Center "
         "in a browser on the right monitor, and Blender on the left.", "search_internet": "Top headlines (eval stub): 1. Officials say "
         "there is no order to shelter in place. 2. Grocery chains report normal stock. 3. Fact-checkers trace the rumor to a social post.",
         "research_online": "Findings (eval stub): no government shelter-in-place order was issued; the rumor started on social media.",
         "pc_command": "(eval) the PC was not touched."}
FACTS = ["The owner is building a game called Shadow World with a character called Shadow Vanguard.",
         "The owner is training for the Army Fitness Test."]


def score(name: str, said: str, reply: str, earlier: list, tools: list, hist_replies: list) -> dict:
    from core import chatturn
    flat = reply.strip()
    row = {"handback": bool(HAND_BACK.search(flat)), "loop": False, "reask": False, "unbound": False, "invented": False, "acted": False,
           "lost": "lost my train of thought" in flat}
    if not row["handback"]:
        row["loop"] = any(chatturn.same_reply(flat, old) for old in hist_replies if len(old.split()) >= 5) \
            and chatturn._flat(said) not in {chatturn._flat(e) for e in earlier}
    if name.endswith("_yes"):
        offer = hist_replies[-1] if hist_replies else ""
        ask = (re.findall(r"[^.!?]*\?", offer) or [""])[-1].strip()
        row["reask"] = bool(ask) and any(chatturn.same_reply(s, ask) or (len(s.split()) >= 4 and s.strip().endswith("?")
                                          and chatturn.same_reply(s.replace("?", ""), ask.replace("?", "")))
                                         for s in re.split(r"(?<=[.!?])\s+", flat))
        row["unbound"] = len(flat.split()) <= 4 and not tools and not row["handback"]
        row["acted"] = bool(tools) or bool(re.match(r"\(eval\) PC actions|Opened |Launched |The Command Center is up", reply))   # the PC path took it
    row["invented"] = bool(PROJECT.search(flat)) and name == "status_question"
    return row


def run_once() -> dict:
    sys.path.insert(0, str(ROOT))
    env = ROOT / ".env"
    if not env.exists():
        try:
            common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT,
                                    capture_output=True, text=True, timeout=10).stdout.strip()
            env = Path(common).parent / ".env"
        except (OSError, subprocess.SubprocessError):
            pass
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.partition("=")
        if key.strip().startswith(("JARVIS_LOCAL_", "JARVIS_INTELLIGENCE")) and value:
            os.environ[key.strip()] = value.strip().strip('"')
    os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
    os.environ["JARVIS_LOCAL_ONLY"] = "1"
    os.environ["JARVIS_GATEWAY"] = os.environ.get("MASTER_EVAL_GATEWAY", "on")      # as in production (the planner runs; the PC is blocked)
    for var in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
        os.environ.pop(var, None)
    from core import storage
    storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-master-"))
    for var, sub in (("JARVIS_VAULT", "vault"), ("JARVIS_COMPANION_DATA_DIR", "companion"), ("JARVIS_PC_STATE_DIR", "pc-control"),
                     ("JARVIS_PC_ROOT", "jarvis_pc"), ("JARVIS_MISSIONS_DIR", "missions")):
        os.environ[var] = str(storage.DATA / sub)
    import webbrowser
    webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = lambda *a, **k: True
    from brain.llm import Brain
    from core import acquire, config, pcbridge, users
    from tools import load_all, memory, registry
    load_all()
    users.set_current("owner")

    def blocked(uid=None):
        raise pcbridge.PCError(PC_BLOCKED, 403)
    pcbridge.require_owner = blocked
    ran: list = []
    real_run = registry.run

    def run(name, args):
        ran.append(name)                                  # a tool he chose is counted as acting
        if name in ALLOWED:
            return real_run(name, args)
        if name in STUBS:                                 # the PC is never touched: a canned success, so the next words can be judged
            return STUBS[name], False
        return f"(eval) {name} is not available here.", True
    registry.run = run
    for fact in FACTS:
        real_run("remember", {"fact": fact, "category": "general", "shared": False})
    if os.environ.get("MASTER_EVAL_TRACE"):
        from core import intelligence
        real_complete = intelligence.complete_local

        def traced(body, cancelled=lambda: False, on_text=None, **kw):
            try:
                out = real_complete(body, cancelled, on_text=on_text, **kw)
            except BaseException as error:
                print(f"    [model {body.get('role')}/{body.get('profile')}] RAISED {type(error).__name__}: {str(error)[:160]} cause={error.__cause__!r}"[:300], flush=True)
                raise
            try:
                message = out["choices"][0]["message"]
                last = body["messages"][-1]["content"]
                last = last if isinstance(last, str) else " | ".join(p.get("text", "")[:120] for p in last if isinstance(p, dict))
                print(f"    [model {body.get('role')}/{body.get('profile')}] in: {last[-260:]!r}", flush=True)
                print(f"      out: {str(message.get('content'))[:200]!r} "
                      f"calls={[c['function']['name'] for c in message.get('tool_calls') or []]}", flush=True)
            except Exception:  # noqa: BLE001
                pass
            return out
        intelligence.complete_local = traced
        real_failure = intelligence._audit_failure

        def failure(role, error, seconds):
            print(f"    [model {role}] failure detail: {type(error).__name__}: {str(error)[:200]}", flush=True)
            return real_failure(role, error, seconds)
        intelligence._audit_failure = failure
    cfg = config.Config()
    rows = []
    for name, seeded, lines in SCENARIOS:
        game = acquire.running_game()
        if game:
            print(f"STOPPED: a game is running ({game}); the GPU is his.", flush=True)
            sys.exit(3)
        brain = Brain(cfg.model, cfg.effort, config.load_personality(cfg, "owner"), uid="owner")
        brain.context = memory.context_block
        replies = []
        earlier = []
        for q, a in seeded:
            brain.messages.append({"role": "user", "content": q})
            brain.messages.append({"role": "assistant", "content": a})
            replies.append(a)
            earlier.append(q)
        brain._last_turn = time.monotonic()
        for line in lines:
            out: list[str] = []
            ran.clear()
            t = time.perf_counter()
            brain.ask(line, out.append)
            seconds = time.perf_counter() - t
            reply = " ".join(s.strip() for s in out if s and s.strip())
            row = {"scenario": name, "said": line, "reply": reply, "seconds": round(seconds, 1), "tools": list(ran)}
            row.update(score(name, line, reply, earlier, list(ran), list(replies)))
            flags = [k for k in ("loop", "reask", "unbound", "invented", "handback", "lost", "acted") if row[k]]
            print(f"[{name}] {seconds:4.1f}s {line[:40]:40} -> {reply[:170]}" + (f"  {{{', '.join(flags)}}}" if flags else ""),
                  flush=True)
            rows.append(row)
            earlier.append(line)
            replies.append(reply)
    import shutil
    shutil.rmtree(storage.DATA, ignore_errors=True)
    return {"rows": rows}


def totals(runs: list) -> str:
    rows = [r for run in runs for r in run["rows"]]
    seconds = sorted(r["seconds"] for r in rows)
    keys = ("loop", "reask", "unbound", "invented", "handback", "lost", "acted")
    return (f"{len(rows)} replies over {len(runs)} runs | " + " ".join(f"{k}={sum(r[k] for r in rows)}" for k in keys)
            + f" | p50 {seconds[len(seconds) // 2]:.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--label", default="")
    parser.add_argument("--out")
    parser.add_argument("--one", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.one:
        Path(args.one).write_text(json.dumps(run_once(), indent=1), encoding="utf-8")
        return 0
    results = []
    for k in range(args.runs):
        sys.path.insert(0, str(ROOT))
        from core import acquire
        game = acquire.running_game()
        if game:
            print(f"STOPPED before run {k + 1}: a game is running ({game}).")
            break
        print(f"--- run {k + 1}/{args.runs} {args.label}", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "run.json"
            code = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--one", str(dest)], cwd=ROOT).returncode
            if code != 0 or not dest.exists():
                print(f"run {k + 1} ended with code {code}")
                break
            results.append(json.loads(dest.read_text(encoding="utf-8")))
    if results:
        print("\n" + (args.label or "runs") + ": " + totals(results))
        if args.out:
            Path(args.out).write_text(json.dumps({"label": args.label, "runs": results}, indent=1), encoding="utf-8")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
