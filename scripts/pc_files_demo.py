"""File skills: search, duplicate report, verified backup, restore test.

  python scripts/pc_files_demo.py --find C:\\Users\\me\\Documents "*.pdf"
  python scripts/pc_files_demo.py --dupes C:\\Users\\me\\Pictures            report only: deletes nothing
  python scripts/pc_files_demo.py --backup C:\\Users\\me\\Documents          copy -> verify every file -> journal (T1)
  python scripts/pc_files_demo.py --restore-test <backup folder>            restore to a scratch folder and check hashes
  python scripts/pc_files_demo.py --undo                                    remove the last backup/scratch copy (asks first)

Backups go to C:\\Jarvis\\backups and are never overwritten. Credential folders (.ssh, .aws, ...) are skipped.
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root, so `jarvis_pc` imports
import argparse
import os
import sys

if os.name != "nt":
    sys.exit("This tool is for the Windows PC.")

from jarvis_pc.files import FileTools, find_duplicates, find_files
from jarvis_pc.guardrails import Guardrails
from jarvis_pc.verify import ChangeJournal

ap = argparse.ArgumentParser()
ap.add_argument("--find", nargs=2, metavar=("FOLDER", "PATTERN"))
ap.add_argument("--dupes", metavar="FOLDER")
ap.add_argument("--backup", metavar="SOURCE")
ap.add_argument("--restore-test", metavar="BACKUP")
ap.add_argument("--undo", action="store_true")
ap.add_argument("--root", default=r"C:\Jarvis")
args = ap.parse_args()
root = args.root
os.makedirs(root, exist_ok=True)


def ask(req):
    print("\n" + "=" * 60)
    print(f"JARVIS WANTS PERMISSION  [{req.tier.name}]\n  action: {req.command}")
    for r in req.reasons:
        print(f"  - {r}")
    ok = input("Type yes to allow, anything else to refuse: ").strip().lower() == "yes"
    print("=" * 60)
    return ok


guard = Guardrails(allowlist_roots=[root], audit_path=os.path.join(root, "audit.jsonl"), approver=ask, dry_run=False)
journal = ChangeJournal(os.path.join(root, "journal.jsonl"), os.path.join(root, "snapshots"))
tools = FileTools(guard, journal)

if args.find:
    hits = find_files(args.find[0], args.find[1])
    print("\n".join(hits) or "no matches")
    print(f"{len(hits)} match(es) (max 200 shown)")
elif args.dupes:
    groups = find_duplicates(args.dupes)
    for g in groups[:25]:
        print(f"{g.wasted / 1048576:.1f} MB wasted, {len(g.paths)} copies of {g.size} bytes:")
        for p in g.paths:
            print("   " + p)
    print(f"{len(groups)} duplicate group(s), {sum(g.wasted for g in groups) / 1048576:.1f} MB reclaimable. Nothing was deleted.")
elif args.backup:
    r = tools.backup(args.backup, os.path.join(root, "backups"))
    print(r.report.render())
    sys.exit(0 if r.report.result == "PASS" else 1)
elif args.restore_test:
    rep = tools.restore_test(args.restore_test, os.path.join(root, "scratch"))
    print(rep.render())
    sys.exit(0 if rep.result == "PASS" else 1)
elif args.undo:
    ids = journal.last_group_ids()
    if not ids:
        sys.exit("Nothing to undo.")
    outcomes = journal.undo_last_group(guard)
    for o in outcomes:
        print(f"  [{'ok' if o.ok else 'FAILED'}] {o.step.kind} {o.step.path or o.step.command}: {o.detail}")
    sys.exit(0 if all(o.ok for o in outcomes) else 1)
else:
    ap.print_help()
