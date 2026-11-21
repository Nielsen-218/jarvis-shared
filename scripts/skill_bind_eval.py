"""Evaluate the stored two-bone binding procedure against JARVIS regenerating code. Local only: no paid API, no model download.

  python scripts/skill_bind_eval.py all        register (if the judge confirms it on all 24 training tasks), compare arms, negatives, recovery, restart probe
  python scripts/skill_bind_eval.py probe      (used by `all`) a FRESH process: load the library from disk, recognise, run, print JSON

The sealed holdout (bb:holdout:*) is never run here. 'dev' tasks are fresh variants generated with a fixed seed, kept out of the training
and holdout sets by digest. Arms: llm_plain (JARVIS's own regeneration with llama3.2:3b) versus auto (recognise + stored procedure)."""
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JARVIS_LEARN_GPU", "1")

from core import learn, procedures  # noqa: E402
from core import learn_blender_basics as bb  # noqa: E402
from core import learn_blender_skill as sk  # noqa: E402
from core.learn import Engine, Store  # noqa: E402

REPORT = ROOT / "data" / "bench" / "skill_eval.json"


def dev_tasks(n=24, seed=20260101, tag="dev"):
    rng = random.Random(seed)
    taken = {t["digest"] for s in ("train", "holdout") for t in bb.tasks(s)}
    out = []
    while len(out) < n:
        ax = ("x", "y", "z")[len(out) % 3]
        start = round(rng.uniform(-1.0, 1.0), 2)
        v = {"axis": ax, "start": start, "end": round(start + rng.uniform(0.7, 2.4), 2), "radius": round(rng.uniform(0.05, 0.2), 3)}
        d = learn.digest(v)
        if d in taken:
            continue
        taken.add(d)
        out.append({"id": f"bb:{tag}:{len(out)}", "label": tag, "variant": v, "digest": d, "split": tag})
    return out


def engine():
    return Engine(sk.BlenderSkillAdapter(), Store("blender_basics"))


def ensure_registered(eng, log):
    lib = sk.library(eng.store)
    have = lib.get(sk.PID) if sk.PID in lib.ids() else None
    if have and have[0]["source_sha"] == procedures.sha(sk.SOURCE.read_text(encoding="utf-8")):
        log["registered"] = f"already stored, v{have[0]['version']} (loaded from disk)"
        return lib
    ev = []
    script_for = lambda v: (sk.SOURCE.read_text(encoding="utf-8") + f"\nrun(axis={v['axis']!r}, start={v['start']}, end={v['end']})\n")
    for t in bb.tasks("train"):
        ev.append({"task": t["id"], "full": bb.judge(t["variant"], script_for(t["variant"]))["full"]})
    evidence = {"judge": eng.judge_hash(), "passed": sum(e["full"] for e in ev), "total": len(ev), "tasks": [e["task"] for e in ev if e["full"]],
                "split": "train", "note": "independent judge, unmodified; holdout not touched"}
    log["registration_evidence"] = {k: evidence[k] for k in ("passed", "total", "judge")}
    sk.register_from_evidence(eng.store, evidence)
    log["registered"] = "new version" if have else "new"
    return lib


def arm(eng, tasks, action, via_engine):
    ok, rows = 0, []
    for t in tasks:
        if via_engine:
            r = eng.run(t["id"], action, purpose="train")
            full, err = r["outcome"]["full"], r["outcome"].get("error")
        else:
            out = eng.adapter.execute(t, action, {"purpose": "train", "store": eng.store})
            full, err = out["full"], out.get("error")
        ok += bool(full)
        rows.append({"task": t["id"], "full": bool(full), "error": (err or "")[:80]})
    declined = sum(1 for r in rows if not r["full"] and "too_thick" in r["error"])
    return {"passed": ok, "total": len(tasks), "declined_out_of_scope": declined, "rows": rows}


def recovery_tests(eng, lib):
    man, _ = lib.get(sk.PID)
    t = bb.tasks("train")[8]                                   # an X tube
    base = {"mesh_name": "Limb", "rig_name": "Rig", "bone_names": ["upper", "lower"], "axis": t["variant"]["axis"], "start": t["variant"]["start"],
            "end": t["variant"]["end"], "bands": [0.15, 0.10, 0.05], "if_exists": "error"}
    out = {}
    stale = ("import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new('Rig', None))\n")

    def run(params, prefix=""):
        return bb.judge(t["variant"], prefix + lib.render(sk.PID, params))

    r1 = run(base, stale)                                       # a stale object called Rig is in the scene
    rec = lib.recovery_for(man, r1.get("error"))
    r2 = run({**base, **rec["set"]}, stale) if rec and rec.get("set") else None
    out["stale_rig"] = {"first_error": r1.get("error"), "detected": rec and rec["class"], "recovered_to_full_pass": bool(r2 and r2["full"])}
    r3 = run({**base, "bands": [0.45]})                         # a deliberately bad blend width: the skill's own measurement must fail
    rec = lib.recovery_for(man, r3.get("error"))
    r4 = run({**base, **rec["set"]}) if rec and rec.get("set") else None
    out["bad_blend"] = {"first_full": r3["full"], "first_error": (r3.get("error") or "")[:90], "detected": rec and rec["class"],
                        "recovered_to_full_pass": bool(r4 and r4["full"])}
    r5 = run({**base, "mesh_name": "Nope"})
    rec = lib.recovery_for(man, r5.get("error"))
    out["missing_mesh"] = {"first_error": (r5.get("error") or "")[:90], "detected": rec and rec["class"], "asks_user_instead_of_guessing": bool(rec and rec.get("ask_user"))}
    return out


def negatives(lib):
    texts = {"three bones": bb.TASK_TEXT.replace("exactly two bones", "exactly three bones"),
             "character": "Rig this dragon character with a spine, a tail and four legs and skin it.",
             "python bug": "Fix the off-by-one error in this Python function so the tests pass.",
             "no armature": "Make the tube bend when I move it."}
    return {k: bool(lib.match(v, context={"axis": "z", "start": 0.0, "end": 1.0})) for k, v in texts.items()}


def probe():
    """Fresh process: nothing in memory; everything comes from the store on disk."""
    eng = engine()
    lib = sk.library(eng.store)
    t = dev_tasks()[5]
    m = lib.match(bb.TASK_TEXT, context=t["variant"])
    verdict, trail = eng.adapter.run_procedure(t, eng.store)
    print(json.dumps({"stored_ids": lib.ids(), "matched": m and m["id"], "version": m and m["manifest"]["version"], "source_verified_on_load": True,
                      "full_pass": bool(verdict and verdict["full"]), "trail": trail, "task": t["id"]}))


def main():
    log = {"started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    eng = engine()
    if learn.default_gaming():
        sys.exit("a game is running")
    lib = ensure_registered(eng, log)
    train, dev = bb.tasks("train"), dev_tasks()
    log["without_skill_train"] = arm(eng, train, "llm_plain", True)
    log["with_skill_train"] = arm(eng, train, "auto", True)
    log["without_skill_dev"] = arm(eng, dev, "llm_plain", False)
    log["with_skill_dev"] = arm(eng, dev, "auto", False)
    dev2 = dev_tasks(24, 777, "dev2")
    log["without_skill_dev2"] = arm(eng, dev2, "llm_plain", False)
    log["with_skill_dev2"] = arm(eng, dev2, "auto", False)
    log["negative_matches_should_all_be_false"] = negatives(lib)
    log["recovery"] = recovery_tests(eng, lib)
    p = subprocess.run([sys.executable, str(Path(__file__)), "probe"], capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    log["restart_probe"] = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {"error": p.stderr[-300:]}
    log["store_uses"] = len(lib.uses())
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(log, indent=1), encoding="utf-8")
    for k in ("without_skill_train", "with_skill_train", "without_skill_dev", "with_skill_dev", "without_skill_dev2", "with_skill_dev2"):
        print(f"{k:22s} {log[k]['passed']}/{log[k]['total']}  declined out of scope: {log[k]['declined_out_of_scope']}")
    print("registered:", log["registered"], log.get("registration_evidence"))
    print("negatives:", log["negative_matches_should_all_be_false"])
    print("recovery:", json.dumps(log["recovery"]))
    print("restart:", json.dumps(log["restart_probe"]))


if __name__ == "__main__":
    probe() if len(sys.argv) > 1 and sys.argv[1] == "probe" else main()
