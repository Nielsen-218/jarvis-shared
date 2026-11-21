"""Mark VI part 1: the mission store. A mission is "Jarvis, go do X while I'm away": JARVIS queues it, works on it in the
background, checks his own result and reports to the owner's phone (docs/plans/mark6-design.md).

Owner's decisions (2026-11-10): the queue lives on the LAPTOP (always on; the PC does the work); a step waiting for his
approval waits UNTIL HE ANSWERS (no answer is never a yes, and a parked mission never holds up other work); a mission never
takes the foreground (background only, to start).

This module is only the durable queue - no execution. Rules borrowed from the reference agents (hermes-agent, openclaw):
  * one SQLite file outside the synced data folder (machine-state/missions): never copied backwards by the household sync
  * every attempt is a row in `runs`; a mission found "running" at start-up was interrupted: it becomes "unknown", never
    replayed blindly; it may be retried ONCE, told to check what was already done first
  * asking for a mission is approval to QUEUE that mission only; JARVIS may mark a mission done / blocked / waiting;
    only the owner pauses, cancels or changes one
  * one global pause switch ("jarvis, pause missions")
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from core import users
from core.config import ROOT

STATES = ("queued", "running", "waiting_approval", "blocked", "done", "failed", "cancelled", "unknown")
ACTIVE = ("queued", "running", "waiting_approval", "unknown")
FINAL = ("done", "failed", "cancelled")
MAX_RETRIES_AFTER_UNKNOWN = 1
DEFAULT_BUDGET = {"minutes": 60, "steps": 40, "model_calls": 80, "cloud_usd": 0.0}
MAX_GOAL_CHARS = 500


def _dir() -> Path:
    return Path(os.getenv("JARVIS_MISSIONS_DIR", str(ROOT / "machine-state" / "missions")))


@contextmanager
def database():
    path = _dir() / "missions.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY, uid TEXT, goal TEXT, state TEXT, created REAL, updated REAL,"
                   " budget TEXT, result TEXT, approval TEXT, retries INTEGER DEFAULT 0, note TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, mission TEXT, machine TEXT, started REAL, ended REAL,"
                   " outcome TEXT, detail TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
        # 2026-11-10 Mark VI C1: a resumed mission continues from its checkpoint, and recovery goes by SILENCE (the run's
        # last heartbeat), so a laptop restart never re-queues a mission the PC is still working on.
        # C3: result_image names the evidence picture the laptop saved for the phone's mission sheet.
        for table, column in (("missions", "checkpoint TEXT"), ("runs", "progress TEXT"), ("runs", "progress_at REAL"),
                              ("missions", "result_image TEXT")):
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass                                                   # already there
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _row(r) -> Dict:
    d = dict(r)
    for k in ("budget", "approval", "checkpoint"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


# ---- the owner's side --------------------------------------------------------------------------------------------------
def add(goal: str, budget: Optional[Dict] = None) -> Dict:
    """Queue a mission in the owner's words. Asking is approval to queue THIS mission only (nothing runs unattended that
    would need his OK: risky steps still stop and ask)."""
    goal = " ".join((goal or "").split())
    if not goal:
        raise ValueError("A mission needs a goal.")
    if len(goal) > MAX_GOAL_CHARS:
        raise ValueError(f"A mission goal is at most {MAX_GOAL_CHARS} characters.")
    b = dict(DEFAULT_BUDGET, **(budget or {}))
    if b["cloud_usd"] < 0 or b["minutes"] <= 0 or b["steps"] <= 0:
        raise ValueError("Invalid mission budget.")
    mid, now = uuid.uuid4().hex[:12], time.time()
    with database() as db:
        db.execute("INSERT INTO missions(id, uid, goal, state, created, updated, budget, retries) VALUES(?,?,?,?,?,?,?,0)",
                   (mid, users.current(), goal, "queued", now, now, json.dumps(b)))
    m = get(mid)
    try:       # Partner stage 2: Jarvis now owes him this; core/commitments resolves it from the mission's state
        from core import commitments
        commitments.on_mission(m)
    except Exception as error:  # noqa: BLE001 - the mission is queued either way
        print(f"[commitments] mission {mid}: {type(error).__name__}: {error}")
    return m


def get(mid: str) -> Optional[Dict]:
    with database() as db:
        r = db.execute("SELECT * FROM missions WHERE id=? AND uid=?", (mid, users.current())).fetchone()
    return _row(r) if r else None


def listing(active_only: bool = False, limit: int = 20) -> List[Dict]:
    q = "SELECT * FROM missions WHERE uid=?" + (f" AND state IN ({','.join('?' * len(ACTIVE))})" if active_only else "")
    args = [users.current()] + (list(ACTIVE) if active_only else [])
    with database() as db:
        rows = db.execute(q + " ORDER BY created DESC LIMIT ?", args + [limit]).fetchall()
    return [_row(r) for r in rows]


def cancel(mid: str) -> Dict:
    """Owner only (not an AI tool target by itself: the voice tool calls it for HIS request)."""
    with database() as db:
        r = db.execute("SELECT state FROM missions WHERE id=? AND uid=?", (mid, users.current())).fetchone()
        if not r:
            raise ValueError("No such mission.")
        if r["state"] in FINAL:
            raise ValueError(f"That mission is already {r['state']}.")
        db.execute("UPDATE missions SET state='cancelled', updated=?, note=? WHERE id=?", (time.time(), "cancelled by the owner", mid))
    return get(mid)


def set_paused(on: bool) -> bool:
    """His pause switch. Resuming also lifts a hold he set by saying he was at the PC (one 'go' undoes both)."""
    with database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES('paused', ?)", ("1" if on else "0",))
        if not on:
            db.execute("DELETE FROM settings WHERE key='hold'")
    return on


def _settings() -> Dict[str, str]:
    """Both switches in one read: 'paused' (his pause) and 'hold' (his words)."""
    with database() as db:
        return {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings WHERE key IN ('paused', 'hold')")}


def paused_switch() -> bool:
    return _settings().get("paused") == "1"


# Owner 2026-11-10: "take away that if I'm at the PC rule. When I say I'm gonna game or I'm at the PC, stop anything that
# would interfere; if I don't say anything and I'm opening browsers, it's fine." So the worker no longer reads his
# keyboard/mouse; HIS WORDS hold missions. A hold lasts until he says he's done, or HOLD_HOURS (he may forget to say so).
def _hold_hours() -> float:
    try:
        return max(0.0, float(os.getenv("JARVIS_MISSION_HOLD_HOURS", "4")))
    except ValueError:      # a typo in .env ("4h") must not take the mission routes down
        return 4.0


HOLD_HOURS = _hold_hours()


def set_hold(reason: str) -> str:
    with database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES('hold', ?)",
                   (json.dumps({"reason": (reason or "you're at the PC")[:80], "until": time.time() + HOLD_HOURS * 3600}),))
    return hold_reason()


def clear_hold() -> None:
    with database() as db:
        db.execute("DELETE FROM settings WHERE key='hold'")


def _hold_from(raw: Optional[str]) -> str:
    """A malformed row is no hold (never an error: heartbeat, claim and the phone sheet all read it)."""
    try:
        h = json.loads(raw) if raw else None
        if isinstance(h, dict) and float(h.get("until") or 0) > time.time():
            return str(h.get("reason") or "you're at the PC")
    except (TypeError, ValueError):
        pass
    return ""


def hold_reason() -> str:
    """Why missions are held for him right now ('you're gaming'), or '' (none, or it ran out)."""
    return _hold_from(_settings().get("hold"))


def paused() -> bool:
    """Nothing new starts and a running mission yields: his pause switch, or a hold from his words."""
    s = _settings()
    return s.get("paused") == "1" or bool(_hold_from(s.get("hold")))


def answer_approval(mid: str, approved: bool, fp: str = "") -> Dict:
    """His answer to a parked step. It covers that exact step only; the mission continues from where it stopped.
    2026-11-10 review: the answer names the step he SAW (its fingerprint). A sheet left open on a second device while the
    mission moved on to another step can never approve that other step."""
    with database() as db:
        r = db.execute("SELECT state, approval, goal FROM missions WHERE id=? AND uid=?", (mid, users.current())).fetchone()
        if not r or r["state"] != "waiting_approval":
            raise ValueError("That mission is not waiting for your answer.")
        a = json.loads(r["approval"] or "{}")
        if not fp or fp != a.get("fp"):
            raise ValueError("That step changed since you opened it. Please look at it again.")
        if a.get("answer"):
            raise ValueError("That step was already answered.")
        a.update(answer="approved" if approved else "denied", answered=time.time())
        cur = db.execute("UPDATE missions SET state=?, approval=?, updated=?, note=? WHERE id=? AND state='waiting_approval' AND approval=?",
                         ("queued" if approved else "blocked", json.dumps(a), time.time(),
                          "approved: continues from the parked step" if approved else "you said no to a step", mid, r["approval"]))
        if cur.rowcount != 1:
            raise ValueError("That step changed since you opened it. Please look at it again.")
        goal = r["goal"] or ""
    try:        # 2026-11-16 studio: his yes/no on a work order is the learning metric's ground truth (core/studiolearn)
        from core import studio
        if studio.is_note(goal):
            from core import studiolearn
            studiolearn.note_decision(mid, approved)
    except Exception:  # noqa: BLE001 - a metric must never break his answer
        pass
    return get(mid)


# ---- the worker's side (the PC claims work from the laptop's store) ---------------------------------------------------
def claim_next(machine: str, uid: Optional[str] = None) -> Optional[Dict]:
    """The oldest queued mission becomes running with a new run row, atomically; None when paused or nothing is queued.
    2026-11-10 C1: only that person's missions (like get()), and the result carries the checkpoint and the approval, so a
    resumed mission continues from where it stopped."""
    if paused():
        return None
    uid = uid or users.current()
    with database() as db:
        r = db.execute("SELECT * FROM missions WHERE state='queued' AND uid=? ORDER BY created LIMIT 1", (uid,)).fetchone()
        if not r:
            return None
        rid, now = uuid.uuid4().hex[:12], time.time()
        db.execute("UPDATE missions SET state='running', updated=? WHERE id=? AND state='queued'", (now, r["id"]))
        db.execute("INSERT INTO runs(id, mission, machine, started) VALUES(?,?,?,?)", (rid, r["id"], machine, now))
    m = _row(r)
    m.update(state="running", run=rid)
    return m


def _current_run(db, run_id: str):
    """(mission id, is this run the mission's newest OPEN run). 2026-11-10 C1: a late finish/park from an old run (one that
    recovery already ended, or a second finish) must never change the mission again - and then sends no push."""
    run = db.execute("SELECT runs.mission, runs.ended FROM runs JOIN missions ON missions.id=runs.mission"
                     " WHERE runs.id=? AND missions.uid=?", (run_id, users.current())).fetchone()
    if not run:
        raise ValueError("No such run.")
    newest = db.execute("SELECT id FROM runs WHERE mission=? ORDER BY started DESC, rowid DESC LIMIT 1", (run["mission"],)).fetchone()
    return run["mission"], run["ended"] is None and newest["id"] == run_id


def _mission(mid: str, changed: bool) -> Dict:
    with database() as db:
        r = db.execute("SELECT * FROM missions WHERE id=?", (mid,)).fetchone()
    m = _row(r)
    m["changed"] = changed
    return m


def finish(run_id: str, outcome: str, detail: str = "") -> Dict:
    """JARVIS may mark a run done / failed / blocked (never cancelled: that is the owner's). `changed` is True only when
    this call really ended the running mission (the laptop pushes the report only then)."""
    if outcome not in ("done", "failed", "blocked"):
        raise ValueError("A run ends done, failed or blocked.")
    changed = False
    with database() as db:
        mid, current = _current_run(db, run_id)
        if current:
            now = time.time()
            cur = db.execute("UPDATE missions SET state=?, result=?, updated=? WHERE id=? AND state='running'",
                             (outcome, detail[:2000], now, mid))
            changed = cur.rowcount == 1
            if changed:
                db.execute("UPDATE runs SET ended=?, outcome=?, detail=? WHERE id=?", (now, outcome, detail[:2000], run_id))
    return _mission(mid, changed)


def park_for_approval(run_id: str, step: Dict, preview: str, checkpoint: Optional[Dict] = None) -> Dict:
    """A risky step: the mission waits for the owner's answer (owner's decision: until he answers; no answer is no).
    2026-11-10 C1: the approval is bound to that exact step by its fingerprint and can be used once (`used`)."""
    from core.pcledger import fingerprint
    changed = False
    with database() as db:
        mid, current = _current_run(db, run_id)
        if current:
            now = time.time()
            approval = {"step": step, "fp": fingerprint(step), "preview": preview[:1000], "asked": now, "answer": None, "used": None}
            cur = db.execute("UPDATE missions SET state='waiting_approval', approval=?, checkpoint=?, updated=? WHERE id=? AND state='running'",
                             (json.dumps(approval), json.dumps(checkpoint) if checkpoint is not None else None, now, mid))
            changed = cur.rowcount == 1
            if changed:
                db.execute("UPDATE runs SET ended=?, outcome='parked', detail=? WHERE id=?", (now, preview[:2000], run_id))
    return _mission(mid, changed)


def heartbeat(run_id: str, progress: str = "", checkpoint: Optional[Dict] = None) -> Dict:
    """The worker says it is still alive before each step. It learns whether to go on: the owner may have cancelled the
    mission or paused all missions (2026-11-10 C1: nothing told the PC before). A run that is no longer the mission's
    current one gets state 'ended', so a superseded worker stops.
    2026-11-10 review: it may carry the checkpoint (right after an approved step ran), so a crash after that point resumes
    from it instead of re-planning and re-asking for a step that already ran."""
    with database() as db:
        mid, current = _current_run(db, run_id)
        if current:
            db.execute("UPDATE runs SET progress=?, progress_at=? WHERE id=?", ((progress or "")[:500], time.time(), run_id))
            if checkpoint is not None:
                db.execute("UPDATE missions SET checkpoint=? WHERE id=? AND state='running'", (json.dumps(checkpoint), mid))
        state = db.execute("SELECT state FROM missions WHERE id=?", (mid,)).fetchone()["state"] if current else "ended"
    return {"state": state, "paused": paused()}


def release(run_id: str, checkpoint: Optional[Dict] = None) -> Dict:
    """The PC has to stop for now (a game started, he came back to the PC): the mission goes back to the queue with its
    checkpoint. Not a failure, so it does not count as a retry (2026-11-10 C1)."""
    changed = False
    with database() as db:
        mid, current = _current_run(db, run_id)
        if current:
            now = time.time()
            cur = db.execute("UPDATE missions SET state='queued', checkpoint=COALESCE(?, checkpoint), updated=? WHERE id=? AND state='running'",
                             (json.dumps(checkpoint) if checkpoint is not None else None, now, mid))
            changed = cur.rowcount == 1
            if changed:
                db.execute("UPDATE runs SET ended=?, outcome='yielded' WHERE id=?", (now, run_id))
    return _mission(mid, changed)


def consume_approval(mid: str) -> Optional[Dict]:
    """The approved step, exactly once: it is stamped `used`, so a retry or a recovered mission can never replay it
    (2026-11-10 C1). None when there is no unused approved step."""
    with database() as db:
        r = db.execute("SELECT approval FROM missions WHERE id=? AND uid=?", (mid, users.current())).fetchone()
        a = json.loads(r["approval"]) if r and r["approval"] else None
        if not a or a.get("answer") != "approved" or a.get("used"):
            return None
        a["used"] = time.time()
        db.execute("UPDATE missions SET approval=? WHERE id=?", (json.dumps(a), mid))
    return a


def consume_approval_for_run(run_id: str) -> Optional[Dict]:
    """The worker's side of consume_approval (C11): only for the mission's CURRENT run, so a superseded worker can never
    take the approved step. None when there is nothing to take."""
    with database() as db:
        mid, current = _current_run(db, run_id)
    return consume_approval(mid) if current else None


def set_result_image(mid: str, name: str) -> None:
    """2026-11-10 C3: the evidence picture's file name (in the person's data folder, missions/)."""
    with database() as db:
        db.execute("UPDATE missions SET result_image=? WHERE id=? AND uid=?", (name, mid, users.current()))


def recover(stale_s: float = 600) -> List[str]:
    return [r["id"] for r in _recover(stale_s)]


BACKUPS_KEPT = 7


def backup_dir() -> Path:
    """Where the daily copies go: JARVIS_MISSIONS_BACKUP_DIR, else data/backups/missions, which Syncthing carries to the other
    machine (the live database itself stays out of the sync on purpose). Cross-device audit 9: a wiped laptop lost the queue."""
    from core import storage
    return Path(os.getenv("JARVIS_MISSIONS_BACKUP_DIR") or (storage.DATA / "backups" / "missions"))


def backup_daily(keep: int = BACKUPS_KEPT, today: Optional[str] = None) -> Optional[Path]:
    """One consistent copy of missions.sqlite per day (sqlite's own online backup), the newest `keep` kept. Returns the new
    file, or None when today's copy exists already or there is no database yet. Never raises into the caller's loop."""
    source = _dir() / "missions.sqlite"
    if not source.exists():
        return None
    target_dir = backup_dir()
    target = target_dir / f"missions-{today or time.strftime('%Y%m%d')}.sqlite"
    if target.exists():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    src, dst = sqlite3.connect(source, timeout=10), sqlite3.connect(partial)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(partial, target)
    for old in sorted(target_dir.glob("missions-*.sqlite"))[:-max(1, keep)]:
        old.unlink(missing_ok=True)
    return target


def recover_and_report(stale_s: float = 600) -> List[str]:
    """recover(), and tell the owner (once) about each mission that just landed in 'unknown': nobody else will ask him.
    Only the running -> unknown transition notifies, and a mission leaves 'running' exactly once, so a later pass
    never repeats it. A first interruption is retried silently."""
    rows = _recover(stale_s)
    for r in rows:
        if r["state"] != "unknown":
            continue
        try:
            from core import notify
            notify.notify("Mission interrupted", f"{r['goal'][:80]}: it was interrupted twice and I have not retried it. Tell me what to do.",
                          r["uid"], "missions", urgent=True,
                          extra={"kind": "unknown", "mission": r["id"], "tag": "mission-" + r["id"], "url": "/#mission=" + r["id"]})
        except Exception as error:  # noqa: BLE001
            from core import diag
            diag.swallowed("mission interrupted notice", error)
    return [r["id"] for r in rows]


def _recover(stale_s: float) -> List[Dict]:
    """A mission still 'running' whose run has gone SILENT (no heartbeat for stale_s) was interrupted (a crash, a restart).
    It becomes 'unknown' - never replayed blindly. It is queued again ONCE, with a note to check what was already done
    first; after that it stays unknown. 2026-11-10 C1: by silence only - this runs every minute on the laptop, and a
    laptop restart must never re-queue a mission the PC is still working on. An approval keeps its `used` stamp."""
    changed: List[Dict] = []
    with database() as db:
        cutoff = time.time() - stale_s
        stale = db.execute("SELECT missions.id, missions.uid, missions.goal, missions.retries FROM missions JOIN runs ON runs.mission=missions.id"
                           " WHERE missions.state='running' AND runs.ended IS NULL AND COALESCE(runs.progress_at, runs.started) <= ?"
                           " GROUP BY missions.id", (cutoff,)).fetchall()
        for r in stale:
            now = time.time()
            db.execute("UPDATE runs SET ended=?, outcome='interrupted' WHERE mission=? AND ended IS NULL", (now, r["id"]))
            if (r["retries"] or 0) < MAX_RETRIES_AFTER_UNKNOWN:
                db.execute("UPDATE missions SET state='queued', retries=retries+1, updated=?, note=? WHERE id=?",
                           (now, "interrupted: check what was already done before doing anything again", r["id"]))
                state = "queued"
            else:
                db.execute("UPDATE missions SET state='unknown', updated=?, note=? WHERE id=?",
                           (now, "interrupted twice: not retried; ask the owner", r["id"]))
                state = "unknown"
            changed.append({"id": r["id"], "uid": r["uid"], "goal": r["goal"], "state": state})
    return changed


def runs(mid: str) -> List[Dict]:
    with database() as db:
        return [dict(r) for r in db.execute("SELECT * FROM runs WHERE mission=? ORDER BY started", (mid,)).fetchall()]


# ---- said aloud ---------------------------------------------------------------------------------------------------------
_SAY = {"queued": "queued", "running": "working on it", "waiting_approval": "waiting for your answer", "blocked": "blocked",
        "done": "done", "failed": "failed", "cancelled": "cancelled", "unknown": "interrupted, needs you"}


def spoken(m: Dict) -> str:
    return f"\"{m['goal'][:80]}\": {_SAY.get(m['state'], m['state'])}" + (f" ({m['note']})" if m.get("note") and m["state"] in ("blocked", "unknown") else "")
