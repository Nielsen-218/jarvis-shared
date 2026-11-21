"""What Jarvis is made of, said from facts instead of invented.

Replaying the owner's own lines (2026-11-16): "What's the hub and where is that?" -> "your main control point for the house... likely in the living room or
kitchen" and "...the laptop running my brain"; the 9B makes up an answer about his own parts every time because nothing tells it. A question that asks what
one of his parts IS gets its short, true description here, before any model. Only parts whose description is in the repository itself are listed; anything
else is the model's, as before.
"""
import re

# term pattern -> the answer (title is filled in). Every sentence is a fact from the project's own docs (hub/README.md, server.py, the companion/ and
# app/ folders, core/personality.md), never a guess about the house.
_PARTS = (
    (r"hub|control hub|jarvis hub|universal control hub",
     "The hub is the PC control hub, {title}: one shared set of PC tools that I, Claude Code and Codex all use, such as opening apps, taking a screenshot, "
     "the volume and driving Blender through its own interface. It lives in the hub folder of my project on the PC, and anything that changes something "
     "needs your approval first."),
    (r"companion|desktop companion|little robot|the robot",
     "The companion is the little robot that lives on your PC screen, {title}: my face on the desktop."),
    (r"laptop|laptop server|server|jarvis server|home server",
     "The laptop is my always-on server, {title}. It runs the phone app, reminders and notifications, the Garmin sync, the home lab watchdog and the journal. "
     "The PC keeps \"Hey Jarvis\", PC control, try-ons and gaming."),
    (r"phone app|the app|jarvis app|my app",
     "The phone app is served by the laptop, {title}. Say \"pair my phone\" and I'll give you a one-time code to link it. It has to reach the laptop, "
     "so away from home it works only if the laptop is reachable from outside, which I can't check from here."),
    (r"world intelligence|the globe|intelligence globe",
     "World Intelligence is the live globe, {title}, with layers such as weather, earthquakes, aircraft and satellites. Say \"activate Intelligence\", "
     "then something like \"go to Haiti\" or \"turn on aircraft\"."),
    (r"pc control|computer control|control computer",
     "PC control means I operate your PC by looking at the screen and clicking and typing, {title}. Your own command is the approval; risky or "
     "irreversible steps wait for your yes. It runs on the PC, so from the phone I hand it over to the PC."),
)
_COMPILED = tuple((re.compile(rf"^(?:{pat})$", re.I), text) for pat, text in _PARTS)
_ASK = re.compile(
    r"^\W*(?:(?:so|well|okay|ok|and|but|hey jarvis|jarvis)[,.\s]+)*(?:what(?:'s| is| are)|where(?:'s| is| are)|tell me about|explain|what does|what do you mean by|"
    r"what do you mean when you say)\s+(?:the |my |your |this |that )?(?P<term>[a-z][a-z \-']{1,40}?)(?:\s+do)?(?:\s+and where (?:is|are) (?:that|it|they))?"
    r"(?:\s+(?:is|mean))?(?:[,\s]+(?:sir|jarvis|please))*\W*$", re.I)


def answer(raw: str, title: str = "sir") -> str | None:
    """The true description of one of his parts when the whole line asks what it is; None for anything else."""
    plain = re.sub(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", str(raw or "")).strip()
    m = _ASK.match(plain)
    if not m:
        return None
    term = re.sub(r"\s+", " ", m.group("term")).strip().lower()
    term = re.sub(r"^(?:the|my|your|this|that) ", "", term)
    asks_where = bool(re.match(r"^\W*(?:(?:so|well|okay|ok|and|but|hey jarvis|jarvis)[,.\s]+)*where\b", plain, re.I)) and not re.search(r"\band where\b", plain, re.I)
    if asks_where and not re.fullmatch(r"hub|control hub|jarvis hub|universal control hub", term):
        return None                 # "Where is the laptop?" asks for a place, which the card does not know
    for pattern, text in _COMPILED:
        if pattern.match(term):
            return text.format(title=title)
    return None
