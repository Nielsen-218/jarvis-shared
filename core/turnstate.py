"""What is in flight for one person right now, and which question his next short answer belongs to (upgrade 2, 2026-11-10).

Live 2026-11-10: "Jarvis, watch me save this on Blender." -> "Okay, I'm watching. Say 'done' when you've finished." -> "Done." ->
"Of course, sir." and the mic slept: the voice loop's dismiss filter took 'done' before core.demonstrate saw it. A bare "no" to a
plan ended the conversation and left the plan approvable for five minutes. So:

  open_items()    what is waiting on him (read-only, cheap): a demonstration being watched, an approval, a PC plan shown for
                  review, a guide plan, 'which one?', 'save it before I close it?', a guided walkthrough, the question the
                  last reply asked. While any is open, a short line ('done', 'no', 'yes', 'the second one') goes to the brain
                  (main.pre_brain_route), never to the dismiss filter.
  decline()       a bare no-word while a plan, an approval or a guide waits: all of them declined, 'Okay, I won't.' Not when
                  the latest exchange asked something else ('Do you want tomorrow's forecast?', a walkthrough offer): the
                  no is that question's answer. A demonstration's own cancel words go to the demonstration.
  note_exchange() each exchange records what it asked (a re-ask counts only when he heard it; a reply that asks again, in
                  its own words, about a waiting plan or approval it names asks that item).
  answering()     which item his yes or no answers: the one the latest exchange asked, nothing else. A yes runs or approves
                  only that item (core.pcfast, approvals.on_user_message); a yes to one asked before another exchange gets one
                  re-ask (reask()), naming it, and never runs (UFO: a confirmation is bound to the exact action and consumed
                  once; Hermes: a stale approval is discarded when the conversation has moved on).

Nothing here acts on the PC. What it does not know (no exchange recorded for this person in this process, or an item no exchange
put up) is judged as before this module, so a caller outside a Brain turn keeps its old behaviour.
"""
from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager

QUESTION_SECONDS = 300           # a question asked in words stays open as long as an approval does (approvals.EXPIRY)
YES_KINDS = ("approval", "guide", "plan", "cloud")      # what a yes runs or approves
DECLINABLE = YES_KINDS                                  # ...and what a bare no declines
_KEEP_MARKS = 50                                         # exchanges whose questions are remembered

# A bare no, the whole line ("No.", "Nope.", "Nah, leave it.", "No thanks.", "Never mind."), in his words or the household's.
_NO_WORDS = {"no", "nope", "nah", "no thanks", "no thank you", "nah thanks", "leave it", "nah leave it", "no leave it",
             "never mind", "nevermind", "no never mind", "not now", "no not now", "don't", "dont", "no don't", "don't do it",
             "no don't do it", "cancel", "cancel that", "cancel it", "forget it", "forget that", "hold off", "skip it",
             "non", "non merci", "pa kounye a", "non pa kounye a"}
_FILLER = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis|sir)\s+|\s+(?:jarvis|sir|please)$")

_lock = threading.Lock()
_exchanges: dict = {}    # uid -> number of exchanges recorded with this person
_asked: dict = {}        # uid -> {item id: the exchange that asked it}
_last: dict = {}         # uid -> {"n", "t", "items"}: the latest exchange and what it asked
_then: dict = {}         # uid -> {exchange number: what it asked}: the last _KEEP_MARKS exchanges (heard_at)
_channel: dict = {}      # uid -> {exchange number: whose turn it was: '' the voice, 'typed', 'phone'; None unknown}
# .at = (uid, exchanges recorded when the words this thread answers came in, the channel they came in on): Brain.ask
_heard = threading.local()
# uid -> [(time.time(), item, the words said, the exchange count then)]: re-asked (reask()) since the latest recorded exchange.
# The Brain exchange running it records it as asked only if he heard those words; one made outside any Brain exchange (the phone
# relay, core.pctasks) is the question just asked until the next exchange is recorded.
_asking: dict = {}
REASK_SECONDS = 120      # a re-ask is credited only to the exchange that said it, within this


def _item(kind: str, rid, question, t=None, key=None) -> dict:
    item = {"kind": kind, "id": str(rid), "question": " ".join(str(question or "").split())[:200], "t": t}
    if key is not None:
        item["key"] = str(key)
    return item


def _plan_question(row: dict) -> str:
    data = row.get("data") or {}
    try:
        from core import pchybrid
        words = pchybrid.spoken_plan(data.get("plan"))
    except Exception:  # noqa: BLE001
        words = ""
    return words or str(data.get("task") or "")


def open_items(uid: str, brain=None, kinds=None) -> list:
    """What is waiting on `uid` now: [{'kind', 'id', 'question', 't'}]. Each store is read on its own; one that cannot be read
    (no PC ledger on this machine, say) is simply not listed. `brain`: its _last_question is the question asked in words
    (else the one this module recorded). `kinds`: only those."""
    from core import users
    items: list = []

    def want(kind):
        return kinds is None or kind in kinds

    if want("demonstrate"):
        try:
            from core import demonstrate
            if demonstrate.pending():           # watched now, or stopped by itself and still waiting for 'done'
                items.append(_item("demonstrate", "demonstrate", "Say 'done' when you've finished."))
        except Exception:  # noqa: BLE001
            pass
    guide = None
    if want("guide") or want("approval"):
        try:
            from tools import pcskills
            g = pcskills.pending_guide(uid)
            if g:              # its summary's first line names it ("Goal: set up ripgrep\nSteps (3): ...")
                first = (str(g.get("summary") or "").strip().splitlines() or [""])[0]
                goal = re.sub(r"^\s*goal:\s*", "", first, flags=re.I)
                guide = _item("guide", f"guide:{g.get('key')}:{g.get('t')}", goal, g.get("t"), g.get("key"))
        except Exception:  # noqa: BLE001
            pass
    if want("approval"):
        try:
            from core import approvals
            p = approvals.pending(uid)
            # A guide asks with an approval of its own key (tools/pcskills._two_phase): one question, listed as the guide.
            if (p and not p.get("approved") and time.monotonic() - p["t"] <= approvals.EXPIRY
                    and not (guide and str(p.get("key")) == guide["key"])):
                # asked again (approvals.request anew) is a new question: its time is part of the id
                items.append(_item("approval", f"approval:{p['key']}:{p['t']}", p.get("what"), p["t"], p.get("key")))
        except Exception:  # noqa: BLE001
            pass
    if guide and want("guide"):
        items.append(guide)
    if want("plan") or want("cloud"):
        try:
            from core import pcledger
            with users.acting_as(uid):
                rows = pcledger.pending()
            items += [_item(r["kind"], r["id"], _plan_question(r)) for r in rows if want(r["kind"])]
        except Exception:  # noqa: BLE001
            pass
    if want("choice"):
        try:
            from core import gateway
            c = gateway.pending_choice(uid)
            if c and time.time() - c.get("t", 0) <= gateway.CHOICE_SECONDS:
                items.append(_item("choice", f"choice:{c.get('t')}", "Which one?", c.get("t")))
        except Exception:  # noqa: BLE001
            pass
    if want("save"):
        try:
            from core import saveprompt
            s = saveprompt.pending(uid)
            if s:
                items.append(_item("save", f"save:{s.get('t')}", f"Save {s.get('app') or 'it'} before I close it?", s.get("t")))
        except Exception:  # noqa: BLE001
            pass
    if want("learning"):
        try:
            from core import guided_learning
            with users.acting_as(uid):
                state = guided_learning._state()
            if (state.get("status") in ("offered", "active") and not state.get("paused")
                    and time.time() - float(state.get("at") or 0) <= guided_learning.IDLE_S):
                items.append(_item("learning", f"learning:{state.get('topic')}:{state.get('index')}", state.get("topic"),
                                   state.get("at")))
        except Exception:  # noqa: BLE001
            pass
    if want("question"):
        q = getattr(brain, "_last_question", None) if brain is not None else last_question(uid)
        if q and q.get("kind") == "question" and time.time() - float(q.get("t") or 0) <= QUESTION_SECONDS:
            items.append(dict(q))
    return items


# ------------------------------------------------------------------------------------------------ what each exchange asked
def closing_question(said: str) -> str:
    """The question a reply ends on when it is a real one, '' when it ends on none or on a closing offer ("Anything else?",
    intelligence.generic_offer)."""
    text = " ".join(str(said or "").split())
    if not text.endswith("?"):
        return ""
    from core import intelligence
    if intelligence.generic_offer(text):
        return ""
    m = re.search(r"([^.!?]*\?)\s*$", text)
    return (m.group(1).strip() if m else text)[:200]


# Words that say nothing about which plan or approval a reply is about.
_COMMON = frozenset(
    "a an the to in into on onto of for and or but with without it its it'll it'd this that these those your you you'd you'll "
    "my me i i'll i'd i'm is are be was were will would should shall can could want wants do does did go going ahead sir "
    "ma'am madam now then just please so okay ok yes no not up out at by from as all also first again there here what "
    "which how when like sure right".split())


def _key_words(text) -> set:
    """The words that name a thing ("type hello in Notepad" -> type, hello, notepad), a plural's 's' dropped and an aside in
    brackets ("(1 MB)") left out."""
    text = re.sub(r"\([^)]*\)", " ", str(text or "")).replace("’", "'").casefold()
    words = set()
    for w in re.findall(r"[a-z0-9][a-z0-9'._-]*", text):
        w = w.strip("'._-")
        if w.endswith("'s"):
            w = w[:-2]
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w and w not in _COMMON:
            words.add(w)
    return words


def _asks_again_in_words(said: str, waiting) -> dict | None:
    """The waiting plan, approval or guide a reply asks about again in its own words: it ends on a question (even "Shall I go
    ahead?") and names that item ("It'll type hello into Notepad, sir. Shall I go ahead?", "Just to confirm, delete x?"): at
    least two of the item's own words, or all of them. One item clearly best, or none."""
    text = " ".join(str(said or "").split())
    if not text.endswith("?"):
        return None
    words = _key_words(text)
    scored = sorted(((len(_key_words(i.get("question")) & words), i) for i in waiting
                     if _key_words(i.get("question"))
                     and len(_key_words(i.get("question")) & words) >= min(2, len(_key_words(i.get("question"))))),
                    key=lambda s: -s[0])
    if len(scored) == 1 or (len(scored) > 1 and scored[0][0] > scored[1][0]):
        return dict(scored[0][1])
    return None


def _flat(text) -> str:
    return " ".join(str(text or "").replace("’", "'").casefold().split())


def note_exchange(uid: str, before=(), after=(), said: str = "", channel: str | None = None) -> dict | None:
    """One exchange with `uid` ended. `before` / `after`: open_items() as it began and as it ended; `said`: what he heard;
    `channel`: whose turn it was (Brain._kind(): '' the voice, 'typed', 'phone'; None: not a turn of his, or unknown).
    What it asked him: what it re-asked (reask(), only when he heard the re-ask) and every item open now that was not before
    (a plan put up for review, an approval, 'which one?', a demonstration it started); else a waiting plan or approval its reply
    asked about again in its own words; else the real question its reply ended on. Returns the first of them (the Brain keeps
    it as _last_question), or None when it asked nothing."""
    before_ids = {i.get("id") for i in before or ()}
    heard = _flat(said)
    with _lock:
        reasked = _asking.pop(uid, [])
    # a re-ask he never heard (cut off, or one relayed to the phone while this exchange was about something else) asked nothing
    asked = [dict(item) for at, item, line, _n in reasked
             if time.time() - at <= REASK_SECONDS and _flat(line) and _flat(line) in heard]
    asked += [dict(i) for i in after or () if i.get("id") not in before_ids and i.get("kind") != "question"
              and i.get("id") not in {a.get("id") for a in asked}]
    if not asked:
        again = _asks_again_in_words(said, [i for i in after or () if i.get("kind") in YES_KINDS])
        if again:
            asked = [again]
    question = "" if asked else closing_question(said)
    now = time.time()
    with _lock:
        n = _exchanges.get(uid, 0) + 1
        _exchanges[uid] = n
        if question:
            asked = [_item("question", f"question:{n}", question, now)]
        marks = _asked.setdefault(uid, {})
        for a in asked:
            marks[a["id"]] = n
        for rid in [r for r, at in marks.items() if at < n - _KEEP_MARKS]:
            del marks[rid]
        _last[uid] = {"n": n, "t": now, "items": asked}
        then = _then.setdefault(uid, {})
        then[n] = [dict(a) for a in asked]
        for old in [k for k in then if k < n - _KEEP_MARKS]:
            del then[old]
        whose = _channel.setdefault(uid, {})
        whose[n] = channel
        for old in [k for k in whose if k < n - _KEEP_MARKS]:
            del whose[old]
    return dict(asked[0]) if asked else None


def exchanges(uid: str) -> int:
    """How many exchanges with `uid` were recorded so far (Brain.ask notes it as his words come in)."""
    with _lock:
        return _exchanges.get(uid, 0)


def all_exchanges() -> dict:
    """{uid: exchanges recorded so far} for everyone: the voice loop takes it the moment his words are captured, before
    Whisper and before it knows whose voice it was (main.voice_loop hear -> Brain.ask heard_upto)."""
    with _lock:
        return dict(_exchanges)


@contextmanager
def heard_at(uid: str, n: int, channel: str | None = None):
    """The words this thread's turn answers came in when `n` exchanges with `uid` had been recorded (Brain.ask, before
    its turn waits for another channel's: on the PC the companion's typed chat and the voice share one Brain). An exchange
    recorded after that asked nothing these words can answer: a spoken yes that waited behind a typed turn never approves,
    runs, picks or declines what the typed turn asked (review 2026-11-11), and is answered as of when he said it.
    `channel`: the channel these words came in on (Brain._kind()); his own earlier turn on it is never 'after' (_anchor)."""
    before = getattr(_heard, "at", None)
    _heard.at = (uid, int(n), channel)
    try:
        yield
    finally:
        _heard.at = before


def _heard_upto(uid: str) -> int | None:
    at = getattr(_heard, "at", None)
    return at[1] if at and at[0] == uid else None


def _anchor(uid: str, cut: int) -> int:
    """With _lock held: the exchange his words follow. `cut` (heard_at), moved past his own earlier turns on the same
    channel that asked nothing and were recorded only after these words came in: he said them BEFORE these words (a voice
    turn still working when he cut in with "yes" ends after the count his yes carries), so the conversation had moved on
    through them, and a yes to what was asked before them is stale (re-asked), as when they ended first (review
    2026-11-11). What they asked, had they asked anything, he never heard: such a turn stays after his words."""
    at = getattr(_heard, "at", None)
    mine = at[2] if at and at[0] == uid and len(at) > 2 else None
    if mine is None:
        return cut
    asked = _then.get(uid) or {}
    return max((k for k, whose in (_channel.get(uid) or {}).items() if k > cut and whose == mine and not asked.get(k)),
               default=cut)


def asked_after_heard(uid: str, item_id) -> bool:
    """Whether `item_id` was first asked by an exchange recorded after the words being answered now came in (heard_at).
    One an exchange he had heard asked stays his to answer though a later one asked it again (in its own words, or
    reask()): his no declines it, as his yes approves it (review 2026-11-11: a typed re-ask kept his spoken no off it)."""
    cut = _heard_upto(uid)
    if cut is None:
        return False
    rid = str(item_id)
    with _lock:
        n = _asked.get(uid, {}).get(rid)          # the NEWEST exchange that asked it (answering() orders by it)
        if n is None or n <= cut:
            return False
        return not any(a.get("id") == rid for k, asked in (_then.get(uid) or {}).items() if k <= cut for a in asked)


def last_question(uid: str) -> dict | None:
    with _lock:
        last = _last.get(uid)
        return dict(last["items"][0]) if last and last["items"] else None


def answering(uid: str, items=None) -> tuple:
    """Which open item his yes or no answers now (`items`: open_items(uid), when the caller has it):
      ('fresh', item)    the latest exchange asked `item` and it is still open: his answer is for it and for nothing else
                         (a plan, an approval, a guide, or a question in words, 'which one?', a walkthrough offer)
      ('stale', item)    the latest exchange asked nothing still open, and `item`, something a yes runs or approves, was asked
                         by an earlier one ('what time is it?' came in between): a yes to it is re-asked, never taken
      ('unknown', None)  nothing recorded for him in this process, or nothing waiting was ever asked by a recorded exchange
                         (a caller outside a Brain exchange): as before this module."""
    items = open_items(uid) if items is None else list(items)
    cut = _heard_upto(uid)
    with _lock:
        if cut is not None:
            cut = _anchor(uid, cut)
        last = _last.get(uid)
        marks = dict(_asked.get(uid, {}))
        reasked = list(_asking.get(uid, []))
        then = [dict(a) for a in (_then.get(uid) or {}).get(cut, ())] if cut is not None else []
    if last is None:
        return "unknown", None
    by_id = {i.get("id"): i for i in items}
    if cut is not None and last["n"] > cut:
        # His words came in before the latest exchange (another channel's turn ran while his waited, heard_at): what he
        # answers is what the exchange before it asked; what was asked since is never his to take, only to re-ask.
        for a in then:
            if a.get("id") in by_id:
                return "fresh", by_id[a["id"]]
            if a.get("kind") == "question" and time.time() - float(a.get("t") or 0) <= QUESTION_SECONDS:
                return "fresh", a
        waiting = [i for i in items if i.get("kind") in YES_KINDS and i.get("id") in marks]
        if waiting:
            return "stale", max(waiting, key=lambda i: marks[i["id"]])
        return "unknown", None
    # re-asked outside a Brain exchange (the phone relay) and no exchange recorded since: that re-ask is the question just asked
    for at, item, _line, n in reversed(reasked):
        if n == last["n"] and time.time() - at <= QUESTION_SECONDS and item.get("id") in by_id:
            return "fresh", by_id[item["id"]]
    for a in last["items"]:
        if a.get("id") in by_id:
            return "fresh", by_id[a["id"]]
    waiting = [i for i in items if i.get("kind") in YES_KINDS and i.get("id") in marks]
    if waiting:
        return "stale", max(waiting, key=lambda i: marks[i["id"]])
    return "unknown", None


def answers_other(uid: str, kinds=YES_KINDS, items=None) -> bool:
    """His yes or no is the answer to something the latest exchange asked that is none of `kinds` (a question in words, a
    walkthrough offer, 'which one?'): it is not meant for a plan, an approval or a guide waiting from before."""
    state, item = answering(uid, items)
    return state == "fresh" and item.get("kind") not in kinds


def stale(uid: str) -> dict | None:
    """The waiting item a bare yes would now land on stale (answering(): asked before the latest exchange, which asked nothing
    still open). Then the yes was for something else and is never taken as approval. None otherwise."""
    state, item = answering(uid)
    return item if state == "stale" else None


def reask(uid: str, title: str = "sir") -> str:
    """One re-ask for a stale yes, naming what would run: "To be clear, sir: paste the text into Claude?". The exchange that
    says it then asked it (note_exchange, when he heard it), so his next yes is the answer to it."""
    item = stale(uid) or next(iter(open_items(uid, kinds=YES_KINDS)), None)
    if item is None:
        return f"Sorry, {title}, there's nothing waiting for a yes."
    what = (item.get("question") or "go ahead with that").strip().rstrip(".!?")
    if not what[:2].isupper():                        # "Delete report.txt" -> "delete report.txt"; "PC ..." stays
        what = what[:1].lower() + what[1:]
    line = f"To be clear, {title}: {what}?"
    with _lock:
        n = (_last.get(uid) or {}).get("n", 0)
        _asking.setdefault(uid, []).append((time.time(), dict(item), line, n))
    return line


# ------------------------------------------------------------------------------------------------ a bare no
def bare_no(text: str) -> bool:
    """The whole line is a no ("No.", "Nope.", "Nah, leave it.", "No thanks, Jarvis."), nothing else in it."""
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", str(text or "")).replace("’", "'").lower()
    t = " ".join(re.sub(r"[^\w\s']", " ", t).split())
    for _ in range(2):
        t = _FILLER.sub("", t).strip()
    return t in _NO_WORDS or (bool(t) and set(t.split()) <= {"no", "nope", "nah"})


def decline(uid: str, text: str, items=None) -> str | None:
    """A bare no while a plan, an approval or a guide waits for his yes: each one is declined (no later yes can approve it) and
    he hears "Okay, I won't.". None for anything else; while 'Save it before I close it?' waits (there "no" is the answer
    "don't save", core.saveprompt); for a demonstration's own cancel words ('cancel', 'never mind', 'forget it': core.demonstrate
    stops watching); and when the latest exchange asked something else ("Do you want tomorrow's forecast too?", a walkthrough
    offer): the no is that question's answer."""
    if not bare_no(text):
        return None
    items = open_items(uid) if items is None else list(items)
    later = [i for i in items if asked_after_heard(uid, i.get("id"))]    # asked after he said it (heard_at): not his no's
    items = [i for i in items if i not in later]
    kinds = {i.get("kind") for i in items}
    if "save" in kinds or not kinds & set(DECLINABLE):
        return None
    if "demonstrate" in kinds:
        from core import demonstrate
        if demonstrate.ends_it(text):
            return None
    if answers_other(uid, DECLINABLE, items):
        return None
    from core import approvals, users
    for i in items:
        try:
            if i.get("kind") in ("plan", "cloud"):
                from core import pcledger
                with users.acting_as(uid):
                    pcledger.decline(i["id"])
            elif i.get("kind") == "guide":
                from tools import pcskills
                pcskills.withdraw_pending(uid)
        except Exception as error:  # noqa: BLE001 - one store failing never keeps the others approvable
            print(f"[turnstate] could not decline {i.get('kind')}: {type(error).__name__}: {error}")
    keep = approvals.pending(uid) if any(i.get("kind") == "approval" for i in later) else None
    approvals.withdraw(uid, keep)           # the approval waiting, without a model call (not one asked after his no)
    approvals.on_user_message(uid, text)    # and a plain no takes back a standing "just do it" or direct command
    return "Okay, I won't."


def reset() -> None:
    """Forget every exchange (tests)."""
    with _lock:
        _exchanges.clear()
        _asked.clear()
        _last.clear()
        _then.clear()
        _channel.clear()
        _asking.clear()
