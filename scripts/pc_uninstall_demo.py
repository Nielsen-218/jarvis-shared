"""Uninstall and cleanup, through the same permission gate as installs.

  python scripts/pc_uninstall_demo.py                       list what is installed (read-only)
  python scripts/pc_uninstall_demo.py ripgrep               show the plan only (nothing is changed)
  python scripts/pc_uninstall_demo.py ripgrep --go          uninstall for real: moves the app (and its installer) to quarantine
  python scripts/pc_uninstall_demo.py --winget Git.Git [--go]
  python scripts/pc_uninstall_demo.py --cleanup [--go]      move leftovers (extracted installers, .part files, empty folders)
  python scripts/pc_uninstall_demo.py --purge [--older-than-days 7] [--go]
                                                 PERMANENTLY delete quarantined items (asks per batch, cannot be undone)
  python scripts/pc_uninstall_demo.py --undo                put back everything the last uninstall/cleanup moved

Nothing is deleted until --purge. Everything is logged to C:\\Jarvis\\audit.jsonl and C:\\Jarvis\\journal.jsonl.
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root, so `jarvis_pc` imports
import argparse
import os
import sys

if os.name != "nt":
    sys.exit("This demo is for the Windows PC.")

from jarvis_pc.guardrails import Guardrails
from jarvis_pc.uninstall import UninstallPipeline
from jarvis_pc.verify import ChangeJournal
from jarvis_pc.windows import make_pre_change

ap = argparse.ArgumentParser()
ap.add_argument("name", nargs="?", help="app folder name under apps (for example ripgrep)")
ap.add_argument("--winget", help="winget package id to uninstall")
ap.add_argument("--cleanup", action="store_true", help="move leftovers JARVIS created to quarantine")
ap.add_argument("--purge", action="store_true", help="permanently delete quarantined items")
ap.add_argument("--older-than-days", type=float, default=0.0)
ap.add_argument("--go", action="store_true", help="really do it (default is plan only)")
ap.add_argument("--undo", action="store_true", help="undo the last uninstall/cleanup/install group")
ap.add_argument("--root", default=r"C:\Jarvis", help="folder JARVIS is allowed to write in")
args = ap.parse_args()

root = args.root
os.makedirs(root, exist_ok=True)


def ask(req):
    print("\n" + "=" * 60)
    print(f"JARVIS WANTS PERMISSION  [{req.tier.name}]")
    print(f"  action: {req.command}")
    for r in req.reasons:
        print(f"  - {r}")
    answer = input("Type yes to allow, anything else to refuse: ").strip().lower()
    print("=" * 60)
    return answer == "yes"


live = args.go or args.undo
guard = Guardrails(
    allowlist_roots=[root],
    audit_path=os.path.join(root, "audit.jsonl"),
    approver=ask,
    pre_change=make_pre_change(),
    dry_run=not live,
)
journal = ChangeJournal(os.path.join(root, "journal.jsonl"), os.path.join(root, "snapshots"))
pipe = UninstallPipeline(guard, journal, apps_dir=os.path.join(root, "apps"),
                         quarantine_dir=os.path.join(root, "quarantine"))

if args.undo:
    ids = journal.last_group_ids()
    if not ids:
        sys.exit("Nothing to undo.")
    print("Undoing: " + ", ".join(e["description"] for e in journal.undo_plan(ids=ids)))
    outcomes = journal.undo_last_group(guard)
    for o in outcomes:
        print(f"  [{'ok' if o.ok else 'FAILED'}] {o.step.kind} {o.step.command or o.step.path or ''}: {o.detail}")
    sys.exit(0 if outcomes and all(o.ok for o in outcomes) else 1)

if args.winget:
    plan = pipe.plan_winget(args.winget)
elif args.cleanup:
    plan = pipe.plan_cleanup()
elif args.purge:
    plan = pipe.plan_purge(args.older_than_days)
elif args.name:
    plan = pipe.plan_uninstall(args.name)
else:
    print(f"Installed under {pipe.apps_dir}:")
    for m in pipe.inventory():
        print(f"  {os.path.basename(m.src):45} {m.files:5} file(s)  {m.size / 1048576:8.1f} MB  ({m.why})")
    sys.exit(0)

print(plan.render())
if plan.blocked_reason:
    sys.exit(f"\nStopped: {plan.blocked_reason}")
if plan.empty:
    sys.exit(0)
if not args.go:
    print("\nDRY RUN: nothing was changed. To do it for real, run again with --go")
    sys.exit(0)

result = pipe.execute(plan)
print("\n" + result.report.render())
if result.rolled_back:
    print("\nSomething failed; everything this run moved was put back.")
elif result.report.result == "PASS" and plan.method != "purge":
    print("\nDone and verified. To put it back:  python scripts/pc_uninstall_demo.py --undo")
sys.exit(0 if result.report.result == "PASS" else 1)
