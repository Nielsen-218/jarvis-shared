"""Graceful yield (2026-11-21): he starts again within the first seconds of a reply that answered half his thought, and the
whole thought is answered once; a real barge-in, an echo, or a reply that already acted stays its own turn.

Sequences are from data/jarvis.log 2026-11-15..21 (the cut-in lines and the turns around them).
"""
import time

import pytest

from core import followthrough as F
from core import selfreview, turntrace


@pytest.fixture(autouse=True)
def _clean_ledger():
    """Each test sees only its own spans (the ring is process-wide)."""
    with turntrace._lock:
        saved, turntrace._spans = turntrace._spans, type(turntrace._spans)(maxlen=turntrace._spans.maxlen)
        running, turntrace._open = turntrace._open, {}
    yield
    with turntrace._lock:
        turntrace._spans, turntrace._open = saved, running


def kept(text, *, played, first_done=False, since=None, his_voice=True, said="", heard=True, now=100.0, working=None):
    return F.keep_fragment(text, True, heard_any=heard, route="respond", items=[], taken=now - 2, played=played,
                           first_done=first_done, since=since, his_voice=his_voice, said=said, working=working)


def turn(previous, command, now=100.0, route="respond", items=()):
    return F.turn_words(previous, command, from_cut=True, route=route, items=list(items), now=now)


# -- his thought goes on: one turn -----------------------------------------------------------------------------------
@pytest.mark.parametrize("first, then, reply", [
    ("Right. Okay. So now.", "I need to know, did Apex get a new update?", "As I said, sir: I used search_internet."),
    ("That's right.", "Open world intelligence in its own window.", "I haven't started that, sir, and I won't pretend I have."),
    ("Doing good.", "I think Blade Runner is better than Alien. What do you think?", "I'm doing well, sir."),
    ("J. Jarvis.", "Turn on satellite.", "I haven't started that, sir."),
    ("To my mother's house, which is in Brentwood.", "And I was wondering where to make my stops for food.",
     "That's a long drive across the country."),
])
def test_his_continuation_within_the_first_seconds_is_one_turn(first, then, reply):
    words = turn(kept(first, played=0.9, said=reply), then)
    assert words == f"{first} {then}"


def test_only_while_the_first_sentence_still_plays():
    long_first = "As I said, sir: I used search_internet to look up today's news and answer your question about the tool."
    assert turn(kept("Right. Okay. So now.", played=2.8, said=long_first), "I need to know.") == \
        "Right. Okay. So now. I need to know."
    assert turn(kept("Right. Okay. So now.", played=2.8, first_done=True, said=long_first), "I need to know.") == "I need to know."


def test_a_whole_short_answer_heard_makes_his_words_a_new_turn():
    # review 2026-11-21: "What time is it?" "It's 3 pm." (1.2 s, heard whole) "And in Tokyo?" answers it, it does not continue it
    assert turn(kept("What time is it?", played=1.2, first_done=True, said="It's 3 pm."), "And in Tokyo?") == "And in Tokyo?"


def test_a_late_cut_is_his_answer_to_what_he_heard():
    # 2026-11-15: six sentences about Blender sessions, then "Well, how can I give you control over it?"
    late = kept("Why can't you work on the one I have open right now?", played=9.0, first_done=True,
                said="I cannot work on your existing file because I do not have direct control.")
    assert turn(late, "Well, how can I give you control over it?") == "Well, how can I give you control over it?"
    assert turn(kept("Doing good.", played=F.YIELD_S + 0.1, said="I'm doing well, sir."), "I think so.") == "I think so."


def test_unknown_timing_is_never_a_yield():
    assert F.carry("Be a partner.", "Thanks.", heard_any=True, age=1.0) == "Thanks."
    assert turn(kept("Doing good.", played=None), "I think so.") == "I think so."


# -- a real barge-in stays a new order -------------------------------------------------------------------------------
@pytest.mark.parametrize("cut_in", ["Stop.", "Stop, stop, stop. Don't listen to me.", "No, open Edge instead.",
                                    "No. Stop. Don't do anything.", "Wait, not that one.", "Actually, open Spotify.",
                                    "Hey Jarvis. Open World Intelligence.", "Jarvis, I need to know the latest news.",
                                    "Never mind.", "Cancel that.", "Hold up.", "Don't do that.", "Shut up.", "Enough.",
                                    "Sorry, I meant Friday.", "Attends.", "Tann.", "Kanpe."])
def test_a_barge_in_is_never_glued_on(cut_in):
    assert turn(kept("What's the weather like today?", played=0.5, said="It's sunny."), cut_in) == cut_in
    assert F.carry("What's the weather like today?", cut_in, heard_any=False, age=1.0) == cut_in   # before any sound too


def test_a_stop_word_route_is_never_carried():
    assert turn(kept("Tell me your joke.", played=0.4, said="Here's one."), "Stop.", route="stop") == "Stop."


# -- not him -------------------------------------------------------------------------------------------------------
def test_the_tv_or_my_own_echo_is_never_merged():
    # 2026-11-16: voice match 0.11 -> "5. Gigabytes in YouTube."
    assert turn(kept("How much RAM is free?", played=0.6, his_voice=False, said="8.6 GB of memory is free."),
                "5. Gigabytes in YouTube.") == "5. Gigabytes in YouTube."
    # "Yes, sir." is Jarvis heard as him: never the start of his message
    assert turn(kept("Yes, sir.", played=0.5, said="Got it."), "How much RAM is free?") == "How much RAM is free?"


# -- what already ran never runs again ---------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["Zoomed in.", "Closed notepad.", "Opened notepad.", "Done.",
                                  "The globe is now centred on Springfield."])
def test_a_reply_that_reports_an_action_is_not_yielded(said):
    assert turn(kept("Make it bigger.", played=0.6, said=said), "A bit more.") == "A bit more."


@pytest.mark.parametrize("first", ["Zoom in.", "Zoom out.", "Reset.", "Open Discord.", "Pause the music."])
def test_an_order_is_never_sent_twice(first):
    assert turn(kept(first, played=0.4, said="Right away, sir."), "and a bit more") == "and a bit more"


def test_a_reply_whose_worker_still_runs_is_not_yielded():
    # the cancelled reply may still start a tool: never asked again while it runs (review 2026-11-21)
    assert turn(kept("I'd like a character.", played=0.5, said="Sure.", working=lambda: True), "A humanoid one.") == "A humanoid one."
    assert turn(kept("I'd like a character.", played=0.5, said="Sure.", working=lambda: False), "A humanoid one.") ==         "I'd like a character. A humanoid one."


def test_a_reply_that_reports_done_when_the_check_fails_is_not_yielded(monkeypatch):
    from core import intelligence
    monkeypatch.setattr(intelligence, "claims_done", lambda s: 1 / 0)
    assert F.reports_done("Sure thing.") is True


def test_calendar_writes_and_protocol_steps_are_on_the_ledger():
    import inspect

    from core import calintent, protocols
    assert inspect.getsource(calintent.handle).count("with _acting(") == 3
    assert 'turntrace.timed("tool", f"protocol:' in inspect.getsource(protocols)


def test_a_tool_that_ran_during_the_reply_blocks_the_merge():
    since = time.time()
    turntrace.span("tool", "rig_my_character", since + 0.2, since + 0.5)
    assert turn(kept("I'd like a character.", played=0.5, since=since, said="One moment, sir."), "A humanoid one.") == \
        "A humanoid one."


def test_a_tool_still_running_blocks_the_merge():
    since = time.time()
    with turntrace.timed("pc", "open_app"):
        assert turntrace.acted_since(since)
        assert turn(kept("I'd like a character.", played=0.5, since=since, said="One moment, sir."), "A humanoid one.") == \
            "A humanoid one."


def test_a_reply_that_only_talked_is_merged():
    since = time.time() + 1000          # nothing has run after this moment
    turntrace.span("model", "fast", since + 1, since + 2)       # thinking is not acting
    assert not turntrace.acted_since(since)
    assert turn(kept("I'd like a character.", played=0.5, since=since, said="Sure."), "A humanoid one.") == \
        "I'd like a character. A humanoid one."


def test_an_older_tool_does_not_count():
    since = time.time()
    turntrace.span("tool", "search_internet", since - 30, since - 29)
    assert not turntrace.acted_since(since)


# -- the voice loop wires it -------------------------------------------------------------------------------------------
def test_main_passes_the_cut_timing_to_the_fragment():
    import inspect

    import main
    src = inspect.getsource(main.voice_loop)
    for needle in ("played=cut.get(\"played\")", "since=taken_wall", "his_voice=cut.get(\"his_voice\", True)",
                   "said=cut.get(\"said\", \"\"), working=cut.get(\"working\")", "began_at = monotonic() - len(interruption) * FRAME_SECS",
                   "cut = speaking.pop(\"cut\", None) or {}", "speaking[\"cut\"] = {\"played\": played"):
        assert needle in src, needle


def test_the_new_timing_line_leaves_selfreview_parsing_alone():
    assert selfreview._CUT.match("  (cut-in: 10 words, voice match 0.72, between sentences)")
    assert not selfreview._CUT.match("  (cut-in timing: 0.8s into my voice, first sentence not done)")
