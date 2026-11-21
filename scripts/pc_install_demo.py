"""First real JARVIS install, with everything wired together.

  python scripts/pc_install_demo.py              show the plan only (nothing is installed)
  python scripts/pc_install_demo.py --go         install it for real (asks you first)
  python scripts/pc_install_demo.py --undo       undo the last thing this script installed
  python scripts/pc_install_demo.py --github https://github.com/owner/tool [--go]
                                            install from a GitHub release (download, checksum, virus scan)

Default package is jq, a tiny command-line tool, installed per-user through winget.
Use another with:  python scripts/pc_install_demo.py Some.PackageId
Everything JARVIS does is logged to C:\\Jarvis\\audit.jsonl and C:\\Jarvis\\journal.jsonl.
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root, so `jarvis_pc` imports
import argparse
import os
import sys

if os.name != "nt":
    sys.exit("This demo is for the Windows PC.")

from jarvis_pc.guardrails import Guardrails
from jarvis_pc.install import InstallPipeline
from jarvis_pc.verify import ChangeJournal
from jarvis_pc.windows import make_pre_change

ap = argparse.ArgumentParser()
ap.add_argument("package", nargs="?", default="jqlang.jq", help="winget package id")
ap.add_argument("--github", help="GitHub repo link; installs from its latest Windows release")
ap.add_argument("--go", action="store_true", help="really install (default is plan only)")
ap.add_argument("--undo", action="store_true", help="undo the last install made through the journal")
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
    pre_change=make_pre_change(),  # creates a restore point before changes
    dry_run=not live,
)
journal = ChangeJournal(os.path.join(root, "journal.jsonl"), os.path.join(root, "snapshots"))
pipe = InstallPipeline(
    guard, journal, apps_dir=os.path.join(root, "apps"),
    known_names=["ripgrep", "jq", "git", "7zip", "python", "nodejs", "curl", "wget", "fzf", "bat", "fd"],
)

if args.undo:
    ids = journal.last_group_ids()
    if not ids:
        sys.exit("Nothing to undo.")
    pending = journal.undo_plan(ids=ids)
    print("Undoing the whole last install: " + ", ".join(e["description"] for e in pending))
    outcomes = journal.undo_last_group(guard)
    for o in outcomes:
        print(f"  [{'ok' if o.ok else 'FAILED'}] {o.step.kind} {o.step.command or o.step.path or ''}: {o.detail}")
    sys.exit(0 if outcomes and all(o.ok for o in outcomes) else 1)

if args.github:
    from jarvis_pc.install import GitHubError
    try:
        plan = pipe.plan_github(args.github)
    except GitHubError as e:
        sys.exit(f"Could not read that GitHub link: {e}")
else:
    plan = pipe.plan_winget(args.package)
print(plan.render(guard))

if plan.blocked_reason:
    sys.exit(f"\nStopped: {plan.blocked_reason}")
if not args.go:
    print("\nDRY RUN: nothing was installed or changed. To install for real, run again with --go")
    sys.exit(0)

print("\nStarting. A restore point is created first, which can take up to a minute.")
result = pipe.execute(plan)
print("\n" + result.report.render())
if result.rolled_back:
    print("\nThe install failed and was rolled back.")
elif result.report.result == "PASS":
    print("\nInstalled and verified. To remove it again:  python scripts/pc_install_demo.py --undo")
    if args.github:
        print(rf"Find the program with:  Get-ChildItem {os.path.join(root, 'apps')} -Recurse -Filter *.exe")
