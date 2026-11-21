"""Fair 'without the skill' baseline: the bench's plain code-block prompt, one sample per task (bench seed and sampling), judged by the unmodified judge,
on the same 72 tasks as the skill evaluation (24 train + 24 dev + 24 dev2). Local models only.
  python scripts/skill_baseline.py MODEL [MODEL ...]  ->  data/bench/skill_baseline.json"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import model_bench as mb  # noqa: E402
from core import learn, learn_llm  # noqa: E402
from core import learn_blender_basics as bb  # noqa: E402
from skill_bind_eval import dev_tasks  # noqa: E402

OUT = ROOT / "data" / "bench" / "skill_baseline.json"


def main(models):
    tasks = bb.tasks("train") + dev_tasks(24, 20260101, "dev") + dev_tasks(24, 777, "dev2")
    prompt = mb.prompt_for_blender()
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    for m in models:
        rows = []
        for t in tasks:
            if learn.default_gaming() or (mb.OUT / "STOP").exists():
                print("stopped"); break
            text, *_ = mb.generate(m, prompt, mb.SPEC["sampling"]["seed_base"])
            v = bb.judge(t["variant"], learn_llm.code_block(text))
            rows.append({"task": t["id"], "full": v["full"], "fail_count": v["fail_count"], "error": (v.get("error") or "")[:80]})
        mb.unload(m)
        by = {}
        for r in rows:
            by.setdefault(r["task"].split(":")[1], []).append(r["full"])
        res[m] = {"passed": sum(r["full"] for r in rows), "total": len(rows), "by_set": {k: [sum(v), len(v)] for k, v in by.items()}, "rows": rows}
        print(m, res[m]["passed"], "/", res[m]["total"], res[m]["by_set"], flush=True)
        OUT.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
