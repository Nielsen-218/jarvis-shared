"""Check the desktop practice worlds (core/labworlds) for real, on the hidden lab desktop.

  python scripts/lab_worlds.py [kind ...]

For each world the lab's own reference method must pass the judge and doing nothing must fail it. No model is called.
It refuses to run while the owner is using the PC, a game runs, a mission runs or the graphics card is busy (the same
guards as the nightly practice), and the lab stops the moment the keyboard or mouse is touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv) -> int:
    from core import acquire, labpractice
    why = acquire.idle_ready()
    if why:
        print(f"not run: {why}")
        return 2
    rows = labpractice.self_check(argv or None)
    for r in rows:
        ok = r["reference_passed"] and r["noop_failed"]
        print(f"{'OK  ' if ok else 'FAIL'} {r['kind']:17} reference_passed={r['reference_passed']} noop_failed={r['noop_failed']} lab={r['lab']} {r['detail']}")
    return 0 if all(r["reference_passed"] and r["noop_failed"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
