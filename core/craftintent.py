"""Deterministic front door for character-craft requests (rig / find / generate / status / cancel).

The local 3B model is unreliable at choosing tools and filling arguments, so a clear craft request is decided HERE,
in code, and carried out directly; the model never sees the decision. Anything unclear returns None and falls
through to the normal conversation, so nothing the model can already do is taken away."""
import re
import time

_CHANNEL = re.compile(r"^\s*\[[^\]]*\]\s*")
_LEAD = re.compile(r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:jarvis[,:\s]+)?(?:please\s+|can you\s+|could you\s+|i (?:want|need) you to\s+|i'd like you to\s+)*", re.I)
_VERB = re.compile(r"\b(?:rig(?:ged|ging)?|auto-?rig|find|locate|look for|get|make|turn|set up|prepare)\b", re.I)
_PATH = re.compile(r'(?:"([^"]+\.(?:glb|gltf|blend|png|jpe?g|webp))"|([A-Za-z]:[\\/][^\s"]*?\.(?:glb|gltf|blend|png|jpe?g|webp)))', re.I)
_TRAIL = re.compile(r"\s*(?:,?\s*please|for me|now|thanks?|thank you|and (?:tell|let) me.*)?[.!?\s]*$", re.I)
_CREATURE = re.compile(r"\b(?:dragon|wyvern|drake|spider|scorpion|insect|crab|snake|serpent|worm|bird|monster|alien|creature|tentacle|octopus|hydra|griffin|gryphon|hexapod|wings?|winged)\b", re.I)
_QUAD = re.compile(r"\b(?:quadruped|four[- ]legged|dog|wolf|horse|cat|lion|bear|animal|beast)\b", re.I)

# Matches the signals the model-side selector already used (kept in step with core.intelligence).
_ABILITY = re.compile(r"\b(?:rigging|rigged|rig)\b.*\b(?:skills?|abilit(?:y|ies)|good at|able to)\b|\bcan you rig\b|"
                      r"\b(?:your|you) (?:\w+ )?(?:rigging|blender) (?:skills?|abilit(?:y|ies))\b|\bgood\b.*\bat (?:rigging|rig|blender)\b", re.I)
_LESSONS = re.compile(r"\b(?:learn(?:ed|t)?|lessons?|mistakes?)\b.*\b(?:rigging|rig)\b|\b(?:rigging|rig)\b.*\b(?:learn(?:ed|t)?|lessons?|mistakes?)\b", re.I)
_RIG_JOB = re.compile(r"\brig(?:ged|ging)?\b\s+(?:my|the|this|that|it|him|her|them|an?|[\w'-]+\s+character)\b|"
                      r"\brig\b.*\b(?:i have open|open in blender|already open|in (?:my )?blender)\b|\bgame[- ]ready\b.*\b(?:character|model)\b|"
                      r"\bmake\b.*\b(?:rigged|game[- ]ready)\b|\b(?:image|picture|photo)\b.*\b(?:rigged|rig)\b", re.I)
_FIND = re.compile(r"\b(?:find|locate|look for|where(?:'s| is))\b.*\b(?:my|the|a)\b.*\b(?:character|model)\b|"
                   r"\bwhat (?:am i|do i have) (?:working on|open)\b", re.I)
_CAN_YOU = re.compile(r"\b(?:can|could|are you able to|do you know how to) you (?:\w+ )?rig an?\b", re.I)
_SPECIFIC = re.compile(r"\b(?:my|the|this|that|it|these|those|open|image|picture|photo)\b", re.I)
_CLEAN = re.compile(r"\b(?:clean ?up|tidy|confirm cleanup)\b.*\b(?:rig(?:ged)?|character|model)s?\b.*\b(?:outputs?|files|runs|folders?)\b|\bconfirm (?:the )?clean ?up\b", re.I)
_CONFIRM = re.compile(r"\bconfirm\b", re.I)
_MAKE = re.compile(r"\b(?:make|create|design|generate|build|invent|dream up)\b.*\b(?:3d|three[- ]d|model|character|creature|monster|rigged|game[- ]ready)\b", re.I)
_MAKE_TAIL = re.compile(r"^.*?\b(?:make|create|design|generate|build|invent|dream up)\b(?: me)?(?: an?| the)?(?: new)?(?: 3d| three[- ]d)?(?: rigged| game[- ]ready)?(?: model| character)?(?: of| that is| that looks like| who is)?\s*", re.I)
_OPEN_VERB = re.compile(r"\b(?:open|show|pull up|bring up|load)\b\s+(?P<obj>.+?)(?:[.?!,]|\s+(?:for me|please|now)\b|$)", re.I)
_OPEN_PRONOUN = re.compile(r"^(?:it|that|this|them|the (?:rigged|new|finished|last|merged)\b.*|(?:the |my )?(?:character|model|result)s?)$", re.I)
_OPEN_NOT_BLENDER = re.compile(r"\b(?:browser|website|web ?page|folder|explorer|chrome|youtube|spotify|app|settings|email|calendar|notes?|document)\b", re.I)
_HOWTO = re.compile(r"\b(?:how (?:do|can|would|to)|tutorials?|guide|explain|what is)\b", re.I)
_STATUS = re.compile(r"\b(?:how(?:'s| is)|is|are)\b.*\b(?:rig|rigging|character|model|job|it|that|generation)\b.*\b(?:going|done|ready|finished|coming along)\b|"
                     r"\b(?:rig|rigging|character|job)\b.*\b(?:status|progress)\b|\bare you (?:done|finished)\b|\bwhat happened with (?:the|my) (?:rig|character|model)\b", re.I)
_CANCEL = re.compile(r"\b(?:cancel|stop|abort|kill|never ?mind)\b.*\b(?:rig|rigging|that|it|job|character|model|generation)\b|^\s*(?:cancel|abort)\b", re.I)


def _verified_bind(text):
    try:
        from tools import blenderskills
        return blenderskills.wants(text)
    except Exception:
        return False


def _installed_skill(text):
    try:
        from core import skillhost
        return skillhost.wants(text)
    except Exception:
        return False


def _choice(text):
    """'number one' right after JARVIS offered a numbered list -> that tool with the chosen file."""
    try:
        from tools import charjobs
        p = charjobs.pending()
        if not p or charjobs.ordinal(text) is None:
            return None
        chosen = charjobs.pick(text)
        if not chosen:
            return None
        return p["tool"], {**({"kind": "", "profile": "ue5", "use_saved_version": False} if p["tool"] == "rig_my_character" else {}),
                           **p["args"], "which": chosen["path"]}
    except Exception:
        return None


_WHERE = re.compile(r"\bwhere(?:'s| is| are| did you (?:put|save|store|leave)| have you (?:put|saved|stored))\b.*\b(?:rig(?:ged)?|character|model|it|that|result|file|folder)s?\b|"
                    r"\bwhere\b.*\b(?:put|puts|save[ds]?|store[ds]?|keeps?|kept)\b.*\b(?:it|that|rig(?:ged)?|character|model)\b", re.I)
_FOLDER = re.compile(r"\b(?:show|open|take me to)\b.*\b(?:the )?(?:folder|directory)\b", re.I)


def _where_request(text):
    """'where is it' / 'where did you put the rigged character' / 'show me the folder' -> where_is_rigged_character, when something was rigged lately."""
    try:
        from tools import charjobs
        if not (charjobs.last_delivery() or recent_job()):
            return None
    except Exception:
        return None
    if _FOLDER.search(text) and not _HOWTO.search(text):
        return "where_is_rigged_character", {"open_folder": True}
    if _WHERE.search(text) and not _HOWTO.search(text):
        return "where_is_rigged_character", {"open_folder": False}
    return None


# A part of Blender's window, not a file ("open the Meshy panel in the sidebar": live 2026-11-14 it went to open_in_blender, which
# looked for a character file of that name): the in-app steps belong to the PC layer (core.pcsteps), never to this door.
_UI_PART = re.compile(r"\b(?:panel|sidebar|side\s*bar|tab|menu|toolbar|tool\s*bar|button|dialog|window|section|options?|settings|"
                      r"properties|editor|add-?on|plug-?in|header|outliner|inspector|viewport|pane|ribbon|shelf)\b", re.I)


def _open_request(text):
    """'open it' / 'open the rigged vanguard in Blender' -> open_in_blender, only when it is clear what is meant."""
    m = _OPEN_VERB.search(text)
    if not m or _OPEN_NOT_BLENDER.search(text) or _HOWTO.search(text):
        return None
    if _UI_PART.search(m.group("obj")):
        return None
    obj = re.sub(r"\b(?:in|with|on|into)\s+(?:my\s+|the\s+)?blender\b", "", m.group("obj"), flags=re.I).strip(" ,")
    obj = re.sub(r"^(?:up|me)\s+", "", obj, flags=re.I)
    mentions_blender = bool(re.search(r"\bblender\b", text, re.I))
    j = recent_job()
    try:
        from tools.charjobs import _words, last_delivery
    except Exception:
        return None
    rec = last_delivery(6 * 3600)
    finished = bool((j and j["state"] == "done") or rec)
    label = (j["label"] if j and j["state"] == "done" else (rec or {}).get("name", ""))
    if _OPEN_PRONOUN.match(obj) or (finished and re.search(r"\bjust\b", text, re.I)):
        return ("open_in_blender", {"which": ""}) if finished else None
    if finished and _words(obj) and _words(obj) <= _words(label):         # only words of the job's own name: "the vanguard"; extra words mean another file
        return "open_in_blender", {"which": ""}
    if mentions_blender and _words(obj):
        return "open_in_blender", {"which": obj}
    return None


def clean(text):
    return _CHANNEL.sub("", text or "").strip()


def which_of(text):
    """What the owner pointed at: a path as given, else the words after the verb (the resolver ignores filler words)."""
    t = _LEAD.sub("", clean(text))
    m = _PATH.search(t)
    if m:
        return m.group(1) or m.group(2)
    v = _VERB.search(t)
    rest = t[v.end():] if v else t
    return _TRAIL.sub("", rest).strip(" ,.")


def recent_job():
    from core import craftjobs
    j = craftjobs.get()
    if j and (j["state"] in ("running", "queued") or time.time() - (j["finished"] or 0) < 1800):
        return j
    return None


_DEFAULTS = {
    "bind_two_bone_limb": {"request": ""},
    "open_in_blender": {"which": ""},
    "where_is_rigged_character": {"open_folder": False},
    "run_installed_skill": {"request": ""},
    "rig_my_character": {"which": "", "kind": "", "profile": "ue5", "use_saved_version": False},
    "find_character": {"query": ""},
    "make_character_from_description": {"description": "", "kind": "", "profile": "ue5"},
    "tidy_character_outputs": {"confirm": False},
    "rigging_lessons": {"limit": 12},
    "start_practice": {"minutes": 60},
}


_PRACTICE = re.compile(r"\b(?:practi[cs]e|train yourself|get better at|improve yourself)\b", re.I)
_PRACTICE_STOP = re.compile(r"\b(?:stop|cancel|end|quit)\b.*\b(?:practi[cs]\w*|training)\b", re.I)
_PRACTICE_STATUS = re.compile(r"\b(?:how|what)\b.*\b(?:practi[cs]\w*|training)\b.*\b(?:going|doing|status|progress|so far)\b|\bpracti[cs]e (?:status|progress)\b", re.I)
_LEARNED = re.compile(r"\b(?:what|how much)\b.*\b(?:learn(?:ed|t)?|improved)\b.*\b(?:practi[cs]\w*|overnight|experiments?|sandbox|dojo|lately|so far)\b|\bare you a master\b|\b(?:how good|how skilled)\b.*\b(?:rigging|blender)\b", re.I)
_REVIEW = re.compile(r"\bstrateg\w+\b.*\b(?:waiting|pending|review)\b|\b(?:review|pending)\b.*\b(?:learned|strateg\w+)\b", re.I)
_DECIDE = re.compile(r"\b(approve|reject)\b\s+(?:the\s+|that\s+)?(?:learned\s+)?strateg\w*\s+([0-9a-f]{6,8})\b", re.I)
_MINUTES = re.compile(r"(\d+)\s*(?:min|minutes?|hours?|hrs?)", re.I)


def match(text):
    """(tool_name, arguments) for a clear craft request, else None. Arguments are complete: the tool registry
    requires every declared argument, and a 3B model is no longer there to fill them."""
    hit = _match(text)
    if hit is None:
        return None
    name, args = hit
    return name, {**_DEFAULTS.get(name, {}), **args}


def _match(text):
    t0 = clean(text)
    d = _DECIDE.search(t0)
    if d:
        return ("approve_learned_strategy" if d.group(1).lower() == "approve" else "reject_learned_strategy"), {"strategy_id": d.group(2).lower()}
    if _REVIEW.search(t0) and not _PRACTICE.search(t0):
        return "review_learned_strategies", {}
    if _PRACTICE_STOP.search(t0):
        return "stop_practice", {}
    if _PRACTICE_STATUS.search(t0):
        return "practice_status", {}
    if _LEARNED.search(t0):
        return "what_have_you_learned", {}
    if _PRACTICE.search(t0) and re.search(r"\b(?:rig\w*|blender|3d|character|skills?)\b|^\s*(?:please )?(?:jarvis[, ]+)?practi[cs]e\b", t0, re.I):
        m = _MINUTES.search(t0)
        mins = int(m.group(1)) * (60 if re.search(r"hour|hr", m.group(0), re.I) else 1) if m else 60
        return "start_practice", {"minutes": mins}
    t = clean(text)
    if not t or len(t) > 400:
        return None
    j = recent_job()
    if j and j["state"] in ("running", "queued") and _CANCEL.search(t):
        return "cancel_character_job", {}
    if _CLEAN.search(t):
        return "tidy_character_outputs", {"confirm": bool(_CONFIRM.search(t))}
    if j and _STATUS.search(t) and not _RIG_JOB.search(t):
        return "character_job_status", {}
    c = _choice(t)
    if c:
        return c
    o = _open_request(t) if not _RIG_JOB.search(t) else None
    if o:
        return o
    w = _where_request(t) if not _RIG_JOB.search(t) else None
    if w:
        return w
    if (_CAN_YOU.search(t) and not _SPECIFIC.search(t)) or (_ABILITY.search(t) and not _RIG_JOB.search(t)):
        return "rigging_skills", {}
    if _LESSONS.search(t) and not _RIG_JOB.search(t):
        return "rigging_lessons", {}
    if _verified_bind(t):                       # a stored, judge-verified procedure covers this exact job: it comes before the general rig front door
        return "bind_two_bone_limb", {"request": t}
    if (_RIG_JOB.search(t) or (_PATH.search(t) and re.search(chr(92)+"brig"+chr(92)+"b", t, re.I))) and not _HOWTO.search(t):
        args = {"which": which_of(t)}
        if _CREATURE.search(t):
            args["kind"] = "creature"
        elif _QUAD.search(t):
            args["kind"] = "quadruped"
        return "rig_my_character", args
    if _MAKE.search(t) and not _HOWTO.search(t) and not _FIND.search(t):
        d = _MAKE_TAIL.sub("", t)
        return "make_character_from_description", {"description": d}
    if _FIND.search(t):
        return "find_character", {"query": which_of(t)}
    if _installed_skill(t):                     # last: a skill the owner installed claims only what no built-in front door already took
        return "run_installed_skill", {"request": t}
    return None
