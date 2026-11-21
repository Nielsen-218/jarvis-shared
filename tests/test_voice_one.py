"""The owner's real-voice test, 2026-11-15 ~20:55: a website talk started a 3D character job; "So how much RAM is free." was answered with the
total; and he asked for an opinion and only got agreement."""
import pytest

from core import intelligence, quick

WEBSITE_TALK = "It offers people to shop for insurance. But our branding or our model will be insurance you can trust. That's all I have."


def _call(name, args):
    import json
    return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def test_a_website_talk_does_not_start_a_character_job():
    response = _call("make_character_from_description", {"description": "But our branding or our model will be insurance you can tr"})
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_reply(response, {"make_character_from_description"}, said=WEBSITE_TALK, heard=WEBSITE_TALK)


@pytest.mark.parametrize("said", [
    "Can you make me a humanoid in Blender?", "Create a dragon for the game.", "Make me a 3D robot.", "Build a character called Zed.",
    "Design a mascot for my business.", "Rig the avatar."])
def test_a_request_for_a_character_still_grounds_the_tool(said):
    assert intelligence._GROUNDING["make_character_from_description"].search(said)


@pytest.mark.parametrize("said", [
    "Can you build me a website for my business?", WEBSITE_TALK, "Make me a presentation about space.", "Yes, go ahead.", "I like your model."])
def test_other_talk_does_not_ground_it(said):
    assert not intelligence._GROUNDING["make_character_from_description"].search(said)


@pytest.mark.parametrize("raw", ["So how much RAM is free.", "Well, how much RAM is free?", "And how much memory is left.", "Okay so how much RAM is free."])
def test_a_filler_in_front_of_a_memory_question_does_not_hide_it(raw, monkeypatch):
    from tools import registry
    monkeypatch.setattr(registry, "names", lambda: ["get_system_status"])
    monkeypatch.setattr(registry, "run", lambda name, args: ({"memory_used_gb": 20.0, "memory_total_gb": 31.5}, False))
    assert quick._pc_reading(raw) == "11.5 GB of memory is free, with 20.0 of 31.5 GB in use, sir."


def test_the_personality_gives_an_asked_opinion_not_agreement():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "core" / "personality.md").read_text(encoding="utf-8")
    assert "never just agree to please them" in text and "get no comment unless they ask" in text
