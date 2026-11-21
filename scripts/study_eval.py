"""Sandboxed real-model evaluation of the study tutor on the fixed IT-degree set (scripts/study_eval_set.py).

    .venv\\Scripts\\python.exe scripts\\study_eval.py --out DIR [--topics "DNS,NAT"] [--no-boards-only]

For every topic: the full ladder, requested the way the owner does it (explain it, then "simpler" five times, one rung
each), through tools.study.explain with the local model (Ollama) on a TEMPORARY data folder, so no live data is read or
written.  Every explanation and its board is saved to DIR/<topic>.json for scoring (scripts/study_score.py).
Nothing here reaches a cloud model or a real PC-control tool."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
os.environ["JARVIS_LOCAL_ONLY"] = "1"
os.environ.setdefault("JARVIS_LOCAL_FAST_PROVIDER", "ollama")      # the live brain: qwen3.5:9b through Ollama
for _v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
    os.environ.pop(_v, None)

from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-studyeval-"))
os.environ["JARVIS_VAULT"] = str(storage.DATA / "vault")
os.environ["JARVIS_PC_STATE_DIR"] = str(storage.DATA / "pc-control")

from core import study_ladder, study_store, users  # noqa: E402
from scripts import study_eval_set as S  # noqa: E402
from tools import study  # noqa: E402

CALLS = []


def timed_model(prompt):
    t = time.time()
    try:
        return study._local_model(prompt)
    finally:
        CALLS.append(round(time.time() - t, 1))


def run_topic(topic: str, course: str) -> dict:
    study._explained.clear()
    rows = []
    t0 = time.time()
    for step in range(len(study_ladder.RUNGS)):
        before = len(CALLS)
        t = time.time()
        try:
            out = study.explain(topic, "" if step == 0 else "simpler", course) if step == 0 else study.explain("", "simpler", course)
            err = ""
        except Exception as e:  # noqa: BLE001
            out, err = {}, f"{type(e).__name__}: {e}"
        rows.append({"step": step, "rung": out.get("level"), "source": out.get("source"), "text": out.get("text"),
                     "analogy": out.get("analogy"), "limit": out.get("limit"), "check": out.get("check"),
                     "board": out.get("board"), "refused": out.get("refused"), "model_calls": len(CALLS) - before, "seconds": round(time.time() - t, 1), "error": err})
    return {"topic": topic, "seconds": round(time.time() - t0, 1), "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--topics", default="")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    users.set_current("owner")
    study.MODEL = timed_model
    study.MODEL_SECONDS = 150          # the live deadline is 45 s; a slow shared GPU must not read as a quality failure
    notes = "\n\n".join(f"# {t}\n\n{S.NOTES[t]}" for t in S.TOPICS)
    study_store.add_material("IT Eval", "Eval notes", notes)
    wanted = [t.strip() for t in args.topics.split(",") if t.strip()] or S.TOPICS
    for topic in wanted:
        res = run_topic(topic, "IT Eval")
        (out / (study_store.slug(topic) + ".json")).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{topic}: {res['seconds']}s, sources={[r['source'] for r in res['rows']]}", flush=True)


if __name__ == "__main__":
    main()
