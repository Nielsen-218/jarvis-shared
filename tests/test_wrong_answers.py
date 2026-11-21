"""Wrong answers he cut off or corrected (data/jarvis.log Nov 8-21, hand-labelled 2026-11-21): the structural fixes.
Scored end to end on the real model by scripts/wrong_answer_eval.py; these pin the code rules."""
import re
from types import SimpleNamespace

import pytest

from core import gateway, intelligence, quick, screenguide, selfcard


# ---- talk that says "do that" / "make this" is not "follow the instructions on my screen" -------------------------------
@pytest.mark.parametrize("said", [
    "I guess to do that. That's a skill Jarvis needs to have. And then Japanese.",
    "He can do that too. Like go ahead. Go crazy. Improve. Improve. Improve. There is no limit here.",
    "Every time it starts. Yeah. So stuff like that. Now, please make this sound very detailed and professional.",
    "No, you can stop. I don't know how you would do that. Can you actually do that?",
    "I want to demonstrate you live to my family. Can we do that?",
    "No, I meant to tell, I told you to create a humanoid character in Blender and you didn't do that.",
    "Can you make this thing do some type of animation?",
    "And Unreal Engine. Can you make this character do some animation?",
    "Yes, that's good. Now, can you do that?"])
def test_talk_pointing_back_at_the_conversation_is_not_the_screen_guide(said):
    assert not screenguide.asked(said)


@pytest.mark.parametrize("said", [
    "do this", "Jarvis, do this for me.", "please do this now", "do this for me please", "do these steps", "Can you do this on my screen?",
    "make this work", "try this tutorial", "Follow the instruction on my screen.", "Hey Jarvis, install this."])
def test_a_real_order_about_the_screen_still_is_one(said):
    assert screenguide.asked(said)


# ---- a promise of work in any verb, with nothing behind it --------------------------------------------------------------
@pytest.mark.parametrize("sentence", [
    "Understood, sir: I will upgrade your Jarvis to speak, teach you languages and upgrade your UI.",
    "I will fix your Jarvis to speak.", "I'll improve the wardrobe app.", "I'm going to redesign your UI.",
    "I'll research and improve your workouts."])
def test_a_future_promise_of_work_is_a_promise(sentence):
    assert intelligence.promises_action(sentence)


@pytest.mark.parametrize("sentence", [
    "I'll be here if you need me.", "I'll keep it short.", "I'll explain the difference.", "I'll take that as a yes.",
    "If you want, I'll fix the playlist.", "I'll fix it once you confirm.", "I'll stay quiet until you call me.",
    "I'll give you the short version.", "I'll do my best.", "I'll draw your attention to the second option.",
    "I'll turn it over to you.", "You'll love the ending.", "I will summarize the key points for you.", "I'll recap the main points.",
    "I'll rephrase that.", "I'll read the first item.", "I'll do the best I can."])
def test_talk_offers_and_staying_put_are_not_promises(sentence):
    assert not intelligence.promises_action(sentence)


# ---- the gateway never opens an app he did not ask for, never closes what he asked to minimize ---------------------------
@pytest.mark.parametrize("said,app,ok", [
    ("I want to show you live.", "livecaptions", False), ("The new Claude. To light.", "claude", False),
    ("ChatGPT.", "chatgpt", True), ("I want Spotify", "spotify", True), ("Open calc", "calculator", True),
    ("Task manager", "task manager", True), ("Hey Jarvis, Discord please", "discord", True), ("opening steam", "steam", True),
    ("Spotify, play Drake", "spotify", True), ("yes", "spotify", True), ("[Sent from my phone] yes", "spotify", True),
    ("ouvre chrome", "chrome", True), ("take me to YouTube", "youtube", True)])
def test_open_needs_an_open_word_or_only_the_apps_name(said, app, ok):
    assert gateway._asked_to_open(said, app) is ok


def test_a_minimize_is_never_decided_as_a_close(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda name: "claude")
    monkeypatch.setattr(gateway, "verbs", lambda: {"close_app", "open_app", "minimize_app"})
    from core import pcactions
    monkeypatch.setattr(pcactions, "validate", lambda plan: None)
    ctx = SimpleNamespace(said="Minimize Claude. Associated Rated Candidates. Minimize Claude.", pc={})
    with pytest.raises(ValueError, match="minimize"):
        gateway._check_step({"tool": "close_app", "arguments": {"app": "claude"}}, ctx)
    ctx.said = "Minimize Claude and close Notepad."
    assert gateway._check_step({"tool": "close_app", "arguments": {"app": "claude"}}, ctx)["tool"] == "close_app"


# ---- mishearings and self-knowledge -------------------------------------------------------------------------------------
@pytest.mark.parametrize("said,fixed", [("And stream.", "end the stream"), ("And the stream.", "end the stream"),
                                        ("End. Stream.", "end the stream"), ("And streaming protocol.", "And streaming protocol."),
                                        ("and stream it on twitch", "and stream it on twitch")])
def test_and_stream_is_end_the_stream(said, fixed):
    import main
    assert main.fix_misheard(said) == fixed


@pytest.mark.parametrize("said", ["No, tell me all your skills", "Oh my gosh, is that the only thing you can do",
                                  "Tell me your skills"])
def test_every_way_of_asking_for_all_his_skills_gets_the_whole_list(said):
    assert selfcard._GENERAL.fullmatch(said)


@pytest.mark.parametrize("said,hit", [
    ("Jarvis, are you using a paid model to do this or is that all local?", True), ("Are you running locally?", True),
    ("Are you on the cloud right now", True), ("Are you using Claude to write the code for me", False),
    ("are you working on the model", False), ("Are you going to cloud my judgment", False), ("are you a paid model", True),
    ("Are you paid?", True), ("Are you using API credits to answer me or no?", True)])
def test_paid_or_local_questions_get_the_settings_answer(said, hit):
    bare = re.sub(r"[\s,]+(?!\d{3}\b)", " ", said).strip(" ?.!")
    assert bool(quick._OWN_BRAIN.match(bare)) is hit


def test_a_claim_of_a_paid_or_unavailable_brain_is_not_said(monkeypatch):
    monkeypatch.setattr(intelligence, "local_only", lambda: True)
    assert intelligence.chat_unsaid("I run on a paid cloud model that costs extra credits.")
    assert intelligence.chat_unsaid("Local intelligence is unavailable right now.")
    assert not intelligence.chat_unsaid("I'm not using a paid model for this; I run locally on your PC.")
    assert not intelligence.chat_unsaid("I think cloud storage is great.")
    assert not intelligence.chat_unsaid("I run a paid service for you.")


def test_end_the_stream_is_never_guessed_to_be_a_goodbye(monkeypatch):
    import main
    from core import meaning
    monkeypatch.setattr(meaning, "choose", lambda *a, **k: "dismiss")
    assert main.pre_brain_route("end the stream", []) == "respond"
    assert main.pre_brain_route("gaming protocol", []) == "respond"
