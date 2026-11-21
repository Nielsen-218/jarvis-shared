"""A question about HERE ("any weather advisories near me?", "what's the traffic like?", "is the power out around here?"): answered
by a search for the TOWN he is in right now (core/place), never for his words.

The query is built from a closed list of public subjects (alerts, traffic, outages, gas prices, local news, events, places to eat)
plus "Springfield, Illinois": nothing else he said leaves the machine, so a sentence that also names a person, a street or a family
matter cannot leak through it. Until 2026-11-15 every local question was refused for privacy; the owner then asked for live
location on phone and PC ("I need him to have access to live location").
"""
import re

# He means HERE: "near me", "around here", "in my area", "local ...", "in town". Some subjects are local without saying so.
_HERE = re.compile(r"\b(?:near(?:by| me| us| here)?|close to (?:me|here)|around (?:here|me|us)|in (?:my|our|the) (?:area|town|city|neighbou?rhood|county)|"
                   r"(?:my|our) (?:area|town|city|neighbou?rhood|county)|where i live|local(?:ly)?|in town|here in town|today around)\b", re.I)

# (name, subject pattern, needs "here" spoken, query template). The query holds the place label and a closed phrase, nothing he said.
_SUBJECTS = (
    ("alerts", re.compile(r"\b(?:(?:weather|storm|flood(?:ing)?|wind|heat|freeze|freezing|winter|coastal|tropical|hurricane|tornado|fog|rip current|surf)\s+"
                          r"(?:advisor(?:y|ies)|alerts?|warnings?|watch(?:es)?)|advisor(?:y|ies)|severe weather|weather alerts?)\b", re.I), False,
     "National Weather Service weather alerts advisories warnings {place} today"),
    ("outage", re.compile(r"\b(?:power|electric(?:ity)?|internet|water)\s+(?:outages?|(?:is |has gone |went )?out|down)\b|\boutages?\b", re.I), False,
     "power outages {place} today"),
    ("traffic", re.compile(r"\b(?:traffic|road closures?|roads? (?:closed|blocked))\b", re.I), False,
     "traffic accidents road closures {place} today"),
    ("roads", re.compile(r"\b(?:accidents?|crash(?:es)?|roadwork|road work|detours?|construction)\b", re.I), True,
     "traffic accidents road closures {place} today"),
    ("gas", re.compile(r"\b(?:gas|gasoline|fuel) prices?\b|\bcheapest (?:gas|gasoline)\b", re.I), False,
     "gas prices {place} today"),
    ("safety", re.compile(r"\b(?:police activity|sirens?|helicopters?|crime|shooting|fire trucks?|ambulances?|emergency)\b", re.I), True,
     "police fire emergency activity news {place} today"),
    ("events", re.compile(r"\b(?:events?|things to do|festivals?|concerts?|farmers'? market|fireworks|parades?|what'?s (?:on|happening) (?:this|tonight|today))\b", re.I), True,
     "events this week {place}"),
    ("news", re.compile(r"\b(?:news|headlines?|top stories|latest stories|what(?:'s| is| has been|'s been) (?:going on|happening)|anything (?:going on|happening|new)|anything interesting|what happened|what(?:'s| is) new)\b", re.I), True,
     "local news {place} today"),
    ("food", re.compile(r"\b(?:restaurants?|pizza|coffee|cafes?|breakfast|brunch|lunch|dinner|burgers?|sushi|tacos?|ice cream|bakery|diner|bars?|pharmacy|"
                        r"drug ?store|grocery|supermarket|hardware store|urgent care|pet store|gas station)\b", re.I), True,
     "best {thing} near {place}"),
)
_THING = re.compile(_SUBJECTS[-1][1].pattern, re.I)
# Not a question about the world: his own list, files or a machine ("local files", "my local model", "the construction project in Blender").
_NOT_WORLD = re.compile(r"\b(?:files?|folders?|drives?|disk|model|models|server|network|host|branch|repo|repository|copy|backup|blender|unreal|"
                        r"scene|project|game|code|install|ollama|build|construction (?:site )?(?:in|of) (?:the )?game)\b", re.I)
_PLACE_NAMED = re.compile(r"\b(?:in|at|near|around|for)\s+(?!me\b|us\b|here\b|my\b|our\b|the\b|town\b)[A-Z][a-z]+", re.U)   # "in Richmond": another place, not here


_ASKISH = re.compile(r"^\W*(?:(?:so|well|okay|ok|and|also|hey jarvis|jarvis|please|um|uh)[,.\s]+)*(?:any|is|are|was|were|do|does|did|can|could|will|would|"
                     r"what|what's|whats|how|how's|hows|where|where's|wheres|who|when|which|tell me|show me|find|check|look|search|give me|got|have you)\b", re.I)


_CLAIM = re.compile(r"\b(?:is (?:that|it|this) true|they say|people (?:are )?(?:say|saying)|(?:i|we) (?:heard|read|saw)|apparently|supposedly|rumou?rs?|"
                    r"from work|my (?:wife|husband|mom|mother|dad|father|brother|sister|son|daughter|friend|neighbou?r|boss))\b", re.I)


def subject(text: str) -> tuple[str, str] | None:
    """(name, query template) of the local subject a line asks about, or None. Two or more spoken places make it not this one."""
    t = re.sub(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", str(text or ""), flags=re.I)   # the phone's prefix is not his words (live 2026-11-15)
    t = re.sub(r"\s+", " ", t).strip()
    if not t or len(t.split()) > 40 or _NOT_WORLD.search(t) or _PLACE_NAMED.search(t):
        return None
    if _CLAIM.search(t) or len(re.findall(r"[.!?](?:\s|$)", t)) > 2:
        return None                                         # a rumour to check ("They say ... Is that true?") is the claim check's, not a local question
    if not (t.endswith("?") or _ASKISH.match(t)):
        return None                                         # "I had an accident", "the construction is loud": a statement, not a question
    here = bool(_HERE.search(t))
    for name, pattern, needs_here, template in _SUBJECTS:
        if pattern.search(t) and (here or not needs_here):
            return name, template
    return None


def query(text: str, place: str) -> str:
    """The search for a local question, or '' when the line is not one or no place is known. Built from a closed phrase and the
    town label only."""
    found = subject(text)
    if not found or not place:
        return ""
    name, template = found
    thing = ""
    if name == "food":
        m = _THING.search(str(text))
        thing = (m.group(0).lower() if m else "places to eat")
    return template.format(place=place, thing=thing)


_WHERE_AM_I = re.compile(
    r"^\W*(?:(?:so|well|okay|ok|and|hey jarvis|jarvis|what about|how about)[,.\s]+)*(?:from (?:my|the) (?:pc|phone|laptop|computer)[,.\s]+)?"
    r"(?:where (?:am|are) (?:i|we)(?: (?:right now|now|at|located|currently|exactly|sitting|staying))*|where is this|where (?:am|are) (?:i|we) at right now|"
    r"what(?:'s| is) my (?:current )?location|what town am i in|what city am i in|what state am i in|what state are we in|do you know where (?:i am|we are))"
    r"(?:\s+(?:on|from|using) (?:my|the) (?:pc|phone|laptop|computer))?(?:\s+(?:right now|now|jarvis|sir))*\W*$", re.I)


def asks_where_am_i(text: str) -> bool:
    """"Where am I?", "Where am I located?", "Where are we located right now?", "What about from my PC? Where am I located on my PC?": the last
    sentence of the line decides (live 2026-11-16: the model said 'That's where I see you from here', and he has no camera)."""
    plain = re.sub(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", str(text or "")).strip()
    parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+", plain) if p.strip()]
    return bool(parts) and any(_WHERE_AM_I.match(p) for p in (parts[-1], plain))
