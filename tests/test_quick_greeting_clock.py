"""Time and date quick answers in four languages: a greeting, a name or Spanish question marks in front do not stop them,
and the spoken wording is right for pinned clock values (no real clock)."""
from datetime import datetime

import pytest

from core import quick


class _Clock(datetime):
    pinned = datetime(2026, 11, 12, 9, 5)

    @classmethod
    def now(cls, tz=None):
        return cls.pinned


@pytest.fixture
def at(monkeypatch):
    monkeypatch.setattr(quick, "datetime", _Clock)

    def set_time(hour, minute, day=(2026, 11, 12)):
        _Clock.pinned = datetime(*day, hour, minute)
    return set_time


@pytest.mark.parametrize("line", [
    "Hola, ¿qué hora es?", "¿Qué hora es?", "Hola Jarvis, ¿qué horas son?", "Oye, ¿qué hora es, por favor?", "Hey Jarvis, ¿qué hora es?",
])
def test_spanish_time_survives_greeting_and_inverted_marks(at, line):
    at(15, 51)
    assert quick.handle(line) == "Son las 3:51 de la tarde."


@pytest.mark.parametrize("line", ["Bonjour, quelle heure est-il ?", "Bonjour Jarvis, quelle heure est-il", "Salut, tu as l'heure ?",
                                  "Hey Jarvis, quelle heure est-il ?"])
def test_french_time_survives_greeting(at, line):
    at(15, 5)
    assert quick.handle(line) == "Il est 15 h 05."


@pytest.mark.parametrize("line", ["Bonjou, ki lè li ye?", "Bonjou Jarvis, ki lè li ye", "Hey Jarvis, ki lè li ye?", "Bonswa, kilè li ye"])
def test_creole_time_survives_greeting(at, line):
    at(15, 20)
    assert quick.handle(line) == "Li twazè ven nan apremidi."


@pytest.mark.parametrize("line", ["Hello, what time is it?", "Hi Jarvis, what's the time?", "Hey Jarvis, what time is it?"])
def test_english_time_survives_greeting(at, line):
    at(15, 5)
    assert quick.handle(line) == "It's 3:05 PM."


def test_dates_survive_greeting_in_every_language(at):
    at(8, 0)                                   # Thursday 12 November 2026
    assert quick.handle("Hola, ¿qué día es hoy?") == "Hoy es jueves 12 de noviembre."
    assert quick.handle("Bonjour, quel jour sommes-nous ?") == "Nous sommes le jeudi 12 novembre."
    assert quick.handle("Bonjou, ki dat jodi a ye?") == "Jodi a se jedi douz novanm."
    assert quick.handle("Hello, what's the date?") == "It's Thursday, November 12."


def test_ordinary_sentences_with_a_greeting_are_not_taken(at):
    at(8, 0)
    assert quick.handle("Hola, ¿a qué hora es la película?") is None
    assert quick.handle("Hello, what time is the game?") is None


@pytest.mark.parametrize("hour, minute, said", [
    (12, 0, "Es mediodía."), (0, 0, "Es medianoche."), (13, 5, "Es la 1:05 de la tarde."), (0, 51, "Son las 12:51 de la madrugada."),
    (9, 51, "Son las 9:51 de la mañana."), (21, 30, "Son las 9:30 de la noche."), (12, 51, "Son las 12:51 de la tarde."),
])
def test_spanish_clock_wording(hour, minute, said):
    assert quick.clock_in("es", datetime(2026, 11, 12, hour, minute)) == said


@pytest.mark.parametrize("hour, minute, said", [
    (12, 0, "Il est midi."), (0, 0, "Il est minuit."), (1, 0, "Il est 1 heure."), (14, 0, "Il est 14 heures."),
    (9, 51, "Il est 9 h 51."), (17, 5, "Il est 17 h 05."), (0, 51, "Il est 0 h 51."),
])
def test_french_clock_wording(hour, minute, said):
    assert quick.clock_in("fr", datetime(2026, 11, 12, hour, minute)) == said


@pytest.mark.parametrize("hour, minute, said", [
    (12, 0, "Li midi."), (0, 0, "Li minwi."), (9, 51, "Li nevè senkanteyen nan maten."), (15, 5, "Li twazè senk nan apremidi."),
    (18, 30, "Li sizè edmi nan aswè."), (12, 5, "Li midi senk."),
])
def test_creole_clock_wording(hour, minute, said):
    assert quick.clock_in("ht", datetime(2026, 11, 12, hour, minute)) == said
