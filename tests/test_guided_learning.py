"""Paced, source-linked Blender instruction without a model or network call."""
from core import guided_learning


def _memory(monkeypatch):
    data = {}
    monkeypatch.setattr(guided_learning.storage, "load", lambda name, default: data.get(name, default))
    monkeypatch.setattr(guided_learning.storage, "save", lambda name, value: data.__setitem__(name, value))
    return data


def test_blender_human_guide_offers_a_real_source_then_waits_for_yes(monkeypatch):
    _memory(monkeypatch)
    offer = guided_learning.handle("Help me build a human in Blender")
    assert "right Blender guide" in offer
    assert "https://" not in offer
    assert "Would you like me to walk you through it" in offer
    assert guided_learning.handle("yes") .startswith("Step 1 of")


def test_natural_how_do_i_wording_starts_the_same_supported_guide(monkeypatch):
    _memory(monkeypatch)
    assert "right Blender guide" in guided_learning.handle("How do I build a human in Blender?")


def test_guide_advances_only_after_an_acknowledgement(monkeypatch):
    _memory(monkeypatch)
    guided_learning.handle("Teach me how to make a human in Blender")
    first = guided_learning.handle("okay")
    assert first.startswith("Step 1 of")
    assert guided_learning.handle("what time is it") is None
    second = guided_learning.handle("got it")
    assert second.startswith("Step 2 of")
    assert "Step 1 of" in guided_learning.handle("back")


def test_unrelated_acknowledgement_never_starts_a_guide(monkeypatch):
    _memory(monkeypatch)
    assert guided_learning.handle("okay") is None


def test_asking_for_youtube_videos_or_links_is_not_taken_by_the_offline_guide(monkeypatch):
    # Regression: "Find me two Blender rigging tutorials on YouTube" was answered with the offline guide instead of the video list.
    _memory(monkeypatch)
    for phrase in ("Find me two Blender rigging tutorials on YouTube.", "look up Blender videos", "find a link for a Blender tutorial",
                   "Show me a Blender playlist on how to rig", "find a you tube channel for Blender"):
        assert guided_learning.handle(phrase) is None, phrase


def test_the_offline_guide_still_answers_how_do_i_and_teach_me_requests(monkeypatch):
    _memory(monkeypatch)
    for phrase in ("How do I build a human in Blender?", "Teach me how to make a human in Blender", "Find me a Blender guide for a human"):
        assert "right Blender guide" in guided_learning.handle(phrase), phrase
        guided_learning.handle("no thanks")  # leave no pending offer behind
