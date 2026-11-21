"""Fit a conversation into the local model's context window, cutting what matters least first.

The local models run with num_ctx 16384 tokens. Jarvis's instructions take ~7,800 of them and the core background
notes (date, who's talking, identity and pinned facts, the latest chats, lessons: tools/memory._core) ~1,000 (the
rest of his memory comes with each question), plus, within two hours of using his other machine, its last exchanges
(core.thread, outside the core budget, at most ~470: thread.BACKGROUND_CHARS); a turn that may use a tool adds the tool
definitions his words point at (up to 16, ~2,800). When a prompt overflowed, Ollama silently dropped the OLDEST messages, which is where the
background notes live, and the model then invented what it had lost (asked for a code word it no longer had, it
answered "HELLO"). So the request is trimmed here instead, in this order:

  always       the background notes move into the system message (never cut); memory lookups attached to earlier
               questions go (only the current question's lookup matters); one tool result is capped at 8,000 chars
  summarized   the exchanges the running summary covers (core/convsummary: both sides, folded 6 at a time after a
               turn) are replaced by that summary, one block at the start of the dialogue
  over budget  tool results older than the last two exchanges shrink to their first line
  still over   the oldest exchanges go, two at a time; one recap line per message keeps what THEY said in them

The budget doesn't depend on whether THIS turn offers tools (a fixed allowance is kept for them), so a conversation
is trimmed the same way on small talk and on requests, and Ollama's cached prompt stays valid between them.
Only what the local model is sent is trimmed: Jarvis's own history, the journal and the cloud tiers keep everything.
"""
from __future__ import annotations

import json
import re

CONTEXT = 16384          # num_ctx the local models run with (core.intelligence._ollama_body)
REPLY_ROOM = 700         # room left for the spoken reply (they're short; a long one must not overflow mid-sentence)
BUDGET = CONTEXT - REPLY_ROOM
TOOL_ROOM = 2800         # ~16 tool definitions on the first round of a turn, or that turn's tool results after it
QUESTION_ROOM = 700      # the current question and its memory lookup, typically (a bigger one: see the last step)
RESULT_CAP = 8000        # chars of one tool result (~1,900 tokens: fits in TOOL_ROOM)
PREV_ROOM = 500          # extra tokens to show the exchange before the current one whole (its tool results)
DROP_STEP = 2            # exchanges dropped at a time (fewer re-reads than one by one)
# A chat turn (core.chatturn) sends the short persona instead of the ~7,800-token manual and no tools, so the window has
# room for the conversation itself: up to CHAT_HISTORY tokens of earlier exchanges, shown WHOLE (no summary in their
# place) while they fit. Its reply may be a real answer (core.intelligence.PROFILES: up to 480 tokens, plus the capped
# reasoning before it), so more room is kept for it.
CHAT_HISTORY = 8000
CHAT_REPLY_ROOM = 1300
# <tool-images>: the note core.intelligence puts before a screenshot a tool returned (not a question of theirs).
# <said-unprompted>: what Jarvis said unasked before their message (core.asides); it stays in history, like his replies.
# <recently-elsewhere>: what was said on the other machine since the conversation began (core.thread.news).
_INJECTED = ("<background>", "<retrieved-memory>", "<daily-awareness>", "<tool-images>", "<said-unprompted>",
             "<recently-elsewhere>")
_LOOKUPS = ("<retrieved-memory>", "<daily-awareness>")
RECAP_ROOM = 700         # tokens for the recap of what they said in the exchanges that had to go
RECAP_HEAD = "[Earlier in this conversation (shortened to save space), they said, oldest first:"
# The running summary (core/convsummary) of the oldest exchanges, both sides: what they asked AND what Jarvis did.
SUMMARY_HEAD = "[Earlier in this conversation (reference only; answer only their latest message):"


def tokens(messages: list[dict], tools: list | tuple = ()) -> int:
    """Llama 3 tokens, estimated (measured: ~4.4 chars per token for text, ~3.7 for tool JSON; this errs high)."""
    n = 0.0
    for m in messages:
        n += 14                                            # role header and end-of-turn
        n += len(_text(m)) / 4.3
        n += sum(len(json.dumps(c.get("function", {}))) for c in m.get("tool_calls") or []) / 3.6
    if tools:
        n += 60 + sum(len(json.dumps(t)) for t in tools) / 3.6
    return int(n)


def _parts(m: dict) -> list[dict]:
    c = m.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return [p for p in c or [] if isinstance(p, dict)]


def _text(m: dict) -> str:
    return "\n".join(p.get("text", "") for p in _parts(m) if p.get("type") == "text")


def _question(m: dict) -> bool:
    """A user message with something they actually said (not tool output, not only attached context)."""
    return m.get("role") == "user" and any(
        p.get("type") == "text" and p.get("text", "").strip() and not p["text"].lstrip().startswith(_INJECTED)
        for p in _parts(m))


def _exchanges(messages: list[dict]) -> list[list[dict]]:
    """Split at each question: a question, the tool rounds it caused and the answer stay together."""
    out: list[list[dict]] = []
    for m in messages:
        if _question(m) or not out:
            out.append([])
        out[-1].append(m)
    return out


def _without(m: dict, prefixes: tuple) -> dict:
    parts = _parts(m)
    keep = [p for p in parts if not (p.get("type") == "text" and p.get("text", "").lstrip().startswith(prefixes))]
    return m if len(keep) == len(parts) else {**m, "content": keep}


def _shortened(m: dict) -> dict:
    first = (_text(m).strip().splitlines() or [""])[0][:200]
    return {**m, "content": f"{first} … (older result, shortened to save space)"}


def _said(group: list[dict]) -> str:
    """What they said in an exchange, as one recap line (their words only: not the lookups, not app notes)."""
    words = " ".join(p.get("text", "") for p in _parts(group[0]) if p.get("type") == "text"
                     and not p.get("text", "").lstrip().startswith(_INJECTED)) if group and _question(group[0]) else ""
    words = " ".join(re.sub(r"^\s*\[[^\]]*\]\s*", "", words).split())
    return f"- {words[:150]}{'…' if len(words) > 150 else ''}" if words else ""


def _recap(dropped: list[list[dict]]) -> str:
    """Their own words from the dropped exchanges, newest kept if even this gets long. An 8B model ignored every
    'earlier parts were removed, don't guess' note and said "you didn't give me a code word"; with their words
    still here it simply has the answer."""
    lines = [line for line in map(_said, dropped) if line]
    while len(lines) > 1 and tokens([{"content": "\n".join([RECAP_HEAD, *lines, "]"])}]) > RECAP_ROOM:
        lines = lines[1:]
    return "\n".join([RECAP_HEAD, *lines, "]"]) if lines else ""


def summary_block(summary: str) -> str:
    return f"{SUMMARY_HEAD}\n{summary.strip()}\n]"


def _prepared(messages: list[dict]) -> tuple[dict, list[dict]]:
    """The always-rules: background into the system message, earlier lookups out, giant tool results capped."""
    system, rest = dict(messages[0]), [dict(m) for m in messages[1:]]
    pinned = [p["text"] for m in rest if m.get("role") == "user" for p in _parts(m)
              if p.get("type") == "text" and p.get("text", "").lstrip().startswith("<background>")]
    if pinned:
        system["content"] = f"{system.get('content') or ''}\n\n" + "\n\n".join(pinned)
        rest = [_without(m, ("<background>",)) if m.get("role") == "user" else m for m in rest]
    asked_at = max((i for i, m in enumerate(rest) if _question(m)), default=len(rest))
    rest = [_without(m, _LOOKUPS) if m.get("role") == "user" and i < asked_at else m for i, m in enumerate(rest)]
    rest = [{**m, "content": _text(m)[:RESULT_CAP] + "\n… (cut: too long to keep whole)"}
            if m.get("role") == "tool" and len(_text(m)) > RESULT_CAP else m for m in rest]
    return system, rest


def _folded(earlier: list[list[dict]], summary: str | None, upto: int) -> tuple[list[str], list[list[dict]]]:
    """([the summary block] or [], the earlier exchanges it does not cover). It covers the first `upto` (a summary that
    covers nothing is not shown)."""
    n = min(max(int(upto or 0), 0), len(earlier)) if summary and str(summary).strip() else 0
    return ([summary_block(summary)] if n else []), earlier[n:]


def _cut(system: dict, head: list[str], earlier: list[list[dict]], tools, room: int | None = None) -> tuple[int, list | None]:
    """How many of the earlier exchanges must go, and those exchanges with their tool results shortened (None: they all
    fit whole). Decided by the EARLIER exchanges only: they're the same on every round of a turn (the tool results coming
    in don't move it, so the answer after a tool reuses the cached prompt) and they only ever grow."""
    lead = [{"content": h} for h in head]
    if room is None:
        room = BUDGET - max(TOOL_ROOM, tokens([], tools)) - QUESTION_ROOM
    if tokens([system] + lead + _flat(earlier)) <= room:
        return 0, None
    # Over: decide on sizes that never change as the chat grows (every earlier exchange shortened, plus a fixed
    # allowance for showing the last one whole), so the cut only ever moves forward. Measured on the real exchange
    # sizes instead, it went back and forth and Ollama re-read the conversation nearly every turn.
    short = [[_shortened(m) if m.get("role") == "tool" else m for m in g] for g in earlier]
    gone = 0
    while gone < len(short) and (tokens([system, *lead, {"content": _recap(earlier[:gone])}] + _flat(short[gone:]))
                                 > room - PREV_ROOM):
        gone += DROP_STEP
    return gone, short


def _chat_room(system: dict) -> int:
    """Tokens a chat turn may spend on the system message plus the earlier exchanges."""
    return min(CONTEXT - CHAT_REPLY_ROOM - QUESTION_ROOM, tokens([system]) + CHAT_HISTORY)


def fit(messages: list[dict], tools: list | tuple = (), summary: str | None = None, upto: int = 0,
        chat: bool = False) -> list[dict]:
    """The messages (OpenAI format, system first) to send the local model. Doesn't change the list it's given.

    summary/upto: the running summary of this conversation (core/convsummary) and how many of its first exchanges it
    covers. Those exchanges are replaced by one block, '[Earlier in this conversation (reference only; ...): ...]', at the
    start of the dialogue; it moves only when the summary is extended (a batch at a time), so the cached prompt rarely
    changes. Whatever still does not fit after it goes as before, with the recap of their words.

    chat: a chat turn (short system prompt, no tools). The earlier exchanges are shown whole, with no summary in their
    place, while they fit in CHAT_HISTORY: the conversation only grows at its end, so the cached prompt holds. Past
    that, the summary and the cut apply as above, with the chat turn's larger room."""
    if not messages or messages[0].get("role") != "system":
        return messages
    system, rest = _prepared(messages)
    *earlier, current = _exchanges(rest) or [[]]
    room = budget = None
    if chat:
        room, budget = _chat_room(system), CONTEXT - CHAT_REPLY_ROOM
        if tokens([system] + _flat(earlier)) <= room and tokens([system] + _flat(earlier) + current) <= budget:
            return [system] + _flat(earlier) + current
    budget = budget or BUDGET
    head, earlier = _folded(earlier, summary, upto)
    gone, short = _cut(system, head, earlier, tools, room)
    if short is None:
        return [system] + _opened(earlier, current, head)
    kept = short[gone:]
    if kept and tokens(earlier[-1]) - tokens(short[-1]) <= PREV_ROOM:
        kept[-1] = earlier[-1]                       # the exchange just before this one stays whole (follow-ups)
    # A turn bigger than the allowances (a long memory lookup, several big results): drop more, for this request only.
    lead = [{"content": h} for h in head]
    while kept and tokens([system] + lead + _flat(kept) + current, tools) > budget:
        kept, gone = kept[DROP_STEP:], gone + DROP_STEP
    recap = _recap(earlier[:gone])
    return [system] + _opened(kept, current, head + ([recap] if recap else []))


def dropped(messages: list[dict], summary: str | None = None, upto: int = 0) -> int:
    """How many exchanges after the summarized ones the NEXT turn would lose to the cut (0: they all fit). `messages` is
    a finished conversation (OpenAI format, system first); the next question is the usual QUESTION_ROOM."""
    if not messages or messages[0].get("role") != "system":
        return 0
    system, rest = _prepared(list(messages) + [{"role": "user", "content": "?"}])
    *earlier, _next = _exchanges(rest) or [[]]
    head, earlier = _folded(earlier, summary, upto)
    return _cut(system, head, earlier, ())[0]


def exchanges(messages: list[dict]) -> list[list[dict]]:
    """A conversation (OpenAI format, no system message) split into exchanges, as fit() counts them for `upto`."""
    return _exchanges(list(messages))


def _opened(groups: list[list[dict]], current: list[dict], blocks: list[str]) -> list[dict]:
    """The groups and the current exchange as one list, with `blocks` before the first message's own words."""
    start = groups[0] if groups else current
    if blocks and start:
        start[0] = {**start[0], "content": [{"type": "text", "text": b} for b in blocks] + _parts(start[0])}
    return _flat(groups) + current


def _flat(groups: list[list[dict]]) -> list[dict]:
    return [m for g in groups for m in g]


def guard(messages: list[dict], tools: list | tuple = ()) -> list[dict]:
    """The last line of defence before a call to the local model: no prompt may be bigger than the window, because Ollama
    then cuts it from the FRONT without a word (the rules go first). Whatever called, an oversized prompt loses its oldest
    exchanges whole (a tool call stays with its result) until it fits; the system message and the newest exchange (his
    line, with the tool rounds it caused) are never touched. A prompt that fits is returned as it is (the same list).
    If only the newest exchange is left and it still does not fit, that is returned too and the caller refuses it."""
    if not messages or tokens(messages, tools) <= BUDGET:
        return messages
    lead = [messages[0]] if messages[0].get("role") == "system" else []
    groups = _exchanges(messages[len(lead):])
    while len(groups) > 1 and tokens(lead + _flat(groups), tools) > BUDGET:
        groups = groups[1:]
    return lead + _flat(groups)
