"""Conversation board of the model bench: the REAL brain (tools, personality, guards) on one local model at a time, via scripts/convo_local.py.

  python scripts/voice_bench.py MODEL [MODEL ...]

Scored: scenario checks passed (tool routing, no leaks, exact answers), the first-turn time (cold load) and the median warm turn time.
This times the BRAIN only. Microphone endpointing, speech-to-text and text-to-speech are timed by scripts/speech_bench.py.
Results are appended to data/bench/voice.jsonl. Local only; stops if a game is running or data/bench/STOP exists."""
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "bench"
SCENARIOS = ["small_talk", "memory", "questions"]


def run(model):
    env = {**os.environ, "JARVIS_LOCAL_FAST_PROVIDER": "ollama", "JARVIS_LOCAL_FAST_MODEL": model, "JARVIS_LOCAL_ONLY": "1"}
    t0 = time.time()
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "convo_local.py"), *SCENARIOS], cwd=str(ROOT), env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=1500)
    out = p.stdout
    checks = re.findall(r"\[(PASS|FAIL)\] (.+)", out)
    turns = [float(x) for x in re.findall(r"JARVIS \(([\d.]+)s\)", out)]
    used = re.findall(r"models: (\[.*?\])", out)
    row = {"model": model, "passed": sum(1 for s, _ in checks if s == "PASS"), "total": len(checks),
           "failed": [n for s, n in checks if s == "FAIL"], "first_turn_s": turns[0] if turns else None,
           "median_warm_s": round(statistics.median(turns[1:]), 1) if len(turns) > 1 else None, "max_s": max(turns) if turns else None,
           "models_seen": sorted(set(re.findall(r"'([^']+)'", " ".join(used)))), "wall_s": round(time.time() - t0), "exit": p.returncode}
    with open(OUT / "voice.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


if __name__ == "__main__":
    from core import learn
    OUT.mkdir(exist_ok=True)
    for m in sys.argv[1:]:
        if (OUT / "STOP").exists() or learn.default_gaming():
            print("stopped: STOP file or a game is running")
            break
        r = run(m)
        print(f"{m:28s} {r['passed']}/{r['total']} checks, first turn {r['first_turn_s']}s, warm median {r['median_warm_s']}s, max {r['max_s']}s, models used {r['models_seen']}")
        for n in r["failed"]:
            print("    FAIL:", n)
        subprocess.run(["ollama", "stop", m], capture_output=True)
