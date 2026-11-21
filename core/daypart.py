"""The part of the day, decided in ONE place.

morning 05:00-11:59, afternoon 12:00-16:59, evening 17:00-21:59, night 22:00-04:59. The same bounds are used by the
web pages (app/companion/pokes.js `daypart`, app/companion/behavior.js `timeGreeting`, app/index.html, app/dashboard.html,
hud/index.html) and tests/test_daypart.py pins them. Live 2026-11-13: a "Hey Jarvis." at 04:12 was answered "Good
evening." because night accepted "evening" at every hour; the small hours are no time of day to name in a greeting.
"""
from __future__ import annotations

MORNING_FROM, AFTERNOON_FROM, EVENING_FROM, NIGHT_FROM = 5, 12, 17, 22


def day_part(now) -> str:
    """morning, afternoon, evening or night, for a datetime."""
    hour = now.hour
    if MORNING_FROM <= hour < AFTERNOON_FROM:
        return "morning"
    if AFTERNOON_FROM <= hour < EVENING_FROM:
        return "afternoon"
    if EVENING_FROM <= hour < NIGHT_FROM:
        return "evening"
    return "night"


def small_hours(now) -> bool:
    """Midnight to five: no time-of-day word fits a greeting here."""
    return now.hour < MORNING_FROM


def greeting_fits(word: str, now) -> bool:
    """May a greeting name this part of the day now? At night only "evening" in the late evening (22:00-23:59) fits."""
    word = word.casefold()
    part = day_part(now)
    if part == "night":
        return word == "evening" and not small_hours(now)
    return word == part


def neutral_greeting(capitalised: bool = True) -> str:
    """The greeting that is right at any hour."""
    return "Hello" if capitalised else "hello"


def start_greeting(title: str, now) -> str:
    """What he says once when he starts: the hour's greeting and his title, nothing else. Until 2026-11-14 every start said
    "Good to see you, sir. All systems are online.", the same line up to ten times a day, in a voice (a system reporting
    for duty) the character sheet rules out (core/character.md)."""
    part = day_part(now)
    word = {"morning": "Good morning", "afternoon": "Good afternoon", "evening": "Good evening"}.get(part, "Hello")
    title = (title or "").strip()
    return f"{word}, {title}." if title else f"{word}."
