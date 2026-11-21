"""PC intent interception before any paid conversational turn, shared by voice and phone.

Approval UX: the owner never has to type a dollar amount. A paid or plan gate is cleared by
a plain spoken "yes", and if the owner has said "just do it / don't bother me" it clears
automatically. Hard safety stays: the monthly budget still caps spend, and a sensitive or
irreversible step (uninstall/delete/close/set-text and anything buy/send/post) is never
auto-run — it is always shown for an explicit review.
"""
import contextvars
import re
import time

from core import approvals, pcbridge, pchybrid, storage, users

_GATE = ("needs_budget", "needs_review")


def _auto_usd():
    """Per-task cloud budget auto-granted on a yes / do-not-disturb, capped at $1 and by the
    remaining monthly budget. 0 means the monthly budget is spent, so the gate stands."""
    from core import usage
    remaining = usage.budget() - usage.spent()
    return round(min(1.0, remaining), 4) if remaining > 0.01 else 0


def _plan_safe(plan):
    """Safe to run without an explicit review only if no step is sensitive/irreversible."""
    from core import pcactions
    try:
        steps = (plan or {}).get("steps") or []
        return bool(steps) and not any(pcactions.SCHEMAS[s["tool"]][1] for s in steps)
    except Exception:
        return False


def _grant(rid, kind):
    """Approve one pending gate as the owner (a verbal yes / do-not-disturb). True if granted."""
    from core import pcledger
    if not rid:
        return False
    try:
        if kind == "cloud":
            usd = _auto_usd()
            if not usd:
                return False
            pcledger.approve(rid, "cloud", usd)
        else:
            pcledger.approve(rid, "plan")
        return True
    except ValueError:
        return False


def _run_with_consent(task, control_pc, *, allow):
    """Run `task`; while it stops at a cost or SAFE plan gate the owner has authorized, approve
    that gate and continue. Stops at any sensitive/irreversible plan or when the budget is spent."""
    blocks = control_pc(task)
    for _ in range(3):
        res = pchybrid.last_result()
        if not allow or not res or res.get("state") not in _GATE:
            return blocks
        if res.get("guarded"):
            return blocks  # "Did you mean ChatGPT?" is a question only the owner answers: standing autonomy never says yes to a guess
        if res["state"] == "needs_review" and not _plan_safe(res.get("plan")):
            return blocks  # sensitive/irreversible: always show for explicit review
        kind = "cloud" if res["state"] == "needs_budget" else "plan"
        if not _grant(res.get("approval_id"), kind):
            return blocks  # budget spent or grant failed: the gate stands
        blocks = control_pc(task)
    return blocks


def _turn_cancel():
    """The owner's interruption for THIS turn: Brain.cancel() calls tools.computer.abort(). Cleared when the turn starts (a
    stale stop from the last turn must not stop this one), then read by the understanding and by every step of the plan.
    Without it a cancelled gateway plan ran on to its end (2026-11-06 acceptance: save_document after a cancel)."""
    try:
        from tools import computer
    except Exception:  # noqa: BLE001  (the Linux server has no computer control)
        return lambda: False
    computer._abort.clear()
    return computer._abort.is_set


NOT_PC = "[not a PC request]"
# A question about the desk PC itself. On the laptop (the phone's server) these were answered by the laptop's model from
# imagination: live 2026-11-06 "how much RAM is on my PC" -> "16 GB" (31.5 GB), "graphic card" -> "RTX 3060" (5070 Ti).
_ABOUT_PC = re.compile(r"\b(?:pc|computer|desktop|screen|monitor|graphics? card|gpu|cpu|processor|ram|c drive|d drive|disk|storage|"
                       r"installed|windows (?:version|build)|apps? (?:are )?open|which apps|what apps|steam|notepad|file explorer|downloads|"
                       # the game project, Unreal, Blender and the rigs live on the PC: from the phone the brain INVENTED "the
                       # training plan ... is still pending" for "Is my Shadow World character ready?" (laptop, 2026-11-10)
                       r"unreal|shadow ?world|blender|rig(?:ged|ging|s)?|retarget\w*|game test|in-?game test)\b", re.I)


def ask_pc(raw):
    """From the laptop: let the PC's own gateway answer a sentence about the PC (same understanding as the desktop and
    voice). Returns its reply, None when the PC says it is not a PC matter, or an honest 'can't reach' for a PC fact."""
    from tools.pcview import pc_command
    try:
        blocks = pc_command("control_computer", {"task": raw, "probe": True})
    except Exception:  # noqa: BLE001
        blocks = None
    text = " ".join(b.get("text", "") for b in (blocks or []) if isinstance(b, dict) and b.get("type") == "text").strip()
    if text == NOT_PC:
        return None
    if text.startswith(pcbridge.ASLEEP):      # a fact about the PC is never guessed, and a question is not a job to queue
        return "I can't reach the PC right now, it looks asleep or unreachable, so I can't check that, and I won't guess."
    if not text or text.startswith(("The PC is unreachable", "The PC link needs", "The PC returned", "The PC rejected",
                                    "The PC address", "The PC response", "Use the PC's")):
        return "I can't reach the PC right now, so I can't check that, and I won't guess. " + (text or "")
    return text


def _asks_a_pc_fact(raw: str) -> bool:
    """"Is Notepad still open?": the one fact core.pcfacts answers. The laptop asks the PC, which reads its own windows; only a
    sentence that recogniser accepts is forwarded."""
    try:
        from core import pcfacts
        return pcfacts.is_fact(raw)
    except Exception:  # noqa: BLE001
        return False


def _answers_a_question_here(raw: str, history) -> bool:
    """On the laptop: a line that asks for nothing ("Steam.") right after Jarvis asked him something HERE ("Which one,
    sir: Steam or the Epic launcher?") is his answer to that question. The PC's probe has no history and judges it 'a name,
    not a request' (core.gateway, wave 4), so this machine's own gateway, which sees the question, understands it
    (review 2026-11-11: the phone's answer was lost)."""
    try:
        from core import gateway
        return gateway._question_pending(history) and not gateway.asks_for_something(raw)
    except Exception as error:  # noqa: BLE001 - unknown: the PC is asked, as before
        print(f"[pcfast] could not tell whether a question waits here: {type(error).__name__}: {error}")
        return False


def _answers_other(uid: str) -> bool:
    """The latest exchange asked something that is no plan, approval or guide (core.turnstate.answers_other)."""
    try:
        from core import turnstate
        return turnstate.answers_other(uid)
    except Exception as error:  # noqa: BLE001 - unknown: as before turnstate
        print(f"[pcfast] could not tell which question was just asked: {type(error).__name__}: {error}")
        return False


def _yes_runs(uid: str):
    """(guide, plan) his yes runs: only the one the latest exchange asked (core.turnstate.answering). A yes to an approval
    just asked, or to a question in words, runs neither (the brain carries that out); review 2026-11-10: a yes to 'Delete x?'
    ran a Notepad plan put up two exchanges earlier. Nothing recorded (a caller outside a Brain exchange): as before, the
    guide first, else the newest plan."""
    from core import pcledger, turnstate
    from tools import pcskills
    try:
        state, item = turnstate.answering(uid)
    except Exception as error:  # noqa: BLE001 - unknown: as before turnstate
        print(f"[pcfast] could not tell which question this yes answers: {type(error).__name__}: {error}")
        state, item = "unknown", None
    if state == "unknown":
        guide = pcskills.pending_guide(uid)
        return guide, (None if guide else next(iter(pcledger.pending()), None))
    if state != "fresh":
        return None, None
    if item["kind"] == "guide":
        guide = pcskills.pending_guide(uid)
        return (guide if guide and str(guide.get("key")) == item.get("key") else None), None
    if item["kind"] in ("plan", "cloud"):
        return None, next((row for row in pcledger.pending() if row.get("id") == item["id"]), None)
    return None, None


EARCON_SECONDS = 1.5      # understanding slower than this gets one soft, wordless cue (no claim can hide in a beep)


def _with_earcon(fn, earcon, cancelled):
    """Run fn(); if it takes longer than EARCON_SECONDS, `earcon()` plays once (unless the turn was cancelled)."""
    if earcon is None:
        return fn()
    import threading
    timer = threading.Timer(EARCON_SECONDS, lambda: None if cancelled() else earcon())
    timer.daemon = True
    timer.start()
    try:
        return fn()
    finally:
        timer.cancel()


def _session_open() -> bool:
    """A study quiz, a language lesson or a workout is open for him: a bare one-word line is that session's to read first
    ("skip", "leave", "hide"), and the session hooks run after this module. Unknown counts as open (nothing dismissed)."""
    try:
        from tools import langlesson, study, trainer
        return bool(study.on_the_table() or langlesson.on_the_table() or trainer.running())
    except Exception as error:  # noqa: BLE001
        print(f"[pcfast] could not tell whether a lesson is open: {type(error).__name__}: {error}")
        return True


def companion_line(raw, history=None):
    """"Come here" / "go away" said or typed to Jarvis: the desktop companion is shown or dismissed in code (owner
    2026-11-12), on this PC or, from the laptop, on the PC. None for every other line. A bare "leave" / "hide" / "dismiss"
    dismisses only with a history to prove no question is waiting, no lesson is open, and only on this PC."""
    from core import hud
    want = hud.companion_call(raw, answering=False)
    if not want:
        return None
    bare = want == "hide" and hud.companion_call(raw, answering=True) is None
    if bare:
        try:
            from core import gateway
            if history is None or gateway._question_pending(history) or _session_open() or not pcbridge.local_pc():
                return None
        except Exception:  # noqa: BLE001 - unknown: the safe reading is "he may be answering"
            return None
    try:
        pcbridge.require_owner()
    except pcbridge.PCError:
        return None
    return hud.companion_command(raw, answering=False) if pcbridge.local_pc() else ask_pc(raw)


# Set False by the brain when his yes comes too long after the offer (chatturn.YES_MAX_AGE): then the stale plan's re-ask stays.
OFFER_FRESH = contextvars.ContextVar("pcfast_offer_fresh", default=True)


def _yes_to_an_offer(raw: str, history) -> bool:
    """His bare yes answers the offer or question Jarvis's LAST reply ended with ("If you want the real headlines, say so and I'll
    pull them up."), not a plan that has waited since before it. turnstate sees only questions in words, so a declarative offer
    left the old plan the one a yes "landed on" and the yes was re-asked about it (live 2026-11-15: "Yes, I would like that very
    much." answered "To be clear, sir: open the Meshy panel...?"). Only the RE-ASK is skipped: the old plan is not approved."""
    try:
        from core import chatturn
        found = chatturn.binding(list(history or []) + [{"role": "user", "content": raw}])
        return bool(found and found[0] == "yes")
    except Exception:  # noqa: BLE001 - unknown: the stale plan's re-ask stays (the safe side)
        return False


def handle(text, history=None, say=None, earcon=None):
    """A PC request answered before the conversation model, or None (see _handle). Every route out of it (the gateway's plan,
    a "which one?" answer, a yes to a waiting plan, the legacy planner, a guide) ends here, so this is where a failed run is
    written down (core.misses): the executor's last result says whether it stopped or completed. A request nobody ran (None,
    a question, a review waiting for his yes) is neither."""
    from core import misses
    raw = text if isinstance(text, str) else ""
    with misses.turn(raw):
        pchybrid.clear_result()
        reply = _handle(text, history, say, earcon)
        res = pchybrid.last_result() if reply is not None else None
        state = (res or {}).get("state")
        if state == "stopped" and (res or {}).get("stop_reason"):
            pass          # stopped to ask him something or because he said stop: not a failure to learn from
        elif state == "stopped":
            planning = (res or {}).get("local_planning") is not None
            misses.fail("part of the request was not understood" if planning
                        else f"step not verified: {str(res.get('detail'))[:160]}", str(res.get("route") or "pc"))
        elif state == "completed":
            misses.ok(str(res.get("route") or "pc"))
        return reply


def _handle(text, history=None, say=None, earcon=None):
    """A PC request answered before the conversation model, or None. `say` (from Brain.ask: dropped once he cuts in) lets a
    slow turn acknowledge itself and report progress while it runs; `earcon` is a soft cue while it is still understood."""
    from core import misses
    began = time.monotonic()             # the turn's start: the gateway's acknowledgment counts from here (gateway.Ack)
    raw = pcbridge.original_message() or re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", text.strip(), flags=re.I)
    called = companion_line(raw, history)
    if called is not None:
        return called
    from core import pcpower as _pcpower
    # Power ("wake my PC" must go through the laptop's Wake-on-LAN, never to the sleeping PC), screenshots and stop keep
    # their own exact routes below; only other sentences about the PC are asked of the PC.
    if (not pcbridge.local_pc() and (_ABOUT_PC.search(raw) or _asks_a_pc_fact(raw)) and not _pcpower.parse(raw)
            and not re.search(r"screen\s?shot|screen\s?grab|screen\s?cap|\b(?:stop|cancel) (?:the )?(?:pc|computer) (?:task|control)", raw, re.I)
            and not _answers_a_question_here(raw, history)):
        try:
            pcbridge.require_owner()
            from core import approvals as _approvals     # a local name here would shadow the module for the whole function
            if not _approvals.means_yes(users.current(), raw):
                return ask_pc(raw)
        except pcbridge.PCError:
            pass
    from core import gateway
    live = gateway.mode() == "on"
    cancelled = _turn_cancel() if live else (lambda: False)
    try:       # "open the rigged character in Blender" right after a rig finished is the open_in_blender skill, not a PC-control click plan (live 2026-11-01)
        from core import craftintent
        route = craftintent._open_request(craftintent.clean(raw))
        if route and route[0] == "open_in_blender" and route[1].get("which") == "":
            return None
    except Exception:
        pass
    if pcbridge.local_pc():   # closing something that asks to save (owner 2026-11-07): yes/no, or said with the close
        from core import saveprompt
        uid_ = users.current()
        cmd = saveprompt.command(raw)
        if saveprompt.pending(uid_) or cmd:
            try:
                pcbridge.require_owner()
            except pcbridge.PCError as error:
                return str(error)
            with pcbridge.user_message(uid_, raw):
                answered = saveprompt.reply(uid_, raw)
                if answered is not None:
                    return answered
                if cmd:
                    return saveprompt.close_with(uid_, *cmd)
    if pcbridge.local_pc():   # "follow this" / "follow the instructions on my screen / in this video" (owner 2026-11-07)
        from core import screenguide
        if screenguide.asked(raw):
            try:
                pcbridge.require_owner()
            except pcbridge.PCError as error:
                return str(error)
            with pcbridge.user_message(users.current(), raw), gateway.acknowledged(
                    say, gateway.ack_opening(users.title()) + ": reading your screen.", cancelled, "reading the instructions on your screen"):
                return screenguide.follow(raw)
    if pcbridge.local_pc():   # the dashboard / on-screen workout / games are Jarvis's own screens, not PC apps
        from core import hud
        own = hud.own_screen(raw)
        if own:
            return own
    # Parse exact authenticated PC-power instructions BEFORE local models or
    # paid fallback; "Hey Jarvis, turn off my PC" must not be missed.
    from core import pcpower
    parsed_power=pcpower.parse(raw)
    power=parsed_power[0] if parsed_power else None
    immediate=parsed_power[1] if parsed_power else False
    # See the PC screen: match natural phrasing — "screenshot of the pc", "take a screenshot of my
    # computer", "show me my pc", "what's on my desktop", "pc screenshot plz" — not just one exact form.
    screen = bool(
        (re.search(r"screen\s?shot|screen\s?grab|screen\s?cap", raw, re.I)
         and re.search(r"\b(pc|computer|desktop|screen|monitor)\b", raw, re.I))
        or re.fullmatch(
            r"(?:show|see|view|let me see|lemme see|what'?s on|what is on)(?: me)?(?: the| my)?\s+"
            r"(?:pc|computer|desktop)(?:\s+screen)?\s*(?:please|plz|pls)?[.!?]*",
            raw.strip(), re.I))
    stop = bool(re.fullmatch(r"(?:stop|cancel) (?:the )?(?:pc|computer) (?:task|control)[.!]?", raw.strip(), re.I))
    known_workflow = raw.strip().casefold() in storage.load("pc_workflow_triggers.json", [])
    is_pc = bool(power or screen or stop or known_workflow or pchybrid.is_request(raw))
    decision = choice = None
    if live:
        # The gateway decides everything the exact recognisers above did not: the whole request, as a list of intents.
        try:
            pcbridge.require_owner()
            choice = gateway.choose(users.current(), raw)          # their answer to "which one?" from a moment ago
        except pcbridge.PCError:
            choice = None
        answer = None if (power or screen or stop or choice) else approvals.meant(users.current(), raw)
        if answer == "reask" and OFFER_FRESH.get() and _yes_to_an_offer(raw, history):
            return None                # his yes is for the offer just made: not re-asked, not planned, not run (the brain answers it)
        if answer == "reask":          # a yes to a plan asked before something else was said: asked once more, nothing runs
            from core import turnstate
            return turnstate.reask(users.current(), users.title())
        if answer == "no" and not _answers_other(users.current()):
            # "nah leave it" / "hold off on that" to a plan shown for review (live 2026-11-07); not a no to the question the
            # latest exchange asked of its own ("Do you want tomorrow's forecast too?"): that one is the brain's to answer
            try:
                pcbridge.require_owner()
                from core import pcledger as _pcledger    # 'pcledger' is a local name later in this function
                from core import turnstate as _turnstate   # never a plan put up after his words came in (heard_at)
                plan = next((p for p in _pcledger.pending()
                             if not _turnstate.asked_after_heard(users.current(), p.get("id"))), None)
            except pcbridge.PCError:
                plan = None
            if plan:
                _pcledger.decline(plan["id"])
                approvals.on_user_message(users.current(), raw)
                return "Okay, I won't."
        if not (power or screen or stop or choice) and answer != "yes":
            decision = _with_earcon(lambda: gateway.decide(raw, history, cancelled), earcon, cancelled)
            if cancelled():
                return "Stopped." if decision is not None and decision.kind != "chat" else None
            if decision is not None and decision.kind == "chat" and decision.source == "exact":
                # judged talk in code (his own narration, a line said while he demonstrates, "stop", a fragment): the brain
                # answers it with no PC-control tools (gateway.judged_chat -> intelligence._without_pc_tools_if_chat)
                gateway.note_chat(raw)
            is_pc = bool(decision and decision.kind != "chat") or known_workflow
    elif not is_pc:      # not one of the fixed wordings: let the local model understand a free-form app request (core.pcsemantic)
        try:
            from core import pcsemantic
            plan = pcsemantic.interpret(raw)
            if plan is not None and not pcsemantic.mentioned_apps(raw):
                # No app was named: the plan is a guess from the goal alone ("get jq going" -> open Paint, live 2026-11-06).
                # A skill Jarvis learned that fits the sentence wins over such a guess; the brain's learned-skill path takes it.
                from core import skillrouter
                if skillrouter.route(raw) is not None:
                    plan = None
            is_pc = plan is not None or pcsemantic.compound(raw)   # two requests in one sentence go to the planner
        except Exception:  # noqa: BLE001
            is_pc = False

    from core import pcledger
    # Pasted instructions (a ChatGPT answer, a README, a tutorial) with commands in them: jarvis_pc.guide reads them as
    # untrusted text, looks at the PC and shows a tiered plan; the owner's next "yes" runs exactly that plan.
    from jarvis_pc import guide as pcguide
    from tools import pcskills
    guide_text = pcguide.looks_like(raw)
    install_url = pcskills.github_install_request(raw)   # "install https://github.com/owner/repo": the release pipeline, on the owner's yes
    repo_url = "" if install_url or guide_text else pcskills.github_repo_request(raw)   # a repo link in any words ("get this going <link>")
    install_url = install_url or repo_url
    guide_text = guide_text or bool(install_url)
    if live and decision is not None and decision.kind != "guide":
        guide_text, install_url = False, ""            # the gateway's decision carries the guide; nothing is handled twice
    # A plain "yes" to a PC action waiting for approval authorizes and runs it — no UI, no dollar entry.
    pend = pend_guide = None
    answer = approvals.meant(users.current(), raw) if not is_pc and not guide_text else None
    if answer == "reask" and OFFER_FRESH.get() and _yes_to_an_offer(raw, history):
        return None
    if answer == "reask":              # (core.turnstate: a yes binds to the question just asked)
        from core import turnstate
        return turnstate.reask(users.current(), users.title())
    if answer == "yes":
        try:
            pcbridge.require_owner()
            pend_guide, pend = _yes_runs(users.current())
        except pcbridge.PCError:
            pend = pend_guide = None
    if not is_pc and not pend and not guide_text and not pend_guide and not choice:
        return None
    try:
        pcbridge.require_owner()
    except pcbridge.PCError as error:
        return str(error)
    from tools.pcview import control_pc, pc_command, see_pc_screen, stop_pc_control
    uid = users.current()
    approvals.on_user_message(uid, raw)
    with pcbridge.user_message(uid, raw):
        if pend_guide:   # their own yes to the guide or learned procedure they were shown
            from tools import pcskills
            misses.adopt("")      # the request behind this yes is not held here: nothing is written under the word "yes"
            with gateway.acknowledged(say, gateway.ack_opening(users.title(uid)) + ".", cancelled, "carrying out what you approved"):
                return pcskills.run_pending(uid)
        if choice:       # "the first one": the rest of the plan runs on what they chose
            misses.adopt(choice.get("task") or "")     # the request being finished, not the answer word
            out = pchybrid.run(choice["task"], cancelled, plan=choice["plan"], route="gateway", observed=choice["observed"])
            return out["detail"]
        if live and decision is not None and not pend and not power and not screen and not stop:
            text_out = gateway.execute(decision, raw, cancelled, say=say, began=began)
            return text_out
        if install_url and repo_url:   # loose words: the release if there is one, otherwise the repo's own README steps
            from core import screenguide
            return screenguide.install_repo(install_url, raw)
        if install_url:
            return pcskills.pc_install(install_url)
        if guide_text:
            return pcskills.pc_follow_guide(pcguide.goal_of(raw), raw)
        if pend:  # the owner's own "yes" authorizes this exact pending task, then it runs
            task = pend["data"].get("task")
            misses.adopt(task or "")                   # the request the yes approved, not the word "yes"
            _grant(pend["id"], pend["kind"])
            if not task:
                return "Approved — say the task again and I'll run it."
            with gateway.acknowledged(say, gateway.ack_opening(users.title(uid)) + ".", cancelled, "carrying out what you approved"):
                blocks = _run_with_consent(task, control_pc, allow=True)
        elif stop:
            return stop_pc_control()
        elif power == "wake":
            # Off PC cannot serve remote requests: send via always-on laptop LAN.
            from core import pcwake
            return pcwake.wake()
        elif power:
            blocks = pc_command("power", {"action": power, "delay_seconds": 0 if immediate else 30})
        elif screen:
            blocks = see_pc_screen()
        else:  # a PC task: autonomous by default — auto-clear cost/safe gates unless they said "ask me first"
            blocks = _run_with_consent(raw, control_pc, allow=approvals.pc_autonomous(uid))
    return " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
