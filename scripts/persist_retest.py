"""Re-run ONLY the persistence test of a retained skill version: a fresh lab process loads it from disk (hash-checked) and runs it
on worlds no stage has used. The result is appended to the cycle that retained it, marked as a re-run.

    python scripts/persist_retest.py skill.close_site_tab 2 <cycle folder>
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(skill, version, folder):
    from core import compose, skillcontracts, skillspec, skilllab
    worlds = [skillcontracts.generate(s, "close_site_tab") for s in compose.PERSIST_SEEDS]
    out = skilllab.run_job([{"world": w, "action": {"type": "skill", "skill": skill, "version": version, "store": str(skillspec._root())}}
                            for w in worlds], seconds=900, guard=False, label="persist-rerun")
    rows = compose._rows(worlds, out)
    ok = sum(1 for r in rows if r["passed"] and r["class"]["class"] != "verification")
    loaded = [((r.get("result") or {}).get("loaded")) for r in out["results"]]
    f = Path(folder) / "cycle.json"
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec["stages"].append({"stage": "persist", "what": f"RE-RUN after a host bug (an id-wide DISABLED flag hid v{version} from the first "
                          f"test): fresh process loaded {skill} v{version} from disk: {ok}/{len(rows)}", "at": None, "autonomous": True,
                          "assist": "", "data": {"loaded": loaded[:1], "rows": [{k: r[k] for k in ("seed", "site", "passed", "diff", "error", "self_check")}
                                                                                 for r in rows]}})
    if ok == len(rows):
        rec["status"] = "learned"
    rec["persist_rerun_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    f.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"ok": ok, "of": len(rows), "loaded": loaded[:1], "lab": out["status"],
                      "rows": [(r["seed"], r["site"], r["passed"], r["self_check"], r["diff"]) for r in rows]}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3])
