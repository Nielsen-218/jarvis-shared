"""The explanation ladder through the SAME entry the live app uses for a text turn (Brain.ask), on the real local model, in a
sandbox: a temporary data folder, synthetic course material written for this probe, and no PC tool of any kind (only the
study skills may run; everything else is refused and counted).

    .venv\\Scripts\\python.exe -m scripts.ladder_probe --phone --topics ram,subnetting --out runs\\ladder.json
    .venv\\Scripts\\python.exe -m scripts.ladder_probe --lines "Explain RAM from my IT Fundamentals course." "Simpler." "Simpler."

For each turn it prints which component answered (quick / pcfast / the study shortcut / the model's chat path / a tool), the
rung the tutor says it is on, the word count, whether the text changed, and whether a whiteboard was attached.
The default sequence is the evaluation one: explain, simpler x5, more detail, "like I'm one", "college level".
Nothing here reads the owner's data: the material below is invented text."""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_LOCAL_ONLY"] = "1"
# The one local brain, the way the deployed app runs it (these are the app's own settings, set here so the probe needs no .env).
for _k, _v in (("JARVIS_LOCAL_FAST_PROVIDER", "ollama"), ("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1"),
               ("JARVIS_LOCAL_FAST_MODEL", "qwen3.5:9b"), ("JARVIS_LOCAL_ADVANCED_MODEL", "qwen3.5:9b")):
    os.environ.setdefault(_k, _v)
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
    os.environ.pop(_v, None)

if "--gateway" in sys.argv:                                  # the deployed app runs with the gateway on (core/gateway)
    os.environ["JARVIS_GATEWAY"] = "on"

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-ladder-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
os.environ["JARVIS_COMPANION_DATA_DIR"] = str(storage.DATA / "companion")
os.environ["JARVIS_PC_STATE_DIR"] = str(storage.DATA / "pc-control")
os.environ["JARVIS_PC_ROOT"] = str(storage.DATA / "jarvis_pc")

import webbrowser  # noqa: E402

webbrowser.open = lambda url, *a, **k: True

from brain.llm import Brain  # noqa: E402
from core import intelligence, study_store, users  # noqa: E402
from core.config import Config, load_personality  # noqa: E402
from tools import load_all, memory, registry, study  # noqa: E402

load_all()
users.set_current("owner")
study.OPEN_BOARD = lambda: False                                 # no browser, ever

PHONE = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
COURSE = "Systems Notes"          # a course of his own; "IT Fundamentals" is the built-in one (RAM, CPU, firewall...)
# Invented course notes. Every sentence below was written for this probe.
MATERIAL = """Chapter 1: Memory

A cache is a small, very fast memory close to the processor that keeps copies of recently used data. When the data is found in the cache the processor avoids the slower trip to main memory.

Chapter 2: Input and output

An interrupt is a signal that makes the processor pause what it is doing and run a short routine for a device that needs attention, such as a keyboard press or a finished disk read. After the routine ends, the processor goes back to the work it paused.
"""
WITH = {"ram": "Explain RAM from my IT Fundamentals course.", "cpu": "Explain the CPU from my IT Fundamentals course.",
        "firewall": "Explain a firewall from my IT Fundamentals course.",
        "cache": "Explain cache from my Systems Notes course.", "interrupts": "Explain interrupts from my Systems Notes course."}
WITHOUT = {"subnetting": "Explain subnetting from my IT Fundamentals course.", "photosynthesis": "Explain photosynthesis.",
           "inflation": "Teach me about inflation.", "recursion": "Help me understand recursion.",
           "volcanoes": "Explain how volcanoes work."}
SEQUENCE = ["Simpler.", "Simpler.", "Simpler.", "Simpler.", "Simpler.", "More detail.", "Explain it like I am one year old.",
            "Explain it at college level."]

calls: list[tuple[str, dict]] = []
refused: list[str] = []
_run = registry.run


def guarded_run(name, args):
    """Only the study skills may run in this probe; anything else is refused, never executed."""
    if not name.startswith("study_"):
        refused.append(name)
        return f"(refused in the ladder probe: {name})", True
    calls.append((name, args))
    return _run(name, args)


registry.run = guarded_run
legacy_seen: list[str] = []
_shadow = Brain._shadow


def shadow(self, text, legacy):
    legacy_seen.append(legacy)
    return _shadow(self, text, legacy)


Brain._shadow = shadow
cfg = Config()


def new_brain() -> Brain:
    b = Brain(cfg.model, cfg.effort, load_personality(cfg, "owner"), uid="owner")
    b.context = memory.context_block
    return b


def reset_material() -> None:
    study_store.add_material(COURSE, "Course notes", MATERIAL)


def state() -> dict:
    last = study._explained.get(study._k()) or {}
    board = study.last_board() or {}
    return {"rung": last.get("level", ""), "topic": last.get("topic", ""), "board_at": board.get("at", 0), "text": last.get("text", "")}


def turn(brain: Brain, text: str, phone: bool, previous: str = "") -> dict:
    calls.clear()
    refused.clear()
    legacy_seen.clear()
    before = state()
    said: list[str] = []
    t = time.time()
    brain.ask((PHONE if phone else "") + text, said.append)
    took = time.time() - t
    reply = " ".join(s.strip() for s in said if s.strip())
    after = state()
    route = legacy_seen[-1] if legacy_seen else "?"
    ladder_turn = after["board_at"] > before["board_at"]
    return {"you": text, "route": route, "chat_turn": bool(getattr(brain, "_chat_turn", False)), "tools": [n for n, _ in calls],
            "refused": list(refused), "seconds": round(took, 1), "rung": after["rung"] if ladder_turn else "",
            "words": len(reply.split()), "changed": bool(previous) and reply.strip() != previous.strip(),
            "board": ladder_turn, "reply": reply}


def run_topic(name: str, opening: str, phone: bool, lines=None) -> list[dict]:
    brain = new_brain()
    rows, previous = [], ""
    for line in [opening, *(lines or SEQUENCE)]:
        row = turn(brain, line, phone, previous)
        previous = row["reply"]
        rows.append(row)
        print(f"  {row['route']:<14} rung={row['rung'] or '-':<10} words={row['words']:<4} changed={row['changed']!s:<5} "
              f"board={row['board']!s:<5} {row['seconds']:>5}s  YOU: {line}\n      JARVIS: {row['reply'][:300]}", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topics", default="", help="comma list from: " + ", ".join([*WITH, *WITHOUT]))
    ap.add_argument("--phone", action="store_true", help="prefix each line with the phone's channel note")
    ap.add_argument("--lines", nargs="*", help="one conversation of your own lines instead of the evaluation sequence")
    ap.add_argument("--no-material", action="store_true", help="with --lines: do not add the course")
    ap.add_argument("--out", default="")
    ap.add_argument("--gateway", action="store_true", help="JARVIS_GATEWAY=on, as the deployed app runs")
    args = ap.parse_args()
    if not intelligence.enabled():
        print("local intelligence is not enabled")
        return 2
    report: dict = {}
    if args.lines:
        if not args.no_material:
            study_store.add_material(COURSE, "Course notes", MATERIAL)
        brain = new_brain()
        rows, previous = [], ""
        for line in args.lines:
            row = turn(brain, line, args.phone, previous)
            previous = row["reply"]
            rows.append(row)
            print(f"{row['route']:<14} rung={row['rung'] or '-':<10} words={row['words']:<4} changed={row['changed']!s:<5} "
                  f"board={row['board']!s:<5} {row['seconds']:>5}s tools={row['tools']}\n  YOU: {line}\n  JARVIS: {row['reply']}\n", flush=True)
        report["lines"] = rows
    else:
        study_store.add_material(COURSE, "Course notes", MATERIAL)
        wanted = [t for t in args.topics.split(",") if t] or [*WITH, *WITHOUT]
        for name in wanted:
            opening = WITH.get(name) or WITHOUT.get(name)
            if not opening:
                continue
            print(f"\n== {name} ({'material' if name in WITH else 'no material'})", flush=True)
            report[name] = run_topic(name, opening, args.phone)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
