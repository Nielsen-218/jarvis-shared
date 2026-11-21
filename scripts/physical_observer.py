"""Owner-assisted physical test observer (read-only).

Run this, then speak or send ONE command from the paired phone. It watches the owner PC ledger for new rows and prints the
stage timings and verified outcome as they land.  It never sends input, never prints speech text or tokens, and exits after
`--seconds` (default 120) or after the first verified/stopped task.

  python scripts/physical_observer.py --seconds 180
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--data", default=r"C:\Users\you\ECC\jarvis\data", help="data folder of the service under test (read only)")
    args = parser.parse_args()
    from core import storage
    storage.DATA = Path(args.data)
    from core import mediastate, pcledger, users
    users.set_current("owner") if hasattr(users, "set_current") else None
    seen = {(r["component"], r["at"]) for r in pcledger.history()}
    print("Watching the PC ledger. Give the command now (voice or phone).", flush=True)
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        for row in sorted(pcledger.history(), key=lambda r: r["at"]):
            key = (row["component"], row["at"])
            if key in seen:
                continue
            seen.add(key)
            keep = {k: v for k, v in row.items() if k in ("component", "stage", "milliseconds", "route", "tool", "method", "verified", "ok", "site", "app", "billable_usd")}
            print(time.strftime("%H:%M:%S", time.localtime(row["at"])), keep, flush=True)
            if row["component"] == "local_automation":
                print("media sessions now:", [s for s in mediastate.sessions() if s["status"] == "Playing"], flush=True)
                return
        time.sleep(.5)
    print("No new task was observed in the window.")


if __name__ == "__main__":
    main()
