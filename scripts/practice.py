"""Run a JARVIS practice session from the command line (same engine the voice command starts).

  python scripts/practice.py [--minutes 40] [--experiments 8] [--cases 1] [--only red_sweater] [--no-research]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import dojo  # noqa: E402
from core.dojo_store import Store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=40)
    ap.add_argument("--experiments", type=int, default=8)
    ap.add_argument("--cases", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--no-research", action="store_true")
    ap.add_argument("--force-patch", action="store_true", help="let the model propose code patches from the first round")
    ap.add_argument("--no-vision", action="store_true")
    a = ap.parse_args()
    budget = {"max_minutes": a.minutes, "max_experiments": a.experiments, "max_cases": a.cases, "rounds": a.rounds,
              "research": not a.no_research, "force_patch": a.force_patch, "vision": not a.no_vision, "only": [x for x in a.only.split(",") if x]}
    p = dojo.Practice(Store(), budget).start()
    while p.thread.is_alive():
        p.thread.join(30)
        st = p.store.state()
        print(f"[{round((__import__('time').time() - p.t0) / 60, 1)} min] {st.get('step')}  (experiments {p.n_exp})", flush=True)
    print(json.dumps(p.result, indent=1, default=str))
    print(dojo.spoken_learned(p.store))


if __name__ == "__main__":
    main()
