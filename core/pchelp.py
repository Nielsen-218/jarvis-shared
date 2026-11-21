"""Failure that helps (2026-11-12). One place writes what the owner hears when a PC request could not be done, and one place
looks for the next sensible method before giving up.

The audit of his real conversations found about 26 of 430 turns ending in a policy sentence ("Known safe local methods
failed", "stopped without a paid call", "Local intelligence is unavailable"): true, and useless to him. Every line here
says, in plain words, WHAT could not be done, that nothing else was touched, and what he can say next. None mentions paid
AI, tiers, budgets or "local methods". Nothing here runs anything: next_plan() only RETURNS a typed plan, which the caller
sends through core.pchybrid.run and so through the same authorization (core.authz) and guards as any other plan.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# What he can do next, per kind of step. Short: the owner, 2026-11-07: "he says too much".
_NEXT = {
    "open_app": "Tell me the name it has in the Start menu and I'll try that.",
    "open_path": "Tell me the folder it is in and I'll look there.",
    "close_app": "It may be waiting on a save prompt; have a look and tell me what to do with it.",
    "close_foreground": "It may be waiting on a save prompt; have a look and tell me what to do with it.",
    "minimize_app": "Tell me which window you mean and I'll try that one.",
    "focus_app": "If it isn't running, ask me to open it.",
    "type_text": "Click where the text should go and ask me again.",
}
_DEFAULT_NEXT = "Tell me the first step you'd take and I'll follow it."
_WAYS = {1: "one way", 2: "two ways", 3: "three ways", 4: "four ways"}


def _what(step: dict) -> str:
    try:
        from core import pchybrid
        return pchybrid.spoken_plan({"steps": [step]}) or str(step.get("tool", "that")).replace("_", " ")
    except Exception:  # noqa: BLE001 - wording never breaks a failure report
        return str((step or {}).get("tool", "that")).replace("_", " ")


def next_step(tool: str) -> str:
    """The one-line "what you can say next" for a kind of step ('' when there is no specific one)."""
    return _NEXT.get(tool or "", "")


def with_next(tool: str, reason: str) -> str:
    """A step's own failure sentence ("I pasted the text into Notepad, but reading the window back I don't see it") plus
    what he can do next, for the kinds of step that have a specific hint (type_text): the sentence alone left him stuck
    (audit-pc-control defect 7)."""
    reason = " ".join((reason or "").split())
    hint = next_step(tool)
    if not reason or not hint or hint.casefold() in reason.casefold():
        return reason
    return reason.rstrip(".") + ". " + hint


def step_failed(step: dict, attempts: Iterable[dict] = ()) -> str:
    """A step that no method could do or verify: what it was, how many ways were tried, what he can do next. Never the
    exception text (it could carry a path or internals) and never a policy sentence."""
    tried = len([a for a in attempts if isinstance(a, dict)])
    ways = f" I tried {_WAYS.get(tried, 'several ways')}." if tried > 1 else ""
    return f"I couldn't {_what(step)}.{ways} {_NEXT.get((step or {}).get('tool', ''), _DEFAULT_NEXT)}"


# Reasons in Decision.attempts that mean the model never gave an answer (busy, timed out, down), as opposed to an answer
# the validator refused: "advanced: unavailable", "advanced: LocalFailure", "advanced: TimeoutError".
_MODEL_SILENT = re.compile(r":\s*(?:unavailable|too slow(?: \([^)]*\))?|\w*(?:Failure|Timeout|TimeoutError|ConnectionError|URLError|OSError|Busy)\w*)\s*$", re.I)


def model_was_silent(attempts: Iterable[str]) -> bool:
    rows = [a for a in attempts or () if isinstance(a, str) and not a.startswith("fast:")]
    return bool(rows) and all(_MODEL_SILENT.search(a) for a in rows)


_IS_A_QUESTION = re.compile(r"^\W*(?:(?:so|well|okay|ok|and|but|hey jarvis|jarvis)[,.\s]+)*(?:what|which|who|whom|where|when|why|how much|how many|how come|"
                            r"is|are|was|were|did|does)\b", re.I)


def no_plan(words: str, title: str = "sir", attempts: Iterable[str] = ()) -> str:
    """Nothing could be planned and nothing ran. Says so, and what to say next."""
    words = " ".join((words or "").split()).strip(" .,!?;:")
    if _IS_A_QUESTION.match(words) and not model_was_silent(attempts):
        # "What was the title of the window you saw?" -> "I couldn't work out how to do that" (live 2026-11-16): a question is not a job to do.
        return (f"I don't have that, {title}, and I won't guess. Ask me again another way, or tell me what you want done.")
    if model_was_silent(attempts):
        return (f"I was slow working that out, {title}, so I haven't touched anything. "
                "Say it once more, or name the app and what to do with it.")
    if words and len(words.split()) <= 8:
        return (f"I couldn't work out how to do '{words}', {title}, so I haven't touched anything. "
                "Name the app or the first step and I'll take it from there.")
    return (f"I couldn't work out how to do that, {title}, so I haven't touched anything. "
            "Give it to me one step at a time and I'll follow.")


def pc_out_of_reach(title: str = "sir", ran: bool = False) -> str:
    """The laptop's thinking runs on the PC and the PC does not answer. Until 2026-11-19 this turn ended in "I lost my
    train of thought ... say it once more", which was not what happened and got the same line again. The owner is told
    the one thing that works without the model, when it is set up: 'wake my PC' is the laptop's own Wake-on-LAN
    (core.pcfast -> core.pcwake).
    ran: a skill was called in this turn before the PC stopped answering (only the wording of its result was lost).
    Then never "ask me again": he would repeat something that may already have happened. The call may also have failed
    or been refused, which leaves the same trace, so the line claims no more than "started" (review 2026-11-19)."""
    import os
    if ran:
        return (f"I can't reach the PC right now, {title}, and my thinking runs there, so I can't tell you how that went. "
                "I had already started on it before I lost the PC: check what happened before you ask again.")
    line = f"I can't reach the PC right now, {title}, and my thinking runs there. It may be asleep, off or restarting"
    try:
        from core import users
        owner = users.get(users.current()).get("role") == "owner"
    except Exception:  # noqa: BLE001 - no offer rather than a wrong one
        owner = False
    if owner and os.getenv("JARVIS_PC_WAKE_MAC"):
        return line + ". Say 'wake my PC' and I'll try to wake it."
    return line + ", so ask me again once it is back."


def model_silent(title: str = "sir", ran: bool = False) -> str:
    """The conversation model returned nothing usable (brain.llm). No tiers, no 'cloud inference is disabled'. Claims
    nothing about what did or did not run: a tool may already have reported its own result this turn (`ran`: one did)."""
    try:
        from core import pcbridge
        if pcbridge.brain_unreachable():             # the PC that thinks did not answer: say that (pc_out_of_reach)
            return pc_out_of_reach(title, ran)
    except Exception:  # noqa: BLE001 - the old line stands
        pass
    try:
        from core import intelligence
        if intelligence.take_refused_promise():
            return (f"I haven't started that, {title}, and I won't pretend I have. Tell me the first step and I'll do it, "
                    "or ask me to open the app and we'll go from there.")
    except Exception:  # noqa: BLE001 - the old line stands
        pass
    return f"I lost my train of thought there, {title}. Could you say it once more?"


# ---------------------------------------------------------------------------------------------- the next sensible method
# The model could not plan it. Before giving up: take the polite wrapper off the sentence and try the deterministic parser
# (core.pchybrid.simple_plan) on what is left. "Could you open Word for me, please" is "open Word".
_WRAP_FRONT = re.compile(
    r"^(?:(?:hey\s+)?jarvis[,.!\s]+)?(?:(?:ok(?:ay)?|alright|so|now|and|please|yeah|yes)[,\s]+)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+|just\s+)?(?:go\s+ahead\s+and\s+)?"
    r"|i\s+(?:want|need|would\s+like|'d\s+like)\s+you\s+to\s+(?:please\s+)?"
    r"|(?:please\s+)?go\s+ahead\s+and\s+"
    # never "I need to ..." / "I want to ..." / "let's ...": what HE is about to do is narration (review 2026-11-12:
    # "I need to start the car" opened Camera, "Let's start fresh" opened Files)
    r"|please\s+|just\s+)+", re.I)
_WRAP_BACK = re.compile(r"(?:[,\s]+(?:for\s+me|please|now|real\s+quick|quickly|right\s+now|if\s+you\s+(?:can|could|would)|"
                        r"thanks|thank\s+you|sir|jarvis))+[\s.!?]*$", re.I)
# Only steps that are easy to undo are ever proposed here. A close is NOT: when the model could not understand the
# sentence, guessing a close from what is left of it is the dangerous direction.
_FALLBACK_TOOLS = frozenset({"open_app", "minimize_app", "media_command", "set_volume", "change_volume"})
# An app step is accepted only when what he said after the verb is EXACTLY one installed app's name (core.heard.exact_app):
# tools.apps.resolve_app is fuzzy ("the car" -> Camera, "recording" -> a recording manager), fine behind a review, not here.
_APP_REQUEST = re.compile(r"^(?:open|launch|start|run|pull\s+up|bring\s+up|fire\s+up|minimi[sz]e|hide)\s+(?P<obj>.+)$", re.I)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (text or "").casefold())).strip()


def _exactly_named(task: str, step: dict) -> bool:
    from core import heard
    m = _APP_REQUEST.match(task)
    if not m or not heard.exact_app(m.group("obj")):
        return False
    said = re.sub(r"\s+(?:app|window)$", "", re.sub(r"^(?:the|my)\s+", "", _flat(m.group("obj"))))
    return _flat(str((step.get("arguments") or {}).get("app") or "")) == said
_QUESTION = re.compile(r"^(?:what|what's|whats|where|when|who|why|how|which|is|are|was|were|do|does|did|should|have|has)\b", re.I)


def unwrap(said: str) -> str:
    text = " ".join((said or "").split())
    for _ in range(3):
        shorter = _WRAP_BACK.sub("", _WRAP_FRONT.sub("", text)).strip(" ,")
        if shorter == text:
            break
        text = shorter
    return text.rstrip(".!?").strip()


def next_plan(said: str) -> Optional[tuple]:
    """-> (task, plan) or None. A deterministic plan for the sentence with its polite wrapper removed, when that differs from
    what was already tried (the exact lane parsed the sentence as heard) and every step is easy to undo. A sentence that is
    still a question after unwrapping is never turned into an action."""
    base = " ".join((said or "").split()).rstrip(".!?").strip()
    task = unwrap(said)
    if not task or task.casefold() == base.casefold() or _QUESTION.match(task) or len(task.split()) > 8:
        return None
    try:
        from core import pchybrid
        plan = pchybrid.simple_plan(task, cached=False)
    except Exception:  # noqa: BLE001 - a fallback that fails is a miss, never a lost turn
        return None
    steps = (plan or {}).get("steps") or []
    if not steps or any(s.get("tool") not in _FALLBACK_TOOLS for s in steps):
        return None
    try:
        if any(s.get("tool") in ("open_app", "minimize_app") and not _exactly_named(task, s) for s in steps):
            return None
    except Exception:  # noqa: BLE001 - no catalogue: fail closed
        return None
    return task, plan
