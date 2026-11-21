"""Protocols: "when X happens, do Y" kept as DATA per person (data/users/<id>/protocols.json), not as new code.

Before this, every proactive feature was its own hand-wired method in core/scheduler.py (reminders, prices, homelab,
briefing, nudges). A protocol is a named list of steps plus the triggers that start it:

  triggers   his spoken phrase ("gaming protocol", always), a time of day ("at 7 pm"), "when I come back to the PC"
  steps      wrappers around skills that already exist, nothing new: open an app (tools/apps.launch_and_wait), close an
             app (core/pcactions close_app: graceful, keeps its save prompt), close a site's tab (pcactions
             close_site_tab), set the volume (pcactions set_volume, measured after), notify (core/notify), say a line,
             start a focus block (core/focus)

Rules (owner 2026-11-16):
- Every step reports what actually happened: done only when the skill's own check says so (a launch that shows no
  window is "waiting", a close that leaves the window open is "failed"), and the run ends with a short per-step summary.
- A time or came-back trigger never acts until the owner approved THAT protocol ("approve the night protocol"). The
  approval binds to the exact steps and triggers (a hash): changing the protocol withdraws it. His own phrase needs no
  approval: saying it is the command.
- Nothing destructive in v1: no deleting, no forced closes, no closing a game (unsaved progress), no "close everything".
  A step the grammar does not know makes the whole creation refuse and say which part, never a guess.

Voice (parsed by pattern, no model, like core/commands.py): list, describe, run, create, delete, approve / withdraw
approval, and "how did the gaming protocol go" (the last run's per-step result, including a game confirmed later)."""
from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from core import storage
from core.words import plural

FILE = "protocols.json"
RUNS = "protocol-runs.json"
STATE = "protocol-state.json"
MAX_PROTOCOLS, MAX_STEPS, MAX_RUNS = 40, 12, 40
NEARLY = 0.85              # how alike (letters only) a heard name must be to a stored one (STT: "gaming protocols")
TIME_WINDOW_MIN = 10       # a time trigger fires within 10 minutes after its time, once a day (the loop ticks every 20 s)
GAME_CONFIRM_S = 180       # a game launched through Steam/Epic shows its window after a minute or more
SITES = ("youtube", "google", "bing", "duckduckgo", "wikipedia", "github")   # pcactions.validate's close_site_tab list

DONE, FAILED, SKIPPED, WAITING = "done", "failed", "skipped", "waiting"

# The first real protocol (owner 2026-11-16): close distractions, set the volume, open Discord and Apex Legends.
# The distraction is the one he closes most in his own logs: YouTube (a tab). ChatGPT, the other one he closes, is left out:
# on his PC the installed "ChatGPT" entry launches the OpenAI Codex app (shell:AppsFolder\OpenAI.Codex_...), where his
# coding agents run (review 2026-11-16). 50% is the level he asked for ("Put the volume up to 50%"). Apex is last so the
# game ends up in front. Do-not-disturb is left out: Jarvis has no such skill (core/focus is a Pomodoro timer; game mode
# already holds his own unprompted lines while a game runs).
# The first step (core/gameboost, 2026-11-20 "I want maximum performance") puts his heavy background work behind the game
# and closes idle launchers; all of it comes back when the game closes.
GAMING = {"name": "gaming", "builtin": True, "triggers": [],
          "steps": [{"do": "free_up"}, {"do": "close_tab", "target": "youtube"},
                    {"do": "set_volume", "percent": 50}, {"do": "open_app", "target": "discord"},
                    {"do": "open_app", "target": "apex legends"}]}
# Streaming (owner 2026-11-20: Kick, YouTube, TikTok and Twitch at once, Animaze to hide his face, maximum performance):
# the Gaming protocol's clearing, then his avatar, OBS and Discord, then OBS's check (core/obsstream), Apex last so it is in
# front. It never goes live by itself: "go live" is his own sentence, after he has seen what OBS is ready to send.
STREAMING = {"name": "streaming", "builtin": True, "triggers": [],
             "steps": [{"do": "free_up"}, {"do": "close_tab", "target": "youtube"}, {"do": "open_app", "target": "animaze"},
                       {"do": "open_app", "target": "obs studio"}, {"do": "open_app", "target": "discord"},
                       {"do": "stream_ready"}, {"do": "open_app", "target": "apex legends"}]}
DEFAULTS = (GAMING, STREAMING)
PC_STEPS = {"open_app", "close_app", "close_tab", "set_volume", "focus", "free_up", "stream_ready"}

# "And streaming protocol." (live 2026-11-20 23:48, said twice): a spoken connector before the name made the name "and streaming",
# which matched no protocol, so the line went to the model and it said "Got it, sir." with nothing run.
_LEAD = r"^\W*(?:(?:ok|okay|hey|jarvis|please|alright|right|so|now|and|also|then|just|i said|i say)[\s,]+)*"
_NAME = r"(?:the |my |a |an )?(?P<name>[a-z0-9][a-z0-9' -]{0,40}?)"
_NAME2 = _NAME.replace("?P<name>", "?P<name2>")
_P = r"\s+protocols?"
_END = r"(?:[\s,]+(?:now|please|sir|jarvis|for me|already|right now))*\W*$"
_RUN_VERB = r"(?:run|start|activate|engage|initiate|execute|launch|begin|trigger|kick off|fire up|go into|enter)"
_RUN = re.compile(_LEAD + rf"(?:(?:can|could|would|will) you (?:please )?(?P<verb1>{_RUN_VERB})\s+|(?P<verb2>{_RUN_VERB})\s+)?{_NAME}{_P}{_END}", re.I)
# Never a run: a negation, a cancel, filler, narration ("no gaming protocol", "never mind the gaming protocol").
_NOT_RUN = re.compile(r"\b(?:no|not|nope|never|don'?t|do not|never ?mind|forget it|um+|uh+|oh|hmm+|ran|was|were|yesterday|earlier|"
                      r"love|like|hate|use|using|about)\b", re.I)
_STOP = re.compile(_LEAD + rf"(?:stop|cancel|abort|halt|end|quit|kill|undo)\s+{_NAME}{_P}{_END}", re.I)
_LIST = re.compile(_LEAD + r"(?:(?:what|which) protocols?(?: do (?:i|you|we) have| have (?:i|you|we) got| are there| do you know| exist|"
                   r" are set(?: up)?| can you run)?|(?:list|show|tell me|read)(?: me)?(?: all)?(?: of)?(?: my| the| your)? protocols"
                   r"|(?:my|your|the) protocols)\W*$", re.I)
_DESCRIBE = re.compile(_LEAD + rf"(?:what(?:'s| is| does| do)(?: in)?|tell me about|describe|explain|show me|read me|what's on)\s+{_NAME}{_P}"
                       r"(?:\s+(?:do|does|contain|have|include|again))?\W*$", re.I)
_DELETE = re.compile(_LEAD + rf"(?:delete|remove|forget|erase|drop|get rid of|scrap|throw away)\s+{_NAME}{_P}{_END}", re.I)
_APPROVE = re.compile(_LEAD + rf"(?:i )?(?:approve|authori[sz]e)\s+{_NAME}{_P}"
                      r"(?:\s+(?:to run )?(?:on its own|by itself|automatically))?" + _END, re.I)
_WITHDRAW = re.compile(_LEAD + rf"(?:(?:un-?approve|revoke|withdraw(?: (?:the )?approval (?:for|of))?|disable|pause)\s+{_NAME}{_P}"
                       rf"|(?:stop|don'?t let|do not let)\s+{_NAME2}{_P}\s+(?:from )?running (?:on its own|by itself|automatically))" + _END, re.I)
_LAST = re.compile(_LEAD + rf"(?:how did|did)\s+{_NAME}{_P}\s+(?:go|work|do|run)(?: (?:just now|earlier|today))?\W*$"
                   rf"|{_LEAD}what happened (?:with|to|in)\s+{_NAME2}{_P}\W*$", re.I)
_CREATE = re.compile(_LEAD + r"(?:i want (?:you )?to |can you |could you |please )?(?:create|make|add|set up|setup|build|program|write)"
                     r"(?: me)?(?: a| an)?(?: new)?\s+(?:protocol (?:called|named)\s+(?P<n1>[a-z0-9][a-z0-9' -]{0,40}?)(?:\s+protocol)?"
                     r"|(?P<n2>[a-z0-9][a-z0-9' -]{0,40}?)\s+protocol)\s*(?:[,:;.-]+\s*|\s+(?:that|which|to|with|where it|it)\s+"
                     r"|\s+(?=(?:at \d|every (?:day|morning|evening|night)|daily|when(?:ever)? i|each time|every time|once i)))(?P<body>.+?)\W*$",
                     re.I)
_IS_APPROVED = re.compile(_LEAD + rf"(?:is|has)\s+{_NAME}{_P}\s+(?:been\s+)?(?:approved|authori[sz]ed|allowed to run(?: on its own| by itself)?|"
                          r"automatic|set to run (?:on its own|by itself|automatically)|on autopilot)\W*$"
                          rf"|{_LEAD}(?:does|will|can)\s+{_NAME2}{_P}\s+run (?:on its own|by itself|automatically)\W*$", re.I)
# "storm watch", "check storm watch", "storm watch status", "what's on storm watch?" (no word 'protocol' needed)
_STORM_ASK = re.compile(_LEAD + r"(?:(?:run|check|do|give me|read me|show me)(?: a| the| my)?\s+|what(?:'s| is) on(?: the| my)?\s+)?"
                        r"storm ?watch(?:\s+(?:status|report|check|update|now))?" + _END, re.I)
_QUESTION_LEAD = re.compile(r"^\W*(?:what|which|why|how|when|where|who|is|are|does|do|did|should|tell me)\b", re.I)

_lock = threading.Lock()
_running: set = set()


# -- the store -------------------------------------------------------------------------------------------------------
def norm(name: str) -> str:
    """'The Gaming Protocol.' / 'gaming protocols' / 'Jarvis, gaming' -> 'gaming'."""
    name = re.sub(r"^\W*(?:hey\s+)?jarvis\b\W*", "", (name or "").lower().replace("’", "'"))
    name = re.sub(r"[^a-z0-9' ]+", " ", name)
    name = re.sub(r"^(?:the|my|a|an)\s+", "", re.sub(r"\s+", " ", name).strip())
    return re.sub(r"\s+protocols?$", "", name).strip(" '")


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _is_owner(uid: str | None = None) -> bool:
    from core import users
    uid = uid or users.current()
    try:
        return users.all_users().get(uid, {}).get("role") == "owner"
    except Exception:  # noqa: BLE001 - unknown role: treated as not the owner (the safe side)
        return False


def _stored() -> list[dict]:
    return [p for p in storage.load(FILE, []) if isinstance(p, dict) and p.get("name")]


def protocols() -> list[dict]:
    """This person's protocols; the owner also has the built-in ones he has not deleted (a tombstone keeps one deleted)."""
    items = _stored()
    if _is_owner():
        have = {p["name"] for p in items}
        items += [copy.deepcopy(d) for d in DEFAULTS if d["name"] not in have]
    return [p for p in items if not p.get("deleted")]


def _put(proto: dict | None, *, remove: str = "") -> None:
    items = [p for p in _stored() if p["name"] not in {remove, (proto or {}).get("name")}]
    if proto is not None:
        items.append(proto)
    storage.save(FILE, items[-MAX_PROTOCOLS:])


def find(name: str) -> dict | None:
    """The protocol called `name`: exact, else the ONE stored name it is near (STT slips); two near ones -> neither."""
    want = norm(name)
    if not want:
        return None
    items = protocols()
    for p in items:
        if p["name"] == want:
            return p
    # Near only with the SAME number of words: "gamming" is "gaming", but "no gaming" / "um gaming" are not (a short
    # filler kept the letters 0.86 alike and ran the protocol: review 2026-11-16).
    near = [p for p in items if len(want.split()) == len(p["name"].split())
            and SequenceMatcher(None, _letters(want), _letters(p["name"])).ratio() >= NEARLY]
    return near[0] if len(near) == 1 else None


def fingerprint(proto: dict) -> str:
    """What an approval binds to: the exact steps and triggers."""
    body = json.dumps({"steps": proto.get("steps", []), "triggers": proto.get("triggers", [])}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def approved(proto: dict) -> bool:
    mark = proto.get("approved") or {}
    return bool(mark) and mark.get("hash") == fingerprint(proto)


# Storm Watch (owner 2026-11-16): the first trigger driven by OUTSIDE data (core/stormwatch: NHC storms, USGS quakes, NWS
# alerts near his pinned places). Its steps only warn (out loud, through core.attention) and ping his phone; nothing on
# the PC is changed. It ships APPROVED because he asked for exactly this ("warns out loud and pings the owner's phone");
# "withdraw approval for the storm watch protocol" stores an unapproved copy and the automatic warnings stop.
STORM_WATCH = {"name": "storm watch", "builtin": True,
               "triggers": [{"type": "storm", "places": ["springfield", "haiti"]}],
               "steps": [{"do": "warn"}, {"do": "ping"}]}
STORM_WATCH["approved"] = {"hash": fingerprint(STORM_WATCH), "at": "2026-11-16T12:00:00", "by": "the owner's request"}
DEFAULTS = DEFAULTS + (STORM_WATCH,)


# -- words -----------------------------------------------------------------------------------------------------------
_DISPLAY = {"youtube": "YouTube", "chatgpt": "ChatGPT", "github": "GitHub", "duckduckgo": "DuckDuckGo", "obs studio": "OBS Studio",
            "vs code": "VS Code", "vscode": "VS Code", "ea app": "EA app"}


def _app_name(target: str) -> str:
    key = str(target).strip().lower()
    return _DISPLAY.get(key) or " ".join(w if w.isupper() else w.capitalize() for w in str(target).split())


def step_text(step: dict) -> str:
    do, target = step.get("do"), step.get("target", "")
    if do == "open_app":
        return f"open {_app_name(target)}"
    if do == "close_app":
        return f"close {_app_name(target)}"
    if do == "close_tab":
        return f"close the {_app_name(target)} tab"
    if do == "set_volume":
        return f"set the volume to {step.get('percent')}%"
    if do == "notify":
        return f"send you a notification: {step.get('text', '')}"
    if do == "say":
        return f"say \"{step.get('text', '')}\""
    if do == "focus":
        return f"start a {step.get('minutes', 25)}-minute focus block"
    if do == "warn":
        return "warn you out loud"
    if do == "ping":
        return "ping your phone"
    if do == "free_up":
        return "clear the background for the game"
    if do == "stream_ready":
        return "check OBS is ready to stream (without going live)"
    return str(do)


def _clock(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(2000, 1, 1, h, m).strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def trigger_text(proto: dict) -> str:
    parts = [f"when you say '{proto['name']} protocol'"]
    for t in proto.get("triggers", []):
        if t.get("type") == "time":
            parts.append(f"{_days_text(t.get('days'))} at {_clock(t['at'])}")
        elif t.get("type") == "came_back":
            parts.append("when you come back to the PC")
        elif t.get("type") == "storm":
            from core import stormwatch
            names = [stormwatch.PLACES[p]["name"] for p in t.get("places", []) if p in stormwatch.PLACES]
            where = (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]) if names else "your pinned places"
            # "or": any one of his places is enough (live check 2026-11-16 read "covers Springfield, Illinois and Haiti")
            parts.append(f"when a hurricane or tropical storm is forecast near {where}, or a strong earthquake or a severe "
                         "weather alert hits one of them")
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]


_WEEKDAYS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")


def _days_text(days) -> str:
    """A time trigger's days (0 = Monday, as datetime.weekday); none = every day."""
    days = sorted({int(d) for d in days or [] if str(d).isdigit() and 0 <= int(d) <= 6})
    if not days or len(days) == 7:
        return "every day"
    if days == [0, 1, 2, 3, 4]:
        return "on weekdays"
    if days == [5, 6]:
        return "on weekends"
    return "on " + _join([_WEEKDAYS[d] for d in days])


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def describe(proto: dict) -> str:
    steps = [step_text(s) for s in proto.get("steps", [])]
    out = f"The {proto['name']} protocol will {_join(steps)}. It runs {trigger_text(proto)}."
    if proto.get("triggers"):
        out += (" It's approved to run on its own." if approved(proto)
                else f" It won't run on its own until you say 'approve the {proto['name']} protocol'.")
    return out


def listing() -> str:
    items = protocols()
    if not items:
        return "You don't have any protocols yet. Say, for example: 'create a protocol called study: open Notepad, set the volume to 20'."
    names = [f"{p['name']}" + (" (automatic, approved)" if p.get("triggers") and approved(p) else
                               " (automatic, waiting for your approval)" if p.get("triggers") else "") for p in items]
    return (f"You have {len(items)} protocol{'s' if len(items) != 1 else ''}: {_join(names)}. "
            "Say a name followed by 'protocol' to run one, or ask what one does.")


# -- creating by voice -----------------------------------------------------------------------------------------------
_TIME = re.compile(r"\b(?:(?:every|each) (?:day|morning|evening|night) |daily )?at (?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap])?\.?\s*(?:m\b\.?)?"
                   r"(?:\s+(?:every|each) day|\s+daily)?", re.I)
_CAME_BACK = re.compile(r"\b(?:when(?:ever)?|every time|each time|once) i(?:'m| am)? (?:come back|get back|return|back|sit down)"
                        r"(?: (?:to|at) (?:the|my) (?:pc|computer|desk))?", re.I)
_CONNECT = re.compile(r"^(?:(?:it )?(?:should |will |must )?(?:runs?|starts?|goes)\s*)|^(?:and|then|to|that|which)\s+", re.I)
_SPLIT = re.compile(r"(\s*(?:,|;|\band then\b|\bthen\b|\band\b)\s*)", re.I)     # kept: a spoken line may hold "and"
_THIRD = {"opens": "open", "closes": "close", "sets": "set", "says": "say", "launches": "launch", "starts": "start",
          "notifies": "notify", "turns": "turn", "puts": "put", "quits": "quit", "tells": "tell", "sends": "send"}
_DESTRUCTIVE = re.compile(r"\b(?:delete|erase|wipe|format|remove|uninstall|kill|force|shut ?down|shutdown|restart|reboot|log ?off|"
                          r"sign out|empty|trash|terminate|end task|everything|all (?:apps|programs|windows|tabs)|every)\b", re.I)
_DND = re.compile(r"\b(?:do not disturb|don'?t disturb|dnd|silence (?:my )?notifications|mute (?:my )?notifications)\b", re.I)


def _step(phrase: str) -> dict | str:
    """One spoken step -> a step dict, or the reason it can't be one (said back to him)."""
    s = re.sub(r"\s+", " ", phrase.strip(" .!'\"")).strip()
    s = re.sub(r"^(?:please |also |and |then |to |jarvis,? )+", "", s, flags=re.I)
    if not s:
        return ""
    first, _, rest = s.partition(" ")
    if first.lower() in _THIRD:                      # "that opens Spotify and sets the volume to 30"
        s = (_THIRD[first.lower()] + " " + rest).strip()
    low = s.lower()
    if _DND.search(low):
        return "do-not-disturb isn't a skill I have yet"
    if _DESTRUCTIVE.search(low):
        return f"'{s}' would delete, force or close everything, and protocols can't do that"
    m = re.match(r"^(?:set|put|turn|change|make)? ?(?:the |my )?volume (?:to |at |up to |down to )?(?P<n>\d{1,3})\s*(?:%|percent)?$", low)
    if m:
        n = int(m.group("n"))
        return {"do": "set_volume", "percent": n} if 0 <= n <= 100 else "a volume has to be between 0 and 100"
    m = re.match(r"^(?:start|begin|turn on|switch on|enable)?\s*(?:a |my )?focus(?: mode| session| block| timer)?(?: for (?P<n>\d{1,3}) minutes?)?$", low)
    if m:
        return {"do": "focus", "minutes": max(5, min(int(m.group("n") or 25), 180))}
    m = re.match(r"^(?:close|shut) (?:the |my )?(?P<x>[a-z0-9 .'-]+?) tab$", low)
    if m:
        site = m.group("x").replace(" ", "").replace(".com", "")
        return {"do": "close_tab", "target": site} if site in SITES else f"I can only close tabs for {_join(list(SITES))}"
    m = re.match(r"^(?:close|quit|exit) (?:down |out of )?(?:the |my )?(?P<x>[a-z0-9 .'+&-]{2,40})$", low)
    if m:
        target = m.group("x").removesuffix(" app").strip()
        return f"'{s}' doesn't name an app" if target in _VAGUE else {"do": "close_app", "target": target}
    m = re.match(r"^(?:open|launch|start|run|fire up|bring up|load) (?:up )?(?:the |my )?(?P<x>[a-z0-9 .'+&-]{2,40})$", low)
    if m:
        target = m.group("x").removesuffix(" app").strip()
        return f"'{s}' doesn't name an app" if target in _VAGUE else {"do": "open_app", "target": target}
    m = re.match(r"^(?:notify me|send me a notification|send a notification|push a notification|ping me|alert me)"
                 r"(?: saying| that| with| to say| to)?[\s:]*(?P<x>.{1,200})$", s, re.I)
    if m:
        return {"do": "notify", "text": m.group("x").strip()}
    m = re.match(r"^(?:say|tell me|announce|remind me)(?: that)?[\s:]*(?P<x>.{1,200})$", s, re.I)
    if m:
        return {"do": "say", "text": m.group("x").strip()}
    return f"I don't know how to '{s}' in a protocol"


_VAGUE = {"it", "this", "that", "them", "these", "those", "the game", "game", "a game", "window", "this window", "that window",
          "the window", "app", "this app", "that app", "something", "stuff", "everything", "all", "apps", "programs", "it all"}
_SPOKEN_STEP = re.compile(r"\b(?:say|tell me|announce|remind me|notify me|send (?:me )?a notification|push a notification|ping me|alert me)\b",
                          re.I)


def parse_body(body: str) -> tuple[list[dict], list[dict], str]:
    """'at 7 pm: open spotify, set the volume to 30' -> (steps, triggers, problem). A problem refuses the whole thing.
    Triggers are read only BEFORE the first spoken / notification line: "say meet me at 7 pm" is a line, not a time."""
    triggers: list[dict] = []
    text = " " + body.strip() + " "
    spoken = _SPOKEN_STEP.search(text)
    head_end = spoken.start() if spoken else len(text)
    m = _TIME.search(text, 0, head_end)
    if m:
        h, mi, ap = int(m.group("h")), int(m.group("m") or 0), (m.group("ap") or "").lower()
        if ap:
            if not 1 <= h <= 12:
                return [], [], f"{m.group(0).strip()} isn't a time I understand"
            h = h % 12 + (12 if ap == "p" else 0)
        elif h <= 12 and not m.group("m"):
            return [], [], f"say whether {m.group(0).strip()} is AM or PM"
        if h > 23 or mi > 59:
            return [], [], f"{m.group(0).strip()} isn't a time I understand"
        triggers.append({"type": "time", "at": f"{h:02d}:{mi:02d}"})
        text = text[:m.start()] + " " + text[m.end():]
        head_end -= m.end() - m.start() - 1
    c = _CAME_BACK.search(text, 0, head_end)
    if c:
        triggers.append({"type": "came_back"})
        text = text[:c.start()] + " " + text[c.end():]
    text = re.sub(r"^\s*[,:;.-]+", "", text).strip()
    text = _CONNECT.sub("", text).strip(" ,:;.-")
    steps: list[dict] = []
    pieces = _SPLIT.split(text)
    for i in range(0, len(pieces), 2):
        part = _CONNECT.sub("", pieces[i].strip(" ,:;.-")).strip()
        if not part:
            continue
        step = _step(part)
        if isinstance(step, str) and steps and steps[-1]["do"] in ("say", "notify"):
            steps[-1]["text"] += pieces[i - 1] + pieces[i].strip()   # "say good luck and have fun": one line, not two steps
            continue
        if isinstance(step, str):
            return [], [], step
        if step:
            steps.append(step)
    if not steps:
        return [], [], "I didn't hear any steps"
    if len(steps) > MAX_STEPS:
        return [], [], f"a protocol can have at most {plural(MAX_STEPS, 'step')}"
    return steps, triggers, ""


def resolve(target: str) -> str | None:
    """The installed app a spoken name means, only when the catalog's match really is that name: tools/apps._find matches
    loosely (difflib 0.6: "file manager" -> Task Manager, "code" -> Claude), fine when he is there to see it, not for a
    recipe that runs later (review 2026-11-16)."""
    from tools import apps
    canonical = apps.resolve_app(target)
    if not canonical:
        return None
    def letters(text: str) -> str:
        return re.sub(r"[^a-z0-9+#]", "", text.lower())
    a, b = letters(target), letters(canonical)
    # "windows terminal" / "settings app" mean Terminal / Settings: generic words are not part of the name
    said = [w for w in target.lower().split() if w not in {"windows", "microsoft", "app", "application", "program", "the", "my"}]
    named = canonical.lower().split()
    # "apex" -> Apex Legends (his words inside the name); never "visual studio code" -> Visual Studio (name inside his words)
    if (a == b or (len(a) >= 3 and said and all(w in named for w in said))
            or (len(a) >= 4 and b.startswith(a) and canonical in apps.BUILTINS)          # "calc" -> Calculator, as tools/apps does
            or (len(said) == len(named) and SequenceMatcher(None, a, b).ratio() >= 0.9)):
        return canonical
    return None


def _check_apps(steps: list[dict]) -> str:
    """On the PC, every app a protocol opens or closes must be installed under that name; elsewhere it is checked at run time."""
    from core import pcbridge
    if not pcbridge.local_pc():
        return ""
    for step in steps:
        if step["do"] in ("open_app", "close_app"):
            try:
                found = resolve(step["target"])
            except Exception:  # noqa: BLE001 - the catalog could not be read: checked when it runs
                continue
            if not found:
                return f"I can't find an app called {_app_name(step['target'])} on the PC"
    return ""


def create(name: str, body: str) -> str:
    name = norm(name)
    if not name or len(name) > 40:
        return "I didn't catch a name for that protocol."
    steps, triggers, problem = parse_body(body)
    if not problem:
        problem = _check_apps(steps)
    if problem:
        return f"I didn't save the {name} protocol: {problem}. Nothing was changed."
    old = find(name)
    old = old if old and old["name"] == name else None
    proto = {"name": name, "steps": steps, "triggers": triggers, "created": datetime.now().isoformat(timespec="seconds")}
    _put(proto)
    said = (f"Replaced the {name} protocol (it used to {_join([step_text(s) for s in old.get('steps', [])])}). It will now "
            if old else f"Saved the {name} protocol: it will ") + f"{_join([step_text(s) for s in steps])}."
    if triggers:
        said += f" It runs {trigger_text(proto)}, but it won't run on its own until you say 'approve the {name} protocol'."
    else:
        said += f" Say '{name} protocol' to run it."
    inside = next((t for s in steps if s["do"] in ("say", "notify") for t in [_TIME.search(" " + s["text"] + " ") or
                   _CAME_BACK.search(s["text"])] if t), None)
    if inside:
        said += (f" I took '{inside.group(0).strip()}' as part of what to say, not as when to run; to make it run then, say "
                 f"when first, like 'create a protocol called {name} at 7 pm: ...'.")
    return said


def delete(name: str) -> str:
    proto = find(name)
    if proto is None:
        return f"There's no {norm(name)} protocol to delete."
    if proto.get("learned"):
        from core import habits           # a habit he deleted is never suggested again
        habits.decline(proto["learned"].get("habit", ""))
    if proto.get("builtin"):
        _put({"name": proto["name"], "deleted": datetime.now().isoformat(timespec="seconds")})     # a tombstone: not seeded back
    else:
        _put(None, remove=proto["name"])
    return f"Deleted the {proto['name']} protocol."


def approve(name: str, ok: bool = True) -> str:
    proto = find(name)
    if proto is None:
        return f"There's no {norm(name)} protocol."
    if not _is_owner():
        return "Only the PC's owner can approve a protocol to run on its own."
    if not proto.get("triggers"):
        return (f"The {proto['name']} protocol only runs when you say it, so it doesn't need approval."
                if ok else f"The {proto['name']} protocol never runs on its own; it only runs when you say it.")
    proto = {k: v for k, v in proto.items() if k != "approved"}
    if ok:
        proto["approved"] = {"hash": fingerprint(proto), "at": datetime.now().isoformat(timespec="seconds")}
    _put(proto)
    return (f"Approved. The {proto['name']} protocol will now run {trigger_text(proto)}." if ok else
            f"Done: the {proto['name']} protocol won't run on its own any more. Saying '{proto['name']} protocol' still runs it.")


# -- running ---------------------------------------------------------------------------------------------------------
def _pc():
    from core import pcactions
    return pcactions


def _open_app(step: dict, ctx: dict) -> tuple[str, str]:
    from tools import apps
    canonical = resolve(step["target"])
    if not canonical:
        return FAILED, f"I can't find an app called {_app_name(step['target'])}, so nothing was opened"
    name = _app_name(canonical)
    match = apps._find(canonical)
    target = match[1] if match else ""
    if apps.is_game(str(target)):
        if apps.game_processes(canonical):
            return DONE, f"{name} is already running"
        detail, ready = apps.launch_and_wait(canonical, timeout=8)
        if ready:
            return DONE, f"opened {name}"
        if detail.startswith("No installed app"):
            return FAILED, f"{name} isn't installed"
        ctx.setdefault("games", []).append(canonical)
        ctx["waiting_game"] = canonical
        return WAITING, f"asked {'Steam' if str(target).startswith('steam:') else 'its launcher'} to start {name}; not confirmed running yet"
    detail, ready = apps.launch_and_wait(canonical, timeout=20)
    return (DONE, f"opened {name}") if ready else (FAILED, detail.rstrip("."))


def _close_app(step: dict, ctx: dict) -> tuple[str, str]:
    from tools import apps
    canonical = resolve(step["target"])
    if not canonical:
        return SKIPPED, f"I can't find an app called {_app_name(step['target'])}, so nothing was closed"
    name = _app_name(canonical)
    if canonical.casefold().replace(" ", "").removesuffix(".exe") in apps.PROTECTED:
        return FAILED, f"{name} is protected"
    match = apps._find(canonical)
    if apps.game_processes(canonical) or (match and apps.is_game(str(match[1]))):
        return FAILED, f"I don't close games from a protocol (unsaved progress); {name} was left alone"
    if not apps.app_running(canonical) and not apps.visible_windows_for_app(canonical):
        return SKIPPED, f"{name} wasn't open"
    try:
        from core import saveprompt, users
        asked_before = saveprompt.pending(users.current())
    except Exception:  # noqa: BLE001
        asked_before = None
    result = _pc().perform("close_app", {"app": canonical}, approved=True)     # graceful: an unsaved document keeps its save prompt
    if result.get("verified"):
        try:
            still = apps.app_running(canonical)
        except Exception:  # noqa: BLE001
            still = False
        # verified means its windows are gone; a tray app (Discord) can keep running without one: said, not hidden
        return DONE, f"closed {name}'s window; it still runs in the background" if still else f"closed {name}"
    if ctx.get("source") == "trigger" and result.get("error_kind") == "needs_owner":
        try:       # nobody asked him: no "Save it before I close it?" waiting for a stray "no" that would throw his work away
            from core import saveprompt, users
            saveprompt.withdraw(users.current(), asked_before)     # only the question THIS close made, never one he is answering
        except Exception:  # noqa: BLE001
            pass
        return FAILED, f"{name} is still open with unsaved work, so I left it alone"
    return FAILED, str(result.get("detail") or f"{name} is still open").rstrip(".")


def _close_tab(step: dict, ctx: dict) -> tuple[str, str]:
    site = step["target"]
    try:
        result = _pc().perform("close_site_tab", {"site": site}, approved=True)
    except RuntimeError as error:
        if str(error).startswith(("No visible browser tab", "No browser tab")):
            return SKIPPED, f"no {_app_name(site)} tab was showing"
        raise
    return (DONE, f"closed the {_app_name(site)} tab") if result.get("verified") else (FAILED, str(result.get("detail")).rstrip("."))


def _set_volume(step: dict, ctx: dict) -> tuple[str, str]:
    result = _pc().perform("set_volume", {"percent": int(step["percent"])})
    return (DONE, f"volume is {result.get('percent')}%") if result.get("verified") else (FAILED, str(result.get("detail")).rstrip("."))


def _notify(step: dict, ctx: dict) -> tuple[str, str]:
    from core import notify, users
    notify.notify(f"{ctx['name'].title()} protocol", step["text"], users.current(), "jarvis")
    return DONE, "notification sent (delivery to the phone isn't confirmed)"


def _say(step: dict, ctx: dict) -> tuple[str, str]:
    ctx.setdefault("spoken", []).append(step["text"])
    return DONE, "said it"


def _focus(step: dict, ctx: dict) -> tuple[str, str]:
    from core import focus
    if focus.status().get("phase") != "off":
        return SKIPPED, "a focus session is already running"
    state = focus.start(int(step.get("minutes", 25)))
    return (DONE, f"focus block started ({plural(state.get('focus_min'), 'minute')})") if state.get("phase") == "focus" else (FAILED, "focus mode didn't start")


def _warn(step: dict, ctx: dict) -> tuple[str, str]:
    """Storm Watch: say the warning (the PC speaks; the laptop leaves it to the PC)."""
    from core import pcbridge, stormwatch
    alert = ctx.get("alert")
    if not alert:
        return SKIPPED, "there was nothing to warn about"
    if not pcbridge.local_pc():
        return SKIPPED, "the PC says warnings out loud"
    return DONE, stormwatch.speak(alert)


def _ping(step: dict, ctx: dict) -> tuple[str, str]:
    """Storm Watch: push it to his phone (the laptop does it when there is one: it runs when the PC is off)."""
    from core import stormwatch
    alert = ctx.get("alert")
    if not alert:
        return SKIPPED, "there was nothing to send"
    if stormwatch.laptop_pings():
        return SKIPPED, "the laptop sends the phone alert"
    return DONE, stormwatch.ping(alert)


def _free_up(step: dict, ctx: dict) -> tuple[str, str]:
    """Heavy background work behind the game, idle launchers closed (core/gameboost); put back when the game closes."""
    from core import gameboost, pcbridge
    if not pcbridge.local_pc():
        return SKIPPED, "clearing the background happens on the PC"
    _changed, detail = gameboost.free_up()
    try:
        from tools import gaming
        recorders = gaming.performance_check().splitlines()[0].split(". NVIDIA", 1)[0]     # "Other recorders: ... ."
        if recorders and not recorders.startswith("Other recorders: none"):
            detail += "; FPS note: " + recorders.rstrip(".").replace("Other recorders: ", "also recording in the background: ")
    except Exception:  # noqa: BLE001 - only a note: the step itself is what was done above
        pass
    return DONE, detail            # "nothing heavy was running" is the usual, good case, not a step that did nothing


def _stream_ready(step: dict, ctx: dict) -> tuple[str, str]:
    """Reads OBS's setup only: going live is his own 'go live' (core/obsstream), never a protocol step."""
    from core import obsstream, pcbridge
    if not pcbridge.local_pc():
        return SKIPPED, "OBS runs on the PC"
    ok, detail = obsstream.ready_text()
    return (DONE if ok else FAILED), detail


ACTIONS = {"open_app": _open_app, "close_app": _close_app, "close_tab": _close_tab, "set_volume": _set_volume,
           "notify": _notify, "say": _say, "focus": _focus, "warn": _warn, "ping": _ping, "free_up": _free_up,
           "stream_ready": _stream_ready}


_record_lock = threading.Lock()


def _record(entry: dict) -> None:
    with _record_lock:          # the run and its game confirmation write the same file from two threads
        runs = [r for r in storage.load(RUNS, []) if isinstance(r, dict) and r.get("id") != entry["id"]]
        runs.append(copy.deepcopy(entry))
        storage.save(RUNS, runs[-MAX_RUNS:])


def last_run(name: str, *, ran: bool = False) -> dict | None:
    """The newest record of this protocol; with `ran`, the newest one in which steps actually ran (not a skip note)."""
    want = norm(name)
    return next((r for r in reversed(storage.load(RUNS, [])) if isinstance(r, dict) and r.get("name") == want
                 and (r.get("steps") or not ran)), None)


def approval_status(proto: dict) -> str:
    """Whether a protocol may act on its own: read from the data, never guessed."""
    if not proto.get("triggers"):
        return (f"The {proto['name']} protocol has no time or come-back trigger, so there's nothing to approve: "
                f"it only runs when you say '{proto['name']} protocol'.")
    if approved(proto):
        return f"Yes. The {proto['name']} protocol is approved to run {trigger_text(proto)}."
    return (f"No. The {proto['name']} protocol won't run on its own until you say 'approve the {proto['name']} protocol'; "
            f"until then it only runs when you say it.")


def how_it_went(name: str) -> str | None:
    newest, real = last_run(name), last_run(name, ran=True)
    if newest is None:
        return None
    if real is None or real.get("id") == newest.get("id"):
        return summary(newest)
    return summary(real) + f" Since then, a scheduled run was skipped: {newest.get('stopped', 'it did not run')}."


def summary(entry: dict) -> str:
    """'Gaming protocol: 3 of 5 done. Done: ... Failed: ... Waiting: ...' (one clause per step, by outcome)."""
    rows = entry.get("steps", [])
    title = f"{entry['name'].capitalize()} protocol" + (f" ({entry['about']})" if entry.get("about") else "")
    done = [r for r in rows if r["status"] == DONE]
    if not rows and entry.get("stopped"):
        return f"{title} didn't run: {entry['stopped']}."
    if rows and len(done) == len(rows):
        count = "the one step" if len(rows) == 1 else "both steps" if len(rows) == 2 else "all " + plural(len(rows), "step")
        out = f"{title}: {count} done. " + "; ".join(r["detail"] for r in done) + "."
    else:
        out = f"{title}: {len(done)} of {plural(len(rows), 'step')} done."
        for status, label in ((DONE, "Done"), (FAILED, "Failed"), (WAITING, "Waiting"), (SKIPPED, "Nothing to do")):
            these = [r for r in rows if r["status"] == status]
            if these:
                out += f" {label}: " + "; ".join(r["detail"] for r in these) + "."
    if entry.get("stopped"):
        out += f" Stopped early: {entry['stopped']}."
    return out


def run(proto: dict, source: str = "voice", *, alert: dict | None = None, record: bool = True) -> dict:
    """Run every step in order, each judged by its own skill's check; returns the run record (kept in RUNS when `record`:
    the laptop's Storm Watch runs are not, so only the PC writes that synced file). `alert` is an outside trigger's event."""
    from core import users
    name = proto["name"]
    key = (users.current(), name)
    with _lock:
        if key in _running:
            return {"name": name, "busy": True, "steps": []}
        _running.add(key)
    entry = {"id": f"{name}-{time.time():.3f}", "name": name, "source": source, "uid": users.current(),
             "started": datetime.now().isoformat(timespec="seconds"), "steps": []}
    if alert:
        entry["about"] = alert.get("title", "")
    ctx: dict = {"name": name, "source": source, "alert": alert}
    try:
        for step in proto.get("steps", []):
            label = step_text(step)
            fn = ACTIONS.get(step.get("do"))
            if fn is None:
                status, detail = FAILED, f"'{label}' isn't a step I know"
            elif step.get("do") in PC_STEPS and not _is_owner():
                status, detail = FAILED, f"{label}: PC steps run for the PC's owner only"
            else:
                ctx.pop("waiting_game", None)
                try:
                    from core import turntrace
                    with turntrace.timed("tool", f"protocol:{step.get('do')}"):   # on the turn's ledger (main.py: never run twice)
                        status, detail = fn(step, ctx)
                except Exception as error:  # noqa: BLE001 - a step that raised did not happen: said as failed, never as done
                    status, detail = FAILED, f"{label}: {str(error).strip().rstrip('.') or type(error).__name__}"
            row = {"step": label, "status": status, "detail": detail}
            if status == WAITING and ctx.get("waiting_game"):
                row["game"] = ctx["waiting_game"]          # the confirmation finds its row by the game, not by the words
            entry["steps"].append(row)
    finally:
        with _lock:
            _running.discard(key)
    entry["spoken"] = ctx.get("spoken", [])
    entry["finished"] = datetime.now().isoformat(timespec="seconds")
    if record:
        _record(entry)
    if ctx.get("games"):
        threading.Thread(target=_confirm_games, args=(entry, list(ctx["games"]), users.current()), daemon=True,
                         name=f"protocol-{name}-game").start()
    return entry


def _confirm_games(entry: dict, games: list[str], uid: str, wait: float = GAME_CONFIRM_S, poll: float = 2.0) -> None:
    """A game asked of Steam/Epic shows up a minute or more later: the run record says whether it really started."""
    from core import users
    from tools import apps
    deadline = time.monotonic() + wait
    left = set(games)
    started = time.monotonic()
    with users.acting_as(uid):
        while left and time.monotonic() < deadline:
            for game in list(left):
                try:
                    running = bool(apps.game_processes(game))
                except Exception:  # noqa: BLE001
                    running = False
                if running:
                    left.discard(game)
                    _mark(entry, game, DONE, f"{_app_name(game)} is running (confirmed {time.monotonic() - started:.0f} s after launch)")
            # only games still not running are blamed on a launcher waiting for him (review: a running game stays done)
            blocked = launcher_blocked() if left and time.monotonic() - started > BLOCKED_GRACE_S else ""
            if blocked:
                for game in left:
                    _mark(entry, game, FAILED, f"{_app_name(game)} didn't start: {blocked}")
                return
            if left:
                time.sleep(poll)
        blocked = launcher_blocked() if left else ""
        for game in left:
            _mark(entry, game, FAILED, f"{_app_name(game)} didn't start: {blocked}" if blocked
                  else f"{_app_name(game)} didn't start within {plural(int(wait // 60), 'minute')}")


BLOCKED_GRACE_S = 20      # a launcher's sign-in window that is still there after this is waiting on him, not loading
_BLOCKERS = (("sign in to steam", "Steam is asking you to sign in"), ("steam sign in", "Steam is asking you to sign in"),
             ("steam guard", "Steam is asking for your Steam Guard code"))


def launcher_blocked() -> str:
    """A visible launcher window that waits on the owner (only he signs in: Jarvis never types a password)."""
    try:
        from tools import pcops
        windows = pcops.windows()
    except Exception:  # noqa: BLE001
        return ""
    for w in windows:
        title = str(w.get("title", "")).casefold()
        if str(w.get("process", "")).casefold() in ("steam.exe", "steamwebhelper.exe"):     # Steam's own window, not a page about it
            for needle, said in _BLOCKERS:
                if needle in title:
                    return said
    return ""


def _mark(entry: dict, game: str, status: str, detail: str) -> None:
    for row in entry["steps"]:
        if row["status"] == WAITING and row.get("game") == game:
            row.update(status=status, detail=detail)
    _record(entry)


def run_text(proto: dict, source: str = "voice") -> str:
    entry = run(proto, source)
    if entry.get("busy"):
        return f"The {proto['name']} protocol is already running."
    spoken = " ".join(entry.get("spoken") or [])
    return (summary(entry) + (" " + spoken if spoken else "")).strip()


# -- voice -----------------------------------------------------------------------------------------------------------
def _name(m: re.Match) -> str:
    return next((g for g in (m.groupdict().get(k) for k in ("name", "name2", "n1", "n2")) if g), "") or ""


def handle(text: str) -> str | None:
    """A protocol sentence -> the answer; None for every other sentence. Order matters: a question about a protocol is
    answered, never run (a question read as an order is the dangerous mistake)."""
    raw = re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", (text or "").strip(), flags=re.I)
    raw = raw.replace("’", "'").strip()
    if raw and _STORM_ASK.match(raw):
        proto = find("storm watch")
        return start(proto, raw) if proto is not None and storm_places(proto) else None
    if not raw or len(raw) > 400 or "protocol" not in raw.lower():
        return None
    m = _CREATE.match(raw)
    if m:
        return create(_name(m), m.group("body"))
    if _LIST.match(raw):
        return listing()
    m = _IS_APPROVED.match(raw)
    if m:
        proto = find(_name(m))
        return approval_status(proto) if proto is not None else None
    for pattern, action in ((_DESCRIBE, "describe"), (_LAST, "last"), (_DELETE, "delete"), (_APPROVE, "approve"),
                            (_WITHDRAW, "withdraw")):
        m = pattern.match(raw)
        if not m:
            continue
        name = _name(m)
        if action == "delete":
            return delete(name)
        if action == "approve":
            return approve(name, True)
        if action == "withdraw":
            return approve(name, False)
        proto = find(name)
        if proto is None and action == "describe":
            return None           # "what is a network protocol" is a question for the model, not about one of his
        if proto is None:
            return _unknown(name)
        if action == "describe":
            return describe(proto)
        return how_it_went(proto["name"]) or f"The {proto['name']} protocol hasn't run yet."
    m = _STOP.match(raw)
    if m:
        proto = find(_name(m))
        if proto is None:
            return None
        from core import users
        if (users.current(), proto["name"]) in _running:
            return (f"I can't stop the {proto['name']} protocol halfway in this version; its remaining steps finish on their own. "
                    f"Ask 'how did the {proto['name']} protocol go' to see each step.")
        if any(s.get("do") == "free_up" for s in proto.get("steps", [])) and _is_owner():
            from core import pcbridge
            if not pcbridge.local_pc():
                from core import pcfast        # from the phone: what was cleared is on the PC, so the PC puts it back
                reply = pcfast.ask_pc(raw)
                return reply or "I didn't get an answer from the PC, so I can't tell whether it put everything back yet."
            from core import gameboost
            back = gameboost.restore() if gameboost.active() else ""
            live = ""
            if any(s.get("do") == "stream_ready" for s in proto.get("steps", [])):
                from core import obsstream     # stopping the protocol never ends a broadcast by itself
                if obsstream.still_live():
                    live = " You're still live: say 'end the stream' to stop broadcasting."
            if back:
                return "Putting everything back: " + back + live
            if live:
                return f"The {proto['name']} protocol isn't running.{live}"
        return f"The {proto['name']} protocol isn't running, so there's nothing to stop."
    hit = match_run(raw)
    if hit is None:
        proto = run_in_sentences(raw)
        return start(proto, raw) if proto is not None else None
    proto, name, ordered = hit
    if proto is None:
        # Only an explicit order ("run the house party protocol") is told there is none; a bare "TCP protocol" is the model's.
        return _unknown(name) if ordered else None
    return start(proto, raw)


def match_run(raw: str) -> tuple[dict | None, str, bool] | None:
    """One sentence as an order to run a protocol, without running it: (his protocol or None, the name heard, whether a run
    verb was said); None when the sentence is no run order (a question, a negation, filler, narration)."""
    raw = (raw or "").replace("’", "'").strip()
    if "protocol" not in raw.lower():
        return None
    if raw.rstrip().endswith("?") and not re.match(r"^\W*(?:can|could|would|will) you\b", raw, re.I):
        return None
    if _QUESTION_LEAD.match(raw):
        return None
    m = _RUN.match(raw)
    if not m:
        return None
    name = _name(m)
    span = m.span("name")
    if _NOT_RUN.search(raw[:span[0]] + " " + raw[span[1]:]):       # outside his protocol's own name ("the like protocol" runs)
        return None
    if len(name.split()) > 4:
        return None
    return find(name), name, bool(m.group("verb1") or m.group("verb2"))


# Spoken sentences that take back what came before ("Streaming protocol. No, wait.", "... Please don't.", "... Or not.").
_RETRACT = re.compile(r"^\W*(?:no|nope|nah|wait|hold(?: on| it)?|hang on|never ?mind|never|cancel|stop|don'?t|do not|please don'?t|"
                      r"actually|forget (?:it|that)|or not|not (?:that|now|yet|it)|um+|uh+|hmm+|erm+)\b", re.I)
_SENTENCES = re.compile(r"(?<=[.!?;])\s+")
# What may follow his order in the same breath without changing it (live 2026-11-20 23:48: "And streaming protocol. That's
# what I was telling you. Understood, sir. Yeah, whatever, man. You're not here for shit. You can shut the fuck up. That's
# what you can do. All right. Let's see."): filler, his title for Jarvis, and words aimed at Jarvis. Anything else after it
# (a question, "Please don't.", "Hmm.", "What do you think of it.") and the order is not run.
_TAIL_WORD = (r"(?:yeah|yes|yep|whatever|man|bro|dude|ok|okay|all right|alright|let'?s see|let'?s go|come on|seriously|please|"
              r"thanks|thank you|now|jarvis|sir|understood|right|god|damn|shit|fuck(?: you| off| this| me)?)")
_TAIL = re.compile(
    rf"^\W*(?:(?:{_TAIL_WORD}|you(?:'re| are) not (?:here|listening|understanding|helping)(?: (?:for shit|at all|to me))?|"
    r"(?:you can )?shut (?:the fuck |the hell )?up|that'?s what (?:you can do|i (?:was|am|'m) (?:telling|saying|asking)(?: you)?)|"
    r"you(?:'re| are) (?:so |fucking |really )?(?:useless|annoying|stupid))[\s,.!]*)+$", re.I)


def _order_among(raw: str, known: bool) -> dict | None:
    """The protocol ordered in ONE sentence of several, None if none. `known`: only his own protocols (else any 'run the X
    protocol' counts, as {'name': X}). The order must be the last sentence, or followed only by filler / words aimed at
    Jarvis (_TAIL); exactly one protocol is ordered; no sentence is a question about a protocol."""
    parts = [p.strip() for p in _SENTENCES.split(raw) if p.strip()]
    if len(parts) < 2:
        return None
    found: list[tuple[int, dict]] = []
    for i, part in enumerate(parts):
        if "protocol" not in part.lower():
            continue
        hit = match_run(part)
        if hit is not None and (hit[0] is not None or (not known and hit[2])):
            found.append((i, hit[0] or {"name": norm(hit[1])}))
        elif part.rstrip().endswith("?") or _QUESTION_LEAD.match(part):
            return None                         # "What does the gaming protocol do? ..." stays a question
    if len({p["name"] for _, p in found}) != 1:
        return None
    at = found[-1][0]
    after = parts[at + 1:]
    if any(_RETRACT.match(p) for p in after) or not all(_TAIL.match(p) for p in after):
        return None
    return found[-1][1]


def run_in_sentences(raw: str) -> dict | None:
    """His protocol order said as ONE sentence of several (live 2026-11-20: "Hey Jarvis. I'm sorry Seth. Streaming Protocol.",
    "And streaming protocol. That's what I was telling you. ..."): the whole-line patterns are anchored, so the order was lost
    to the model. See _order_among for what counts (review 2026-11-21: "Gaming protocol. Why did you do that?" ran)."""
    raw = (raw or "").replace("’", "'").strip()
    # "Streaming. Protocol." (live 2026-11-20 23:46: Whisper cut his "streaming protocol" at a pause): the name is rejoined
    joined = re.sub(r"(?i)\b([a-z0-9']+)[.!,]\s+(protocols?)(?=\W*$)", r"\1 \2", raw)
    if joined != raw:
        last = [p for p in _SENTENCES.split(joined) if p.strip()][-1]
        hit = match_run(last)
        if hit is not None and hit[0] is not None:
            return hit[0]
    return _order_among(raw, known=True)


def sounds_like_order(raw: str) -> bool:
    """These words order a protocol, known to him or not, and do not take it back (core.intelligence: a reply with no tool
    behind it may not simply acknowledge one; "Okay, sir." to "Streaming protocol. No, wait." is fine: review 2026-11-21)."""
    raw = re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", (raw or "").strip(), flags=re.I).replace("’", "'")
    hit = match_run(raw)
    if hit is not None:
        return hit[0] is not None or hit[2]      # his protocol, or "run the X protocol" ("I love the X protocol" is talk)
    return _order_among(raw, known=False) is not None


def _unknown(name: str) -> str:
    have = [p["name"] for p in protocols()]
    return (f"I don't have a {norm(name)} protocol." + (f" You have: {_join(have)}." if have else
                                                        " Say 'create a protocol called ...' to make one."))


def start(proto: dict, raw: str) -> str:
    """Run it here on the PC; from the laptop (the phone's server) the PC runs it and its answer comes back."""
    from core import pcbridge
    places = storm_places(proto)
    if places:
        # Saying it is a question ("what's near my places now?"): a live check with sources, never a replayed warning.
        from core import stormwatch
        return stormwatch.report(places)
    if any(s.get("do") in PC_STEPS for s in proto.get("steps", [])):
        if not _is_owner():
            return "Protocols that control the PC run for the PC's owner only."
        if not pcbridge.local_pc():
            from core import pcfast
            reply = pcfast.ask_pc(raw)
            # ask_pc swallows timeouts: the PC may have run some steps, so "nothing was done" would be a guess (review 2026-11-16)
            return reply or (f"I didn't get an answer from the PC, so I can't tell you whether the {proto['name']} protocol ran. "
                             f"Ask me 'how did the {proto['name']} protocol go' in a minute.")
        _say_early(proto)
    return run_text(proto, "voice")


# The voice brain sets this around its quick answers (brain/llm.py): a PC protocol takes ~20 s (live 2026-11-20 23:46: the
# streaming protocol ran 21 s in silence, he said "Streaming." "Protocol." again, and that cut-in swallowed the result).
EARLY_SAY: contextvars.ContextVar = contextvars.ContextVar("protocols_early_say", default=None)


@contextlib.contextmanager
def early_say(say):
    """While a voice turn's quick answers run: `say(sentence)` speaks at once (None: nothing early, e.g. the phone)."""
    token = EARLY_SAY.set(say)
    try:
        yield
    finally:
        EARLY_SAY.reset(token)


def _say_early(proto: dict) -> None:
    say = EARLY_SAY.get()
    if say is None:
        return
    from core import users
    if (users.current(), proto["name"]) in _running:
        return                     # it answers "already running" at once: nothing to announce
    try:
        say(f"Starting the {proto['name']} protocol, {users.title(users.current(), 'en') or 'sir'}.")
    except Exception as error:  # noqa: BLE001 - an acknowledgment never costs the run
        print(f"[protocols] early line: {type(error).__name__}: {error}")


# -- triggers the scheduler checks (PC voice process) ----------------------------------------------------------------
def _process_age() -> float:
    try:
        import psutil
        return time.time() - psutil.Process().create_time()
    except Exception:  # noqa: BLE001
        return time.monotonic() - _STARTED


_STARTED = time.monotonic()
_retry_back_until = 0.0
STARTUP_S = 300            # Jarvis starting (a deploy, a restart) is not him coming back: the scheduler counts startup as arriving
CAME_BACK_GAP_S = 30 * 60  # one came-back run per protocol per half hour at most


def due(returned: bool, now: datetime | None = None, *, mark: bool = True) -> list[dict]:
    """Approved protocols whose time or came-back trigger fires now. With `mark`, each one is marked fired here, once
    (a time trigger once per day, came-back once per half hour); `unmark` gives it back when it could not run. An
    unapproved protocol is never due, whatever its triggers say. A malformed trigger is skipped, never a crash."""
    now = now or datetime.now()
    state = storage.load(STATE, {})
    fired = state.setdefault("fired", {})
    back = state.setdefault("came_back", {})
    out, changed = [], False
    for proto in protocols():
        if not proto.get("triggers") or not approved(proto):
            continue
        for t in proto["triggers"]:
            if t.get("type") == "came_back" and returned:
                if min(_process_age(), time.monotonic() - _STARTED) < STARTUP_S or now.timestamp() - float(back.get(proto["name"], 0)) < CAME_BACK_GAP_S:
                    continue
                if mark:
                    back[proto["name"]] = now.timestamp()
                    changed = True
                out.append({**proto, "_fired": ["came_back", proto["name"]]})
                break
            if t.get("type") == "time":
                try:
                    h, mi = (int(x) for x in str(t["at"]).split(":"))
                    today = now.replace(hour=h, minute=mi, second=0, microsecond=0)
                except (KeyError, ValueError, TypeError):
                    continue
                key = f"{proto['name']}@{t['at']}"
                # yesterday's time too: 23:55 still fires at 00:03 (its window crosses midnight)
                at = next((a for a in (today, today - timedelta(days=1)) if a <= now < a + timedelta(minutes=TIME_WINDOW_MIN)), None)
                if at is not None and t.get("days") and at.weekday() not in {int(d) for d in t["days"] if str(d).isdigit()}:
                    continue             # a weekday / weekend trigger (core/habits): not today
                if at is not None and fired.get(key) != at.date().isoformat():
                    if mark:
                        fired[key] = at.date().isoformat()
                        changed = True
                    out.append({**proto, "_fired": ["fired", key]})
                    break
    if changed:
        storage.save(STATE, state)
    return out


def unmark(protos: list[dict]) -> None:
    """Give the triggers back (the job thread was busy): the next tick inside the window tries again."""
    state = storage.load(STATE, {})
    for proto in protos:
        kind, key = (proto.get("_fired") or [None, None])[:2]
        if kind and key in state.get(kind, {}):
            state[kind].pop(key, None)
    storage.save(STATE, state)


def _game() -> str:
    try:
        from core import acquire
        return acquire.running_game()
    except Exception:  # noqa: BLE001
        return ""


def _owner() -> str | None:
    from core import users
    return next((u for u, row in users.all_users().items() if row.get("role") == "owner"), None)


def tick(returned: bool, deliver=None, now: datetime | None = None, spawn=None) -> list[dict]:
    """One scheduler pass for the owner (cheap: one file read). What is due runs on `spawn`'s thread when given (the
    scheduler's one-at-a-time job runner), else here; returns the protocols that were due."""
    global _retry_back_until
    from core import users
    uid = _owner()
    if uid is None:
        return []
    if returned:
        _retry_back_until = 0.0
    elif time.monotonic() < _retry_back_until:
        returned = True                 # his return met a busy job thread a moment ago: still his return
    game = _game()
    with users.acting_as(uid):
        protos = due(returned, now, mark=not game)
        if protos and game:
            # Never over a game, and the day is not spent: inside its window it runs once the game ends. Said once a day.
            state = storage.load(STATE, {})
            told = state.setdefault("game_skip", {})
            day = (now or datetime.now()).date().isoformat()
            for proto in protos:
                if told.get(proto["name"]) != day:
                    told[proto["name"]] = day
                    _record({"id": f"{proto['name']}-{time.time():.3f}", "name": proto["name"], "source": "trigger", "uid": uid,
                             "started": datetime.now().isoformat(timespec="seconds"), "steps": [],
                             "stopped": f"you were in a game ({game}), so nothing was done; it runs if the game ends within "
                                        f"{plural(TIME_WINDOW_MIN, 'minute')} of its time"})
            storage.save(STATE, state)
            return []
    if protos:
        protos = _present_for_learned(uid, protos, now)
    if not protos:
        return []

    def job() -> list[dict]:
        return run_due(uid, protos, deliver)
    if spawn is not None:
        if spawn("protocols", job) is False:
            with users.acting_as(uid):
                unmark(protos)
            if any((p.get("_fired") or [""])[0] == "came_back" for p in protos):
                _retry_back_until = time.monotonic() + 120
            return []
        _retry_back_until = 0.0
    else:
        job()
    return protos


def _here() -> bool:
    try:
        from core import attention
        return bool(attention.situation().get("present"))
    except Exception:  # noqa: BLE001 - unknown: treated as away (a learned protocol then waits)
        return False


def _present_for_learned(uid: str, protos: list[dict], now: datetime | None = None) -> list[dict]:
    """A protocol LEARNED from a habit (core/habits) opens his game or app for him: pointless and unwelcome to an empty
    room. While he's away it isn't marked fired, so it runs if he sits down within its window; said once a day."""
    from core import users
    learned = [p for p in protos if p.get("learned")]
    if not learned or _here():
        return protos
    with users.acting_as(uid):
        unmark(learned)
        state = storage.load(STATE, {})
        told = state.setdefault("away_skip", {})
        day = (now or datetime.now()).date().isoformat()
        for proto in learned:
            if told.get(proto["name"]) != day:
                told[proto["name"]] = day
                _record({"id": f"{proto['name']}-{time.time():.3f}", "name": proto["name"], "source": "trigger", "uid": uid,
                         "started": datetime.now().isoformat(timespec="seconds"), "steps": [],
                         "stopped": f"you weren't at the PC, so nothing was done; it runs if you come back within "
                                    f"{plural(TIME_WINDOW_MIN, 'minute')} of its time"})
        storage.save(STATE, state)
    return [p for p in protos if not p.get("learned")]


def run_due(uid: str, protos: list[dict], deliver=None) -> list[dict]:
    """Run each due protocol (never over a game), then say its per-step summary through `deliver` (core.attention by
    default: the one judge of unprompted sentences). Returns the run records."""
    from core import users
    results = []
    with users.acting_as(uid):
        for proto in protos:
            game = _game()               # a game started between the tick and this thread
            if game:
                entry = {"id": f"{proto['name']}-{time.time():.3f}", "name": proto["name"], "source": "trigger", "uid": uid,
                         "started": datetime.now().isoformat(timespec="seconds"), "steps": [],
                         "stopped": f"you were in a game ({game}), so nothing was done"}
                _record(entry)
                results.append(entry)
                continue
            current = find(proto["name"])
            if current is None or current.get("name") != proto["name"] or not approved(current):
                continue                 # deleted, stopped or withdrawn after it was found due (review 2026-11-16)
            proto = current
            entry = run(proto, "trigger")
            if entry.get("busy"):
                continue
            results.append(entry)
            text = summary(entry) + "".join(" " + s for s in entry.get("spoken") or [])
            if proto.get("learned"):
                try:     # told once with how to undo it; afterwards only a failure is said
                    from core import habits
                    text = habits.after_run(proto, entry)
                except Exception as error:  # noqa: BLE001 - a broken habits file never stops the other due protocols
                    print(f"[protocols] habits: {type(error).__name__}: {error}")
                if not text:
                    continue
            try:
                if deliver is None:
                    from core import attention
                    attention.deliver(text, attention.NORMAL, key=f"protocol:{entry['id']}")
                else:
                    deliver(text)
            except Exception as error:  # noqa: BLE001
                print(f"[protocols] deliver: {type(error).__name__}: {error}")
    return results


# -- outside-data triggers (Storm Watch), both machines: the PC speaks, the laptop pings the phone ---------------------
_last_outside = 0.0
_storm_tries: dict = {}
STORM_TRIES = 3


def storm_places(proto: dict) -> list[str]:
    return [p for t in proto.get("triggers", []) if t.get("type") == "storm" for p in t.get("places", []) if isinstance(p, str)]


def outside_tick(spawn=None, get=None, now=None, force: bool = False) -> list[dict]:
    """Every 5 minutes: the owner's APPROVED protocols with a storm trigger check the public feeds and run once per new
    (or escalated) warning. JARVIS_STORMWATCH=off switches it off. Unlike time triggers, a game does not hold it back:
    the warn step leaves that to core.attention (a DANGER is said over a game, an ALERT waits until it ends)."""
    global _last_outside
    import os
    if os.getenv("JARVIS_STORMWATCH", "off").lower() == "off":
        return []
    if not force and time.monotonic() - _last_outside < _stormwatch().POLL_S and _last_outside:
        return []
    from core import users
    uid = _owner()
    if uid is None:
        return []
    with users.acting_as(uid):
        protos = [p for p in protocols() if storm_places(p) and approved(p)]
    if not protos:
        return []
    _last_outside = time.monotonic()

    def job() -> list[dict]:
        return storm_run(uid, protos, get=get, now=now)
    if spawn is not None:
        if spawn("stormwatch", job) is False:
            _last_outside = 0.0                  # the previous check is still running: try again next tick
        return []
    return job()


def _stormwatch():
    from core import stormwatch
    return stormwatch


def storm_run(uid: str, protos: list[dict], get=None, now=None) -> list[dict]:
    """One check of the feeds, then one run of each matching protocol per warning (the run record says which steps
    happened; only the PC keeps records). A warning is marked given only after its steps ran."""
    from core import pcbridge, users
    sw = _stormwatch()
    results = []
    with users.acting_as(uid):
        places = list(dict.fromkeys(p for proto in protos for p in storm_places(proto)))
        data = sw.check(places, get)
        events, notices = sw.pending(places, data, now)
        for ev in events:
            reached = False
            for proto in protos:
                if ev["place"] not in storm_places(proto):
                    continue
                entry = run(proto, "storm", alert=ev, record=pcbridge.local_pc())
                if not entry.get("busy"):
                    results.append(entry)
                    reached = reached or any(r["status"] == DONE for r in entry.get("steps", []))
            # Marked given only when a step reached him: a warning whose every step failed (or met a busy run) is tried
            # again next check, at most STORM_TRIES times (review 2026-11-16), so a broken voice never repeats forever.
            slot = (ev["key"], ev["level"])          # an escalation to DANGER gets its own tries
            tries = _storm_tries.get(slot, 0) + 1
            if reached or tries >= STORM_TRIES:
                _storm_tries.pop(slot, None)
                sw.mark(ev, now)
            else:
                _storm_tries[slot] = tries
        waiting = {(e["key"], e["level"]) for e in events}
        for slot in [k for k in _storm_tries if k not in waiting]:
            _storm_tries.pop(slot, None)
        if notices and pcbridge.local_pc():
            try:
                from core import attention
                for text in notices:
                    attention.deliver(text, attention.NORMAL, key=f"stormwatch-source:{text[:60]}")
            except Exception as error:  # noqa: BLE001
                print(f"[stormwatch] notice: {type(error).__name__}: {error}")
    return results
