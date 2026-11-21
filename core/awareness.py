"""Daily Awareness (Issue #7): a rolling 24-hour, timestamped transcript you can ask about —
"Jarvis, what were we talking about at 8 this morning?"

Two sources, both transcribed locally and kept per person (data/users/<id>/):
  * conversations with Jarvis (voice on the PC, chat on the phone). They were addressed to Jarvis, so they're
    always in the 24-hour window (the journal already keeps summaries of them for good);
  * room listening — OPT-IN only: while a Daily Awareness session is on, through the PC microphone. Its state is
    visible and it can be paused or stopped any time. Only household voices are kept (voice ID); anyone else is
    dropped unless the session was started with "guests have agreed".
Nothing older than 24 hours is kept, unless the person says to keep a moment (it goes to their memory vault).

Each machine writes only its own file (awareness-pc.json / awareness-server.json) because data/ is shared by
Syncthing and two writers on one file would conflict; readers merge them. The session file is written only by
the PC, which owns the microphone.
"""
import re
import threading
import time
import uuid
from datetime import datetime, timedelta

from core import storage, users
from core import diag
from core.words import plural

RETENTION = 24 * 3600
MAX_ENTRIES = 6000                 # ~ a long, chatty day; oldest drop first
SESSION = "awareness_session.json"
_lock = threading.Lock()
_SECRET = re.compile(r"(password|passcode|api[ _-]?key|\btoken\b|secret|\b\d{12,19}\b|\bsk-[a-z0-9-]{10,})", re.I)
# What Whisper "hears" in near-silence or room noise; never worth a transcript line on its own.
_NOISE = re.compile(r"^\W*(thank you( very much)?|thanks( for watching)?|you|bye|okay|ok|hmm+|uh+|um+|so|yeah|"
                    r"\[?music\]?|\[?applause\]?|\.+|subtitles by .*)\W*$", re.I)


def _host() -> str:
    from core import pcbridge
    return "pc" if pcbridge.local_pc() else "server"


def _file(host: str | None = None) -> str:
    return f"awareness-{host or _host()}.json"


def _now() -> float:
    return time.time()


# -- the transcript --------------------------------------------------------------------------------
def add(who: str, text: str, source: str = "jarvis", t: float | None = None) -> dict | None:
    """Append one line for the current person (users.acting_as). source: 'jarvis' | 'room'."""
    text = " ".join(str(text or "").split())
    if not text or text.startswith("[ignore]") or (source == "room" and _NOISE.match(text)):
        return None
    if _SECRET.search(text):
        text = _SECRET.sub("[hidden]", text)
    entry = {"id": uuid.uuid4().hex[:10], "t": round(t or _now(), 1), "who": str(who)[:40], "text": text[:1000],
             "source": source}
    with _lock:
        rows = [r for r in storage.load(_file(), []) if r.get("t", 0) >= _now() - RETENTION]
        rows.append(entry)
        storage.save(_file(), rows[-MAX_ENTRIES:])
    return entry


def entries() -> list:
    """Everything from the last 24 hours for the current person, from every machine, oldest first."""
    cutoff = _now() - RETENTION
    rows = []
    for host in ("pc", "server"):
        rows += [r for r in storage.load(_file(host), []) if r.get("t", 0) >= cutoff]
    return sorted(rows, key=lambda r: r["t"])


def window(start: float, end: float) -> list:
    return [r for r in entries() if start <= r["t"] <= end]


def search(query: str, limit: int = 60) -> list:
    words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 1]
    if not words:
        return entries()[-limit:]
    hits = [r for r in entries() if all(w in (r["who"] + " " + r["text"]).lower() for w in words)]
    return hits[-limit:]


class ThreadNotForgotten(RuntimeError):
    """The Daily Awareness lines were deleted, but the same words could not be dropped from a machine's thread ledger
    (core.thread: the file stayed held by a reader): the forget is not done, and he must not be told it is.
    `deleted`: the awareness lines deleted all the same; `complete`: whether every machine was reached."""

    def __init__(self, message: str, deleted: int = 0, complete: bool = True):
        super().__init__(message)
        self.deleted, self.complete = deleted, complete


def delete(ids=None, minutes: float | None = None, everything: bool = False) -> int:
    """Delete lines from THIS machine's file (the API asks the other machine to do the same). Raises ThreadNotForgotten
    when the thread ledger kept them (asking again finishes it)."""
    with _lock:
        rows = storage.load(_file(), [])
        keep = rows
        if everything:
            keep = []
        elif minutes:
            keep = [r for r in rows if r.get("t", 0) < _now() - minutes * 60]
        elif ids:
            ids = set(ids)
            keep = [r for r in rows if r.get("id") not in ids]
        storage.save(_file(), keep)
    # The same words are in this machine's thread ledger (core.thread), which the other machine shows: forgotten there
    # too, chosen lines included (the awareness page's "delete these": the exchange that holds them goes).
    try:
        from core import thread
        if everything or minutes:
            thread.forget(minutes=minutes, everything=everything)
        elif ids:
            gone = [r for r in rows if r.get("id") in set(ids)]
            thread.forget_lines(lines=[(r.get("t"), r.get("text")) for r in gone])
    except Exception as error:  # noqa: BLE001 - never a silent miss: the other machine would keep showing those words
        diag.swallowed("thread forget", error)
        raise ThreadNotForgotten(f"the conversation thread kept those words ({type(error).__name__})",
                                 len(rows) - len(keep)) from error
    return len(rows) - len(keep)


def delete_answer(ids=None, minutes: float | None = None, everything: bool = False) -> dict:
    """delete() as the other machine's API answers it: {'deleted': n}, plus 'thread_kept' when its thread ledger kept the
    words (never an HTTP 500, which the asking machine could only take for 'offline')."""
    try:
        return {"deleted": delete(ids, minutes, everything)}
    except ThreadNotForgotten as kept:
        return {"deleted": kept.deleted, "thread_kept": True}


def delete_everywhere(ids=None, minutes: float | None = None, everything: bool = False) -> tuple[int, bool]:
    """Delete from this machine's file AND ask the other machine to delete from its own.
    Returns (lines deleted, whether the other machine was reached). Raises ThreadNotForgotten when either machine's
    thread ledger kept the words; the other machine is asked all the same, so its own lines go."""
    kept = None
    try:
        n = delete(ids, minutes, everything)
    except ThreadNotForgotten as error:
        n, kept = error.deleted, error
    body = {"ids": list(ids) if ids else None, "minutes": minutes, "everything": everything}
    reached, answer = True, {}
    try:
        if _host() == "server":
            from core import pcbridge
            if users.get(users.current()).get("role") == "owner":   # household members' lines only live on this server
                answer = pcbridge.json_request("/api/pc/awareness", "POST", {"op": "delete", **body})
        else:
            from tools import vault
            if vault._remote():                                      # else a single machine: nothing else holds a copy
                answer = vault._call("POST", "/api/awareness/delete", {**body, "local_only": True})
        n += int(answer.get("deleted", 0))
    except Exception:
        reached = False
    if kept is not None or answer.get("thread_kept"):
        raise ThreadNotForgotten(str(kept or "the other machine's conversation thread kept those words"), n, reached)
    return n, reached


def session_anywhere() -> dict:
    """The live room-listening session: the PC owns it; the server asks the PC, else reads the synced copy."""
    if _host() == "pc":
        return session()
    try:
        from core import pcbridge
        return pcbridge.json_request("/api/pc/awareness", "POST", {"op": "status"})["session"]
    except Exception:
        s = session()
        s["stale"] = True   # the PC is unreachable: this is the last state it synced
        return s


def control_anywhere(action: str, guests: bool = False, consent: bool = False) -> dict:
    if _host() == "pc":
        return set_session(action, guests, consent)
    from core import pcbridge
    return pcbridge.json_request("/api/pc/awareness", "POST",
                                 {"op": "session", "action": action, "guests": guests, "consent": consent})["session"]


def keep(ids) -> str:
    """Save chosen lines long-term in the person's memory vault (the only way anything outlives 24 h)."""
    chosen = [r for r in entries() if r["id"] in set(ids or [])]
    if not chosen:
        return "Nothing to keep."
    from tools import vault
    text = "\n".join(f"{_clock(r['t'])} {r['who']}: {r['text']}" for r in chosen)
    title = f"Conversation, {datetime.fromtimestamp(chosen[0]['t']).strftime('%a %d %b %H:%M')}"
    return vault.remember_note(text, title)


# -- the room-listening session (written by the PC only) ---------------------------------------------
def session() -> dict:
    s = storage.load(SESSION, None) or {"state": "off", "since": None, "guests": False, "periods": []}
    s["periods"] = [p for p in s.get("periods", []) if (p[1] or _now()) >= _now() - RETENTION]
    return s


def set_session(action: str, guests: bool = False, consent: bool = False) -> dict:
    """start (needs consent: everyone present knows Jarvis is listening) / pause / resume / stop."""
    s = session()
    now = round(_now(), 1)
    if action == "start":
        if not consent:
            raise ValueError("Confirm that everyone in the room knows Jarvis is listening before starting.")
        if s["state"] != "on":
            s["periods"].append([now, None])
        s.update(state="on", since=now, guests=bool(guests))
    elif action == "resume" and s["state"] == "paused":
        s["periods"].append([now, None])
        s.update(state="on", since=now)
    elif action in ("pause", "stop") and s["state"] in ("on", "paused"):
        if s["periods"] and s["periods"][-1][1] is None:
            s["periods"][-1][1] = now
        s.update(state="paused" if action == "pause" else "off", since=now)
        if action == "stop":
            s["guests"] = False
    storage.save(SESSION, s)
    return s


def listening_at(t: float, s: dict | None = None) -> bool:
    return any(a <= t <= (b or _now()) for a, b in (s or session())["periods"])


# -- recall ------------------------------------------------------------------------------------------
_PARTS = {"morning": (6, 12), "afternoon": (12, 17), "evening": (17, 22), "night": (20, 26), "tonight": (18, 26)}


def parse_when(text: str, now: datetime | None = None) -> tuple[float, float] | None:
    """'8', '8 am', '8:30 this morning', 'around 3pm', 'an hour ago', 'this morning', 'last night', 'noon'
    -> (start, end) epoch seconds inside the last 24 hours, or None."""
    now = now or datetime.fromtimestamp(_now())   # the module's clock, same as entries()/window()/retention
    s = (text or "").lower().strip()
    if not s:
        return None
    m = re.search(r"(\d+|an?|half an?)\s*(hour|hr|minute|min)s?\s+ago", s)
    if m:
        n = {"a": 1, "an": 1, "half a": 0.5, "half an": 0.5}.get(m[1], None) or float(m[1])
        delta = timedelta(hours=n) if m[2].startswith(("hour", "hr")) else timedelta(minutes=n)
        at = now - delta
        span = max(10, delta.total_seconds() / 60 * 0.25)
        return (at - timedelta(minutes=span)).timestamp(), (at + timedelta(minutes=span)).timestamp()
    m = re.search(r"\b(noon|midday|midnight)\b", s)
    hour = minute = None
    if m:
        hour, minute = (12 if m[1] in ("noon", "midday") else 0), 0
    else:
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?=\W|$)", s)
        if m and 0 <= int(m[1]) <= 23:
            hour, minute = int(m[1]), int(m[2] or 0)
            ampm = (m[3] or "").replace(".", "")
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            elif not ampm and hour < 12:   # "at 8" with no am/pm: the morning/afternoon/evening word decides,
                if re.search(r"\b(afternoon|evening|tonight|night)\b", s) or (                       # else the most
                        not re.search(r"\bmorning\b", s) and hour + 12 <= now.hour):                  # recent one
                    hour += 12
    if hour is not None:
        at = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        if at > now + timedelta(minutes=5):
            at -= timedelta(days=1)       # "at 11 pm" asked in the morning = last night
        return (at - timedelta(minutes=25)).timestamp(), (at + timedelta(minutes=25)).timestamp()
    for word, (a, b) in _PARTS.items():
        if re.search(rf"\b{word}\b", s):
            day = now - timedelta(days=1) if re.search(r"\b(last|yesterday)\b", s) or (word == "night" and now.hour < 12) else now
            start = day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=a)
            end = day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=b)
            return start.timestamp(), min(end, now).timestamp()
    return None


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M")


def recall(around: str = "", query: str = "", limit: int = 80, rng: tuple | None = None) -> str:
    """Grounded, timestamped lines for the brain to answer from — or an honest 'I don't have that'."""
    rng = rng or (parse_when(around) if around else None)
    if around and rng is None:
        return (f"I couldn't work out what time '{around}' means. Ask with a time like '8 am', '3:30 pm', "
                "'an hour ago' or 'this morning'.")
    rows = window(*rng) if rng else entries()
    if query:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        rows = [r for r in rows if any(w in (r["who"] + " " + r["text"]).lower() for w in words)] or (rows if rng else [])
    label = f"between {_clock(rng[0])} and {_clock(rng[1])}" if rng else ("matching that" if query else "in the last 24 hours")
    if not rows:
        oldest = entries()[:1]
        why = []
        if rng and rng[1] < _now() - RETENTION:
            why.append("that's more than 24 hours ago, and nothing that old is kept")
        elif rng and not listening_at((rng[0] + rng[1]) / 2):
            why.append("room listening (Daily Awareness) was off then, so only conversations with me would be here")
        if oldest:
            why.append(f"my 24-hour log starts at {_clock(oldest[0]['t'])}")
        return f"I don't have anything {label}" + (f" — {'; '.join(why)}" if why else "") + ". Don't guess."
    rows = rows[-limit:]
    lines = "\n".join(f"{_clock(r['t'])} {r['who']}{' (room)' if r['source'] == 'room' else ''}: {r['text']}" for r in rows)
    return (f"Transcript {label} ({plural(len(rows), 'line')}, times are local). Answer ONLY from these lines; say the times, "
            f"and say so if they don't answer the question:\n{lines}")


# "What were we just talking about?" / "what did I say at 8 this morning?" — asked about today, not last week.
_ASKS_ABOUT_TALK = re.compile(
    r"\b(what|who|which|when|remind me)\b.{0,60}?\b(talk(?:ing|ed)?|say(?:ing)?|said|discuss(?:ing|ed)?|chat(?:ting|ted)?|"
    r"decid(?:e|ed|ing)|mention(?:ed)?|told|tell(?:ing)?|ask(?:ed|ing)?)\b", re.I)
_TODAY_HINT = re.compile(r"\b(just|earlier|ago|today|this (?:morning|afternoon|evening)|tonight|last night|"
                         r"before|a (?:moment|minute|second|bit|while) ago|noon|midnight|\d{1,2}(?::\d{2})?\s*(?:a\.?m|p\.?m)|"
                         r"(?:at|around|about) \d{1,2})\b", re.I)
_LONG_AGO = re.compile(r"\b(yesterday|last (?:week|month|year)|days? ago|weeks? ago|months? ago)\b", re.I)


def context_for(text: str) -> str | None:
    """Recall lines to hand the brain BEFORE it answers a question about today's conversation, so any
    model tier answers from the real transcript instead of guessing or picking the wrong memory tool."""
    s = (text or "").strip()
    if not (_ASKS_ABOUT_TALK.search(s) and _TODAY_HINT.search(s)) or _LONG_AGO.search(s):
        return None
    rng = parse_when(s)
    if rng is None and re.search(r"\b(just|a (?:moment|second|bit) ago|before)\b", s, re.I):
        rng = (_now() - 30 * 60, _now())                                    # "just now" = the last half hour
    out = recall(rng=rng, limit=40)
    # The question itself may already be logged (the voice loop logs before answering): don't quote it back.
    tail = f": {s}"
    return "\n".join(line for line in out.split("\n") if not line.endswith(tail))


# -- room listening on the PC microphone ------------------------------------------------------------
class RoomListener:
    """While a session is on and Jarvis isn't mid-conversation, cut the microphone into utterances,
    transcribe them locally and keep household voices (voice ID). Runs on the PC only."""

    MIN_SECONDS, MAX_SECONDS, SILENCE_END = 0.7, 20.0, 0.8

    def __init__(self, listener, stt, voiceid, idle, owner: str = "owner"):
        import queue

        self.listener, self.stt, self.voiceid, self.idle, self.owner = listener, stt, voiceid, idle, owner
        self.q = queue.Queue(maxsize=600)
        self._vad = None
        self._check = 0.0
        self._on = self._guests = False

    def start(self) -> None:
        self.listener.aware_sink = self._feed
        threading.Thread(target=self._run, daemon=True, name="daily-awareness").start()

    def _feed(self, frame) -> None:  # called from the audio callback: must be instant and never raise
        if self._on:
            try:
                self.q.put_nowait(frame)
            except Exception:
                pass

    def _state(self) -> dict:
        with users.acting_as(self.owner):
            return session()

    def _run(self) -> None:
        import numpy as np
        from openwakeword.vad import VAD

        from audio.listener import FRAME_SECS

        self._vad = VAD()           # its own model: the main loop's VAD keeps state between frames
        frames, silent = [], 0.0
        while True:
            if time.monotonic() >= self._check:   # the phone/voice can change the session any time
                s = self._state()
                self._on, self._guests = s["state"] == "on", s.get("guests", False)
                self._check = time.monotonic() + 2.0
            if not self._on:
                frames, silent = [], 0.0
                time.sleep(0.5)
                continue
            try:
                frame = self.q.get(timeout=0.5)
            except Exception:
                continue
            if not self.idle():               # mid-conversation: those turns are logged as conversation
                frames, silent = [], 0.0
                continue
            speaking = self._vad.predict(frame, frame_size=640) >= 0.5
            if not frames and not speaking:
                continue
            frames.append(frame)
            silent = 0.0 if speaking else silent + FRAME_SECS
            seconds = len(frames) * FRAME_SECS
            if silent >= self.SILENCE_END or seconds >= self.MAX_SECONDS:
                audio = np.concatenate(frames)
                frames, silent = [], 0.0
                if seconds - self.SILENCE_END >= self.MIN_SECONDS:
                    self._keep(audio)

    def _who(self, audio) -> str | None:
        """Household member's name, 'Guest' (only if guests agreed), or None to drop. Read-only voice ID:
        no voiceprint adaptation from room audio."""
        v = self.voiceid
        if v is None or not v.enrolled:
            return "Someone"                   # can't tell voices apart: the session's consent covers the room
        try:
            from audio.voiceid import SHORT_DROP, SHORT_SECONDS, THRESHOLD
            e = v.embed(audio)
            scores = {uid: float(vec @ e) for uid, vec in v.prints.items()}
            uid = max(scores, key=scores.get)
            bar = v.thresholds.get(uid, THRESHOLD) - (SHORT_DROP if len(audio) / 16000 < SHORT_SECONDS else 0)
            if scores[uid] >= bar:
                return users.get(uid).get("name") or ("You" if uid == self.owner else uid)
        except Exception as error:
            diag.swallowed("voice id", error)
        return "Guest" if self._guests else None

    def _keep(self, audio) -> None:
        try:
            who = self._who(audio)
            if who is None:
                return                            # an unknown voice and no guest consent: not recorded
            text = self.stt.transcribe(audio)
            with users.acting_as(self.owner):
                add(who, text, "room")
        except Exception as error:
            print(f"[awareness] skipped an utterance: {type(error).__name__}: {error}")
