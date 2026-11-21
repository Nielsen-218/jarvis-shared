"""College teachers: Gemini (free tier, always on for the school) and Claude (promotional credits, school lessons only).

Owner decision (2026-11-04): "Gemini cloud should always be on, it's free; the Claude credits can be used for school only."

* Gemini goes through its OpenAI-compatible endpoint. The school uses it even while JARVIS_LOCAL_ONLY is on, because a lesson
  contains no personal data: only a practice task such as "how many .md files are in {folder}" and the failure text from a
  throw-away practice folder. JARVIS_LOCAL_ONLY still governs everything else (conversations stay local).
* Claude is asked only after Gemini has had its tries on a subject, only by the school, with a small daily call cap
  (JARVIS_SCHOOL_CLAUDE_DAILY, default 40; raised from 5 to 15 on 2026-11-04 and to 40 by the owner on 2026-11-05), a hard output limit, no retries and no tools. Nothing else in JARVIS calls it from here.
* JARVIS_SCHOOL_API=off turns both off. Keys are never logged; errors are reported by status code only (no provider text).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

DEFAULT_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
CLAUDE_MODEL = "claude-haiku-5-5"
MAX_OUTPUT = 600  # tokens: a lesson answer is a few lines of commands


def _off() -> bool:
    return os.getenv("JARVIS_SCHOOL_API", "on").lower() == "off"


def why_not() -> str:
    """'' when Gemini can teach, otherwise the plain-words reason it cannot."""
    if _off():
        return "college is switched off (JARVIS_SCHOOL_API=off)"
    if not os.getenv("GEMINI_API_KEY"):
        return "no GEMINI_API_KEY is configured"
    return ""


def claude_why_not() -> str:
    if _off():
        return "college is switched off (JARVIS_SCHOOL_API=off)"
    if not os.getenv("ANTHROPIC_API_KEY"):
        return "no ANTHROPIC_API_KEY is configured"
    if os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/") != "https://api.anthropic.com":
        return "a custom Anthropic gateway is configured; its billing has not been reviewed"
    return ""


def enabled() -> bool:
    return not why_not()


def ask(prompt: str, *, model: Optional[str] = None, timeout: float = 45.0) -> str:
    """One Gemini chat completion. Raises RuntimeError with a short code on any problem."""
    key = os.getenv("GEMINI_API_KEY")
    url = os.getenv("JARVIS_GEMINI_URL", DEFAULT_URL)
    if not key or not url.startswith("https://"):
        raise RuntimeError("cloud_unconfigured")
    body = {"model": model or os.getenv("JARVIS_GEMINI_MODEL", "gemini-3.5-flash"),
            "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 700,
            "reasoning_effort": "none", "stream": False}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(2_000_001)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"cloud_http_{e.code}") from None
    except Exception:  # noqa: BLE001
        raise RuntimeError("cloud_request_failed") from None
    if len(raw) > 2_000_000:
        raise RuntimeError("response_too_large")
    try:
        return (((json.loads(raw).get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    except ValueError:
        raise RuntimeError("invalid_response") from None


def ask_claude(prompt: str, *, timeout: float = 45.0) -> str:
    """One Claude message: no tools, no retries, no extra turns, hard output limit. Raises RuntimeError with a short code."""
    if claude_why_not():
        raise RuntimeError("claude_unavailable")
    try:
        import anthropic
        client = anthropic.Anthropic(max_retries=0, timeout=timeout)
        reply = client.messages.create(model=os.getenv("JARVIS_SCHOOL_CLAUDE_MODEL", CLAUDE_MODEL), max_tokens=MAX_OUTPUT,
                                       messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in reply.content if getattr(b, "type", "") == "text")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("claude_" + type(e).__name__) from None
