"""JARVIS's universal learning core: ONE engine every skill plugs into (Blender, Python debugging, later PC control...).

A skill is an `Adapter` (tasks, actions, an independent judge). The core owns everything that makes learning honest:
  * episodes: every judged attempt is stored (core/dojo_store.py) with the judge's code hash, so results only compare
    when the judge did not change in between;
  * sealed exams: held-out tasks are sealed before training; a sealed task can never be trained on, studied or fed to a
    proposer, and a trained-on task can never be sealed (use `Engine.assert_trainable`);
  * hint tracking: an episode that used teacher-written hints is `assisted` and never counts as independent evidence;
  * the improvement rule (`better`): hardened against the three reward hacks found in the Blender dojo
    (tie-break "wins", a better ratio from added tests, passing by deleting the thing under test);
  * progress levels computed from evidence only (nobody can set "mastered"), and a capability report that keeps
    (a) reading (b) saved memory (c) parameter/search tuning (d) trained policy updates (e) model-weight updates apart.

Nothing in this module knows about any one skill: no Blender, no rigging, no Python-specific code."""
import abc
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from core.dojo_store import Store

SESSION = uuid.uuid4().hex[:8]                       # one id per process: "after a restart" is a different SESSION
LEVELS = ("none", "learned_about", "attempted", "practised", "evaluated", "independently_verified", "deployed", "mastered")
PRACTISED_MIN = 10
VERIFIED_MIN_TASKS, VERIFIED_MIN_RATE = 5, 0.7
MASTERED_MIN_TASKS, MASTERED_MIN_RATE = 10, 0.8


class Stopped(RuntimeError):
    """The owner's kill switch (or a cancel) is set."""


class Paused(RuntimeError):
    """A game is running or real work holds the machine: learning yields."""


class SealedError(RuntimeError):
    """A held-out task was about to be trained on / studied / proposed against, or an exam used an unsealed task."""


class JudgeChanged(RuntimeError):
    """The judge's code changed while an episode ran; the result is not comparable."""


class AdapterError(TypeError):
    """An adapter does not fill the contract."""


# ------------------------------------------------------------------ the improvement rule (shared by every skill)
def better(a, b):
    """A MEANINGFUL improvement of outcome `a` over `b`: a full pass newly reached, a stage fixed, or FEWER failing tests
    without fewer tests. A smaller tie-break is not an improvement; a better ratio from adding tests is not one; passing by
    deleting what is under test (total shrinks below 80%) is not one. Each of these was found as a real hack."""
    if a["full"] and not b["full"]:
        return True
    if a["stages_ok"] != b["stages_ok"]:
        return a["stages_ok"] > b["stages_ok"]
    if a.get("total") and b.get("total") and a["total"] < 0.8 * b["total"]:
        return False
    fa = 1 - a["fail_count"] / a["total"] if a.get("total") else 0.0
    fb = 1 - b["fail_count"] / b["total"] if b.get("total") else 0.0
    return a["fail_count"] < b["fail_count"] and fa >= fb - 0.002


def key_of(o):
    return [int(o["full"]), o["stages_ok"], round(1 - o["fail_count"] / o["total"], 4) if o.get("total") else 0.0]


def at_least_as_good(a, b):
    return key_of(a) >= key_of(b)


def file_hash(*paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).read_bytes())
    return h.hexdigest()[:16]


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ the contract
class Adapter(abc.ABC):
    """What a skill supplies. The core never reaches past this."""
    skill = ""
    TEACHER_SOURCES = ("teacher",)                    # hint sources that make an episode `assisted`

    @abc.abstractmethod
    def tasks(self, split):
        """-> [{"id": str, "digest": str(content hash of the task incl. its judge data), "label": str, ...}] for 'train' or 'holdout'.
        The two splits must be disjoint."""

    @abc.abstractmethod
    def actions(self):
        """-> [str] the permitted actions (strategies) an episode may take."""

    @abc.abstractmethod
    def features(self, task):
        """-> {name: float} what is observable about the task BEFORE acting (the policy's state)."""

    @abc.abstractmethod
    def execute(self, task, action, ctx):
        """Take `action` on `task` in a disposable workspace and return the judge's outcome:
        {full: bool, stages_ok: int, fail_count: int, total: int, cost_s: float, hints: [source labels], error?: str}.
        ctx: {purpose: train|drill|exam, closed_book: bool, cancel: threading.Event, store: Store}."""

    @abc.abstractmethod
    def judge_files(self):
        """-> [Path] the code of the judge; its hash is recorded in every episode."""

    def default_action(self):
        """The fixed, untuned baseline strategy (declared before any training); a learned policy must beat it on held-out tasks."""
        return self.actions()[0]

    def action_kind(self, action):
        return "strategy"                              # or "param" / "search" for the report's category (c)

    def validate(self):
        for t in ("train", "holdout"):
            rows = self.tasks(t)
            if not rows or any(not r.get("id") or not r.get("digest") for r in rows):
                raise AdapterError(f"{self.skill}: tasks({t!r}) must be non-empty with id and digest")
        a, b = {r["id"] for r in self.tasks("train")}, {r["id"] for r in self.tasks("holdout")}
        if a & b:
            raise AdapterError(f"{self.skill}: train and holdout tasks overlap")
        if not self.actions() or not self.judge_files():
            raise AdapterError(f"{self.skill}: needs actions and judge_files")
        return True


def default_gaming():
    try:
        from core import craftjobs
        from tools import pcops
        return bool(pcops.processes_matching(*craftjobs.GAME_PROCESSES))
    except Exception:
        return False


REQUIRED = ("full", "stages_ok", "fail_count", "total")


class Engine:
    """Runs episodes for one adapter against one store. The only place that writes episodes."""

    def __init__(self, adapter, store=None, gaming=default_gaming, cancel=None):
        self.adapter = adapter
        self.store = store or Store(adapter.skill)
        self.gaming = gaming
        self.cancel = cancel or threading.Event()
        self._tasks = {}

    # --- tasks
    def task(self, task_id):
        if not self._tasks:
            for split in ("train", "holdout"):
                for t in self.adapter.tasks(split):
                    self._tasks[t["id"]] = {**t, "split": split}
        return self._tasks[task_id]

    def judge_hash(self):
        return file_hash(*self.adapter.judge_files())

    def seal_holdout(self):
        """Seal every held-out task. Refuses (returns the offenders) if any was already trained on."""
        bad = []
        for t in self.adapter.tasks("holdout"):
            if not self.store.seal(t["id"], t["digest"]):
                bad.append(t["id"])
        return bad

    def assert_trainable(self, task_id):
        """Call before using a task for ANYTHING that can teach (training, a prompt, a proposer, a policy update)."""
        if task_id in self.store.sealed():
            raise SealedError(f"{task_id} is a sealed held-out task")

    # --- episodes
    def run(self, task_id, action, purpose="train", closed_book=False):
        if purpose not in ("train", "drill", "exam"):
            raise ValueError("purpose must be train, drill or exam")
        if self.cancel.is_set() or self.store.stop_requested():
            raise Stopped("STOP requested")
        if self.gaming():
            raise Paused("a game is running")
        t = self.task(task_id)
        sealed = task_id in self.store.sealed()
        if purpose == "exam" and not sealed:
            raise SealedError("exams may only use tasks sealed in advance")
        if purpose != "exam":
            self.assert_trainable(task_id)
        if action not in self.adapter.actions():
            raise ValueError(f"unknown action {action!r}")
        j0 = self.judge_hash()
        t0 = time.monotonic()
        out = self.adapter.execute(t, action, {"purpose": purpose, "closed_book": closed_book, "cancel": self.cancel, "store": self.store})
        if self.judge_hash() != j0:
            raise JudgeChanged("the judge changed during the episode")
        if any(k not in out for k in REQUIRED):
            raise AdapterError(f"{self.adapter.skill}: outcome lacks {[k for k in REQUIRED if k not in out]}")
        hints = list(out.get("hints") or [])
        row = {"skill": self.adapter.skill, "task": task_id, "split": t["split"], "purpose": purpose, "closed_book": closed_book,
               "action": action, "action_kind": self.adapter.action_kind(action), "judge": j0, "session": SESSION,
               "features": self.adapter.features(t), "hints": hints, "assisted": any(h in self.adapter.TEACHER_SOURCES for h in hints),
               "outcome": {**{k: out[k] for k in REQUIRED}, "cost_s": round(out.get("cost_s", time.monotonic() - t0), 3),
                           "error": out.get("error"), "detail": out.get("detail")}}
        return self.store.add_episode(row)

    @staticmethod
    def comparable(a, b):
        return a.get("judge") == b.get("judge")

    def training_episodes(self):
        """Only these may update a policy or feed a proposer: never exam rows, never rows of a sealed task, never a stale judge."""
        sealed, head = self.store.sealed(), self.judge_hash()
        return [e for e in self.store.episodes() if e["purpose"] in ("train", "drill") and e["task"] not in sealed and e["judge"] == head]

    # --- honest reporting
    def progress(self, via=None):
        """`via`: {task id: action} the skill is judged on (e.g. a learned policy's choices); default = the adapter's fixed default action only,
        so a pile of exam rows from weaker or stronger side strategies cannot move the level."""
        s, eps = self.store, self.store.episodes()
        default = self.adapter.default_action()
        exams = [e for e in eps if e["purpose"] == "exam" and not e["assisted"] and e["judge"] == self.judge_hash()
                 and e["action"] == (via or {}).get(e["task"], default)]
        by_task = {}
        for e in exams:
            by_task.setdefault(e["task"], []).append(e["outcome"]["full"])
        solved = sum(1 for v in by_task.values() if sum(v) / len(v) >= 0.5)
        rate = solved / len(by_task) if by_task else 0.0
        trained = [e for e in eps if e["purpose"] in ("train", "drill")]
        deployed = any(ev.get("kind") == "owner_approved" for ev in s.events(limit=1000))
        sessions = {e["session"] for e in exams}
        level = "none"
        if s.lessons() or s.cards() or s.events(kind="lesson") or s.events(kind="research"):
            level = "learned_about"
        if eps:
            level = "attempted"
        if len(trained) >= PRACTISED_MIN:
            level = "practised"
        if exams:
            level = "evaluated"
        if len(by_task) >= VERIFIED_MIN_TASKS and rate >= VERIFIED_MIN_RATE:
            level = "independently_verified"
            if deployed:
                level = "deployed"
            if len(by_task) >= MASTERED_MIN_TASKS and rate >= MASTERED_MIN_RATE and len(sessions) >= 2 and deployed:
                level = "mastered"
        return {"skill": self.adapter.skill, "level": level, "exam_tasks": len(by_task), "exam_solved": solved, "exam_rate": round(rate, 3),
                "train_episodes": len(trained), "exam_sessions": len(sessions), "owner_approved": deployed,
                "note": "computed from stored episodes only; 'deployed' needs an owner_approved event; 'mastered' needs deployed + >=2 sessions"}

    def capability_report(self):
        s = self.store
        eps = s.episodes()
        pol_dir = s.root / "policies"
        pols = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(pol_dir.glob("*.json"))] if pol_dir.is_dir() else []
        w_dir = s.root / "weights"
        return {
            "a_reading": {"studied_lessons": sum(1 for l in s.lessons() if l.get("kind") == "academy"), "research_cards": len(s.cards()), "research_events": len(s.events(kind="research"))},
            "b_saved_memory": {"own_lessons": sum(1 for l in s.lessons() if l.get("kind") != "academy"), "playbook": len(s.playbook()), "episodes": len(eps)},
            "c_tuning_or_search": {"episodes": sum(1 for e in eps if e["action_kind"] in ("param", "search"))},
            "d_trained_policy": {"versions": len(pols), "with_updates": sum(1 for p in pols if p.get("updates", 0) > 0),
                                 "evaluated_on_holdout": sum(1 for p in pols if p.get("evaluation"))},
            "e_model_weights": {"checkpoints": len(list(w_dir.glob("*/evaluation.json"))) if w_dir.is_dir() else 0},
            "note": "d counts only policies updated from judged rewards; e counts only versioned weight checkpoints with held-out evaluation. Neither is implied by a, b or c.",
        }
