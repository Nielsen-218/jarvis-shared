"""Jarvis's short lines said in CODE (canned small talk, the goodbye when he dismisses the conversation, the acknowledgement
said instead of his own word back), written by the same rules as the character sheet (core/character.md): contractions,
plain words, no "Very good" / "Standing by" butler talk, no help-desk lines, never the same line twice in a row, and his
title on about one line in three (his choice 2026-11-14: "sir" with greetings and finished orders, not in every reply).

Wave 3b (2026-11-21): the chat persona, the manual and these lines described three different voices; the live log of
2026-11-20 had "Very good, sir." / "Of course, sir." / "Standing by." after commands that had done nothing.
"""
from __future__ import annotations

import threading

# Lines that claim nothing was done: an acknowledgement, never "Of course" (it sounded like "I'll do it" to "End it.").
LINES = {
    "thanks": ("Any time.", "My pleasure.", "Not at all.", "Sure thing.", "Glad to."),
    "ok": ("Got it.", "Okay.", "Sure.", "Right."),
    "decline": ("Sure.", "No problem.", "Fair enough.", "All right."),
    "react": ("Good.", "Glad to hear it.", "Nice.", "Good to hear."),
    "social": ("I'm here.", "Right here.", "Here."),
    "yes": ("Yes to what?", "Yes to which part?"),
    "dismiss": ("Okay.", "Sure.", "Talk later.", "Got it."),
    "dismiss_night": ("Good night.", "Night.", "Sleep well."),
    "fallback": ("Mm-hm.", "Okay.", "Sure."),
    # said in place of his own acknowledgement word echoed back ("Right." -> "Right, sir."): never a butler's word
    "ack": ("Got it", "Right", "Okay", "Sure", "Fair enough", "Noted"),
}
TITLE_EVERY = 3            # the title goes on one line in three

_lock = threading.Lock()
_last: dict[str, str] = {}
_count = [0]


def titled(text: str, title: str) -> str:
    """'Any time.' -> 'Any time, sir.' ('Yes to what?' -> 'Yes to what, sir?')."""
    text = text.strip()
    if not title or not text:
        return text
    end = text[-1] if text[-1] in ".!?" else ""
    return f"{text[:-1] if end else text}, {title}{end or '.'}"


def title_due() -> bool:
    """True on one line in three (counted over every line said in code): the title goes on that one."""
    with _lock:
        _count[0] += 1
        return _count[0] % TITLE_EVERY == 1


def line(kind: str, title: str = "sir", *, avoid: str = "") -> str:
    """The next line of this kind: never the one said last for it, nor `avoid` (what was just said); the title on about one
    in three. Rotation, not dice, so tests and replays are stable."""
    pool = LINES.get(kind) or LINES["fallback"]
    with _lock:
        last = _last.get(kind, "")
        start = (pool.index(last) + 1) % len(pool) if last in pool else 0
        pick = pool[start]
        for step in range(len(pool)):
            option = pool[(start + step) % len(pool)]
            if option != last and option.rstrip(".!?").casefold() not in (avoid or "").casefold():
                pick = option
                break
        _last[kind] = pick
    return titled(pick, title) if title and title_due() else pick
