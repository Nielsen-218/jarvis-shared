"""Owner CLI for JARVIS's self-taught PC methods.

  python scripts/acquire.py discover                 ranked weaknesses from the owner's real misses (writes the queue)
  python scripts/acquire.py learn [ID] [--attended]  one bounded learning cycle for the top ready weakness (or ID)
                                                     --attended: don't suspend on keyboard/mouse (the owner is watching)
  python scripts/acquire.py list                     learned methods and whether they are approved
  python scripts/acquire.py show ID                  a learned method's code and evidence, for review
  python scripts/acquire.py approve ID               activate it on the real desktop (bound to its exact code hash)
  python scripts/acquire.py revoke ID                switch it off again
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv):
    from core import acquire, procedures
    cmd = argv[0] if argv else "discover"
    if cmd == "discover":
        rows = acquire.discover()
        acquire.save_queue(rows)
        for r in rows:
            print(f"{r['status']:12} p={r['priority']:<3} x{r['count']} {r['tool'] or '-':18} {r['id']}  {r['examples'][0]['said'][:60]!r}  ({r['note']})")
    elif cmd == "learn":
        if "--attended" in argv:
            os.environ["JARVIS_LAB_GUARD"] = "off"
        ids = [a for a in argv[1:] if not a.startswith("--")]
        rows = [r for r in acquire.discover() if r["status"] == "ready" and (not ids or r["id"] == ids[0])]
        if not rows:
            print("nothing ready to learn"); return 1
        rec = acquire.learn(rows[0])
        for s in rec["stages"]:
            print(f"[{s['at']:>7}s] {s['stage']:11} {s['what']}")
        print(rec["status"], rec.get("method", ""), rec["folder"])
    elif cmd == "teach":
        # Owner 2026-11-08: "use the promotional credits to teach Jarvis". One lesson now, from the cloud teacher unless --local;
        # the credit, monthly budget and per-skill cap are checked exactly as for an automatic escalation.
        from core import teacher
        rows = [r for r in acquire.discover() if r["status"] == "ready"]
        if not rows:
            print("nothing ready to learn"); return 1
        rec = teacher.consult(rows[0], tier="local" if "--local" in argv else "cloud", requested_by="owner command")
        print(json.dumps({k: rec.get(k) for k in ("status", "tier", "model", "why", "cost_usd", "lesson", "removed", "file")},
                         indent=1, ensure_ascii=False))
        return 0 if rec["status"] == "given" else 1
    elif cmd == "list":
        for m in acquire.learned_methods():
            how = {"independent": "learned locally, no help", "teacher_assisted": "learned WITH a teacher's hints"}.get(
                m.get("provenance", "independent"), m.get("provenance"))
            print(f"{m['id']} v{m['version']} for {m['for_tool']}  approved={acquire.approved(m)}  evidence {m['evidence']['passed']}/{m['evidence']['total']}  by {m['authored_by']}  ({how})")
        print("every other PC step: native code, developed by people (not learned)")
    elif cmd == "show":
        man, src = procedures.Library(acquire._root()).get(argv[1])
        print(src); print(json.dumps({k: v for k, v in man.items() if k != "evidence"}, indent=1))
    elif cmd == "retest":
        if "--attended" in argv:
            os.environ["JARVIS_LAB_GUARD"] = "off"
        said = next((a for a in argv[1:] if not a.startswith("--")), "Close GitHub.")
        out = acquire.retest(said, env_file=os.getenv("JARVIS_ENV_FILE", str(Path(__file__).resolve().parent.parent / ".env")))
        print(json.dumps(out, indent=1, ensure_ascii=False))
    elif cmd == "approve":
        # --by NAME records who approved (the owner by default); a staging copy approved only for a lab retest says so
        by = argv[argv.index("--by") + 1] if "--by" in argv else "owner"
        print(acquire.approve(argv[1], by=by))
    elif cmd == "revoke":
        acquire.revoke(argv[1]); print("revoked")
    else:
        print(__doc__); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
