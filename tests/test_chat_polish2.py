"""Chat polish 2 (live 2026-11-12, the owner's real background): small talk must not state what he is doing now from
background, must not end on a task prompt, and a decline gets a few words. Synthetic lines only."""
import re
from datetime import datetime, timedelta

import pytest

from core import chatturn, intelligence, speech


@pytest.mark.parametrize("sentence", [
    "What do you need right now?", "What is your priority right now?", "I will handle whatever you need me to do.",
    "What do you want me to do next?",
])
def test_task_prompt_endings_are_offers(sentence):
    assert speech.chat_offer(sentence, "hello")


@pytest.mark.parametrize("sentence", ["Which part went wrong?", "What do you need to hear?", "What do you want for dinner?"])
def test_real_questions_are_not_offers(sentence):
    assert not speech.chat_offer(sentence, "hello")


@pytest.mark.parametrize("sentence", [
    "I have been standing by while you handle your own work on Brightmoor.",
    "Just sitting here while the system hums along.", "It is quiet with your study session going on.",
    "I am simply waiting for whatever you need to say next.",
])
def test_present_claims_are_invented(sentence):
    assert intelligence.invents_present(sentence, "hey")


def test_present_claim_allowed_when_his_line_says_it():
    assert not intelligence.invents_present("Then the study session can wait.", "I have a study session tonight")


@pytest.mark.parametrize("line", ["I'm wiped out.", "Honestly I'm stressed out.", "Didn't sleep well.", "Too much going on.",
                                  "I'm so over today.", "Nope, nothing right now.", "Yeah, thanks for listening."])
def test_venting_and_declines_are_remarks(line):
    assert chatturn.remark(line)


@pytest.mark.parametrize("line", ["Turn it off.", "Play something.", "Open the game."])
def test_orders_are_not_remarks(line):
    assert not chatturn.remark(line)


NOW = datetime(2026, 11, 12, 20, 0)


def _background(then):
    when = then.strftime("%a %d %b %H:%M")
    return (f"x\n{intelligence._JOURNAL_KNOWN}\n- {when}: Worked on the rig. Still open: finish the weights; pick a date.\n"
            "Lessons you've learned:\n- be plain\n")


LONG = "Can you remind me what we worked on the other day with the rig?"


def test_journal_is_dropped_on_a_short_line():
    out = intelligence._past_journal(_background(NOW - timedelta(days=2)), "Hey Jarvis.", NOW)
    assert "Worked on the rig" not in out and "Lessons" in out


def test_journal_on_a_long_line_is_dated_relative_and_has_no_open_threads():
    out = intelligence._past_journal(_background(NOW - timedelta(days=2)), LONG, NOW)
    assert "Worked on the rig" in out and "2 days ago" in out and "Still open" not in out
    assert not re.search(r"\b\d\d:\d\d\b", out)


def test_december_line_read_in_january_is_last_year():
    new_year = datetime(2027, 1, 2, 9, 0)
    out = intelligence._past_journal(_background(datetime(2026, 12, 28, 21, 0)), LONG, new_year)
    assert "5 days ago" in out and "today" not in out


def test_days_count_calendar_days_not_24h_blocks():
    late = datetime(2026, 11, 12, 0, 30)
    out = intelligence._past_journal(_background(datetime(2026, 11, 11, 23, 50)), LONG, late)
    assert "yesterday" in out


@pytest.mark.parametrize("line", ["What did we do yesterday?", "What's still open?"])
def test_asking_about_the_past_keeps_the_journal_and_open_items(line):
    out = intelligence._past_journal(_background(NOW - timedelta(days=1)), line, NOW)
    assert "Worked on the rig" in out and "finish the weights" in out and "yesterday" in out


@pytest.mark.parametrize("sentence", [
    "I'm here to help with that: the fix is to restart it.", "I'm here to assist, and the answer is yes.",
    "What needs to be done first is the foundation.", "Anything you need to know is in the manual, I'll point you there.",
    "I can handle anything you throw at me, including that.", "What do you want me to say?",
])
def test_an_offer_shape_followed_by_content_is_kept(sentence):
    assert not speech.chat_offer(sentence, "hello")


@pytest.mark.parametrize("sentence", [
    "The system runs while you sleep.", "While the system runs the update, the fan will spin up.",
    "Plants are waiting for water while you travel.", "It's listening for the wake word while idle.",
    "Your training is in progress, as you said.",
])
def test_ordinary_explanations_are_not_invented_present(sentence):
    assert not intelligence.invents_present(sentence, "hey")


def _chat(text):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "model": "m"}


def test_a_chat_turn_is_never_failed_by_the_present_guard():
    reply = intelligence.parse_chat_reply(_chat("I have been standing by while you handle your own work."), "hey", "chat")
    assert "standing by" in reply.content[0].text


def test_machinery_still_fails_a_chat_turn():
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_chat_reply(_chat("<tool_call>{}</tool_call>"), "hey", "chat")


def _offer_history(question, line):
    return [{"role": "user", "content": "clean up the downloads"},
            {"role": "assistant", "content": question},
            {"role": "user", "content": line}]


@pytest.mark.parametrize("line", ["Nothing right now.", "I'm good.", "Nothing else.", "Not really."])
def test_a_decline_to_a_pending_question_stays_on_the_tool_path(line):
    assert chatturn.command_like(line, _offer_history("Shall I delete it?", line), [{"function": {"name": "delete_file"}}])


def test_the_same_decline_after_plain_chat_stays_chat():
    line = "Nothing right now."
    assert not chatturn.command_like(line, _offer_history("Quiet evening.", line), [{"function": {"name": "delete_file"}}])


def test_note_for_a_tired_line_keeps_task_prompts_out():
    note = chatturn.note("chat", [{"role": "user", "content": "Long one today, I'm tired and fed up honestly."}])
    assert "never ask what they need" in note
