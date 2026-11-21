"""A bounded learning budget per skill and per STRATEGY, so JARVIS stops repeating near-identical attempts.

A strategy is one way of attacking a skill (which capabilities are offered, how the learner is prompted). Each has hard limits;
when it stalls, its best work is preserved and the loop moves on: next strategy for this skill, or a different task.

    state file: data/learning/budget/<skill>.json  {"strategies": {name: {...}}, "history": [...]}
"""
import json
import os
import time
from pathlib import Path

LIMITS = {
    "rounds": 6,               # design->practice rounds per strategy (all cycles together)
    "stall_rounds": 2,         # rounds in a row without a better best score -> stalled
    "minutes": 75,             # wall-clock minutes per strategy (all cycles together)
    "duplicates": 4,           # near-identical candidates refused before the strategy counts as stalled
    "teacher_lessons": 2,      # lessons per strategy (each still passes core/teacher's credit gate and per-skill cap)
}


def _root() -> Path:
    from core import storage
    return Path(os.getenv("JARVIS_BUDGET_DIR", str(storage.DATA / "learning" / "budget")))


def _file(skill) -> Path:
    return _root() / f"{skill}.json"


def load(skill) -> dict:
    try:
        return json.loads(_file(skill).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"skill": skill, "strategies": {}, "history": []}


def save(state) -> None:
    f = _file(state["skill"])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")


def strategy(state, name) -> dict:
    return state["strategies"].setdefault(name, {"status": "active", "rounds": 0, "minutes": 0.0, "duplicates": 0, "lessons": 0,
                                                 "best": -1, "best_of": 0, "since_better": 0, "best_ref": "", "started": time.time()})


def note(state, name, event, **kw) -> None:
    state["history"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "strategy": name, "event": event, **kw})


def record_round(state, name, score, total, minutes, ref="") -> None:
    s = strategy(state, name)
    s["rounds"] += 1
    s["minutes"] = round(s["minutes"] + minutes, 1)
    frac = score / total if total else 0
    if frac > (s["best"] / s["best_of"] if s["best_of"] else -1):
        s.update(best=score, best_of=total, since_better=0, best_ref=ref)
    else:
        s["since_better"] += 1
    note(state, name, "round", score=f"{score}/{total}", ref=ref)


def verdict(state, name, limits=LIMITS) -> tuple:
    """-> ("continue", "") or ("stalled", why). A stalled strategy is marked so and never resumed automatically."""
    s = strategy(state, name)
    if s["status"] != "active":
        return "stalled", f"strategy {name} is {s['status']}"
    from core.words import plural
    why = ""
    if s["rounds"] >= limits["rounds"]:
        why = f"{plural(s['rounds'], 'round')} used (limit {limits['rounds']})"
    elif s["since_better"] >= limits["stall_rounds"]:
        why = f"no better score for {plural(s['since_better'], 'round')} (best {s['best']}/{s['best_of']})"
    elif s["minutes"] >= limits["minutes"]:
        why = f"{plural(round(s['minutes']), 'minute')} used (limit {limits['minutes']})"
    elif s["duplicates"] >= limits["duplicates"]:
        why = f"{plural(s['duplicates'], 'near-identical candidate')} refused"
    if why:
        s["status"] = "stalled"
        s["stalled_because"] = why
        note(state, name, "stalled", why=why, best=f"{s['best']}/{s['best_of']}", best_ref=s["best_ref"])
        return "stalled", why
    return "continue", ""


def next_step(state, strategies, other_tasks=()) -> tuple:
    """After a stall: ("strategy", name) for the next untried strategy, ("task", id) for another useful task, or ("stop", why)."""
    for n in strategies:
        if strategy(state, n)["status"] == "active":
            return "strategy", n
    for t in other_tasks:
        return "task", t
    return "stop", "every strategy for this skill has stalled and no other task is ready: waiting for a developer or the owner"
