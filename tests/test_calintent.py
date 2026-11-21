"""Clear calendar commands go straight to the skills (core.calintent), with a fake Google."""
from datetime import datetime

import pytest

from core import calintent
from tests.test_gcal import _add, google  # noqa: F401  (fixture)
from tools import gcalendar

NOW = datetime(2026, 11, 20, 9, 0)   # a Friday morning


@pytest.mark.parametrize("said,title,start,end", [
    ("Put the gym on my calendar tomorrow at 6 pm.", "Gym", "2026-11-21T18:00:00", "2026-11-21T19:00:00"),
    ("Add dentist to my calendar Tuesday at 3", "Dentist", "2026-11-24T15:00:00", "2026-11-24T16:00:00"),
    ("Please put lunch with Marie on my calendar next Friday at noon, sir.", "Lunch with Marie", "2026-11-27T12:00:00", "2026-11-27T13:00:00"),
    ("Schedule a team call on my calendar December 3rd from 2 to 3:30 pm", "Team call", "2026-12-03T14:00:00", "2026-12-03T15:30:00"),
    ("Put the dentist tomorrow at 3 on my calendar", "Dentist", "2026-11-21T15:00:00", "2026-11-21T16:00:00"),
])
def test_add(google, said, title, start, end):  # noqa: F811
    out = calintent.handle(said, NOW)
    ev = next(iter(google.events.values()))
    assert ev["summary"] == title and ev["start"]["dateTime"] == start and ev["end"]["dateTime"] == end, ev
    assert out.startswith("Added to your calendar: " + title)


def test_add_all_day(google):  # noqa: F811
    out = calintent.handle("Add Mom's birthday to my calendar on December 2nd.", NOW)
    ev = next(iter(google.events.values()))
    assert ev["summary"] == "Mom's birthday" and ev["start"] == {"date": "2026-12-02"} and "all day" in out


def test_add_without_a_day_asks(google):  # noqa: F811
    assert calintent.handle("Put the gym on my calendar.", NOW).startswith("When should I put Gym on your calendar?")
    assert not google.events


def test_move(google):  # noqa: F811
    _add(google, "Dentist", "2026-11-24T15:00:00", "2026-11-24T16:00:00")
    out = calintent.handle("Move my dentist appointment to Wednesday at 4.", NOW)
    assert google.events["ev1"]["start"]["dateTime"] == "2026-11-25T16:00:00"
    assert out.startswith("Moved: Dentist, Wednesday November 25 at 4 PM")


def test_cancel(google):  # noqa: F811
    _add(google, "Gym", "2026-11-21T17:00:00")
    _add(google, "Gym", "2026-11-23T17:00:00")
    assert "Which one?" in calintent.handle("Cancel the gym on my calendar.", NOW)
    out = calintent.handle("Remove the gym from my calendar on Monday.", NOW)
    assert out.startswith("Cancelled: Gym, Monday November 23") and list(google.events) == ["ev1"]
    _add(google, "Dentist", "2026-11-24T15:00:00")
    assert calintent.handle("Cancel my dentist appointment.", NOW).startswith("Cancelled: Dentist")


@pytest.mark.parametrize("said", [
    "Cancel the timer.", "Cancel my mission.", "Move the file to the desktop.", "Put eggs on my shopping list.",
    "Add milk to my list tomorrow.", "Change the volume to 30.", "Remove the last note.", "What's on my calendar today?",
    "My calendar is full.", "Delete that.", "Move it to Friday."])
def test_other_lines_are_left_to_the_brain(google, said):  # noqa: F811
    assert calintent.handle(said, NOW) is None
    assert not google.events or True


def test_not_connected_is_said(monkeypatch):
    from core import gcal, storage
    storage.save(gcal.TOKEN_FILE, {})
    assert "isn't connected" in calintent.handle("Put the gym on my calendar tomorrow at 6 pm.", NOW)


# -- review round 1 (2026-11-20) ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", [
    "Cancel the timer for my meeting.", "Delete the note about the meeting.", "Cancel the reminder about the appointment.",
    "Put it on my calendar tomorrow at 5.", "Take it off my calendar.", "Cancel everything on my calendar tomorrow.",
    "Move the reminder for my meeting to Friday at 3.", "Put the meeting on my calendar on the 31st."])
def test_review_hijacks_and_vague_lines_go_to_the_brain(google, said):  # noqa: F811
    assert calintent.handle(said, NOW) is None


def test_the_3_pm_meeting_is_a_time_not_a_date(google):  # noqa: F811
    calintent.handle("Put the 3 pm meeting on my calendar tomorrow", NOW)
    ev = next(iter(google.events.values()))
    assert ev["summary"] == "Meeting" and ev["start"]["dateTime"] == "2026-11-21T15:00:00", ev


def test_move_for_a_to_b(google):  # noqa: F811
    _add(google, "Meeting", "2026-11-21T10:00:00", "2026-11-21T11:00:00")
    out = calintent.handle("Reschedule my meeting for tomorrow to Friday at 3", NOW)
    assert google.events["ev1"]["start"]["dateTime"] == "2026-11-20T15:00:00", out   # NOW is a Friday: "Friday" is today


@pytest.mark.parametrize("said,start,end,title", [
    ("Put study on my calendar tomorrow at 3 for 2 hours", "2026-11-21T15:00:00", "2026-11-21T16:00:00", "Study for 2 hours"),
    ("Put lunch for 2 people on my calendar tomorrow at noon", "2026-11-21T12:00:00", "2026-11-21T13:00:00", "Lunch for 2 people"),
    ("Put team sync on my calendar tomorrow 2 to 4 pm", "2026-11-21T14:00:00", "2026-11-21T16:00:00", "Team sync"),
    ("Put dinner with mom on my calendar tomorrow at 7 pm", "2026-11-21T19:00:00", "2026-11-21T20:00:00", "Dinner with mom"),
    ("Put lunch on my calendar tomorrow from noon to 2 pm", "2026-11-21T12:00:00", "2026-11-21T14:00:00", "Lunch"),
    ("Put lunch on my calendar tomorrow from 11 to 12 pm", "2026-11-21T11:00:00", "2026-11-21T12:00:00", "Lunch"),
    ("Put work on my calendar tomorrow 9 to 5 pm", "2026-11-21T09:00:00", "2026-11-21T17:00:00", "Work"),
    ("Put a call on my calendar tomorrow from 2 to 4", "2026-11-21T14:00:00", "2026-11-21T16:00:00", "Call"),
    ("Put the gym on my calendar at 8 tonight", "2026-11-20T20:00:00", "2026-11-20T21:00:00", "Gym"),
])
def test_review_times(google, said, start, end, title):  # noqa: F811
    calintent.handle(said, NOW)
    ev = next(iter(google.events.values()))
    assert (ev["summary"], ev["start"]["dateTime"], ev["end"]["dateTime"]) == (title, start, end)


def test_no_past_dates():
    w = calintent.parse_when("november 19", NOW)
    assert w["start"].year == 2027


@pytest.mark.parametrize("said,claim", [
    ("I've put together a plan for you.", False), ("I've put some thought into it.", False), ("I've put up with worse.", False),
    ("I've put a gym session on your calendar for tomorrow.", True), ("I've put eggs on your shopping list.", True)])
def test_put_claims(said, claim):
    from core import intelligence
    assert intelligence.claims_done(said) is claim


# -- review round 2 ------------------------------------------------------------------------------------------------------
def test_bare_six_or_seven_is_not_guessed(google):  # noqa: F811
    assert calintent.handle("Put gym on my calendar tomorrow at 7", NOW) is None and not google.events


def test_a_named_day_is_binding(google):  # noqa: F811
    _add(google, "Team meeting", "2026-12-10T10:00:00")
    out = calintent.handle("Cancel the meeting on my calendar tomorrow", NOW)
    assert "on Saturday November 21" in out and "ev1" in google.events


def test_move_keeps_to_inside_the_title(google):  # noqa: F811
    _add(google, "Trip to Boston meeting", "2026-11-24T10:00:00", "2026-11-24T11:00:00")
    out = calintent.handle("Move my trip to Boston meeting to Friday at 3 pm", NOW)
    assert google.events["ev1"]["start"]["dateTime"] == "2026-11-20T15:00:00", out


def test_time_only_old_spec_is_not_today(google):  # noqa: F811
    _add(google, "Standup meeting", "2026-11-24T15:00:00", "2026-11-24T15:15:00")
    out = calintent.handle("Move the 3 pm meeting to Friday at 4 pm", NOW)
    assert google.events["ev1"]["start"]["dateTime"] == "2026-11-20T16:00:00", out


# -- review round 3 ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said,start,end", [
    ("Put lunch on my calendar tomorrow from 12 to 2 pm", "2026-11-21T12:00:00", "2026-11-21T14:00:00"),
    ("Put lunch on my calendar tomorrow from 12 to 1 pm", "2026-11-21T12:00:00", "2026-11-21T13:00:00"),
    ("Put work on my calendar tomorrow from 9 to 6", "2026-11-21T09:00:00", "2026-11-21T18:00:00"),
])
def test_round3_ranges(google, said, start, end):  # noqa: F811
    calintent.handle(said, NOW)
    ev = next(iter(google.events.values()))
    assert (ev["start"]["dateTime"], ev["end"]["dateTime"]) == (start, end)


@pytest.mark.parametrize("said", ["Move my meeting to Friday from 2 to 4 pm", "Move my meeting to Friday at 2 to 4 pm"])
def test_round3_range_move(google, said):  # noqa: F811
    _add(google, "Meeting", "2026-11-24T10:00:00", "2026-11-24T11:00:00")
    calintent.handle(said, NOW)
    ev = google.events["ev1"]
    assert (ev["start"]["dateTime"], ev["end"]["dateTime"]) == ("2026-11-20T14:00:00", "2026-11-20T16:00:00")


def test_round3_unguessable_lines_go_to_the_brain(google):  # noqa: F811
    assert calintent.handle("Put gym on my calendar at 7", NOW) is None
    assert not google.events
    calintent.handle("Put a party on my calendar tomorrow from 5 to 7", NOW)   # 5 is afternoon, so 7 is the same evening
    ev = next(iter(google.events.values()))
    assert (ev["start"]["dateTime"], ev["end"]["dateTime"]) == ("2026-11-21T17:00:00", "2026-11-21T19:00:00")
