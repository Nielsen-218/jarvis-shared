"""Bounded PC jobs. One desktop job at a time; no external shell or AI instructions executor."""
import copy
import hashlib
import importlib
import json
import threading
import time
import uuid

from core import approvals, pcbridge, progress, users

COLLECT_WAIT_S = 30.0   # the laptop polls a relayed task every 0.4 s: a result not collected by then is news at the PC


class Jobs:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.active = None

    def _view(self, job):
        return copy.deepcopy({k: v for k, v in job.items() if k not in ("uid", "fingerprint", "cancel_event")})

    def start(self, uid, name, arguments, request_id, message=""):
        pcbridge.require_owner(uid)
        if not pcbridge.local_pc():
            raise pcbridge.PCError("This machine is not the Windows desk PC.")
        if name not in pcbridge.TOOLS:
            raise pcbridge.PCError("That operation is not an allowed PC tool.", 400)
        if not isinstance(arguments, dict) or len(json.dumps(arguments)) > 12000:
            raise pcbridge.PCError("Invalid PC tool arguments.", 400)
        if not isinstance(message, str) or len(message) > 8000:
            raise pcbridge.PCError("Invalid user message.", 400)
        try:
            uuid.UUID(request_id)
        except (ValueError, TypeError, AttributeError):
            raise pcbridge.PCError("A valid request_id is required to prevent duplicate tasks.", 400) from None
        importlib.import_module("tools." + pcbridge.TOOLS[name])
        from tools import registry
        arguments = dict(arguments)
        # The laptop's "is this a PC matter? answer it" flag is relay metadata, not a tool argument (live 2026-11-06:
        # validating it as one refused every probe with "unknown argument(s): probe").
        probe = name == "control_computer" and arguments.pop("probe", False) is True
        problem = registry.validate(name, arguments)
        if problem:
            raise pcbridge.PCError(problem, 400)
        fingerprint = hashlib.sha256(json.dumps([name, arguments, message, probe], sort_keys=True).encode()).hexdigest()
        with self.lock:
            now = time.time()
            self.jobs = {k: j for k, j in self.jobs.items() if k == self.active or now - j["updated"] < 3600}
            old = self.jobs.get(request_id)
            if old:
                if old["uid"] != uid or old["fingerprint"] != fingerprint:
                    raise pcbridge.PCError("This request_id already belongs to a different task.", 409)
                return self._view(old)
            if self.active:
                raise pcbridge.PCError("The PC is already working on a task. Check status or stop it first.", 409)
            if len(self.jobs) >= 64:
                raise pcbridge.PCError("PC task history is full; wait for older tasks to expire.", 429)
            job = {"id": request_id, "uid": uid, "fingerprint": fingerprint, "name": name,
                "state": "running", "detail": "starting " + name.replace("_", " "), "updated": now,
                "cancel_event": threading.Event(), "probe": probe}
            self.jobs[request_id] = job
            self.active = request_id
            threading.Thread(target=self._execute, args=(request_id, dict(arguments), message), daemon=True,
                name="pc-task").start()
            return self._view(job)

    def _execute(self, jid, arguments, message):
        job = self.jobs[jid]
        timed_out = threading.Event()
        started = time.time()

        def timeout():
            timed_out.set()
            self.cancel(job["uid"], jid)

        timer = threading.Timer(600, timeout)
        timer.daemon = True
        timer.start()
        try:
            with users.acting_as(job["uid"]):
                # The authenticated relay carries the ingress words, never a model-created "yes".
                if message:
                    approvals.on_user_message(job["uid"], message)
                from tools import registry
                if not job.get("probe"):             # a probe is the laptop asking: it must not look like work in progress to quick._status
                    progress.start("controlling your PC")
                if job["cancel_event"].is_set():
                    result, error = "Stopped before execution.", False
                elif job["name"] == "control_computer":
                    from tools import computer, gaming
                    if gaming.game_mode():
                        result, error = "PC control is paused during competitive gaming.", True
                    else:
                        from core import gateway, pcfast, pchybrid
                        result, error, hybrid = None, False, None
                        probe = bool(job.get("probe"))                   # the laptop asks: "is this a PC matter? answer it"
                        if probe and gateway.mode() == "on":
                            said = gateway_text(arguments.get("task"), message) or str(arguments.get("task") or "")
                            from core import quick
                            instant = quick.handle(said)              # the PC's exact answers (skills list, memory use, volume)
                            if instant is not None:
                                result = instant
                            else:
                                pchybrid.clear_result()
                                reply = pcfast.handle(said)
                                result = pcfast.NOT_PC if reply is None else reply
                                hybrid = pchybrid.last_result() if reply is not None else None
                        elif probe:
                            result = pcfast.NOT_PC                   # gateway off: the laptop's own path answers, as before
                        if result is None and gateway.mode() == "on" and message:
                            # The phone's words get the SAME understanding and execution as the desktop and voice: the
                            # PC's gateway entry (core.pcfast.handle). Before, a phone request skipped the gateway and went
                            # straight to the older hybrid planner, so the three channels answered differently.
                            pchybrid.clear_result()
                            said = gateway_text(arguments.get("task"), message)
                            reply = pcfast.handle(said) if said else None
                            if reply is not None:
                                result, hybrid = reply, pchybrid.last_result()
                        if result is None:
                            if job.get("probe"):
                                progress.start("controlling your PC")
                            result = computer.control_computer(**arguments, cancel_event=job["cancel_event"])
                            hybrid = pchybrid.last_result()
                        # Autonomy (default on, off only after "ask me first"): clear a cost or SAFE
                        # plan gate and continue, exactly like the chat path — so the owner isn't asked
                        # to approve harmless tasks. A sensitive/irreversible plan is still shown.
                        allow = approvals.pc_autonomous(job["uid"])
                        for _ in range(3):
                            if (not allow or not hybrid or hybrid.get("state") not in pcfast._GATE
                                    or job["cancel_event"].is_set()):
                                break
                            if hybrid["state"] == "needs_review" and not pcfast._plan_safe(hybrid.get("plan")):
                                break  # sensitive/irreversible: always show for explicit review
                            kind = "cloud" if hybrid["state"] == "needs_budget" else "plan"
                            if not pcfast._grant(hybrid.get("approval_id"), kind):
                                break  # budget spent or grant failed: the gate stands
                            result = computer.control_computer(**arguments, cancel_event=job["cancel_event"])
                            hybrid = pchybrid.last_result()
                        if hybrid:
                            with self.lock:
                                job["hybrid"] = hybrid
                else:
                    result, error = registry.run(job["name"], arguments)
                blocks = result if isinstance(result, list) else [{"type": "text", "text": result}]
                with self.lock:
                    job["blocks"] = blocks
                    text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                    job["state"] = ("failed" if error or "Computer control failed:" in text
                        or text.startswith(("Stopped after", "The PC is already being controlled.")) else
                        "timed_out" if timed_out.is_set() else
                        "cancelled" if text.startswith(("Stopped:", "Stopped before execution.")) else
                        "completed_after_stop" if job["state"] == "cancel_requested" else
                        "needs_confirmation" if "NEEDS_CONFIRMATION" in text else "completed")
                    job["detail"] = text[:1000]
                    if job.get("hybrid"):
                        job["state"] = job["hybrid"]["state"]
        except Exception as error:
            with self.lock:
                job.update(state="failed", error=f"PC task failed ({type(error).__name__}). Check the PC Jarvis log.")
        finally:
            timer.cancel()
            progress.done()
            with self.lock:
                job["updated"] = time.time()
                self.active = None
            took = time.time() - started
            from core import notice
            if took >= notice.LONG_TASK_S and not job.get("probe"):
                later = threading.Timer(COLLECT_WAIT_S, self._notice, args=(job, arguments, took, message))
                later.daemon = True
                later.start()

    @staticmethod
    def _notice(job, arguments, took, message=""):
        """Plan upgrade 9 (core/notice): a long relayed task (sent from the phone or the laptop) whose result nobody
        collected is a slot for the voice at the PC. The laptop waits for the result (pcbridge.run polls until it ends) and
        the phone shows it: a collected one is no news at the PC. Its words are his own request (else the tool's), never
        what the PC part reported."""
        from core import notice
        if (job.get("collected") or took < notice.LONG_TASK_S or job.get("probe")
                or job["state"] not in ("completed", "failed", "timed_out")):
            return
        import re
        said = re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", str(message or "").strip(), flags=re.I)
        what = " ".join(str(said or arguments.get("task") or job["name"].replace("_", " ")).split())[:120].rstrip(".?!")
        done = {"completed": "finished", "failed": "failed", "timed_out": "ran out of time"}[job["state"]]
        notice.finished("pc-task", job["id"], f"The PC task you asked for earlier ({what}) {done}.")

    def get(self, uid, jid):
        pcbridge.require_owner(uid)
        with self.lock:
            job = self.jobs.get(jid)
            if not job or job["uid"] != uid:
                raise pcbridge.PCError("PC task not found (the PC may have restarted).", 404)
            if job["state"] not in ("running", "cancel_requested"):
                job["collected"] = True      # the laptop that asked has the result now (core/notice: no news at the PC)
            view = self._view(job)
            if jid == self.active:
                view["detail"] = progress.report()
            return view

    def cancel(self, uid, jid=None):
        pcbridge.require_owner(uid)
        with self.lock:
            jid = jid or self.active
            if not jid:
                return {"state": "idle", "detail": "No remote PC task is running."}
            job = self.jobs.get(jid)
            if not job or job["uid"] != uid:
                raise pcbridge.PCError("PC task not found.", 404)
            if jid == self.active:
                job["state"] = "cancel_requested"
                job["cancel_event"].set()
                if job["name"] == "control_computer":
                    from tools import computer
                    computer.abort()
                job["detail"] = "Stop requested. An already-started installer or Windows operation may need to finish."
            return self._view(job)

    def status(self, uid):
        pcbridge.require_owner(uid)
        from tools.software import is_admin
        with self.lock:
            return {"connected": True, "machine": "Windows PC", "admin": is_admin(),
                "tools": list(pcbridge.TOOLS), "active": self.get(uid, self.active) if self.active else None,
                "recent": [{k: v for k, v in self._view(j).items() if k != "blocks"}
                    for j in list(self.jobs.values())[-5:] if j["uid"] == uid],
                "note": "Screen actions need an unlocked Windows desktop. Admin rights do not bypass the Windows lock screen or secure UAC desktop."}


def gateway_text(task, message):
    """The owner's own words for a relayed PC task, when the task IS those words (the laptop's fast path forwards the
    sentence unchanged). A task the laptop's model paraphrased or cut out of a longer sentence returns "" and keeps the
    older path: the gateway authorizes by the owner's words, so it must never act on someone else's wording of them."""
    import re
    said = re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", str(message or "").strip(), flags=re.I)
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", str(t or "").casefold()).strip()
    return said if said and norm(said) == norm(task) else ""
