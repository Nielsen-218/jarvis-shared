"""Google Calendar skills: what's on the calendar, add an event, move one, cancel one.

The work is in core/gcal.py; the one-time sign-in is scripts/google_calendar_setup.py. These are NOT Jarvis's own
reminders (tools/proactive.py): a reminder is Jarvis speaking at a time, an event lives on his Google Calendar.
"""
import functools
from datetime import date, timedelta

from core import gcal, ownertime
from core.words import plural
from tools.registry import tool


def _spoken(fn):
    """Calendar trouble is said out loud, never raised: an odd answer from Google must not crash the turn."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        kwargs = {k: ("" if v is None else v) for k, v in kwargs.items()}  # a model sending null for an optional field
        try:
            return fn(*args, **kwargs)
        except gcal.CalendarError as e:
            return str(e)
        except (KeyError, ValueError, TypeError, AttributeError):
            return "Something went wrong with your Google Calendar. I'm not sure it went through; check the calendar."
    return wrapper


def _said(prefix: str, ev: dict) -> str:
    """The confirmation, AFTER the change went through: if the event can't be described, still say it was done."""
    try:
        return f"{prefix}: {gcal.describe(ev)}."
    except (gcal.CalendarError, KeyError, ValueError, TypeError, AttributeError):
        return f"{prefix}: {ev.get('summary') or 'the event'}." if isinstance(ev, dict) else f"{prefix}."


def _day(text: str, default: date) -> date:
    if not (text or "").strip():
        return default
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        raise gcal.CalendarError(f"I couldn't read the date '{text}'. Use a form like 2026-11-20.") from None


def _pick(what: str, event_time: str) -> dict | str:
    """The one event the owner means, or a spoken question when it isn't clear."""
    around = gcal.parse_local(event_time) if event_time.strip() else None
    hits = gcal.find(what, around=around)
    if around is not None:   # a day he named is binding: never act on a match from another day
        hits = [ev for ev in hits if gcal._event_start(ev)[0].date() == around.date()]
        if not hits:
            day = around.strftime("%A %B %d").replace(" 0", " ")
            return f"I don't see anything called '{what}' on your calendar on {day}."
    if not hits:
        return f"I don't see anything called '{what}' on your calendar in the next two months."
    if len(hits) > 1:
        options = "; ".join(gcal.describe(ev) for ev in hits[:5])
        return f"I found {plural(len(hits), 'match', 'matches')}: {options}. Which one?"
    return hits[0]


@tool(
    "Read the owner's Google Calendar: events between two dates (inclusive). Use for 'what's on my calendar today', "
    "'am I free Friday?', 'what do I have this week?', 'when is my dentist appointment?' (put the words in query and "
    "search ahead). Work out the dates from the background block's current date. Answer ONLY from the returned "
    "events; if it says nothing is scheduled, say so — never invent events.",
    {"from_date": {"type": "string", "description": "First day, ISO '2026-11-20'; '' = today"},
     "to_date": {"type": "string", "description": "Last day, ISO; '' = same as from_date"},
     "query": {"type": "string", "description": "Optional words to search for, e.g. 'dentist'; '' for everything"}},
    required=[], direct=True,
)
@_spoken
def calendar_events(from_date: str = "", to_date: str = "", query: str = "") -> str:
    try:
        today = ownertime.now().date()
        first = _day(from_date, today)
        last = _day(to_date, first)
        if query.strip() and not from_date.strip() and not to_date.strip():
            last = first + timedelta(days=90)  # "when is my dentist appointment?" = search ahead
        if last < first:
            first, last = last, first
        if (last - first).days > 366:
            return "That's more than a year of calendar; ask for a shorter stretch."
        start, _ = gcal.day_bounds(first)
        _, end = gcal.day_bounds(last)
        evs = gcal.events(start, end, query=query)
    except gcal.CalendarError as e:
        return str(e)
    span = ("today" if first == last == today else "tomorrow" if first == last == today + timedelta(days=1)
            else first.strftime("%A %B %d").replace(" 0", " ") if first == last
            else f"{first.strftime('%B %d').replace(' 0', ' ')} to {last.strftime('%B %d').replace(' 0', ' ')}")
    about = f" matching '{query.strip()}'" if query.strip() else ""
    if not evs:
        return f"Nothing on your calendar{about} {('for ' + span) if first != today or last != today else 'today'}."
    one_day = first == last
    body = "; ".join(gcal.describe(ev, with_day=not one_day) for ev in evs)
    return f"Your calendar{about}, {span}: {body}."


@tool(
    "Add an event to the owner's Google Calendar ('put dentist on my calendar Tuesday at 3', 'block Friday "
    "afternoon for the gym', 'add Mom's birthday on December 2'). This is the calendar, not a spoken reminder — use "
    "set_reminder when they want Jarvis to remind them. Work out exact local times from the background block. "
    "Leave end empty for a one-hour event. For a multi-day all-day event, end is the LAST day (inclusive).",
    {"title": {"type": "string", "description": "Event name, e.g. 'Dentist'"},
     "start": {"type": "string", "description": "Local start, ISO '2026-11-25T15:00' (date only '2026-12-02' for all-day)"},
     "end": {"type": "string", "description": "Local end, ISO; '' = one hour after start. All-day: the last day, inclusive ('' = one day)"},
     "all_day": {"type": "boolean"},
     "location": {"type": "string", "description": "Where, or ''"}},
    required=["title", "start"], direct=True,
)
@_spoken
def add_calendar_event(title: str, start: str, end: str = "", all_day: bool = False, location: str = "") -> str:
    if not title.strip():
        return "What should I call the event?"
    try:
        all_day = all_day or len(start.strip()) == 10
        begin = gcal.parse_local(start)
        finish = gcal.parse_local(end) if end.strip() else None
        if not all_day and begin < ownertime.now() - timedelta(minutes=5):
            return f"{begin.strftime('%A %B %d at %I:%M %p').replace(' 0', ' ')} has already passed; when should it be?"
        ev = gcal.add(title, begin, finish, all_day=all_day, location=location)
    except gcal.CalendarError as e:
        return str(e)
    return _said("Added to your calendar", ev)


@tool(
    "Move (reschedule) an event already on the owner's Google Calendar ('move my dentist appointment to Thursday "
    "at 4'). Give words from its title; event_time helps pick the right one when there are several. If several "
    "match, ask which one rather than guessing.",
    {"event": {"type": "string", "description": "Words from the event's title, e.g. 'dentist'"},
     "new_start": {"type": "string", "description": "New local start, ISO '2026-11-27T16:00'"},
     "new_end": {"type": "string", "description": "New local end, ISO; '' keeps the same length"},
     "event_time": {"type": "string", "description": "When the event is now, ISO, if they said; '' otherwise"}},
    required=["event", "new_start"], direct=True,
)
@_spoken
def move_calendar_event(event: str, new_start: str, new_end: str = "", event_time: str = "") -> str:
    try:
        ev = _pick(event, event_time)
        if isinstance(ev, str):
            return ev
        moved = gcal.move(ev, gcal.parse_local(new_start), gcal.parse_local(new_end) if new_end.strip() else None)
    except gcal.CalendarError as e:
        return str(e)
    return _said("Moved", moved)


@tool(
    "Cancel (delete) one event on the owner's Google Calendar ('cancel my dentist appointment', 'take the gym off "
    "Friday'). Give words from its title; event_time picks between several. If several match, ask which one — "
    "never delete more than one event per call and never guess.",
    {"event": {"type": "string", "description": "Words from the event's title"},
     "event_time": {"type": "string", "description": "When the event is, ISO, if they said; '' otherwise"}},
    required=["event"], direct=True,
)
@_spoken
def cancel_calendar_event(event: str, event_time: str = "") -> str:
    try:
        ev = _pick(event, event_time)
        if isinstance(ev, str):
            return ev
        gcal.cancel(ev)
    except gcal.CalendarError as e:
        return str(e)
    return _said("Cancelled", ev)


def today_summary() -> str:
    """One line for the morning briefing; '' when the calendar isn't connected or can't be reached."""
    if not gcal.connected():
        return ""
    try:
        start, end = gcal.day_bounds(ownertime.now().date())
        evs = gcal.events(start, end)
        return "Calendar today: " + ("; ".join(gcal.describe(ev, with_day=False) for ev in evs) or "nothing scheduled")
    except (gcal.CalendarError, KeyError, ValueError, TypeError, AttributeError):
        return "Calendar today: couldn't reach Google Calendar"
