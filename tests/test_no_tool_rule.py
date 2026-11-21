"""The durable rule (owner /goal 2026-11-15): asked to MAKE something and no tool ran, a reply is a question, an admission or an offer.
Any other statement is not said, whatever its wording (fifteen rounds of phrase lists could not keep up with the 9B)."""
import pytest

from core import intelligence

ASK = "Can you make me a humanoid in Blender?"


@pytest.mark.parametrize("text", [
    "It will be created locally and free of charge.", "Understood.", "Yes, sir.", "Got it, sir.", "Sure thing.", "Of course, sir.",
    "Blender is a great tool for that.", "The humanoid will have a standard rig.", "A character like that takes skill.",
    "Right away, sir.", "On it."])
def test_a_plain_statement_in_reply_to_a_make_request_is_not_said(text):
    assert intelligence.bare_on_action(text, ASK)


@pytest.mark.parametrize("text", [
    "Which style would you like, sir?", "Shall I open Blender first?", "I haven't started that, sir, and I won't pretend I have.",
    "I can't make one yet, sir.", "Tell me the first step and I'll do it.", "If you want, I can open Blender and we'll go from there.",
    "I don't have a tool for that yet.", "Sorry, sir, not yet.", "Say the word and I'll start."])
def test_a_question_an_admission_or_an_offer_is_said(text):
    assert not intelligence.bare_on_action(text, ASK)


@pytest.mark.parametrize("said", [
    "How are you?", "What's the weather?", "Can you make it shorter?", "Make me laugh.", "Open Notepad.", "Write that down.",
    "Tell me about humanoid robots.", "", None])
def test_other_requests_are_not_judged_by_this_rule(said):
    assert not intelligence.bare_on_action("It will be done shortly, sir.", said)


@pytest.mark.parametrize("said", [
    "Create a new character for the game.", "Build me a website.", "Generate a 3D model of a chair.", "Can you rig the avatar?",
    "Make a short video about my trip.", "Write a script for the app."])
def test_the_requests_it_applies_to(said):
    assert intelligence.bare_on_action("Understood, sir.", said)


def _response(text):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


def test_parse_reply_refuses_a_bare_statement_when_no_tool_ran():
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_reply(_response("It will be created locally and free of charge."), set(), said=ASK, idle=True)


def test_parse_reply_keeps_the_question_and_drops_the_statement():
    message = intelligence.parse_reply(_response("Yes, sir. Which style would you like?"), set(), said=ASK, idle=True)
    assert " ".join(b.text for b in message.content) == "Which style would you like?"


def test_parse_reply_does_not_judge_a_turn_where_a_skill_ran():
    message = intelligence.parse_reply(_response("Understood."), set(), said=ASK, idle=False)
    assert message.content[0].text == "Understood."


def test_the_chat_path_applies_the_same_rule():
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_chat_reply(_response("It will be created locally and free of charge."), ASK, "chat")
    message = intelligence.parse_chat_reply(_response("Shall I open Blender first?"), ASK, "chat")
    assert "Blender" in message.content[0].text
    plain = intelligence.parse_chat_reply(_response("It will be sunny tomorrow, sir."), "How is the weather?", "chat")
    assert "sunny" in plain.content[0].text


def test_a_refused_bare_statement_is_reported_as_not_started_not_a_lost_train_of_thought():
    intelligence.take_refused_promise()                       # clear any earlier note
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_reply(_response("Understood, I can build that."), set(), said="Can you build me a website for my business?", idle=True)
    assert intelligence.take_refused_promise()
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_chat_reply(_response("It will be created locally."), ASK, "chat")
    assert intelligence.take_refused_promise()


@pytest.mark.parametrize("said,text", [
    ("Make me a presentation about space.", "I can build you a website, sir, but I need to know your business name."),
    ("Make me a presentation about space.", "I can't build a website directly, sir. Where do you want to start?"),
    ("Create a new video about my trip.", "Which website do you mean?"),
    ("Can you make me a humanoid in Blender?", "I haven't started the presentation, sir.")])
def test_a_reply_about_another_kind_of_thing_is_an_answer_to_an_earlier_request(said, text):
    assert intelligence.bare_on_action(text, said)


@pytest.mark.parametrize("said,text", [
    ("Make me a presentation about space.", "I can't make a presentation directly, sir."),
    ("Make me a presentation about space.", "Which slides do you want, sir?"),
    ("Can you build me a website for my business?", "What should the site be about?"),
    ("Make me a presentation about my app.", "I haven't started that, sir. Tell me which app."),
    ("Can you make me a humanoid in Blender?", "I haven't started that, sir, and I won't pretend I have.")])
def test_a_reply_about_the_same_thing_keeps_its_honest_or_question_form(said, text):
    assert not intelligence.bare_on_action(text, said)


def test_an_opinion_question_is_answered_in_two_sentences():
    long = ("I think Blade Runner is better for its mood. Its world lingers after the credits. Alien is tighter but thinner. "
            "Both are classics. Your taste is good.")
    message = intelligence.parse_chat_reply(_response(long), "I think Blade Runner is better than Alien. What do you think?", "chat")
    assert message.content[0].text == "I think Blade Runner is better for its mood. Its world lingers after the credits."
    other = intelligence.parse_chat_reply(_response("One. Two. Three."), "Tell me about space.", "chat")
    assert other.content[0].text == "One. Two. Three."


@pytest.mark.parametrize("text", [
    "Sir, I can build that website right now, but you haven't told me your business name or what service you offer yet.",
    "I can build that presentation right now, but you have not told me how long it should be."])
def test_you_havent_told_me_is_not_an_admission_that_nothing_was_started(text):
    assert intelligence.bare_on_action(text, "Make me a presentation about space.") or intelligence.bare_on_action(text, "Can you build me a website for my business?")


def test_the_stream_stops_after_two_sentences_of_an_opinion():
    from brain.llm import _SpokenAsWritten
    said = []
    voice = _SpokenAsWritten(said.append, unchecked="What do you think about pineapple on pizza?")
    voice.as_chat("chat")
    voice.feed("I think it works. The sweetness cuts the salt. A third thing. A fourth thing. ")
    voice.feed("The end.")
    assert said == ["I think it works.", "The sweetness cuts the salt."]
    plain = []
    voice = _SpokenAsWritten(plain.append, unchecked="Tell me about space.")
    voice.as_chat("chat")
    voice.feed("One. Two. Three. Four. ")
    assert plain == ["One.", "Two.", "Three."]


@pytest.mark.parametrize("kind", ["chat", "depth", "think"])
def test_the_opinion_cap_holds_in_every_chat_mode(kind):
    from brain.llm import _SpokenAsWritten
    said = []
    voice = _SpokenAsWritten(said.append, unchecked="What do you think about pineapple on pizza?")
    voice.as_chat(kind)
    voice.feed("I think it works. The sweetness cuts the salt. A third thing. A fourth thing. ")
    assert said == ["I think it works.", "The sweetness cuts the salt."]


@pytest.mark.parametrize("text", [
    "I have queued a mission to build your character, sir.", "I've submitted the request, sir.", "I have launched the render.",
    "I've generated the model.", "I queued it for you."])
def test_a_claim_of_queued_or_launched_work_with_nothing_behind_it_is_a_claim(text):
    assert intelligence.claims_done(text)


@pytest.mark.parametrize("text", ["Shall I queue a mission?", "I can queue that if you want.", "Should I submit it?"])
def test_offers_to_queue_are_still_offers(text):
    assert not intelligence.claims_done(text)


@pytest.mark.parametrize("said,reading", [
    ("Make me a presentation about space.", False), ("Tell me about the space race.", False), ("What is outer space like?", False),
    ("How much space is left on my drive?", True), ("How much disk space do I have?", True), ("How much free space is there?", True),
    ("How much space is on the C drive?", True)])
def test_the_word_space_alone_is_not_a_question_about_the_disk(said, reading):
    assert bool(intelligence._PC_READING_Q.search(said)) == reading
