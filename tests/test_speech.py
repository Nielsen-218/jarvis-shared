"""Spoken forms for the voice: what Jarvis writes for the screen -> what Kokoro should say."""
from datetime import date

import pytest

from core.speech import spoken

YEAR = date.today().year


@pytest.mark.parametrize("written, said", [
    # dates Kokoro misread ("Dec. 20" came out as "deck 20")
    ("Your flight leaves Dec. 20 at 8:05 AM.", "Your flight leaves December 20 at 8:05 AM."),
    ("The meeting moved to Tuesday, Oct. 28.", "The meeting moved to Tuesday, October 28."),
    ("Due Sept 3rd", "Due September 3rd"),
    ("It ships Jun 2027.", "It ships June 2027."),
    ("Thu Oct 23 at 9:15 AM", "Thursday October 23 at 9:15 AM"),
    ("Open Mon-Fri, 9 to 5.", "Open Monday to Friday, 9 to 5."),
    ("Sat., Dec 20 works.", "Saturday, December 20 works."),
    (f"Reminder set for {YEAR}-10-23T17:00.", "Reminder set for October 23 at 5 PM."),
    ("Renewal on 2031-02-01.", "Renewal on February 1, 2031."),
    ("Dinner at 18:30 tonight.", "Dinner at 6:30 PM tonight."),
    # units
    ("It's 62°F and sunny.", "It's 62 degrees and sunny."),
    ("It's 17 °C in Paris.", "It's 17 degrees Celsius in Paris."),
    ("Winds around 8 mph.", "Winds around 8 miles per hour."),
    ("Check the 5V rail.", "Check the 5 volts rail."),
    ("A 100W bulb", "A 100 watts bulb"),
    ("The 2 TB drive has 260 GB free.", "The 2 terabytes drive has 260 gigabytes free."),
    ("About 1 hr left, then 12 min.", "About 1 hour left, then 12 minutes."),
    ("Buy 2 lbs of rice and 1 lb of beans.", "Buy 2 pounds of rice and 1 pound of beans."),
    ("A 2200 mAh battery", "A 2200 milliamp hours battery"),
    ("It's 5 mi away.", "It's 5 miles away."),
    ("Running at 144 fps", "Running at 144 frames per second"),
    # abbreviations and symbols
    ("Pick up eggs, milk, etc. tomorrow.", "Pick up eggs, milk, et cetera tomorrow."),
    ("Leafy greens, e.g. spinach.", "Leafy greens, for example spinach."),
    ("Your appointment with Dr. Patel", "Your appointment with Doctor Patel"),
    ("Lakers vs. Celtics", "Lakers versus Celtics"),
    ("Tom & Jerry", "Tom and Jerry"),
    ("It takes ~20 minutes.", "It takes about 20 minutes."),
    ("A 1920x1080 screen", "A 1920 by 1080 screen"),
    ("Coffee w/ milk", "Coffee with milk"),
    # screen formatting
    ("**Note:** the meeting moved.", "Note: the meeting moved."),
    ("- Step 1: unplug the board. - Step 2: check it.", "Step 1: unplug the board. Step 2: check it."),
    ("## Today", "Today"),
    ("Run `pip install kokoro` first.", "Run pip install kokoro first."),
    ("See [the docs](https://example.com/docs) for more.", "See the docs for more."),
    ("Details at https://example.com/a/b?c=1.", "Details at the link."),
    ("Maps (https://maps.google.com/?q=a,b) or (see www.example.com)", "Maps the link or (see the link)"),
    ("All set ✅ 🎉", "All set"),
])
def test_written_forms_become_spoken_forms(written, said):
    assert spoken(written) == said


@pytest.mark.parametrize("text", [
    "Good morning, sir. You have 2 reminders and the forecast is clear.",
    "Your total comes to $4.29, and the store closes at 9:30 PM.",
    "You've completed 75% of this week's goal.",
    "The cat sat. Then it slept in the sun.",                  # not Saturday / Sunday
    "We wed in May, and I may go again.",
    "Head to 120 W 42nd St, apartment 5A.",                    # an address, not watts or amps
    "That recording is 13:20 long.",                          # a duration, not 1:20 PM
    "March on, sir.",
    "AT&T called.",
    "",
])
def test_clean_text_is_left_alone(text):
    assert spoken(text) == text


def test_idempotent():
    for text in ["It's 62°F, Dec. 20, 5V, e.g. this.", "**Bold** - item"]:
        assert spoken(spoken(text)) == spoken(text)


# ---- how JARVIS sounds (upgrade 4): polish() and the Brain's live version of it ------------------------------------------
from core import speech  # noqa: E402

SPIEL = ("I'm a personal AI assistant running on your PC. I can manage your schedule, control your apps and look things up. "
         "I also keep notes on what matters to you. What can I help you with today?")


def test_a_real_question_is_kept():
    assert speech.polish("Which drive, sir?") == "Which drive, sir?"
    assert speech.polish("I found two drives. Which drive, sir?") == "I found two drives. Which drive, sir?"
    assert speech.polish("Want me to open Blender or Unreal?") == "Want me to open Blender or Unreal?"


@pytest.mark.parametrize("offer", ["Anything else I can help with?", "Anything else, sir?", "Is there anything else you need?",
                                   "Would you like me to look into it further?", "How can I help?"])
def test_a_closing_offer_after_content_is_dropped_but_an_offer_alone_is_kept(offer):
    assert speech.polish(f"It's 62 degrees and sunny, sir. {offer}") == "It's 62 degrees and sunny, sir."
    assert speech.polish(offer) == offer
    assert speech.polish(f"{offer} Also, it's 62 degrees.") == f"{offer} Also, it's 62 degrees.", "mid-reply: said in order"


def test_content_with_an_offer_tacked_on_keeps_its_content():
    assert speech.polish("It's sunny, anything else?") == "It's sunny, anything else?"


def test_an_offer_that_is_a_real_pending_question_is_kept():
    text = "I can uninstall ripgrep for you. Would you like me to go ahead?"
    assert speech.polish(text, keep=lambda: True) == text


def test_he_is_you_not_the_owner():
    """Nov 9: 'The owner had previously asked me to work on it' said to the owner himself."""
    assert speech.polish("The owner had previously asked me to work on it.") == "You had previously asked me to work on it."
    assert speech.polish("The owner wants me to check the drive. The owner has asked me twice.") == \
        "You want me to check the drive. You have asked me twice."
    assert speech.polish("It belongs to the owner of this PC.") == "It belongs to the owner of this PC."
    assert speech.polish("The owner had asked me.", owner=False) == "The owner had asked me.", "someone else is talking"


@pytest.mark.parametrize("text", [
    "The owner hasn't pushed a commit since 2021.",
    "The owner is John Smith, who bought the restaurant in 2019.",
    "I called the shop; the owner says they open at nine.",
    "The owner archived that repo last year.",
    "I checked the listing; the owner is asking five hundred dollars.",
    "Check the owner's manual for the torque setting.",
    "The owner wants 500 dollars for the bike, and the owner is in Norfolk.",
    "The owner is listed as SYSTEM on that folder.",
    "The owner prefers short answers.",
])
def test_any_other_owner_is_left_as_written(text):
    """Review 2026-11-10: every 'the owner' became 'you', so a repo's or a shop's owner turned into false statements about
    him ('You archived that repo', 'You are listed as SYSTEM'), written into history too, and 'You prefers'."""
    assert speech.polish(text) == text


def test_a_copy_of_the_last_reply_is_said_once_shortened():
    """Nov 9 18:2x: 'Why are you here?' got the same four-sentence spiel twice in a row."""
    history = [{"role": "user", "content": "Why are you here?"}, {"role": "assistant", "content": SPIEL}]
    again = speech.polish(SPIEL, history)
    assert again == "As I said, sir: I'm a personal AI assistant running on your PC."
    assert speech.polish("Certainly, sir. " + SPIEL, history) == \
        "Certainly, sir. As I said, sir: I'm a personal AI assistant running on your PC.", "a lead-in never swallows the answer"
    assert speech.polish(SPIEL, history, asked="say that again") != again, "asked to hear it again: it is the answer"
    assert speech.polish("Okay, sir.", [{"role": "assistant", "content": "Okay, sir."}]) == "Okay, sir.", "short lines repeat freely"


def test_the_brain_says_and_keeps_exactly_the_polished_reply(monkeypatch):
    """Streamed: sentences come out in order as written, the closing offer is dropped only at the end, and the history
    holds exactly what was said."""
    from types import SimpleNamespace

    from anthropic.types import TextBlock

    from brain import llm
    from core import intelligence, pcfast, quick
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    events = []

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        for piece in ["It's 62 degrees", " and sunny, sir. ", "The owner asked me", " to watch for rain. ", "Anything else?"]:
            on_text(piece)
        events.append("model finished")
        return SimpleNamespace(content=[TextBlock(type="text", text="It's 62 degrees and sunny, sir. The owner asked me to "
                                                  "watch for rain. Anything else?")], stop_reason="end_turn")
    monkeypatch.setattr(intelligence, "request", request)
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)

    def said(sentence):
        events.append(sentence)
    b.ask("how's the weather looking", said)
    assert events == ["It's 62 degrees and sunny, sir.", "You asked me to watch for rain.", "model finished"]
    stored = b.messages[-1]["content"]
    text = stored if isinstance(stored, str) else " ".join(x.text for x in stored)
    assert text == "It's 62 degrees and sunny, sir. You asked me to watch for rain."


WEATHER = "It's 62 degrees and sunny in Springfield today, sir."


def test_a_repeated_answer_after_a_lead_in_is_never_swallowed():
    """Review 2026-11-10: 'Checking, sir.' then the same answer as last time: the answer was dropped, he heard only the
    lead-in, and the stored reply lost it too."""
    history = [{"role": "assistant", "content": WEATHER}]
    out = speech.polish("Checking, sir. " + WEATHER, history)
    assert out.startswith("Checking, sir. ") and "62 degrees" in out


@pytest.mark.parametrize("before,now", [
    ("Steam is still running on your PC right now, sir.", "Steam is not running on your PC right now, sir."),
    ("Your CPU is at 45 percent right now, sir.", "Your CPU is at 92 percent right now, sir."),
    ("It's 72 degrees and sunny in Springfield today, sir.", "It's 58 degrees and sunny in Springfield today, sir."),
])
def test_a_changed_fact_is_never_a_copy(before, now):
    """Review 2026-11-10: a 'not' or a new number read 0.93-0.95 alike: the new reading was dropped after 'Checking, sir.'
    or said as 'As I said, sir:', claiming he had already been told it."""
    history = [{"role": "assistant", "content": before}]
    assert speech.polish(now, history) == now
    assert speech.polish("Checking, sir. " + now, history) == "Checking, sir. " + now


def test_after_a_tool_ran_nothing_is_a_copy():
    """A fresh reading or a second action's result ('set another 10 minute timer') is news even when it reads the same."""
    history = [{"role": "assistant", "content": WEATHER}]
    assert speech.polish("Checking, sir. " + WEATHER, history, fresh=lambda: True) == "Checking, sir. " + WEATHER


@pytest.mark.parametrize("asked", ["What was that?", "Sorry, what?", "Huh, what?", "I didn't hear you.", "I couldn't hear you",
                                   "I missed that.", "Say that again?"])
def test_he_did_not_hear_it_so_the_repeat_is_the_answer(asked):
    history = [{"role": "assistant", "content": SPIEL}]
    assert speech.polish(SPIEL, history, asked=asked) == SPIEL.rsplit(" What can I help", 1)[0]


def test_as_i_said_never_doubles_the_title():
    line = "To keep your PC, your plans and your day in order, sir."
    assert speech.polish(line, [{"role": "assistant", "content": line}]) == "As I said: " + line


@pytest.mark.parametrize("text", [
    "Jellyfin lost the media drive. Want me to go ahead and restart it?",
    "To finish, I'd need to close Edge first. Should I close it?",
    "That's the gist of photosynthesis. Want me to open a lesson page on it?",
    "I only caught part of that. Could you tell me more about what you need?",
])
def test_a_real_question_about_a_step_is_never_trimmed_as_an_offer(text):
    """Review 2026-11-10: the persona asks first (restart_app, repair_drive_mounts, an extra step, a lesson page); trimming
    those questions dead-ended the reply and took the question out of history, so his 'yes' had nothing to answer."""
    assert speech.polish(text) == text


def test_a_direct_tool_result_said_again_is_said_as_it_is(monkeypatch):
    """A second real action's result (the same words as the last reply) is never 'As I said, sir:'."""
    from types import SimpleNamespace

    from anthropic.types import ToolUseBlock

    from brain import llm
    from core import intelligence
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")

    def one_turn(emit):
        return SimpleNamespace(stop_reason="tool_use", content=[ToolUseBlock(type="tool_use", id="c1", name="list_countdowns", input={})])
    monkeypatch.setattr(b, "_stream_turn", one_turn)
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    first, second = [], []
    b.ask("what am I looking forward to", first.append)
    b.ask("what am I looking forward to now", second.append)
    assert len(" ".join(first).split()) >= 6 and second == first, (first, second)
