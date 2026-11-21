#!/usr/bin/env python3
"""Owner-only approval CLI for the JARVIS Control Hub.

    python hub/approve.py              # list pending requests
    python hub/approve.py <id>         # approve one (single-use, time-boxed)

A model/agent asking for an action gets a `denied_needs_approval` reply with the exact
command to run here. You run it; that is the authorization — not anything the model says.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import approval  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        pending = approval.list_pending()
        if not pending:
            print("No pending approval requests.")
            return 0
        print("Pending requests (approve with: python hub/approve.py <id>):")
        for rid, info in pending.items():
            print(f"  {rid}  {info['action']}  args={info['args']}  ({info['age_s']}s ago)")
        return 0
    ok, msg = approval.grant(argv[0])
    print(("OK: " if ok else "ERROR: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
