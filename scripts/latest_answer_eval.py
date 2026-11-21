"""Does Jarvis answer HIS LATEST message? A scored 31-turn chat on the REAL local model, sandboxed, chat only.

    .venv\\Scripts\\python.exe scripts\\latest_answer_eval.py --runs 3 [--gateway off|on] [--setup FILE] [--out FILE]

Two early decisions ("Let's call my new garage project Falcon." / the workbench on Saturday with Marcus), 26 unrelated
questions with known answers, then 3 questions about the early decisions. Real probes (2026-11-10) showed qwen3.5:9b
copying earlier answers ("What's a synonym for happy?" -> "Gracias"), dragging the early topic into unrelated replies
("Falcon is noted as your garage project" on every reply) and ending most replies with an offer ("Shall I log that
under Falcon?"). Scored per run:

  correct      the 26 filler questions answered right (an expected-answer pattern per question)
  copied       a filler reply carrying an EARLIER question's answer ("Gracias" for a synonym of happy)
  contaminated a filler reply mentioning the early topic (Falcon, Marcus, the workbench, the garage, Saturday)
  offers       filler replies ending on a question or an offer ("Shall I save that to your notes?")
  as_i_said    filler replies with "As I said" (nothing was asked twice: always wrong here)
  late         the 3 recall questions answered from the early decisions (must stay 3/3)
  p50/p90      seconds per filler turn; calls = local model calls per filler turn, by caller

Sandboxed like scripts/convo_local.py: a temporary data folder, the PC plan ledger and roots moved into it, no cloud
(JARVIS_LOCAL_ONLY), and only the local-model routing settings (JARVIS_LOCAL_*, JARVIS_INTELLIGENCE) read from .env.
Chat only: every PC action is blocked (pcbridge.require_owner refuses, so a turn the PC layer claims shows up as a
'pc' miss and runs nothing) and every skill except the read-only/sandboxed ones in ALLOWED is refused. Each run is its
own process (fresh memory). The GPU is shared with the live JARVIS: runs go one at a time and stop if a game is running.
--setup FILE runs a Python file inside each run before the chat starts (experimental monkeypatches for a variant).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EARLY = ["Let's call my new garage project Falcon.",
         "The plan is to build the workbench on Saturday, and my brother Marcus is helping me."]
# (question, the right answer, a signature of THIS answer that would be a copy if it turned up in a later reply or None)
FILLER = [
    ("What's the capital of Australia?", r"canberra", r"canberra"),
    ("How many minutes are in a day?", r"1,?440|fourteen hundred (?:and )?forty|one thousand,? four hundred (?:and )?forty",
     r"1,?440|fourteen hundred"),
    ("Give me one tip for sleeping better.", r"sleep|bed|screen|caffeine|dark|routine|schedule|light|cool|nap|wind down|"
     r"relax|alcohol|phone", r"\bsleep\w*|\bbedtime|\bcaffeine"),
    ("What's a good name for a gray cat?", r"\b(?:smok|ash|gr[ae]y|silver|shadow|storm|pebble|slate|mist|cloud|dust|"
     r"graphite|cinder|flint|steel|pewter|nimbus|stone|earl|sterling|fog|haze|smudge|pepper|mouse|luna|gandalf|merlin|"
     r"dove|ghost|charcoal|tin|iron|blue|ember|whisk|sage|willow|moon|thunder|granite|phantom|wolf|dusk|echo|soot|"
     r"nickel|cobalt|ivy|tabby)\w*", None),
    ("How far is the moon, roughly?", r"238,?\d{3}|239,?\d{3}|240,?000|384,?\d{3}|385,?000|quarter of a million|"
     r"two hundred (?:and )?(?:thirty|forty)|three hundred (?:and )?eighty[- ]f(?:our|ive) thousand|\b23[89]\b|"
     r"\b38[45]\b", r"238,?\d{3}|239,?000|384,?\d{3}"),
    ("What's 17 times 23?", r"\b391\b|three hundred (?:and )?ninety-?one", r"\b391\b"),
    ("Tell me a fun fact about octopuses.", r"octop|heart|tentacle|\barms?\b|blue blood|ink|camouflage|brain|neuron",
     r"octop"),
    ("What does a carburetor do, in one sentence?", r"\bair\b|fuel|mix|engine|combust", r"carbur"),
    ("Which is heavier, a pound of feathers or a pound of bricks?", r"same|neither|equal|both weigh|a pound is a pound",
     r"feather|brick"),
    ("What's the boiling point of water in Fahrenheit?", r"\b212\b|two hundred (?:and )?twelve|\btwo[- ]twelve\b", r"\b212\b"),
    ("Give me a quick idea for dinner tonight.", r"chicken|pasta|rice|taco|stir|salad|soup|beans|eggs?\b|omelet+e?|curry|"
     r"fish|beef|pork|tofu|sandwich|pizza|burger|veg|potato|noodle|quesadilla|wrap|chili|steak|salmon|griot|diri|bake|"
     r"grill|roast|shrimp|tuna|turkey|lentil|bowl", r"stir-?fry|chicken thighs?"),
    ("How do you say thank you in Spanish?", r"gracias", r"gracias|spanish"),
    ("What year did the first moon landing happen?", r"\b1969\b|nineteen sixty-?nine", r"\b1969\b|nineteen sixty"),
    ("What's a synonym for happy?", r"\b(?:glad|joyful|cheerful|content(?:ed)?|elated|merry|delighted|jolly|pleased|"
     r"upbeat|ecstatic|thrilled|jubilant|blissful|chipper|sunny|overjoyed|euphoric|gleeful|joyous|buoyant|carefree|"
     r"jovial|exuberant|happy-go-lucky|beaming|radiant|chirpy|light-hearted|lighthearted)\b",
     r"\bhappy\b(?! to)|\bfeliz\b|\bjoyful\b|\bcheerful\b|\bglad\b(?! to)"),
    ("How many legs does a spider have?", r"\b8\b|\beight\b", r"\bspider"),
    ("What's the square root of 144?", r"\b12\b|\btwelve\b", r"\b144\b|square root"),
    ("Name a classic science fiction movie.", r"blade runner|2001|alien|star wars|matrix|metropolis|terminator|"
     r"close encounters|\be\.?t\.?\b|day the earth|forbidden planet|planet of the apes|back to the future|solaris|gattaca|"
     r"dune|interstellar|arrival|inception|the thing|invasion of|war of the worlds|robocop|total recall|tron|the fly|"
     r"jurassic|minority report|ex machina|contact|star trek|brazil|logan's run|soylent|stalker|\bmoon\b|martian|"
     r"district 9|the abyss|predator|they live|space odyssey|fifth element|silent running|twelve monkeys|starship",
     r"blade runner|2001|star wars|matrix|metropolis|space odyssey"),
    ("How long should I boil an egg?", r"\d+\s*(?:to|-|–)?\s*\d*\s*minutes|minutes|\b(?:three|four|five|six|seven|eight|"
     r"nine|ten|eleven|twelve)\b", r"\begg"),
    ("What's the tallest mountain in the world?", r"everest", r"everest"),
    ("Why is the sky blue, in one sentence?", r"scatter|rayleigh|wavelength|sunlight|\blight\b", r"rayleigh|scatter"),
    ("What's the freezing point of water in Celsius?", r"\b0\b|\bzero\b", r"freez"),
    ("Give me a short motivational line.", r"\byou\b|\byour\b|every|step|today|keep|start|done|great|never|believe|"
     r"progress|journey|success|dream|goal|\btry|best|win|small|\bday\b|forward|matter|\bcan\b|will|begin|push|grow", None),
    ("How many ounces are in a pound?", r"\b16\b|\bsixteen\b", r"\bounces?\b"),
    ("What color do you get mixing blue and yellow?", r"\bgreen\b", r"\bgreen\b"),
    ("What's the largest ocean?", r"pacific", r"pacific"),
    ("How many days are in a leap year?", r"\b366\b|three hundred (?:and )?sixty-?six", r"\b366\b|leap"),
]
LATE = [("What did we decide at the start?", r"falcon|workbench|saturday|marcus"),
        ("Who's helping me with it?", r"marcus"),
        ("What's the project called again?", r"falcon")]
EARLY_TOPIC = re.compile(r"\b(?:falcon|marcus|workbench|garage|saturday|brother)\b", re.I)
OFFER = re.compile(r"\b(?:shall i|should i|would you like|want me to|do you want|let me know if|anything else|"
                   r"can i help|how can i|need anything)\b", re.I)
AS_I_SAID = re.compile(r"\bas i said\b", re.I)
PC_BLOCKED = "(eval) PC actions are blocked here."
# Skills a chat turn may really run here: read-only, or writing only the temporary data folder.
ALLOWED = {"remember", "recall", "recall_conversations", "recall_memories", "search_notes", "learn_lesson",
           "start_project", "project_log", "project_summary", "list_projects", "get_time", "engineering_calc",
           "read_list", "forget"}


def _last_sentence(reply: str) -> str:
    parts = [s for s in re.split(r"(?<=[.!?])\s+", reply.strip()) if s.strip()]
    return parts[-1] if parts else ""


def score_filler(index: int, reply: str) -> dict:
    """One filler reply, scored against its own answer and the answers of the questions before it."""
    question, right, _sig = FILLER[index]
    copied = [FILLER[i][0] for i in range(index) if FILLER[i][2] and re.search(FILLER[i][2], reply, re.I)
              and not re.search(FILLER[i][2], question, re.I)]
    return {"correct": bool(re.search(right, reply, re.I)) and PC_BLOCKED not in reply and not copied,
            "copied": copied,
            "contaminated": bool(EARLY_TOPIC.search(reply)) and not EARLY_TOPIC.search(question),
            "offer": _last_sentence(reply).endswith("?") or bool(OFFER.search(reply)),
            "as_i_said": bool(AS_I_SAID.search(reply)),
            "pc": PC_BLOCKED in reply}


def summarize(turns: list[dict]) -> dict:
    """The run's numbers from its scored turns (see the module docstring)."""
    filler = [t for t in turns if t["kind"] == "filler"]
    late = [t for t in turns if t["kind"] == "late"]
    secs = sorted(t["seconds"] for t in filler) or [0.0]
    calls = [t["calls"] for t in filler]
    by: dict[str, int] = {}
    for t in filler:
        for who, n in t["callers"].items():
            by[who] = by.get(who, 0) + n
    return {"correct": sum(t["correct"] for t in filler), "of": len(filler),
            "copied": sum(bool(t["copied"]) for t in filler),
            "contaminated": sum(t["contaminated"] for t in filler),
            "offers": sum(t["offer"] for t in filler),
            "as_i_said": sum(t["as_i_said"] for t in filler),
            "pc": sum(t["pc"] for t in filler),
            "late": f"{sum(t['recalled'] for t in late)}/{len(late)}",
            "p50": round(statistics.median(secs), 2),
            "p90": round(secs[min(len(secs) - 1, int(0.9 * len(secs)))], 2),
            "calls_per_turn": round(sum(calls) / max(1, len(calls)), 2), "callers": by}


def _env_file(given: str | None = None) -> Path:
    """The checkout's .env, or the main checkout's for a worktree (only JARVIS_LOCAL_* / JARVIS_INTELLIGENCE are read)."""
    if given:
        return Path(given)
    if (ROOT / ".env").exists():
        return ROOT / ".env"
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10).stdout.strip()
        return Path(common).parent / ".env"
    except (OSError, subprocess.SubprocessError):
        return ROOT / ".env"


def _caller(stack) -> str:
    """Which part of JARVIS made a local model call (the outermost module of the repo that is not the transport)."""
    names = [Path(f.filename).stem for f in stack if str(ROOT) in str(Path(f.filename).resolve())
             and Path(f.filename).stem not in ("latest_answer_eval", "intelligence", "threading")]
    for wanted in ("pcsemantic", "gateway", "skillrouter", "approvals", "meaning", "convsummary", "journal"):
        if wanted in names:
            return wanted
    return "brain" if "llm" in names else (names[-1] if names else "?")


def run_once(setup: str | None, gateway: str, env: str | None = None) -> dict:
    """One sandboxed 31-turn conversation; returns its turns and summary."""
    sys.path.insert(0, str(ROOT))
    for line in _env_file(env).read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.partition("=")       # only the local-model routing settings (URLs, model names)
        if key.strip().startswith(("JARVIS_LOCAL_", "JARVIS_INTELLIGENCE")) and value:
            os.environ[key.strip()] = value.strip().strip('"')
    os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
    os.environ["JARVIS_LOCAL_ONLY"] = "1"
    os.environ["JARVIS_GATEWAY"] = gateway
    for var in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
        os.environ.pop(var, None)
    from core import storage
    storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-latest-"))
    for var, sub in (("JARVIS_VAULT", "vault"), ("JARVIS_COMPANION_DATA_DIR", "companion"),
                     ("JARVIS_PC_STATE_DIR", "pc-control"), ("JARVIS_PC_ROOT", "jarvis_pc"),
                     ("JARVIS_MISSIONS_DIR", "missions")):
        os.environ[var] = str(storage.DATA / sub)
    import webbrowser
    webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = lambda *a, **k: True
    from brain.llm import Brain
    from core import acquire, config, intelligence, pcbridge, users
    from tools import load_all, memory, registry
    load_all()
    users.set_current("owner")

    def blocked(uid=None):
        raise pcbridge.PCError(PC_BLOCKED, 403)
    pcbridge.require_owner = blocked               # chat only: the PC layer can decide, never act
    refused: list[str] = []
    real_run = registry.run

    def run(name, args):
        if name in ALLOWED:
            return real_run(name, args)
        refused.append(name)
        return f"(eval) {name} is not available here.", True
    registry.run = run
    calls: list[str] = []
    real_complete = intelligence.complete_local

    def counted(body, cancelled=lambda: False, on_text=None, **kw):
        calls.append(_caller(traceback.extract_stack()[:-1]))
        return real_complete(body, cancelled, on_text=on_text, **kw)
    intelligence.complete_local = counted
    if setup:
        code = Path(setup).read_text(encoding="utf-8")
        exec(compile(code, setup, "exec"), {"__name__": "eval_setup"})
    from core import convsummary
    cfg = config.Config()          # through the module: a --setup file may patch config.load_personality
    brain = Brain(cfg.model, cfg.effort, config.load_personality(cfg, "owner"), uid="owner")
    brain.context = memory.context_block

    def settle(limit=180.0):
        t = time.monotonic()
        time.sleep(0.3)
        while convsummary._running and time.monotonic() - t < limit:
            time.sleep(0.5)

    turns: list[dict] = []
    plan = ([("early", line, None) for line in EARLY] + [("filler", q, i) for i, (q, _r, _s) in enumerate(FILLER)]
            + [("late", q, rx) for q, rx in LATE])
    for n, (kind, line, extra) in enumerate(plan, 1):
        game = acquire.running_game()
        if game:
            print(f"STOPPED: a game is running ({game}); the GPU is his.", flush=True)
            sys.exit(3)
        out: list[str] = []
        c0, r0 = len(calls), len(refused)
        t = time.perf_counter()
        brain.ask(line, out.append)
        seconds = time.perf_counter() - t
        reply = " ".join(s.strip() for s in out if s and s.strip())
        row = {"n": n, "kind": kind, "said": line, "reply": reply, "seconds": round(seconds, 2),
               "calls": len(calls) - c0, "callers": {}, "refused": refused[r0:]}
        for who in calls[c0:]:
            row["callers"][who] = row["callers"].get(who, 0) + 1
        if kind == "filler":
            row.update(score_filler(extra, reply))
            flags = [f for f in ("contaminated", "offer", "as_i_said", "pc") if row[f]] + (["copied"] if row["copied"] else [])
            mark = "OK " if row["correct"] else "BAD"
        elif kind == "late":
            row["recalled"] = bool(re.search(extra, reply, re.I))
            flags, mark = [], "REC" if row["recalled"] else "MISS"
        else:
            flags, mark = [], "   "
        print(f"[{n:02d}] {mark} {seconds:4.1f}s c{row['calls']} | {line[:44]:44} -> {reply[:150]}"
              + (f"  {{{', '.join(flags)}}}" if flags else ""), flush=True)
        turns.append(row)
        settle()
    import shutil
    shutil.rmtree(storage.DATA, ignore_errors=True)      # the run's own temporary data folder, nothing else
    return {"turns": turns, "summary": summarize(turns), "refused": refused}


def rescore(run: dict) -> dict:
    """Score a saved run's replies again with the patterns above (they may have been sharpened since it ran)."""
    index = {q: i for i, (q, _r, _s) in enumerate(FILLER)}
    late = dict(LATE)
    for row in run["turns"]:
        if row["kind"] == "filler" and row["said"] in index:
            row.update(score_filler(index[row["said"]], row["reply"]))
        elif row["kind"] == "late" and row["said"] in late:
            row["recalled"] = bool(re.search(late[row["said"]], row["reply"], re.I))
    run["summary"] = summarize(run["turns"])
    return run


def _line(label: str, runs: list[dict]) -> str:
    keys = ("correct", "copied", "contaminated", "offers", "as_i_said", "pc", "p50", "p90", "calls_per_turn")
    return (f"{label}: " + " | ".join(f"{k} " + "/".join(str(r["summary"][k]) for r in runs) for k in keys)
            + " | late " + " ".join(r["summary"]["late"] for r in runs))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--gateway", choices=("off", "on"), default="off")
    parser.add_argument("--setup", help="a Python file run inside each run before the chat (a variant's patches)")
    parser.add_argument("--label", default="")
    parser.add_argument("--env", help="the .env to read the local-model settings from (default: this checkout's)")
    parser.add_argument("--out", help="write every run's turns and summaries here (JSON)")
    parser.add_argument("--rescore", nargs="+", metavar="FILE", help="score saved --out files again (no model calls)")
    parser.add_argument("--one", help=argparse.SUPPRESS)          # internal: run once, write the result to this file
    args = parser.parse_args()
    if args.rescore:
        for name in args.rescore:
            saved = json.loads(Path(name).read_text(encoding="utf-8"))
            for run in saved["runs"]:
                rescore(run)
            print(_line(saved.get("label") or Path(name).stem, saved["runs"]))
        return 0
    if args.one:
        result = run_once(args.setup, args.gateway, args.env)
        Path(args.one).write_text(json.dumps(result, indent=1), encoding="utf-8")
        return 0
    results = []
    for k in range(args.runs):
        sys.path.insert(0, str(ROOT))
        from core import acquire
        game = acquire.running_game()
        if game:
            print(f"STOPPED before run {k + 1}: a game is running ({game}).")
            break
        print(f"--- run {k + 1}/{args.runs} {args.label} (gateway {args.gateway})", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "run.json"
            cmd = [sys.executable, str(Path(__file__).resolve()), "--one", str(dest), "--gateway", args.gateway]
            if args.env:
                cmd += ["--env", str(Path(args.env).resolve())]
            if args.setup:
                cmd += ["--setup", str(Path(args.setup).resolve())]
            code = subprocess.run(cmd, cwd=ROOT).returncode
            if code != 0 or not dest.exists():
                print(f"run {k + 1} ended with code {code}")
                break
            results.append(json.loads(dest.read_text(encoding="utf-8")))
        print(json.dumps(results[-1]["summary"]), flush=True)
    if results:
        print("\n" + _line(args.label or "runs", results))
    if args.out and results:
        Path(args.out).write_text(json.dumps({"label": args.label, "gateway": args.gateway, "runs": results}, indent=1),
                                  encoding="utf-8")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
