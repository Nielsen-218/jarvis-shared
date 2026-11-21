"""One answer to "what part of the day is it", for the chat clock, the greeting check and the web pages."""
from datetime import datetime

import pytest

from core import chatturn, daypart


def at(hour, minute=0):
    return datetime(2026, 11, 13, hour, minute)


@pytest.mark.parametrize("hour, part", [(0, "night"), (4, "night"), (5, "morning"), (11, "morning"), (12, "afternoon"),
                                        (16, "afternoon"), (17, "evening"), (21, "evening"), (22, "night"), (23, "night")])
def test_the_bounds(hour, part):
    assert daypart.day_part(at(hour)) == part
    assert chatturn.day_part(at(hour)) == part, "the chat clock uses the shared helper"


@pytest.mark.parametrize("hour", [0, 1, 3, 4])
@pytest.mark.parametrize("word", ["morning", "afternoon", "evening", "night"])
def test_no_time_of_day_greeting_fits_in_the_small_hours(hour, word):
    assert not daypart.greeting_fits(word, at(hour, 12))


def test_the_late_evening_still_takes_evening_but_nothing_else():
    assert daypart.greeting_fits("Evening", at(22, 30)) and daypart.greeting_fits("evening", at(23))
    assert not daypart.greeting_fits("morning", at(23))


def test_a_greeting_at_04_12_is_never_good_evening():
    now = at(4, 12)
    assert chatturn.wrong_greeting("Good evening.", "Hey Jarvis.", now)
    assert chatturn.wrong_greeting("Good morning, sir.", "Hey Jarvis.", now)
    assert chatturn.right_greeting("Good evening.", "Hey Jarvis.", now) == "Hello."
    assert chatturn.right_greeting("Evening.", "Hey Jarvis.", now) == "Hello."
    assert chatturn.right_greeting("Good morning, sir. Quiet so far.", "Hey Jarvis.", now) == "Hello, sir. Quiet so far."


def test_his_own_good_evening_at_night_is_still_said_back():
    assert chatturn.right_greeting("Good evening.", "Good evening.", at(4, 12)) == "Good evening."


def test_the_night_note_names_no_time_of_day_to_greet_with():
    note = chatturn.clock_note(at(4, 12))
    assert "small hours" in note and "hello" in note


def test_the_web_pages_use_the_same_bounds():
    """The Python bounds are mirrored in the pages; this reads them so the copies cannot drift unnoticed."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    js = (root / "app/companion/behavior.js").read_text(encoding="utf-8")
    assert "hour >= 17 && hour < 22" in js and "hour >= 12 && hour < 17" in js and "hour >= 5 && hour < 12" in js
    pokes = (root / "app/companion/pokes.js").read_text(encoding="utf-8")
    assert 'h < 5 ? "night" : h < 12 ? "morning" : h < 17 ? "afternoon" : h < 22 ? "evening" : "night"' in pokes
    index = (root / "app/index.html").read_text(encoding="utf-8")
    assert "h < 5 ? " in index.split('$("#greet")')[1].split("\n")[0]


@pytest.mark.parametrize("hour, minute", [(2, 0), (23, 30)])
@pytest.mark.parametrize("reply", ["Have a good evening, sir.", "Have a good morning, sir.",
                                   "Sleep well, and have a good morning.", "Rest up. Have a good afternoon."])
def test_at_night_a_good_wish_mid_sentence_is_not_a_greeting_and_is_left_alone(reply, hour, minute):
    now = at(hour, minute)
    assert chatturn.right_greeting(reply, "", now) == reply
    assert "hello" not in chatturn.right_greeting(reply, "", now).lower()
    assert not chatturn.wrong_greeting(reply, "", now)


def test_at_night_a_greeting_that_opens_a_later_sentence_is_still_set_right():
    now = at(2, 0)
    assert chatturn.right_greeting("Still up, sir. Good morning to you.", "", now) == "Still up, sir. Hello to you."
    assert chatturn.wrong_greeting("Still up, sir. Good morning to you.", "", now)
