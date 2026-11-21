"""Chat first: is this turn conversation, and how much of an answer does it deserve?

Until 2026-11-12 every line that was not an exact command went to the model behind a 10,000-token operations manual, at
temperature 0.2, told to answer in one to three sentences, and usually after a JSON classifier call that only decided it
was talk (docs/mission/audit-brain-pipeline.md). A conversational turn now takes the chat path instead
(core.intelligence.request): the short persona (core/persona-core.md), no tools, a conversation sampling profile, more
of the conversation in view and a reply budget that fits the question.

The decision uses only what the pipeline already works out: no skill is pointed at by his words
(intelligence.select_tools returned none, or only its loose lexical recall: none of them is named by a word of his and
the line is plain talk, see pointed_at), no tool ran in this turn, it is the first local tier, and the line is not a
question about HIS things that must be looked up first (intelligence.about_them) nor an order no skill matched. Anything
else, and anything unsure, keeps the manual and the tools exactly as before: no command is lost. The chat path offers no
tool and accepts no call, so it can never run a PC action.

JARVIS_CHAT_FIRST=off restores the old path for every turn. JARVIS_CHAT_THINK=on lets a depth turn reason first.
"""
from __future__ import annotations

import os
import re

from core import daypart as _daypart
from core.words import plural

# The budgets and sampling each kind of chat turn is sent with are core.intelligence.PROFILES (the PC applies them, also for
# the phone's turns relayed from the laptop); these are only the names.
CHAT, DEPTH, THINK = "chat", "depth", "think"


def enabled() -> bool:
    return os.getenv("JARVIS_CHAT_FIRST", "on").strip().lower() not in ("off", "0", "false", "no")


def thinking() -> bool:
    """Reason before a depth answer? Off by default. Measured 2026-11-12 with scripts/chat_eval.py on the four depth
    conversations (runs/chat-eval-think.md against runs/chat-eval-nothink.md): first words after a median 4.05 s with
    thinking against 0.74 s without, for answers of the same length and shape (149 against 137 words, 9 of 10 with four or
    more sentences either way). Too slow for speech for no audible gain."""
    return os.getenv("JARVIS_CHAT_THINK", "off").strip().lower() in ("on", "1", "true", "yes")


# A question that deserves a real spoken answer: why, how something works, explain, compare, what he should do, what
# Jarvis thinks, a plan. Not "how are you", and not a how-much / how-many fact.
_DEPTH = re.compile(
    r"\bwhy\b(?!\s+not\b)|\bhow\s+come\b|"
    r"\bhow\s+(?:do|does|did|can|could|would|should|is\s+it\s+that|exactly|to)\b|\bhow\b[^.?!]{0,40}\bwork(?:s|ed|ing)?\b|"
    r"\bexplain\b|\bwalk\s+me\s+through\b|\bbreak\s+(?:it|that|this)\s+down\b|\btell\s+me\s+(?:more\s+)?about\b|\bteach\s+me\b|"
    r"\bwhat(?:'s|\s+is|\s+are)\s+the\s+(?:difference|point|best\s+way|idea\s+behind|catch|deal\s+with)\b|\bdifference\s+between\b|"
    r"\bcompare[ds]?\b|\bversus\b|\bvs\.?\b|\bpros\s+and\s+cons\b|\bis\s+(?:that|it|this)\s+the\s+same\b|"
    r"\b(?:which|what)\b[^.?!]{0,40}\b(?:better|best|pick|choose|go\s+with|recommend)\b|\bbetter\b[^.?!]{0,40}\bor\b|"
    r"\b(?:any|some|your|need|want)\s+(?:advice|tips|suggestions|thoughts|ideas)\b|"
    r"\bwhat\s+(?:should|would|could)\s+(?:i|we|you)\b|\bshould\s+(?:i|we)\b|\bwhat\s+do\s+you\s+(?:think|make\s+of|reckon)\b|"
    r"\bhow\s+would\s+you\b|\bwhere\s+(?:should|do)\s+(?:i|we)\s+start\b|\bwhat\s+does\b[^.?!]{1,40}\b(?:mean|do)\b|"
    r"\bwhat\s+(?:happens|would\s+happen)\b|\bin\s+detail\b|\bgo\s+deeper\b|"
    r"^\W*(?:(?:so|and|but|okay|ok)[,\s]+)?what(?:'s|\s+is|\s+are)\s+(?:an?\s+)?[\w-]+(?:\s+[\w-]+)?\s*\?*\s*$",
    re.I)
_SHALLOW = re.compile(
    r"\bhow\s+(?:are|r)\s+(?:you|u|ya)\b|\bhow(?:'s|\s+is)\s+(?:it|everything|life|your)\b|\bhow\s+(?:you|u)\s+doing\b|"
    r"\bhow\s+have\s+you\s+been\b|\bhow\s+(?:much|many|long|old|far|big|tall|often|late|early|soon)\b|\bhow\s+about\b|"
    r"\bwhat(?:'s|\s+is)\s+(?:up|good|new|that|this|it|going\s+on|the\s+(?:time|date|matter))\b|\bwhat\s+(?:was|is)\s+that\b|"
    r"\bwhy\s+not\b|\bwhat\s+(?:time|day|date|year)\b", re.I)


def depth(said: str) -> bool:
    """Does his line ask for a real answer (four to eight spoken sentences), not a remark or a quick fact?"""
    text = (said or "").replace("’", "'").strip()
    if not text or len(text.split()) < 2:
        return False
    if _SHALLOW.search(text) and not re.search(r"\bwhy\b(?!\s+not\b)|\bexplain\b|\bhow\s+(?:does|do)\b[^.?!]{0,40}\bwork", text, re.I):
        return False
    return bool(_DEPTH.search(text))


# Verbs that ask him to TALK ("tell me a joke", "explain that again", "give me your honest opinion", "help me decide"):
# an order of words, not of work. Every other command verb with no skill behind it keeps today's path.
_TALK_VERBS = {"tell", "explain", "describe", "give", "help", "answer", "repeat", "continue", "mention", "wait", "hold",
               "try", "keep", "say", "talk", "guess", "imagine", "listen", "look", "see", "let", "be", "think", "pick", "choose"}
_FILLER = re.compile(r"^(?:(?:hey|hi|yo|ok|okay|so|and|but|also|now|then|please|just|um+|uh+|well|yeah|yes|no|nah|oh|right|"
                     r"alright|sir|jarvis)\b[,\s]*)+", re.I)
_CLAUSES = re.compile(r"[,.;:!?]+|\s+-+\s+|\b(?:and\s+then|then|and)\b", re.I)


def orders(said: str) -> bool:
    """Does a clause of his line order WORK (open, close, set, send, install...)? Judged by core.gateway's own command
    words, so the two never disagree about what a command looks like."""
    try:
        from core import gateway
        command = gateway._COMMAND_CLAUSE
    except Exception:  # noqa: BLE001  (unsure: an order, so today's path decides)
        return True
    text = (said or "").replace("’", "'")
    for clause in _CLAUSES.split(text):
        body = _FILLER.sub("", clause.strip())
        hit = command.match(body)
        if hit:
            verb = body[:hit.end()].split()[-1].casefold()
            if verb not in _TALK_VERBS and not (verb == "do" and re.match(r"do\s+(?:you|i|we|they)\b", body, re.I)):
                return True
    return False


# "Can you ...", "please ...", "I need you to ...": a request to act, unless it only asks to be told something.
_ASKS_TO_ACT = re.compile(r"(?<!what )(?<!how )(?<!who )(?<!where )(?<!which )(?<!why )(?<!when )\b(?:can|could|would|will)\s+you\b|\bplease\b|\bi\s+(?:want|need)\s+you\s+to\b|\bgo\s+ahead\b|"
                          r"\bdo\s+it\b|\bmake\s+sure\b|\bany\s+chance\b|\bare\s+you\s+able\s+to\b|\bwould\s+you\s+mind\b", re.I)
_ASKS_TO_TELL = re.compile(r"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:just\s+)?(?:tell|explain|describe|give|help\s+me\s+"
                           r"(?:understand|decide|think)|talk|say|walk\s+me\s+through|break|remind\s+me\s+(?:what|why|how|who))\b|"
                           r"\bplease\s+(?:tell|explain|describe|say|talk|go\s+on|continue)\b", re.I)


def asks_to_act(said: str) -> bool:
    text = (said or "").replace("’", "'")
    return bool(_ASKS_TO_ACT.search(text)) and not _ASKS_TO_TELL.search(text)


def _pictured(history) -> bool:
    newest = history[-1].get("content") if history else None
    return isinstance(newest, list) and any(
        (b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) in ("image", "image_url") for b in newest)


# Words of a skill's name that say nothing about which skill it is.
_NAME_NOISE = {"get", "set", "list", "add", "my", "the", "for", "and", "with", "from", "into", "status", "mode", "this",
               "that", "your", "what", "have", "are", "all", "new", "one", "now", "you", "can", "not", "any", "out",
               # everyday verbs: as an ORDER they open a clause, and then the line is not plain talk (orders)
               "find", "keep", "make", "take", "start", "stop", "check", "look", "show", "tell", "give", "open", "close",
               "run", "play", "see", "use", "help", "read", "write", "send", "turn", "switch", "pick", "move", "work",
               "change", "create", "build", "learn", "watch", "save", "wait", "try", "want", "need", "like", "know",
               # when, not what (2026-11-12: "the LAST assistant I had" pointed at undo_last_expense, "nothing is working
               # TODAY" at four *_today skills, and both lines got the manual): the skill's own word still points at it
               "last", "today", "tonight", "yesterday", "day", "days", "week", "time", "thing", "things", "first",
               "person", "people", "normal"}


def pointed_at(said: str, schemas) -> bool:
    """Do his words point at one of the skills selected for this turn? select_tools is lexical recall (measured
    2026-11-12 with scripts/chat_eval.py: it returned skills for 37 of 48 purely conversational lines, "Long day." among
    them), so a selected skill only counts when the line is not plain talk (the machine, media, an errand, an order, a
    request to act: plain_talk) or when a word of his IS a word of that skill's name ("weather", "timer", "recipe")."""
    if not schemas:
        return False
    if not plain_talk(said):
        return True
    words = set()
    for w in re.findall(r"[a-z0-9]+", (said or "").casefold()):
        if len(w) >= 3 and w not in _NAME_NOISE:
            words |= {w, w[:-1] if w.endswith("s") and len(w) > 3 else w}
    for schema in schemas:
        name = (schema.get("function") or schema).get("name", "") if isinstance(schema, dict) else ""
        parts = {p for p in re.findall(r"[a-z0-9]+", name.casefold()) if p not in _NAME_NOISE}
        # EVERY distinctive word of the skill's name must be one of his: one common word of a longer name is no sign he
        # means the skill ("end" -> end_workout for "it took all weekend, grey in the end"; "next" -> a study skill; "two"
        # -> a two-bone-limb skill; "learning" -> jarvis_learning_status: replay 2026-11-13).
        if parts and all(p in words or (p.endswith("s") and len(p) > 3 and p[:-1] in words) for p in parts):
            return True
    return False


# ---------------------------------------------------------------- his answer to Jarvis, and clipped commands
# Review 2026-11-12: "Sure." after "Shall I set a reminder?" and "Quieter" after "Playing jazz now, sir." read like talk
# but are his answer and a command. They keep today's path (the manual and the tools), never the tool-less chat path.
_YES_NO = re.compile(
    r"^\W*(?:(?:yes|yeah|yep|yup|ya|sure|ok|okay|k|alright|all\s+right|right|fine|absolutely|definitely|certainly|"
    r"of\s+course|please|go\s+(?:ahead|for\s+it|on)|do\s+(?:it|that|so)|sounds\s+(?:good|great|perfect|fine)|"
    r"let'?s\s+do\s+(?:it|that)|why\s+not|by\s+all\s+means|(?:that|it)(?:\s+would|\s+will|'?d)\s+be\s+\w+|"
    r"(?:great|good|perfect)\s+idea|make\s+it\s+so|i'?d\s+like\s+that|that\s+works|"
    r"no|nope|nah|not\s+now|not\s+yet|never\s+mind|don'?t|leave\s+it|cancel)\b)", re.I)
_OFFER = re.compile(r"\?|\b(?:i\s+can|i\s+could|shall\s+i|should\s+i|want\s+me\s+to|would\s+you\s+like|do\s+you\s+want|"
                    r"if\s+you(?:'d|\s+would)?\s+like|say\s+the\s+word|just\s+say|let\s+me\s+know\s+if)\b", re.I)
# What Jarvis says when something was just done or is running: the next clipped line is about THAT.
_ACTED = re.compile(r"\b(?:playing|paused|pausing|opening|opened|closing|closed|starting|started|stopping|stopped|setting|"
                    r"turning|turned|switching|switched|calling|sending|sent|muted|unmuted|launching|launched|skipping|"
                    r"skipped|dimming|dimmed|resuming|resumed|done|is\s+(?:now\s+)?(?:set|on|off|open|playing|running))\b", re.I)
_CLIPPED = re.compile(
    r"^\W*(?:(?:a\s+(?:bit|little)\s+)?(?:quieter|louder|brighter|dimmer|darker|lighter|faster|slower|higher|lower|"
    r"bigger|smaller|warmer|cooler)|(?:be\s+)?quiet|silence|shush|shut\s+up|"
    r"(?:the\s+)?(?:next|previous|last|other)(?:\s+(?:one|song|track|tab|page|window))?|"
    r"(?:dim|mute|unmute|skip|pause|resume|stop|play|close|open|undo|redo|cancel|repeat)(?:\s+(?:it|that|this|them))?|"
    r"(?:the\s+)?lights?\s+(?:on|off|up|down)|(?:turn\s+)?(?:it|that)\s+(?:up|down|off|on)|(?:volume\s+)?(?:up|down)|"
    r"hang\s+up|go\s+back|back|forward|again|once\s+more|one\s+more\s+time|wait|hold\s+on|hang\s+on|"
    r"not\s+(?:that|this)(?:\s+one)?|the\s+other\s+one|more|less|off|on)\W*(?:please|sir|jarvis)?\W*$", re.I)


_FEELING = (r"tired|fine|good|okay|ok|well|great|exhausted|beat|wiped|bored|glad|happy|sorry|kidding|joking|serious|sure|"
            r"alright|all\s+right")
_VERDICT = (r"fair(?:\s+enough)?|true|right|fine|good|great|nice|funny|interesting|odd|weird|strange|sad|rough|tough|okay|"
            r"ok|all\s+right|alright|cool|lovely|kind(?:\s+of\s+you)?|sweet|unfortunate|understandable|reassuring|helpful|"
            r"simpler|simple|easier|easy|clearer|clear|"
            r"wild|crazy|a\s+shame|a\s+pity|a\s+relief|something|life|good\s+to\s+(?:hear|know)")
_REMARK = re.compile(
    r"^\W*(?:(?:oh|ah|well|hm+|huh|yeah|yes|no|nope|nah|ok|okay|so|and|but|ha|heh|honestly|ugh|man)[,\s]+)*(?:"
    r"(?:good|glad|nice|great)\s+to\s+(?:hear|know)(?:\s+(?:it|that))?|"
    r"good|great|nice|lovely|brilliant|excellent|wonderful|perfect|cool|sweet|neat|awesome|fair\s+enough|"
    r"pretty\s+(?:good|great|well|bad|tired|rough|busy|nice|decent|slow)|"
    r"not\s+(?:really|exactly|quite|much|bad|sure|especially|particularly|at\s+all|a\s+lot)|"
    r"i\s+(?:see|know|guess(?:\s+so)?|suppose(?:\s+so)?|think\s+so|hope\s+so|doubt\s+it|agree|disagree|get\s+it|"
    r"understand|bet|wish)|"
    r"(?:i'm|i\s+am|i\s+was)\s+(?:just\s+|so\s+|really\s+|pretty\s+|a\s+bit\s+)?(?:" + _FEELING + r")|"
    r"(?:that|it|this)(?:'s|\s+is|\s+was)\s+(?:so\s+|very\s+|really\s+|pretty\s+|quite\s+|just\s+|a\s+lot\s+|much\s+)?(?:"
    + _VERDICT + r")(?:\s+now|\s+than\s+(?:i|we)\s+(?:thought|expected|imagined|figured|had\s+thought)(?:\s+it\s+(?:would|was)(?:\s+be)?)?)?|"
    r"sounds\s+(?:good|great|fine|right|about\s+right|rough|tough|nice|lovely|fun|familiar)|makes\s+sense|"
    r"(?:quiet|long|rough|tough|hard|busy|slow|weird|strange|odd|funny|crazy|wild|lazy|late|early|good|bad|nice)\s+"
    r"(?:day|night|evening|morning|week|one|month|year|afternoon|weekend)|"
    r"what\s+a\s+(?:day|week|night|mess|shame|relief|world|morning|year)|same(?:\s+here)?|me\s+(?:too|neither)|true|"
    r"exactly|indeed|maybe|perhaps|probably|possibly|point\s+taken|typical|figures|go\s+figure|"
    r"no\s+(?:idea|kidding|way|worries|problem|doubt|rush|wonder)|"
    # Venting and declines (live 2026-11-12): short, and the lexical skill pick for them ("movies_out", "garmin_sync",
    # "today_workout") sent them down the manual's path, which answered with a task prompt or a made-up action.
    r"(?:i'm|i\s+am|i\s+was|i\s+feel|i\s+feel\s+so)\s+(?:so\s+|really\s+|just\s+|pretty\s+|totally\s+|completely\s+|a\s+bit\s+)?"
    r"(?:wiped(?:\s+out)?|worn\s+out|stressed(?:\s+out)?|drained|burn(?:t|ed)\s+out|done|over\s+(?:it|today|this|everything)|"
    r"fed\s+up|overwhelmed|anxious|worried|sad|upset|annoyed|frustrated|spent|shattered|knackered|cooked)(?:\s+(?:today|tonight))?|"
    r"(?:i\s+)?(?:didn'?t|did\s+not|couldn'?t)\s+sleep(?:\s+(?:well|much|at\s+all))?|"
    r"too\s+much\s+(?:going\s+on|to\s+do)|everything\s+(?:just\s+)?piles\s+up|"
    r"nothing(?:\s+(?:right\s+now|at\s+the\s+moment|really|much|else|for\s+now|today))*|thanks\s+for\s+listening|"
    r"(?:a\s+)?quick\s+check[-\s]?in"
    r")(?:[,\s]+(?:then|though|thanks|thank\s+you|i\s+guess|i\s+suppose|honestly|really|sir|jarvis|mate|man))*\W*$", re.I)
# Never a remark, whatever its shape (review 2026-11-12: "Good, next", "Not bad, louder", "That's enough", "Same again",
# "It's a bit loud", "It's getting dark" took the tool-less chat path): a word that orders or adjusts, or the state of
# the room or the machine.
_ADJUSTS = re.compile(
    r"\b(?:next|previous|more|less|louder|quieter|brighter|dimmer|darker|lighter|faster|slower|higher|lower|again|(?<!fair )enough|"
    r"play|stop|done|pause|skip|back|forward|resume|repeat|undo|cancel|off|(?<!going )on|up|down|mute|unmute|close|open|turn|switch|"
    r"that'll\s+do|time\s+to|"
    r"(?:too|a\s+bit|a\s+little|getting|kind\s+of|so|really|very|pretty|quite|still)\s+(?:loud|noisy|quiet|dark|bright|hot|"
    r"cold|warm|stuffy|freezing|chilly|dim|slow|frozen|stuck|broken)|"
    r"(?:it|that|this)(?:'s|\s+is)\s+(?:loud|noisy|quiet|dark|bright|hot|cold|warm|stuffy|freezing|chilly|dim|slow|frozen|"
    r"stuck|broken)|in\s+here)\b", re.I)


def remark(said: str) -> bool:
    """Is the WHOLE short line a remark from a closed set of shapes ("Good to hear.", "Not really.", "Quiet evening,
    then.", "That's fair."), with no word in it that orders or adjusts something and nothing about the state of the
    room? The skills recalled for a turn come from the conversation as much as from the line, so "any skill selected"
    sent these to the manual (measured 2026-11-12: 5 of 24 plain lines, each 1.4 to 2.1 s and a worse answer)."""
    text = (said or "").replace("’", "'").strip()
    return bool(text) and not _ADJUSTS.search(text) and bool(_REMARK.match(text))


# The last reply written on the chat path, per person: a question in it is conversation ("And you?"), never a step
# waiting for his yes, so his plain answer to it needs no planner (core.gateway.plainly_talk).
_CHAT_REPLY: dict = {}
CHAT_REPLY_SECONDS = 900


def _flat(text) -> str:
    return re.sub(r"\W+", " ", str(text or "")).strip().casefold()


def note_reply(uid: str, reply: str, chat: bool) -> None:
    """Remember (or forget) that the reply just given to this person was written on the chat path."""
    import time
    if chat and _flat(reply):
        _CHAT_REPLY[uid] = (_flat(reply), time.monotonic())
    else:
        _CHAT_REPLY.pop(uid, None)


def asked_in_chat(history, uid: str | None = None) -> bool:
    """Is the newest thing Jarvis said in `history` a chat-path reply (so a question in it asks for no go-ahead)?"""
    import time
    try:
        if uid is None:
            from core import users
            uid = users.current()
        kept = _CHAT_REPLY.get(uid)
        if not kept or time.monotonic() - kept[1] > CHAT_REPLY_SECONDS:
            return False
        before, tooled = _before(history, "")
        said = _flat(before)
        return bool(said) and not tooled and (said == kept[0] or (len(said) >= 12 and (said in kept[0] or kept[0] in said)))
    except Exception:  # noqa: BLE001 - unsure: the question counts, as before
        return False


def _before(history, said):
    """(what Jarvis said just before his line, did that turn use a tool). The history may or may not end with his line."""
    turns = [m for m in (history or []) if isinstance(m, dict)]
    if turns and turns[-1].get("role") == "user":
        turns = turns[:-1]
    text, tooled = "", False
    for m in reversed(turns):
        content = m.get("content")
        blocks = content if isinstance(content, list) else []
        kinds = {(b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) for b in blocks}
        if m.get("role") == "user" and "tool_result" not in kinds:
            break                                        # his previous message: the exchange before this line ends here
        tooled = tooled or bool(kinds & {"tool_use", "tool_result"}) or bool(m.get("tool_calls")) or m.get("role") == "tool"
        if m.get("role") == "assistant" and not text:
            text = content if isinstance(content, str) else " ".join(
                (b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "") or "") for b in blocks
                if (b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) == "text")
    return text or "", tooled


# A decline that may answer a question about a step ("Shall I delete it?" -> "Nothing else", "I'm good", "Not really").
_DECLINE = re.compile(r"^\W*(?:(?:yeah|well|no|nope|nah|honestly)[,\s]+)*(?:nothing(?:\s+(?:right\s+now|at\s+the\s+moment|really|much|else|for\s+now|today))*|i'?m\s+(?:good|fine|all\s+right|okay|ok)|not\s+really|all\s+good)\W*$", re.I)


# A line that is only a greeting: "Hey Jarvis.", "Hello again.", "Good evening, sir.", "Bonjou Jarvis.". It opens a
# fresh exchange wherever it comes. Live 2026-11-12: "Hey Jarvis." right after a report was no small talk (the name
# and its "hey" are stripped as an address, leaving nothing) and, coming after a skill, counted as a clipped command:
# it went down the manual's path with the last topic's skills and results in view and got more of that topic.
_HELLO = (r"h(?:i|ello|ey|iya|owdy)|yo|good\s+(?:morning|afternoon|evening|day)|morning|afternoon|evening|"
          r"bonjou|bonswa|alo|bonjour|bonsoir|salut|hola|buenos\s+d[ií]as|buenas\s+(?:tardes|noches)")
_GREETING = re.compile(rf"^\W*(?:(?:{_HELLO})(?:[,\s]+(?:there|again|jarvis|sir|mesye|you|everyone|buddy|mate|my\s+friend))*[,.!\s]*)+$",
                       re.I)


def greeting(said: str) -> bool:
    """Is the whole line a greeting and nothing else?"""
    text = (said or "").replace("’", "'").strip()
    return bool(text) and len(text.split()) <= 6 and bool(_GREETING.match(text))


# ------------------------------------------------------------------------------------------------- a new subject
# A chat turn sees the conversation so far, and a small model keeps talking about whatever fills it: after a long
# answer about one thing, an unrelated line got more of that thing. A line that is a greeting, or that shares no
# content word with the exchange before it and has no word pointing back ("and why is that?", "what about the other
# one?"), is a new subject: the turn's note says so. A real follow-up keeps the context and gets no such note.
_POINTS_BACK = re.compile(
    r"^\W*(?:and|but|so|also|then|or|because|why|how\s+so|how\s+come|what\s+about|which\s+one|really|seriously|wait)\b|"
    r"\b(?:that|this|it|its|it's|those|these|them|they|one|ones|same|other|another|else|instead|again|too|either|"
    r"more|less|earlier|before|you\s+(?:said|mean|meant|mentioned)|go\s+on|keep\s+going|continue|example|explain|"
    r"elaborate|deeper|further|detail|details|simpler|shorter|longer|rephrase|summar\w+|why|how\s+come|"
    # Review 2026-11-12: "How many people live there?" after Paris and "How old was she?" after Marie Curie were
    # reset. Any word that stands for a person, place or thing named earlier points back.
    r"he|she|him|her|hers|his|he's|she's|their|theirs|there|here|there's)\b", re.I)
_THE = re.compile(r"\bthe\b", re.I)
_SMALL_WORDS = set("the a an and or but of to in on at for with is are was were be been am do does did can could would "
                   "should will have has had not no yes i you he she we they me him her us my your his our their what "
                   "which who how when where there here now just about from so if then than very really some any all "
                   "get got go going think know want need like good day today tonight thanks thank please jarvis sir "
                   "hey hi hello okay ok well oh yeah".split())
FRESH_NOTE = ("(Their newest line starts a new subject: answer that line on its own. Say nothing more about what was "
              "discussed before it.)")
GREETING_NOTE = ("(They are only greeting you: greet them back in a few words and stop. It is a fresh start: nothing "
                 "about what was discussed before, no report, no question about their plans.)")


def _content_words(text: str) -> set:
    return {w[:6] for w in re.findall(r"[a-z\u00C0-\u024F']{3,}", (text or "").casefold()) if w not in _SMALL_WORDS}


def _other_tongue(text: str) -> bool:
    """Is this line in Creole, French or Spanish (or anything not plainly English)? Then nothing here can tell a
    follow-up from a new subject, and unsure means no (review 2026-11-12: every Creole follow-up was reset)."""
    try:
        from audio import language
        return language.identify(text)[0] not in (None, "en") or not text.isascii()
    except Exception:  # noqa: BLE001
        return True


def new_subject(said: str, history) -> bool:
    """Does his line start a new subject after an exchange about something else? Unsure means no: a follow-up that
    loses its context is the worse mistake."""
    text = (said or "").replace("’", "'").strip()
    before, _ = _before(history, said)
    if not text or not _flat(before):
        return False                     # the first line of a conversation has nothing to reset
    if greeting(text):
        return True
    mine = _content_words(text)
    if len(text.split()) < 3 or not mine or _POINTS_BACK.search(text):
        return False
    if len(text.split()) <= 5 and _THE.search(text):
        return False                     # "Is the food good?": a short line about "the" something leans on what came before
    if _other_tongue(text):
        return False                     # the words that point back, and the small words, are known in English only
    return not (mine & (_content_words(before) | _content_words(_his_line_before(said, history))))


# ------------------------------------------------------------------------------- the memory lookup on a plain remark
# The brain attaches what it remembers about the topic to every question (<retrieved-memory>). On a plain chat turn that
# is no question about HIM, the lexical hits ("work" -> his job, "creative" -> his studies) were recited back: a reply
# to "Even for creative work?" brought in his certification, "Do you get tired?" brought in leg day (replay 2026-11-13,
# 11 of 54 chat replies dragged his background in). What he said in this conversation is still in view, and a question
# about him (about_them) keeps its lookup.
def without_recall(messages):
    """`messages` with the <retrieved-memory> part taken off the newest user message (a copy; nothing else changes)."""
    out = list(messages)
    for n in range(len(out) - 1, -1, -1):
        m = out[n]
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):
            kept = [p for p in content if not (isinstance(p, dict) and p.get("type") == "text"
                                               and str(p.get("text", "")).lstrip().startswith("<retrieved-memory>"))]
            if kept and len(kept) != len(content):
                out[n] = dict(m, content=kept)
        break
    return out


# ------------------------------------------------------------------------------- the background on a plain remark
# The background's facts about him and his latest conversations ("Still open: finish the rig weights") sit in the chat
# prompt of every turn, and a 9B model reaches for them: "Do you get tired?" was answered with leg day, "What would you
# do tonight?" with his son's party, "Which would you pick?" with his rig (replay 2026-11-13: 22 of 212 replies). On a
# chat turn that is not about him (a remark, an opinion, a how-to) the facts and the old conversations are left out; his
# habits, the household facts and the lessons stay. Any question about him or his things (my, me) keeps everything.
_BACKGROUND_FACTS = re.compile(
    r"^(?:What you remember about this person|Your (?:latest|recent) conversations with this person)[^\n]*\n(?:- [^\n]*(?:\n|$))*",
    re.M)
_RECALLS = re.compile(r"\b(?:remember|remind|know about|told you|i told|i said|i mentioned|you know)\b", re.I)
# A question that asks what is known about a person, a date or a plan ("Who is Maya?", "When is the dentist
# appointment", "What's the plan for Saturday") has no "my" in it, but only the lookup holds the answer: it keeps it.
_LOOKUP_Q = re.compile(r"^\W*(?:(?:who|when|where)(?:\s+(?:is|are|was|were|did|does|do|will)\b|'s)|"
                       r"what(?:'s|\s+is|\s+was)\s+(?:the\s+)?(?:plan|schedule|time|date|address|number|name|deal)\b)", re.I)


def wants_recall(said: str) -> bool:
    """Does this line ask Jarvis to remember or look something up (the lookup and the background stay)?"""
    text = (said or "").replace("’", "'")
    return bool(_RECALLS.search(text) or _LOOKUP_Q.search(text))


def _on_background(messages, change):
    """`messages` with `change` applied to the text that holds the <background> block, wherever the brain put it: the
    system message, or a text part of one of his messages (the brain attaches it to the conversation's first line and
    core.contextfit moves it into the system text later). Until 2026-11-14 only the system message was looked at, so on
    the live path nothing was ever left out: the facts reached every chat turn."""
    out, found = [], False
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else None
        if not found and isinstance(content, str) and "<background>" in content:
            m, found = dict(m, content=change(content)), True
        elif not found and isinstance(content, list):
            parts = []
            for part in content:
                text = part.get("text") if isinstance(part, dict) and part.get("type") == "text" else None
                if not found and isinstance(text, str) and "<background>" in text:
                    part, found = dict(part, text=change(text)), True
                parts.append(part)
            if found:
                m = dict(m, content=parts)
        out.append(m)
    return out if found else messages


# His own things or himself, in any wording: "Am I married?", "Tell me my wife's name", "Quel est mon nom ?". Wider than
# intelligence.about_them (which needs a question word in front). A line this takes wrongly ("Oh my god") only keeps the
# facts it has always had on the live app; nothing is added for it.
_MINE = re.compile(r"\b(?:my|mine|our|ours|myself|mon|ma|mes|mwen)\b|\b(?:am|was|did|have|do)\s+i\b|\b(?:suis-je|ai-je)\b", re.I)


def lean_messages(messages, said: str, kind: str):
    """`messages` with the background's facts and old conversations left out, for a plain remark on a chat turn. A line
    about him or his things (intelligence.about_them, _MINE) or one that asks to recall keeps everything, untouched.

    Two review rounds (2026-11-14) on a finer rule (only the facts his words point at, moved onto his newest line) found a
    lost fact or a returned leak in each version; this is the narrow rule the code always meant to apply, now applied
    where the background really is (_on_background)."""
    from core import intelligence
    if kind not in (CHAT, DEPTH, THINK) or not messages:
        return messages
    if intelligence.about_them(said) or wants_recall(said) or _MINE.search((said or "").replace("’", "'")):
        return messages
    return _on_background(messages, lambda text: _BACKGROUND_FACTS.sub("", text))


ACK_TAIL = 260          # characters kept of each message shown before an acknowledgement
ACK_KEEP = 4            # messages (two exchanges) shown before an acknowledgement


def lean_history(messages, said: str):
    """For an acknowledgement ("Right.", "Thanks.", "That makes sense.") the conversation before it is only context: the last
    two exchanges, the end of each message (a lesson reply is 1,000 words and ends with the point), no tool rounds. The
    persona and his line are untouched. Any other line gets `messages` back as it is (the same list). Measured: "Right."
    in a lesson carried 3.8k prompt tokens on the chat path, 2.2k without the lesson text."""
    if not acknowledgement(said) or len(messages) < 3 or messages[0].get("role") != "system":
        return messages
    body = []
    for m in messages[1:-1]:
        if m.get("role") not in ("user", "assistant") or m.get("tool_calls"):
            continue
        content = m.get("content")
        if isinstance(content, list):                    # a turn stored as blocks (an attached image, injected context): its text
            content = " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
        if isinstance(content, str) and content.strip():
            body.append(dict(m, content=content))
    cut = [dict(m, content=m["content"] if len(m["content"]) <= ACK_TAIL else "…" + m["content"][-ACK_TAIL:])
           for m in body[-ACK_KEEP:]]
    while cut and cut[0].get("role") != "user":
        cut = cut[1:]
    return [messages[0]] + cut + [messages[-1]]


# ------------------------------------------------------------------------------- speech that is not meant for him
# The microphone is open while he talks to someone else or dictates to another program (live 2026-11-13 replay: a
# 600-word briefing for another assistant was answered with "I hear you, sir. I will focus on being as helpful as
# possible..." again and again). A long stretch of speech with no question in it, without his name, after a turn that
# asked him nothing and ran nothing is probably not for Jarvis. The chat path (which may answer [ignore]) judges it with
# OVERHEARD_NOTE; it is never dropped here, so a long request that really is for him still gets an answer.
OVERHEARD_WORDS = 30
OVERHEARD_LONG = 45
_NAMED = re.compile(r"\b(?:a?j[ae]r[vw]is|jarvi\w*|jarvas)\b", re.I)


OVERHEARD_ORDER_MAX = 80       # a line this long with a command word in it is a briefing, not one order
_CHANNEL = re.compile(r"^\s*\[Sent from their Jarvis phone app[^\]]*\]\s*")


def typed(history) -> bool:
    """Was his newest message typed or sent from the phone app (the app's channel note is on it)? It is for Jarvis."""
    turn = history[-1] if history else {}
    if not isinstance(turn, dict) or turn.get("role") != "user":
        return False
    content = turn.get("content")
    texts = [content] if isinstance(content, str) else [
        str(b.get("text", "")) for b in (content or []) if isinstance(b, dict) and b.get("type") == "text"]
    return any(_CHANNEL.match(t) for t in texts)


def overheard(said: str, history=()) -> bool:
    text = (said or "").replace("’", "'").strip()
    count = len(text.split())
    if count < OVERHEARD_WORDS or "?" in text or _NAMED.search(text) or typed(history):
        return False
    # A line that orders or asks for work is for him however long it is (a three-step order runs past thirty words); only
    # an enormous one is a briefing. A question in another language comes without "?" as often as not: not judged.
    if count <= OVERHEARD_ORDER_MAX:
        from core import intelligence
        if orders(text) or (asks_to_act(text) and not intelligence.just_asking(text)):
            return False
    if _other_tongue(text):
        return False
    before, tooled = _before(history, said)
    if tooled:
        return False
    # After a question or an offer of his own a long line may be the answer to it: only a very long one is judged anyway
    # (the note lets the chat path answer when it does answer him).
    return len(text.split()) >= OVERHEARD_LONG or ("?" not in before and not _OFFER.search(before))


_INTERJECTION = (r"yes|yeah|yep|yup|ya|no|nope|nah|ok|okay|k|oh|ah|aha|hm+|huh|uh+|um+|er|sigh|wow|ha+|heh|right|alright|"
                 r"all\s+right|exactly|exactement|indeed|sure|fine|cool|nice|great|good|thanks|thank\s+you|cheers|gracias|merci|"
                 r"got\s+it|i\s+see|understood|noted|done|finished|stopped|sorry|never\s*mind|whatever|anyway|well|so|and|then|"
                 r"but|sir|man|dude|bro|mate|jarvis|hey|please|oui|non|wi|dak[oò]|(?:it|that)\s+(?:actually\s+|really\s+|just\s+)?(?:helps?|helped|works?|worked)")
_FRAGMENT_TALK = re.compile(rf"^\W*(?:(?:{_INTERJECTION})\b\W*)+$", re.I)


def fragment_talk(said: str) -> bool:
    """Is the whole line only interjections, yes or no, thanks or acknowledgements ("Oh.", "Okay, got it.", "Right.")?"""
    return bool(_FRAGMENT_TALK.match((said or "").replace("’", "'").strip()))


# An acknowledgement is not a request and not his answer to a question: "Right.", "Okay, got it.", "Thanks.", "That makes
# sense.", "This is simpler than I thought.". Closed set; yes, no, sure, please and "go ahead" are answers and are not in it.
_ACK_WORDS = (r"right|ok|okay|k|alright|all\s+right|got\s+it|i\s+see|i\s+get\s+it|now\s+i\s+get\s+it|understood|noted|cool|nice|"
              r"great|good|fine|indeed|exactly|thanks|thank\s+you|cheers|aha|ah|oh|hm+|huh|wow|interesting|makes\s+sense|"
              r"that\s+makes\s+sense|it\s+makes\s+sense")
_ACK = re.compile(rf"^\W*(?:(?:{_ACK_WORDS})\b[\s,.!]*)+(?:sir|jarvis)?\W*$", re.I)


def acknowledgement(said: str) -> bool:
    """Is the whole line only an acknowledgement or a remark that things are clear ("Right.", "Thanks.", "That makes sense.",
    "This is simpler than I thought.")? It asks for nothing."""
    text = (said or "").replace("’", "'").strip()
    return (bool(text) and len(text.split()) <= 9 and "?" not in text and not _ANSWER_LEAD.match(text)
            and bool(_ACK.match(text) or remark(text)))


# A line that opens on a yes, a no or a go-ahead is an answer to Jarvis ("Yes, that's fine.", "No, that's fine.", "Sure."), whatever follows.
_ANSWER_LEAD = re.compile(r"^\W*(?:yes|yeah|yep|yup|ya|no|nope|nah|sure|please|go\s+(?:ahead|on)|do\s+it)\b", re.I)
_EXPLICIT_OFFER = re.compile(r"\b(?:i\s+can|i\s+could|shall\s+i|should\s+i|want\s+me\s+to|would\s+you\s+like|do\s+you\s+want|"
                             r"if\s+you(?:'d|\s+would)?\s+like|say\s+the\s+word|just\s+say|let\s+me\s+know\s+if|shall\s+we|"
                             r"want\s+to\s+(?:start|begin|try|do|see|review|go|move)|ready\s+(?:to|for))\b", re.I)


def lesson_ack(said: str, history) -> bool:
    """An acknowledgement right after a lesson that offered him nothing to say yes to (its "Check yourself: ...?" asks for no
    go-ahead). A lesson reply that does offer something ("Want me to start a review session now?") keeps its old route."""
    if not (acknowledgement(said) and lesson_turn(history)):
        return False
    before, _ = _before(history, said)
    return not _EXPLICIT_OFFER.search((before or "").replace("’", "'"))


def lesson_turn(history) -> bool:
    """Did the exchange just before his line run a study skill (the tutor)? What a lesson ends with ("Check yourself: ...?")
    asks him for no go-ahead: an acknowledgement after it is only that."""
    turns = [m for m in (history or []) if isinstance(m, dict)]
    if turns and turns[-1].get("role") == "user" and not (isinstance(turns[-1].get("content"), list)
                                                          and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                                  for b in turns[-1]["content"])):
        turns = turns[:-1]
    for m in reversed(turns):
        content = m.get("content")
        blocks = content if isinstance(content, list) else []
        kinds = {(b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) for b in blocks}
        if m.get("role") == "user" and "tool_result" not in kinds:
            return False
        for b in blocks:
            name = b.get("name") if isinstance(b, dict) else getattr(b, "name", "")
            if (b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) == "tool_use" and str(name).startswith("study_"):
                return True
    return False


def command_like(said: str, history, schemas=()) -> bool:
    """Is this short line his answer to Jarvis or a clipped command, whatever it reads like? Then it is not chat."""
    text = (said or "").replace("’", "'").strip()
    words = text.split()
    if not words or len(words) > 6 or "?" in text or greeting(text):
        return False
    if lesson_ack(text, history):
        return False                                     # "Right." after the tutor: the lesson's own question wants no yes
    before, tooled = _before(history, said)
    if _YES_NO.match(text) and _OFFER.search(before):
        return True                                      # "Sure." to "Shall I set a reminder?" / "I can look that up."
    if _DECLINE.match(text) and _OFFER.search(before):
        return True                                      # "Nothing else." / "I'm good." to "Shall I delete it?"
    if len(words) > 4:
        return False
    if _CLIPPED.match(text):
        return True                                      # "Quieter", "Next one", "Lights off", "Not that one"
    from core import intelligence
    if intelligence.small_talk(text, "?" in before):
        return False                                     # "Thanks.", "Ha, fair enough.": talk wherever it comes
    # What Jarvis says when he acted is short ("Playing jazz now, sir."); "the sun is setting" in a long explanation
    # is none, and "Huh, interesting." after it was answered with the canned what-did-you-mean (eval 2026-11-12).
    acted = len(before.split()) <= 30 and bool(_ACTED.search(before))
    # Nothing was just done, offered or asked, and the fragment is only an interjection, a yes or no, or an
    # acknowledgement ("Oh.", "Okay, got it.", "Sigh.", "Right."): there is nothing to act on, so it is talk even when the
    # lexical skill recall found something (it found some for most of them: 28 of 119 real conversational lines went to
    # the manual's path that way, replay 2026-11-13, and were answered with stale replies, offers and "I lost my train of
    # thought"). Any other short fragment with a skill recalled ("It's too loud", "Jazz maybe") keeps the tools.
    return tooled or acted or (bool(schemas) and not remark(text) and not fragment_talk(text))


# ---------------------------------------------------------------------------- "Simpler." about his previous answer
# With no lesson open, "Simpler.", "More detail.", "Shorter.", "Say that again.", "Explain that differently." and "Give me an
# example." are about what Jarvis just answered (a lesson's follow-ups belong to the tutor: core/studyintent). They took the
# manual's path: about 9,000 prompt tokens and 3 to 9 s, and with nothing to restyle the model invented a topic. The whole
# line has to be the request (a closed set); anything with an object ("make the font simpler", "more detail on the invoice",
# "shorter timer", "give me an example file") is a command or a question and keeps its route.
_RESTYLE_LEAD = (r"^\W*(?:(?:ok|okay|so|and|now|hey|alright|all\s+right|uh|um|sorry|jarvis)\b[,.\s]*)*"
                 r"(?:(?:can|could|would|will)\s+you\s+)?(?:(?:please|just)\s+)?")
_RESTYLE_TAIL = r"(?:\s+(?:please|sir|jarvis|for\s+me))*\W*$"
_THAT = r"(?:it|that|this)"
_BIT = r"(?:(?:a\s+(?:bit|little)|much|way|even)\s+)?"
_RESTYLES = {
    "simpler": [rf"(?:explain|say|put|make|tell|do)\s+{_THAT}\s+{_BIT}simpler", rf"explain\s+{_THAT}\s+(?:in\s+)?(?:simple|plain)\s+(?:terms|words|english)",
                rf"(?:explain\s+)?{_BIT}simpler(?:\s+(?:terms|words))?", r"simplify(?:\s+(?:it|that|this))?",
                r"in\s+(?:simpler|plain|simple)\s+(?:terms|words|english)", r"dumb\s+(?:it|that)\s+down"],
    "longer": [r"(?:(?:a\s+(?:bit|little)\s+)?more|in\s+more|some\s+more|further)\s+detail(?:s|ed)?", r"more\s+detailed", r"longer",
               rf"make\s+{_THAT}\s+{_BIT}longer", rf"elaborate(?:\s+on\s+{_THAT})?", r"(?:go\s+into|give\s+me)\s+(?:a\s+bit\s+)?more\s+detail",
               rf"expand\s+on\s+{_THAT}"],
    "shorter": [rf"(?:make|say|put|keep)\s+{_THAT}\s+{_BIT}(?:shorter|briefer|short)", r"(?:a\s+bit\s+)?(?:shorter|briefer)", rf"shorten\s+{_THAT}",
                r"(?:the\s+)?short\s+version", r"more\s+(?:briefly|concisely)", rf"sum\s+{_THAT}\s+up", rf"summari[sz]e\s+{_THAT}"],
    "example": [r"(?:give|show)\s+me\s+(?:an?|another|one\s+more|a\s+quick|a\s+simple)\s+example", r"(?:an?|another)\s+example",
                r"for\s+example", r"example"],
    "again": [rf"say\s+{_THAT}\s+(?:again|once\s+more|one\s+more\s+time)", rf"tell\s+me\s+{_THAT}\s+(?:again|once\s+more)", r"say\s+again"],
    "other": [rf"(?:explain|say|put|tell\s+me)\s+{_THAT}\s+(?:differently|another\s+way|in\s+(?:another|a\s+different|other)\s+(?:way|words))",
              rf"(?:rephrase|reword|re-?explain|paraphrase)\s+{_THAT}", r"in\s+other\s+words", r"another\s+way", r"a\s+different\s+way",
              r"differently"],
}
_RESTYLE_RES = {style: re.compile(_RESTYLE_LEAD + r"(?:" + "|".join(parts) + r")" + _RESTYLE_TAIL, re.I) for style, parts in _RESTYLES.items()}


def restyle(said: str) -> str:
    """Which restyle of his previous answer this whole line asks for ('simpler', 'longer', 'shorter', 'example', 'again',
    'other'), or ''. Judged on the words alone: whether there is an answer to restyle is restyle_target's question."""
    text = (said or "").replace("’", "'").strip()
    if not text or len(text.split()) > 9:
        return ""
    for style, rx in _RESTYLE_RES.items():
        if rx.match(text):
            return style
    return ""


def restyle_target(said: str, history) -> str:
    """'' when this is not a restyle follow-up that belongs to the chat path (not one of the phrases, a lesson is open, or
    Jarvis' last words offered him something to say yes to); 'answer' when there is an answer of Jarvis' just before the
    line to restyle; 'nothing' when there is none (a first line). After a short action reply ("Playing jazz now, sir.") it is '' too: the line is a clipped command."""
    if not restyle(said) or lesson_turn(history):
        return ""
    before, tooled = _before(history, said)
    flat = " ".join((before or "").split())
    if not flat:
        return "nothing"
    words = len(flat.split())
    if _OFFER.search(flat) and words <= 12:
        return ""                                        # "Shall I set a reminder?": his words answer that, not restyle it
    if words <= 10 and _ACTED.search(flat):
        return ""                                        # "Setting a timer, sir.": "Shorter." is a clipped command, keep its tool route
    return "answer"


def restyle_profile(said: str) -> str:
    return DEPTH if restyle(said) in ("longer", "example") else CHAT


RESTYLE_NOTES = {
    "simpler": "(They want your previous answer again, but simpler: plain everyday words, short sentences, no jargon, one comparison if it helps. The same facts, not the same sentences, and never \"as I said\". Two to four sentences.)",
    "longer": "(They want more detail on your previous answer: what it left out, the reason behind it and one concrete example, in four to six spoken sentences. Do not repeat what you said, and never \"as I said\".)",
    "shorter": "(They want your previous answer shorter: the one thing that matters, in a single short sentence. Not a repeat of it, and never \"as I said\".)",
    "example": "(They want one concrete everyday example of your previous answer: two or three spoken sentences about the example. Do not repeat the answer, and never \"as I said\".)",
    "again": "(They did not catch your previous answer: say it again in other words, a little simpler, in no more sentences than before. Not the same sentences, and never \"as I said\".)",
    "other": "(They want your previous answer explained a different way: another comparison or angle, other words. Not the same sentences, and never \"as I said\".)",
}
RESTYLE_NOTES["again"] = RESTYLE_NOTES["again"][:-1] + " Say nothing about earlier attempts: never \"my earlier attempt\", \"as I said\" or \"to rephrase\".)"
# A restyle that follows another restyle is about the SUBJECT, the first question and Jarvis' answer to it, never about the
# comparison or example in the latest reply (live 2026-11-13: sky, beach water, a crowded room, sound waves).
_CHAIN_HEAD = "(The subject is still: \"{q}\" and your first answer to it, above. Your latest reply is above too. "
_CHAIN_TAIL = " The new reply must be different from your latest reply. Start straight with the content: no remark about the request or about earlier replies. Never \"as I said\", \"my earlier attempt\" or \"to rephrase\".)"
CHAIN_NOTES = {
    "simpler": "Say the subject again, simpler: plain everyday words, short sentences, two to four sentences.",
    "longer": "Begin with a fact. Add true detail about the subject itself, not about any comparison or example in your latest reply: what it left out and the reason behind it, in four to six spoken sentences.",
    "shorter": "Say the subject in a single short sentence.",
    "example": "Give one concrete everyday example OF the subject: a real situation where it happens or can be seen, not a comparison or an analogy and not an example of an earlier example, in two or three spoken sentences.",
    "again": "Say your latest reply again in other words, a little simpler, in no more sentences than it had, with no comment about earlier attempts.",
    "other": "Explain the subject another way: a new angle, other words, not built on the comparison in your latest reply.",
}
RESTYLE_NOTHING = ("(There is nothing earlier in this conversation of yours to change. Say so in one short sentence and ask what they "
                   "would like explained. Do not name or invent a topic.)")
NOTHING_LINES = {
    "simpler": "What should I simplify, {t}? I haven't explained anything yet.",
    "longer": "More detail on what, {t}? I haven't said anything about it yet.",
    "shorter": "What should I shorten, {t}? I haven't said anything yet.",
    "example": "An example of what, {t}? Give me the topic and I will.",
    "again": "Say what again, {t}? I haven't said anything just now.",
    "other": "What should I explain differently, {t}? I haven't explained anything yet.",
}


def nothing_to_restyle(said: str, title: str = "sir") -> str:
    """The one short, honest line for a restyle follow-up with no answer before it (no model call)."""
    return NOTHING_LINES.get(restyle(said), NOTHING_LINES["simpler"]).replace("{t}", title or "sir")


RESTYLE_TOKENS = 3500            # the restyle prompt's budget (system text included); the original answer's middle goes first
_CUT = " [...] "


def _short(text: str, n: int = 120) -> str:
    flat = " ".join((text or "").split()).replace('"', "'")
    return flat if len(flat) <= n else flat[:n].rsplit(" ", 1)[0] + "..."


def _turns(messages):
    """(role, plain text) of the spoken turns: tool rounds, empty and non-text messages left out."""
    from core import intelligence
    out = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or m.get("tool_calls"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = intelligence.last_user_text([m]) if m.get("role") == "user" else " ".join(
                str(b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "") or "")
                for b in content if (b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) == "text").strip()
        if isinstance(content, str) and content.strip():
            out.append((m["role"], content))
    return out


def restyle_subject(history, said: str):
    """What a restyle line is about: {'question', 'answer', 'chain', 'latest'} or None. `question` is the last line of his
    before this one that was not itself a restyle, `answer` Jarvis' reply to it; `chain` are the restyle exchanges after
    it (oldest first, (line, reply)), `latest` the newest reply (the answer itself when there is no chain). The search
    never goes past the first non-restyle exchange. `history` may or may not end with his line."""
    turns = _turns(history)
    if turns and turns[-1][0] == "user":
        turns = turns[:-1]
    groups = []                                           # [his line, Jarvis' last words after it]
    for role, text in turns:
        if role == "user" or not groups:
            groups.append([text if role == "user" else "", "" if role == "user" else text])
        else:
            groups[-1][1] = text
    chain = []
    for line, reply in reversed(groups):
        if not reply:
            continue
        if restyle(line):
            chain.append((line, reply))
            continue
        chain.reverse()
        return {"question": line, "answer": reply, "chain": chain, "latest": chain[-1][1] if chain else reply}
    return None


def _cut_middle(text: str, keep: int) -> str:
    if len(text) <= keep:
        return text
    head = max(keep - len(_CUT), 0) * 2 // 3
    tail = max(keep - len(_CUT) - head, 0)
    return text[:head].rstrip() + _CUT + (text[len(text) - tail:].lstrip() if tail else "")


def restyle_history(messages, said: str):
    """For a restyle follow-up the SUBJECT is the whole conversation: his last question that was not a restyle and Jarvis'
    answer to it, in full (an overlong answer loses its middle, never the question), and when the previous reply was itself
    a restyle that one exchange too, so the new reply differs from it. Older turns, tool rounds and the background blocks are
    left out. The persona and his line are untouched. Anything else gets `messages` back as it is."""
    if not restyle(said) or len(messages) < 3 or messages[0].get("role") != "system":
        return messages
    subject = restyle_subject(messages[1:-1], said)
    if subject is None:
        turns = _turns(messages[1:-1])                    # only restyle lines before: the previous exchange as it was
        cut = next((i for i in range(len(turns) - 1, -1, -1) if turns[i][0] == "assistant"), None)
        if cut is None:
            return [messages[0], messages[-1]]
        keep = turns[cut - 1:cut + 1] if cut > 0 and turns[cut - 1][0] == "user" else turns[cut:cut + 1]
        return [messages[0]] + [{"role": r, "content": t} for r, t in keep] + [messages[-1]]
    answer = subject["answer"]
    fixed = len(str(messages[0].get("content", ""))) + len(str(messages[-1].get("content", ""))) + len(subject["question"])
    if subject["chain"]:
        line, reply = subject["chain"][-1]
        fixed += len(line) + len(reply)
    room = int(RESTYLE_TOKENS * 3.6) - fixed
    if len(answer) > room:
        answer = _cut_middle(answer, max(room, 500))
    body = [{"role": "user", "content": subject["question"]}] if subject["question"] else []
    body.append({"role": "assistant", "content": answer})
    if subject["chain"]:
        body += [{"role": "user", "content": subject["chain"][-1][0]}, {"role": "assistant", "content": subject["chain"][-1][1]}]
    return [messages[0]] + body + [messages[-1]]


# ---- the subject check after generation: a restyle reply with no content word of the subject is written once more
# Filler that carries no subject (review 2026-11-13: "more" and "short" let "Nothing more to say, in short." pass), and
# the small words of Spanish, French and German, whose plain letters the English lists do not know.
_NOISE = set("more most less least much many few little short long term terms work works worked working use used using "
              "make made makes making thing things way ways kind sort lot lots part parts bit well also even still "
              "other another same such own back down over under again once ever never always often sometimes "
              "something nothing anything everything someone anyone everyone say said says mean means meant "
              "los las del una uno unos unas con por para que como pero sus este esta esto son "
              "les des une est sont dans pour qui avec sur pas "
              "der die das und ist ein eine nicht mit von".split())
_NOISE_STEMS = None


def subject_words(text: str) -> set:
    """Content stems of a text: stop words (all lists) out, plural and -ing/-ed endings folded."""
    global _NOISE_STEMS
    from core import intelligence
    if _NOISE_STEMS is None:
        _NOISE_STEMS = {intelligence._stem(w) for w in _NOISE}
    out = set()
    for w in re.findall(r"[a-z]{3,}", (text or "").casefold()):
        st = intelligence._stem(w)
        if (w not in _SMALL_WORDS and w not in intelligence._STOP and st not in intelligence._STOP_STEMS
                and w not in _NOISE and st not in _NOISE_STEMS):
            out.add(st)
    return out


# A restyle reply that talks about the request or about his earlier replies instead of saying the thing. Two kinds:
# _REQ ("You asked for ...", "The previous answer ...", "I gave you ...": about the exchange; only a ", so X", ": X" or "; X"
# clause after it can be content) and _LEAD ("Let me ...", "Here is ...", "To rephrase, ...": an announcement; a comma
# clause after it is content too).
_REQ = re.compile(
    r"^\W*(?:"
    r"(?:the|my|your|our|that)\s+(?:previous|earlier|last|first|initial|prior|original|preceding)\s+"
    r"(?:(?:explanation|answer|reply|attempt|version|sentence|statement|response|one|try)|time(?!\s+(?:you|your|he|she|they|someone|a|an)\b))|"
    r"(?:last|previous)\s+time(?!\s+(?:you|your|he|she|they|someone|a|an)\b)|"
    r"as\s+(?:i|we)\s+(?:said|mentioned|explained|noted|stated)|"
    r"(?:as\s+)?(?:mentioned|stated|said|noted)\s+(?:before|earlier|previously)|"
    r"you\s+(?:asked|wanted|requested)\s+(?:for|me|that)|"
    r"i\s+(?:have\s+)?(?:gave|given|kept|made|chose|picked|simplified|skipped|focused|tried|wrote|left\s+out)\s+"
    r"(?:you|it|this|that|things|them|more|less|fewer|the\s+(?:answer|explanation|reply|same|first|last|previous|earlier)|"
    r"a\s+(?:shorter|simpler|longer|different|bit|little))|"
    r"i\s+put\s+(?:it|this|that|things)\s+(?:more\s+|in\s+|simply|plainly|shorter|differently|another|a\s+)"
    r")\b", re.I)
_LEAD = re.compile(
    r"^\W*(?:"
    r"let\s+me\b|as\s+(?:you\s+)?(?:requested|asked)|"
    r"(?:i(?:'ll|\s+will)|allow\s+me\s+to)\s+(?:rephrase|restate|reword|put|say|explain|give|fill|add|expand|simplify|try)|"
    r"here(?:\s+is|'s|\s+are)\b|"
    r"to\s+(?:rephrase|restate|reword|put\s+it|say\s+it|simplify|clarify)"
    r")", re.I)
_INTERJECTION = re.compile(r"^\W*(?:sure|certainly|of\s+course|absolutely|gladly|very\s+well|no\s+problem|happy\s+to(?:\s+help)?)(?:\s*[,.!:;\-]+\s*|\s*$)", re.I)
_IN_OTHER_WORDS = re.compile(r"^\W*in\s+other\s+words\b[\s,:;\-]*", re.I)
# The style word or his line said back as the first word: "Shorter. Air ...", "Simpler: ...", "Example: ...".
_STYLE_ECHO = re.compile(r"^\W*(?:shorter|simpler|more\s+detail|longer|an?\s+example|example|again|differently|once\s+more)\s*(?:[.:!\u2014\u2013]+|-+)(?:\s+|$)", re.I)
_SENTENCE = re.compile(r"[.!?]+[\"')\]]*(?=\s|$)")
_SENTENCE_STREAM = re.compile(r"[.!?]+[\"')\]]*(?=\s)")
_KEEP_STRICT = re.compile(r",\s+so\s+|:\s+|;\s+")
_COMMA = re.compile(r",\s+")
_ABBREVIATION = re.compile(r"(?:(?:[A-Za-z]\.){2,}|(?:vs|dr|mr|mrs|ms|prof|approx|st|etc|inc|ltd|jr|sr|fig|no)\.)$", re.I)
_PREPOSITION = re.compile(r"^(?:in|to|of|with|on|for|by|as|at|and|or|but|that|which|so)\b", re.I)
OPENER_HOLD_WORDS = 30                    # a stream waits at most this long for an opener sentence to end
SIMPLER_WORDS = 60


def commentary(text: str) -> bool:
    """Does the reply open by speaking about the request or his earlier replies instead of answering?"""
    t = text or ""
    return bool(_REQ.search(t) or _LEAD.search(t) or _INTERJECTION.search(t) or _IN_OTHER_WORDS.search(t) or _STYLE_ECHO.search(t))


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _sentence_end(text: str, rx=None):
    """The index just after the first sentence end in `text`, or None. A full stop that closes an abbreviation or a
    dotted initialism ("U.S.", "e.g.", "vs.", "Dr.") is not a sentence end."""
    for m in (rx or _SENTENCE).finditer(text):
        if text[m.start():m.end()].strip("\"')]") == "." and _ABBREVIATION.search(text[:m.start() + 1]):
            continue
        return m.end()
    return None


def _first_sentence(text: str):
    """(first sentence, rest, whether it is complete)."""
    end = _sentence_end(text)
    return (text[:end], text[end:].strip(), True) if end else (text, "", False)


def _keep(sentence: str, loose: bool) -> str:
    """The content clause of a commentary sentence ("You asked for a different angle, so imagine X." -> "imagine X."), or ''."""
    for m in _KEEP_STRICT.finditer(sentence):
        rest = sentence[m.end():]
        # after a colon even a one-word answer is content ("Here is the answer: yes.")
        if len(rest.split()) >= (1 if m.group().startswith(":") else 3) and not _REQ.match(rest) and not _LEAD.match(rest):
            return rest
    if loose:
        # no colon or "so": a short announcement, a comma, then the thing itself; never a comma inside the announcement
        m = _COMMA.search(sentence)
        if m:
            rest = sentence[m.end():]
            if (len(sentence[:m.start()].split()) <= 6 and len(rest.split()) >= 3 and not _PREPOSITION.match(rest)
                    and not _REQ.match(rest) and not _LEAD.match(rest)):
                return rest
    return ""


def _drop_openers(text: str, said: str, final: bool):
    """(the text without its leading commentary, whether the first sentence is still being written and could be
    commentary). Whole leading sentences only; the rest is never touched, so a sentence with the answer in it stays."""
    s = (text or "").strip()
    line = " ".join((said or "").replace("\u2019", "'").split()).strip(" .!?:,")
    echo = re.compile(r"^\W*" + re.escape(line) + r"\s*(?:[.:!\u2014\u2013]+|-+)(?:\s+|$)", re.I) if 0 < len(line.split()) <= 6 else None
    for _ in range(8):
        before = s
        for rx in (_STYLE_ECHO, echo, _INTERJECTION, _IN_OTHER_WORDS):
            if rx:
                s = rx.sub("", s, count=1).lstrip()
        sentence, rest, whole = _first_sentence(s)
        if sentence and (_REQ.match(sentence) or _LEAD.match(sentence)):
            if not whole and not final:
                return s, True
            kept = _keep(sentence, bool(_LEAD.match(sentence)))
            s = _capital((kept + " " + rest).strip() if kept else rest.strip())
        if s == before:
            break
    return _capital(s) if s != (text or "").strip() else s, False


def strip_opener(text: str, said: str = "") -> str:
    """A restyle reply without the commentary it opens with: a sentence about the request or about earlier replies is
    dropped ("You asked for more detail, so let me ..."), a clause after it that says the thing is kept and capitalised
    (", so imagine X" -> "Imagine X"), and the style word or his line said back ("Shorter. ...", "Simpler: ...") goes.
    '' when everything was commentary: the caller writes it once more."""
    return _drop_openers(text, said, True)[0]


def commentary_share(text: str) -> float:
    """How much of a reply is commentary (0 to 1): the least commentary is the one kept when every try has some."""
    words = len((text or "").split())
    return 0.0 if not words else 1.0 - len(strip_opener(text).split()) / words


def sentences(text: str) -> list:
    out, rest = [], (text or "").strip()
    while rest:
        sentence, rest, _ = _first_sentence(rest)
        out.append(sentence.strip())
    return [x for x in out if x]


def fit_length(text: str, cap) -> str:
    """The reply cut at a sentence end to at most `cap` words: the first sentence always stays, a later one only when the
    whole of it still fits, and the cut ends there (never mid-sentence)."""
    if not cap:
        return text
    kept, total = [], 0
    for sentence in sentences(text):
        n = len(sentence.split())
        if kept and total + n > cap:
            break
        kept.append(sentence)
        total += n
    return " ".join(kept)


def length_cap(said: str, history):
    """The most words a restyle reply may have, or None: 'Shorter.' is strictly shorter than the latest reply, 'Simpler.' is
    no longer than the ORIGINAL answer and at most SIMPLER_WORDS words unless that is shorter."""
    style = restyle(said)
    if style not in ("shorter", "simpler"):
        return None
    subject = restyle_subject(history, said)
    if not subject:
        return None
    if style == "shorter":
        latest = len(subject["latest"].split())
        return latest - 1 if latest > 3 else None
    original = len(subject["answer"].split())
    return min(SIMPLER_WORDS, original) if original > 3 else None


def subject_overlap(reply: str, words: set) -> int:
    return len(words & subject_words(reply))


def subject_check(history, said: str):
    """(content stems of the subject's question and answer, the question) for a restyle line with an answer before it,
    else None."""
    if restyle_target(said, history) != "answer":
        return None
    subject = restyle_subject(history, said)
    if not subject:
        return None
    words = subject_words(subject["question"] + " " + subject["answer"])
    return (words, subject["question"], bool(subject["chain"])) if words else None


COMMENTARY_NOTE = ("(Your reply spoke about your earlier replies. Say the thing itself, starting with the content: no "
                   "\"previous explanation\", no \"as I said\", no \"to rephrase\", no comment on what was asked.)")


def length_note(cap: int) -> str:
    return (f"(Your reply was too long. At most {plural(cap, 'word')}, whole sentences, the one thing that matters. Start with the "
            f"content: no remark about the request.)")


def subject_note(question: str) -> str:
    return (f"(Your reply was not about the subject. The subject is \"{_short(question)}\" and your first answer to it. "
            f"Write the reply about that subject itself.)")


def plan(role, history, schemas, after_tool, response_format) -> str | None:
    """The chat profile this turn takes (CHAT, DEPTH or THINK), or None: today's path with the manual and the tools.

    schemas: the skills his words point at (already selected); after_tool: a tool's result is the newest message."""
    if not enabled() or role != "fast" or response_format or after_tool:
        return None
    if not history or history[-1].get("role") != "user" or _pictured(history):
        return None
    from core import intelligence
    said = intelligence.last_user_text(history)
    if greeting(said):
        return CHAT                      # "Hey Jarvis." is a hello wherever it comes, never the last topic's command
    if overheard(said, history):
        return CHAT                      # not meant for him, probably: the chat path may stay silent (OVERHEARD_NOTE)
    if restyle_target(said, history):
        return restyle_profile(said)     # "Simpler." about his previous answer: the lean chat path, never the manual (restyle)
    if not said or not said.strip() or pointed_at(said, schemas) or command_like(said, history, schemas):
        return None
    if intelligence.small_talk(said, "?" in intelligence._last_assistant_text(history)):
        return CHAT                      # a hello, thanks, a laugh, a polite no: it simply lands
    deep = depth(said)
    if intelligence.about_them(said) and not intelligence.about_this_chat(said):
        # A fact about him or his things is looked up, never guessed: unless what is known is already attached to the
        # question, or it is advice he wants ("what should I do on the days I'm too tired?"), which no lookup holds.
        if intelligence._IN_VIEW.search(said) or not (intelligence.retrieved_notes(history) or deep):
            return None
    if orders(said) or (asks_to_act(said) and not intelligence.just_asking(said)):
        return None                      # an order no skill matched: the manual's path answers it, as before
    if not deep:
        return CHAT
    return THINK if thinking() else DEPTH


# Added to his newest message for this request only (never kept in the history), like intelligence._SAY_IT.
DEPTH_NOTE = ("(This one deserves a proper spoken answer: four to seven short sentences, a hundred words or so. The "
              "answer first, then the reasoning in order, one concrete example. Plain sentences for the ear, no lists.)")
# A why / how / opinion question he did NOT ask to have explained in full (core.speech.spoken_budget): the answer first and
# a short reason; "tell me more" gets the rest (owner 2026-11-21: "shorter, better answers"; he cut off the long ones).
SHORT_DEPTH_NOTE = ("(Spoken answer: say the answer itself in the first sentence, then at most two short sentences of "
                    "reason, about fifty words in all. Not their words said back, no lead-in, no offer. They will ask if "
                    "they want more.)")
BRIEF_NOTE = ("(A few words from them: answer in one short sentence, or in three or four words if it is a no or a "
              "thanks. No new topic, no question back, no offer, nothing about their projects or plans.)")
TALK_NOTE = ("(Conversation: one or two sentences, like someone who was listening, then stop. No offer and no question "
             "that changes the subject; never ask what they need, want handled or their priority. Nothing about what they or you "
             "are doing right now unless they just said it, and none of their projects, workouts, food or plans unless their "
             "line is about them.)")
KNOWN_NOTE = ("(If what you know about them above answers this, answer from it. If it does not, say plainly that you "
              "don't know. Never make it up.)")
THIS_CHAT_NOTE = "(They mean this very conversation: answer from the messages above.)"
# He asks what Jarvis is or was doing ("What's the status on the thing that you were just working on?"). Live 2026-11-14 the answer
# was "I'm ready to get back to work on 'Shadow World'": a project of his, from the background, made up as Jarvis's own task.
ACTIVITY_NOTE = ("(They ask what you are or were doing. You are not doing anything between their messages. Say only what this "
                 "conversation shows you did in the last ten minutes, in a sentence; if it shows nothing, say plainly you are "
                 "not in the middle of anything. Never name their projects or characters as something you are working on.)")
_ACTIVITY_Q = re.compile(
    r"\bwhat\s+(?:are|were)\s+you\s+(?:just\s+|currently\s+|still\s+)*(?:doing|working\s+on|up\s+to)\b|"
    r"\bwhat\s+have\s+you\s+been\s+(?:doing|up\s+to|working\s+on)\b|"
    r"\b(?:status|progress|update)\s+(?:on|of|with)\s+(?:the\s+)?(?:thing|task|job|work)\s+(?:that\s+)?you\s+(?:were|are|have\s+been)\b", re.I)
OVERHEARD_NOTE = ("(This is a long stretch of speech with no question in it, and your name was not said. It may be them "
                  "talking to someone else, a recording, or them dictating to another program: if it is clearly about or "
                  "for someone or something else (it talks about you in the third person, or speaks to another assistant "
                  "or person by name), reply with exactly [ignore]. If it could be meant for you, answer it in "
                  "a sentence or two like someone who was listening, with no promise to do anything.)")


def note(kind: str, history) -> str:
    """The one-request note for a chat turn: depth guidance, or how to answer a question about them or this chat."""
    from core import intelligence
    said = intelligence.last_user_text(history)
    notes = []
    if greeting(said):
        return GREETING_NOTE if _flat(_before(history, said)[0]) else BRIEF_NOTE
    if overheard(said, history):
        return OVERHEARD_NOTE
    target = restyle_target(said, history)
    if target:
        if target != "answer":
            return RESTYLE_NOTHING
        subject = restyle_subject(history, said)
        if subject and subject["chain"]:
            return _CHAIN_HEAD.format(q=_short(subject["question"])) + CHAIN_NOTES[restyle(said)] + _CHAIN_TAIL
        return RESTYLE_NOTES[restyle(said)]
    if _ACTIVITY_Q.search((said or "").replace("’", "'")):
        notes.append(ACTIVITY_NOTE)
    elif intelligence.about_this_chat(said):
        notes.append(THIS_CHAT_NOTE)
    elif (intelligence.about_them(said) or wants_recall(said)) and not depth(said):
        notes.append(KNOWN_NOTE)
    if kind in (DEPTH, THINK):
        from core import speech
        budget = speech.spoken_budget(said)
        notes.append(DEPTH_NOTE if budget is None else SHORT_DEPTH_NOTE if budget == speech.DEPTH_BUDGET else TALK_NOTE)
    elif not notes:
        brief = len((said or "").split()) <= 4 and "?" not in (said or "")
        notes.append(BRIEF_NOTE if brief else TALK_NOTE)
    if new_subject(said, history) and not intelligence.about_this_chat(said):
        notes.append(FRESH_NOTE)
    return " ".join(notes)


# ------------------------------------------------------------------------------------------- the gateway's pre-gate
# Words that put a line in the PC planner's hands even when it reads like talk: the machine, its parts, what is on it,
# media and anything a learned skill might read.
_PC_TOPIC = re.compile(
    r"\b(?:pc|computer|laptop|desktop|screen|monitor|display|window|windows|tab|tabs|browser|edge|chrome|firefox|app|apps|"
    r"program|programs|file|files|folder|folders|document|documents|download|downloads|install\w*|uninstall\w*|update|updates|"
    r"volume|mute|sound|speaker|speakers|headphones|mic|microphone|brightness|battery|wi-?fi|network|internet|bluetooth|"
    r"keyboard|mouse|cursor|clipboard|screenshot|wallpaper|taskbar|start\s+menu|settings|notification|notifications|"
    r"drive|disk|storage|ram|memory|cpu|gpu|graphics|processor|temperature|fan|uptime|ip\s+address|"
    r"music|song|songs|track|playlist|album|video|videos|youtube|spotify|netflix|movie\s+night|stream|game|games|steam|"
    r"blender|unreal|rig|rigging|character|notepad|word|excel|outlook|terminal|powershell|command\s+prompt|git|"
    r"timer|alarm|reminder|remind|calendar|schedule|email|e-?mail|message|text|call|phone|camera|photo|picture|"
    r"weather|forecast|news|headline|headlines|price|prices|stock|score|search|google|look\s+up|website|site|link|url|"
    r"note|notes|list|lists|shopping|remember|forget|save|skill|skills|mission|task|tasks|workflow|guide|"
    r"directions|route|traffic|from\s+here|near\s+me|nearby|translate|calories|pantry|fridge|recipe|recipes|workout|"
    r"budget|outfit|wardrobe|what\s+time|what\s+day|the\s+date|today'?s\s+date)\b", re.I)


def plain_talk(said: str) -> bool:
    """Is this line conversation that the PC planner has nothing to decide about? Narrow on purpose: nothing about the
    machine or what is on it, no media, no errand a skill could run, no order and no request to act. The gateway adds
    its own checks (no app or folder named, no learned skill fits, nothing waiting for his answer)."""
    text = (said or "").replace("’", "'").strip()
    if not text or len(text.split()) > 40:
        return False
    if orders(text) or asks_to_act(text):
        return False
    hits = {m.group(0).casefold() for m in _PC_TOPIC.finditer(text)}
    if hits and not _just_telling(text, hits):
        return False
    return True


# Everyday words that are also skills' words: "I'm going to hang some pictures next." and "It took all weekend." are the
# owner telling something, not asking the PC for a picture, a call or a drive. 11 of the 54 scripted replay lines that
# went to the manual's path (2026-11-13) were of this kind, and its replies were stale, offered tasks or carried on
# about the last answer. Only a plain first-person or third-person statement counts: no question, no need, want,
# request or problem in it, and every PC word in it is one of these.
_EVERYDAY = {"picture", "pictures", "photo", "photos", "call", "calls", "text", "message", "phone", "drive", "game", "games",
             "word", "video", "videos", "movie", "music", "song", "songs", "news", "score", "price", "prices", "weather",
             "schedule", "calendar", "email", "mail", "workout", "recipe", "recipes", "budget", "character", "route",
             "traffic", "stream", "album", "track", "memory", "list", "note", "notes", "update", "updates", "network",
             "internet", "camera", "site", "link", "stock", "task", "tasks", "app", "apps", "file", "files", "document",
             "documents", "mission", "outfit", "wardrobe", "search", "google", "kind"}
_STATEMENT = re.compile(r"^\W*(?:(?:yeah|yes|well|so|ha|oh|and|but|anyway|honestly|okay|ok|right)[,.\s]+)*"
                        r"(?:i|i'm|i've|i'd|i'll|we|we're|we've|my|it|it's|that|that's|this|she|he|they|the|there|there's|"
                        r"last|today|yesterday|tonight)\b", re.I)
_REQUESTING = re.compile(r"\b(?:need|needs|want|wants|wanna|looking|trying|can't|cannot|won't|doesn't|isn't|aren't|wasn't|"
                         r"not\s+working|broken|stuck|frozen|crashed|forgot|forgotten|remind|remember|forget|should|supposed|"
                         r"how|what|where|which|who|why|please|let's|let\s+me|too\s+(?:loud|quiet|dark|bright|slow)|"
                         r"turn|open|close|play|stop|set|put|save|send|show|find)\b", re.I)


_NOT_KNOWING = re.compile(r"\bi\s+(?:don'?t|do\s+not|didn'?t|never)\s+(?:really\s+)?know\s+(?:what|how|where|when|which|who|why)\b", re.I)


def _just_telling(text: str, hits) -> bool:
    # "I don't know what to say when I call her." tells something: the "what" asks nothing of the PC.
    return (bool(hits) and hits <= _EVERYDAY and "?" not in text and bool(_STATEMENT.match(text))
            and not _REQUESTING.search(_NOT_KNOWING.sub("", text)))


# ------------------------------------------------------------------------------------------------------ the clock
# The chat prompt had no clock: "Evening." at eight in the morning (live 2026-11-12). The time of day and the weekday
# go on his newest message for this request only (like the notes above), so the cached persona and the conversation
# before it are byte-for-byte what they were. Tests pin `_now`.
def _now():
    from datetime import datetime
    return datetime.now()


def day_part(now=None) -> str:
    """morning (5 to noon), afternoon (to 5 pm), evening (to 10 pm) or night. Decided in core/daypart.py."""
    return _daypart.day_part(now or _now())


def clock_note(now=None) -> str:
    """The per-request note that tells a chat turn the local weekday and time of day. Only the part of the day: with
    the hour in it the small model recited the time in every other reply (eval 2026-11-12)."""
    now = now or _now()
    part = day_part(now)
    when = f"the small hours of {now.strftime('%A')}" if part == "night" and now.hour < 5 else f"{now.strftime('%A')} {part}"
    greet = ("Greet only if they greeted you, to match it" if part != "night"
             else "Greet only if they greeted you, and then just say hello, naming no time of day")
    return (f"(For reference only: it is {when} where they are. {greet}; do not "
            f"mention the day or the hour unless they ask.)")


_GREETED = re.compile(r"\bgood\s+(morning|afternoon|evening)\b|"
                      r"^(\W*)(morning|afternoon|evening)\b(?=\s*[,.!?]|\s*$)", re.I)


_OPENS_SENTENCE = re.compile(r"(?:^|[.!?][\"')\]]*\s)\W*$")


def _greets(reply: str, m) -> bool:
    """Is this match a greeting, i.e. does it open the reply or a sentence? "Have a good evening" is a wish, not a
    greeting, and a neutral word cannot take its place."""
    return bool(_OPENS_SENTENCE.search(reply[:m.start()]))


def _fits(word: str, now) -> bool:
    return _daypart.greeting_fits(word, now)


def wrong_greeting(reply: str, said: str = "", now=None) -> bool:
    """Does the reply greet with a time of day the clock contradicts ("Good evening" at eight in the morning, or at
    four at night)? A word of his own line said back to him ("Good evening." answered in kind) is his clock, not a
    mistake."""
    now = now or _now()
    his = _flat(said).split()
    night = day_part(now) == "night"
    for m in _GREETED.finditer(reply or ""):
        word = m.group(1) or m.group(3)
        if night and not _greets(reply, m):     # a wish mid-sentence is left alone at night
            continue
        if word.casefold() not in his and not _fits(word, now):
            return True
    return False


def right_greeting(reply: str, said: str = "", now=None) -> str:
    """The reply with a greeting that contradicts the clock set right ("Evening, sir." at 8 am -> "Morning, sir.";
    any time-of-day greeting in the night hours -> "Hello"). Deterministic: the note tells the model the hour, this
    makes sure of the one word that is plainly wrong."""
    now = now or _now()
    his = _flat(said).split()
    night = day_part(now) == "night"
    want = day_part(now)

    def fix(m):
        word = m.group(1) or m.group(3)
        if word.casefold() in his or _fits(word, now):
            return m.group(0)
        whole = m.group(0)
        if night:                       # "Good evening" / "Evening" -> "Hello": a neutral word in the night hours
            if not _greets(m.string, m):    # "have a good evening" is a wish, not a greeting: never "have a hello"
                return whole
            if m.group(3):              # the bare form: keep what leads it
                return whole[:len(whole) - len(word)] + _daypart.neutral_greeting(word[:1].isupper())
            return _daypart.neutral_greeting(whole[:1].isupper())
        new = want.capitalize() if word[:1].isupper() else want
        return whole[:len(whole) - len(word)] + new
    return _GREETED.sub(fix, reply or "")


# --------------------------------------------------------------------------------------------- never the same line twice
# Live 2026-11-12: one stock line came back twice in a row to two different tired remarks, and a bare acknowledgement
# was the whole reply to "How is it going?". A plain chat reply is compared with his last few replies in this
# conversation; one that is (nearly) the same, or that only acknowledges an open question, is written once more with a
# note. The second attempt is kept whatever it is, and if it fails the first one stands: never silence.
RECENT_REPLIES = 4
_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _text(message) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    return " ".join((b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "") or "")
                    for b in (content if isinstance(content, list) else [])
                    if (b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) == "text")


def recent_replies(history, count: int = RECENT_REPLIES) -> list:
    """What Jarvis said in his last few replies of this conversation, newest first."""
    out = []
    for m in reversed([m for m in (history or []) if isinstance(m, dict)]):
        if m.get("role") != "assistant":
            continue
        text = _text(m).strip()
        if text and "[ignore]" not in text.lower():
            out.append(text)
        if len(out) >= count:
            break
    return out


def same_reply(reply: str, earlier: str) -> bool:
    """Is `reply` the same line as `earlier`, or nearly: equal once case and punctuation are gone, almost equal, or
    sharing a whole sentence of three words or more?"""
    a, b = _flat(reply), _flat(earlier)
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a.split()), len(b.split())) < 3:
        return False
    from difflib import SequenceMatcher
    if SequenceMatcher(None, a, b).ratio() >= 0.86:
        return True
    mine = {_flat(s) for s in _SPLIT.split(reply) if len(_flat(s).split()) >= 3}
    return bool(mine & {_flat(s) for s in _SPLIT.split(earlier)})


def repeats(reply: str, history) -> str:
    """The earlier reply of this conversation that `reply` repeats, or ''."""
    return next((r for r in recent_replies(history) if same_reply(reply, r)), "")


# A reply that only acknowledges. Right for a remark or a decline; not an answer to "How is it going?".
_ACKS = ("fair enough", "very good", "very well", "understood", "noted", "indeed", "quite", "quite so", "right",
         "all right", "alright", "okay", "ok", "of course", "certainly", "as you wish", "any time", "anytime", "good",
         "i see", "got it", "duly noted", "point taken")
_OPEN_QUESTION = re.compile(r"^\W*(?:(?:hey|hi|hello|so|and|well|ok|okay|but|jarvis|now|then)[,\s]+)*"
                            r"(?:how|what|why|where|who|which|when)\b", re.I)


def open_question(said: str) -> bool:
    """A how / what / why question: one that a bare "Fair enough." cannot answer."""
    return bool(_OPEN_QUESTION.match((said or "").replace("’", "'")))


def only_acknowledges(reply: str) -> bool:
    words = _flat(reply).split()
    return any(words[:len(a.split())] == a.split() and len(words) - len(a.split()) <= 1 for a in _ACKS)


# Review 2026-11-12: a repeat is not always stale. "Yes." is the right reply to the next yes-or-no question too, the
# same question earns the same answer, and "say that again" asks for the very line. When the repeat may fit his line
# it is left alone: writing it again "differently" could turn a yes into something else.
_ASKS_AGAIN = re.compile(
    r"\b(?:(?:say|repeat|tell\s+me|run)\s+(?:that|it|this)(?:\s+\w+){0,2}\s+(?:again|once\s+more|one\s+more\s+time)|"
    r"say\s+again|come\s+again|repeat\s+(?:that|it|yourself|what\s+you)|(?:once|one)\s+more(?:\s+time)?\W*$|"
    r"what\s+(?:did|do)\s+you\s+(?:just\s+)?(?:say|mean)|what\s+was\s+that|"
    r"(?:didn'?t|did\s+not|couldn'?t|could\s+not)\s+(?:quite\s+)?(?:catch|hear|get)|(?:beg\s+your\s+)?pardon|"
    r"missed\s+that)\b|^\W*(?:sorry|what|huh|again|eh)\W*$", re.I)
_CLOSED_QUESTION = re.compile(
    r"^\W*(?:(?:hey|hi|hello|so|and|well|ok|okay|but|jarvis|now|then|wait|sorry)[,\s]+)*"
    r"(?:is|are|was|were|am|do|does|did|can|could|will|would|should|shall|have|has|had|may|might|must|"
    r"isn'?t|aren'?t|wasn'?t|weren'?t|don'?t|doesn'?t|didn'?t|can'?t|couldn'?t|won'?t|wouldn'?t|shouldn'?t|"
    r"haven'?t|hasn'?t)\b"
    r"|,?\s*(?:right|correct|yes|no|is\s+it|isn'?t\s+it|are\s+you|aren'?t\s+you|do\s+you|don'?t\s+you|did\s+i|"
    r"didn'?t\s+i|does\s+it|doesn'?t\s+it|is\s+that\s+(?:right|so|it))\s*\?+\W*$", re.I)
SHORT_ANSWER_WORDS = 4


def _his_line_before(said: str, history) -> str:
    """His previous line in this conversation (the history may or may not end with `said`)."""
    lines = [_text(m) for m in (history or []) if isinstance(m, dict) and m.get("role") == "user"]
    lines = [line for line in lines if _flat(line)]
    if lines and _flat(lines[-1]) == _flat(said):
        last = [m for m in history if isinstance(m, dict)][-1]
        if last.get("role") == "user":
            lines = lines[:-1]
    return lines[-1] if lines else ""


def _may_repeat(reply: str, said: str, history) -> bool:
    """May this repeated reply be the right one for his line? He asked to hear it again, he asked the same thing
    again, or it is a short answer to a yes-or-no question."""
    line = (said or "").replace("\u2019", "'")
    if not _flat(line):
        return True                      # nothing to judge it against: unsure, so it is left alone
    if _ASKS_AGAIN.search(line):
        return True
    if _flat(line) == _flat(_his_line_before(said, history)):
        return True
    return len(_flat(reply).split()) <= SHORT_ANSWER_WORDS and bool(_CLOSED_QUESTION.search(line))


def stale(reply: str, said: str, history) -> tuple:
    """(why, the earlier line) when a plain chat reply should be written once more: ('repeat', what it repeats),
    ('unanswered', '') when his open question got only an acknowledgement, or ('', '')."""
    if not _flat(reply) or "[ignore]" in (reply or "").lower():
        return "", ""
    again = repeats(reply, history)
    if again and (restyle_target(said, history) == "answer" or not _may_repeat(reply, said, history)):
        return "repeat", again
    if open_question(said) and only_acknowledges(reply):
        return "unanswered", ""
    return "", ""


def again_note(why: str, reply: str) -> str:
    """The note for the one more try."""
    line = " ".join((reply or "").split())[:160].replace('"', "'")
    if why == "unanswered":
        return (f"(They asked you something, and \"{line}\" does not answer it. Answer the question itself in a "
                f"sentence, about yourself if it is about you.)")
    return (f"(You already said \"{line}\" a moment ago in this conversation. Reply to their newest line in other "
            f"words, just as briefly. A fact, a yes or a no stays what it is: only the wording changes.)")


SUBJECT_WAIT = 10                               # words a chained restyle's stream waits for a word of its subject


class Hold:
    """What a chat reply's stream passes through on its way to speech. The first words are kept back while the reply
    could still turn out to be one that is written again (the start of one of his recent replies, or of a bare
    acknowledgement to an open question), and until a greeting can be checked against the clock. Whatever is never
    released here is spoken from the finished reply by the caller, so nothing is lost and nothing is said twice."""

    def __init__(self, emit, history=(), said: str = "", plain: bool = True, now=None, subject=None,
                 restyle: bool = False, cap=None):
        self.emit, self.said, self.now = emit, said, now
        self.cap = cap                          # a restyle reply's word cap (length_cap): later sentences are released whole
        self.out, self.tail, self.first_done, self.stopped = "", "", False, False
        self.restyle = restyle                  # a restyle reply: a commentary opener is never released
        self.subject = subject                  # a chained restyle's subject words: the stream waits for the first one,
        #                                         at most SUBJECT_WAIT words (subject_check)
        self.buffer, self.open, self.spoke = "", False, False
        recent = recent_replies(history) if plain else []
        self.suspects = [_flat(r) for r in recent]
        # A first sentence of under three words is no repeat by itself (same_reply): only the whole short reply is,
        # and `suspects` covers that. Held as an opening it would keep back every answer that begins "Indeed, ...".
        self.firsts = [f for f in (_flat(_SPLIT.split(r.strip())[0]) for r in recent if len(r.split()) <= 14)
                       if len(f.split()) >= 3]
        self.acks = list(_ACKS) if plain and open_question(said) else []

    def _may_be_stale(self, flat: str) -> bool:
        if any(s.startswith(flat) for s in self.suspects + self.acks):
            return True
        # An acknowledgement and at most one word more (only_acknowledges): "Right, so sunlight ..." is an answer
        # and goes out on its third word.
        if self.acks and only_acknowledges(flat):
            return True
        return any(flat == f or flat.startswith(f + " ") for f in self.firsts)

    def _release(self, text: str) -> None:
        """Say `text`; with a word cap the first sentence goes out as it is and the later ones only when the whole of
        each still fits (fit_length keeps the same ones in the finished reply)."""
        if not self.cap:
            self.emit(text)
            return
        if not self.first_done:
            end = _sentence_end(text)
            if not end:
                self.out += text
                self.emit(text)
                return
            self.first_done = True
            self.out += text[:end]
            self.emit(text[:end])
            text = text[end:]
        self.tail += text
        while not self.stopped:
            end = _sentence_end(self.tail, _SENTENCE_STREAM)
            if not end:
                return
            sentence, self.tail = self.tail[:end].strip(), self.tail[end:]
            if len(self.out.split()) + len(sentence.split()) > self.cap:
                self.stopped = True
                return
            self.out += " " + sentence
            self.emit(" " + sentence)

    def feed(self, piece: str) -> None:
        if self.open:
            self._release(piece)
            return
        self.buffer += piece
        flat = _flat(self.buffer)
        if self._may_be_stale(flat):
            return
        words = len(flat.split())
        text = self.buffer
        if self.restyle:
            if words < 5:
                return
            # Commentary about the request or earlier replies (and an echoed style word) is judged before a word is
            # said: dropped, what follows it said. A sentence that is still being written and opens like commentary
            # waits for its end, at most OPENER_HOLD_WORDS words.
            text, pending = _drop_openers(self.buffer, self.said, False)
            if (pending and words < OPENER_HOLD_WORDS) or not text.strip():
                return
            # the cleaning strips the ends: the spaces the pieces came with go back so the next piece is not glued on
            text = text.rstrip() + self.buffer[len(self.buffer.rstrip()):]
        if self.subject and not self.subject & subject_words(text) and words < SUBJECT_WAIT:
            return                       # not about the subject yet: said at once when a word of it comes, or after a few words
        if len(text.split()) < 3 and not re.search(r"[.!?]\s+\S", text):
            return                       # the opening word or two: a greeting is set right before it is said
        self.open = self.spoke = True
        self._release(right_greeting(text, self.said, self.now))


# ---------------------------------------------------------------------------------------- a bare yes / no / "I don't see it"
# Master conversationalist (2026-11-15). Live 2026-11-14: "I don't see it." -> "Would you like me to open it now?" -> "Yes." was
# answered "I don't see a Dashboard on your screen right now. Would you like me to open it?", the same question again; "I don't
# see it." after "The Command Center is up on your screen" was answered as if about a new topic. A bare yes, no or sightless
# complaint is an answer to what was JUST said, so the one request says so, with the words it answers.
# What a yes is (review 2026-11-15: 'Not now.', 'Thank you.', 'Why not?', 'Sir.' and 'Really?' were all read as yes and re-ran his
# old request): an affirmation word, and only filler around it, and no negation anywhere. Thanks, names and 'really' are no yes.
_AFFIRM = re.compile(r"\b(?:yes|yeah|yep|yup|sure|ok|okay|alright|all right|correct|absolutely|definitely|certainly|of course|"
                     r"please do|go ahead|do it|do that|sounds good|i would like that|i'?d like that|that would be great|"
                     r"that'?d be great|make it so)\b", re.I)
_AFFIRM_FILLER = re.compile(
    r"^(?:please|sir|jarvis|very much|i would like that|i'?d like that|that would be great|that'?d be great|do it|do that|go ahead|"
    r"now|then|so|and|for me|thanks|thank you|right|"
    r"(?:open|show|launch|start|bring up|pull up)\s+(?:it|that|them)(?:\s+(?:up|now|again))?)$", re.I)
_NEGATION = re.compile(r"\b(?:not|no|never|nope|nah|don'?t|do not|dont|without|stop|cancel|wait|hold on|hold|hang on|later|except|but|"
                       r"can'?t|cannot|won'?t|shouldn'?t|wouldn'?t|isn'?t|aren'?t|wasn'?t|didn'?t|doesn'?t|let'?s not)\b|n't\b", re.I)
_REFUSAL = re.compile(r"^\W*(?:(?:ok|okay|yeah|sure|well|no|oh|hmm)[,.\s]+)*(?:(?:please\s+)?(?:do\s+not|don'?t|never)(?:\s+do\s+(?:it|that))?|"
                      r"(?:absolutely|definitely|certainly|of\s+course|really|actually)\s+not|not\s+(?:now|today|yet|right\s+now|anymore|really|"
                      r"tonight|this\s+time)|let'?s\s+not|no\s+way|nope)(?:[,.\s]+(?:thanks|thank\s+you|sir|jarvis|please))*\W*$", re.I)
_CHANNEL_PREFIX = re.compile(r"^\s*\[[^\]]*\]\s*")
_OFFER_DECL = re.compile(r"\b(?:i|we)\s+(?:can|could)\b[^.!?]*\b(?:for you|if you(?:'d| would)? like|if you want)\b|"
                         r"\bif you(?:'d| would)? like,?\s+i\s+(?:can|could)\b|"
                         r"\bif you (?:want|would like|'d like)\b(?![^.!?]*\b(?:can'?t|cannot|won'?t|stop|leave|quit|delete|remove|erase|format)\b)"
                         r"[^.!?]*\b(?:say so|just say|let me know|tell me|ask)\b[^.!?]*\bi(?:'ll| will| can)\b", re.I)
# He says what Jarvis said happened did not: "I don't see it.", "It's not there.", "Nothing happened.", "It didn't open."
_NOT_SEEN = re.compile(
    r"^\W*(?:(?:but|well|no|hey|jarvis|okay|ok)[,\s]+)*(?:i\s+(?:still\s+|really\s+)?(?:don'?t|can'?t|do\s+not|cannot)\s+see\s+(?:it|that|anything|"
    r"the\s+\w+|a\s+\w+)|(?:it|that)(?:'?s|\s+is)\s+not\s+(?:there|here|showing|up|open|visible|on\s+(?:the|my)\s+screen)|"
    r"nothing\s+(?:happened|is\s+happening|came\s+up|opened|changed|there)|(?:it|that)\s+(?:didn'?t|did\s+not|isn'?t|is\s+not|"
    r"hasn'?t|has\s+not)\s+(?:work|open|show|appear|come\s+up|happen|worked|opened|appeared|showing)\w*)\W*$", re.I)


# A closing question that offers nothing a yes could accept ("Anything else?", "How can I help?"): unlike "Want me to open it?"
# (intelligence.generic_offer counts both as closing offers; a yes to the second one is an instruction).
_VACUOUS = re.compile(r"\banything\b|\bhow (?:can|may) i help\b|\bwhat (?:would|do|else) you (?:like|need|want)\b|"
                      r"\bwhat can i (?:do|help)\b|\bneed (?:a hand|help)\b|\bhow about you\b|\bhow'?s your\b|"
                      r"\bcould you (?:say|tell me) (?:a (?:little|bit) )?more\b", re.I)


# Not a yes/no question: his "Yes." / "Let's go." answers nothing there ("What exactly are you doing in Blender?"), and the wake
# answer "Yes, sir?" asks him to speak, it offers nothing.
_WH = re.compile(r"^\W*(?:(?:and|so|but|okay|ok|well)[,\s]+)*(?:what|which|who|whom|whose|where|when|why|how)\b(?!\s+about\b)", re.I)
_WAKE_ANSWER = re.compile(r"^\W*(?:yes|yeah|hello|hi|sir|jarvis)(?:[,\s]+(?:sir|jarvis|there))?\W*$", re.I)


def _sentences(text: str) -> list:
    return [s.strip() for s in _SPLIT.split(" ".join(str(text or "").split())) if s.strip()]


def _plain(said: str) -> str:
    """His words without the channel prefix ('[Spoken into the laptop] Yes.'), curly quotes straightened."""
    return _CHANNEL_PREFIX.sub("", str(said or "")).replace("\u2019", "'").strip()


def declines(said: str) -> bool:
    """A no in any short wording: 'No.', 'Not now.', 'Absolutely not.', 'Do not.', "Let's not." (never a question: 'Why not?')."""
    from core import turnstate
    text = _plain(said)
    if not text or text.endswith("?"):
        return False
    return turnstate.bare_no(text) or bool(_REFUSAL.match(text))


def bare_yes(said: str) -> bool:
    """The whole line is a yes: an affirmation and filler only, no negation, no question ("Yes.", "Yes, please.", "Yeah, open
    it.", "Yes, I would like that very much."). 'Not now.', 'Thank you.', 'Why not?', 'Really?', 'Sir.' are not."""
    text = _plain(said)
    if not text or len(text.split()) > 10 or text.endswith("?") or _NEGATION.search(text):
        return False
    words = re.findall(r"[a-z']+", text.casefold())
    if re.search(r"\byeah,?\s+right\b", text, re.I) or any(a == b for a, b in zip(words, words[1:])):
        return False                                  # "Yeah right.", "Yeah yeah.", "Sure sure.": a dismissal, not a yes
    rest = _AFFIRM.sub(" ", text)
    if rest == text:
        return False                                  # no affirmation word at all
    pieces = [p.strip() for p in re.split(r"[,.;!]+|\s{2,}", rest) if p.strip()]
    return all(_AFFIRM_FILLER.match(re.sub(r"\s+", " ", p)) for p in pieces) if pieces else True


def _offer_in(reply: str) -> str:
    """What the reply offered or asked at its end: its last sentence when that is a real question, or a declarative offer
    ("If you'd like, I can show you the headlines."), else ''."""
    parts = _sentences(reply)
    if not parts:
        return ""
    last = parts[-1]
    if last.endswith("?"):
        flat = last.replace("’", "'")
        return "" if _VACUOUS.search(flat) or _WH.match(flat) or _WAKE_ANSWER.match(flat.rstrip("?")) else last
    return last if _OFFER_DECL.search(last.replace("\u2019", "'")) else ""


def _newest(history) -> str:
    from core import intelligence
    return _plain(intelligence.last_user_text(history))


def binding(history):
    """('yes' | 'no' | 'unseen', what Jarvis offered or claimed, the line of his that started that exchange) when his newest
    line is a bare yes, a bare no, or a complaint that what was just done is not there; else None."""
    from core import intelligence, turnstate
    said = _newest(history)
    if not said or len(history or []) < 2 or history[-1].get("role") != "user":
        return None
    line, reply = None, _before(history, said)[0].strip()
    if not reply:
        return None
    # his own line that began the exchange the reply answered (not the one he is saying now)
    from core import speech
    from core import turnstate as _ts
    pairs = [p for p in speech.exchanges(history[:-1]) if p[1]][-4:]
    # what he asked for, not his complaints or answers in between ("I don't see it.", "Okay.")
    line = next((q for q, _r in reversed(pairs)
                 if not (_NOT_SEEN.match(q.replace("’", "'")) or bare_yes(q) or declines(q) or acknowledgement(q))), "")
    offer = _offer_in(reply)
    if offer and declines(said):                       # a no is looked at FIRST: reading a refusal as a yes is the dangerous mistake
        return ("no", offer, line)
    if offer and bare_yes(said):
        return ("yes", offer, line)
    if not reply.rstrip().endswith("?") and _NOT_SEEN.match(said.replace("\u2019", "'")):
        return ("unseen", _sentences(reply)[-1], line)
    return None


# He heard the warning and decided (scripts/pushback_eval.py "decide": "I know, do it anyway. It's my drive.", "I'll manage, I have my
# antihistamines.", "It'll be fine, it's only for an hour. Moving on."): 3-4 of 6 replies warned AGAIN (2026-11-21, 7 runs). The owner's
# rule (2026-11-15): one warning, his final say. A per-turn note at the END of the prompt (the cached prefix stays) says so.
_DECIDES = re.compile(r"\b(?:(?:do|doing|send|make|write|run|give me) (?:it|that|them)(?: \w+)? anyway|i'?ll (?:\w+ )?(?:do|take|risk) it anyway|"
                      r"regardless|my mind'?s made up|i(?:'ve| have) decided|i know(?: the risks?)?[,.]|"
                      r"it'?s my (?:pc|computer|drive|call|choice|decision|money|life|body|kitchen|house|report)|"
                      r"i don'?t care|i'?ll manage|i'?ll be fine|it'?ll be fine|i hear you,? but|just (?:do|give|write|send|run) it\b|"
                      r"just give me|i'?m doing it|i'?m going to anyway|that'?s my call|my call)\b", re.I)
_WARNED = re.compile(r"\b(?:erase|wipe|lose|risk|danger|careful|not safe|unsafe|can'?t be undone|no undo|allerg\w*|overload|fire|hazard|"
                     r"exposed|malware|not the same|isn'?t included|doesn'?t cover|"
                     r"i'?d (?:advise|suggest|recommend) against|over (?:your|the) budget|exceeds?|back (?:it|them) up)\b", re.I)
# A correction of a fact ("Actually, Canberra is the capital"): his "just say it" never makes a falsehood true, so no comply note
_CORRECTED = re.compile(r"\b(?:actually|in fact|that'?s not (?:right|true|correct)|isn'?t (?:right|true|correct)|aren'?t|it'?s not)\b", re.I)


def decided_note(history) -> str:
    """The one-request note when his line decides after Jarvis's last reply warned him; '' otherwise."""
    try:
        said = last_user_text_safe(history)
        if not said or "?" in said or not _DECIDES.search(said.replace("’", "'")):
            return ""
        # the reply before HIS message (the round after a tool keeps the same note: the cached prompt stays the same)
        mine = max((i for i, m in enumerate(history) if m.get("role") == "user" and isinstance(m.get("content"), str)), default=-1)
        last = next((m.get("content") for m in reversed(history[:max(mine, 0)]) if m.get("role") == "assistant"
                     and isinstance(m.get("content"), str) and m.get("content").strip()), "")
        if not last or not _WARNED.search(last.replace("’", "'")) or _CORRECTED.search(last.replace("’", "'")):
            return ""
    except Exception:  # noqa: BLE001 - a note is only help
        return ""
    return ("(They heard your warning and decided: it is their call. Do not warn again or argue. Do what they asked, or if you cannot or "
            "will not, say that once in one sentence.)")


def last_user_text_safe(history) -> str:
    from core import intelligence
    return intelligence.last_user_text(history) or ""


def binding_note(history) -> str:
    """The note for this one request (never kept in the history) that binds his short answer to what it answers; '' when it
    answers nothing in particular."""
    try:
        found = binding(history)
    except Exception:  # noqa: BLE001 - a note is only help: its absence leaves the request as it was
        return ""
    if not found:
        return ""
    kind, said, line = found
    said = said.replace('"', "'")[:240]
    context = f" What they had asked for before that: \"{line.replace(chr(34), chr(39))[:160]}\"." if line.strip() else ""
    if kind == "yes":
        return (f"(Their line is a yes to what you just said: \"{said}\".{context} Do that now, or say in one sentence what "
                f"stops you. Do not ask it again, and do not answer anything else.)")
    if kind == "no":
        return (f"(Their line is a no to what you just said: \"{said}\".{context} Drop it: one short acknowledgement, no new "
                f"offer, no question.)")
    return (f"(Their line says what you just reported is not there: \"{said}\".{context} Believe them: do not say it again as "
            f"if it were done, and do not treat it as a new topic. Say what you will try instead, or what you cannot do, in "
            f"one or two sentences.)")


# His yes to "Would you like me to open it now?" is the request he already made ("Open Dashboard."), said again. Live 2026-11-14
# the model answered that yes with the same question (and, when told to act, announced "Opening the Command Center now" without
# a tool, 3 of 3 on qwen3.5:9b: the polisher refuses such a claim): the PC path that opened it the first time does it again.
# Only an offer to open, show or start something he asked for in those words himself; closing, deleting, sending and installing
# keep their own review (core.approvals, the PC plan ledger).
_REOPEN_OFFER = re.compile(r"^\W*(?:(?:and|so|okay|ok|well)[,\s]+)*(?:would you like me to|do you want me to|want me to|shall i|should i|"
                           r"i can|i could)\s+(?:try\s+to\s+|just\s+)?(?:open|launch|start|show|bring\s+up|pull\s+up|bring)\s+(?P<obj>[^?.!]*?)\W*$", re.I)
# His own earlier request, ONE simple open/show/start of one thing: no 'and', no click / type / delete / send / install / close.
_REOPEN_ASKED = re.compile(r"^\W*(?:(?:hey\s+)?jarvis[,\s]+)?(?:(?:please|just|can\s+you|could\s+you|i\s+need\s+you\s+to)\s+)*"
                           r"(?:open|launch|start|show|bring\s+up|pull\s+up|bring)\s+(?P<obj>[^?.!]*?)\W*$", re.I)
_RISKY = re.compile(r"\b(?:and|then|also|plus|click\w*|press\w*|type\w*|typing|delet\w*|remov\w*|erase\w*|send\w*|install\w*|uninstall\w*|"
                    r"clos\w*|save\w*|buy\w*|pay\w*|purchas\w*|password\w*|format\w*|how|why|what|me\s+how)\b", re.I)
_OBJ_FILLER = frozenset("the a an my your our this that these those it them too also now again up page window app for you me please "
                        "there here".split())


def _object_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").casefold()) if w not in _OBJ_FILLER}


def _same_thing(offer_obj: str, asked_obj: str) -> bool:
    """The offer is about the thing he asked for: it says 'it' / 'that', or shares a word of its name ('open the Dashboard page'
    for 'Open Dashboard.'); 'the Command Center too' is another thing."""
    mine = _object_words(offer_obj)
    if not mine:
        return True                                    # "open it now": it, that, them
    return bool(mine & _object_words(asked_obj)) and mine <= _object_words(asked_obj) | {"page", "window", "app"}


_WEB_OFFER = re.compile(r"\b(?:i|we)\s+(?:can|could)\s+(?:also\s+)?(?:show|pull\s+up|search|look|find|read|get|check|bring\s+up|dig)\b|"
                        r"^\W*(?:would you like me to|do you want me to|want me to|shall i|should i)\s+(?:show|pull\s+up|search|look|find|read|"
                        r"get|check|bring\s+up|dig)\b|"
                        r"\bsay so and i(?:'ll| will)\s+(?:pull|show|search|look|find|read|get|check|bring)\b", re.I)


YES_MAX_AGE = 120                      # seconds: his yes to an older offer is not read as that offer's yes (brain.llm)
_WEB_GENERIC = frozenset("actual real top latest best cheapest nearest newest more most full rest details detail information info results "
                         "result sources source trusted reliable headlines headline stories story news articles article links link summary "
                         "and or of in on at to from with about what whats is are was really being reported see can could "
                         "official officially advice guidance says say said statement statements area local region".split())


def yes_reissues(history, web: bool = False) -> str:
    """The earlier request of his that his bare yes says again ('Open Dashboard.'), or '' when it is no such yes. Only when that
    request is the one he made just before (acknowledgements between are skipped, another request is not), is ONE simple
    open / show / start, and the offer is about the same thing ('open it', 'open the Dashboard'). Anything else is not run
    again: the question is answered like any line. `web`: for a request for the internet (core.webintent) instead of the PC."""
    try:
        found = binding(history)
    except Exception:  # noqa: BLE001
        return ""
    if not found or found[0] != "yes":
        return ""
    _kind, offer, line = found
    offer = offer.replace("\u2019", "'")
    line = _plain(line)
    # _RISKY is the PC path's list (and / what / how / delete ...): a search he asked for is no click or delete, and "tell me what the
    # news says" or "What is going on?" is exactly the line his yes is about. The web path is judged by webintent below.
    if not line or (not web and (_RISKY.search(line) or "?" in line)):
        return ""
    if web:
        try:
            from core import webintent
            m = _WEB_OFFER.search(offer)
            if m and webintent.kind(line) in (None, "learn", "recall") and re.search(r"(?i)headlines?|news|reports?|sources", offer):
                # "People are saying X. What is going on?" -> "If you want the real headlines, say so": the yes is the news search
                # about X (the topic passed every privacy check in webintent.lookup_query), never the bare line again.
                about = webintent.lookup_query(line)
                about = re.sub(r"(?i)\s+news$", "", about).strip()
                return f"What is the news about {about}?" if about and webintent.kind(f"What is the news about {about}?") == "news" else ""
            if not m or webintent.kind(line) in (None, "learn", "recall"):
                return ""
            # the offer must be about what he searched for: the words of its topic (generic ones aside) are words of his search
            topic = _object_words(offer[m.end():]) - _WEB_GENERIC
            return line if topic <= _object_words(line) | {"too"} else ""
        except Exception:  # noqa: BLE001
            return ""
    asked, said = _REOPEN_ASKED.match(line), _REOPEN_OFFER.match(offer)
    if not asked or not said or _RISKY.search(said.group("obj")) or not _same_thing(said.group("obj"), asked.group("obj")):
        return ""
    return line


# "Sure, go ahead." with nothing to go ahead with: the 9B said "I have your authorization, sir." (live 2026-11-16, the laptop). A go-ahead is
# only a yes to something: an offer, a question, a plan waiting for approval. With none, ask what he means, in code.
_GO_AHEAD = re.compile(r"^\W*(?:(?:yes|yeah|yep|sure|okay|ok|alright|all right|please)[,.\s]+)*(?:go ahead|go for it|do it|proceed|carry on|make it so)"
                       r"(?:[,\s]+(?:please|then|now|jarvis|sir))*\W*$", re.I)


_ANY_OFFER = re.compile(r"\b(?:if you(?:'d)? (?:want|like|wish|prefer)|i (?:can|could|will|'ll)\b|shall i|should i|would you like|want me to|"
                        r"do you want|let me know|say the word|just say|tell me)\b", re.I)


def nothing_to_go_ahead_with(text: str, history, title: str = "sir") -> str | None:
    """The line to say when his whole line is a go-ahead and nothing Jarvis said, offered or holds waits for it; None otherwise (the
    normal path judges a go-ahead that answers something)."""
    plain = re.sub(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", str(text or "")).strip()
    if not _GO_AHEAD.match(plain):
        return None
    try:
        reply, _tooled = _before(list(history or []), plain)
        if reply.strip() and (reply.rstrip().endswith("?") or _offer_in(reply) or _ANY_OFFER.search(reply.replace("’", "'"))):
            return None                              # it answers a question or an offer
        from core import approvals, pcledger, users
        if approvals.pending(users.current()) or next(iter(pcledger.pending()), None):
            return None                              # a plan or an approval is really waiting
    except Exception:  # noqa: BLE001 - when unsure, the normal path judges it
        return None
    return f"Go ahead with what, {title}? I don't have anything waiting."


# "Why can't you work on the one I have open right now?" -> "write access is blocked until you explicitly grant permission... security rules stop
# me" (live 2026-11-16, his own line, both machines): a reason made up on the spot. With no real cause in what Jarvis just said, he says he
# does not know, in code. When his previous reply DID give a cause ("it failed because...", "I couldn't find..."), the model explains it.
_WHY_CANT = re.compile(r"^(?:[^?]{0,90}?[,.;]\s*)?\W*(?:(?:then|so|well|okay|ok|and|but|hey jarvis|jarvis)[,.\s]+)*why\s+(?:(?:can'?t|cannot|can not|won'?t|don'?t|didn'?t|couldn'?t|aren'?t|isn'?t|doesn'?t)"
                       r"\s+(?:you|it|that|this)|(?:did|do|does|would)\s+you\s+(?:keep|say|do|answer|use|pick|choose|go|mention|give|think|call|open|start|stop|ask|tell))\b[^?]*\?*\W*$", re.I)
_WHY_DID = re.compile(r"\bwhy\s+(?:did|do|does|would)\s+you\b", re.I)
_CAUSE_GIVEN = re.compile(r"\b(?:because|failed|couldn'?t|could not|can'?t|cannot|unable|blocked|refused|denied|no tool|not enough|ran out|timed out|"
                          r"isn'?t (?:available|installed|connected)|not (?:available|installed|connected))\b", re.I)


_CANT_REACH = re.compile(r"^\W*(?:(?:so|well|okay|ok|and|but|hey jarvis|jarvis)[,.\s]+)*(?:how come\s+(?:i|we)\s+(?:can'?t|cannot|won'?t|don'?t)|why\s+(?:can'?t|cannot|won'?t|don'?t)\s+(?:i|we))\s+"
                         r"(?:access|reach|connect(?: to)?|talk to|use|get to|open|call)\s+(?:you|jarvis|the app|the phone app|it)\b[^?]*\?*\W*$", re.I)


def why_cant_you_unknown(text: str, history, title: str = "sir") -> str | None:
    plain = re.sub(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", str(text or "")).strip()
    if _CANT_REACH.match(plain):
        # "How come I can't access you when I'm away from home?" -> "my connection to your home network is cut off when you leave" (live 2026-11-16): not known.
        return (f"I'm not sure why, {title}, and I won't guess. The phone app reaches me through the laptop server, so if it won't connect away from "
                f"home, tell me exactly what the app says and I'll look.")
    if not _WHY_CANT.match(plain):
        return None
    try:
        reply, _tooled = _before(list(history or []), plain)
    except Exception:  # noqa: BLE001
        return None
    if reply.strip() and _CAUSE_GIVEN.search(reply.replace("’", "'")):
        return None                                  # he gave a real cause a moment ago: the model explains that one
    if _WHY_DID.search(plain):
        # "So you know it's not morning, then why did you say good morning?" -> "because you asked about it..." (an invented reason, 2026-11-16)
        return f"I don't have a reason for that, {title}, and I won't make one up. If it was wrong, tell me and I'll do better."
    return (f"I'm not sure why, {title}, and I won't make up a reason. Tell me exactly what you want done and I'll try it, "
            f"or tell you plainly what stopped me.")
