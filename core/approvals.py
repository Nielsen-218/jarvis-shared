"""Approvals enforced in code, not left to the AI.

A risky tool (delete a file, delete a torrent's files, a computer-control step that
sends/buys/deletes) first calls request(); that only records what is waiting. The
action is approved only when the person's *next real message* (spoken, typed or from
the phone) is a clear yes. The AI can't approve on its own: setting confirmed=true or
writing "The user confirmed" does nothing unless consume() finds that yes.

One pending action per person; it expires after 5 minutes. A yes approves it, a no / stop /
cancel drops it, and anything else leaves it waiting.
"""
import json
import re
import threading
import time

EXPIRY = 300  # seconds

YES = {"yes", "yeah", "yep", "yup", "yes please", "sure", "ok", "okay", "do it", "go ahead", "go for it",
       "confirm", "confirmed", "approved", "absolutely", "of course", "please do", "yes do it", "delete it",
       "send it", "yes delete it", "go on",
       "wi", "wi fè l", "wi fe l", "fè l", "fe l", "ale", "dakò", "dako", "wi dakò",
       "oui", "ouais", "d'accord", "vas-y", "allez-y", "oui vas-y", "bien sûr", "fais-le"}
_FILLER = re.compile(r"^(jarvis|hey jarvis|ok jarvis|okay jarvis)[,\s]+|[,\s]+(jarvis|sir|please|madame)$")

# A direct command is its own yes: "uninstall Discord", "delete the old trailer", "close Chrome".
# These verbs, when the person says them, pre-approve that kind of action for this request.
# Money and messages are NOT on this list: buying, paying, sending and posting always get asked.
DIRECT = {
    "uninstall": ("uninstall", "dezinstale", "désinstalle", "desinstalle", "remove", "get rid of", "retire"),
    "delete": ("delete", "remove", "erase", "get rid of", "trash", "efase", "retire", "supprime", "efface"),
    "close": ("close", "quit", "exit", "kill", "fèmen", "femen", "ferme", "quitte"),
    "restart": ("restart", "reboot", "redemare", "redémarre", "redemarre"),
    "power": ("shut down", "shutdown", "power off", "sleep", "lock", "sign out", "log off", "log out",
              "etenn", "éteins", "eteins", "verrouille"),
    # the owner's own commands for the two-phase PC skills (owner 2026-11-07: "when I say do something there are no rules")
    "install": ("install", "set up", "setup", "download", "get me", "enstale", "installe"),
    "guide": ("follow", "do the steps", "set up", "setup", "do what it says", "do the how-to", "do the guide"),
    "clean": ("clean up", "cleanup", "clean out", "clear out", "netwaye", "nettoie"),
    "purge": ("purge", "empty", "delete", "wipe", "clear"),
    "undo": ("undo", "revert", "roll back", "rollback", "put it back", "reverse that", "defè", "annule"),
}
_NEVER_DIRECT = re.compile(r"\b(buy|purchase|order|pay|checkout|check out|send|post|email|text|message|transfer|achte|peye|voye|achète|paye|envoie)\b")
_GRANT_FOR = 600  # a direct command covers that request's steps for 10 minutes

# "Do-not-disturb / just do it": the owner tells Jarvis to proceed on paid planning without
# stopping to ask. It covers the COST/paid gate only; it never auto-approves an irreversible
# action (buy/pay/send/post/transfer) or a sensitive step, and the monthly budget still caps spend.
_AUTONOMY = re.compile(
    r"\b(just (?:get it done|do it|handle it|go)|get it done|handle it|take care of it|"
    r"do(?:n'?t| not) bother( me)?|do(?:n'?t| not) ask( me)?( first)?|no need to ask|"
    r"without asking|don'?t stop to ask|be quick|quickly|hurry|do whatever (?:it takes|you need|is needed)|"
    r"go ahead and do (?:it|whatever)|you have my permission|permission granted|autopilot)\b",
    re.I)
# Turning it back off: "ask me first", "check with me", plus any plain no/stop/wait/cancel.
_ASK_FIRST = re.compile(r"\b(ask me(?: first)?|check with me|don'?t do it without asking|slow down)\b", re.I)
_AUTONOMY_FOR = 1800  # a "just do it" covers paid planning for 30 minutes

_lock = threading.Lock()
_pending: dict[str, dict] = {}  # uid -> {"key", "what", "t", "approved"}
_direct: dict[str, dict] = {}   # uid -> {"verbs": set, "words": str, "t"}
_autonomy: dict[str, float] = {}  # uid -> t of the last "just do it" (paid-planning auto-approve)
_ask_first: dict[str, float] = {}  # uid -> t if they said "ask me first" (opt OUT of autonomous-by-default)


def _norm(text: str) -> str:
    text = re.sub(r"^\[[^\]]*\]\s*", "", text.strip())  # "[Spoken into their Jarvis phone app.] yes"
    text = re.sub(r"[.!?]+$", "", text.lower()).strip()
    for _ in range(2):
        text = _FILLER.sub("", text).strip(" ,")
    return text


_YES_START = {"yes", "yeah", "yep", "sure", "ok", "okay", "wi", "oui", "ouais", "confirm", "confirmed"}
_NEW_REQUEST = re.compile(
    r"^(?:hey |ok |okay )?(?:jarvis,? )?(?:can you |could you |would you |will you |please )?"
    r"(?:add|open|close|set|remind|put|search|find|look|play|turn|show|tell|create|make|start|launch|delete|remove|send|call|"
    r"type|write|read|check|install|uninstall|rig|import|run|take|draw|save|move|copy|what|whats|what's|who|where|when|why|how|"
    r"is|are|do you|did you)\b"
    # "close it" / "save that" / a bare "send" answers the question asked ("Should I close Notepad?"): not a new request
    r"(?!(?: (?:it|that|them|this|those|these|one))?(?: now| please| then)?$)")
# A clause that refuses: "What? No, don't do that.", "Look, no.", "How about no.", "Why? Cancel that." (review 2026-11-10).
_REFUSAL = re.compile(r"(?:^|[.?!,;:]\s*)(?:no|nope|nah|stop|cancel(?: that| it)?|don'?t|do not|never ?mind|leave it|wait)\b|"
                      r"\b(?:about|just) no$")


def _fresh_request(t: str) -> bool:
    """A new request (that may contain 'no' as a word, like 'no module named yaml'), not an answer refusing something."""
    return bool(_NEW_REQUEST.match(t) or re.match(r"^(?:google|search|look up|find)\b", t)) and not _REFUSAL.search(t)


_NEGATION = re.compile(r"\b(no|not|don'?t|do not|never|wait|stop|cancel|non|pa|pas|ne)\b")


def is_yes(text: str) -> bool:
    """A clear yes: "yes", "go ahead", "wi", "oui vas-y", or "yes, delete it" (no 'not'/'wait' anywhere)."""
    t = _norm(text)
    if t in YES:
        return True
    first = re.split(r"[\s,]+", t, maxsplit=1)[0]
    return first in _YES_START and not _NEGATION.search(t)


_YES_LEAD = re.compile(r"^(?:(?:yes|yeah|yep|ok|okay|sure|wi|oui)(?:,?\s+sir)?[\s,.!]+)+", re.I)
_PRONOUN = re.compile(r"\b(?:it|that|this|them|those|these|one)\b", re.I)


def _yes_then_new_request(text: str) -> bool:
    """'Yes, sir. Open Edge Browser.' -> True: after the yes comes a fresh request that names its own object (not 'it')."""
    t = re.sub(r"^\[[^\]]*\]\s*", "", (text or "").strip())
    rest = _YES_LEAD.sub("", t, count=1).strip()
    if not rest or rest == t:
        return False
    rest = _norm(rest)
    return bool(rest) and _fresh_request(rest) and not _PRONOUN.search(rest) and len(rest.split()) >= 2


_MEANT: dict = {}   # (uid, text) -> (monotonic time, "yes" | "no" | None): one model question per sentence
_THANKS = re.compile(r"^(?:(?:ok(?:ay)?|cool|great|alright|all right)[,\s]+)?(?:thanks?(?: you)?(?: (?:so|very) much| a lot)?|"
                     r"thank you(?: (?:so|very) much)?|cheers|thx|ty|appreciate (?:it|you)|m[eè]si(?: anpil)?|merci(?: beaucoup)?)$")


def meant(uid: str, text: str) -> str | None:
    """"yes" / "no" / None: their answer to the question waiting for them, in THEIR words. The fixed lists answer first
    ("yes", "go ahead", "no"); with an approval actually waiting, anything else short is asked of the local model
    (core.meaning), so "sounds good", "please do", "nah leave it" or "hold off on that" work too (owner 2026-11-07: "I will
    not remember exact lines"). Nothing waiting: None, and no model call.
    A yes counts only for the question he was just asked (core.turnstate): to one asked before another exchange ("what time is
    it?" came in between) it is "reask", never approval; the caller asks once more, naming what would run."""
    if _THANKS.match(_norm(text)):
        # "Thanks." / "Okay, thanks." used to end the conversation in the voice loop; with a plan open they now reach the
        # brain (main.pre_brain_route), and a thank-you is no answer to anything waiting ("Yes, thanks." still is a yes).
        return None
    if is_yes(text):
        if _yes_then_new_request(text) and not _echoes_waiting(uid, text):
            # "Yes, sir. Open Edge Browser." (live 2026-11-12): his "yes, sir" is how he starts a sentence, and what follows
            # is a new thing to do. Read as a yes it skipped the understanding layer and took 517 s, and with a plan waiting
            # it would have approved THAT plan instead. "Yes, delete the file." to "delete the file?" still answers it
            # (review 2026-11-16): the yes counts when the rest echoes what is waiting.
            return None
        return _bound(uid, "yes")
    if _NEGATION.search(_norm(text)) and not _AUTONOMY.search(text) and not _fresh_request(_norm(text)):
        # "Google this: ... no module named yaml" was a NO to a waiting plan ("Okay, I won't."): a new request that merely
        # contains "no"/"not" is not the owner's answer (live harness 2026-11-10). A refusal clause in it still is.
        return "no"
    with _lock:
        p = _pending.get(uid)
        what = p["what"] if p and not p["approved"] and time.monotonic() - p["t"] <= EXPIRY else ""
    if not what:                       # a PC plan shown for review ("Here's my plan ... want me to go ahead?") waits in the ledger
        try:
            from core import pcledger
            plan = next(iter(pcledger.pending()), None)
            what = str(((plan or {}).get("data") or {}).get("task") or "") if plan else ""
        except Exception:  # noqa: BLE001
            what = ""
    if not what:
        return None
    if _fresh_request(_norm(text)):
        # Live 2026-11-09 on qwen3.5: with "add items to shopping list in Notepad" waiting, "Add eggs and milk to my shopping
        # list." was read as YES and the waiting Notepad plan ran. A fresh request, even about the same thing, is a new
        # request to understand, never the owner's yes to something already shown.
        return None
    if _answers_other(uid):
        return None      # "sounds good" to "Do you want tomorrow's forecast too?": that question's answer, no model call
    hit = _MEANT.get((uid, text))
    if hit and time.monotonic() - hit[0] < 60:
        return _bound(uid, hit[1])
    from core import meaning
    answer = meaning.choose(text, {"yes": "yes, go ahead and do it", "no": "no, don't do it / not now"},
                            f"The assistant asked them for a yes or no about: {what}", max_words=10)
    _MEANT[(uid, text)] = (time.monotonic(), answer)
    return _bound(uid, answer)


def _waiting(uid: str) -> list[str] | None:
    """What is waiting for his yes (an approval, a PC plan shown for review, a guide or learned procedure), as the TASK
    words he was shown (never plan JSON: its keys say "close" and "window" for any plan); [] when nothing waits; None
    when it cannot be told (then a yes answers, as before)."""
    found: list[str] = []
    with _lock:
        p = _pending.get(uid)
        if p and not p["approved"] and time.monotonic() - p["t"] <= EXPIRY:
            found.append(str(p.get("what") or ""))
    try:
        from core import pcledger
        found += [str((plan.get("data") or {}).get("task") or "") for plan in pcledger.pending()]
    except Exception:  # noqa: BLE001
        return None
    try:
        from tools import pcskills
        item = getattr(pcskills, "_PENDING", {}).get(uid)
        if item:
            found.append(str(item.get("task") or item.get("goal") or "") if isinstance(item, dict) else "")
    except Exception:  # noqa: BLE001
        pass
    return found


_STOP = {"the", "a", "an", "sir", "please", "yes", "okay", "ok", "yeah", "go", "ahead", "just", "now", "jarvis", "can", "could",
         "you", "would", "will"}


def _content(text: str) -> list:
    """Every word and number that says WHAT, in order (digits and short words count: 'file 1' is not 'file 2'; 'send
    Mary John' is not 'send John Mary')."""
    return [w for w in re.findall(r"[a-z0-9][a-z0-9']*", (text or "").casefold()) if w not in _STOP]


def _echoes_waiting(uid: str, text: str) -> bool:
    """Is 'yes, <rest>' an answer to what waits? Only when the rest says EXACTLY that task: the same words, no more and no
    fewer ("Yes, delete the file." to "delete the file?"). "Yes, close Chrome." never approves "close Notepad", and "Yes,
    close Notepad." never approves "close Notepad and Chrome" (review 2026-11-16, four rounds). A near echo is read as
    a new request instead (it is asked again: the safe direction). Unknown what waits: as before (it answers)."""
    waiting = _waiting(uid)
    if waiting is None:
        return True
    rest = _YES_LEAD.sub("", re.sub(r"^\[[^\]]*\]\s*", "", (text or "").strip()), count=1)
    said = _content(rest)
    return bool(said) and any(said == _content(w) for w in waiting)


def _bound(uid: str, answer: str | None) -> str | None:
    """A yes to a question asked before the latest exchange is "reask" (core.turnstate.stale); every other answer as it was.
    Which waiting item a yes then runs or approves is core.turnstate.answering's (the one just asked, nothing else)."""
    if answer != "yes":
        return answer
    try:
        from core import turnstate
        return "reask" if turnstate.stale(uid) else "yes"
    except Exception as error:  # noqa: BLE001 - unknown: as before turnstate
        print(f"[approvals] could not tell which question this yes answers: {type(error).__name__}: {error}")
        return answer


def _answers_other(uid: str) -> bool:
    """The latest exchange asked something that is no plan, approval or guide (core.turnstate.answers_other)."""
    try:
        from core import turnstate
        return turnstate.answers_other(uid)
    except Exception as error:  # noqa: BLE001 - unknown: as before turnstate
        print(f"[approvals] could not tell which question was just asked: {type(error).__name__}: {error}")
        return False


def _asked_after(uid: str, p: dict | None) -> bool:
    """The waiting approval `p` was asked after the words being answered now came in (core.turnstate.heard_at: a spoken
    no that waited behind the companion's typed turn on the PC): that no was never about it."""
    if not p:
        return False
    try:
        from core import turnstate
        return turnstate.asked_after_heard(uid, f"approval:{p.get('key')}:{p.get('t')}")
    except Exception as error:  # noqa: BLE001 - unknown: as before turnstate
        print(f"[approvals] could not tell when this approval was asked: {type(error).__name__}: {error}")
        return False


def _yes_is_for(uid: str, p: dict) -> bool:
    """His yes approves the approval `p` only when it answers it: the latest exchange asked exactly it (or the guide whose
    approval it is), or nothing is known (a caller outside a Brain exchange). A yes to a plan just put up, or to a question
    in words, approves no other approval waiting beside it (review 2026-11-10)."""
    try:
        from core import turnstate
        state, item = turnstate.answering(uid)
    except Exception as error:  # noqa: BLE001 - unknown: as before turnstate
        print(f"[approvals] could not tell which question this yes answers: {type(error).__name__}: {error}")
        return True
    if state != "fresh":
        return True
    return item.get("kind") in ("approval", "guide") and item.get("key") == str(p.get("key"))


def means_yes(uid: str, text: str) -> bool:
    return meant(uid, text) == "yes"


def request(uid: str, key: str, what: str) -> str:
    """Record that `key` needs a yes from `uid`; returns the message for the AI to relay.
    One question at a time: while another action is still waiting for an answer, a new one is refused,
    so a "yes" can never land on a different action than the one the person heard about."""
    with _lock:
        p = _pending.get(uid)
        if p and p["key"] != key and not p["approved"] and time.monotonic() - p["t"] <= EXPIRY:
            return (f"NOT_ASKED: still waiting for the user's answer about: {p['what']}. Get that answer first, "
                    "then ask about this one separately.")
        _pending[uid] = {"key": key, "what": what, "t": time.monotonic(), "approved": False}
    return f"NEEDS_CONFIRMATION: {what}. Ask the user; it only goes ahead if their next message is a clear yes."


def direct_verbs(text: str) -> set[str]:
    """Which kinds of action the person directly commanded in this message."""
    t = _norm(text)
    if _NEVER_DIRECT.search(t) or _NEGATION.search(t):
        return set()
    kinds={kind for kind, words in DIRECT.items() if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words)}
    # "turn off my PC" / "turn my PC off" are explicit power permission.
    # Never count "turn off the lights" or the model's invented tool text.
    from core import pcpower
    request=pcpower.parse(t)
    if request and request[0]!="wake":
        kinds.add("power")
    return kinds


def power_direct(uid: str, action: str) -> bool:
    """Require an exact recent REAL owner PC-power utterance, not a model guess."""
    from core import pcpower
    with _lock:
        request=_direct.get(uid)
    if not request or time.monotonic()-request["t"]>_GRANT_FOR:
        return False
    parsed=pcpower.parse(request["words"])
    return bool(parsed and parsed[0]==action)


def direct(uid: str, kind: str, target: str = "") -> bool:
    """True if the person's own latest command directly asked for this kind of action (and, when a
    target is given, named it), so there is no need to ask again."""
    with _lock:
        d = _direct.get(uid)
        if not d or kind not in d["verbs"] or time.monotonic() - d["t"] > _GRANT_FOR:
            return False
        if not target:
            return True
        words = [w for w in re.split(r"[^a-z0-9]+", target.lower()) if len(w) > 3]
        return not words or any(w in d["words"] for w in words)


def on_user_message(uid: str, text: str) -> None:
    """Called for every real message from a person, before the AI sees it."""
    verbs = direct_verbs(text)
    # "don't bother me" / "don't ask me" is a do-not-disturb grant, not a cancellation, even though
    # it contains "don't"/"ask me"; an autonomy phrase wins over the "ask me first" turn-off.
    is_autonomy = bool(_AUTONOMY.search(text))
    # A negation that grants nothing new always takes a direct command back ("Uninstall... no wait, don't"): fail safe.
    # Only a plain new request that merely contains "no" ("Google this: ... no module named yaml") leaves a waiting
    # question open (independent review 2026-11-10: "What? No, don't do that." must still cancel).
    negated = bool(_NEGATION.search(_norm(text))) and not is_yes(text) and not is_autonomy
    called_off = negated and not _fresh_request(_norm(text))
    answer = None if is_autonomy or _ASK_FIRST.search(text) else meant(uid, text)
    called_off = called_off or answer == "no"            # "nah leave it", "hold off on that"
    waiting = pending(uid) if answer == "yes" else None
    for_it = bool(waiting) and _yes_is_for(uid, waiting)    # (read before the lock: core.turnstate reads pending())
    later = _asked_after(uid, pending(uid)) if called_off else False
    with _lock:
        if is_autonomy:  # "just do it" / "go autonomous": and clear any standing "ask me first"
            _autonomy[uid] = time.monotonic()
            _ask_first.pop(uid, None)
        elif _ASK_FIRST.search(text):  # "ask me first": opt OUT of autonomous-by-default until they say otherwise
            _ask_first[uid] = time.monotonic()
            _autonomy.pop(uid, None)
        elif called_off:  # a plain no/stop cancels the pending action, but isn't a standing preference
            _autonomy.pop(uid, None)
        if verbs:  # a new command replaces the old one ("uninstall Discord" then "delete the trailer")
            _direct[uid] = {"verbs": verbs, "words": _norm(text), "t": time.monotonic()}
        elif negated:
            _direct.pop(uid, None)
        # Otherwise their command stands while they chat ("admin mode is on", "what's taking so long?"),
        # so a task they asked for isn't forgotten halfway; it lapses after 10 minutes.
        p = _pending.get(uid)
        if not p:
            return
        if time.monotonic() - p["t"] > EXPIRY or (called_off and not later):
            _pending.pop(uid, None)
        elif not p["approved"] and answer == "yes" and for_it and p.get("key") == waiting.get("key"):
            p["approved"] = True
            p["t"] = time.monotonic()
        # Anything else (a question, a comment) leaves the question open: they may still answer it.


def autonomous(uid: str) -> bool:
    """True if the owner recently said "just do it / don't bother me", so paid planning may
    proceed without stopping to ask. Used only for the COST gate; the monthly budget still caps
    spend and irreversible/sensitive steps are still confirmed separately."""
    with _lock:
        t = _autonomy.get(uid)
        return bool(t) and time.monotonic() - t <= _AUTONOMY_FOR


def pc_autonomous(uid: str) -> bool:
    """Default-ON autonomy for PC tasks: Jarvis just does safe tasks and auto-approves cost
    without stopping to ask, UNLESS the owner said "ask me first". The safety floor still holds:
    sensitive/irreversible steps are always confirmed and the monthly budget still caps spend."""
    with _lock:
        return uid not in _ask_first


def consume(uid: str, key: str) -> bool:
    """True once if `uid` approved exactly `key`; the approval is then used up."""
    with _lock:
        p = _pending.get(uid)
        if p and p["approved"] and p["key"] == key and time.monotonic() - p["t"] <= EXPIRY:
            _pending.pop(uid, None)
            return True
        return False


def pending(uid: str) -> dict | None:
    with _lock:
        return dict(_pending[uid]) if uid in _pending else None


def withdraw(uid: str, before: dict | None = None) -> bool:
    """The question waiting now, if it is not `before` (pending() taken before the turn), was set by a turn whose reply
    was never said (he cut in): it can take no yes. One asked earlier, and heard, stays open. True when one was withdrawn.
    (Compared by identity, not by clock: time.monotonic() ticks every 15.6 ms on Windows.)"""
    with _lock:
        p = _pending.get(uid)
        same = before is not None and (p or {}).get("key") == before.get("key") and (p or {}).get("t") == before.get("t")
        if p and not p["approved"] and not same:
            _pending.pop(uid, None)
            return True
        return False
