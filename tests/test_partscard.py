"""'What's the hub and where is that?' -> 'your main control point for the house... likely in the living room' (invented, 2026-11-16). A question asking what one
of his own parts IS gets its true description in code."""
import pytest

from core import partscard as selfcard, quick


@pytest.mark.parametrize("said,needle", [
    ("What's the hub and where is that?", "PC control hub"), ("What is the hub?", "PC control hub"), ("Where is the hub?", "hub folder"),
    ("Tell me about the companion.", "little robot"), ("What's the companion?", "little robot"), ("What is the laptop server?", "always-on server"),
    ("What is the phone app?", "pair my phone"), ("What's World Intelligence?", "live globe"), ("Explain PC control.", "looking at the screen"),
    ("So what's the hub, sir?", "PC control hub"), ("What does the hub do?", "PC control hub"),
    ("[Sent from their Jarvis phone app.] What's the hub and where is that?", "PC control hub")])
def test_what_one_of_his_parts_is_comes_from_facts(said, needle):
    assert needle in selfcard.answer(said)
    assert needle in quick.handle(said)


def test_the_hub_answer_names_no_room_of_the_house():
    text = selfcard.answer("What's the hub and where is that?")
    for guess in ("living room", "kitchen", "smart devices", "lights", "locks", "home network"):
        assert guess not in text


@pytest.mark.parametrize("said", [
    "Where is the laptop?", "Where's the server?", "What's the weather?", "What is the capital of France?", "What's a hub in networking, in general?",
    "What time is it?", "What is love?", "What's the matter?", "Open the hub.", "What are you?"])
def test_other_questions_are_not_the_card(said):
    assert selfcard.answer(said) is None
