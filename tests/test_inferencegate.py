"""His turn goes first on the one graphics card: background model work waits for a gap, never for a game, and is never
cut off once its call is running (core/inferencegate)."""
import threading
import time

import pytest

from core import inferencegate as gate


def _run_background(name, out, timeout=None, work=lambda slot: None):
    def job():
        with gate.background(name, timeout=timeout) as slot:
            out.append(("start" if slot else "gave up", name, time.monotonic()))
            if slot:
                work(slot)
                out.append(("end", name, time.monotonic()))
    t = threading.Thread(target=job, daemon=True)
    t.start()
    return t


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch):
    monkeypatch.setattr(gate, "POLL_S", 0.05)


def test_background_waits_while_a_turn_is_answered_and_runs_after():
    events = []
    with gate.foreground():
        t = _run_background("journal", events)
        time.sleep(0.3)
        assert events == []                              # nothing starts between the calls of a live turn
        assert gate.busy()
    t.join(2)
    assert [e[0] for e in events] == ["start", "end"] and not gate.busy()


def test_overlapping_turns_all_count():
    events = []
    with gate.foreground():
        with gate.foreground():                          # the phone and the voice at once
            t = _run_background("summary", events)
        time.sleep(0.2)
        assert events == []                              # one turn still running
    t.join(2)
    assert events and events[0][0] == "start"


def test_a_game_holds_background_work_until_it_ends(monkeypatch):
    playing = {"on": True}
    monkeypatch.setattr(gate, "GAMING", lambda: playing["on"])
    events = []
    t = _run_background("summary", events)
    time.sleep(0.3)
    assert events == []                                  # never adds load to a game
    playing["on"] = False
    t.join(2)
    assert [e[0] for e in events] == ["start", "end"]


def test_a_running_background_call_is_never_cut_off():
    """The turn that starts meanwhile does not wait at the gate (it waits for the call on the model lock); the job's
    call finishes, and only then does the job see that a turn began."""
    events, seen = [], {}
    inside, release = threading.Event(), threading.Event()

    def work(slot):
        inside.set()
        release.wait(2)                                  # "the model call"
        seen["cancelled_after_call"] = slot.cancelled()

    t = _run_background("journal", events, work=work)
    assert inside.wait(2)
    entered = time.monotonic()
    with gate.foreground():                              # his turn starts mid-call
        assert time.monotonic() - entered < 0.1          # the gate never makes a turn wait
        release.set()
        t.join(2)
    assert [e[0] for e in events] == ["start", "end"]    # the call ran to its end
    assert seen["cancelled_after_call"] is True          # ...and the job starts no further call during his turn


def test_a_wait_that_times_out_runs_nothing_and_leaves_the_slot_free():
    events = []
    with gate.foreground():
        t = _run_background("misslessons", events, timeout=0.2)
        t.join(2)
    assert [e[0] for e in events] == ["gave up"]
    assert gate.running() is None
    with gate.background("summary", timeout=0.5) as slot:
        assert slot and gate.running() == "summary"


def test_the_slot_is_released_even_when_the_job_fails():
    with pytest.raises(RuntimeError):
        with gate.background("journal", timeout=0.5) as slot:
            assert slot
            raise RuntimeError("model down")
    assert gate.running() is None
    with gate.background("summary", timeout=0.5) as slot:
        assert slot


def test_one_background_job_at_a_time():
    events = []
    first_in, first_out = threading.Event(), threading.Event()
    a = _run_background("journal", events, work=lambda slot: (first_in.set(), first_out.wait(2)))
    assert first_in.wait(2)
    b = _run_background("summary", events)
    time.sleep(0.2)
    assert [e[1] for e in events] == ["journal"]          # the second waits for the slot
    first_out.set()
    a.join(2)
    b.join(2)
    assert [(e[0], e[1]) for e in events] == [("start", "journal"), ("end", "journal"), ("start", "summary"),
                                              ("end", "summary")]


def test_the_wake_word_reserves_the_card_for_the_turn_he_is_speaking():
    events = []
    gate.reserve(5)
    t = _run_background("summary", events, timeout=3)
    time.sleep(0.2)
    assert events == []                                  # he is still talking
    with gate.foreground():
        pass                                             # the turn he spoke was answered: the reservation is used up
    t.join(2)
    assert events and events[0][0] == "start"
    gate.reserve(0.15)                                   # a false wake: the reservation simply runs out
    with gate.background("summary", timeout=2) as slot:
        assert slot


def test_a_turn_ending_never_uses_up_a_reservation_made_for_the_next_one():
    """A typed turn is running when he says the wake word: its end zeroed the reservation while he was still speaking,
    and the summary fold took the card first."""
    events = []
    with gate.foreground():                              # the typed (or companion) turn
        gate.reserve(5)                                  # the wake word, for the turn he is about to speak
    assert gate.busy()                                   # the turn that ended was not that one
    t = _run_background("summary", events, timeout=3)
    time.sleep(0.2)
    assert events == []
    with gate.foreground():
        pass                                             # his turn: now the reservation is used up
    t.join(2)
    assert events and events[0][0] == "start"
