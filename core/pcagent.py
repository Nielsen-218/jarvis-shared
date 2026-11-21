"""Plan -> act -> observe -> recover, bounded, with the owner's approvals preserved.

pchybrid used to run a plan top to bottom and stop at the first unverified step ("do not improvise"). Now a failed step is a
reason to look at the PC again and ask the local planner for the REMAINING steps another way, at most MAX_RECOVERIES times
per task. What a recovery may do on its own is narrower than what the owner reviewed:

  * only non-sensitive tools (pcactions.SCHEMAS), unless that exact step was already in the reviewed plan;
  * no app, process or site that neither the task nor the reviewed plan named;
  * never the step that just failed again unless something different runs first (fix the cause, then retry), never after a
    cancel, a permission block or a user take-over.

A recovery that needs more than that does not run: it comes back as a plan for the owner to review, like any other.
Every attempt, failed or not, stays in the evidence, so the report says what really happened.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable, Dict, List, Optional

from core import pcactions

MAX_RECOVERIES = 2
# Reasons no other method can fix: the owner stepped in, or the step reported a FACT about the PC rather than a method that
# failed ("nothing is playing"). Re-planning those cost a 2-4 s planner call for the same answer (live 2026-11-08).
_UNRECOVERABLE = re.compile(r"\b(?:took over|stop requested|cancel+ed|approval|needs your)\b"
                            r"|nothing is registered with windows as playable media|doesn't let windows move its position"
                            r"|has no (?:next|previous) item", re.I)
_NAME_ARGS = ("app", "process", "site")

# A command turn about ONE app ("close Notepad", "bring Paint forward") has a wall-clock budget for the whole turn: its steps,
# its re-plans and what they run. Past it Jarvis says plainly what happened and stops (live 2026-11-13: a close that failed
# turned into a 62 s turn of planner calls and a model-driven click loop that ended in three contradictory sentences).
SIMPLE_VERBS = frozenset({"open_app", "focus_app", "close_app", "minimize_app", "minimize_foreground", "close_foreground"})
SIMPLE_TURN_S = 12.0
OUT_OF_TIME = "I ran out of time on that, so I stopped."
# ... but a launch that was requested and not yet seen is not "stopped": it may well still be starting.
OUT_OF_TIME_LAUNCHING = "I ran out of time waiting for it, so I stopped checking; it may still be starting."
_LAUNCH_PENDING = re.compile(r"launch was requested|not verified (?:open )?yet|no visible application window was verified", re.I)
# What a failed close may NOT be handed to: the open-ended click loop. Closing is one verb; the only reason to look inside the
# app is a dialog that really blocks it (a save prompt), and that case is answered by the close itself (needs_owner).
_OPEN_ENDED = frozenset({"use_app"})
_CLOSERS = frozenset({"close_app", "close_foreground"})
# The turn's clock bounds the ORIGINAL plan's own app verbs. A step a re-plan added keeps its own ceiling instead, so it is only
# started when enough of the turn is left to be worth starting (never started and then cut after a second or two).
REPLAN_START_S = 3.0
_scope = threading.local()


def replanned_step() -> bool:
    """Is the step being executed on this thread one a re-plan added (not one of the plan the turn started with)?"""
    return bool(getattr(_scope, "replanned", False))


def turn_budget(plan: dict) -> Optional[float]:
    """Seconds the whole turn may take: SIMPLE_TURN_S (JARVIS_SIMPLE_TURN_S) for a plan of one or two plain app verbs, else None."""
    steps = (plan or {}).get("steps") or []
    if not steps or len(steps) > 2 or any(s.get("tool") not in SIMPLE_VERBS for s in steps):
        return None
    if _closes_a_game(steps):
        return None        # a game gets GAME_QUIT_S to quit when asked, then is ended (owner 2026-11-07): a 12 s turn cut that off
    try:
        return max(1.0, float(os.getenv("JARVIS_SIMPLE_TURN_S", SIMPLE_TURN_S)))
    except ValueError:
        return SIMPLE_TURN_S


def _closes_a_game(steps: list) -> bool:
    """Does a close step in this plan close a game (a named game, or the window in front when that is one)? Unreadable: no."""
    try:
        from tools import apps
        for step in steps:
            tool = step.get("tool")
            if tool == "close_app":
                canonical = apps.resolve_app(str((step.get("arguments") or {}).get("app", "")))
                if canonical and (apps.game_processes(canonical) or apps.is_game(str(apps._installed().get(canonical, "")))):
                    return True
            elif tool == "close_foreground":
                from tools import pcops
                front = next((w for w in pcops.windows() if w.get("foreground")), None)
                if front and apps.game_of_window(front.get("hwnd")):
                    return True
    except Exception:  # noqa: BLE001 - a check that cannot run leaves the budget on
        return False
    return False


def _named(plan: dict) -> set:
    names = set()
    for step in plan.get("steps", []):
        for key in _NAME_ARGS:
            value = step.get("arguments", {}).get(key)
            if isinstance(value, str) and value.strip():
                names.add(value.strip().casefold())
    return names


def allowed(step: dict, task: str, reviewed: dict) -> bool:
    """May this recovery step run under the approval the owner already gave for `reviewed`?"""
    tool, args = step.get("tool"), step.get("arguments", {})
    if tool not in pcactions.SCHEMAS:
        return False
    if any(s.get("tool") == tool and s.get("arguments") == args for s in reviewed.get("steps", [])):
        return True
    if pcactions.SCHEMAS[tool][1]:
        return False
    low, names = task.casefold(), _named(reviewed)
    for key in _NAME_ARGS:
        value = args.get(key)
        if isinstance(value, str) and value.strip() and not _mentioned(value.strip().casefold().removesuffix(".exe"), low, names):
            return False
    return True


def _mentioned(value: str, task: str, names: set) -> bool:
    """The same thing the owner or the reviewed plan named, in another spelling: "paint" covers "mspaint" (and "mspaint.exe"),
    "chrome" covers "google chrome"; "photoshop" covers nothing the owner did not say."""
    if value in task:
        return True
    return any(len(n) >= 4 and (n in value or value in n) for n in names)


def _same(a: dict, b: dict) -> bool:
    return a.get("tool") == b.get("tool") and a.get("arguments") == b.get("arguments")


def _fill(step: dict, found: Optional[str]) -> dict:
    """A later step written against a path not known at planning time ("<found>") gets the path an observation produced."""
    if not found:
        return step
    args = {k: (v.replace("<found>", found) if isinstance(v, str) and "<found>" in v else v) for k, v in (step.get("arguments") or {}).items()}
    return dict(step, arguments=args)


def _step_words(step: dict) -> str:
    """A step the way a person says it ('closing ChatGPT', 'typing that into Claude'), for the progress report, which is
    said aloud and kept in the reply: never the text typed, the command run or a path (review 2026-11-10)."""
    try:
        from core import gateway
        return gateway.step_doing(step)
    except Exception:  # noqa: BLE001
        return "working on that"


def _ask_first(blocked) -> str:
    """'To finish, I'd bring Edge to the front. Should I?': the extra step in plain words, not tool names."""
    try:
        from core.pchybrid import spoken_plan
        what = spoken_plan({"steps": blocked})
    except Exception:  # noqa: BLE001
        what = ""
    return f"To finish, I'd {what}. Should I?" if what else "To finish, I need one more step. Should I?"


def pursue(task: str, plan: dict, *, execute: Callable, replan: Callable, observe: Callable[[], str] = lambda: "",
           approved: bool = False, cancelled: Callable[[], bool] = lambda: False, execution_id: Optional[str] = None,
           max_recoveries: int = MAX_RECOVERIES, progress: Callable[[str], None] = lambda text: None,
           allowed_fn: Optional[Callable] = None, reauthorize: Optional[Callable[[dict], bool]] = None,
           deadline: Optional[float] = None, settle: Optional[Callable[[dict], Optional[dict]]] = None,
           dialog_open: Optional[Callable[[dict], bool]] = None) -> Dict:
    """Run `plan` for `task`.

    execute(step, approved=, cancelled=, execution_id=) -> {"verified": bool, "detail": str, ...} (pcmethods.execute).
    replan(task, done_steps, failed_step, reason, pc_state_text) -> a typed plan with the remaining steps, or None.
    allowed_fn(step, task, reviewed_plan, observed_names) -> may this recovery step run unasked (default: `allowed`).
    deadline: a time.monotonic() instant after which no step or re-plan starts (the turn says so and stops). It bounds the plan's
    own steps; a step a re-plan added is started only with REPLAN_START_S left and then keeps its own ceiling (pcmethods).
    settle(step) -> a verified outcome when the PC already shows the failed step's goal reached (the window did go, a moment
    late), else None: checked before any re-plan, so a late success is a success, not a failure with a second try.
    dialog_open(step) -> is a dialog really blocking this step (default: none): only then may a failed close be followed by
    an open-ended step (use_app).
    Returns {"state", "evidence", "plan" (the steps that actually ran), "recoveries", "observed", ["proposed", "detail",
    "choices"]} where state is completed | stopped | cancelled | needs_review (a recovery wants more than was approved;
    `proposed` is that plan) | needs_choice (an observation found several things the owner must choose between).
    """
    steps: List[dict] = list(plan["steps"])
    done: List[dict] = []
    ran: List[dict] = []
    evidence: List[dict] = []
    recoveries: List[dict] = []
    observed: List[str] = []           # names an observation produced (a found path): later steps may act on them
    found: Optional[str] = None
    index, state, stop_reason = 0, "completed", ""
    pending = None                     # (the steps before its re-plan, the failed step's index, the failed step): re-checked once if time runs out
    original = list(steps)             # what the turn started with: only these are cut by the turn's clock
    original_ids = {id(s) for s in original}
    started: set = set()               # steps (by identity) already run once: running one again is a re-plan's doing, whatever it says

    def out_of_time() -> str:
        last = next((str(e.get("detail", "")) for e in reversed(evidence) if not e.get("meta") and e.get("detail")), "")
        return OUT_OF_TIME_LAUNCHING if _LAUNCH_PENDING.search(last) else OUT_OF_TIME

    def late_success() -> bool:
        """Every out-of-time exit looks at the PC once more first: the launch or the close the failed step asked for may have
        finished while a re-plan or a wait went by. True when the goal turned out reached (the run goes on after that step)."""
        nonlocal steps, index, pending
        if pending is None or settle is None:
            return False
        try:
            late = settle(pending[2])
        except Exception:  # noqa: BLE001 - a check that cannot run settles nothing
            late = None
        if not (late and late.get("verified")):
            return False
        evidence.append({"step": len(ran), **{k: v for k, v in late.items() if k != "screen"}})
        done.append(pending[2])
        steps, index, pending = pending[0], pending[1] + 1, None
        return True

    while index < len(steps):
        step = _fill(steps[index], found)
        if reauthorize is not None and step is not steps[index] and not reauthorize(step):
            # The plan was authorized while this step said '<found>'; filled in, it needs more (a found .exe would RUN).
            return {"state": "needs_review", "evidence": evidence, "plan": {"steps": ran}, "recoveries": recoveries,
                    "observed": observed, "proposed": {"steps": [step] + steps[index + 1:]},
                    "detail": f"I found {found}. Opening it runs a program, so it needs your OK."}
        replanned = id(steps[index]) not in original_ids or id(steps[index]) in started
        if deadline is not None and (time.monotonic() >= deadline or (replanned and deadline - time.monotonic() < REPLAN_START_S)):
            if late_success():
                continue
            pending = None
            evidence.append({"verified": False, "detail": out_of_time(), "error_kind": "out_of_time", "meta": True})
            state = "stopped"
            break
        try:
            if cancelled():
                raise InterruptedError("Stop requested before the next step.")
            if "before" in step and not pcactions.condition(step["before"]):
                raise RuntimeError("A precondition changed. Stop and review the current PC state.")
            pending = None                       # the re-plan's own step is running: the earlier failure is no longer the one to re-check
            progress(f"step {len(ran) + 1} of {len(steps)}, {_step_words(step)}")   # said by "what's the hold up?"
            _scope.replanned = replanned
            started.add(id(steps[index]))
            output = execute(step, approved=approved, cancelled=cancelled, execution_id=execution_id)
        except InterruptedError as error:
            evidence.append({"verified": False, "detail": str(error)})
            state = "cancelled"
            break
        except Exception as error:  # noqa: BLE001 - a typed action's own failure is a reason to re-plan, not a crash
            output = {"verified": False, "detail": str(error)}
        finally:
            _scope.replanned = False
        ran.append(step)
        evidence.append({"step": len(ran), **{k: v for k, v in output.items() if k != "screen"}})
        if output.get("verified"):
            done.append(step)
            if isinstance(output.get("found"), str) and output["found"]:
                found = output["found"]
                observed.append(found)
            for key in ("window", "path", "folder"):
                if isinstance(output.get(key), str) and output[key]:
                    observed.append(output[key])
            index += 1
            continue
        reason = str(output.get("detail", ""))
        looked = False                     # did the PC just get looked at for this step's goal (so an exit below need not look again)
        if settle is not None and output.get("error_kind") not in ("needs_owner", "blocked", "user_took_over"):
            looked = True
            try:
                settled = settle(step)
            except Exception:  # noqa: BLE001 - a check that cannot run settles nothing
                settled = None
            if settled and settled.get("verified"):
                evidence.append({"step": len(ran), **{k: v for k, v in settled.items() if k != "screen"}})
                done.append(step)
                index += 1
                continue
        if output.get("error_kind") == "needs_choice":
            return {"state": "needs_choice", "evidence": evidence, "plan": {"steps": ran}, "recoveries": recoveries, "observed": observed,
                    "choices": list(output.get("choices") or []), "remaining": steps[index + 1:], "detail": reason}
        if (len(recoveries) >= max_recoveries or cancelled() or _UNRECOVERABLE.search(reason)
                or output.get("error_kind") in ("blocked", "user_took_over", "needs_owner")):
            state = "stopped"
            # Stopped on purpose or to ask him something (unsaved changes, a guard, he took over, he said stop): not a failure
            # the learning loop should study (core.misses).
            if output.get("error_kind") in ("blocked", "user_took_over", "needs_owner"):
                stop_reason = str(output["error_kind"])
            elif cancelled():
                stop_reason = "cancelled"
            break
        pending = (steps, index, step)
        if deadline is not None and time.monotonic() >= deadline:
            if not looked and late_success():
                continue
            pending = None
            evidence.append({"verified": False, "detail": out_of_time(), "error_kind": "out_of_time", "meta": True})
            state = "stopped"
            break
        progress("that did not work; looking at the PC again and re-planning the rest")
        try:
            new = replan(task, list(done), step, reason, observe())
        except Exception:  # noqa: BLE001
            new = None
        if not isinstance(new, dict) or not new.get("steps"):
            if not any(not e.get("verified") and e.get("detail") for e in evidence):   # a reason was already said
                evidence.append({"verified": False, "detail": "I could not find another way to finish that."})
            state = "stopped"
            break
        try:
            pcactions.validate(new)
        except (ValueError, TypeError):
            evidence.append({"verified": False, "detail": "The alternative plan failed validation, so nothing more ran.", "meta": True})
            state = "stopped"
            break
        remaining = [s for s in new["steps"] if not any(_same(s, d) for d in done)]
        if step.get("tool") in _CLOSERS and not (dialog_open is not None and dialog_open(step)):
            # A close that failed is not a job for the open-ended click loop: it only looks inside the app when a dialog is
            # really in the way. What is left is the close's own reason, said as it stands.
            remaining = [s for s in remaining if s.get("tool") not in _OPEN_ENDED]
            if not remaining or all(_same(s, step) for s in remaining):
                state = "stopped"
                break
        # Repeating the failed step FIRST is no recovery (nothing changed); the same step AFTER a different one (restore the
        # minimized window, then type) is fixing the cause and trying again, still bounded by max_recoveries.
        while remaining and _same(remaining[0], step):
            remaining.pop(0)
        if not remaining or all(_same(s, step) for s in remaining):
            evidence.append({"verified": False, "detail": "The only idea was to repeat what just failed; I stopped instead.", "meta": True})
            state = "stopped"
            break
        blocked = [s for s in remaining if not (allowed_fn(s, task, plan, observed) if allowed_fn else allowed(s, task, plan))]
        recoveries.append({"after_step": len(ran), "reason": reason[:200], "steps": remaining, "needs_review": bool(blocked)})
        if blocked:
            return {"state": "needs_review", "evidence": evidence, "plan": {"steps": ran}, "recoveries": recoveries, "observed": observed,
                    "proposed": {"steps": remaining},
                    "detail": _ask_first(blocked)}
        steps = steps[:index] + remaining          # the failed step and everything after it give way to the new remaining steps
    return {"state": state, "evidence": evidence, "plan": {"steps": ran}, "recoveries": recoveries, "observed": observed,
            "stop_reason": stop_reason}
