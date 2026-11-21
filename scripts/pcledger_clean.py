"""Remove the test suite's rows from the PC-control ledger (machine-state/pc-control/ledger.sqlite).

Before 2026-11-10 the tests wrote into the live ledger: tests/test_antistuck.py makes steps time out on purpose, and every
StepTimeout row (plus the follow-on RuntimeError row and a `methods` failure) landed in the owner's ledger, so focus_app
looked 5% reliable and run_terminal 48%. tests/conftest.py now points the suite at a temp folder; this cleans what is there.

  python scripts/pcledger_clean.py                    # dry run on the live ledger (opened read-only): counts only
  python scripts/pcledger_clean.py --ledger COPY      # dry run on a copy
  python scripts/pcledger_clean.py --apply            # back the file up beside itself, then clean it

What counts as a test row:
  attributed  a StepTimeout usage row whose `where` field (the stuck worker's last frames) names a test_*.py or
              conftest.py file. Always cleaned.
  inferred    a StepTimeout row with no frames ("ended after the stop") written within --window seconds of an attributed
              row: the same test run (run_terminal's test is one). Cleaned only with --include-inferred.
  follow-on   the RuntimeError row the executor writes right after a cleaned timeout (same tool, no `where`).
`methods` is then corrected: each follow-on row was one failure counted against the situation of the test's step (see
TEST_STEPS). Those failures are subtracted; a situation left with no successes and no failures is deleted; one left with no
failures loses its cool-down (last_failure = 0). Nothing else in the file is touched. Output is counts only.

Run it with Jarvis stopped or not: it takes the ledger's own write lock for a moment. It never runs on its own.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pcledger import fingerprint  # noqa: E402

LIVE = ROOT / "machine-state" / "pc-control" / "ledger.sqlite"
TEST_FRAME = re.compile(r"\b(?:test_\w+|conftest)\.py:\d+")
# Steps the suite runs through pcmethods.execute() with a deliberate timeout; its `methods` row is keyed by this step.
TEST_STEPS = {"focus_app": {"tool": "focus_app", "arguments": {"process": "notepad.exe"}}}
FOLLOW_ON_SECONDS = 0.5


def _usage(db):
    rows = []
    for rid, uid, component, data, created in db.execute("SELECT id,uid,component,data,created FROM usage ORDER BY created"):
        try:
            d = json.loads(data)
        except ValueError:
            d = {}
        rows.append({"id": rid, "uid": uid, "component": component, "d": d, "at": created})
    return rows


def plan(db, window=120.0, include_inferred=False):
    """What would be removed: {'attributed','inferred','follow_on': [row ids], 'methods': {...}} (counts come from these)."""
    rows = _usage(db)
    timeouts = [r for r in rows if r["component"] == "local_automation" and r["d"].get("error_type") == "StepTimeout"]
    attributed = [r for r in timeouts if TEST_FRAME.search(str(r["d"].get("where", "")))]
    marks = sorted(r["at"] for r in attributed)
    inferred = []
    for r in timeouts:
        if r in attributed or str(r["d"].get("where", "")) != "ended after the stop":
            continue
        if any(abs(t - r["at"]) <= window for t in marks):
            inferred.append(r)
    cleaned = attributed + (inferred if include_inferred else [])
    gone = {r["id"] for r in cleaned}
    follow_on = []
    for k, r in enumerate(rows):
        if r["id"] not in gone:
            continue
        for x in rows[k + 1:k + 6]:
            if x["at"] - r["at"] > FOLLOW_ON_SECONDS:
                break
            if (x["uid"] == r["uid"] and x["d"].get("tool") == r["d"].get("tool") and x["d"].get("error_type") == "RuntimeError"
                    and "where" not in x["d"] and x["id"] not in gone and x["id"] not in follow_on):
                follow_on.append(x["id"])
                break
    by = {r["id"]: r for r in rows}
    owed = {}        # (uid, tool, method) -> failures the tests added to `methods`
    for rid in follow_on:
        d = by[rid]["d"]
        key = (by[rid]["uid"], d.get("tool"), d.get("method"))
        owed[key] = owed.get(key, 0) + 1
    return {"attributed": [r["id"] for r in attributed], "inferred": [r["id"] for r in inferred],
            "removed": sorted(gone) , "follow_on": follow_on, "owed": owed, "tools": _tool_counts(cleaned)}


def _tool_counts(rows):
    out = {}
    for r in rows:
        out[r["d"].get("tool", "?")] = out.get(r["d"].get("tool", "?"), 0) + 1
    return out


def method_fixes(db, owed):
    """[(uid, situation, method, action, new_failures)] for the `methods` rows the tests polluted."""
    fixes = []
    for (uid, tool, method), n in owed.items():
        step = TEST_STEPS.get(tool)
        if not step:
            continue
        situation = fingerprint(step)
        row = db.execute("SELECT successes,failures FROM methods WHERE uid=? AND situation=? AND method=?",
                         (uid, situation, method)).fetchone()
        if not row:
            continue
        left = max(0, row[1] - n)
        action = "delete" if left == 0 and row[0] == 0 else "cooldown_off" if left == 0 else "lower"
        fixes.append((uid, situation, method, action, left))
    return fixes


def summarize(p, fixes):
    return {"attributed_timeouts": len(p["attributed"]), "inferred_timeouts": len(p["inferred"]),
            "rows_to_remove": len(p["removed"]) + len(p["follow_on"]), "follow_on_rows": len(p["follow_on"]),
            "by_tool": p["tools"], "methods_rows_to_fix": len(fixes), "methods_actions": [f[3] for f in fixes]}


def apply(path, window, include_inferred):
    """Back the file up beside itself, then remove the rows and correct `methods` in one transaction."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    src = sqlite3.connect(path, timeout=10)
    dst = sqlite3.connect(backup)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("BEGIN IMMEDIATE")
        p = plan(db, window, include_inferred)
        fixes = method_fixes(db, p["owed"])
        ids = p["removed"] + p["follow_on"]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            db.execute(f"DELETE FROM usage WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        for uid, situation, method, action, left in fixes:
            if action == "delete":
                db.execute("DELETE FROM methods WHERE uid=? AND situation=? AND method=?", (uid, situation, method))
            elif action == "cooldown_off":
                db.execute("UPDATE methods SET failures=0,last_failure=0 WHERE uid=? AND situation=? AND method=?",
                           (uid, situation, method))
            else:
                db.execute("UPDATE methods SET failures=? WHERE uid=? AND situation=? AND method=?", (left, uid, situation, method))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    return backup, summarize(p, fixes)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ledger", type=Path, default=LIVE, help="ledger.sqlite to read (default: the live one, read-only)")
    ap.add_argument("--apply", action="store_true", help="really clean the file (default is a dry run that changes nothing)")
    ap.add_argument("--include-inferred", action="store_true", help="also clean frame-less StepTimeouts from the same test run")
    ap.add_argument("--window", type=float, default=120.0, help="seconds around an attributed row that count as the same run")
    a = ap.parse_args(argv)
    if not a.ledger.exists():
        print(f"no ledger at {a.ledger}")
        return 2
    if a.apply:
        backup, summary = apply(a.ledger, a.window, a.include_inferred)
        print(json.dumps({"mode": "apply", "backup": str(backup), **summary}, indent=1))
        return 0
    db = sqlite3.connect(f"file:{a.ledger.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        p = plan(db, a.window, a.include_inferred)
        print(json.dumps({"mode": "dry-run", "wrote_nothing": True, **summarize(p, method_fixes(db, p["owed"]))}, indent=1))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
