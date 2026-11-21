"""Small text helpers for what Jarvis says out loud.

Skill results are spoken, so they must read naturally: never "1 items", "item(s)" or "1 day(s)".
"""


def plural(n, word: str, many: str | None = None) -> str:
    """'1 item' / '2 items' / '0 items'. Pass `many` for an irregular plural: plural(2, 'person', 'people')."""
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    return f"{n} {word if n == 1 else (many or word + 's')}"


import re as _re

# A spoken self-correction: "close the app in front of me. Nevermind. Minimize the app in front of me." The owner withdrew
# the first clause (live 2026-11-08: Jarvis closed Claude, then minimized). Only explicit retraction phrases count, and only
# at a clause boundary; "actually" alone is ordinary speech ("can you actually close it") and is left alone.
_RETRACT = _re.compile(
    r"(?:^|[.!?,;]\s*|\s)(?:no\s*,?\s*)?(?:never\s?mind|nevermind|scratch that|forget that|cancel that|no wait|wait no|"
    r"i mean|i meant|correction|not that|sorry)\s*[.!?,;:]+\s*", _re.I)


_WITHDRAW = _re.compile(r"never\s?mind|nevermind|scratch that|forget that|cancel that", _re.I)


def after_correction(text: str) -> str:
    """What the speaker still wants after taking something back: the words after the LAST retraction phrase. With nothing
    after it, only a real withdrawal ("close Discord... never mind.") withdraws everything (returns ''); a trailing "sorry"
    or "I mean" is manners, not a cancel ("Close Claude, sorry." still closes Claude)."""
    s = text or ""
    last = None
    for m in _RETRACT.finditer(s):
        last = m
    if last is None:
        return s
    rest = s[last.end():].strip()
    if rest or _WITHDRAW.search(last.group(0)):
        return rest
    return s[:last.start()].strip()
