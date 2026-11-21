"""Command line for the universal learning core.

  python scripts/learn.py status [SKILL]            progress level + a..e capability report (computed from stored episodes)
  python scripts/learn.py seal SKILL                seal the held-out tasks (before any training)
  python scripts/learn.py episode SKILL TASK ACTION [--purpose train|exam]
  python scripts/learn.py matrix SKILL SPLIT [--actions a,b] [--limit N]   run every action on every task (cached by the stored episodes)
  python scripts/learn.py academy SKILL TOPIC       placement -> docs study -> drills -> sealed A/B exam (remember-before-research)
  python scripts/learn.py train SKILL               train the LinUCB policy from stored TRAINING episodes and save a new version
  python scripts/learn.py eval SKILL                evaluate the saved policy on the sealed holdout (exam episodes of every action must exist)
  python scripts/learn.py replay SKILL              load the saved policy in this fresh process and print its holdout choices (restart proof)
  python scripts/learn.py rsi SKILL                 one self-improvement cycle (propose -> isolate -> verify -> pending_review)
Attended, bounded, CPU-only for the local model; it stops for a game or the STOP file (data/dojo/<skill>/STOP)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import learn_skills  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "seal", "episode", "matrix", "academy", "train", "eval", "replay", "rsi"])
    ap.add_argument("skill", nargs="?")
    ap.add_argument("a", nargs="?")
    ap.add_argument("b", nargs="?")
    ap.add_argument("--purpose", default="train")
    ap.add_argument("--actions")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    skills = [args.skill] if args.skill else list(learn_skills.ADAPTERS)
    if args.cmd == "status":
        for s in skills:
            e = learn_skills.engine(s)
            out = {"progress_with_default_action": e.progress(), "capabilities": e.capability_report()}
            from core import learn_policy as LP
            pol = LP.load(e)
            if pol:
                via = {t["id"]: pol.choose(e.adapter.features(t), explore=False) for t in e.adapter.tasks("holdout")}
                out["progress_with_learned_policy"] = {**e.progress(via=via), "policy": pol.version}
            print(json.dumps(out, indent=1))
    elif args.cmd == "seal":
        print("trained-on offenders:", learn_skills.engine(args.skill).seal_holdout())
    elif args.cmd == "episode":
        print(json.dumps(learn_skills.engine(args.skill).run(args.a, args.b, purpose=args.purpose), indent=1, default=str))
    elif args.cmd == "academy":
        from core import learn_academy
        print(json.dumps(learn_academy.Academy(learn_skills.engine(args.skill)).run_topic(args.a), indent=1, default=str))
    elif args.cmd in ("train", "eval", "replay"):
        from core import learn_policy as LP
        e = learn_skills.engine(args.skill)
        if args.cmd == "train":
            pol = LP.train(e, epochs=8, seed=0, parent=(e.store.load_policy() or {}).get("version"))
            print("trained", pol.to_dict()["version"], "updates", pol.updates, "episodes", len(pol.train_episodes), "counts", pol.n)
            LP.save(e, pol)
        elif args.cmd == "eval":
            pol = LP.load(e)
            res = LP.evaluate(e, pol, e.adapter.default_action())
            LP.save(e, pol)
            print(json.dumps(res, indent=1))
        else:
            pol = LP.load(e)
            print(json.dumps({"version": pol.version, "evaluation": (pol.evaluation or {}).get("improves"),
                              "choices": {t["id"]: pol.choose(e.adapter.features(t), explore=False) for t in e.adapter.tasks("holdout")}}, sort_keys=True))
    elif args.cmd == "rsi":
        from core import learn_rsi
        print(json.dumps(learn_rsi.Cycle(learn_skills.engine(args.skill)).run(), indent=1, default=str))
    else:
        e = learn_skills.engine(args.skill)
        split = args.a
        acts = args.actions.split(",") if args.actions else e.adapter.actions()
        done = {(r["task"], r["action"]) for r in e.store.episodes() if r["judge"] == e.judge_hash() and r["purpose"] == ("exam" if split == "holdout" else "train")}
        for t in e.adapter.tasks(split)[: args.limit]:
            for act in acts:
                if (t["id"], act) not in done:
                    r = e.run(t["id"], act, purpose="exam" if split == "holdout" else "train")
                    print(t["id"], act, r["outcome"]["full"], r["outcome"]["fail_count"], flush=True)


if __name__ == "__main__":
    main()
