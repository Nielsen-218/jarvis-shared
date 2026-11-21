"""Review of chat polish (2026-11-12): a command shaped like a remark keeps the tools; a conversational question is no
offer; "he said it himself" needs a shared word; the non-streamed parse agrees with the Polisher. Real skill list, a
prior assistant turn in every case, no model."""
import pytest

from core import chatturn, intelligence, speech, warmth
from tools import load_all, registry

load_all()


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_CHAT_FIRST", "JARVIS_CHAT_THINK", "JARVIS_PC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)


def _history(said, before="It rained most of the night."):
    return [{"role": "user", "content": "Morning."}, {"role": "assistant", "content": before},
            {"role": "user", "content": said}]


# ----------------------------------------------------------------------------- 1. commands shaped like remarks

COMMANDS = ["That's enough", "That'll do", "We're done", "Probably time to stop", "Good, next", "Great, next",
            "Nice, more of that", "Not bad, louder", "Pretty good, again", "Maybe play something", "Perhaps some jazz",
            "Same again", "Pretty loud", "It's a bit loud", "It's getting dark", "It's stuffy", "It's noisy",
            "It's freezing in here", "That's too quiet", "Good, skip it", "Okay, back"]
REMARKS = ["Good to hear.", "Not really.", "Quiet evening, then.", "That's fair.", "I guess so.", "Pretty good, thanks.",
           "Makes sense.", "Fair enough.", "Me too.", "What a day.", "I'm just tired."]


@pytest.mark.parametrize("said", COMMANDS)
def test_a_command_shaped_like_a_remark_keeps_the_tools(local, said):
    assert not chatturn.remark(said)
    assert chatturn.command_like(said, _history(said), schemas=[{"name": "set_volume"}])
    assert chatturn.plan("fast", _history(said), [{"type": "function", "function": {"name": "set_volume"}}], False, None) is None


@pytest.mark.parametrize("said", REMARKS)
def test_a_whole_line_remark_is_still_chat(local, said):
    assert chatturn.remark(said)
    assert not chatturn.command_like(said, _history(said), schemas=[{"name": "set_volume"}])
    schemas = intelligence._schemas(intelligence.select_tools(_history(said), registry.schemas()))
    assert chatturn.plan("fast", _history(said), schemas, False, None) == chatturn.CHAT


# ------------------------------------------------------------------------------ 2. a question is not an offer

QUESTIONS = ["May I ask what you're working on?", "Can I ask what happened?", "Would you like to talk about it?",
             "What do you want for dinner?", "Do you need a break?", "Anything on your mind?", "Tell me what happened.",
             "Feel free to vent.", "What's next for the project?", "Should I be worried?",
             "Can I just say, that sounds rough.", "So, what's next?", "What would you like to do about it?"]
TASK_OFFERS = ["Want me to pull up today's workout or check how the character rig is coming along?",
               "Anything specific you want me to tackle next on the garden plan?", "Shall I look into it?",
               "Should I keep going?", "Would you like me to put the kettle on?", "Let me know if you need anything.",
               "What can I do for you?", "Is there anything else I can do?", "Could I open that for you?",
               "Shall I quickly check the forecast?", "If you need anything done on the PC, let me know."]


@pytest.mark.parametrize("sentence", QUESTIONS)
def test_a_conversational_question_is_not_an_offer(sentence):
    assert not speech.chat_offer(sentence, "Long day.")
    out = []
    voice = speech.Polisher(out.append, asked="Long day. Work was nonstop.", chat=lambda: True)
    for s in ("That sounds like a long one.", sentence):
        voice(s)
    voice.settle()
    voice.finish()
    assert out[-1] == sentence


@pytest.mark.parametrize("sentence", TASK_OFFERS)
def test_an_offer_to_do_a_task_is_still_an_offer(sentence):
    assert speech.chat_offer(sentence, "Long day.")


# ----------------------------------------------------------------- 3. "he said it himself" needs a shared word

def test_any_im_or_busy_in_his_line_no_longer_excuses_an_invented_present():
    assert intelligence.invents_present("Just keeping an eye on the build for you.", "I'm fine, thanks.")
    assert intelligence.invents_present("I've been monitoring the logs.", "Busy day, I'm beat.")
    assert intelligence.invents_present("You are still working on the boat model, I see.", "I'm hungry.")


def test_what_his_line_names_may_be_said_back():
    assert not intelligence.invents_present("Good luck while you work on it.", "I'm working on the boat model tonight.")
    assert not intelligence.invents_present("You are busy with the boat model, then.", "The boat model is eating my evening.")
    assert not intelligence.invents_present("I'm checking the weather question you asked.", "What do you make of this weather?")


# ------------------------------------------------------------------ 4. the non-streamed parse agrees

def _reply(text):
    return {"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": text}}]}


def _text(msg):
    return " ".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


@pytest.mark.parametrize("offer", ["Want me to put the kettle on?", "Let me know if you need anything."])
def test_the_parsed_chat_reply_ends_before_the_offer(offer):
    kept = intelligence.parse_chat_reply(_reply("That sounds like a long one. " + offer), "Long day.", kind=chatturn.CHAT)
    assert _text(kept) == "That sounds like a long one."


def test_the_parse_keeps_a_question_an_only_sentence_and_an_asked_for_offer():
    for text, said in (("That sounds like a long one. Was it the manager again?", "Long day."),
                       ("Want me to keep you company?", "Long day."),
                       ("A few things. Want me to draw up a plan?", "What can you do about the plan?")):
        assert _text(intelligence.parse_chat_reply(_reply(text), said, kind=chatturn.CHAT)) == text


def test_a_depth_answer_ends_before_the_offer_too_as_the_polisher_has_it():
    text = "It is water, not smoke. Let me know if you need the longer version."
    assert _text(intelligence.parse_chat_reply(_reply(text), "Why do planes leave trails?", kind=chatturn.DEPTH)) == "It is water, not smoke."
