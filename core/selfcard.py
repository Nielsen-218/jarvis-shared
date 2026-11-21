"""What JARVIS can do, read from the skills that are really loaded: never from a model's memory of itself.

Live 2026-11-14 (qwen3.5:9b): "what are your skills at the moment?" got "I can handle character rigging in Blender...
What part needs rigging today?"; "Where is the gaming coach?" got "I have no record of a 'gaming coach'" and, one line
later, "that specific ability was never installed here... the custom skill setup for Alexa" (invented: the coach is in
tools/gaming.py). So the answer to "what can you do" and "do you have X" is built here in code. An area is named only
when at least one of its skills is registered on this machine (`_loaded`), so the laptop does not claim the PC's skills.
"""
from __future__ import annotations

import re

# (key, the tool modules that prove it, what he says he can do, the owner's words for it, how to ask for it or "")
# SHORT: the same area in a few words, for the summary (the full sentence is for "do you have X").
AREAS = [
    ("pc", ("pcops", "apps", "computer", "system", "pcview", "pcskills"),
     "run this PC: open, close and switch apps, type, find files and set the volume",
     r"\b(?:pc|computer) control\b|\b(?:control|run) (?:my|the|this) (?:pc|computer)\b", ""),
    ("screen", ("screen", "screenread", "eyes"),
     "read what is on your screen",
     r"\bscreen read(?:ing|er)?\b|\bread (?:my|the) screen\b", ""),
    ("web", ("web", "research", "reach"),
     "search the web and read pages for you",
     r"\bweb search(?:ing)?\b|\bsearch the (?:web|internet)\b|\bresearch\b", ""),
    ("notes", ("notes", "life", "today", "memory", "vault"),
     "keep your notes, lists, reminders and timers",
     r"\breminders?\b|\btimers?\b|\bnotes?\b|\bshopping list\b|\bto-?do list\b", ""),
    ("gaming", ("gaming",),
     "coach you live in Apex and Call of Duty and check what is costing you frames",
     r"\bgam(?:e|es|ing)(?: assistant| ai)? coach(?:ing)?\b|\bcoach (?:for|in) (?:my )?gam(?:e|es|ing)\b|\b(?:apex|call of duty|cod) coach\b",
     "Ask me to coach you while the game is running."),
    ("fitness", ("fitness", "trainer", "pca", "pca_health", "fitness_library"),
     "coach your workouts and keep your training plan",
     r"\b(?:workout|fitness|training|running) (?:coach|trainer|plan)\b|\bpersonal trainer\b", ""),
    ("kitchen", ("kitchen", "pantry"),
     "plan meals and keep track of the pantry",
     r"\bmeal plan(?:s|ning|ner)?\b|\bpantry\b|\brecipes?\b", ""),
    ("wardrobe", ("fashion", "fitting", "dressing", "studio"),
     "pick outfits and try clothes on your photo",
     r"\bwardrobe\b|\bstylist\b|\btry-?on\b|\boutfits?\b|\bdressing room\b", ""),
    ("study", ("study", "tutor", "learning"),
     "tutor you for your courses, with quizzes and the whiteboard",
     r"\b(?:study )?tutor\b|\bwhiteboard\b|\bquiz(?:zes)?\b|\bstudy (?:coach|helper|mode)\b", ""),
    ("languages", ("languages", "langlesson"),
     "teach you languages, Haitian Creole included",
     r"\blanguage (?:teacher|lessons?|tutor|coach)\b|\b(?:spanish|french|creole|krey[oò]l|japanese|chinese) (?:teacher|lessons?|tutor)\b", ""),
    ("blender", ("blender", "blenderskills", "rigging", "makechar", "charjobs", "garment3d", "dojo"),
     "build and rig 3D characters in Blender",
     r"\bblender\b|\brigging\b|\b3d (?:model|character)s?\b", ""),
    ("media", ("media", "movies", "qbittorrent"),
     "control your music and video and find films",
     r"\bmedia control\b|\bmusic control\b|\bmovies?\b", ""),
    ("homelab", ("homelab",),
     "keep watch on the homelab",
     r"\bhome ?lab\b", ""),
    ("travel", ("weather", "travel"),
     "check the weather, flights and directions",
     r"\bweather\b|\bflights?\b|\bdirections\b", ""),
    ("security", ("security",),
     "check this PC for anything that should not be running",
     r"\bsecurity (?:check|checkup|scan)\b|\bvirus\b|\bmalware\b", ""),
]

SHORT = {"pc": "", "screen": "", "web": "", "notes": "", "gaming": "live game coaching", "fitness": "your workouts", "kitchen": "meals and the pantry", "wardrobe": "your wardrobe", "study": "tutoring with the whiteboard", "languages": "language lessons", "blender": "3D characters in Blender", "media": "music and films", "homelab": "the homelab", "travel": "weather and travel", "security": "a security check of this PC"}

_FIRST = ("pc", "screen", "web", "notes")       # the everyday four open the summary; the rest follow in one sentence


def _loaded() -> set:
    """The tool modules with at least one registered skill here (tools.gaming -> 'gaming')."""
    try:
        from tools import registry
        return {str(getattr(t.get("fn"), "__module__", "")).rsplit(".", 1)[-1] for t in registry._TOOLS.values()}
    except Exception:  # noqa: BLE001
        return set()


def areas() -> list:
    """The AREAS rows whose skills are loaded on this machine, in order."""
    loaded = _loaded()
    return [row for row in AREAS if loaded & set(row[1])]


def _joined(parts: list) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return "; ".join(parts[:-1]) + "; and " + parts[-1]


def summary() -> str | None:
    """Two spoken sentences on what he can do, or None when no skill is loaded (the caller then leaves the line alone)."""
    rows = areas()
    if not rows:
        return None
    first = [r[2] for r in rows if r[0] in _FIRST]
    rest = [SHORT.get(r[0]) or r[2] for r in rows if r[0] not in _FIRST]
    said = []
    if first:
        said.append("I can " + _joined(first) + ".")
    if rest:
        tail = ", ".join(rest[:-1]) + (" and " + rest[-1] if len(rest) > 1 else rest[-1])
        said.append(("Beyond that I handle " if first else "I handle ") + tail + ". Ask about any one of them.")
    return " ".join(said)


def find(words: str):
    """The loaded area the owner's words name ('the gaming coach' -> the gaming row), or None."""
    for row in areas():
        if re.search(row[3], words or "", re.I):
            return row
    return None


# "what are your skills (at the moment)", "what can you do", "what else can you do": about him in general. The PC-only
# forms ("what PC skills do you have") stay with tools.pcskills.capability_summary (quick._CAPABILITIES).
_NOW = r"(?: at the moment| right now| now| these days| so far)?"
_GENERAL = re.compile(
    r"(?:(?:so|okay|ok|and|all right|alright|well|then|jarvis|no|nope|nah|come on|seriously|oh my (?:gosh|god)),? )*"
    r"(?:(?:what|which) (?:are|is) (?:all )?(?:of )?your (?:skills|abilities|capabilities)" + _NOW
    # "No, tell me all your skills." / "Oh my gosh, is that the only thing you can do?" (data/jarvis.log 2026-11-11: three
    # replies in a row about Blender rigging only, then "As I said")
    + r"|(?:is that|are those) (?:all|the only (?:thing|things|stuff)|everything) (?:that )?you (?:can|could) do" + _NOW
    + r"|what (?:skills|abilities|capabilities) (?:do|did) you (?:have|got)" + _NOW
    + r"|what (?:all |else )?(?:can|could) you (?:actually |really )?do(?: for me)?" + _NOW
    + r"|(?:tell me|list|show me) (?:all )?(?:of )?(?:what you can do|your (?:skills|abilities|capabilities))" + _NOW + r")", re.I)

# "do you have a gaming coach (ability)", "where is the gaming coach", "or you don't have the gaming coach ability".
# Only for a named helper (coach, tutor, ...) or an explicit "ability / skill / feature": "where is the weather" and
# "do you have any notes on the meeting" are no such thing.
_KIND = r"(?: ability| abilities| skill| skills| feature| mode| function)"
_HAVE = re.compile(
    r"(?:(?:so|and|or|but|wait|okay|ok|well|jarvis),? )*"
    r"(?:do you (?:have|got)|have you got|don't you have|do you not have|(?:i thought )?you (?:have|had)|"
    r"you don't have|you do not have|(?P<where>where is|where's))"
    r" (?:a |an |the |your |that |any |some )?(?P<what>[\w' -]{3,48}?)(?P<kind>" + _KIND + r")?"
    r"(?: installed| built in| available)?(?: yet| now| anymore| any more| at all)?", re.I)
# The helper is the LAST word of what he names: "the tutor page", "a stylist for my sister", "the fitness coach page
# open", "the trainer's number" are his things or someone else's, not an ability (review 2026-11-14).
_HELPER_LAST = re.compile(r"\b(?:coach|tutor|trainer|teacher|stylist|whiteboard)$", re.I)
# What may come before the question in the same breath: a reaction, never a request.
_REACTION = re.compile(r"(?:that'?s |that is |it'?s )?(?:funny|crazy|wild|cool|nice|great|good|fine|interesting|fair|true|right)"
                       r"|all right|alright|okay|ok|well|so|right|wow|hmm+|ha(?:ha)+|i see|got it|never ?mind|anyway", re.I)


def answer(bare: str) -> str | None:
    """The spoken answer when the line asks what he can do or whether he has a named ability; None for anything else
    (and for an ability he cannot place: the model is not replaced by a guess in code)."""
    parts = [p.strip(" ?.!,") for p in re.split(r"[.!?]+\s+", (bare or "").strip())]
    line = parts[-1]                                   # "That's funny. That's funny. All right. So what are your skills?"
    if not line or len(line) > 90 or not all(_REACTION.fullmatch(p) for p in parts[:-1] if p):
        return None                                    # "Set a timer for five minutes. What can you do?" keeps its order
    if _GENERAL.fullmatch(line):
        return summary()
    m = _HAVE.fullmatch(line)
    if not m:
        return None
    what = m.group("what")
    if not (m.group("kind") or _HELPER_LAST.search(what)):
        return None         # "do you have any notes on the meeting", "do you have a timer running" ask for his things
    row = find(what)
    if row is None:
        return None
    said = f"I do: I can {row[2]}."
    return f"{said} {row[4]}" if row[4] else said
