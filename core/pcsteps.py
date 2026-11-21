"""A spoken request with several UI steps in one app (master conversationalist, 2026-11-15; reviewed the same day).

Live 2026-11-14: "Open the Meshy panel in the Blender sidebar, press N, click Bridge On." took 14.9 s and ended "I couldn't work out
how to do that" (the planner got a sentence of three UI steps, and the craft front door had first taken the first part for a file to
open). He should hear the steps back in one line, be asked at most ONE thing, and then have them done or handed back.

A deterministic front door, like core.gateway.click_in_app. It runs nothing itself:
  - ONLY UI navigation: every clause is a press <key>, a click / tap, scroll / drag / expand / collapse, or an open / show / go to
    of a part of the window (panel, tab, menu, sidebar, section, button, dialog); at least two clauses, no question, no negation,
    no narration. Toggle, enable, disable, turn on/off, check, pick, choose and select are not here: "Turn off the lights and turn
    on the fan" is two requests for the planner, not clicks in a window.
  - the app is the one he names in the line ("in Blender", "the Blender sidebar"), or the one app his previous line named outright.
    Never a guess from words of the conversation (the review found 'sunny' -> Sunshine).
  - the result is ONE `use_app` step marked `review`: core.pchybrid.run puts it in the ledger and says the steps back, "Should I
    open the Meshy panel...; press N; click Bridge On in Blender?", whatever core.authz thinks of a named app (one misheard step
    can click the wrong thing). Nothing runs before his yes.
  - no app known: one question, "Which app are they in?". His answer counts only when that question is the last thing Jarvis said,
    within a minute, and the line is nothing but an app name; any other line drops the held steps.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Optional

from core.words import plural

_CHANNEL = re.compile(r"^\s*\[[^\]]*\]\s*")
_LEAD = re.compile(r"^\W*(?:(?:okay|ok|so|now|then|alright|all right|and|well|hey|yes|yeah)[,.\s]+)*(?:jarvis[,.\s]+)?"
                   r"(?:(?:okay|ok|so|now|then|please)[,.\s]+)*"
                   r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|(?:i|we)\s+(?:need|want)\s+you\s+to\s+|"
                   r"i'?d\s+like\s+you\s+to\s+|please\s+|go\s+ahead\s+and\s+)*", re.I)
_KEY = r"(?:the\s+)?(?:[A-Za-z0-9]|f\d{1,2}|enter|return|escape|esc|tab|space(?:bar)?|delete|backspace|shift|ctrl|control|alt|windows|win|home|end|(?:ctrl|control|alt|shift|win)\s*\+\s*[\w+]+)(?:\s+key)?"
_UI_NOUN = (r"panel|sidebar|side\s*bar|tab|menu|toolbar|tool\s*bar|button|dialog|window|section|options?|settings|properties|"
            r"editor|add-?on|plug-?in|header|outliner|inspector|pane|ribbon")
_STEP = re.compile(
    rf"^(?:then\s+)?(?:(?:press|hit)\s+(?P<key>{_KEY})(?:\s+(?:in|on)\s+[\w .'-]+)?|(?:click(?:\s+on)?|tap)\s+(?P<target>.+)|"
    rf"(?:scroll|drag|expand|collapse)\b.*|(?:open|show|go\s+to|switch\s+to|bring\s+up|pull\s+up)\b\s+.*\b(?:{_UI_NOUN})\b.*)$", re.I)
_SPLIT = re.compile(r"\s*(?:(?<=[a-z0-9])[.!]\s+|,\s*(?:and\s+)?(?:then\s+)?|;\s*|\s+and\s+then\s+|\s+then\s+|\s+and\s+(?=(?:press|hit|click|tap|scroll|drag|expand|collapse|open|show|go\s+to)\b))", re.I)
# "in Blender" / "In Blender," / "inside Blender" / "the Blender sidebar" naming where the steps happen
_IN_APP = re.compile(r"\b(?:in|inside|within|on)\s+(?:the\s+)?(?P<app>[A-Za-z][\w.+-]*(?:\s+[A-Za-z][\w.+-]*)?)\s*$", re.I)
_IN_APP_ALL = re.compile(r"(?=\b(?:in|inside|within|on)\s+(?:the\s+)?(?P<app>[A-Za-z][\w.+-]*(?:\s+[A-Za-z][\w.+-]*)?)\s*$)", re.I)
_PART_APP = re.compile(r"\b(?P<app>[A-Za-z][\w.+-]*)(?:'s)?\s+(?:" + _UI_NOUN + r")\b", re.I)
# a question, an order NOT to, narration, a condition, a report: never steps to carry out
_NOT_STEPS = re.compile(r"^\W*(?:how|what|why|when|where|who|which|should|do|does|did|is|are|was|were|if|i|he|she|they|we|then\s+(?:he|she|they|i)|"
                        r"please\s+(?:do\s*n'?t|do\s+not|never)|don'?t|do\s+not|never|the)\b|\b(?:don'?t|do\s+not|never|stop)\b", re.I)


# A retraction anywhere in what he said ("... in Blender. Wait.", "Cancel that.", "Just kidding.") means there is no plan at all.
_RETRACT = re.compile(r"\b(?:wait|hold\s+on|hang\s+on|cancel(?:\s+that)?|scratch\s+that|never\s*mind|forget\s+(?:it|that)|just\s+kidding|"
                      r"ignore\s+(?:that|me)|disregard|on\s+second\s+thought|actually,?\s+no|belay\s+that|stop|don'?t|do\s+not|never)\b", re.I)

# Words of a window's parts: they never name an app, whatever the app list resolves them to by luck (the review found
# 'Save' -> Brave, 'File' -> Files, 'Edit' -> Registry Editor, 'Windows' -> PowerShell, 'Start' / 'Format' -> Task Manager).
_UI_WORDS = frozenset("save file files edit first second third next last previous main top side left right center new view help tools tool "
                      "settings options home recent windows window start format close ok cancel apply yes no back forward play pause stop "
                      "red green blue small big full quick open show the a an this that my your volume search run download upload add delete remove install "
                      "uninstall send buy pay confirm reset unlock lock submit continue accept done finish exit quit enable disable toggle".split())


def _named(name: str, resolve) -> str:
    """The app `name` IS (its own first word is the app's first word), not the app it resolves to by luck."""
    words = re.findall(r"[A-Za-z0-9.+-]+", name or "")
    if not words or words[0].casefold() in _UI_WORDS:
        return ""
    app = resolve(name.strip()) or resolve(words[0])
    return app if app and str(app).casefold().split()[0] == words[0].casefold() else ""


def split(said: str) -> list:
    """The UI steps in `said`, in order, each a short imperative; [] unless there are two or more and every clause is one."""
    raw = _CHANNEL.sub("", said or "").strip()
    if not raw or "?" in raw or len(raw) > 400 or _RETRACT.search(raw):
        return []
    text = _LEAD.sub("", raw).strip(" .!?")
    if not text or _NOT_STEPS.match(text):
        return []
    parts = [p.strip(" .,!?") for p in _SPLIT.split(text) if p and p.strip(" .,!?")]
    steps = []
    for part in parts:
        if not _STEP.match(part):
            return []                      # one clause is not a UI step: the planner reads the sentence
        steps.append(part)
    if len(steps) < 2 or len("; ".join(steps)) > 290:
        return []                          # a goal longer than use_app takes would lose its last steps silently
    return steps


def _app_of(steps: list, resolve) -> str:
    """The app the steps name: an explicit trailing 'in Blender' / 'in Disk Management', or 'the Blender sidebar' (its first word must
    BE the app's name, and no part of a window ever is one)."""
    for step in steps:
        for m in _IN_APP_ALL.finditer(step):             # every 'in X' at the end ('click Bridge On in Blender': 'On in Blender' too)
            found = _named(m.group("app"), resolve)
            if found:
                return found
    for step in steps:
        for m in _PART_APP.finditer(step):
            found = _named(m.group("app"), resolve)
            if found:
                return found
    return ""


def _app_named_before(history, resolve, said: str = "") -> str:
    """The ONE app the owner's previous line named outright ("Okay. In Blender."), '' when none or more than one. The previous
    line is the one before THIS one, whether or not the history already holds this one (brain.llm hands the PC layer a history
    without it); two lines back is not the previous line. A word counts only when it IS the app's name."""
    users = [m for m in (history or []) if isinstance(m, dict) and m.get("role") == "user"]

    def text_of(m):
        c = m.get("content")
        return _CHANNEL.sub("", c if isinstance(c, str) else " ".join(
            (b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "") or "") for b in c or []))
    if users and said and _flat(text_of(users[-1])) == _flat(said):
        users = users[:-1]                       # the current line is in the history: the one before it is the previous
    if not users:
        return ""
    found = set()
    for word in re.findall(r"[A-Za-z][\w.+-]{2,}", text_of(users[-1])):
        app = _named(word.strip(".+-"), resolve)
        if app and word.strip(".+-").casefold() not in _COMMON_WORDS:
            found.add(app)
    return found.pop() if len(found) == 1 else ""


# Words that resolve to an app only by luck ("paint", "word", "settings"...) are no sign of which app he is working in.
_COMMON_WORDS = frozenset("paint word the one team notes note clock calendar mail photos store camera sound settings files "
                          "video music maps news weather phone movies games".split())

_lock = threading.Lock()
_ASKED: dict = {}                          # uid -> (steps, time, the question as said): the steps whose app was asked for
ASK_SECONDS = 60


def _resolve(name: str) -> str:
    try:
        from tools import apps
        return apps.resolve_app(name) or ""
    except Exception:  # noqa: BLE001 - no app list: no app known
        return ""


def goal(steps: list) -> str:
    """The steps as one goal for use_app: 'open the Meshy panel in the sidebar; press N; click Bridge On'."""
    out = []
    for s in steps:
        s = re.sub(r"^(?:then\s+)", "", s.strip(), flags=re.I)
        out.append(s[0].lower() + s[1:] if s else s)
    return "; ".join(out)[:300]


def _flat(text) -> str:
    return re.sub(r"\W+", " ", str(text or "")).strip().casefold()


def plan_for(said: str, history=None, uid: str = "") -> Optional[dict]:
    """{'steps': [use_app ...], 'app': ..., 'review': True} (or {'ask': question}) for a spoken list of UI steps; None otherwise."""
    steps = split(said)
    if not steps:
        return None
    app = _app_of(steps, _resolve) or _app_named_before(history, _resolve, said)
    if not app:
        spoken = ", then ".join(re.sub(r"^then\s+", "", s, flags=re.I)[:60] for s in steps)
        question = f"That's {plural(len(steps), 'step')}, sir: {spoken}. Which app are they in?"
        with _lock:
            _ASKED[uid] = (steps, time.monotonic(), question)
        return {"ask": question}
    return {"steps": [{"tool": "use_app", "arguments": {"app": app, "goal": goal(steps)}}], "app": app, "review": True}


def _last_jarvis(history) -> str:
    for m in reversed([m for m in (history or []) if isinstance(m, dict)]):
        if m.get("role") == "assistant":
            c = m.get("content")
            return c if isinstance(c, str) else " ".join(
                (b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "") or "") for b in c or [])
    return ""


def answer_for(said: str, uid: str = "", history=None) -> Optional[dict]:
    """His bare answer to 'Which app are they in?' ("In Blender.", "Blender."): the steps asked about, in that app, to be asked
    about once more. Only when that question was the LAST thing Jarvis said, within ASK_SECONDS, and the line is nothing but an app
    name. Any other line drops the held steps."""
    with _lock:
        held = _ASKED.pop(uid, None)
    if not held or time.monotonic() - held[1] > ASK_SECONDS or _flat(_last_jarvis(history)) != _flat(held[2]):
        return None
    text = re.sub(r"^\W*(?:(?:it'?s|they'?re|that'?s|in|inside|on|the)\s+)+", "", _CHANNEL.sub("", said or "").strip(), flags=re.I)
    text = text.strip(" .!?,")
    if not text or len(text.split()) > 3:
        return None
    app = _resolve(text)
    if not app or str(app).casefold().split()[0] != text.casefold().split()[0]:
        return None
    return {"steps": [{"tool": "use_app", "arguments": {"app": app, "goal": goal(held[0])}}], "app": app, "review": True}


def reset() -> None:
    with _lock:
        _ASKED.clear()
