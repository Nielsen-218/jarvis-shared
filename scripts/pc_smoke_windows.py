"""Windows smoke test for JARVIS: exercises the real PowerShell path the unit tests skip.

Run from the project folder (next to README.md):   python scripts/pc_smoke_windows.py
Safe by design: read-only commands, one folder created and removed inside a temp folder,
and the permission gate denies everything risky. Add --scan to also run a real Defender
scan on a harmless file (updates Defender definitions first, takes a minute or two).
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # repo root, so `jarvis_pc` imports
import os
import shutil
import sys
import tempfile
import threading
import time

if os.name != "nt":
    sys.exit("This smoke test is for Windows. On Linux, the unit tests already cover this path.")

from jarvis_pc.actions import make_runner, run
from jarvis_pc.guardrails import Guardrails, KillSwitch
from jarvis_pc.security import scan_file, signature_check

results = []


def check(name, passed, detail=""):
    results.append((name, passed))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


tmp = os.path.join(tempfile.gettempdir(), "jarvis_smoke")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp)

ks = KillSwitch()
asked = []


def approver(req):
    asked.append(req.command)
    return req.command.startswith("Start-Sleep")  # the only thing we allow, for the timeout tests


guard = Guardrails(allowlist_roots=[tmp], audit_path=os.path.join(tmp, "audit.jsonl"),
                   approver=approver, killswitch=ks, dry_run=False)

print(f"Python {sys.version.split()[0]} | temp folder: {tmp}\n")

# 1. read-only commands run automatically
r = run(guard, "Get-Date", cwd=tmp)
check("T0 read-only command runs", r.ok and r.stdout.strip() != "", r.stdout.strip()[:40])
r = run(guard, "echo hello", cwd=tmp)
check("output is captured", r.ok and "hello" in r.stdout, repr(r.stdout.strip()))

# 2. reversible change inside the allowed folder runs without asking
before = len(asked)
target = os.path.join(tmp, "made_by_jarvis")
r = run(guard, f"mkdir '{target}'", cwd=tmp)
check("T1 change inside allowed folder", r.ok and os.path.isdir(target) and len(asked) == before,
      (r.stderr or "").strip()[:80])

# 3. the same kind of change outside the allowed folder is stopped
outside = r"C:\Windows\Temp\jarvis_should_not_exist"
r = run(guard, f"mkdir '{outside}'", cwd=tmp)
check("change outside allowed folder is blocked", (not r.ran) and not os.path.exists(outside))

# 4. never-allowed command is blocked
r = run(guard, "Set-MpPreference -DisableRealtimeMonitoring $true", cwd=tmp)
check("T3 (disable Defender) is blocked", not r.ran)

# 5. errors are understood
r = run(guard, r"Get-Item 'C:\nonexistent_zzz_path'", cwd=tmp)
check("error is classified", r.ran and r.exit_code != 0 and r.error_kind == "not_found",
      f"exit {r.exit_code}, kind {r.error_kind}")

# 6. timeout kills the whole process tree
t = time.monotonic()
r = run(guard, "Start-Sleep -Seconds 30", cwd=tmp, timeout=2)
check("timeout stops a long command", r.timed_out and time.monotonic() - t < 10,
      f"{time.monotonic() - t:.1f}s")

# 7. kill switch stops a running command
t = time.monotonic()
threading.Timer(1.0, ks.trigger).start()
r = run(guard, "Start-Sleep -Seconds 30", cwd=tmp, timeout=25)
check("kill switch stops a running command", r.killed and time.monotonic() - t < 10,
      f"{time.monotonic() - t:.1f}s")
ks.reset()

# 8. tools JARVIS will rely on
r = run(guard, "winget --version", cwd=tmp)
check("winget available", r.ok, r.stdout.strip() or (r.stderr or "").strip()[:60])
r = run(guard, "Get-MpComputerStatus | Format-List AMServiceEnabled,RealTimeProtectionEnabled", cwd=tmp)
check("Defender status readable", r.ok and "AMServiceEnabled" in r.stdout,
      " ".join(r.stdout.split())[:70] or (r.stderr or "").strip()[:60])
sig = signature_check(make_runner(guard, cwd=tmp), sys.executable)
check("signature check works (on python.exe)", sig.status in ("Valid", "NotSigned", "NotTrusted"), sig.status)

# 9. optional real Defender scan
if "--scan" in sys.argv:
    sample = os.path.join(tmp, "harmless.txt")
    with open(sample, "w") as f:
        f.write("hello")
    res = scan_file(make_runner(guard, cwd=tmp), sample, timeout=600)
    check("real Defender scan of a harmless file", res.passed, res.detail)

# 10. audit trail intact
ok, err = guard.audit.verify()
check("audit log chain is intact", ok, err or "")

print()
print("Commands the gate asked about:", asked if asked else "none")
failed = [n for n, p in results if not p]
print(f"\n{len(results) - len(failed)}/{len(results)} passed." + (f"  FAILED: {', '.join(failed)}" if failed else ""))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if failed else 0)
