"""Commands the user teaches Jarvis: "When I say blue notebook, reply only with 'noted'."

Live (2026-10-30, data/jarvis.log 7388-7419): the teaching sentence ran NO skill; "Blue Notebook" -> "Noted." worked
only while the sentence sat in the conversation. After a restart nothing was on file (memory, lessons, reminders all
0), so the model improvised: "Blue Notebook is a notebook used by the owner", then "I've noted it as a reminder",
and when asked what it had been taught it recalled a reminder. Root cause: an explicit trigger -> response rule had
no store and no executor; it was left to the small model's memory of the chat.

Here: a per-person, file-backed store of rules (trigger -> exact response, enabled, history of replaced responses),
parsed from the teaching sentence by pattern (no model), executed by exact match of the whole utterance (no model),
listed / updated / disabled / deleted by pattern too. Nothing a rule says is ever interpreted: the response is spoken
as written. A trigger must be the WHOLE utterance (wake word and punctuation aside) so a rule can never hijack an
ordinary sentence that merely contains the words."""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

from core import storage

FILE = "commands.json"
MAX_RULES = 200
MAX_TRIGGER, MAX_RESPONSE = 80, 400
NEARLY = 0.85       # how alike (letters only) a whole utterance must be to a trigger to count as it, see find()

_QUOTE = r"[\"'“”‘’]"
_TRIGGER = r"(?P<trigger>[^,\"'“”‘’]{1,80}?)"
_RESPONSE = r"(?P<response>.{1,400}?)"
# "When I say X, (you need to / just) (reply|say|answer|respond) (only) (with) Y"   ·   "If I say X, say Y"
# "Whenever I say X, respond with Y"   ·   "When I tell you X, (you) say Y"
_TEACH = re.compile(
    r"^\W*(?:(?:ok|okay|hey|so|jarvis|please)[\s,]+)*"
    r"(?:(?:i (?:want|need) (?:you )?to )?(?:learn|remember|teach you)(?: this| that| a (?:new )?(?:command|trigger))?[\s,:]+)?"
    r"(?:from now on[\s,]+)?(?:when(?:ever)?|if|every time|each time|any ?time) i (?:say|tell you|ask(?: you)? for|type)[\s,]+"
    rf"{_QUOTE}?{_TRIGGER}{_QUOTE}?[\s,]+(?:you (?:(?:need to|have to|should|must|will|can|just|only|always) )?)?(?:just |only |always |simply )?"
    rf"(?:reply|say|answer|respond|tell me|come back)(?: back)?(?: to me)?(?: only| just)?(?: with)?[\s,:]*{_QUOTE}?{_RESPONSE}{_QUOTE}?"
    r"(?:[\s,]+(?:instead|from now on|ok|okay|please|alright))*\W*$",
    re.I)
_WORD = r"(?:command|trigger|rule|response|phrase|shortcut)"
# "forget the blue notebook command"  ·  "delete the trigger (for) blue notebook"  ·  "forget what you say when I say X"
_FORGET = re.compile(
    r"^\W*(?:(?:ok|okay|hey|jarvis|please)[\s,]+)*(?:forget|delete|remove|drop|disable|turn off|cancel|un-?learn)"
    r"(?: (?:the|that|my|your))?(?: (?:learned|taught|custom))?"
    rf"(?: {_WORD}(?: for| when i say| about)?[\s,]+{_QUOTE}?(?P<trigger>[^,\"'“”‘’]{{1,80}}?){_QUOTE}?"
    rf"|[\s,]+{_QUOTE}?(?P<trigger2>[^,\"'“”‘’]{{1,80}}?){_QUOTE}? {_WORD}"
    rf"|[\s,]+what (?:to say|you say|i taught you(?: to say)?) when i say[\s,]+{_QUOTE}?(?P<trigger3>[^,\"'“”‘’]{{1,80}}?){_QUOTE}?)\W*$", re.I)
_STOP_RESPONDING = re.compile(
    rf"^\W*(?:(?:ok|okay|hey|jarvis|please)[\s,]+)*(?:stop|don'?t|do not) (?:respond(?:ing)?|repl(?:y|ying)|say(?:ing)? (?:that|anything)|react(?:ing)?|answer(?:ing)?)"
    rf"(?: to| when i say| for)[\s,]+{_QUOTE}?{_TRIGGER}{_QUOTE}?(?: any ?more| anymore)?\W*$", re.I)
_LIST = re.compile(
    r"^\W*(?:(?:ok|okay|hey|jarvis|please)[\s,]+)*(?:what (?:commands|triggers|phrases|rules|responses)? ?(?:have i|did i) (?:teach|taught|tell|told) you"
    r"(?: to (?:do|say))?|what did i teach you(?: to (?:do|say))?(?: when i say .+)?|list (?:my |the |your )?(?:learned |taught |custom )?(?:commands|triggers|rules)"
    r"|which (?:commands|triggers|phrases) (?:do you|have you) (?:know|learned)|what (?:do you|will you) (?:say|do) when i say (?P<which>.+?))\W*$", re.I)
_WAKE = re.compile(r"^\W*(?:(?:hey|hi|ok|okay|yo)\W+)?(?:jarvis|charvis|jervis|javis|jarvas|jarves|jarvus)\b\W*", re.I)
_YES = re.compile(r"^\W*(?:yes|yeah|yep|yup|okay|ok)[\s,]+", re.I)


def _norm(text: str) -> str:
    """How a trigger is compared: the wake word and punctuation off, one space, lower case, straight quotes."""
    text = _WAKE.sub("", text or "")
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", text.lower())).strip()


def _clean(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip().strip("\"'“”‘’ ")).strip(" ,.;:")
    return text[:limit]


def rules() -> list[dict]:
    return [r for r in storage.load(FILE, []) if isinstance(r, dict) and r.get("trigger")]


def _save(items: list[dict]) -> None:
    storage.save(FILE, items[-MAX_RULES:])


def find(utterance: str) -> dict | None:
    """The enabled rule whose trigger is this whole utterance (wake word and punctuation aside).

    Spoken triggers reach here through the STT, which hears an invented phrase slightly differently from one turn
    to the next (acceptance run: "Hollow Pencil" once, "HoloPencil" the next time). A whole utterance that is
    nearly the trigger, letters only, still counts; a sentence that merely contains it never does."""
    said = _norm(utterance)
    if not said:
        return None
    live = [r for r in rules() if r.get("enabled", True)]
    for rule in live:
        if _norm(rule["trigger"]) == said:
            return rule
    if len(said) <= MAX_TRIGGER:
        letters = said.replace(" ", "").replace("-", "").replace("'", "")
        near = []
        for rule in live:
            target = _norm(rule["trigger"]).replace(" ", "").replace("-", "").replace("'", "")
            if letters and target and SequenceMatcher(None, letters, target).ratio() >= NEARLY:
                near.append(rule)
        if len(near) == 1:                            # two similar-sounding triggers both near: ambiguous, run neither
            return near[0]
    return None


def teach(trigger: str, response: str) -> dict:
    """Store (or update) the rule. Updating keeps the replaced responses on the record."""
    trigger, response = _clean(trigger, MAX_TRIGGER), _clean(response, MAX_RESPONSE)
    if not trigger or not response:
        raise ValueError("a trigger and a response are both needed")
    items = rules()
    now = datetime.now().isoformat(timespec="minutes")
    for rule in items:
        if _norm(rule["trigger"]) == _norm(trigger):
            if rule.get("response") != response:
                rule.setdefault("history", []).append({"response": rule.get("response"), "until": now})
                rule["history"] = rule["history"][-10:]
            rule.update(response=response, enabled=True, updated=now)
            _save(items)
            return dict(rule, updated_existing=True)
    rule = {"trigger": trigger, "response": response, "enabled": True, "created": now, "updated": now, "history": []}
    items.append(rule)
    _save(items)
    return dict(rule, updated_existing=False)


def forget(trigger: str, *, delete: bool = True) -> dict | None:
    """Delete (or just disable) the rule with this trigger."""
    items = rules()
    for rule in items:
        if _norm(rule["trigger"]) == _norm(trigger):
            if delete:
                items.remove(rule)
            else:
                rule["enabled"] = False
                rule["updated"] = datetime.now().isoformat(timespec="minutes")
            _save(items)
            return rule
    return None


def describe(which: str | None = None) -> str:
    """What has been taught, in words (for "what did I teach you?")."""
    items = [r for r in rules() if r.get("enabled", True)]
    if which:
        items = [r for r in items if _norm(r["trigger"]) == _norm(which)]
        if not items:
            return f"You haven't taught me anything for '{_clean(which, MAX_TRIGGER)}', sir."
    if not items:
        return "You haven't taught me any commands yet, sir. Say something like: when I say blue notebook, reply with noted."
    lines = [f"when you say '{r['trigger']}', I say '{r['response']}'" for r in items[:12]]
    more = f" and {len(items) - 12} more" if len(items) > 12 else ""
    return f"You've taught me {len(items)} command{'s' if len(items) != 1 else ''}, sir: " + "; ".join(lines) + more + "."


def handle(text: str) -> str | None:
    """The whole learned-command lifecycle, before any model: execute a trigger, learn one, list them, forget one.
    None when the utterance is none of these (the brain takes over)."""
    raw = re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", (text or "").strip(), flags=re.I)
    if not raw or len(raw) > 600:
        return None
    rule = find(raw)
    if rule:
        return rule["response"]
    m = _TEACH.match(raw)
    if m:
        trigger, response = _clean(m.group("trigger"), MAX_TRIGGER), _clean(m.group("response"), MAX_RESPONSE)
        if not trigger or not response or _norm(trigger) == _norm(response):
            return None
        if len(_norm(trigger).split()) > 8:          # a whole sentence is not a trigger: the brain hears it as a request
            return None
        rule = teach(trigger, response)
        verb = "Updated" if rule["updated_existing"] else "Learned"
        return f"{verb}, sir: when you say '{rule['trigger']}', I'll say '{rule['response']}'."
    m = _LIST.match(raw)
    if m:
        return describe(m.group("which"))
    m = _FORGET.match(raw)
    if m:
        trigger = _clean(m.group("trigger") or m.group("trigger2") or m.group("trigger3") or "", MAX_TRIGGER)
        rule = forget(trigger) if trigger else None
        if rule:
            return f"Forgotten, sir: I no longer answer '{rule['trigger']}'."
        return f"I don't have a command for '{trigger}', sir." if trigger else None
    m = _STOP_RESPONDING.match(raw)
    if m:
        rule = forget(_clean(m.group("trigger"), MAX_TRIGGER))
        if rule:                                      # "stop replying to my emails" has no rule: the brain's sentence
            return f"Forgotten, sir: I no longer answer '{rule['trigger']}'."
    return None
