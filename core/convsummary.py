"""A running summary of the older part of a conversation, both sides, so the local model still knows what was said AND
done once those exchanges no longer fit its window (core/contextfit).

Before, the exchanges that had to go became a recap of what HE said, one line per message: what Jarvis answered or did
was lost, so "what did you do for me earlier?" had no answer. Now, after a turn, when the next turn would drop
exchanges, the oldest batch (BATCH exchanges, never the last KEEP_RECENT) is written up as a few bullets: what he asked,
what Jarvis did or answered, decisions, his stated preferences, what is still open. Tool results go in as their first
line. The bullets are ADDED to the notes so far (the model sees those for context, it never rewrites them), so a fold
never loses an earlier note; over ROOM tokens the oldest notes go first, as the recap's oldest lines did.

Every bullet is checked: it must share at least two content words with the batch (one about something nobody said is a
guess, as in the journal), and a bullet about an exchange whose skill failed or only asked first must say so (his
request alone made "Jarvis set a reminder" look grounded). An exchange of the batch that no surviving bullet speaks for
gets its own plain line (his words, what Jarvis used or tried, Jarvis's first real sentence), so what is folded is
always in the notes; when the model cannot write usable notes, every exchange gets one.

The summary lives in the Brain (_summary, _summarized_upto), never on disk, and goes with the conversation (_archive).
It runs at background priority (core/inferencegate): after a turn, never during one or a game, and a call the owner's
next turn got in ahead of waits for the next gap. Only where the brain's model runs on this machine (the PC): on the
laptop the turns go to the PC's model over the network, and the recap stays.
"""
from __future__ import annotations

import os
import re
import threading

from core.words import plural   # counts read right in the prompt too: "at most 1 short bullet"

BATCH = 6           # exchanges folded at a time: the start of the dialogue moves rarely, so the cached prompt holds
KEEP_RECENT = 2      # the latest exchanges always stay word for word
ASK_BULLETS, ASK_WORDS = 6, 120   # what the model is asked to write for one batch
BULLET_WORDS = 50    # one note at most (a longer one is shortened, never dropped)
ROOM = 700           # tokens the whole summary may take: the room the recap of his words had (contextfit.RECAP_ROOM)
LINE_CHARS = 700     # one line of the transcript the model reads (a long reply is shortened, never left out)
TRANSCRIPT_CHARS = 12000
WAIT_S = 900.0       # how long a fold waits for a free card (his turns, a game) before leaving it to the next turn
MAX_PASSES = 4       # batches folded in one go at most (a backlog after a long game)

SYSTEM = "You keep Jarvis's running notes on the earlier part of a conversation, as the JSON asked for. No prose."
PROMPT = """Below is part of a conversation between {who} (USER) and Jarvis (JARVIS). Write the notes Jarvis needs to \
carry on the conversation, as JSON: {{"bullets": ["...", "..."]}}, at most {bullets} and {words} in \
all, covering: what {who} asked for, what Jarvis did or answered (a skill whose result failed, one that only asked \
first, or a reply marked as cut off did not get it done: say so), decisions made, preferences {who} stated, and \
anything still open (an offer of Jarvis's that {who} did not take up is not open). Use only what is written below, in \
its own words: never add a name, number or detail it does not contain.

{body}"""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["bullets"],
          "properties": {"bullets": {"type": "array", "items": {"type": "string"}}}}
_PEOPLE = "jarvis owner user sir"          # who the notes are about is not what they say (as journal._PARTICIPANTS)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
# A skill that could not do it: an error, or its own words saying so (intelligence._RESULT_FAILED); one that only asked
# for his yes first (core/approvals) did nothing yet either.
_ERROR = re.compile(r"^\s*error\b", re.I)
_ASKED_FIRST = re.compile(r"^\s*(?:NEEDS_CONFIRMATION|NOT_ASKED)\b")
_COULD_NOT = re.compile(r"\bi (?:couldn'?t|could not|can'?t|cannot|wasn'?t able|was unable|am unable|'?m unable|"
                        r"didn'?t manage)\b|\bfailed\b|\bdidn'?t work\b|\bnothing (?:was|got) (?:added|done|set|sent|saved)\b",
                        re.I)
# ...and a note about such an exchange is kept only when it says it was not done ("confirmed", "fixed the errors": done).
_NOT_DONE = re.compile(r"\b(?:fail(?:ed|s|ure)?|couldn'?t|could not|can'?t|cannot|unable|didn'?t|did not|not|never|"
                       r"declin\w*|refus\w*|tried|wasn'?t|was not|isn'?t|won'?t|pending|waiting|cut off|unfinished|"
                       r"(?:needs?|awaiting|asked for) (?:his |your |a )?(?:approval|confirmation|yes))\b", re.I)
_TITLE = re.compile(r"^(?:sir|ma'?am|boss|chief)$", re.I)

_running: set = set()                      # conversations being summarized now (one job per Brain)
_running_lock = threading.Lock()


def enabled() -> bool:
    """On the machine whose local model answers the turns (the PC), unless JARVIS_CONV_SUMMARY=off."""
    if os.getenv("JARVIS_CONV_SUMMARY", "on").strip().lower() in ("off", "0", "false", "no"):
        return False
    try:
        from core import intelligence, pcbridge
        return bool(intelligence.enabled() and intelligence.uses_ollama("fast")
                    and (pcbridge.local_pc() or not os.getenv("JARVIS_PC_URL")))
    except Exception:  # noqa: BLE001
        return False


# -- what is folded ----------------------------------------------------------------------------------------------------
def _first(m: dict) -> str:
    from core import contextfit
    return (contextfit._text(m).strip().splitlines() or [""])[0][:200]


def _outcomes(group: list[dict]) -> list[tuple[str, str, str]]:
    """(skill, 'ok' | 'failed' | 'asked', the first line of its result) for each call Jarvis made in the exchange."""
    from core import intelligence
    results = {m.get("tool_call_id"): _first(m) for m in group if m.get("role") == "tool"}
    out = []
    for m in group:
        for call in m.get("tool_calls") or []:
            first = results.get(call.get("id"), "")
            status = ("asked" if _ASKED_FIRST.match(first) else
                      "failed" if _ERROR.match(first) or intelligence._RESULT_FAILED.search(first) else "ok")
            out.append((str((call.get("function") or {}).get("name")), status, first))
    return out


def _reply(group: list[dict]) -> str:
    """Jarvis's last words in the exchange."""
    from core import contextfit
    return next((contextfit._text(m).strip() for m in reversed(group)
                 if m.get("role") == "assistant" and contextfit._text(m).strip()), "")


def _not_done(group: list[dict]) -> bool:
    """A skill of the exchange failed or only asked first, or Jarvis said he could not do it."""
    return any(status != "ok" for _, status, _ in _outcomes(group)) or bool(_COULD_NOT.search(_reply(group)))


def _clip_chars(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= LINE_CHARS else line[:LINE_CHARS - 1] + "…"


def _group_lines(group: list[dict]) -> list[str]:
    """One exchange (OpenAI format, as core/contextfit splits them) as transcript lines: his words, Jarvis's words, what
    Jarvis used and the first line of each result (marked when it failed). Never the notes Jarvis attaches (background,
    lookups). A long line is shortened, so every exchange of a batch reaches the model."""
    from core import asides, contextfit, intelligence
    failed = {first for _, status, first in _outcomes(group) if status == "failed"}
    out = []
    for m in group:
        role = m.get("role")
        if role == "tool":
            first = _first(m)
            if first:
                out.append(f"  (result{', it failed' if first in failed else ''}: {first})")
            continue
        for p in contextfit._parts(m):
            text = p.get("text", "") if p.get("type") == "text" else ""
            if text.lstrip().startswith(asides.TAG):
                said = asides.lines(text)
                if said:
                    out.append(_clip_chars("JARVIS (unprompted): " + " / ".join(said)))
            elif text.strip() and not text.lstrip().startswith(contextfit._INJECTED):
                words = intelligence._CHANNEL_NOTE.sub("", text).strip()
                if words:
                    out.append(_clip_chars(f"{'USER' if role == 'user' else 'JARVIS'}: {words}"))
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            out.append(f"  (Jarvis used {fn.get('name')}: {str(fn.get('arguments') or '')[:160]})")
    return out


def _lines(groups: list[list[dict]]) -> list[str]:
    """Both sides of the exchanges, as transcript lines (see _group_lines)."""
    return [line for group in groups for line in _group_lines(group)]


def _clip(text: str, words: int) -> str:
    parts = " ".join(str(text or "").split()).split(" ")
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _progress(sentence: str) -> bool:
    """'Give me a second, sir, I'm working on it.': said while the task ran, before its outcome (intelligence.is_lead_in,
    clause by clause)."""
    from core import intelligence
    clauses = [c.strip() for c in re.split(r",\s*", sentence.strip().rstrip(".!…")) if c.strip()]
    clauses = [re.sub(r"^(?:i'?m|i am)\s+", "", c, flags=re.I) for c in clauses if not _TITLE.match(c)]
    return bool(clauses) and all(intelligence.is_lead_in(c) for c in clauses)


def _plain_line(group: list[dict]) -> str:
    """A note made from the exchange itself: his words, what Jarvis used (or tried, and how that ended), and Jarvis's
    first sentence that is not a progress line. Grounded by construction."""
    from core import contextfit
    said = contextfit._said(group)[2:]
    did = []
    for name, status, first in _outcomes(group):
        did.append(f"used {name}" if status == "ok" else f"asked before {name} (not done)" if status == "asked"
                   else f"tried {name}, which failed ({_clip(first, 10)})")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", _reply(group)) if s.strip()]
    reply = next((s for s in sentences if not _progress(s)), sentences[0] if sentences else "")
    if not (said or reply):
        return ""
    line = f"They said: {_clip(said, 12)}" if said else "Jarvis spoke"
    if did:
        line += "; Jarvis " + ", ".join(dict.fromkeys(did))
    if reply:
        line += f"; Jarvis: {_clip(reply, 12)}"
    return line


def _plain(groups: list[list[dict]]) -> list[str]:
    """Notes made from the exchanges themselves (see _plain_line)."""
    return [line for line in map(_plain_line, groups) if line]


# -- the notes ---------------------------------------------------------------------------------------------------------
def _clean(bullet) -> str:
    return " ".join(_BULLET.sub("", str(bullet or "")).split())


def _bullets(summary: str) -> list[str]:
    return [b for b in map(_clean, (summary or "").splitlines()) if b]


def _grounded(bullet: str, source: str, who: str = "") -> bool:
    """At least two of the bullet's content words occur in what was folded (who it is about does not count)."""
    from core.intelligence import _words
    people = _words(f"{who} {_PEOPLE}", hints=False)
    words = {w for w in _words(bullet, hints=False) if len(w) >= 4} - people
    return len(words & _words(source, hints=False)) >= 2


def _bounded(bullets: list[str]) -> str:
    """'- ' lines within ROOM tokens, the newest kept (as contextfit's recap keeps the newest of his lines); one note at
    most BULLET_WORDS words. Never nothing while there is a note."""
    from core import contextfit
    chosen: list[str] = []
    for b in reversed([_clip(b, BULLET_WORDS) for b in bullets if b]):
        if chosen and contextfit.tokens([{"content": "\n".join(f"- {x}" for x in [b, *chosen])}]) > ROOM:
            break
        chosen.insert(0, b)
    return "\n".join(f"- {b}" for b in chosen)


def _ask(prompt: str, cancelled) -> list[str] | None:
    """The local model's bullets ([] when it wrote nothing usable); None when his turn has the card (try after it)."""
    from core import intelligence, journal
    if cancelled():
        return None
    schema = {"type": "json_schema", "json_schema": {"name": "jarvis_conversation_notes", "strict": True, "schema": SCHEMA}}
    try:
        # A turn that got the card first is never queued behind the notes: the call gives up at once instead.
        with intelligence.background_inference():
            reply = intelligence.request("fast", SYSTEM, [{"role": "user", "content": prompt}], [],
                                         response_format=schema)
    except intelligence.LocalFailure as error:
        if str(error) in ("local_model_busy", "cancelled"):
            return None
        raise
    data = journal._notes_object("".join(getattr(b, "text", "") for b in reply.content))
    bullets = data.get("bullets") if isinstance(data, dict) else None
    return [str(b) for b in bullets if isinstance(b, str)] if isinstance(bullets, list) else []


def fold(previous: str, groups: list[list[dict]], who: str = "the owner", ask=None,
         cancelled=lambda: False) -> str | None:
    """The summary so far plus notes on these exchanges, as '- ' lines within ROOM tokens. Every exchange folded is in
    it (a bullet that speaks for it, or its plain line) and no earlier note is lost to it (only, over ROOM, to age).
    None: not now (a turn of his has the card); the same fold is tried after it."""
    each = [_group_lines(g) for g in groups]
    if not any(each):
        return previous or ""
    transcript = "\n".join(line for lines in each for line in lines)[-TRANSCRIPT_CHARS:]
    body = (f"Notes already kept on what came before (context only: do not repeat them):\n{previous}\n\n"
            f"Write the notes for this part:\n{transcript}") if previous else transcript
    try:
        prompt = PROMPT.format(who=who, bullets=plural(ASK_BULLETS, "short bullet"), words=plural(ASK_WORDS, "word"),
                               body=body)
        bullets = (ask or _ask)(prompt, cancelled)
    except Exception as error:  # noqa: BLE001  (no model: the notes come from the exchanges themselves)
        from core import diag
        diag.swallowed("conversation summary", error, every=600)
        bullets = []
    if bullets is None:
        return None
    earlier = _bullets(previous)
    known = {b.casefold() for b in earlier}
    sources = ["\n".join(lines) for lines in each]
    undone = [src for g, src in zip(groups, sources) if _not_done(g)]
    kept = []
    for b in map(_clean, bullets):
        if not b or b.casefold() in known or not _grounded(b, transcript, who):
            continue                   # empty, an earlier note restated, or about something nobody said here
        if not _NOT_DONE.search(b) and any(_grounded(b, src, who) for src in undone):
            continue                   # "Jarvis set a reminder" when the skill failed or only asked first
        known.add(b.casefold())
        kept.append(b)
    missing = [_plain_line(g) for g, src in zip(groups, sources) if src and not any(_grounded(b, src, who) for b in kept)]
    return _bounded(earlier + kept + [line for line in missing if line])


# -- the Brain's side --------------------------------------------------------------------------------------------------
def _who(brain) -> str:
    try:
        from core import users
        return users.get(brain.uid).get("name") or "the owner"
    except Exception:  # noqa: BLE001
        return "the owner"


def _plan(brain):
    """(conversation, summary, upto, the exchanges to fold) for the next batch, or None when the next turn loses none."""
    from core import contextfit, intelligence
    conversation, summary, upto = brain._conversation, brain._summary, brain._summarized_upto
    messages = list(brain.messages)
    if not messages:
        return None
    try:
        local = intelligence.messages_for_local(brain.system[0]["text"], messages)
    except Exception:  # noqa: BLE001  (an unreadable message: leave it to the recap)
        return None
    if not contextfit.dropped(local, summary, upto):
        return None
    groups = contextfit.exchanges(local[1:])
    batch = min(BATCH, len(groups) - upto - KEEP_RECENT)
    if batch <= 0:
        return None
    return conversation, summary, upto, groups[upto:upto + batch]


def update(brain, ask=None, wait: float | None = WAIT_S) -> bool:
    """Fold batches into the brain's summary while the next turn would drop exchanges. True when it changed."""
    from core import inferencegate
    changed = False
    for _ in range(MAX_PASSES):
        if _plan(brain) is None:
            return changed
        with inferencegate.background("summary", timeout=wait) as slot:
            if not slot:
                return changed                         # no gap in time (his turns, a game): the next turn tries again
            plan = _plan(brain)                         # what a turn added while it waited
            if plan is None:
                return changed
            conversation, summary, upto, groups = plan
            new = fold(summary, groups, _who(brain), ask=ask, cancelled=slot.cancelled)
        if new is None:
            return changed
        with brain._lock:                               # between turns: a turn's rounds never see it change under them
            if brain._conversation != conversation or brain._summarized_upto != upto:
                return changed                          # archived, or folded meanwhile
            brain._summary, brain._summarized_upto = new, upto + len(groups)
        changed = True
    return changed


def after_turn(brain) -> None:
    """Called when a turn ends: summarize in the background if the next turn would drop exchanges."""
    if not enabled() or not getattr(brain, "messages", None):
        return
    key = id(brain)
    with _running_lock:
        if key in _running:
            return
        _running.add(key)

    def run():
        try:
            update(brain)
        except Exception as error:  # noqa: BLE001  (the recap is still there)
            from core import diag
            diag.swallowed("conversation summary", error, every=600)
        finally:
            with _running_lock:
                _running.discard(key)
    try:
        threading.Thread(target=run, daemon=True, name="conv-summary").start()
    except RuntimeError:            # no thread to be had: the turn is over all the same, and the recap stays
        with _running_lock:
            _running.discard(key)
