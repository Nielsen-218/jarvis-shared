"""Are the spoken answers short, and is the answer first? The owner's own question lines replayed on the REAL local model.

    .venv\\Scripts\\python.exe scripts\\short_answer_eval.py --runs 2 --label after
    .venv\\Scripts\\python.exe scripts\\short_answer_eval.py --root C:\\path\\to\\other\\checkout --label before

Owner 2026-11-21: "shorter, better answers" (of 63 cut-ins since Nov 15, 30 were him cutting a reply off on purpose). Each
case group is one conversation; most lines are his real questions from data/jarvis.log, a few (marked) are written to
check that what he ASKED to have in full (an explanation, steps, a recipe) is never cut. "Tell me more." after a trimmed
answer must give the rest. Per reply: spoken words and sentences, and what the spoken budget kept back. Whether the first
sentence answers the question is judged by a person from the transcript written to --out.

Sandboxed like scripts/voice_style_eval.py: a temporary data folder, no cloud (JARVIS_LOCAL_ONLY), the gateway off, quick
answers and the PC layer off, every tool call refused, the deterministic skill shortcuts off. --root runs another checkout's
code (the base branch) with this same harness, so before and after are measured the same way. One run at a time: the GPU
is shared with the live JARVIS.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

_args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
_args.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent, help="the checkout whose code runs")
_args.add_argument("--runs", type=int, default=1)
_args.add_argument("--label", default="")
_args.add_argument("--out", type=Path, default=None)
_args.add_argument("--groups", default="", help="comma-separated case group numbers (0-based) to run; default all")
ARGS = _args.parse_args()
ROOT = ARGS.root.resolve()
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Both checkouts get the SAME settings: only the local-model routing lines of the main checkout's .env (URLs, model
# names), and core.config's own load of the whole .env is switched off (relay URLs, roles and keys never come in).
_MAIN = Path(__file__).resolve().parent.parent
_MAIN = _MAIN.parent.parent if _MAIN.parent.name == ".worktrees" else _MAIN
for _line in (_MAIN / ".env").read_text(encoding="utf-8-sig", errors="replace").splitlines():
    _key, _, _value = _line.partition("=")
    if _key.strip().startswith(("JARVIS_LOCAL_", "JARVIS_INTELLIGENCE", "JARVIS_USER_TITLE", "OLLAMA_")) and _value.strip():
        os.environ[_key.strip()] = _value.strip().strip('"')
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **k: False
os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_LOCAL_ONLY"] = "1"                      # a paid call here would be a bug, not a fallback
os.environ["JARVIS_GATEWAY"] = "off"                       # no PC understanding, no PC execution
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
    os.environ.pop(_v, None)

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-short-eval-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
os.environ["JARVIS_COMPANION_DATA_DIR"] = str(storage.DATA / "companion")
os.environ["JARVIS_PC_STATE_DIR"] = str(storage.DATA / "pc-control")
os.environ["JARVIS_PC_ROOT"] = str(storage.DATA / "jarvis_pc")
os.environ["JARVIS_MISSIONS_DIR"] = str(storage.DATA / "missions")

import webbrowser  # noqa: E402

from brain.llm import Brain  # noqa: E402
from core import intelligence, pcfast, quick, users  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import load_all, memory, registry  # noqa: E402

load_all()
users.set_current("owner")
webbrowser.open = lambda url, *a, **k: True
quick.handle = lambda text: None                           # quick answers include volume and timers: they ACT
pcfast.handle = lambda text, history=None, **kw: None      # never the PC
registry.run = lambda name, args: ("Not run: tools are off during this evaluation.", True)
for _shortcut in ("_learned_skill_request", "_craft_request", "_web_request", "_local_note_request"):
    setattr(Brain, _shortcut, lambda self, text, emit: False)   # the deterministic shortcuts run skills: off

# (kind, his line). kind: "q" his real question; "detail" a line asking for the long form (never to be trimmed; * = written
# for this check, not his); "more" his ask for the rest.
CASES = [
    [("q", "Are you able to learn now?"), ("q", "Can you learn all by yourself?"), ("q", "Can you control my PC?"),
     ("q", "Jarvis, are you using a paid model to do this or is that all local?")],
    [("q", "I think Blade Runner is better than Alien. What do you think?"), ("more", "Tell me more."),
     ("q", "So what do you think? You think that was a good update?")],
    [("q", "I was thinking of wiping everything. What do you think?"), ("q", "Jarvis, I cracked him. Should I push?")],
    [("q", "My computer keeps opening my files folder by itself. Can you tell me why it keeps doing that?"),
     ("more", "Go on."), ("q", "how come I can't access you when I'm away from home?")],
    [("q", "So, what do you think will make you even smarter when it comes to conversations?"),
     ("q", "What do you think about the world?"), ("q", "What's the basic of C++?")],
    [("q", "What's a budget-friendly Haitian food that you would suggest we eat for lunch today?"),
     ("detail", "Give me the recipe for diri ak pwa, step by step.*")],
    [("q", "Why are you here?"), ("q", "Nah, that's all good. So who are you?"),
     ("q", "Oh, okay. So you got a real short-term memory, huh?"),
     ("q", "So you know it's not morning, then why did you say good morning?")],
    [("q", "Should I show them so that they can invest in improving you?"), ("q", "Why is local intelligence unavailable?"),
     ("q", "So what tool do you use to improve your conversation flow?")],
    [("q", "How are you doing?"), ("q", "What are you up to?"),
     ("q", "Okay, so you are just flowing conversation right now just by yourself. So you got a little bit smarter, you think?")],
    [("q", "You are frustrating, you know that? I've been teaching you for ages and you still can't perform basic tasks."),
     ("q", "Jarvis, why can't you do what I told you to do? Why can't you do what I told you to do?")],
    [("q", "What's 50% of 100?"), ("q", "Can you build me a website for my business?"), ("q", "What are your skills?")],
    # "tell me more" right after an answer the budget is likely to shorten
    [("q", "What's the basic of C++?"), ("more", "Tell me more.")],
    [("q", "So what tool do you use to improve your conversation flow?"), ("more", "Go on.")],
    [("detail", "Explain how computer processor works in five sentences."),
     ("detail", "Explain it to me like I was five: what is RAM?*"),
     ("detail", "Walk me through how to back up my files before a Windows reinstall.*")],
]
_SENT = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> int:
    return len([s for s in _SENT.split(text.strip()) if s.strip()]) if text.strip() else 0


def one_run(cfg) -> list[dict]:
    rows = []
    chosen = {int(g) for g in ARGS.groups.split(",") if g.strip()} if ARGS.groups else None
    for number, group in enumerate(CASES):
        if chosen is not None and number not in chosen:
            continue
        b = Brain(cfg.model, cfg.effort, load_personality(cfg, "owner"), uid="owner")
        b.context = memory.context_block
        for kind, line in group:
            text = line.rstrip("*")
            said: list[str] = []
            started = time.time()
            b.ask(text, said.append)
            reply = " ".join(s.strip() for s in said if isinstance(s, str) and s.strip())
            more = getattr(b, "_more", None)
            kept = more[0] if more and getattr(b, "_unsaid", None) is more else ""
            rows.append({"kind": kind, "you": line, "jarvis": reply, "words": len(reply.split()),
                         "sentences": sentences(reply), "kept_back": kept, "seconds": round(time.time() - started, 1)})
            print(f"\n[{kind}] YOU: {line}\n  JARVIS ({rows[-1]['words']}w): {reply[:500]}" + (f"\n  (kept back: {kept[:300]})" if kept else ""))
    return rows


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else 0


def main() -> int:
    cfg = Config()
    runs = [one_run(cfg) for _ in range(max(1, ARGS.runs))]
    allrows = [r for run in runs for r in run]
    summary = {}
    for kind in ("q", "detail", "more"):
        rows = [r for r in allrows if r["kind"] == kind]
        if rows:
            w = [r["words"] for r in rows]
            summary[kind] = {"replies": len(rows), "median_words": statistics.median(w), "p90_words": pct(w, 0.9),
                             "median_sentences": statistics.median(r["sentences"] for r in rows),
                             "p90_sentences": pct([r["sentences"] for r in rows], 0.9),
                             "trimmed": sum(1 for r in rows if r["kept_back"])}
    summary["root"], summary["model"] = str(ROOT), intelligence.model("fast")
    print("\n" + json.dumps(summary, indent=2))
    out = ARGS.out or Path(tempfile.gettempdir()) / f"short-answers-{time.strftime('%Y%m%d-%H%M')}{'-' + ARGS.label if ARGS.label else ''}.json"
    out.write_text(json.dumps({"summary": summary, "runs": runs}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
