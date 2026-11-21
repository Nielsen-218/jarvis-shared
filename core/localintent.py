"""Deterministic, read-only requests for information kept on this JARVIS.

These are deliberately narrower than general tool routing.  A clear request such
as "search my notes for the dentist appointment" should not depend on a small
model choosing between list, memory-vault, journal, and web skills.
"""
import re


_CHANNEL = re.compile(r"^\s*\[Sent from their Jarvis phone app[^\]]*\]\s*", re.I)
_WAKE = re.compile(r"^\W*(?:(?:hey|ok|okay|yo|hi)\W+)?(?:jarvis|charvis|jervis|travis|javis)\W*", re.I)
_NOTES = re.compile(r"\b(?:my|our|the) (?:notes?|note list|to[- ]do(?: list)?|ideas?)\b", re.I)
_LOOKUP = re.compile(r"\b(?:search|find|look(?:ing)?\s+(?:for|through)|look\s+up|check|show|what .*?(?:say|have))\b", re.I)
_MUTATE = re.compile(r"\b(?:add|save|write|create|delete|remove|erase|update|mark|complete|done)\b", re.I)
_PREFIX = re.compile(
    r"^(?:(?:please|can you|could you|would you)\s+)?(?:search|find|look\s+(?:for|through)|look\s+up|check|show)\s+"
    r"(?:my|our|the)\s+(?:notes?|note list|to[- ]do(?: list)?|ideas?)\s*(?:for|about|on)?\s*", re.I)
_WHAT = re.compile(r"^what\s+(?:do|does)\s+(?:my|our|the)\s+(?:notes?|note list|to[- ]do(?: list)?|ideas?)\s+"
                   r"(?:say|have)\s+(?:about|on|for)\s+", re.I)


def _bare(text: str) -> str:
    return _WAKE.sub("", _CHANNEL.sub("", text or "")).strip()


def notes_query(text: str) -> str | None:
    """Return the requested local-note topic, or None for ordinary/mutating text.

    This intentionally excludes "add a note" and generic "search" requests so
    it cannot steal a normal command or a live web search.
    """
    body = _bare(text)
    if not body or len(body.split()) > 60 or _MUTATE.search(body):
        return None
    if not (_NOTES.search(body) and _LOOKUP.search(body)):
        return None
    query = _PREFIX.sub("", body)
    query = _WHAT.sub("", query)
    query = re.sub(r"\s*(?:please|for me)[?.!]*$", "", query, flags=re.I).strip(" .,:;?!")
    query = re.sub(r"^(?:the|a|an)\s+", "", query, flags=re.I)
    return query if len(re.findall(r"[A-Za-z0-9]", query)) >= 2 else None
