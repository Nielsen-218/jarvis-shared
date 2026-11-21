"""One-off: add the owner's goals from his past logs as PROPOSED goals (Partner stage 2, core/commitments.SEED).

They are never made active here: the first morning report says "I think these are your goals ... tell me which are real",
and only his yes ("the insurance one is a goal", "all of them") activates one. Idempotent: a seed goal that exists in any
state (even one he dropped) is left alone, so running it twice adds nothing.

  python scripts/seed_partner_goals.py            # dry run: prints what would be added
  python scripts/seed_partner_goals.py --apply    # writes data/users/<owner>/goals-<machine>.json of THIS checkout

Run it from the live checkout (C:\\Users\\you\\ECC\\jarvis) on the PC; a worktree has its own empty data/.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write the goals (default: dry run)")
    args = ap.parse_args(argv)
    from core import commitments, storage
    added = commitments.seed_owner(dry_run=not args.apply)
    where = storage.DATA
    if not added:
        print(f"Nothing to add: every seed goal is already there ({where}).")
        return 0
    verb = "Added" if args.apply else "Would add (dry run, pass --apply)"
    print(f"{verb} {len(added)} proposed goals under {where}:")
    for s in commitments.SEED:
        if s["gid"] in added:
            print(f"  {s['gid']}: {s['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
