"""PC-authoritative task approvals, workflow evidence and component accounting.

Only authenticated owner UI/CLI endpoints grant approvals; these functions are not AI tools.
SQLite transactions bind grants to exact contents and prevent concurrent reuse.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager

from core import storage, users
from core.config import ROOT


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def state_dir() -> Path:
    """The PC-control state folder (ledger, procedure notes). Tests point JARVIS_PC_STATE_DIR at a temp folder, and
    tests/conftest.py refuses this live default (it once collected the suite's StepTimeout rows)."""
    return Path(os.getenv("JARVIS_PC_STATE_DIR", str(ROOT / "machine-state" / "pc-control")))


@contextmanager
def database():
    # Grants must never be copied backwards by the household's two-way data sync.
    path = state_dir() / "ledger.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, uid TEXT, kind TEXT, fp TEXT, data TEXT, state TEXT, created REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY, uid TEXT, component TEXT, data TEXT, created REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS methods(uid TEXT, situation TEXT, method TEXT, successes INTEGER, failures INTEGER, last_success REAL, last_failure REAL, PRIMARY KEY(uid,situation,method))")
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def create(kind, data, state="pending"):
    rid = uuid.uuid4().hex
    with database() as db:
        db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?)", (rid, users.current(), kind,
            fingerprint(data), json.dumps(data), state, time.time()))
    return rid


def read(rid, kind=None):
    if not isinstance(rid, str) or not re.fullmatch(r"[0-9a-f]{32}", rid):
        raise ValueError("Invalid record ID.")
    with database() as db:
        row = db.execute("SELECT * FROM records WHERE id=? AND uid=?", (rid, users.current())).fetchone()
    if not row or (kind and row["kind"] != kind):
        raise ValueError("Record not found for this owner.")
    return {**dict(row), "data": json.loads(row["data"])}


def approve(rid, kind, amount=None):
    """Trusted owner ingress only: exact plan or one cloud planning call, valid five minutes."""
    with database() as db:
        row = db.execute("SELECT * FROM records WHERE id=? AND uid=? AND kind=?", (rid, users.current(), kind)).fetchone()
        if not row or row["state"] != "pending" or time.time() - row["created"] > 300:
            raise ValueError("Approval request expired, already used or not found.")
        data = json.loads(row["data"])
        if fingerprint(data) != row["fp"]:
            raise ValueError("Request contents changed; obtain fresh approval.")
        if kind == "cloud":
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or not 0 < amount <= 1:
                raise ValueError("Choose an explicit budget greater than $0 and at most $1 for one planning call.")
            data["authorized_usd"] = amount
        db.execute("UPDATE records SET data=?,state='granted',created=? WHERE id=?", (json.dumps(data), time.time(), rid))


def consume(kind, exact):
    with database() as db:
        rows = db.execute("SELECT * FROM records WHERE uid=? AND kind=? AND state='granted' AND created>? ORDER BY created DESC",
            (users.current(), kind, time.time() - 300)).fetchall()
        for row in rows:
            data = json.loads(row["data"])
            binding = {k: v for k, v in data.items() if k != "authorized_usd"}
            if fingerprint(binding) == row["fp"] and fingerprint(binding) == fingerprint(exact):
                db.execute("UPDATE records SET state='used' WHERE id=?", (row["id"],))
                return data
    return None


def usage(component, **fields):
    with database() as db:
        db.execute("INSERT INTO usage VALUES(?,?,?,?,?)", (uuid.uuid4().hex, users.current(), component,
            json.dumps(fields), time.time()))


def history():
    with database() as db:
        rows = db.execute("SELECT * FROM usage WHERE uid=? ORDER BY created DESC LIMIT 100", (users.current(),)).fetchall()
    return [{"component": r["component"], "at": r["created"], **json.loads(r["data"])} for r in rows]


def latency(component, started, **fields):
    """Persist a monotonic duration without recording command or speech text."""
    milliseconds = round((time.monotonic() - started) * 1000, 2)
    usage("latency", stage=component, milliseconds=milliseconds, **fields)
    return milliseconds


def latency_summary(limit=1000):
    """Observed p50/p95 per stage for the owner-facing PC diagnostics API."""
    with database() as db:
        rows = db.execute("SELECT data FROM usage WHERE uid=? AND component='latency' ORDER BY created DESC LIMIT ?",
                          (users.current(), limit)).fetchall()
    samples = {}
    for row in rows:
        data = json.loads(row["data"])
        value = data.get("milliseconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            samples.setdefault(data.get("stage", "unknown"), []).append(float(value))
    def percentile(values, point):
        values.sort()
        # Nearest-rank avoids understating p95 for small operational samples.
        # With n=6, p95 is the slowest observed sample, not the fifth one.
        return round(values[min(len(values) - 1, max(0, math.ceil(len(values) * point) - 1))], 2)
    return {stage: {"count": len(values), "p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95)}
            for stage, values in samples.items()}


def method_outcome(step, method, success):
    """Only executor-verified outcomes teach preferences; never executable model text."""
    now = time.time()
    with database() as db:
        db.execute("INSERT INTO methods VALUES(?,?,?,?,?,?,?) ON CONFLICT(uid,situation,method) DO UPDATE SET "
            "successes=successes+excluded.successes, failures=failures+excluded.failures, "
            "last_success=MAX(last_success,excluded.last_success), last_failure=MAX(last_failure,excluded.last_failure)",
            (users.current(), fingerprint(step), method, int(success), int(not success), now if success else 0, 0 if success else now))


def method_order(step, candidates):
    """Prefer working methods for this exact situation. Failed methods cool down for a day.

    Only caller-supplied, code-reviewed candidates can be selected; database rows add no tools.
    """
    with database() as db:
        rows = db.execute("SELECT * FROM methods WHERE uid=? AND situation=?", (users.current(), fingerprint(step))).fetchall()
    stats = {r["method"]: r for r in rows}
    def rank(method):
        r = stats.get(method)
        if not r:
            return (0, 0)
        failed = r["last_failure"] > r["last_success"] and time.time() - r["last_failure"] < 86400
        return (1 if failed else -1 if r["successes"] else 0, -r["last_success"])
    ordered = sorted(candidates, key=rank)
    if step.get("tool") not in _learned_native_first_tools():      # only the desktop kinds with a production check are held behind native
        return ordered
    return _learned_placed(ordered, stats)


def _learned_native_first_tools():
    from core import skillcontracts
    return skillcontracts.PRODUCTION_KINDS


def _failed_lately(r) -> bool:
    """A failure in the last 24 hours that no later success has cancelled (as rank() reads it)."""
    return bool(r) and (r["last_failure"] or 0) > (r["last_success"] or 0) and time.time() - r["last_failure"] < 86400


def _learned_placed(order, stats):
    """Where an approved learned method (core/acquire, "learned:<id>") goes. The built-in method keeps first place EXCEPT where it
    has failed for this exact situation in the last 24 hours and the learned method has not: then the learned one is tried first.
    JARVIS_LEARNED_FIRST=1 puts approved learned methods first regardless (an explicit proof run only)."""
    learned = [m for m in order if m.startswith("learned:")]
    if not learned or "native" not in order:
        return order
    rest = [m for m in order if not m.startswith("learned:")]
    if os.getenv("JARVIS_LEARNED_FIRST", "").lower() in ("1", "on", "true", "yes"):
        return learned + rest
    native_failed = _failed_lately(stats.get("native"))
    first = [m for m in learned if native_failed and not _failed_lately(stats.get(m))]
    later = [m for m in learned if m not in first]
    at = rest.index("native") + 1
    return first + rest[:at] + later + rest[at:]


def decline(record_id) -> None:
    """The owner said no (in any words) to a plan waiting for review: it can no longer be approved."""
    with database() as db:
        db.execute("UPDATE records SET state='declined' WHERE id=? AND state='pending'", (record_id,))


def withdraw_since(since, uid=None) -> int:
    """Plans put up for review at or after `since` (time.time(), taken when the turn began) whose question was never said
    (he cut in): declined, so a later yes cannot approve them. Returns how many. (A plan is made by planning, well after
    the turn's first clock tick, so the 15.6 ms Windows clock cannot mix up this turn's plan with an earlier one.)"""
    with database() as db:
        cur = db.execute("UPDATE records SET state='declined' WHERE uid=? AND kind IN ('plan','cloud') AND state='pending' "
                         "AND created>=?", (uid or users.current(), since))
        return cur.rowcount


def pending():
    with database() as db:
        rows = db.execute("SELECT * FROM records WHERE uid=? AND kind IN ('plan','cloud') AND state='pending' AND created>? ORDER BY created DESC LIMIT 20",
            (users.current(), time.time() - 300)).fetchall()
    result = []
    for r in rows:
        data = json.loads(r["data"])
        if fingerprint(data) != r["fp"]:
            continue
        if r["kind"] == "cloud":
            diagnostic = planning_failure(data.get("task"), r["created"])
            if diagnostic:
                # Display metadata only, outside the approval binding stored in records.
                data["local_planning"] = diagnostic
        result.append({"id": r["id"], "kind": r["kind"], "data": data})
    return result


def planning_failure(task, before):
    with database() as db:
        rows = db.execute("SELECT data FROM usage WHERE uid=? AND component='local_planning_failure' AND created<=? AND created>? ORDER BY created DESC LIMIT 100",
            (users.current(), before, before - 300)).fetchall()
    fp = fingerprint(task)
    for row in rows:
        data = json.loads(row["data"])
        if data.get("task_fingerprint") == fp:
            return {k: data[k] for k in ("status", "category", "code", "stage", "detail")}
    return None


def _trigger(text):
    """A workflow phrase as it is matched: any case, without the full stop speech-to-text adds ('Minimize Quad.')."""
    return re.sub(r"[\s.!?]+$", "", (text or "").strip()).casefold()


def save_workflow(run_id, name, trigger, demonstrated=False, by_owner=False):
    run = read(run_id, "run")["data"]
    if not run.get("verified") or run.get("state") != "completed":
        raise ValueError("Only a fully verified successful run may become a workflow.")
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or not isinstance(trigger, str) or not trigger.strip() or len(trigger) > 300:
        raise ValueError("Provide a short workflow name and exact trigger phrase.")
    data = {"name": name.strip(), "trigger": _trigger(trigger), "plan": run["plan"], "evidence_run": run_id}
    if demonstrated:
        data["demonstrated"] = True        # the owner showed these exact steps himself (core/demonstrate)
    if by_owner:
        data["saved_by_owner"] = True      # he saved this run under his own phrase (the PC page)
    rid = create("workflow", data, "saved")
    # Only routing hints may sync; procedures, approval grants and spending stay on the PC.
    hints = storage.load("pc_workflow_triggers.json", [])
    storage.save("pc_workflow_triggers.json", sorted(set(hints + [_trigger(trigger)])))
    return rid


def chosen_by_owner(wf):
    """Did the owner choose this workflow himself (showed the steps, or saved the run on the PC page), rather than a plan
    the gateway remembered silently (core.pchybrid saves those under the sentence itself: name = its first 80 characters)?"""
    if not isinstance(wf, dict):
        return False
    if wf.get("demonstrated") or wf.get("saved_by_owner"):
        return True
    name = _trigger(wf.get("name"))
    return not (name and _trigger(wf.get("trigger")).startswith(name))


def workflow(trigger):
    with database() as db:
        rows = db.execute("SELECT data FROM records WHERE uid=? AND kind='workflow' AND state='saved' ORDER BY created DESC",
            (users.current(),)).fetchall()
    want = _trigger(trigger)
    return next((json.loads(r[0]) for r in rows if _trigger(json.loads(r[0])["trigger"]) == want), None)


def prepared(task):
    with database() as db:
        rows = db.execute("SELECT * FROM records WHERE uid=? AND kind='plan' AND state IN ('pending','granted') AND created>? ORDER BY created DESC",
            (users.current(), time.time() - 300)).fetchall()
    return next(({**dict(r), "data": json.loads(r["data"])} for r in rows
        if json.loads(r["data"])["task"] == task and fingerprint(json.loads(r["data"])) == r["fp"]), None)
