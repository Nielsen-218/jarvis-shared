"""Bounded local recovery. All alternatives implement the SAME typed operation.

No raw clicks, shell commands, writes, installs or paid retries. The one kind of self-written method allowed is a LEARNED method
(core/acquire): restricted core.methodlang code, passed by the host's independent judge on held-out lab worlds, and approved by the
owner for its exact code hash. Nothing else model-generated ever runs here.
Unverified results stop immediately: an uncertain action is never blindly repeated.
"""
import os
from pathlib import Path
import time

from core import pcactions, pcledger

MAX_ATTEMPTS = 2
RECOVERY_SECONDS = 15


def candidates(step):
    if step["tool"] == "inspect_blender":
        return ["native", "blender_mcp"]
    if step["tool"] == "open_app" and step["arguments"]["app"].casefold() in ("notepad", "paint"):
        return ["native", "windows_builtin"]
    from core import acquire, skillspec             # methods JARVIS learned himself: verified in the lab AND approved by the owner
    # A learned skill exists because the native method failed the owner. Once it passed its exam, was approved and has not
    # been demoted by a retest, it is OFFERED FIRST for exactly this step (close_foreground alone is held behind native by pcledger.method_order
    # unless native has just failed), and nothing here changes what the step is allowed to do
    # (core/authz decided that before any method runs).
    return acquire.candidates_for(step["tool"]) + skillspec.candidates_for(step["tool"]) + ["native"]


def methods_for(step):
    """The methods one step may try, best first, at most MAX_ATTEMPTS. The native method always keeps a place: learned
    methods and skills are extras, and two of them must never push the built-in way out of the list."""
    order = pcledger.method_order(step, candidates(step))
    chosen = order[:MAX_ATTEMPTS]
    if "native" in order and "native" not in chosen:
        chosen = chosen[:MAX_ATTEMPTS - 1] + ["native"]
    return chosen


def _builtin(app, cancelled):
    expected = pcactions.APPS[app.casefold()]
    # A late successful first launch must never cause a duplicate launch.
    if pcactions.process_running(expected):
        return {"verified": True, "detail": expected + " is already running; no second launch.", "process": expected}
    # Fixed OS-owned executables only; no PATH search or command text from a model.
    target = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / expected
    if not target.is_file():
        raise RuntimeError("The fixed Windows builtin executable is unavailable.")
    if cancelled():
        raise InterruptedError("Stopped before alternate launch.")
    from tools.software import launch_as_user
    launch_as_user(str(target))
    for _ in range(30):
        if cancelled():
            raise InterruptedError("Stopped while verifying alternate launch.")
        if pcactions.process_running(expected):
            return {"verified": True, "detail": "Windows builtin launch verified: " + expected, "process": expected}
        time.sleep(.1)
    raise RuntimeError("Alternate launch was not verified.")


# Hard ceiling for ONE step, in seconds. A UI step is 20 s; slower verbs get an explicit, bounded allowance. A step that hits
# its ceiling is reported as failed and the turn moves on (the turn lock is released), AND the operation is stopped: see
# _bounded (fenced primitives, killed process trees). An OS call already in flight (one UIA query) cannot be pre-empted; it
# may finish, but nothing after it can change the PC.
STEP_TIMEOUT_S = 20
STEP_TIMEOUTS = {
    "unreal_run_tests": 600,    # the anti-stuck maximum: discovery (<=120 s) + list (<=60) + run (<=360) + results (<=60)
    "unreal_tests": 200, "unreal_test_results": 200, "unreal_log": 60, "unreal_find_asset": 60,
    "shadow_world_import": 600,  # import + verify + retarget + game test (each fenced) + git
    "shadow_world_retarget": 360, "shadow_world_game_test": 480,
    "save_document": 30,        # Ctrl+S, up to 8 s for the dialog, the path, up to 8 s for the file
    "run_terminal": 40,         # core.pcverbs.TERMINAL_TIMEOUT_S (30 s) plus the gate and audit
    "find_project": 60, "find_files": 45,                     # file-system walks over the owner's folders
    "open_path": 160,           # its own window wait: 150 s for an Unreal project (shader compile), 40 s .blend/.sln, 15 s else
    "investigate_slowness": 45, "read_screen": 90,            # OCR / a local vision model reading the screen
    "research_web": 90, "read_web_page": 60, "browse_research": 90,
    "install_program": 600, "uninstall_program": 600,         # winget; always asked first (irreversible)
    "render_preview": 300, "export_glb": 120,
    "use_app": 180,             # the click loop: up to 12 model-chosen steps, each read back from the window (live 2026-11-07: 20 s cut it off)
}


def step_timeout(tool: str) -> float:
    return float(STEP_TIMEOUTS.get(tool, STEP_TIMEOUT_S))


import threading as _threading
import contextlib as _contextlib
_turn = _threading.local()


@_contextlib.contextmanager
def turn_deadline(at):
    """While a command turn has a wall-clock budget (core.pcagent.turn_budget), no step may run past it: a step's own ceiling
    is cut to what is left of the turn (never below one second, so a step that was allowed to start can do its work)."""
    before = getattr(_turn, "at", None)
    _turn.at = at
    try:
        yield
    finally:
        _turn.at = before


_CLOSE_FLOOR_S = 6.0           # pcactions.CLOSE_WAIT_S (4 s) and the time to ask the window to close
_LAUNCH_FLOOR_S = 10.0         # tools.apps.launch_and_wait waits up to 8 s for the window, plus the settle after it


def _step_limit(tool: str) -> float:
    """The step's ceiling, cut to what is left of the turn for the ORIGINAL plan's own plain app verbs ONLY. A step a re-plan added
    (even an app verb, and open_path, use_app, find_project) keeps its own ceiling: pursue starts it only with time left in the
    turn, and one it did start must not be killed after it had begun launching or clicking."""
    limit = step_timeout(tool)
    at = getattr(_turn, "at", None)
    if at is None:
        return limit
    from core.pcagent import SIMPLE_VERBS, replanned_step
    if tool not in SIMPLE_VERBS or replanned_step():
        return limit
    # An app verb that was allowed to start gets the time its own wait needs: a launch waits for the window, a close waits for the
    # window to go (pcactions.CLOSE_WAIT_S) plus the UIA listing; cut sooner it is started and then killed mid-way.
    floor = _LAUNCH_FLOOR_S if tool == "open_app" else (_CLOSE_FLOOR_S if tool in ("close_app", "close_foreground") else 1.0)
    return max(floor, min(limit, at - time.monotonic()))


def _thread_context():
    """The per-thread state a step reads on the caller's thread: who is acting, the owner's own message (approval context),
    and the craft job being reported on. The step worker gets exactly the same."""
    from core import craftjobs, pcbridge, users
    from jarvis_pc.guardrails import stepfence
    # "bg": a mission step (stepfence.background_only on the caller) stays background on the worker too, so no fenced
    # primitive there can take the foreground (2026-11-10, Mark VI part 3).
    # "meter"/"bgi" (2026-11-10 review): a mission's $0 meter and background inference ride along too, so a model call on
    # the step worker (use_app's click loop, a second look) is charged to the mission, never falls back to the paid cloud
    # (intelligence._cloud_fallback refuses while a meter is set) and never queues in front of the owner's own turn.
    from core import intelligence
    return {"uid": getattr(users._local, "uid", None), "request": getattr(pcbridge._local, "request", None),
            "job": getattr(craftjobs._tls, "job", None), "bg": stepfence.is_background(),
            "meter": getattr(intelligence._meter, "fn", None), "bgi": bool(getattr(intelligence._background, "on", False))}


def _restore_thread_context(c):
    from core import craftjobs, pcbridge, users
    from jarvis_pc.guardrails import stepfence
    if c["uid"] is not None:
        users._local.uid = c["uid"]
    pcbridge._local.request = c["request"]
    craftjobs._tls.job = c["job"]
    stepfence._local.background = bool(c.get("bg"))
    from core import intelligence
    intelligence._meter.fn = c.get("meter")
    intelligence._background.on = bool(c.get("bgi"))


def _waiting_in(thread) -> str:
    """Where a stuck step is waiting (the last frames of its thread), so a timeout is diagnosable from the log alone."""
    import sys
    import traceback
    frame = sys._current_frames().get(thread.ident)
    if frame is None:
        return "?"
    return " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in reversed(traceback.extract_stack(frame)[-5:]))


MAX_ABANDONED = 2          # workers still stuck after their limit; beyond this no new step starts (bounded, honest)
_ABANDONED: list = []      # (tool, thread, token, when it was abandoned)
# A worker still blocked in an OS call this long after its limit stays blocked for good. It is fenced (it can change
# nothing), so it stops counting against MAX_ABANDONED instead of waiting for a restart (audit-pc-control defect 11); the
# leak is at most MAX_ABANDONED threads per window.
ABANDONED_FORGOTTEN_S = 600.0
_now = time.monotonic


def abandoned() -> list:
    """Workers that passed their limit and have not ended yet (each fenced: it can no longer change the PC), the
    ones abandoned within the last ABANDONED_FORGOTTEN_S."""
    now = _now()
    _ABANDONED[:] = [a for a in _ABANDONED if a[1].is_alive() and (len(a) < 4 or now - a[3] < ABANDONED_FORGOTTEN_S)]
    return list(_ABANDONED)


def _bounded(tool, fn, cancelled):
    """Run fn(stop) on a worker thread, wait at most step_timeout(tool) seconds, and on the limit STOP THE OPERATION:
    the step's token expires (every PC-changing primitive refuses to act: jarvis_pc.guardrails.stepfence), its registered
    child process trees are killed (a terminal step's kill switch trips), and cooperative checks see stop() turn true."""
    import threading
    from core.words import plural
    from jarvis_pc.guardrails import stepfence
    stuck = abandoned()
    if len(stuck) >= MAX_ABANDONED:
        raise RuntimeError(f"{len(stuck)} earlier PC steps ({', '.join(a[0] for a in stuck)}) are still stuck past their time "
                           "limit, so I'm not starting another one; they can't change anything, and they clear when they end or after "
                           f"{plural(round(ABANDONED_FORGOTTEN_S / 60), 'minute')}")
    limit_now = _step_limit(tool)        # on the caller's thread: the turn's deadline is per turn
    token = stepfence.StepToken(tool)
    stop = lambda: cancelled() or token.expired.is_set()  # noqa: E731
    box = {}
    carried = _thread_context()
    token.background = carried["bg"]

    def run():
        _restore_thread_context(carried)
        stepfence.bind(token)
        try:
            box["out"] = fn(stop)
        except BaseException as error:  # noqa: BLE001  (re-raised on the caller's thread)
            box["error"] = error
        finally:
            stepfence.bind(None)

    worker = threading.Thread(target=run, daemon=True, name=f"pcstep-{tool}")
    worker.start()
    limit = limit_now
    worker.join(limit)
    if worker.is_alive():
        killed = token.expire()                     # from here the worker cannot press, paste, launch, close or focus
        worker.join(1.0)                            # a cooperative step usually ends at its next check
        where = _waiting_in(worker) if worker.is_alive() else "ended after the stop"
        if worker.is_alive():
            _ABANDONED.append((tool, worker, token, _now()))
        print(f"[pcstep] {tool} passed its {limit:.0f} s ceiling; stopped (killed {len(killed)} process tree(s)); "
              f"worker: {where}", flush=True)
        pcledger.usage("local_automation", tool=tool, method="timeout", billable_usd=0, verified=False, error_type="StepTimeout",
                       where=where, killed=len(killed))
        raise RuntimeError(f"The {tool.replace('_', ' ')} step did not finish within {limit:.0f} s, so I stopped it: nothing more "
                           "will be typed, clicked, launched or closed for it" + (f", and its {len(killed)} program(s) were ended" if killed else ""))
    if "error" in box:
        raise box["error"]
    return box["out"]


def invoke(method, step, approved, cancelled):
    if method == "native":
        return _bounded(step["tool"], lambda stop: pcactions.perform(step["tool"], step["arguments"], approved=approved, cancelled=stop),
                        cancelled)
    if method == "blender_mcp" and step["tool"] == "inspect_blender":
        return pcactions.perform("blender_status", {}, cancelled=cancelled)
    if method == "windows_builtin" and step["tool"] == "open_app":
        return _builtin(step["arguments"]["app"], cancelled)
    if method.startswith("learned:") and method in candidates(step):
        from core import acquire
        return _bounded(step["tool"], lambda stop: acquire.run_learned(method, step), cancelled)
    if method.startswith("skill:") and method in candidates(step):
        from core import skillspec
        return _bounded(step["tool"], lambda stop: skillspec.run_approved(method, step), cancelled)
    raise RuntimeError("Unregistered recovery method.")


def _why(step, output=None, error=None) -> dict:
    """What to write beside a failed attempt in the ledger so it can be diagnosed later (audit-pc-control defect 6: most
    failed find_project rows said only 'native'). Only our own typed actions' RuntimeError text, the outcome's error_kind and,
    for find_project, the name that was looked up; any other exception text could carry internals and stays out."""
    out = {}
    if step.get("tool") == "find_project":
        tried = " ".join(str((step.get("arguments") or {}).get("name", "")).split())[:60]
        if tried:
            out["tried"] = tried
    if isinstance(output, dict):
        if output.get("error_kind"):
            out["error_kind"] = str(output["error_kind"])[:40]
        if output.get("candidates"):
            out["candidates"] = len(output["candidates"])
    if error is not None and type(error) is RuntimeError and str(error).strip():
        out["reason"] = " ".join(str(error).split())[:160]
    return out


def execute(step, *, approved=False, cancelled=lambda: False, execution_id=None):
    from core import turntrace
    with turntrace.timed("pc", step.get("tool", "?")):
        return _execute(step, approved=approved, cancelled=cancelled, execution_id=execution_id)


def _front_before(step):
    """close_foreground only: the window in front before any method runs (None when it cannot be told or is not that tool)."""
    if step.get("tool") != "close_foreground":
        return None
    try:
        from core import learnedclose
        hwnd = learnedclose.Env().front_hwnd()
        return hwnd or None
    except Exception:  # noqa: BLE001
        return None


def _same_window_in_front(hwnd) -> bool:
    try:
        from core import learnedclose
        env = learnedclose.Env()
        return bool(env.shown(hwnd)) and env.front_hwnd() == hwnd
    except Exception:  # noqa: BLE001
        return False


def _execute(step, *, approved=False, cancelled=lambda: False, execution_id=None):
    from jarvis_pc.guardrails import stepfence
    from core import learnedclose
    pcactions.validate({"steps": [step]})
    deadline = time.monotonic() + RECOVERY_SECONDS
    attempts = []
    methods = methods_for(step)
    front0 = _front_before(step)
    for method in methods:
        if attempts and front0 is not None and not _same_window_in_front(front0):
            break          # a close may have gone out: the window in front now is a different one, and nothing else acts on it
        if cancelled():
            raise InterruptedError("Stopped before the next local method.")
        if time.monotonic() >= deadline:
            break  # Deadline bounds starting retries, not preemption of an OS call.
        if "before" in step and not pcactions.condition(step["before"]):
            raise RuntimeError("A precondition changed before the next method.")
        try:
            started = time.monotonic()
            output = invoke(method, step, approved, cancelled)
            pcledger.latency("window_readiness" if step["tool"] == "open_app" else "pc_step", started,
                execution_id=execution_id, tool=step["tool"], method=method)
            verified = bool(output["verified"])
            if verified and "expect" in step:
                verified = pcactions.condition(step["expect"])
            # A learned close that left the app's own save prompt open did what it should: the owner is asked, it is not a failure.
            # A close that ends in "asking about unsaved changes" / "still open, needs you" is the app's answer, not a method failure.
            if not (step["tool"] == "close_foreground" and not verified and output.get("error_kind") == "needs_owner"):
                pcledger.method_outcome(step, method, verified or bool(output.get("prompt_confirmed")))
            pcledger.usage("local_automation", execution_id=execution_id, tool=step["tool"], method=method,
                billable_usd=0, verified=verified, **({} if verified else _why(step, output)))
            attempts.append({"method": method, "verified": verified})
            if verified and step["tool"] in pcactions.OPENS:
                pcactions.note_opened(step)             # "close it" later means THIS window, not whatever is in front
            # No alternate after an uncertain outcome, especially any write.
            return {**output, "verified": verified, "attempts": attempts, "method": method}
        except InterruptedError:
            raise
        except stepfence.BackgroundUnavailable as error:
            # A mission step that would need the foreground: nothing was done, and no other method is tried (each would need
            # it too, or worse, take it). Not a method failure, so the method order is not punished (2026-11-10, Mark VI).
            attempts.append({"method": method, "verified": False, "error_type": "BackgroundUnavailable"})
            return {"verified": False, "error_kind": error.kind, "detail": f"needs you at the PC: {error.what}",
                    "attempts": attempts}
        except learnedclose.Refused as error:
            # A learned close declined before touching anything (Jarvis's own window, the desktop, a game, the lock screen ...):
            # not the method's failure, so nothing is recorded against it; the built-in method is next.
            attempts.append({"method": method, "verified": False, "error_type": "Refused"})
            continue
        except learnedclose.NoFallback as error:
            # The learned close ran and what it did is unknown (or another window is in front now): the built-in method would
            # act on the wrong window, so nothing else is tried. A Stale stop pressed nothing, so it is not held against the method.
            if not isinstance(error, learnedclose.Stale):
                pcledger.method_outcome(step, method, False)
            pcledger.usage("local_automation", execution_id=execution_id, tool=step["tool"], method=method,
                billable_usd=0, verified=False, error_type="NoFallback", **_why(step))
            attempts.append({"method": method, "verified": False, "error_type": "NoFallback"})
            break
        except stepfence.UncertainOutcome as error:
            # A write went out but could not be read back: no alternate method (it would write a second time).
            pcledger.method_outcome(step, method, False)
            attempts.append({"method": method, "verified": False, "error_type": "UncertainOutcome"})
            return {"verified": False, "error_kind": error.kind, "detail": " ".join(str(error).split())[:240],
                    "attempts": attempts}
        except Exception as error:
            pcledger.method_outcome(step, method, False)
            pcledger.usage("local_automation", execution_id=execution_id, tool=step["tool"], method=method,
                billable_usd=0, verified=False, error_type=type(error).__name__, **_why(step, error=error))
            # Only a RuntimeError raised by our own typed actions is written for the user ("No browser window is showing
            # YouTube; nothing was closed."); any other exception text could carry internals and is never surfaced.
            reason = " ".join(str(error).split())[:240] if type(error) is RuntimeError and str(error).strip() else ""
            attempts.append({"method": method, "verified": False, "error_type": type(error).__name__, **({"reason": reason} if reason else {})})
            if step["tool"] == "close_foreground" and method.startswith("learned:") and not isinstance(error, learnedclose.NotDone):
                # A learned close that timed out or broke may already have pressed Close; the built-in close would then act on
                # whatever is in front now. Nothing else is tried (the wrapper itself handles the case where that is provably safe).
                break
    reasons = [a["reason"] for a in attempts if a.get("reason")]
    from core import pchelp
    detail = pchelp.step_failed(step, attempts)       # what could not be done and what he can say next (core.pchelp)
    if reasons:
        detail = pchelp.with_next(step["tool"], reasons[-1].rstrip(".") + ".")   # type_text: plus what to do next
    return {"verified": False, "detail": detail, "attempts": attempts}
