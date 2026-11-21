"""The CORE SCORECARD: does Jarvis do what he says the first time? (2026-11-16)

From his REAL turns only (test traffic is marked where it is written: core.turntrace.test_traffic; core.thread does not
record verification turns), one card per day:

  first try   everyday commands (open / close / minimize / bring forward, Jarvis's own screens, the globe, plain PC and
              everyday questions) done on the first try: the reply says it was done or gives the reading, and he did not
              have to say it again or complain within two minutes. Since 2026-11-21 also his protocols (start / stop
              one of his: done only when a run of it was recorded in protocol-runs.json or the reply is that run's
              summary, never on "Got it, sir."), the stream (go live / end / am I live), media and volume, timers and
              reminders, the calendar, phone pairing, and click / scroll / type: each judged by the action asked for.
              Rows kept before a kind existed are read with today's kinds (nothing rewritten).
  repeats     a command he said again within two minutes (the previous one counts as failed)
  mishears    lines the recogniser got wrong in a way we know (core.heard's slips, the Command Center forms)
  not understood   "I couldn't work out ...", "I lost my train of thought", "Which ... ?" for a named app
  latency     seconds from his last word to Jarvis's first (voice), or the whole turn (phone): median and 90th percentile
  per kind    the same per command type

Where the turns come from: every finished exchange of the owner's (core.thread.append calls note()) is folded into
data/learning/core-turns-<machine>.jsonl as {ts, channel, said (80 chars, as turn-timing keeps it), kind, outcome,
reply_head (60 chars)}: the thread ledger itself only keeps a day. Latency is joined from turn-timing-<machine>.jsonl.
Nothing here decides or runs anything; it only reads and counts.

The frozen replay set (scripts/core_replay.py, data/learning/core-replay.json) scores a CHANGE before it ships; this card
scores his real days after it ships. Both are shown in the Hub (/api/core/scorecard).
"""
from __future__ import annotations

import contextlib
import difflib
import json
import re
import statistics
import threading
import time
from datetime import datetime, timedelta

REPEAT_S = 120.0            # said again within this: the first try failed
KEEP_SAID = 80
_lock = threading.Lock()

# --- what kind of line it is ---------------------------------------------------------------------------------------
_LEAD = re.compile(r"^(?:(?:hey|hi|ok|okay|yes|yeah|so|now|alright|all right|um|uh|no)[,.\s]+)*(?:(?:hey\s+)?jarvis[,.\s]+)*"
                   r"(?:(?:please|can you|could you|i need you to|i want you to|go ahead and|do me a favou?r and)\s+)*", re.I)
_KINDS = [
    ("cancel", re.compile(r"\b(?:never ?mind|cancel that|forget it)\b", re.I)),
    ("screen", re.compile(r"\b(?:dashboard|command\s*[.,]?\s*cent(?:er|re)|comm(?:ent|unity)\s+cent(?:er|re)|world intelligence|"
                          r"intelligence|the hub|bird'?s[- ]?eye)\b", re.I)),
    ("globe", re.compile(r"^(?!go to (?:sleep|bed))(?:go to|take me to|zoom (?:in|out)|reset|turn on|turn off|return on|show cameras|show (?:the )?"
                         r"(?:aircraft|satellites?|ships|cameras))\b", re.I)),
    ("minimize", re.compile(r"^minimi[sz]e\b", re.I)),
    ("close", re.compile(r"^(?:close|quit|exit|get rid of|kill|shut)\b|\bclose (?:the |this |that )?(?:app|window|tab)\b", re.I)),
    ("focus", re.compile(r"^(?:bring|switch to|focus)\b", re.I)),
    ("open", re.compile(r"^(?:open|launch|pull up|bring up)\b", re.I)),
    ("pc_question", re.compile(r"\b(?:cpu|ram|gpu|vram|disk space|uptime|what'?s running|taking all my)\b|"
                               r"\b(?:wasting|free|using|usage|use)\b[^.?!]{0,12}\bmemory\b|\bmemory\s+(?:usage|free|used)\b", re.I)),
    ("info", re.compile(r"^(?:what(?:'s| is)? (?:the )?(?:time|day|date|weather)|what time|what day|where am i|"
                        r"how'?s the weather|tell me about the weather)\b", re.I)),
]
# The command types added 2026-11-21 (on 11-20 the card counted 40 turns and 0 commands while his protocol and stream orders
# failed). Each is anchored at the start of one of his last two sentences, so narration ("I'm going to click on save", "the
# volume is still at 100") and questions about a thing stay conversation. Their outcome is judged by the action asked for
# (act()): "Pause the video." -> "The video is now playing." is not done, "Go live." -> "Opened livecaptions." is not done.
_MEDIA_NOUN = r"(?:the |this |my |that |some )?(?:video|music|song|track|movie|show|episode|playback|podcast|spotify|youtube)"
_MEDIA_TAIL = r"(?:\s+(?:please|now|for me|again|jarvis|sir|on (?:youtube|spotify|the tv|my pc)))*\W*$"
_MEDIA = re.compile(
    r"^(?:(?:turn|put|set|bring|crank|pump|change)\s+(?:the |my )?(?:volume|sound)\s+(?:all the way\s+)?(?:up|down|to|at|by|back|higher|lower)\b|"
    r"(?:raise|lower|increase|decrease|drop)\s+(?:the |my )?(?:volume|sound)\b|"
    r"(?:turn|crank)\s+(?:the |my )?(?:volume|sound|music)\s+(?:up|down)\b|(?:volume|sound)\s+(?:up|down|to \d)|"
    r"(?:make it |a bit |a little )?(?:louder|quieter)\W*$|(?:un)?mute(?:\s+(?:the |my )?(?:sound|volume|audio|pc|computer|music|video))?\W*$|"
    rf"(?:pause|resume|unpause|play|stop|continue|restart|turn (?:off|on))\s+{_MEDIA_NOUN}{_MEDIA_TAIL}|play\s+(?:some\s+)?music\b|"
    rf"(?:skip|next|previous)(?:\s+(?:this |the )?(?:song|track|video))?{_MEDIA_TAIL})",
    re.I)
_REMINDER = re.compile(r"^(?:(?:set|start|put|make)\s+(?:a |an |the |my |me a )?(?:[\w-]+[- ](?:minute|second|hour)s?\s+)?"
                       r"(?:timer|alarm|reminder)\b|(?:a\s+)?timer for\b|remind me\s+(?:to|about|at|in|on|tomorrow|tonight|that i)\b|"
                       r"wake me(?: up)?\s+(?:at|in)\b|"
                       r"(?:cancel|stop|delete|clear)\s+(?:the |my |all )?(?:\w+\s+)?(?:timers?|alarms?|reminders?)\W*$)", re.I)
_DEV = r"(?!\s+(?:page|tab|tile|view|code|app|module|widget|screen|button|panel|feature|section|ui|integration|link)\b)"
_CALENDAR = re.compile(r"^(?:(?:what'?s|what is|anything|is there anything|do i have (?:anything|something)|read me|check)\b"
                       r"(?![^.?!]*\b(?:wrong|broken|problem|issue|bug|code|connected|working)\b)[^.?!]{0,30}"
                       rf"\b(?:my |the )calendar\b{_DEV}|(?:put|add|schedule|move|reschedule|cancel|delete|remove)\b"
                       r"(?!\s+(?:a |an |the |new |another )*(?:button|tile|tab|page|feature|section|widget|panel|link|field|view)\b)"
                       rf"[^.?!]{{1,60}}\b(?:on|to|from|in|off) (?:my |the )calendar\b{_DEV})", re.I)
_PAIR = re.compile(r"^pair (?:my|the|a|this|his|her) (?:new )?phone\b", re.I)
_UI = re.compile(r"^(?:(?:on|in) [^,.]{1,40},\s*(?:i (?:want|need) you to\s+)?)?(?:(?P<click>(?:double[- ]|right[- ])?click)\s+(?:on\s+)?\S|"
                 r"(?P<scroll>scroll)\s+(?:all the way\s+)?(?:up|down|to)\b|"
                 r"(?P<type>type)\s+(?!of\b|\d|is\b|was\b)(?:in\s+)?\S.{0,60}\s(?:in|into|on)\s+(?:the |this |that |my )?\w)", re.I)
_ACT_KINDS = ("protocol", "stream", "media", "reminder", "calendar", "pair", "ui")
_NEW = (("media", _MEDIA), ("reminder", _REMINDER), ("calendar", _CALENDAR), ("pair", _PAIR), ("ui", _UI))
EVERYDAY = ("open", "close", "minimize", "focus", "screen", "globe", "pc_question", "info", "cancel",
            "protocol", "stream", "media", "reminder", "calendar", "pair", "ui")


def _sentences(text: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]


def _stream_act(t: str) -> str | None:
    """'go' / 'end' / 'status' (core.obsstream's own patterns), 'end?' for "And (the) stream." (Whisper's "End": core.followthrough),
    on the line or any one of its sentences; "End. The stream." (cut at a pause) is rejoined."""
    try:
        from core import followthrough, obsstream
    except Exception:  # noqa: BLE001
        return None
    parts = _sentences(t)
    tries = [t] + (parts if len(parts) > 1 else [])
    if 1 < len(parts) <= 3 and len(t.split()) <= 5:
        tries.append(re.sub(r"[.!?,]+\s+", " ", t))
    for x in tries:
        k = obsstream.kind(_LEAD.sub("", x))
        if k is not None:
            return {"go": "go", "end": "end", "status": "status"}[k]
    if any(followthrough._AND_STREAM.match(x) for x in tries):
        return "end?"
    return None


def act(said: str, kind_: str | None = None) -> str:
    """What was asked within its kind: 'run:<name>' / 'stop:<name>' (protocol), 'go' / 'end' / 'end?' / 'status' (stream),
    'volume' / 'pause' / 'play' / 'skip' (media), 'read' / 'write' (calendar), 'click' / 'scroll' / 'type' (ui); '' otherwise."""
    raw = " ".join((said or "").replace("’", "'").split())
    t = _LEAD.sub("", raw)
    k = kind_ or kind(raw)
    if k == "protocol":
        try:
            from core import followthrough, protocols
            hit = followthrough.order_in(raw)
            if hit is not None:
                return f"run:{hit['name']}"
            m = protocols._STOP.match(raw)
            proto = protocols.find(protocols._name(m)) if m else None
            return f"stop:{proto['name']}" if proto else ""
        except Exception:  # noqa: BLE001
            return ""
    if k == "stream":
        return _stream_act(t) or ""
    last = [_LEAD.sub("", p) for p in _sentences(t)[-2:]]
    if k == "media":
        p = next((p for p in reversed(last) if _MEDIA.search(p)), "")
        if re.match(r"(?:pause|stop|turn off)\b", p, re.I):
            return "pause"
        if re.match(r"(?:resume|unpause|play|continue|restart|turn on)\b", p, re.I):
            return "play"
        if re.match(r"(?:skip|next|previous)\b", p, re.I):
            return "skip"
        return "volume"
    if k == "reminder":
        p = next((p for p in reversed(last) if _REMINDER.search(p)), "")
        return "cancel" if re.match(r"(?:cancel|stop|delete|clear)\b", p, re.I) else "set"
    if k == "calendar":
        p = next((p for p in reversed(last) if _CALENDAR.search(p)), "")
        return "write" if re.match(r"(?:put|add|schedule|move|reschedule|cancel|delete|remove)\b", p, re.I) else "read"
    if k == "ui":
        m = next((m for m in (_UI.search(p) for p in reversed(last)) if m), None)
        return next((g for g in ("click", "scroll", "type") if m and m.group(g)), "")
    return ""


def _protocol_kind(raw: str) -> bool:
    """A start or stop of one of HIS protocols (core.followthrough.order_in, as core.selfreview reads the log; a stop names
    a protocol he has). Unknown names, questions and "Streaming protocol. No, wait." are not orders."""
    if "protocol" not in raw.lower():
        return False
    try:
        from core import followthrough, protocols
        if followthrough.order_in(raw) is not None:
            return True
        m = protocols._STOP.match(raw)
        return bool(m) and protocols.find(protocols._name(m)) is not None
    except Exception:  # noqa: BLE001
        return False


def kind(said: str) -> str:
    """The command type of one line: one of EVERYDAY, or 'other' (conversation, long tasks)."""
    raw = " ".join((said or "").replace("’", "'").split())
    t = _LEAD.sub("", raw)
    if not t:
        return "other"
    if _protocol_kind(raw):              # before the length cap: "And streaming protocol. That's what I was telling you. ..."
        return "protocol"
    if len(t.split()) > 22:
        return "other"
    if _KINDS[0][1].search(t):           # "Open Paint. Never mind." is a cancel, as before
        return "cancel"
    if len(t.split()) <= 12 and _stream_act(t):
        return "stream"
    last = _sentences(t)
    for name, rx in _NEW:
        if any(rx.search(_LEAD.sub("", p)) for p in last[-2:]):
            return name
    for name, rx in _KINDS:
        if name in ("globe", "minimize", "close", "focus", "open", "info"):
            if any(rx.search(_LEAD.sub("", p)) for p in last[-2:]):
                return name
        elif rx.search(t):
            if name == "pc_question" and not re.search(r"\?|how much|what'?s|scan|look for", t, re.I):
                continue
            return name
    return "other"


# --- what came of it -----------------------------------------------------------------------------------------------
_NOT_UNDERSTOOD = re.compile(r"couldn't work out|not sure what you meant|lost my train of thought|haven't started that|"
                             r"not sure that covers|caught a short phrase|cannot place it|say it another way|"
                             r"which (?:'?\w+'? )?(?:app|window|one)|what did you need\??$", re.I)
_FAILED = re.compile(r"didn't confirm|did not confirm|is still open|no visible app window|couldn't find|could not find|"
                     r"isn't in front|i couldn't|i could not|stopped without|it didn't|wasn't able|nothing called .* is running", re.I)
_DONE = re.compile(r"\b(?:was already open|opened|opening|launching|closed|minimi[sz]ed|focused|brought .{0,30}(?:front|forward)|launched|clicked|"
                   r"layers now showing|the globe is now centred|zoomed (?:in|out)|showing the whole world|bird'?s-eye view|"
                   r"world intelligence is (?:open|in front)|intelligence is active|the command center is (?:up|in front)|"
                   r"is up on your screen|is in front now|have reset|okay, i'll leave it|okay, i won't)\b|"
                   r"(?:^|\.\s)[\w' ]{1,40} (?:is|are) open\b", re.I)
_READING = re.compile(r"\d")
_ASKED = re.compile(r"^(?:should i|do you want|which|what (?:app|window)|shall i)\b.*\?\s*$", re.I)
_COMPLAINT = re.compile(r"\b(?:i (?:don'?t|do not|can'?t|cannot) see it|it'?s not\b|no,? it isn'?t|you closed it|"
                        r"that'?s (?:wrong|not what)|you'?re not (?:opening|doing)|you(?: are|'re)? opening my files|"
                        r"i said\b|i told you)\b", re.I)


# Done, per action asked for (act()): only the words the doing skill says, never a bare "Got it, sir." (core.followthrough).
# A protocol's run summary ("Gaming protocol: 4 of 5 steps done.") comes only from a run (protocols.run_text); judge() also
# accepts a run recorded in protocol-runs.json while the reply said nothing.
_SUMMARY = r"protocol(?: \([^)]*\))?: (?:\d+ of \d+ steps? done|the one step done|both steps done|all \d+ steps done)"
_DONE_ACT = {
    "run": re.compile(_SUMMARY + r"|protocol is already running", re.I),
    "stop": re.compile(r"putting everything back|protocol isn'?t running", re.I),
    "go": re.compile(r"\byou'?re live on\b", re.I),
    "end": re.compile(r"\bstream ended on\b|not live anywhere", re.I),
    "status": re.compile(r"\byou'?re (?:not )?live\b|\bnot live on\b", re.I),
    "volume": re.compile(r"\bvolume (?:is|is now|now at|set to|at|to)\b|\b(?:increased|decreased|raised|lowered|turned (?:up|down)|set|put|"
                         r"adjusted|changed) the volume\b|\bvolume (?:up|down)\b|\b(?:un)?muted\b|\d+ ?%", re.I),
    "pause": re.compile(r"\bpaused\b|\bhas stopped\b|\bstopped (?:the )?(?:video|music|song|playback)\b|\bturned (?:it |the \w+ )?off\b|"
                        r"sent play/pause", re.I),
    "play": re.compile(r"\b(?:is )?(?:now )?playing\b|\bresumed\b|\brestarted\b|\bturned (?:it |the \w+ )?on\b|sent play/pause", re.I),
    "skip": re.compile(r"\bskipped\b|\bnext (?:track|song|video)\b|\bnow playing\b|sent (?:next|previous)", re.I),
    "set": re.compile(r"\btimer\b.*\bi'?ll tell you\b|on the [\w ]{0,30}timer\b|\btimer (?:set|started|is set|is running)\b|"
                      r"\breminder (?:set|saved|added)\b|i'?ll remind you|\balarm (?:set|is set)\b|i'?ll wake you", re.I),
    "cancel": re.compile(r"\bcancel(?:l)?ed the\b|\b(?:reminder|timer|alarm)s? (?:is |are )?cancel(?:l)?ed\b|"
                         r"no (?:timers|reminders|alarms) (?:are running|set)|there'?s no timer like that", re.I),
    "write": re.compile(r"added to your calendar|\bmoved\b|\bremoved\b|\bdeleted\b|\bcancel(?:l)?ed\b|isn'?t on your calendar any more|"
                        r"i'?ve (?:added|put|moved)", re.I),
    "read": re.compile(r"nothing on your calendar|clear day|calendar is clear|\byou have\b|\bon your calendar\b|\d", re.I),
    "pair": re.compile(r"pairing code is|the code is\b|code is on (?:your|the)|pairing confirmed", re.I),
    "click": re.compile(r"\bclicked\b", re.I),
    "scroll": re.compile(r"\bscrolled\b", re.I),
    "type": re.compile(r"\btyped\b|\bpasted\b", re.I),
}
# A "done" word inside a not-done answer ("Nothing is playing", "You have to connect Google Calendar first", "not paired").
_NOT_DONE_ACT = {
    "play": re.compile(r"\bnothing(?:'s| is)? playing|\b(?:isn'?t|not|no longer) (?:\w+ )?playing|still paused", re.I),
    "skip": re.compile(r"\bnothing(?:'s| is)? playing|\b(?:isn'?t|not) playing", re.I),
    "read": re.compile(r"\bconnect|\blink (?:it|your)|\bnot (?:linked|connected|set up)|\bisn'?t (?:linked|connected|set up)|\bsign in|"
                       r"\bhave to\b|\bneed to\b|\bcan'?t (?:read|see|reach)", re.I),
    "pair": re.compile(r"\b(?:not|isn'?t|no longer|un)[- ]?paired|\bexpired\b", re.I),
}
_FAILED_ACT = re.compile(r"still (?:paused|playing)|didn'?t run|nothing went live|can'?t go live|can'?t reach obs|can'?t check|"
                         r"\bfailed\b|didn'?t start|still live", re.I)


def outcome(kind_: str, reply: str, said: str = "") -> str:
    """'done' | 'not_understood' | 'asked' | 'failed' | 'silent' | 'talk', read from what Jarvis said (`said`: his words,
    for the kinds judged by the action asked for)."""
    r = " ".join((reply or "").split())
    if not r:
        return "silent"
    if kind_ == "protocol" and re.search(_SUMMARY, r, re.I) and not re.search(r"\b0 of \d+ steps? done", r):
        return "done"        # a run's summary may list a failed step ("Failed: I couldn't ..."): it still ran
    if _NOT_UNDERSTOOD.search(r):
        return "not_understood"
    if _FAILED.search(r):
        return "failed"
    if _ASKED.match(r):
        return "asked"
    if kind_ in _ACT_KINDS:
        a = act(said, kind_) if said else ""
        a = {"end?": "end", "": kind_}.get(a, a.split(":")[0])
        rx, no = _DONE_ACT.get(a), _NOT_DONE_ACT.get(a)
        if rx is not None and rx.search(r) and not (no is not None and no.search(r)):
            return "done"
        return "failed" if _FAILED_ACT.search(r) else "talk"
    if kind_ in ("pc_question", "info"):
        if re.search(r"\bdon'?t (?:have|know)\b|\bno live\b|unavailable|\bi'?ll get\b", r, re.I):
            return "talk"
        return "done" if _READING.search(r) or re.search(r"\byou'?re in\b", r, re.I) else "talk"
    return "done" if _DONE.search(r) else "talk"


def misheard(said: str) -> bool:
    """A recognition slip we know of: core.heard would rewrite it, or a Command Center form that is not the words."""
    t = (said or "").strip()
    try:
        from core import heard, hud
        if heard.normalise(t) != t:
            return True
        if hud._cc_heard(t) and not re.search(r"command\s+cent(?:er|re)|\bhub\b", t, re.I):
            return True
    except Exception:  # noqa: BLE001
        pass
    if _stream_act(_LEAD.sub("", " ".join(t.split()))) == "end?":     # "And the stream." is Whisper's "End the stream."
        return True
    return bool(re.search(r"\b(?:minimi[sz]e|open|close|bring)\s+(?:cloud|clot|quad|clod)\b|return on satellite", t, re.I))


# --- where the turns are kept --------------------------------------------------------------------------------------
def _host() -> str:
    try:
        from core import pcbridge
        return "pc" if pcbridge.local_pc() else "server"
    except Exception:  # noqa: BLE001
        return "pc"


def _turns_path(host: str | None = None):
    from core import storage
    return storage.DATA / "learning" / f"core-turns-{host or _host()}.jsonl"


def note(uid: str, said: str, reply: str, channel: str, cut: bool = False, now: float | None = None) -> None:
    """One finished exchange of the owner's (core.thread.append, which already skips test turns). Never raises."""
    try:
        from core import turntrace, users
        if users.get(uid).get("role") != "owner" or turntrace.test_traffic():
            return
        said = " ".join((said or "").split())
        if not said or said.startswith("[Automatic"):
            return
        k = kind(said)
        row = {"ts": round(now if now is not None else time.time(), 1), "channel": str(channel)[:12], "said": said[:KEEP_SAID],
               "kind": k, "outcome": outcome(k, reply, said), "mishear": misheard(said), "reply_head": " ".join((reply or "").split())[:60]}
        a = act(said, k) if k in _ACTS else ""
        if a:
            row["act"] = a
        if cut:
            row["cut"] = True
        path = _turns_path()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as error:  # noqa: BLE001
        print(f"[scorecard] note: {type(error).__name__}: {error}")


def _read(path) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


_ACTS = ("protocol", "stream", "media", "reminder", "calendar", "ui")


def _rekind(row: dict) -> dict:
    """A row written before its kind existed (kind 'other' then, 2026-11-21) is read with today's kinds: its outcome from the
    reply's first 60 characters, which hold these short answers ("Gaming protocol: 4 of 5 steps done. ..."). Nothing is
    rewritten; the file stays as it was. A verdict the full reply gave ("I couldn't ...", "Which one?") is kept."""
    try:
        said = row.get("said")
        if row.get("kind") != "other" or not isinstance(said, str) or not said:
            return row
        k = kind(said)
        if k == "other":
            return row
        was = row.get("outcome")
        reply = str(row.get("reply_head") or "") if was != "silent" else ""
        out = dict(row, kind=k, outcome=was if was in ("not_understood", "failed", "asked") else outcome(k, reply, said),
                   rekinded=True)
        a = act(said, k) if k in _ACTS else ""
        if a:
            out["act"] = a
        return out
    except Exception:  # noqa: BLE001 - one odd row never hides the card
        return row


def _owner_uid() -> str | None:
    try:
        from core import users
        return next((u for u, d in users.all_users().items() if (d or {}).get("role") == "owner"), None)
    except Exception:  # noqa: BLE001
        return None


@contextlib.contextmanager
def _as_owner():
    """His protocols (core.protocols.find) are read as HIS, whoever asks for the card (the Hub request's thread)."""
    from core import users
    uid = _owner_uid()
    before = getattr(users._local, "uid", None)
    if uid:
        users._local.uid = uid
    try:
        yield
    finally:
        if before is None:
            users._local.__dict__.pop("uid", None)
        else:
            users._local.uid = before


def turns(hosts=("pc", "server")) -> list[dict]:
    out = []
    with _as_owner():
        for h in hosts:
            out += [dict(_rekind(r), host=h) for r in _read(_turns_path(h)) if isinstance(r, dict)]
    return sorted(out, key=lambda r: r.get("ts", 0))


def _timings(hosts=("pc", "server")) -> list[dict]:
    from core import storage
    rows = []
    for h in hosts:
        rows += [r for r in _read(storage.DATA / "learning" / f"turn-timing-{h}.jsonl") if not r.get("test")]
    return rows


def _latency(turn: dict, timings: list[dict]) -> float | None:
    """The turn-timing row for this turn: same words (80 chars), started up to 3 minutes before the reply was recorded."""
    said = turn.get("said", "")[:KEEP_SAID]
    best = None
    for r in timings:
        if r.get("said", "")[:KEEP_SAID] == said and 0 <= turn["ts"] - r.get("at", 0) <= 180:
            if best is None or r["at"] > best["at"]:
                best = r
    if best is None:
        return None
    st = best.get("stages") or {}
    return float(st.get("total") or best.get("total") or 0) or None


# --- the card --------------------------------------------------------------------------------------------------------
def _again_is_more(r: dict) -> bool:
    """Saying it again does more, not the same thing again: "Zoom in." twice, "Volume up." twice, "Next song." twice."""
    return (r["kind"] == "globe" or (r["kind"] == "media" and r.get("act") in ("volume", "skip"))
            or (r["kind"] == "ui" and r.get("act") == "scroll"))


def _same_ask(a: dict, b: dict) -> bool:
    if a["kind"] != b["kind"] or a["kind"] not in EVERYDAY:
        return False
    if a.get("act", "") != b.get("act", ""):
        return False          # "Pause the video." then "Play the video.", start then stop a protocol: two asks
    x, y = _LEAD.sub("", a["said"]).casefold(), _LEAD.sub("", b["said"]).casefold()
    # the new kinds carry their own words (a timer's length, a calendar entry, what to click): one word changed is a new ask
    need = 0.95 if a["kind"] in ("reminder", "calendar", "ui", "pair") else 0.8
    return difflib.SequenceMatcher(None, x, y).ratio() >= need


RUN_BEFORE_S = 60.0          # a protocol run began at most this long before its order's row was written (live 11-20: 21 s)
RUN_AFTER_S = 1.0           # 'started' is kept to the second (truncated); a row is written after its run began


def _owner_runs() -> tuple[list[tuple[str, float]], float]:
    """The owner's protocol runs (core.selfreview._runs: (name, started) and the moment the kept list reaches back to)."""
    try:
        from core import selfreview
        uid = _owner_uid()
        return selfreview._runs(uid) if uid else ([], float("inf"))
    except Exception as error:  # noqa: BLE001 - unknown: no order is credited by a run (the reply still may be)
        print(f"[scorecard] runs: {type(error).__name__}: {error}")
        return [], float("inf")


def _credit_runs(rows: list[dict], runs: list[tuple[str, float]], known: float) -> set[int]:
    """Indexes of protocol-run orders that a recorded run answered. Each run answers ONE order: the first order of that
    protocol whose turn ended after the run began ("I'm sorry Seth. Streaming Protocol." -> "Understood, sir" is not
    credited with the run that the NEXT order started). Orders older than the kept run list are left to the reply."""
    orders = [(i, r) for i, r in enumerate(rows) if r.get("kind") == "protocol" and str(r.get("act", "")).startswith("run:")]
    credited: set[int] = set()
    for name, at in sorted(runs, key=lambda x: x[1]):
        for i, r in orders:
            if i in credited or r["act"][4:] != name or r["ts"] < known:
                continue
            if at - RUN_AFTER_S <= r["ts"] <= at + RUN_BEFORE_S:
                credited.add(i)
                break
    return credited


def judge(rows: list[dict], runs: tuple[list[tuple[str, float]], float] | None = None) -> list[dict]:
    """Each row with first_try (True/False for an everyday command, None otherwise) and repeated/complained marks.
    A protocol order is done when its run was recorded (`runs`: (name, started) list and the time it is complete from;
    None: the owner's protocol-runs.json) or the reply is that run's own summary; "Got it, sir." alone is not done."""
    if runs is None and any(r.get("kind") == "protocol" for r in rows):
        runs = _owner_runs()
    credited = _credit_runs(rows, *runs) if runs else set()
    out = []
    for i, r in enumerate(rows):
        r = dict(r)
        if i in credited:
            r["outcome"], r["ran"] = "done", True
        later = [n for n in rows[i + 1:i + 4] if 0 < n["ts"] - r["ts"] <= REPEAT_S and n.get("channel") == r.get("channel")]
        r["repeated"] = bool(later) and not _again_is_more(r) and _same_ask(r, later[0])    # "Zoom in." twice is zooming more
        r["complained"] = bool(later) and bool(_COMPLAINT.search(later[0]["said"]))
        if r["kind"] in EVERYDAY:
            r["first_try"] = r["outcome"] == "done" and not r["repeated"] and not r["complained"]
        else:
            r["first_try"] = None
        out.append(r)
    return out


def _pct(values, p):
    v = sorted(x for x in values if x is not None)
    return round(v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))], 2) if v else None


def card(rows: list[dict], timings: list[dict] | None = None) -> dict:
    judged = judge(rows)
    cmds = [r for r in judged if r["first_try"] is not None]
    lat = [_latency(r, timings or []) for r in cmds] if timings is not None else []
    by_kind: dict = {}
    for r in cmds:
        k = by_kind.setdefault(r["kind"], [0, 0])
        k[0] += bool(r["first_try"])
        k[1] += 1
    ok = sum(bool(r["first_try"]) for r in cmds)
    return {
        "turns": len(judged), "commands": len(cmds), "first_try": [ok, len(cmds)],
        "rate": round(ok / len(cmds), 3) if cmds else None,
        "repeats": sum(r["repeated"] for r in judged),
        "mishears": sum(bool(r.get("mishear")) for r in judged),
        "not_understood": sum(r["outcome"] == "not_understood" for r in judged),
        "latency_p50": _pct(lat, .5), "latency_p90": _pct(lat, .9),
        "by_kind": dict(sorted(by_kind.items())),
        "missed": [{"said": r["said"], "kind": r["kind"], "why": ("repeated" if r["repeated"] else "complained" if r["complained"]
                                                                 else r["outcome"])} for r in cmds if not r["first_try"]][-8:],
    }


def daily(days: int = 7, now: float | None = None) -> dict:
    """{date: card} for the last `days` days with any of his turns, newest first; plus the replay's last score."""
    now = now if now is not None else time.time()
    rows, timings = turns(), _timings()
    out = {}
    for d in range(days):
        day = (datetime.fromtimestamp(now) - timedelta(days=d)).strftime("%Y-%m-%d")
        sel = [r for r in rows if datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d") == day]
        if sel:
            out[day] = card(sel, timings)
    return {"days": out, "replay": replay_score(), "target": 0.9}


def replay_score() -> dict | None:
    """The last core replay's summary (scripts/core_replay.py --save), or None."""
    from core import storage
    try:
        return json.loads((storage.DATA / "learning" / "core-replay-score.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None) -> int:
    """python -m core.scorecard [--backfill data/jarvis.log] [--days 7]: print his card (counts only, no words)."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", default="")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args(argv)
    if a.backfill:
        print(f"backfilled {backfill_from_log(a.backfill)} voice turns")
    d = daily(a.days)
    for day, c in d["days"].items():
        print(day, f"{c['first_try'][0]}/{c['first_try'][1]}", c["rate"], f"repeats {c['repeats']} misheard {c['mishears']} "
              f"not understood {c['not_understood']} wait p50 {c['latency_p50']} p90 {c['latency_p90']}", c["by_kind"])
    print("replay:", d["replay"])
    return 0


def backfill_from_log(log_path, hosts_timing=("pc",)) -> int:
    """One-time: his voice turns from data/jarvis.log (YOU: / JARVIS: lines) into core-turns-pc.jsonl, timestamped from
    the matching turn-timing row (lines without one are left out: no time, no day). Returns rows written."""
    from core import storage
    timing = [r for h in hosts_timing for r in _read(storage.DATA / "learning" / f"turn-timing-{h}.jsonl")
              if r.get("channel") == "voice" and not r.get("test")]
    have = {(round(r["ts"]), r["said"]) for r in turns(("pc",))}
    pairs, cur = [], None
    for line in open(log_path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("YOU: "):
            cur = [line[5:], []]
            pairs.append(cur)
        elif line.startswith("JARVIS: ") and cur is not None:
            cur[1].append(line[8:])
    used, written = set(), 0
    path = _turns_path("pc")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for said, reply in pairs:
            hit = next((r for r in timing if r.get("said", "")[:KEEP_SAID] == said[:KEEP_SAID] and id(r) not in used), None)
            if hit is None:
                continue
            used.add(id(hit))
            ts = round(hit["at"] + float(hit.get("total") or 0), 1)
            said_ = " ".join(said.split())
            if (round(ts), said_[:KEEP_SAID]) in have:
                continue
            k = kind(said_)
            text = " ".join(reply)
            row = {"ts": ts, "channel": "voice", "said": said_[:KEEP_SAID], "kind": k, "outcome": outcome(k, text, said_),
                   "mishear": misheard(said_), "reply_head": " ".join(text.split())[:60], "backfill": True}
            a = act(said_, k) if k in _ACTS else ""
            if a:
                row["act"] = a
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    return written


if __name__ == "__main__":
    raise SystemExit(main())
