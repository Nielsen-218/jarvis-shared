"""Summarise stored episodes of a skill: per-action success and cost, and which tasks nothing solved (training rows by default)."""
import collections
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import learn_skills  # noqa: E402


def main():
    skill = sys.argv[1]
    purpose = sys.argv[2] if len(sys.argv) > 2 else "train"
    e = learn_skills.engine(skill)
    rows = [r for r in e.store.episodes() if r["judge"] == e.judge_hash() and r["purpose"] == purpose]
    by = collections.defaultdict(list)
    for r in rows:
        by[r["action"]].append(r)
    solved = collections.defaultdict(set)
    for r in rows:
        solved[r["task"]]
        if r["outcome"]["full"]:
            solved[r["task"]].add(r["action"])
    print(json.dumps({"episodes": len(rows), "tasks": len(solved),
                      "per_action": {a: {"solved": sum(x["outcome"]["full"] for x in v), "n": len(v), "mean_cost_s": round(statistics.mean(x["outcome"]["cost_s"] for x in v), 2)} for a, v in by.items()},
                      "unsolved_by_every_action": sum(1 for v in solved.values() if not v),
                      "solved_only_by_search": sum(1 for v in solved.values() if v == {"search"}),
                      "solved_only_by_a_model": sum(1 for v in solved.values() if v and "search" not in v)}, indent=1))


if __name__ == "__main__":
    main()
