"""Follow-through: an order he gave that Jarvis did not carry out is owned and done, never answered with filler.

Live 2026-11-20 23:48 (data/jarvis.log): "And Streaming Protocol." -> "Got it, sir." (nothing ran); he said it again,
then "And streaming protocol. That's what I was telling you. ... You're not here for shit." -> "I'm here and listening,
sir: tell me what you need done." The streaming protocol never started. Two deterministic pieces, no model:

- `handle(raw)` (core.quick, before the protocols): the exchange JUST before this one ordered a protocol, its reply did
  nothing ("Got it, sir.", or cut before a word), and it has not run since; his line orders it again or is wholly a
  frustrated line aimed at Jarvis ("That's what I was telling you!"). On the PC Jarvis says "Sorry, sir: I didn't start
  the streaming protocol when you asked." and runs it ("run the streaming protocol", through protocols.start and its
  rules). The laptop never retries (it cannot know what the PC ran) and says so.
- `carry(previous, new, ...)` (main.py's voice loop): his multi-sentence message cut into fragments (each new sentence
  cut off the reply to the one before before a word of it was heard, or within its first seconds: `yielded`) is answered
  ONCE, as one turn, unless the earlier fragment was an order that may already have run.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

WINDOW_S = 5 * 60           # the order he repeats was asked at most this long ago
RAN_SLACK_S = 90            # a run that began up to this long before its exchange was written answered it (rows are written at turn end)

# His WHOLE line is frustration aimed at Jarvis (review 2026-11-21: a search anywhere in the line retried his protocol on "I said
# hi to Maya", "nothing happened at school today", "how many times has Seth called", "You didn't do it, Seth", ...). Every
# sentence must be one of these (fullmatch) or filler / words aimed at Jarvis (core.protocols._TAIL), and one must be these.
_YOU = r"(?:jarvis[,\s]+|bro[,\s]+|man[,\s]+)?"
_TO_HIM = r"(?:[,\s]+(?:jarvis|sir|bro|man|dude))?"
_FRUSTRATION = re.compile(
    rf"^\W*{_YOU}(?:that'?s what i (?:was|am|'m) (?:telling|saying|asking)(?: you)?(?: to do)?|"
    r"i (?:just )?told you(?: to do (?:it|that))?|"
    r"(?:you(?:'re| are) not|you aren'?t) (?:listening|understanding)(?: (?:to )?me)?|"
    r"(?:are you|you) (?:even )?(?:listening|hearing me)(?: to me)?|did you (?:not )?hear me|"
    r"(?:why )?(?:didn'?t|don'?t|won'?t) you (?:do|start|run) (?:it|that|what i (?:said|asked(?: you)?|told you))|"
    r"you (?:didn'?t|did not|never) (?:do|start|run) (?:it|that|what i (?:said|asked(?: you)?|told you))|"
    r"(?:it|that) (?:didn'?t|did not|never) (?:start|run|happen)|nothing happened|nothing (?:started|ran)|"
    r"do it(?: (?:now|already|then))?|how many times do i have to (?:say|tell you)(?: (?:it|this|that))?)"
    + _TO_HIM + r"\W*$", re.I)
# Jarvis's own voice heard back as his ("YOU: Yes, sir." at 23:48:22): the owner never calls Jarvis "sir". Skipped when finding
# the exchange before this one; nothing else is.
_ECHO = re.compile(r"^\W*(?:(?:yes|yeah|very good|got it|understood|of course|certainly|right)[,\s]+)?sir\W*$", re.I)
# A reply that did nothing: cut before a word, or an acknowledgment with nothing behind it ("Got it, sir.", "Understood, sir:
# protocol engaged."). A refusal or a report ("...run for the PC's owner only.", "Streaming protocol: 6 of 7 steps done")
# is an answer, and nothing is owed for it (review 2026-11-21).
_ACK = re.compile(r"^\W*(?:got it|understood|very good|of course|certainly|right away|right|okay|ok|sure|yes|absolutely|noted|"
                  r"i hear you|i'?m here|i am here|ready|standing by|on it)\b", re.I)
_ANSWERED = re.compile(r"\b(?:can'?t|cannot|only|don'?t have|isn'?t|not|unable|steps? done|already running|didn'?t get an answer|"
                       r"i don'?t|couldn'?t|failed|sorry)\b|\bprotocols?:", re.I)


def frustrated(text: str) -> bool:
    """His whole line is frustration aimed at Jarvis ("That's what I was telling you!", "You didn't do it, Jarvis.")."""
    from core import protocols
    text = (text or "").replace("’", "'").strip()
    parts = [p for p in re.split(r"(?<=[.!?;])\s+", text) if p.strip()]
    if not parts or len(text.split()) > 30:
        return False
    hits = [bool(_FRUSTRATION.match(p)) for p in parts]
    return any(hits) and all(h or protocols._TAIL.match(p) for h, p in zip(hits, parts))


def order_in(text: str) -> dict | None:
    """One of his protocols ordered in these words (a whole line or one sentence of several); never runs it."""
    from core import protocols
    raw = re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", (text or "").strip(), flags=re.I)
    raw = re.sub(r"^\W*(?:hey\s+)?jarvis[,.!\s]+", "", raw, flags=re.I).strip()
    if "protocol" not in raw.lower():
        return None
    hit = protocols.match_run(raw)
    if hit is not None:
        return hit[0]
    return protocols.run_in_sentences(raw)


def _ran_since(name: str, ts: float) -> bool:
    from core import protocols
    entry = protocols.last_run(name)
    if not entry:
        return False
    try:
        started = datetime.fromisoformat(str(entry.get("started"))).timestamp()
    except (TypeError, ValueError):
        return True                          # unknown: never re-run on a guess
    return started >= ts - RAN_SLACK_S


def did_nothing(reply: str) -> bool:
    reply = (reply or "").replace("’", "'").strip()
    return not reply or (bool(_ACK.match(reply)) and not _ANSWERED.search(reply))


def unfulfilled(uid: str, name: str | None = None, now: float | None = None) -> tuple[dict, dict] | None:
    """The exchange JUST before this one (this machine; Jarvis's own echoed "Yes, sir." skipped) when it ordered one of his
    protocols (`name`: that one), its reply did nothing, and the protocol has not run since: (protocol, row). Anything said
    in between (a "cancel that", another request) means nothing is owed (review 2026-11-21)."""
    from core import thread
    now = time.time() if now is None else now
    rows = [r for r in thread.own(uid, window=WINDOW_S, now=now) if not _ECHO.match(r.get("said", ""))]
    if not rows:
        return None
    row = rows[-1]
    proto = order_in(row.get("said", ""))
    if proto is None or (name and proto["name"] != name) or not did_nothing(row.get("reply", "")):
        return None
    if _ran_since(proto["name"], float(row.get("ts") or 0)):
        return None
    return proto, row


# Whisper hears his "End" as "And" (live 2026-11-20 23:43-23:45: "And stream." / "And the stream." six times while he was live,
# then "you're not understanding me. I'm saying end this."). A sentence that opens on "and" + the stream or a protocol that is
# ACTIVE is that slip as often as a connector: asked with a word that cannot be misheard, never guessed either way.
_AND_PROTOCOL = re.compile(r"^\W*(?:(?:hey\s+)?jarvis[,.!\s]+)?and\s+(?:the\s+|my\s+)?[a-z0-9' -]{1,40}?\s+protocols?\b", re.I)
_AND_STREAM = re.compile(r"^\W*(?:(?:hey\s+)?jarvis[,.!\s]+)?and\s+(?:the\s+|my\s+)?(?:stream|streaming|broadcast|live ?stream)"
                         r"(?:[\s,]+(?:now|please|sir|jarvis))*\W*$", re.I)
ACTIVE_S = 30 * 60          # a protocol that ran this recently may still be "on" (its apps, the cleared background)


def _active(proto: dict) -> bool:
    from core import protocols, users
    if (users.current(), proto["name"]) in protocols._running:
        return True
    if any(s.get("do") == "free_up" for s in proto.get("steps", [])):
        try:
            from core import gameboost
            if gameboost.active():
                return True
        except Exception:  # noqa: BLE001
            pass
    return _ran_since(proto["name"], time.time() - ACTIVE_S + RAN_SLACK_S)


def and_or_end(text: str) -> str | None:
    """'And streaming protocol.' while it is active / 'And the stream.' while he is live: the question, else None."""
    from core import protocols, users
    title = users.title(users.current(), "en") or "sir"
    parts = [p for p in re.split(r"(?<=[.!?;])\s+", text) if p.strip()]
    for part in parts:
        if _AND_PROTOCOL.match(part):
            proto = order_in(part)
            if proto is not None and _active(proto):
                name = proto["name"]
                return (f"Sorry, {title}, I may have misheard: did you say end the {name} protocol? It is already on. "
                        f"Say 'stop the {name} protocol' to end it and put everything back, or 'run the {name} protocol' to run it again.")
        if _AND_STREAM.match(part) and protocols._is_owner():
            try:
                from core import obsstream, pcbridge
                live = pcbridge.local_pc() and obsstream.still_live()
            except Exception:  # noqa: BLE001 - unknown: as before
                live = False
            if live:
                return (f"You're live, {title}. Did you mean end the stream? I may have heard 'and'. "
                        f"Say 'stop the stream' and I'll end it.")
    return None


def handle(raw: str, now: float | None = None) -> str | None:
    """His repeat of an order that was not carried out: the miss said plainly, then the order done. None otherwise (the
    protocols and everything after answer as before)."""
    text = (raw or "").replace("’", "'").strip()
    if not text or len(text) > 400:
        return None
    if re.search(r"(?i)\band\b", text):
        asked = and_or_end(text)
        if asked is not None:
            return asked
    now_order = order_in(text)
    angry = now_order is None and frustrated(text)
    if now_order is None and not angry:
        return None
    from core import pcbridge, protocols, users
    uid = users.current()
    owed = unfulfilled(uid, now_order["name"] if now_order else None, now)
    if owed is None:
        return None
    proto = owed[0]
    name, title = proto["name"], users.title(uid, "en") or "sir"
    if not pcbridge.local_pc():
        # The laptop (phone) cannot know whether the PC ran it: protocol-runs.json is written on the PC and reaches the laptop
        # only when Syncthing syncs it. Never a retry from here (a second run on the PC), never a guess (review 2026-11-21).
        # His own new order goes on to the PC as before (protocols.start -> pcfast.ask_pc).
        if angry:
            return (f"Sorry, {title}: I can't tell from here whether the PC ran the {name} protocol. "
                    f"Say 'run the {name} protocol' and the PC will run it.")
        return None
    if not protocols._is_owner() or (uid, name) in protocols._running:
        return None                          # the protocols answer: "for the PC's owner only" / "already running"
    sorry = f"Sorry, {title}: I didn't start the {name} protocol when you asked."
    say = protocols.EARLY_SAY.get()
    if say is not None:
        say(sorry)                           # said at once; protocols says "Starting the X protocol" as its steps begin
        return protocols.start(proto, f"run the {name} protocol")
    return f"{sorry} {protocols.start(proto, f'run the {name} protocol')}"


# -- one turn from his fragments (main.py) ---------------------------------------------------------------------------
CARRY_S = 20.0              # his next words began this soon after the cut fragment (the bedtime message: 1-38 s apart)
CARRY_CHARS = 600
# An earlier fragment that may already have DONE something is never sent again (a second "pause the music" undoes it).
_VERB = (r"(?:open|close|play|pause|stop|search|find|show|turn|set|check|start|launch|run|go|look|read|write|type|send|call|add|"
         r"remind|mute|unmute|volume|louder|quieter|minimi[sz]e|maximi[sz]e|focus|switch|scroll|hide|resume|skip|next|previous|"
         r"bring|pull|fire|boot|load|delete|remove|uninstall|install|kill|end|cancel|shut|restart|reboot|log|save|buy|order|pair|"
         r"repair|approve|create|build|generate|make(?! it happen| sure)|rig|render|download|upload|move|copy|rename|clear|clean|"
         r"empty|format|wipe|lock|sign|connect|disconnect|enable|disable|record|take|capture|text|email|message|tell|click|press|"
         r"follow|do|scan|use(?! whatever| any (?:means|way))|fast forward|rewind|get rid|fix|change|update|learn|teach|press|select|drag|"
         # the globe and screens (2026-11-21 replay: "Zoom in." "Zoomed in." "Zoom in more." must never zoom twice)
         r"zoom|reset|undo|redo|toggle|refresh|reload|rotate)")
# an order verb ANYWHERE in his words ("my pc shut everything down", "I would actually like you to open it"): an order inside narration
# may have run, and one carried into a second turn may act twice. Errs toward keeping fragments apart (review direction 2026-11-11).
_ACTS = re.compile(r"\b" + _VERB + r"\b", re.I)


def acts(text: str) -> bool:
    """Words that may have run something: an order verb anywhere, or a protocol named. Errs toward True:
    a fragment wrongly kept apart is answered alone, as before; one wrongly sent again may act twice."""
    text = (text or "").replace("’", "'")
    return "protocol" in text.lower() or bool(_ACTS.search(text))


def carry(previous: str, new: str, heard_any: bool, age: float, *, played: float | None = None, first_done: bool = True,
          acted: bool = False, his_voice: bool = True, said: str = "", working: bool = False) -> str:
    """What to answer now. `previous`: his fragment whose reply he cut off; `heard_any`: whether any of that reply played;
    `age`: seconds since that fragment was taken. His fragments become one turn when his next words came quickly, the earlier
    words could not have done anything (`acts`, and `acted`: a tool / PC step ran during that reply), and either nothing of
    the reply was heard or he YIELDED it (`yielded`: he began again while its first sentence played, in his own voice, not with
    a stop / "no, ..." / his name) before the reply said it had done something (`said`: what of it played) and while its
    worker is not still running (`working`: it may yet start a tool, review 2026-11-21); otherwise only his new words."""
    previous, new = (previous or "").strip(), (new or "").strip()
    if (not previous or not new or age > CARRY_S or acted or acts(previous) or to_someone_else(new) or barge_in(new)
            or _ECHO.match(previous)):    # "Yes, sir.": Jarvis's own voice heard as his, never part of his message
        return new
    if heard_any and (working or not yielded(new, played, first_done, his_voice) or reports_done(said)):
        return new
    merged = f"{previous} {new}"
    return merged if len(merged) <= CARRY_CHARS else new


# -- graceful yield (2026-11-21) ------------------------------------------------------------------------------------
# data/jarvis.log 2026-11-15..21: 63 cut-ins; 9 were his thought going on after Jarvis had begun answering its first part at
# his pause ("Right. Okay. So now." -> "As I said..." -> "I need to know, did Apex get a new update?"), 8 more before any
# sound (carry() merged those already). When he starts again this early the reply was premature, not wrong: it is dropped
# and his whole thought is answered once. Later, he heard the reply and is answering it (a new order, a correction): his
# words stand alone, as before.
YIELD_S = 3.0               # he began again while Jarvis's FIRST sentence still played, at most this far into his voice
# His new words open on a stop, a "no", a correction or his name: a real barge-in, a new order, never glued onto what came
# before ("No, open Edge instead.", "Stop, stop, stop.", "Hey Jarvis. Open World Intelligence.").
_BARGE = re.compile(r"^\W*(?:(?:hey|ok(?:ay)?|yo)[,\s]+)?(?:no|nope|nah|stop|wait|hold (?:on|up)|hang on|cancel|actually|instead|"
                    r"not that|never ?mind|forget (?:it|that)|scratch that|shut up|be quiet|quiet|shh+|enough|don'?t|pause|sorry|"
                    r"listen|jarvis|jervis|javis|charvis|"
                    # his French and Kreyol stop words (main.STOP_WORDS)
                    r"attends|arr[eê]te|chut|annule|laisse tomber|tann|rete|kanpe|anile|kite sa|pa bezwen)\b", re.I)


# A reply that reports an action ("Zoomed in.", "Closed notepad.", "Done.", or core.intelligence.claims_done): something ran.
_DID = re.compile(r"^\W*(?:done|all set|(?:ok(?:ay)?|right|very good)[,\s]+(?:sir[,\s]+)?done|[a-z]+ed)\b", re.I)


def reports_done(said: str) -> bool:
    said = (said or "").replace("’", "'").strip()
    if not said:
        return False
    if _DID.match(said):
        return True
    try:
        from core import intelligence
        return intelligence.claims_done(said)
    except Exception:  # noqa: BLE001 - unknown: it may have acted (keep the fragments apart)
        return True


def barge_in(new: str) -> bool:
    return bool(_BARGE.match((new or "").replace("’", "'")))


def yielded(new: str, played: float | None, first_done: bool = True, his_voice: bool = True) -> bool:
    """He took the turn back from a premature reply: he began again while its first sentence still played (`first_done`
    False), `played` seconds into Jarvis's voice (None: unknown, never a yield; at most YIELD_S), in his own voice
    (`his_voice`: the voiceprint matched, not the TV or Jarvis's echo), and not with a barge-in. A whole first sentence
    heard ("It's 3 pm." "And in Tokyo?") makes his words an answer to it: their own turn (review 2026-11-21)."""
    if played is None or first_done or not his_voice or barge_in(new):
        return False
    return played <= YIELD_S


# His next words said to someone else in the room ("Seth, put that down", "Hey Maya, ..."): not part of his message to
# Jarvis (review 2026-11-21). Whisper writes a name with a capital; the words that open a sentence to Jarvis are excluded.
_NOT_A_NAME = (r"(?:Jarvis|Hey|Hi|Ok|Okay|Yes|Yeah|Yep|No|Nope|Nah|So|Well|And|But|Or|Now|Right|Alright|All|Sir|Please|Oh|Um|Uh|"
               r"Wait|Look|Listen|Man|Bro|Dude|Actually|Also|Then|Thanks|Sure|Fine|Good|Great|Cool|Nice|Sorry|Like|Hmm|Mm)")
_TO_OTHER = re.compile(rf"^\W*(?:(?i:hey|yo|hi)[,\s]+)?(?!{_NOT_A_NAME}\b)[A-Z][a-z]+[,!]\s"
                       rf"|^\W*(?i:hey|yo)[,\s]+(?!(?i:jarvis)\b)(?!{_NOT_A_NAME}\b)[A-Z][a-z]+\b")


def to_someone_else(text: str) -> bool:
    """A name other than Jarvis's opens these words. Whisper's slips of "Jarvis" ("Jervis", "Joris", "Jairus", "Jardis": 7 of
    the log's 16 hits) are his name. A miss only keeps two fragments apart, as before this change."""
    m = _TO_OTHER.search((text or "").strip())
    if not m:
        return False
    name = re.findall(r"[A-Z][a-z]+", m.group(0))[-1].lower()
    from difflib import SequenceMatcher
    if name[:1] == "j" and SequenceMatcher(None, name, "jarvis").ratio() >= 0.5:
        return False
    return name not in _OPENERS


_OPENERS = {"yo", "wow", "oui", "non", "stop", "technically", "honestly", "basically", "listen", "seriously", "dang", "damn",
            "bruh", "boy", "girl", "babe", "baby", "man", "guys", "see", "look", "hello", "hmm"}


def turn_words(previous: dict | None, command: str, *, from_cut: bool, route: str, items, now: float | None = None) -> str:
    """main.voice_loop: the words this turn answers. `previous`: the fragment kept by keep_fragment() for the turn before
    (None: none); `from_cut`: these words cut off that turn's reply; `route`: main.pre_brain_route's verdict for these words;
    `items`: what waits on his answer now (core.turnstate.open_items). Only a plain turn after a plain turn, with nothing
    waiting on a yes, is carried: a cut "Yes." to an approval merged with "Sure, go ahead" would answer it twice."""
    if not (from_cut and previous and route == "respond" and not items and previous.get("route") == "respond"
            and not previous.get("items")):
        return command
    now = time.monotonic() if now is None else now
    return carry(previous.get("text", ""), command, bool(previous.get("heard")), now - float(previous.get("at", now)),
                 played=previous.get("played"), first_done=bool(previous.get("first_done", True)),
                 acted=_acted(previous.get("since")), his_voice=bool(previous.get("his_voice", True)),
                 said=previous.get("said", ""), working=_working(previous.get("working")))


def _working(alive) -> bool:
    """The cut reply's worker (a callable: Thread.is_alive) still runs. Unknown: not; an error: it does."""
    try:
        return bool(alive is not None and alive())
    except Exception:  # noqa: BLE001
        return True


def _acted(since: float | None) -> bool:
    """A tool or PC step began (or still runs) since wall-clock `since` (core.turntrace): his words are never sent again.
    No `since` counts as not; an error counts as acted."""
    try:
        if since is None:
            return False
        from core import turntrace
        return turntrace.acted_since(float(since))
    except Exception:  # noqa: BLE001 - unknown: keep the fragments apart (the safe side)
        return True


def keep_fragment(words: str, cut: bool, *, heard_any: bool, route: str, items, taken: float, played: float | None = None,
                  first_done: bool = True, since: float | None = None, his_voice: bool = True, said: str = "",
                  working=None) -> dict | None:
    """After respond(): what the next turn may be carried onto (None when the reply was not cut). `played`: seconds of the
    reply's voice before his words began (None: none played, or unknown); `first_done`: its first sentence had played whole;
    `since`: wall-clock start of the reply (what ran since is checked when the next turn comes); `his_voice`: the cut-in
    matched his voiceprint (or could not be checked); `said`: the reply's sentences that began to play; `working`: a
    callable, True while the cut reply's worker still runs."""
    if not cut:
        return None
    return {"text": words, "at": taken, "heard": heard_any, "route": route, "items": bool(items), "played": played,
            "first_done": first_done, "since": since, "his_voice": his_voice, "said": said,
            "working": working}
