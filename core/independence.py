"""INDEPENDENCE: is Jarvis doing his work himself, with local models, after being taught once?

Two things live here, both plain data (no model, no network):

  * the USE LOG (data/learning/learned-uses.jsonl): one row every time a learned skill, method or school playbook runs:
    who asked (owner | lab), whether it passed its independent check, and whether a teacher or ANY outside call was part
    of that run. A learned thing runs from disk with local code only, so both are recorded as false/0 by the runners; the
    fields exist so the report can prove it instead of assuming it.
  * the REPORT: skills learned (by himself, with the local teacher, with the paid teacher), the share of recent PC tasks
    done with zero outside calls, second-attempt success after one lesson, and what the paid teacher cost this month.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

USES = "learning/learned-uses.jsonl"
REPORT = "learning/independence.json"
RECENT_DAYS = 14


def asker() -> str:
    """Who is asking for this run: the lab's practice/retest process, or the owner's real request."""
    return "lab" if os.getenv("JARVIS_SKILL_LAB") == "1" else "owner"


def log_use(skill: str, kind: str, passed: bool, *, tool: str = "", source: Optional[str] = None, teacher: bool = False,
            outside_calls: int = 0, at: Optional[float] = None) -> None:
    """Never raises: a logging problem must not break the owner's step."""
    try:
        from core import storage
        path = storage.DATA / USES
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": time.time() if at is None else at, "skill": skill, "kind": kind, "tool": tool, "passed": bool(passed),
               "source": source or asker(), "teacher": bool(teacher), "outside_calls": int(outside_calls)}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001
        pass


def uses() -> list:
    from core import storage
    try:
        return [json.loads(l) for l in (storage.DATA / USES).read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def _taught_by(man) -> str:
    tiers = {l.get("tier") for l in man.get("teacher_lessons") or []}
    if "cloud" in tiers:
        return "paid"
    return "local" if tiers or man.get("provenance") == "teacher_assisted" else "self"


def _learned() -> list:
    """Every learned skill/method in both stores: [{"id", "taught_by", "approved"}]."""
    out = []
    try:
        from core import acquire
        for m in acquire.learned_methods():
            out.append({"id": m["id"], "taught_by": _taught_by(m), "approved": acquire.approved(m)})
    except Exception:  # noqa: BLE001
        pass
    try:
        from core import skillspec
        lib = skillspec.library()
        for sid in lib.ids() if lib.root.exists() else []:
            got = lib.get(sid) if sid.startswith("skill.") else None
            if got:
                out.append({"id": sid, "taught_by": _taught_by(got[0]), "approved": bool(skillspec.approved_skill(sid))})
    except Exception:  # noqa: BLE001
        pass
    return out


def _playbooks() -> dict:
    """The school's playbooks by who taught them (their `source` names the model): local vs paid."""
    counts = {"local": 0, "paid": 0}
    try:
        from tools import pcskills
        for b in pcskills.skills().playbooks.all():
            src = str(getattr(b, "source", "") or "").casefold()
            if getattr(b, "stale", False) or not src:
                continue
            counts["paid" if any(w in src for w in ("claude", "gemini", "gpt-", "openai", "api")) else "local"] += 1
    except Exception:  # noqa: BLE001
        pass
    return counts


def _lessons() -> list:
    """Every teacher lesson on disk (skills and miss lessons)."""
    from core import teacher
    out = []
    try:
        root = teacher.lessons_dir("x").parent
        for f in sorted(root.glob("*/*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    except Exception:  # noqa: BLE001
        pass
    return out


PLANNED_AFTER = 300.0     # seconds: a run this soon after a local planning failure is the same request, planned elsewhere


def _pc_tasks(since: float) -> tuple:
    """(tasks done with no outside call, tasks that needed one, tasks that could not be planned, PC steps run, tasks that stopped
    before finishing) since `since`. A TASK is one run record in the PC ledger (one request carried out), not one step: the usage
    table holds a row per step. A request the local planner gave up on never reaches a run, so its local_planning_failure row is a
    task in its own right, UNLESS a run followed it at once (the cloud planner took the same request: one request, counted once, as
    the run) or the planning was cancelled (he said stop: not an unplannable task). A run that stopped is not a finished task:
    it is counted apart, never as done with no outside call. A cancelled run is left out."""
    try:
        from core import pcledger, users
        local = outside = stopped = 0
        run_times = []
        with pcledger.database() as db:
            for r in db.execute("SELECT data, state, created FROM records WHERE uid=? AND kind='run' AND created>=?", (users.current(), since)):
                try:
                    data = json.loads(r["data"])
                except ValueError:
                    continue
                state = str(data.get("state") or r["state"] or "")
                run_times.append(float(r["created"] or 0))
                if state == "stopped":
                    stopped += 1
                elif state != "completed":
                    continue
                elif data.get("route") == "cloud_ai" or float(data.get("cloud_usd") or 0) > 0:
                    outside += 1
                else:
                    local += 1
            failures = []
            steps = 0
            for r in db.execute("SELECT component, data, created FROM usage WHERE uid=? AND created>=? AND component IN "
                                "('local_planning_failure','local_automation')", (users.current(), since)):
                if r["component"] == "local_automation":
                    steps += 1
                    continue
                try:
                    detail = json.loads(r["data"])
                except ValueError:
                    detail = {}
                if detail.get("status") == "cancelled" or detail.get("code") == "cancelled":
                    continue
                failures.append(float(r["created"] or 0))
    except Exception:  # noqa: BLE001
        return 0, 0, 0, 0, 0
    unplanned = sum(1 for t in failures if not any(t <= run <= t + PLANNED_AFTER for run in run_times))
    return local, outside, unplanned, steps, stopped


def _creole_corrections() -> int:
    """Creole phrases he corrected and Jarvis keeps (core/kreyol_bank): lessons taught by him, used with no outside call."""
    try:
        from core import kreyol_bank
        return kreyol_bank.count()
    except Exception:  # noqa: BLE001
        return 0


def report(now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    since = now - RECENT_DAYS * 86400
    learned, books, lessons = _learned(), _playbooks(), _lessons()
    by = {k: sum(1 for s in learned if s["taught_by"] == k) for k in ("self", "local", "paid")}
    recent = [u for u in uses() if u.get("at", 0) >= since]
    owner = [u for u in recent if u.get("source") == "owner"]
    local_tasks, outside_tasks, failed_tasks, steps, stopped_tasks = _pc_tasks(since)
    outside_tasks += sum(1 for u in owner if u.get("outside_calls") or u.get("teacher"))
    total = local_tasks + outside_tasks + failed_tasks + stopped_tasks
    # Second attempt after ONE lesson: of the things that got a lesson, how many were then done (learned / understood).
    given = {}
    for l in lessons:
        if l.get("status") == "given":
            given.setdefault(l.get("weakness"), []).append(l)
    won = sum(1 for ls in given.values() if any(l.get("outcome") == "led_to_learned" for l in ls))
    after_one = sum(1 for ls in given.values() if len(ls) == 1 and ls[0].get("outcome") == "led_to_learned")
    month = time.strftime("%Y-%m", time.localtime(now))
    paid = [l for l in lessons if l.get("tier") == "cloud" and l.get("status") == "given"
            and time.strftime("%Y-%m", time.localtime(float(l.get("at") or 0))) == month]
    return {
        "at": now,
        "skills": {"total": len(learned), "active": sum(1 for s in learned if s["approved"]), "by_himself": by["self"],
                   "local_teacher": by["local"], "paid_teacher": by["paid"]},
        "playbooks": books,
        "pc_tasks": {"days": RECENT_DAYS, "local": local_tasks, "outside": outside_tasks, "unplanned": failed_tasks, "stopped": stopped_tasks, "steps": steps,
                     "local_share": round(local_tasks / total, 3) if total else None},
        "learned_uses": {"days": RECENT_DAYS, "owner": len(owner), "owner_passed": sum(1 for u in owner if u.get("passed")),
                         "lab": len(recent) - len(owner),
                         "with_outside_calls": sum(1 for u in recent if u.get("outside_calls") or u.get("teacher"))},
        "lessons": {"taught": len(given), "worked": won, "worked_after_one": after_one,
                    "second_attempt_success": round(won / len(given), 3) if given else None},
        "paid_teacher": {"month": month, "calls": len(paid), "cost_usd": round(sum(float(l.get("cost_usd") or 0) for l in paid), 4)},
        "language": {"creole_corrections": _creole_corrections()},
    }


def save(now: Optional[float] = None) -> dict:
    """Write the report where the dashboard reads its data (data/learning/independence.json)."""
    from core import storage
    data = report(now)
    path = storage.DATA / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def spoken(data: Optional[dict] = None) -> str:
    """The report as he would say it: short, honest, numbers only where there is something to count."""
    from core.words import plural
    d = report() if data is None else data
    s, t, u, les, paid = d["skills"], d["pc_tasks"], d["learned_uses"], d["lessons"], d["paid_teacher"]
    parts = []
    if s["total"]:
        how = [f"{s['by_himself']} on my own" if s["by_himself"] else "", f"{s['local_teacher']} with my local teacher" if s["local_teacher"] else "",
               f"{s['paid_teacher']} with the paid teacher" if s["paid_teacher"] else ""]
        parts.append(f"I have learned {plural(s['total'], 'skill')} in my lab: " + ", ".join(h for h in how if h)
                     + f"; {s['active']} {'is' if s['active'] == 1 else 'are'} active")
    else:
        parts.append("I have not learned a skill in my lab yet")
    books = d.get("playbooks") or {}
    if books.get("local") or books.get("paid"):
        parts.append(f"From school I hold {plural(books['local'] + books['paid'], 'playbook')}, {books['local']} taught locally and {books['paid']} by a paid model")
    if t["local_share"] is None:
        parts.append(f"I have no PC tasks on record for the last {plural(t['days'], 'day')}")
    else:
        n = t["local"] + t["outside"] + t.get("unplanned", 0) + t.get("stopped", 0)
        line = (f"In the last {plural(t['days'], 'day')} {round(t['local_share'] * 100)} percent of {plural(n, 'PC task')} "
                "ran with no outside call")
        if t.get("unplanned"):
            line += f", and {t['unplanned']} could not be planned"
        if t.get("stopped"):
            line += f", and {t['stopped']} stopped before they finished"
        if t.get("steps"):
            line += f" ({plural(t['steps'], 'PC step')} in all)"
        parts.append(line)
    if u["owner"]:
        parts.append(f"You used what I learned {plural(u['owner'], 'time')}, and it worked {plural(u['owner_passed'], 'time')}")
    else:
        parts.append("Nothing I learned has been used on a real request yet")
    if les["taught"]:
        parts.append(f"Of {plural(les['taught'], 'thing')} I was taught, {les['worked']} worked afterwards")
    fixed = (d.get("language") or {}).get("creole_corrections", 0)
    if fixed:
        parts.append(f"You corrected my Creole {plural(fixed, 'time')}, and I keep every one")
    parts.append(f"The paid teacher was called {plural(paid['calls'], 'time')} this month, costing ${paid['cost_usd']:.2f}")
    return ". ".join(parts) + "."
