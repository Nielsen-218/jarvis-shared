"""A paced, source-linked learning conversation for supported offline references.

This is deliberately small and deterministic: it never invents a tutorial from
memory, never treats a page title as evidence that it was read, and never moves
to the next step until the owner explicitly acknowledges the current one.
"""
import re
import time

from core import storage

# A bare "yes" / "okay" belongs to the guide only while the guide is the conversation: live 2026-11-05 the owner's "Yes."
# to "close the USB-to-TTL tab?" was taken as "next step" of a Blender outline from an hour earlier. Any other request
# pauses the guide, and a paused or idle guide answers only when it is named ("next step", "continue the guide").
IDLE_S = 15 * 60
_GENERIC = re.compile(r"^(?:yes|yeah|yep|yup|ok|okay|sure|go ahead|please|no)\b[\s.!,]*(?:please|sir|jarvis)?[\s.!]*$", re.I)
_NAMED = re.compile(r"\b(?:guide|walkthrough|walk ?through|outline|step|blender)\b", re.I)

_START = re.compile(r"\b(?:help me|teach me|show me how|walk me through|guide me|research|look(?:ing)? for|look up|find|how (?:do|can) i)\b.*\bblender\b", re.I)
# Someone who names YouTube, videos or links wants those results, not the offline guide: the video-list path owns that request.
_EXTERNAL = re.compile(r"\b(?:youtube|you tube|videos?|links?|urls?|playlists?|channels?|online|internet|the web|google)\b|"
                       # a question about what was ALREADY found is recall of saved research, not a new guide (2026-11-10:
                       # "What did you find about Blender rigging tutorials?" started the offline guide)
                       r"\b(?:what (?:did|have) you (?:find|found|learn|learned|save|saved)|did you find|you (?:found|saved|learned))\b",
                       re.I)
_YES = re.compile(r"^(?:yes|yeah|yep|yup|okay|ok|sure|go ahead|let'?s do it|start|please|continue)\b", re.I)
_NEXT = re.compile(r"^(?:next|okay|ok|yes|yeah|yep|sure|continue|go ahead|ready|got it|i'?m ready)\b", re.I)
_BACK = re.compile(r"^(?:back|previous|repeat|say that again)\b", re.I)
_STOP = re.compile(r"^(?:stop|cancel|quit|never mind|no thanks)\b", re.I)


def _state():
    value = storage.load("guided-learning.json", {})
    return value if isinstance(value, dict) else {}


def _save(value):
    storage.save("guided-learning.json", value)


def _sentences(card):
    """Turn a reviewed handbook card into short, source-faithful actions."""
    body = " ".join(str(card.get("body", "")).split())
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", body) if len(part.strip()) > 20]
    if card.get("id") == "characters":
        # These are the card's recommendations separated into workable stages.
        parts = [
            "Choose properly licensed anatomy references and decide whether you will begin from a base mesh or a carefully constructed sculpt.",
            "Block the overall proportions from suitable reference views before adding fine anatomy; do not treat one photograph as complete hidden geometry.",
            "Retopologize into a deformation mesh, with deliberate edge flow around the eyes, mouth, shoulders, and hips.",
            "Treat skin, eyes, hair, clothing, and lighting as separate quality passes rather than declaring the sculpt finished.",
            "Test expressions and bent poses before committing to high-detail work or export.",
        ]
    return parts[:6]


def _offer(text):
    from tools import blender

    cards = blender.search(text, 3)
    if not cards:
        return None
    chosen = cards[0]
    steps = _sentences(chosen)
    if not steps:
        return None
    state = {"status": "offered", "topic": chosen["title"], "source": chosen["source"],
             "steps": steps, "index": 0, "at": time.time(), "paused": False}
    _save(state)
    return (f"I found the right Blender guide: {chosen['title']}. "
            "It recommends a staged workflow rather than trying to make the whole human at once. "
            "Would you like me to walk you through it one step at a time?")


def _step(state):
    index, steps = state["index"], state["steps"]
    return (f"Step {index + 1} of {len(steps)}: {steps[index]} "
            "Say okay or next when you are ready for the next step, or say repeat, back, or stop.")


def handle(text: str) -> str | None:
    """Handle a supported guide offer or a paced follow-up; otherwise leave chat untouched."""
    text = " ".join((text or "").strip().split())
    state = _state()
    status = state.get("status")
    if status in ("offered", "active"):
        idle = time.time() - float(state.get("at") or 0) > IDLE_S
        if idle and not _NAMED.search(text):
            return None
        if state.get("paused") and _GENERIC.match(text) and not _NAMED.search(text):
            return None                        # a bare yes after another request answers THAT request, not the guide
        reply = _follow(text, state, status)
        if reply is None:                      # something else was asked: the guide steps aside until it is named again
            state["paused"] = True
            _save(state)
        return reply
    if _START.search(text) and not _EXTERNAL.search(text):
        return _offer(text)
    return None


def _save_turn(state):
    state["at"], state["paused"] = time.time(), False
    _save(state)


def _follow(text, state, status):
    if status == "offered":
        if _STOP.match(text):
            _save({})
            return "No problem. I kept the source link in this conversation, and we can return to it later."
        if _YES.match(text):
            state["status"] = "active"
            _save_turn(state)
            return _step(state)
        return None
    if status == "active":
        if _STOP.match(text):
            _save({})
            return "Paused the Blender walkthrough. Say help me with Blender again whenever you want a new guide."
        if _BACK.match(text):
            state["index"] = max(0, int(state.get("index", 0)) - 1)
            _save_turn(state)
            return _step(state)
        if _NEXT.match(text):
            state["index"] = int(state.get("index", 0)) + 1
            if state["index"] >= len(state.get("steps", [])):
                _save({})
                return "That completes this guided outline. I can bring up the source if you want it."
            _save_turn(state)
            return _step(state)
        return None
    return None
