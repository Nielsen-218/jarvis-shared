"""Shorter spoken answers (owner 2026-11-21: "shorter, better answers"): the spoken budget, the restated opening, and
"tell me more" picking up what the budget kept back. Real reply shapes from his log of Nov 8-21."""
import time

import pytest

from audio import bargein
from core import speech


def polish(asked, text, earlier=(), **kw):
    out = []
    p = speech.Polisher(out.append, asked=asked, budget=speech.spoken_budget(asked), earlier=earlier, **kw)
    for sentence in speech._SPLIT.split(text):
        p(sentence)
    p.finish()
    return out, p


# -- the budget -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("asked", [
    "Explain how computer processor works in five sentences.",
    "Walk me through how to back up my files.",
    "Give me the recipe for diri ak pwa.",
    "What are your skills?",
    "Oh my gosh, is that the only thing you can do?",
    "Tell me more about the Bay.",
    "How do I turn on PC control?",
    "Give me a list of what's using my memory.",
])
def test_no_budget_when_he_asked_for_the_long_form(asked):
    assert speech.spoken_budget(asked) is None


@pytest.mark.parametrize("asked, budget", [
    ("What's 50% of 100?", speech.CHAT_BUDGET),
    ("How are you doing?", speech.CHAT_BUDGET),
    ("Why can't you work on the one I have open right now?", speech.DEPTH_BUDGET),
    ("I was thinking of wiping everything. What do you think?", speech.DEPTH_BUDGET),
])
def test_budget_by_kind_of_question(asked, budget):
    assert speech.spoken_budget(asked) == budget


def test_budget_switch_off(monkeypatch):
    monkeypatch.setattr(speech, "SPOKEN_BUDGET", False)
    assert speech.spoken_budget("What's 50% of 100?") is None


def test_a_long_why_answer_stops_after_three_sentences_and_keeps_the_rest():
    # live 2026-11-15: 140 words to "Why can't you work on the one I have open right now?"; he cut in
    out, p = polish("Why can't you work on the one I have open right now?",
                    "I can't edit a file another Blender session has open. I can see your screen, but not reach into that "
                    "process. If you save it and give me the path, I can load it. Otherwise I start a fresh project. "
                    "That keeps your work safe.")
    assert len(out) == 3 and out[0].startswith("I can't edit")
    assert p.trimmed == ["Otherwise I start a fresh project.", "That keeps your work safe."]
    assert ("That keeps your work safe.", "") in p.edits          # the stored reply is what he heard


def test_a_quick_fact_is_two_sentences():
    out, p = polish("What's 50% of 100?", "Fifty. That's half of a hundred. Easy one.")
    assert out == ["Fifty.", "That's half of a hundred."] and p.trimmed == ["Easy one."]


def test_the_first_sentence_is_always_said_however_long():
    long = " ".join(["word"] * 70) + "."
    out, p = polish("What do you think about the world?", long + " Here is a second thought.")
    assert out == [long] and p.trimmed == ["Here is a second thought."]


def test_an_acknowledgement_does_not_count():
    out, _p = polish("What's 50% of 100?", "Sure. Fifty. That's half of a hundred.")
    assert out == ["Sure.", "Fifty.", "That's half of a hundred."]


def test_a_warning_past_the_budget_is_still_said():
    out, p = polish("What's the fastest way to free up space on my C drive?",
                    "Empty the recycle bin. Then clear your downloads folder. Deleting the Windows folder would break the PC "
                    "and can't be undone. Disk Cleanup handles the rest.")
    assert any("can't be undone" in s for s in out)
    assert "Disk Cleanup handles the rest." in p.trimmed


def test_a_question_his_answer_needs_is_still_said():
    out, _p = polish("Should I take the car or the train?",
                     "The train. It's faster at rush hour. Parking downtown is expensive. Which day did you mean?")
    assert out[-1] == "Which day did you mean?"


def test_steps_he_asked_for_are_never_trimmed():
    steps = "First open Settings. Then go to Update. Then pick Recovery. Then choose Reset. Keep your files when asked."
    out, p = polish("Walk me through how to reset Windows.", steps)
    assert len(out) == 5 and not p.trimmed


def test_no_budget_given_says_everything():
    out = []
    p = speech.Polisher(out.append, asked="What's 50% of 100?")
    for s in ("Fifty.", "That's half.", "Easy."):
        p(s)
    p.finish()
    assert out == ["Fifty.", "That's half.", "Easy."]


def test_no_trim_after_a_tool_ran():
    out, p = polish("What's 50% of 100?", "Fifty. Half. Easy. Done.", fresh=lambda: True)
    assert len(out) == 4 and not p.trimmed


def test_no_trim_while_a_question_waits():
    out, p = polish("What's 50% of 100?", "Fifty. Half. Easy. Done.", keep=lambda: True)
    assert len(out) == 4 and not p.trimmed


# -- his words said back ---------------------------------------------------------------------------------------------------
def test_an_opening_that_only_restates_him_is_dropped_when_the_answer_follows():
    # live 2026-11-11: "You want me to look at your Jarvis setup and make it smarter, right? ..."
    out, p = polish("Can you look at my Jarvis setup and make it smarter?",
                    "You want me to look at your Jarvis setup and make it smarter, right? I can't change my own code from "
                    "here. Claude Code can, on a branch.")
    assert out == ["I can't change my own code from here.", "Claude Code can, on a branch."]
    assert ("You want me to look at your Jarvis setup and make it smarter, right?", "") in p.edits


def test_a_restated_trip_from_his_earlier_lines_is_dropped():
    earlier = [("I'm planning a trip from Springfield to my mother's house in Brentwood, New York.", "Got it."),
               ("I'm going on Tuesday.", "Okay.")]
    out, _p = polish("What time should I leave?",
                     "I see you're heading to your mother's in Brentwood, New York on Tuesday. Leave by seven to miss "
                     "the tunnel traffic.", earlier=earlier)
    assert out == ["Leave by seven to miss the tunnel traffic."]


def test_a_restated_opening_alone_is_said():
    out, _p = polish("Can you look at my Jarvis setup?", "You want me to look at your Jarvis setup?")
    assert out == ["You want me to look at your Jarvis setup?"]


@pytest.mark.parametrize("sentence", [
    "You're looking at about six hours of driving.",            # a number of its own: the answer
    "You're right that the drive is long.",                      # not the restating shape
    "You'd like the train better, it's faster.",                 # new words: an opinion
])
def test_an_answer_shaped_like_a_restatement_is_kept(sentence):
    assert not speech.restates(sentence, "How long is the drive to Brentwood? Would I like the train?")


# -- "tell me more" ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said, more", [
    ("Tell me more.", True), ("and?", True), ("What else?", True), ("Go on, Jarvis.", True), ("More.", True),
    ("tell me more about Rome", False), ("Yes.", False), ("No.", False), ("Open more tabs.", False),
])
def test_asks_for_more(said, more):
    assert bargein.asks_for_more(said) is more


@pytest.fixture
def brain(monkeypatch):
    from brain.llm import Brain
    from core import pcfast, quick
    b = Brain("x", "low", "sys")
    b.messages = []
    b._last_turn = time.monotonic()
    b.front_doors = []
    monkeypatch.setattr(quick, "handle", lambda text: b.front_doors.append(("quick", text)))
    monkeypatch.setattr(pcfast, "handle", lambda text, history, *a, **k: b.front_doors.append(("pcfast", text)))
    return b


REPLY = ("I can't edit a file another Blender session has open. I can see your screen, but not reach into that process. "
         "If you save it and give me the path, I can load it. Otherwise I start a fresh project. That keeps your work safe.")


def _model_says(monkeypatch, brain, reply):
    from brain.llm import Brain

    def run(self, voice, start):
        self.messages.append({"role": "assistant", "content": reply})
        for sentence in speech._SPLIT.split(reply):
            voice(sentence)
    monkeypatch.setattr(Brain, "_run", run)


def test_tell_me_more_gives_what_the_budget_kept_back(brain, monkeypatch):
    _model_says(monkeypatch, brain, REPLY)
    said = []
    brain.ask("What do you think about Blender files?", said.append)
    assert said[-1] == "If you save it and give me the path, I can load it."
    assert "That keeps your work safe." not in brain.messages[-1]["content"]     # history = what he heard
    more = []
    brain.ask("Tell me more.", more.append)
    assert " ".join(more) == "Otherwise I start a fresh project. That keeps your work safe."
    assert brain.messages[-2:] == [{"role": "user", "content": "Tell me more."},
                                   {"role": "assistant", "content": " ".join(more)}]
    assert brain.front_doors == [("quick", "What do you think about Blender files?"),
                                 ("pcfast", "What do you think about Blender files?")]


def test_tell_me_more_after_a_cut_in_still_needs_go_on(brain, monkeypatch):
    """A cut-off remainder is picked up by "go on" (as before), not by a bare "and?" that may be a new thought."""
    brain.messages = [{"role": "user", "content": "Explain it."}, {"role": "assistant", "content": "One. Two. Three."}]
    brain._open_turn = 1
    brain.cancel("One.", "Two. Three.")
    _model_says(monkeypatch, brain, "Something new.")
    said = []
    brain.ask("and?", said.append)
    assert said == ["Something new."]


def test_a_reply_that_asked_him_something_keeps_nothing_back(brain, monkeypatch):
    _model_says(monkeypatch, brain, "Fifty. That's half. Which number did you mean? Easy one.")
    brain.ask("What's 50% of 100?", [].append)
    assert brain._unsaid is None


def test_the_depth_note_is_short_unless_he_asked_for_the_long_form():
    from core import chatturn
    history = [{"role": "user", "content": "Why is the sky blue?"}]
    assert chatturn.SHORT_DEPTH_NOTE in chatturn.note(chatturn.DEPTH, history)
    history = [{"role": "user", "content": "Explain why the sky is blue, in detail."}]
    assert chatturn.DEPTH_NOTE in chatturn.note(chatturn.DEPTH, history)


# -- a question asked back after his question was answered ----------------------------------------------------------------
def test_a_question_back_at_the_end_of_an_answer_is_dropped():
    # sandbox 2026-11-21: "Yes, sir. I'm ready to learn anything you want me to right now. What do you have in mind?"
    out, _p = polish("Are you able to learn now?", "Yes. I learn what you teach me. What do you have in mind?")
    assert out == ["Yes.", "I learn what you teach me."]


@pytest.mark.parametrize("question", ["Which day did you mean?", "How about you?", "Should I delete the old backups first?"])
def test_a_question_his_answer_needs_or_a_reciprocal_one_stays(question):
    out, _p = polish("Is it supposed to rain?", f"Yes, light rain. {question}")
    assert out[-1] == question


def test_a_question_back_in_mid_reply_is_said_in_order():
    out, _p = polish("What do you think about the world?", "It's messy. Why do I say that? People keep fixing it anyway.")
    assert out == ["It's messy.", "Why do I say that?", "People keep fixing it anyway."]


def test_the_rest_is_only_for_the_very_next_line(brain, monkeypatch):
    _model_says(monkeypatch, brain, REPLY)
    brain.ask("What do you think about Blender files?", [].append)
    assert brain._unsaid is brain._more is not None
    _model_says(monkeypatch, brain, "Fifty.")
    brain.ask("What's 50% of 100?", [].append)
    assert brain._unsaid is None and brain._more is None
    said = []
    brain.ask("Tell me more.", said.append)
    assert "That keeps your work safe." not in " ".join(said)


# -- review round 1 (ecc:python-reviewer) ---------------------------------------------------------------------------------
def test_a_tool_report_after_trimming_began_is_said():
    ran = [False]
    out = []
    p = speech.Polisher(out.append, asked="What's 50% of 100?", budget=speech.CHAT_BUDGET, fresh=lambda: ran[0])
    for s in ("Fifty.", "That's half of a hundred.", "Easy one to work out."):
        p(s)
    ran[0] = True
    p("Done, the calculator is open.")
    p.finish()
    assert out[-1] == "Done, the calculator is open."


@pytest.mark.parametrize("question", ["How far are you going?", "What's your budget?", "Do you drive or fly?",
                                      "Where are you based?"])
def test_a_clarifying_question_is_not_dropped_as_a_question_back(question):
    out, _p = polish("How long will the trip take?", f"It depends on the distance. {question}")
    assert out == ["It depends on the distance.", question]


def test_an_offer_past_the_budget_is_never_kept_for_more():
    out, p = polish("What's 50% of 100?", "Fifty. That's half. It's an easy sum to do. Want me to open the calculator?")
    assert out == ["Fifty.", "That's half."]
    assert p.trimmed == ["It's an easy sum to do."]


def test_a_recall_answer_opening_on_his_words_is_not_dropped():
    assert not speech.restates("You mentioned your sister's wedding is in Brentwood.",
                               "When is my sister's wedding? You mentioned it.")


def test_the_rest_expires_sooner_than_a_cut_off_reply(brain, monkeypatch):
    from brain import llm
    _model_says(monkeypatch, brain, REPLY)
    brain.ask("What do you think about Blender files?", [].append)
    brain._unsaid = brain._more = (brain._more[0], time.monotonic() - llm.MORE_FOR - 1)
    _model_says(monkeypatch, brain, "A new answer.")
    said = []
    brain.ask("Tell me more.", said.append)
    assert said == ["A new answer."]


def test_what_was_kept_back_only_guards_the_next_model_turn(brain, monkeypatch):
    seen = []
    real = speech.Polisher.__init__

    def init(self, *a, **k):
        seen.append(list(k.get("withheld") or []))
        real(self, *a, **k)
    monkeypatch.setattr(speech.Polisher, "__init__", init)
    _model_says(monkeypatch, brain, REPLY)
    brain.ask("What do you think about Blender files?", [].append)
    _model_says(monkeypatch, brain, "Fifty.")
    brain.ask("What's 50% of 100?", [].append)
    brain.ask("What's 50% of 100?", [].append)
    assert seen[0] == [] and "That keeps your work safe." in seen[1] and seen[2] == []
