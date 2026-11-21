"""Lines Jarvis said without being asked (a reminder, a held-news digest, game mode, a timer), as the conversation records
them: one tagged text block at the start of his NEXT message, so "what?" right after a reminder has an answer.

The block is never his words: the brain, the gateway's recent conversation and the journal all read it as something
JARVIS said (core.contextfit / core.intelligence list the tag with the other injected notes, so grounding never treats a
reminder's words as his request). One format, shared here, so the three readers agree.
"""
from __future__ import annotations

import re

TAG = "<said-unprompted>"
END = "</said-unprompted>"
_LINE = re.compile(r'^Earlier, without being asked, I said(?: \(([^)]*)\))?: "(.*)"\s*$')


def block(items: list[tuple[str, str]]) -> str:
    """[(source, text)] -> the tagged block ('' for none). source "unprompted" adds no label."""
    lines = [f'Earlier, without being asked, I said{"" if source in ("", "unprompted") else f" ({source})"}: "{text}"'
             for source, text in items if text]
    return f"{TAG}\n" + "\n".join(lines) + f"\n{END}" if lines else ""


def lines(text: str) -> list[str]:
    """The sentences a block says he said, oldest first ([] when `text` is not a block)."""
    if not (text or "").lstrip().startswith(TAG):
        return []
    out = []
    for line in text.replace(TAG, "").replace(END, "").splitlines():
        m = _LINE.match(line.strip())
        if m and m.group(2).strip():
            out.append(m.group(2).strip())
    return out
