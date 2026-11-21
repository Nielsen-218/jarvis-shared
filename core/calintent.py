"""Clear Google Calendar commands, run straight by the calendar skills instead of the model.

Live 2026-11-20 (sandbox, the one brain qwen3.5:9b, the calendar skills offered): "Put the gym on my calendar tomorrow at
6 pm." got "Got it, sir. I've added a gym session to your calendar" with NO call, and "Move my dentist appointment to
Wednesday at 4." invented an appointment "on Tuesday at 10:30". A spoken add/move/cancel whose day and time we can read
exactly is done here; anything else (no day, an odd date) goes to the model as before, which now has the skills and
whose done-claims are caught (intelligence._DONE).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_FILLER = r"^\W*(?:(?:so|well|okay|ok|and|also|hey jarvis|jarvis|please|um|uh|sir|can you|could you|would you|will you)[,.\s]+)*(?:please\s+)?"
_TAIL = r"(?:[,\s]+(?:sir|jarvis|please|for me|thanks|thank you))*\W*$"
_CAL = r"(?:my |the |our )?(?:google )?calendar"
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
           "december")
_MON = {m[:3]: i + 1 for i, m in enumerate(_MONTHS)}
_NOT_TIME = r"(?!\s*(?:[ap]\.?m\b|:\d|o'?clock))"
_ORD = r"(\d{1,2})(?:st|nd|rd|th)?" + _NOT_TIME

_DAY = re.compile(
    r"\b(?:(?P<rel>today|tonight|tomorrow(?: night| morning| afternoon| evening)?|the day after tomorrow)"
    r"|(?:on )?(?P<which>this |next |coming )?(?P<wd>" + "|".join(_DAYS) + r")s?"
    r"|(?:on )?(?P<mon>" + "|".join(m for m in _MONTHS) + r"|(?:jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?)\s+(?:the\s+)?" + _ORD +
    r"|(?:on )?the\s+(\d{1,2})(?:(?:st|nd|rd|th)|(?=\s+of\s))" + _NOT_TIME + r"(?:\s+of\s+(?P<mon2>" + "|".join(_MONTHS) + r"))?"
    r"|(?:on )?(?P<md_m>\d{1,2})/(?P<md_d>\d{1,2}))\b", re.I)
_TIME = re.compile(
    r"\b(?:(?:at|for|from|by)\s+)?(?:(?P<noon>noon|midday|midnight)|(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)?"
    r"(?=\W|$))(?:\s+(?P<part>in the morning|in the afternoon|in the evening|at night|tonight))?"
    r"(?:\s*(?:to|till|until|-|–)\s*(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?P<ap2>a\.?m\.?|p\.?m\.?)?)?", re.I)
_ALL_DAY = re.compile(r"\ball[- ]day\b", re.I)


def _next_weekday(today: date, name: str, which: str) -> date:
    ahead = (_DAYS.index(name.lower()) - today.weekday()) % 7
    if which.strip().lower() == "next" and ahead == 0:
        ahead = 7
    return today + timedelta(days=ahead)


def _date_of(m: re.Match, today: date) -> date | None:
    g = {k: (v or "") for k, v in m.groupdict().items()}
    rel = g["rel"].lower()
    if rel:
        if rel.startswith("today") or rel == "tonight":
            return today
        if rel.startswith("tomorrow"):
            return today + timedelta(days=1)
        return today + timedelta(days=2)
    if g["wd"]:
        return _next_weekday(today, g["wd"], g["which"])
    month = day = None
    if g["mon"]:
        month = _MON[g["mon"].lower().rstrip(".")[:3]]
        day = int(re.findall(r"\d+", m.group(0))[0])
    elif g["md_m"]:
        month, day = int(g["md_m"]), int(g["md_d"])
    else:  # "the 3rd" (of December)
        nums = re.findall(r"\d+", m.group(0))
        if not nums:
            return None
        day = int(nums[0])
        month = _MONTHS.index(g["mon2"].lower()) + 1 if g["mon2"] else today.month
        if not g["mon2"] and day < today.day:
            month = today.month % 12 + 1
    year = today.year
    try:
        when = date(year, month, day)
        if when < today:
            when = date(year + 1, month, day)
        return when
    except ValueError:
        return None


def _hour(h: int, ap: str, part: str, evening_day: bool) -> int | None:
    ap = ap.lower().replace(".", "")
    part = part.lower()
    if h > 23:
        return None
    if ap:
        if h > 12 or h == 0:
            return None
        return (h % 12) + (12 if ap == "pm" else 0)
    if h > 12:
        return h
    if any(w in part for w in ("afternoon", "evening", "night")) or evening_day:
        return h % 12 + 12 if h != 12 else 12
    if "morning" in part:
        return h % 12
    # no am/pm: 1-5 are afternoon ("dentist at 3"), 8-11 morning, 12 noon; a bare 6 or 7 is breakfast or dinner, so
    # it is not guessed (None: the model asks "morning or evening?")
    if h in (6, 7):
        return None
    return h + 12 if 1 <= h <= 5 else h


def parse_when(text: str, now: datetime) -> dict | None:
    """{'start': datetime|date, 'end': datetime|None, 'all_day': bool, 'span': (i, j) list} for the day/time in text, or
    None when no day is named exactly. A time with no day ('at 3') means today if still ahead, else None."""
    today = now.date()
    spans = []
    dm = _DAY.search(text)
    day = _date_of(dm, today) if dm else None
    if dm:
        spans.append(dm.span())
    evening = bool(dm and re.search(r"tonight|night|evening", dm.group(0), re.I))
    tm = None
    for cand in _TIME.finditer(text):
        if dm and dm.start() <= cand.start() < dm.end():
            continue  # the '3' of 'the 3rd' is a date, not a time
        if re.match(r"\s*for\b", cand.group(0), re.I) and not (cand.group("ap") or cand.group("m") or cand.group("noon")):
            continue  # "for 2 hours", "lunch for 2 people": a length or a count, not a time
        if (cand.group("noon") or cand.group("ap") or cand.group("ap2") or cand.group("m")
                or re.match(r"\s*(?:at|from|by)\b", cand.group(0), re.I)):
            tm = cand
            break
    if day is None and dm:
        return None
    if tm:
        spans.append(tm.span())
        if tm.group("noon"):
            h, mins = (0 if tm.group("noon").lower() == "midnight" else 12), 0
        else:
            h1, mins = int(tm.group("h")), int(tm.group("m") or 0)
            ap = tm.group("ap") or ""
            if not ap and tm.group("ap2") and h1 <= 12:   # "2 to 4 pm", "10 to 12 pm", "9 to 5 pm": the start's half of the day
                ap2 = tm.group("ap2").lower().replace(".", "")
                h2v = int(tm.group("h2"))
                if ap2 == "pm":
                    ap = "am" if h1 != 12 and (h2v == 12 or h1 > h2v) else "pm"   # "12 to 2 pm" is noon
                else:
                    ap = "am"
            h = _hour(h1, ap, tm.group("part") or "", evening)
            if h is None or mins > 59:
                return None
        if day is None:
            day = today if now.replace(hour=h, minute=mins) > now else None
            if day is None:
                return None
        start = datetime(day.year, day.month, day.day, h, mins)
        end = None
        if tm.group("h2"):
            ap2 = tm.group("ap2") or tm.group("ap") or ""
            if not ap2 and h >= 12:          # "from 2 to 4": the end is in the same half of the day as the start
                ap2 = "pm"
            h2 = _hour(int(tm.group("h2")), ap2, tm.group("part") or "", evening)
            if h2 is None and not ap2 and int(tm.group("h2")) in (6, 7):
                if h < 8:
                    return None                      # "from 5 to 7": not guessed; the model asks
                h2 = int(tm.group("h2")) + 12        # "from 9 to 6": a working day
            if h2 is not None:
                end = datetime(day.year, day.month, day.day, h2, int(tm.group("m2") or 0))
                if end <= start:
                    end = None
        return {"start": start, "end": end, "all_day": False, "spans": spans}
    if day is None:
        return None
    return {"start": datetime(day.year, day.month, day.day), "end": None, "all_day": True, "spans": spans}


def _strip(text: str, spans) -> str:
    merged = []
    for i, j in sorted(spans):
        if merged and i <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(j, merged[-1][1]))
        else:
            merged.append((i, j))
    for i, j in reversed(merged):
        text = text[:i] + " " + text[j:]
    text = re.sub(r"\b(?:on|at|for|from)\s*$", "", text.strip(), flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip(" ,.-")


def _title(raw: str) -> str:
    t = re.sub(r"^(?:an?|the|my|our|a new)\s+", "", raw.strip(), flags=re.I).strip(" ,.-")
    t = re.sub(r"^(?:event|appointment|meeting)\s+(?:for|called|named)\s+", "", t, flags=re.I)
    return t[:1].upper() + t[1:] if t else t


_ADD = re.compile(_FILLER + r"(?:put|add|schedule|book|block(?: off| out)?|pencil in|set up|create|make|mark)\s+(?P<a>.+?)\s+"
                  r"(?:on|to|in|into|onto)\s+" + _CAL + r"(?P<b>.*?)" + _TAIL, re.I)
_ADD2 = re.compile(_FILLER + r"(?:add|put)\s+(?:to|on|in)\s+" + _CAL + r"[:,]?\s+(?P<a>.+?)" + _TAIL, re.I)
_CANCEL = re.compile(_FILLER + r"(?:cancel|delete|remove|clear|take|drop)\s+(?P<a>.+?)(?:\s+(?:on|from|off|in|out of)\s+" + _CAL +
                     r"(?P<b>.*?))?" + _TAIL, re.I)
_MOVE = re.compile(_FILLER + r"(?:move|reschedule|push(?: back)?|bump|shift|change)\s+(?P<rest>.+?)" + _TAIL, re.I)
_SPLIT = re.compile(r"\s+(?:to|till|until)\s+", re.I)
_EVENTISH = re.compile(r"\b(?:appointment|meeting|event|calendar)\b", re.I)
# 4: never a pronoun or "everything" as the event: the model, which has the conversation, takes those
_VAGUE = {"it", "that", "this", "them", "those", "these", "everything", "all", "something", "anything", "one", "stuff",
          "all of them", "all of it", "the one", "that one", "this one"}
# 1: other skills' things ("cancel the timer for my meeting", "delete the note about the meeting") are never calendar events
_OTHER_THING = re.compile(r"\b(?:timers?|reminders?|alarms?|notes?|lists?|missions?|jobs?|downloads?|orders?|tasks?|"
                          r"subscriptions?|plans?|files?|emails?|messages?)\b", re.I)
_OWN_EVENT = re.compile(r"^(?:my|the|that|our|this)\s+(?:[\w'-]+\s+){0,2}(?:appointment|meeting)$", re.I)


def _vague(words: str) -> bool:
    w = re.sub(r"^(?:the|my|our|a|an)\s+", "", (words or "").strip().lower())
    return not w or w in _VAGUE


def handle(raw: str, now: datetime) -> str | None:
    """The spoken result of a clear calendar command, or None to let the model take the line."""
    from tools import gcalendar
    line = raw.strip().replace("’", "'")
    m = _ADD.match(line) or _ADD2.match(line)
    if m:
        both = (m.group("a") + " " + (m.groupdict().get("b") or "")).strip()
        when = parse_when(both, now)
        title = _title(_strip(both, when["spans"]) if when else m.group("a"))
        if not title or len(title) > 80 or _vague(title) or _OTHER_THING.search(title):
            return None
        if not when and (_DAY.search(both) or re.search(r"\b(?:at|from)\s+\d", both, re.I)):
            return None  # a day we can't read exactly ("the 31st" in November): the model asks
        if not when:
            return f"When should I put {title} on your calendar? Say the day and time, like 'Friday at 3 pm'."
        all_day = when["all_day"] or bool(_ALL_DAY.search(both))
        start = when["start"].date().isoformat() if all_day else when["start"].isoformat(timespec="minutes")
        end = when["end"].isoformat(timespec="minutes") if when["end"] else ""
        with _acting("add_calendar_event"):
            return gcalendar.add_calendar_event(re.sub(_ALL_DAY, "", title).strip(" ,") or title, start, end, all_day, "")
    m = _MOVE.match(line)
    if m and _EVENTISH.search(line) and not _OTHER_THING.search(line):
        rest = m.group("rest")
        what = new = None
        dated = first = None
        for cut in _SPLIT.finditer(rest):        # where the new time starts: the LAST " to " followed by a DAY and a time
            tail = rest[cut.end():]              # ("my trip to Boston meeting to Friday at 3"), not the "to" inside a
            cand = parse_when(tail, now)         # range ("to Friday from 2 to 4 pm"); with no day named, the first one
            if cand and not cand["all_day"]:
                first = first or (cut, cand)
                if _DAY.search(tail):
                    dated = (cut, cand)
        pick = dated or first
        if pick:
            what, new = rest[:pick[0].start()], pick[1]
        if new is None:
            return None   # no exact new time ("move it to Friday"): the model asks
        what = re.sub(r"\b(?:on|in)\s+" + _CAL, "", what, flags=re.I)
        old = parse_when(what, now)
        what_words = _strip(what, old["spans"]) if old else what.strip()
        if _vague(what_words):
            return None
        event_time = old["start"].isoformat(timespec="minutes") if old and _DAY.search(what) else ""
        end = new["end"].isoformat(timespec="minutes") if new["end"] else ""
        with _acting("move_calendar_event"):
            return gcalendar.move_calendar_event(what_words, new["start"].isoformat(timespec="minutes"), end, event_time)
    m = _CANCEL.match(line)
    if m and not _OTHER_THING.search(m.group("a")) and (m.group("b") is not None or _OWN_EVENT.match(m.group("a").strip())):
        what = m.group("a")
        rest = m.group("b") or ""
        when = parse_when(what + " " + rest, now)
        words = _strip(what, [s for s in (when["spans"] if when else []) if s[1] <= len(what)])
        if not words or _vague(words):
            return None
        event_time = when["start"].isoformat(timespec="minutes") if when else ""
        with _acting("cancel_calendar_event"):
            return gcalendar.cancel_calendar_event(words, event_time)
    return None


def _acting(name: str):
    """A calendar write, on the turn's ledger as a tool (core.turntrace.acted_since: a reply he cut off that already wrote it
    is never asked again by main.py's graceful yield)."""
    from core import turntrace
    return turntrace.timed("tool", name)
