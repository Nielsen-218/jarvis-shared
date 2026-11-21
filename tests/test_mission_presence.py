"""Owner 2026-11-10: "take away that if I'm at the PC rule - when I say I'm gonna game or I'm at the PC, stop anything that
will interfere, but if I don't say nothing and I'm opening browsers it's fine". His WORDS hold missions now."""
import time

import pytest

from core import missions, quick, users
from tools import missions as mtools


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MISSIONS_DIR", str(tmp_path / "missions"))
    monkeypatch.setattr(mtools, "_remote", lambda: False)
    monkeypatch.setattr(mtools, "_owner", lambda: True)
    users.set_current("owner")


def test_a_hold_stops_new_work_and_says_why():
    missions.add("report my disk space")
    missions.set_hold("you're gaming")
    assert missions.paused() and missions.hold_reason() == "you're gaming"
    assert missions.claim_next("pc") is None
    missions.clear_hold()
    assert not missions.paused() and missions.claim_next("pc")


def test_a_forgotten_hold_runs_out(monkeypatch):
    missions.set_hold("you're at the PC")
    later = time.time() + missions.HOLD_HOURS * 3600 + 5
    monkeypatch.setattr(missions.time, "time", lambda: later)
    assert missions.hold_reason() == "" and not missions.paused()


def test_resume_lifts_a_hold_too_and_the_pause_switch_is_separate():
    missions.set_hold("you're at the PC")
    assert not missions.paused_switch()
    missions.set_paused(False)
    assert not missions.paused()


@pytest.mark.parametrize("said, held", [
    ("I'm at the PC", "you're at the PC"),
    ("im using pc now", "you're at the PC"),
    ("Jarvis, I'm on the computer.", "you're at the PC"),
    ("I'm gonna game", "you're gaming"),
    ("I'm about to play a game", "you're gaming"),
    ("im gaming now", "you're gaming"),
    ("I’m going to play", "you're gaming"),
])
def test_saying_he_is_busy_holds_missions(said, held):
    reply = quick._mission_command(said)
    assert "wait until you tell me you're done" in reply
    assert missions.hold_reason() == held


@pytest.mark.parametrize("said", ["I'm done gaming", "im not at the pc", "I'm off the computer", "I'm away",
                                  "you can work now", "Jarvis, go ahead and work on the missions"])
def test_saying_he_is_done_lets_them_carry_on(said):
    missions.set_hold("you're gaming")
    assert "carry on" in quick._mission_command(said)
    assert missions.hold_reason() == ""


@pytest.mark.parametrize("said", ["what are you doing at the PC", "I'm at the store", "I'm going to bed",
                                  "I'm playing music", "I'm done", "I'm back", "open the PC settings", "I'm done with the report"])
def test_other_sentences_are_not_presence(said):
    assert quick._presence(said) is None
    assert missions.hold_reason() == ""


def test_status_says_missions_are_on_hold_and_why():
    missions.add("report my disk space")
    missions.set_hold("you're gaming")
    assert "on hold because you're gaming" in mtools.mission_status()


def test_the_reply_says_how_long_a_hold_lasts():
    """Review 2026-11-10: 'until you tell me you're done' was not true - a forgotten hold ends after HOLD_HOURS."""
    reply = quick._mission_command("I'm gonna game")
    assert "until you tell me you're done" in reply and "at most" in reply


def test_done_gaming_while_paused_says_they_are_still_paused():
    missions.set_paused(True)
    quick._mission_command("I'm gonna game")
    reply = quick._mission_command("I'm done gaming")
    assert "still paused" in reply and "carry on in the background" not in reply
    assert missions.hold_reason() == "" and missions.paused()


def test_a_household_member_saying_it_is_ordinary_conversation(monkeypatch):
    monkeypatch.setattr(mtools, "_owner", lambda: False)
    assert quick._presence("I'm gonna play a game") is None
    assert missions.hold_reason() == ""


def test_presence_is_not_a_model_tool():
    """Only his exact statements hold missions; the brain can never call it on a loosely related sentence."""
    from tools import registry
    assert "owner_presence" not in registry.names()


def test_a_malformed_hold_row_is_no_hold_and_no_error():
    with missions.database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES('hold', ?)", ('["not", "a", "dict"]',))
    assert missions.hold_reason() == "" and not missions.paused()
    with missions.database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES('hold', ?)", ('{"reason": "x", "until": "soon"}',))
    assert missions.hold_reason() == "" and not missions.paused()


def test_a_bad_hold_hours_setting_falls_back(monkeypatch):
    monkeypatch.setenv("JARVIS_MISSION_HOLD_HOURS", "4h")
    assert missions._hold_hours() == 4.0
