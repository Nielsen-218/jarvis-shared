"""A fact the 9B model cannot be trusted with: what pays for his thinking.

The pushback behaviour itself (one short, specific warning; the owner has the final say; no nagging) lives in the
character sheet (core/character.md, "Speaking up") and is measured by scripts/pushback_eval.py. A code guard that
dropped repeated warnings after he decided was built, reviewed twice and REMOVED (2026-11-15): kept safe (only a literal
repeat of one earlier warning sentence, never a question, failure, result or new fact) it changed nothing measurable
(nagged after he decided 14/18 with it, 12/18 without it); kept effective it deleted answers he needed.

What stays: the owner confuses a Claude chat subscription with API credits (his log of 2026-11: "so if I purchase credits
is that for you"). With "just use my subscription" the 9B model agreed and promised something that cannot work. The fact
is fixed (the API is billed separately from any chat plan; JARVIS has no path that runs on a chat plan), so it is
answered in code, but ONLY when the line asks Jarvis to run or think on the owner's Claude/Anthropic subscription.
Paying, renewing, buying, logging in, drafting or summarising about it, and every other service's subscription, are
ordinary requests and are never taken. JARVIS_PUSHBACK=off switches it off.
"""
from __future__ import annotations

import os
import re

LOOK = 600          # characters of a reply or a line that are ever read: a huge input costs nothing


def enabled() -> bool:
    return os.getenv("JARVIS_PUSHBACK", "on").strip().lower() not in ("0", "false", "no", "off")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, (list, tuple)) else []:
        if block is None:
            continue
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
        if kind == "text":
            value = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
            parts.append(value if isinstance(value, str) else "")
    return " ".join(parts)


def last_reply(history) -> str:
    """Jarvis's newest spoken reply in a conversation (dicts or SDK blocks), '' when there is none."""
    for message in reversed(list(history) if isinstance(history, (list, tuple)) else []):
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = _text(message.get("content")).strip()
            if text:
                return text
    return ""


_SUB = re.compile(r"\b(?:my|the|our|an?)\s+(?:claude|anthropic)(?:\s+(?:code|max|pro|plus|team|chat))*\s+"
                  r"(?:subscription|plan|account|membership)\b", re.I)
_STRONG = re.compile(r"\b(?:run(?:ning)?\s+(?:on|off)|switch(?:ing)?\s+(?:over\s+)?to|think(?:ing)?\s+(?:with|on)|"
                     r"power(?:ed)?\s+(?:by|with)|power\b)", re.I)
_USE = re.compile(r"\b(?:use|using|spend|connect|charge)\b", re.I)
_PURPOSE = re.compile(r"\b(?:for (?:you|jarvis|your (?:brain|thinking))|as your brain|to (?:power|run|think)|instead of|"
                      r"rather than|your brain|your thinking|api (?:credits?|budget|key)|the api)\b", re.I)
_NOT_THIS = re.compile(r"\b(?:(?:pay|renew|buy|purchase|cancel|upgrade|extend)\s+(?:my|a|an|our|the)\s+(?:claude|anthropic)|"
                       r"log\s?in|sign\s?in|summari[sz]e|draft|write|email|e-mail|invoice|receipt)\b", re.I)
_NOT_A_REQUEST = re.compile(r"^\W*(?:what|which|how much|when|where|who|why|is|are|do|does|did)\b|"
                            r"\b(?:don'?t use|do not use|never use|cancel)\b", re.I)
BILLING_NOTE = ("A Claude subscription doesn't pay for my thinking, {title}: the API is billed separately, through "
                "credits in the API console. I can't run on the subscription, and the local model works without either.")
_NOTE_START = BILLING_NOTE.split("{title}")[0]
BILLING_AGAIN = "The subscription simply doesn't reach me, {title}; credits are the only way for the cloud part."


def billing_note(text, history=(), title: str = "sir") -> str | None:
    """A line that asks Jarvis to run or think on his CLAUDE subscription: the true answer. Asked again right after the
    note (with the Claude words in the line itself), the short form. None for anything else, and when switched off."""
    said = text.strip()[:LOOK] if isinstance(text, str) else ""
    if not enabled() or not said or len(said.split()) > 40 or _NOT_A_REQUEST.search(said) or _NOT_THIS.search(said):
        return None
    if not _SUB.search(said):
        return None
    after_note = last_reply(history).startswith((_NOTE_START, "The subscription simply doesn"))
    if not (_STRONG.search(said) or (_USE.search(said) and (_PURPOSE.search(said) or after_note))):
        return None
    return (BILLING_AGAIN if after_note else BILLING_NOTE).format(title=title)
