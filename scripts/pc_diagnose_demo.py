"""Troubleshoot and maintenance checks. Read-only: this never changes the PC.

  python scripts/pc_diagnose_demo.py "my pc is slow"      pick the right checks for a symptom
  python scripts/pc_diagnose_demo.py                      quick general health check
  python scripts/pc_diagnose_demo.py --all                every check (event logs, startup, PATH, drivers, upgrades, ...)
  python scripts/pc_diagnose_demo.py "wifi drops" --recheck   run twice and show what got resolved (use after you fix something)

Every command goes through the permission gate and is T0. Results are advice; fixes go through the gated skills.
Activity is logged to C:\\Jarvis\\audit.jsonl.
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root, so `jarvis_pc` imports
import argparse
import os
import sys

if os.name != "nt":
    sys.exit("This tool is for the Windows PC.")

from jarvis_pc.diagnostics import ALL_CHECKS, Diagnostics, compare, plan_checks, run_checks
from jarvis_pc.guardrails import Guardrails
from jarvis_pc.verify import ChangeJournal

ap = argparse.ArgumentParser()
ap.add_argument("symptom", nargs="?", default="", help="what is wrong, in plain words")
ap.add_argument("--all", action="store_true", help="run every check")
ap.add_argument("--recheck", action="store_true", help="after the first run, wait for Enter, run again and compare")
ap.add_argument("--root", default=r"C:\Jarvis")
args = ap.parse_args()

os.makedirs(args.root, exist_ok=True)
guard = Guardrails(allowlist_roots=[args.root], audit_path=os.path.join(args.root, "audit.jsonl"), dry_run=False)
journal = ChangeJournal(os.path.join(args.root, "journal.jsonl"), os.path.join(args.root, "snapshots"))
diag = Diagnostics(guard, journal=journal)
names = ALL_CHECKS if args.all else plan_checks(args.symptom)

first = run_checks(diag, names, args.symptom)
print(first.render())
if args.recheck:
    input("\nFix something, then press Enter to check again... ")
    second = run_checks(diag, names, args.symptom)
    cmp = compare(first, second)
    print("\n" + second.render())
    print("\nResolved: " + (", ".join(f.title for f in cmp["resolved"]) or "nothing"))
    print("Still there: " + (", ".join(f.title for f in cmp["remaining"]) or "nothing"))
    print("New: " + (", ".join(f.title for f in cmp["new"]) or "nothing"))
sys.exit(0 if first.result != "FAIL" else 1)
