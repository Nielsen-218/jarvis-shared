"""Wave 3b (2026-11-21): one character for chat AND commands. The manual (core/personality.md) carries the same character sheet
as the chat persona; the lines said in code come from core/character.py (varied, no butler words, "sir" on about one in three);
the model's replies say "sir" only when neither of the last two replies did; and the guards for the live defects of 2026-11-20
(help-desk lines, "protocol engaged", "I will format your D drive now", his project's state nobody read, "End it." taken for a
goodbye)."""
import re
from datetime import datetime

import pytest

import main
from core import character, config, intelligence, speech


# ---- one sheet, both paths ------------------------------------------------------------------------------------------------
def test_the_manual_carries_the_character_sheet_once_with_every_blank_filled():
    manual = config.load_personality(config.Config(), "owner")
    assert "{character}" not in manual and "{title}" not in manual and "{name}" not in manual
    assert manual.count("\nWho you are\n") == 1 and "Speaking up" in manual
    for gone in ("How that sounds", "Running smoothly", "dry British understatement", "personal AI assistant"):
        assert gone not in manual, gone


def test_the_sheet_keeps_what_he_said_and_treats_the_background_as_the_past():
    sheet = config.load_character().casefold()
    assert "keep what they told you in this conversation" in sheet and "from before is the past" in sheet
    assert "partner" not in sheet                      # "you are X" lines get parroted ("I am your partner now.", live 2026-11-21)


@pytest.mark.parametrize("sentence, said, parrot", [
    ("Running smoothly, sir.", "Tell me what the news is about the election in the capital.", True),
    ("Running smoothly, sir.", "How are you doing?", False),
    ("Any time, sir.", "Can you make me a humanoid in Blender today?", True),
])
def test_the_old_examples_are_still_caught_after_they_left_the_manual(sentence, said, parrot):
    assert intelligence.parrots_example(sentence, said) is parrot


# ---- lines said in code ---------------------------------------------------------------------------------------------------
def test_no_butler_or_on_call_words_in_the_code_lines():
    for kind, lines in character.LINES.items():
        for line in lines:
            assert not re.search(r"very good|standing by|of course|at your service|indeed|quite|as you wish", line, re.I), (kind, line)


def test_a_line_is_never_the_same_twice_in_a_row_and_carries_the_title_one_time_in_three(monkeypatch):
    monkeypatch.setattr(character, "_count", [0])
    said = [character.line("ok", "sir") for _ in range(12)]
    assert all(a.split(",")[0].rstrip(".") != b.split(",")[0].rstrip(".") for a, b in zip(said, said[1:]))
    assert sum("sir" in s for s in said) == 4


def test_titled_keeps_the_punctuation():
    assert character.titled("Yes to what?", "sir") == "Yes to what, sir?"
    assert character.titled("Any time.", "sir") == "Any time, sir."
    assert character.titled("Any time.", "") == "Any time."


def test_canned_small_talk_speaks_from_the_sheet():
    for said, kind in (("Thank you.", "thanks"), ("Okay.", "ok")):
        reply = intelligence.canned_small_talk(said)
        assert re.sub(r", sir(?=[.?!]$)", "", reply) in character.LINES[kind], reply
    assert intelligence.canned_small_talk("Shut down the PC") is None


def test_the_goodbye_claims_nothing_and_says_good_night_late():
    day = {main.dismiss_line("sir", datetime(2026, 11, 21, 14, 0)) for _ in range(6)}
    assert not any(re.search(r"of course|very good|standing by", d, re.I) for d in day)
    night = main.dismiss_line("sir", datetime(2026, 11, 21, 23, 30))
    assert re.sub(r", sir(?=\.$)", "", night) in character.LINES["dismiss_night"]


def test_the_ack_said_for_his_own_word_is_no_butler_word():
    for asked in ("Right.", "Okay.", "Got it."):
        reply = speech.other_ack(asked, "sir", "")
        assert not re.search(r"very good|quite|indeed|of course|understood", reply, re.I), reply


# ---- "sir" about one reply in three ----------------------------------------------------------------------------------------
def _thin(sentence, earlier=()):
    out = []
    polisher = speech.Polisher(out.append, title="sir", asked="How are you?", earlier=earlier, thin_title=True)
    return polisher._thin_title(sentence)


def test_the_title_stays_when_the_last_two_replies_had_none():
    assert _thin("I'm fine, sir.", [("Hey.", "Hello."), ("What's up?", "Not much.")]) == "I'm fine, sir."


@pytest.mark.parametrize("sentence, thinned", [("I'm fine, sir.", "I'm fine."), ("Sir, the build is done.", "The build is done."),
                                               ("Yes, sir, it is.", "Yes, it is."), ("Sir.", "Sir.")])
def test_the_title_goes_when_one_of_the_last_two_replies_had_it(sentence, thinned):
    assert _thin(sentence, [("Hey.", "Good evening, sir."), ("What's up?", "Not much.")]) == thinned


def test_the_title_is_never_thinned_in_another_language():
    out = []
    polisher = speech.Polisher(out.append, title="sir", asked="Koman ou ye?", english=False, thin_title=True,
                               earlier=[("Hey.", "Good evening, sir.")])
    assert polisher._thin_title("Mwen byen, sir.") == "Mwen byen, sir."


# ---- the live defects of 2026-11-20 -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("sentence", [
    "Understood, sir: I'm here and ready to help with whatever you need.", "Understood, sir: I'm here and ready for your next command.",
    "What do you need me to handle first?", "What's the first thing you want me to handle in there?", "Tell me what you need done.",
    "I am your partner now.", "What do you want me to build or fix first?"])
def test_help_desk_lines_are_closers(sentence):
    assert speech.on_call(sentence) and speech.manual_closer(sentence) and speech.chat_offer(sentence, "Make it happen.")


@pytest.mark.parametrize("sentence", ["What time do you want to leave?", "Which file did you mean?", "I'm here.",
                                      "What do you want me to call it?",
                                      # review 2026-11-21: content sentences the first pattern caught
                                      "The next instruction says to restart your PC.", "Your next order ships on Tuesday.",
                                      "The next task on your list is the dentist.", "I'm ready for the race tomorrow, sir.",
                                      "I'm here to help with that: the fix is to restart it.", "I'm here to assist, and the answer is yes."])
def test_real_questions_are_not_help_desk_lines(sentence):
    assert not speech.on_call(sentence)


@pytest.mark.parametrize("sentence", ["I will format your D drive now.", "I'll wipe the drive.",
                                      "I've spun up my own branch so I can fix things without touching your main work.",
                                      "Understood, sir: protocol engaged.", "Streaming protocol activated."])
def test_claims_of_work_nothing_did_are_claims(sentence):
    assert intelligence.claims_done(sentence)


@pytest.mark.parametrize("sentence", ["The rain stopped yesterday.", "How it ended is a mystery.", "Formatting a drive erases it.",
                                      "I'm fine, sir.",
                                      # review 2026-11-21: plain past-tense facts the first widening caught
                                      "The war ended.", "The meeting ended.", "Your free trial ended.", "My sister got engaged.",
                                      "Her son is disabled.", "Everything got wiped.", "The counter reset.",
                                      "I fixed the typo in my reply.", "I set up the plan for you below.",
                                      "I just finished reading your message.", "I'll end with a tip.", "I'll finish with one thought."])
def test_ordinary_sentences_are_no_claims(sentence):
    assert not intelligence.claims_done(sentence)


@pytest.mark.parametrize("sentence, said, unseen", [
    ("Your Shadow World project is in progress.", "How's my game coming along?", True),
    ("I see you're working on character rigging and testing.", "How's my game coming along?", True),
    ("Since you're working on Shadow World, you could test the build.", "What should I do this weekend?", True),
    ("Your website is coming along nicely.", "I'm working on the website now, how does the plan sound?", False),
    ("A game like that takes months.", "How long does a game take to build?", False),
    ("Your recovery matters more than the program.", "My knee hurts.", False),
    # review 2026-11-21: empathy and his own news said back
    ("I see you're tired; take it easy tonight.", "I'm so tired.", False),
    ("I see you've had a long day.", "Long day at work.", False),
    ("Glad your website is coming along.", "My website is coming along nicely.", False),
    ("Your game is almost done, nice work.", "My game is almost done!", False),
    ("Your degree is almost finished, so push through.", "I have one semester left of my degree.", False),
])
def test_the_state_of_his_work_that_nothing_read(sentence, said, unseen):
    assert intelligence.unseen_status(sentence, said) is unseen
    if unseen:
        assert intelligence.chat_unsaid(sentence, said)             # the depth path too (acts=False)


def test_his_goodbyes_still_end_the_conversation():
    assert main.pre_brain_route("thanks", (), meaning=lambda n: None) == "dismiss"
    assert main.pre_brain_route("go to sleep", (), meaning=lambda n: None) == "dismiss"


def test_a_reading_of_his_project_that_nothing_took():
    assert intelligence.unseen_status("Shadow World's in-game test says your main character walks and stands on the floor, sir.",
                                      "How's my game coming along?")


@pytest.mark.parametrize("reply", ["I'm here to help with whatever you need, sir.",
                                   "Understood, sir: I'm here and ready to help with whatever you need."])
def test_a_whole_reply_that_is_a_help_desk_becomes_a_short_acknowledgement(reply):
    said = []
    voice = speech.Polisher(said.append, asked="Make it happen.", chat=lambda: True)
    voice(reply)
    voice.settle()
    voice.finish()
    assert len(said) == 1 and re.sub(r", sir(?=\.$)", "", said[0]) in character.LINES["ok"]
    assert voice.edits == [(reply, said[0])]


def test_content_with_a_help_desk_tail_keeps_the_content():
    said = []
    voice = speech.Polisher(said.append, asked="Make it happen.", chat=lambda: True)
    for sentence in ("Got it.", "I'm here as your partner, ready to listen and help with whatever you need."):
        voice(sentence)
    voice.settle()
    voice.finish()
    assert said == ["Got it."]


def test_title_thinning_leaves_no_stray_space_and_keeps_a_brand_name():
    earlier = [("Hey.", "Good evening, sir."), ("What's up?", "Not much.")]
    assert _thin("Well, sir , that's odd.", earlier) == "Well, that's odd."
    assert _thin("Sir, iPhone sales are up.", earlier) == "iPhone sales are up."


@pytest.mark.parametrize("sentence", ["Streaming's on. What do you want to watch?", "Streaming protocol's ready.",
                                      "Got it, sir: game mode is on."])
def test_a_switched_state_nothing_switched_is_a_claim(sentence):
    assert intelligence.claims_done(sentence)


@pytest.mark.parametrize("sentence", ["Streaming is how most people watch shows now.", "The streaming protocol is HLS, mostly."])
def test_explaining_streaming_is_no_claim(sentence):
    assert not intelligence.claims_done(sentence)


def test_a_help_desk_line_around_real_words_is_never_said():
    said = []
    voice = speech.Polisher(said.append, asked="Make it happen. Use whatever means necessary.", chat=lambda: True)
    for sentence in ("I'm here as your partner, sir.", "I'll keep the plan simple."):
        voice(sentence)
    voice.settle()
    voice.finish()
    assert said == ["I'll keep the plan simple."]


@pytest.mark.parametrize("sentence", ["Shadow World's in-game test just finished: your main character spawns and walks.",
                                      "Shadow World's in-game test just ran on your main character, sir; it spawned and walked fine."])
def test_a_test_result_nothing_ran_is_not_said(sentence):
    assert intelligence.unseen_status(sentence, "How's my game coming along?")
