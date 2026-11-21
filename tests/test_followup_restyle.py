"""Restyle follow-ups with no lesson open ("Simpler.", "More detail.", "Say that again.", ...): about Jarvis' previous answer.
Lean chat path with that exchange in full (no manual, no planner), a real restyle never a repeat, and with no answer before
the line one short honest line with no model call. Lessons, offers and lookalike commands keep their route. No real model."""
import pytest

from core import chatturn, gateway, gateway_context, intelligence
from tests.test_chat_first import MANUAL, PERSONA, _ask, _system, _text, _transport, local, no_skill  # noqa: F401
from tests.test_chat_latency import LESSON_REPLY, SKY_REPLY, _context

SIX = ["Simpler.", "More detail.", "Shorter.", "Say that again.", "Explain that differently.", "Give me an example."]
LOOKALIKES = ["Make the font simpler.", "More detail on the invoice in Excel.", "Shorter timer.", "Give me an example file.",
              "Make the playlist shorter.", "Say that again to Maya in a text.", "Simpler layout for the slide."]


@pytest.mark.parametrize("said,style", [("Simpler.", "simpler"), ("More detail.", "longer"), ("Shorter.", "shorter"),
                                        ("Say that again.", "again"), ("Explain that differently.", "other"),
                                        ("Give me an example.", "example"), ("Okay, simpler please.", "simpler"),
                                        ("Could you say it once more?", "again"), ("In other words.", "other")])
def test_the_phrases_are_recognised(said, style):
    assert chatturn.restyle(said) == style


@pytest.mark.parametrize("said", LOOKALIKES + ["Why is the sky blue?", "Again", "Go on.", "Right.", "Explain subnetting.",
                                               "Explain it like I'm five.", "Open Notepad.", ""])
def test_commands_and_questions_with_the_same_words_are_not_restyles(said):
    assert chatturn.restyle(said) == ""


def test_the_target_is_the_previous_answer_nothing_a_lesson_or_an_offer():
    assert chatturn.restyle_target("Simpler.", _context("answer")) == "answer"
    assert chatturn.restyle_target("Simpler.", _context("none")) == "nothing"
    assert chatturn.restyle_target("Simpler.", _context("action")) == ""            # a clipped command: keeps its tool route
    assert chatturn.restyle_target("Simpler.", [{"role": "user", "content": "Open Paint."},
                                                {"role": "assistant", "content": "Done."}]) == ""
    assert chatturn.restyle_target("Simpler.", _context("lesson")) == ""
    assert chatturn.restyle_target("Simpler.", _context("offer")) == ""
    assert chatturn.restyle_target("Make the font simpler.", _context("answer")) == ""


def test_a_long_answer_that_ends_in_an_offer_is_still_an_answer():
    long = SKY_REPLY + " Want me to go into the physics of it?"
    history = [{"role": "user", "content": "Why is the sky blue?"}, {"role": "assistant", "content": long}]
    assert chatturn.restyle_target("Shorter.", history) == "answer"


@pytest.mark.parametrize("context", ["none", "answer"])
@pytest.mark.parametrize("said", SIX)
def test_no_planner_for_a_restyle(local, said, context):
    history = _context(context)
    assert gateway.plainly_talk(said, gateway_context.build(said, history), history)


@pytest.mark.parametrize("said", SIX)
def test_a_restyle_takes_the_lean_chat_path_with_the_previous_exchange_in_full(local, no_skill, monkeypatch, said):
    sent = _transport(monkeypatch, "Light bends in the air, so the sky looks blue.")
    msg = _ask(said, _context("answer"))
    assert len(sent) == 1
    body = sent[0]
    assert body["profile"] in ("chat", "depth") and body["tools"] == []
    assert _system(body).startswith("PERSONA-CORE") and "MANUAL-OPERATIONS" not in _system(body)
    texts = [m["content"] for m in body["messages"] if isinstance(m.get("content"), str)]
    assert any(t == "Why is the sky blue?" for t in texts) and any(t == SKY_REPLY for t in texts)
    assert "previous answer" in str(body["messages"][-1]["content"])          # the restyle note rides on his line
    assert _text(msg)


def test_older_turns_and_tool_rounds_are_left_out_of_a_restyle(local, no_skill, monkeypatch):
    sent = _transport(monkeypatch, "Blue light scatters most.")
    earlier = [{"role": "user", "content": "Play some jazz."}, {"role": "assistant", "content": "Playing jazz now, sir."}
               ] + _context("answer")
    _ask("Simpler.", earlier)
    users = [m["content"] for m in sent[0]["messages"] if m.get("role") == "user" and isinstance(m["content"], str)]
    assert not any("jazz" in u for u in users)


def test_more_detail_and_examples_get_the_longer_profile_the_rest_the_short_one():
    assert chatturn.restyle_profile("More detail.") == chatturn.DEPTH == chatturn.restyle_profile("Give me an example.")
    assert all(chatturn.restyle_profile(s) == chatturn.CHAT for s in ("Simpler.", "Shorter.", "Say that again."))


def test_a_verbatim_repeat_is_written_once_more_in_other_words(local, no_skill, monkeypatch):
    answers = iter([SKY_REPLY, "Air bounces blue light around the most, so that is what you see."])
    sent = _transport(monkeypatch, lambda body: next(answers))
    msg = _ask("Say that again.", _context("answer"))
    assert len(sent) == 2 and _text(msg).startswith("Air bounces")
    assert "already said" in str(sent[1]["messages"][-1]["content"])


def test_a_real_restyle_costs_one_call(local, no_skill, monkeypatch):
    sent = _transport(monkeypatch, "Blue light gets scattered the most.")
    _ask("Simpler.", _context("answer"))
    assert len(sent) == 1


def test_a_lesson_follow_up_keeps_the_manual_path(local, monkeypatch):
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    assert chatturn.plan("fast", _context("lesson") + [{"role": "user", "content": "Simpler."}], [], False, None) is None


@pytest.mark.parametrize("said", LOOKALIKES)
def test_lookalikes_do_not_take_the_restyle_path(local, said):
    history = _context("answer") + [{"role": "user", "content": said}]
    assert not chatturn.restyle_target(said, history)
    assert chatturn.RESTYLE_NOTES["simpler"] not in chatturn.note(chatturn.CHAT, history)


def test_the_nothing_line_is_one_short_honest_sentence_pair_with_no_topic():
    for said in SIX:
        line = chatturn.nothing_to_restyle(said, "sir")
        assert len(line.split()) <= 14 and "sir" in line and "{t}" not in line


# ------------------------------------------------------------------------------------------- the Brain, no model call
def _brain(monkeypatch):
    import brain.llm as llm
    from tests import test_chat_first  # noqa: F401
    brain = llm.Brain.__new__(llm.Brain)
    brain.messages = []
    brain._tongue = "en"
    brain._title = lambda: "sir"
    return brain


@pytest.mark.parametrize("said", SIX)
def test_the_brain_answers_a_first_line_restyle_at_once_with_no_model(local, monkeypatch, said):
    brain = _brain(monkeypatch)
    spoken = []
    brain.messages.append({"role": "user", "content": said})
    assert brain._restyle_nothing(said, spoken.append)
    assert len(spoken) == 1 and spoken[0] == brain.messages[-1]["content"] and brain.messages[-1]["role"] == "assistant"


def test_the_brain_leaves_a_restyle_with_an_answer_or_a_lesson_alone(local, monkeypatch):
    for context in ("answer", "lesson", "offer"):
        brain = _brain(monkeypatch)
        brain.messages = _context(context) + [{"role": "user", "content": "Simpler."}]
        assert not brain._restyle_nothing("Simpler.", lambda s: None)


def test_the_brain_does_not_take_a_command_with_the_words(local, monkeypatch):
    brain = _brain(monkeypatch)
    brain.messages = [{"role": "user", "content": "Make the font simpler."}]
    assert not brain._restyle_nothing("Make the font simpler.", lambda s: None)


def _ex(reply):
    return [{"role": "user", "content": "q"}, {"role": "assistant", "content": reply}]


@pytest.mark.parametrize("said", ["Shorter.", "Longer.", "Simpler."])
def test_a_clipped_command_after_an_action_reply_keeps_its_tool_route(said):
    history = _ex("Setting a timer for ten minutes, sir.")
    assert chatturn.restyle_target(said, history) == ""
    assert chatturn.command_like(said, history) is True


@pytest.mark.parametrize("reply,said", [
    ("The war started in 1914 when a Serbian nationalist shot the archduke, and alliances pulled the great powers in.", "Simpler."),
    ("Sunlight scatters in the air and blue light scatters most, so the sky is blue when the sun is on.", "Say that again."),
    ("Your meeting is set for three o'clock with the design team, sir.", "Shorter."),
    ("Four.", "Say that again."),
])
def test_a_short_real_answer_is_restyled_not_called_nothing(reply, said):
    assert chatturn.restyle_target(said, _ex(reply)) == "answer"


# ------------------------------------------------------------------ a chain of restyles stays on the subject (fix/restyle-anchor)
LONG_FACT = "The sky is blue because air scatters short blue wavelengths of sunlight far more than long red ones."
SIMPLER = "Air bounces blue light around more than red light, so the sky looks blue."
CHAIN = [{"role": "user", "content": "Why is the sky blue?"}, {"role": "assistant", "content": SKY_REPLY},
         {"role": "user", "content": "Simpler."}, {"role": "assistant", "content": SIMPLER},
         {"role": "user", "content": "Give me an example."}, {"role": "assistant", "content": "Think of milk in a glass, it looks white."}]


def _texts(body):
    return [(m["role"], m["content"]) for m in body["messages"][1:] if isinstance(m.get("content"), str)]


def test_the_subject_is_the_last_question_that_was_not_a_restyle():
    s = chatturn.restyle_subject(CHAIN + [{"role": "user", "content": "More detail."}], "More detail.")
    assert s["question"] == "Why is the sky blue?" and s["answer"] == SKY_REPLY
    assert s["latest"] == "Think of milk in a glass, it looks white." and len(s["chain"]) == 2


def test_the_subject_search_stops_at_the_first_non_restyle_exchange():
    earlier = [{"role": "user", "content": "What is inflation?"}, {"role": "assistant", "content": "Prices rising over time."},
               {"role": "user", "content": "Simpler."}, {"role": "assistant", "content": "Things cost more."}]
    s = chatturn.restyle_subject(earlier + CHAIN + [{"role": "user", "content": "Shorter."}], "Shorter.")
    assert s["question"] == "Why is the sky blue?" and len(s["chain"]) == 2


def test_no_chain_when_the_previous_reply_is_the_original_answer():
    s = chatturn.restyle_subject(_context("answer") + [{"role": "user", "content": "Simpler."}], "Simpler.")
    assert s["chain"] == [] and s["latest"] == SKY_REPLY and s["answer"] == SKY_REPLY


@pytest.mark.parametrize("said", SIX)
def test_a_chained_restyle_carries_the_original_question_answer_and_latest_reply(local, no_skill, monkeypatch, said):
    sent = _transport(monkeypatch, "Air scatters the blue light of the sky most.")
    _ask(said, CHAIN)
    texts = _texts(sent[0])
    assert ("user", "Why is the sky blue?") in texts and ("assistant", SKY_REPLY) in texts
    assert ("assistant", "Think of milk in a glass, it looks white.") in texts
    assert not any("Simpler" == t for _, t in texts)                       # only the latest restyle line rides along
    note = str(sent[0]["messages"][-1]["content"])
    assert "Why is the sky blue?" in note and "different from" in note


def test_the_say_that_again_note_forbids_talk_about_earlier_attempts():
    for history in (_context("answer"), CHAIN):
        note = chatturn.note(chatturn.CHAT, history + [{"role": "user", "content": "Say that again."}])
        assert "earlier attempt" in note and "never" in note.lower()


def test_an_overlong_original_answer_is_cut_in_the_middle_never_the_question():
    long = " ".join(f"Sentence {i} about scattering of blue light in the air." for i in range(900))
    history = [{"role": "user", "content": "Why is the sky blue?"}, {"role": "assistant", "content": long},
               {"role": "user", "content": "Simpler."}, {"role": "assistant", "content": SIMPLER}]
    out = chatturn.restyle_history([{"role": "system", "content": "P"}] + history + [{"role": "user", "content": "More detail."}],
                                   "More detail.")
    chars = sum(len(m["content"]) for m in out)
    assert chars / 3.6 <= chatturn.RESTYLE_TOKENS + 50
    answer = next(m["content"] for m in out if m["role"] == "assistant" and "Sentence 0" in m["content"])
    assert "Sentence 899" in answer and " [...] " in answer and ("user", "Why is the sky blue?") in [(m["role"], m["content"]) for m in out]


def test_a_short_original_answer_is_kept_whole():
    out = chatturn.restyle_history([{"role": "system", "content": "P"}] + CHAIN + [{"role": "user", "content": "More detail."}], "More detail.")
    assert SKY_REPLY in [m["content"] for m in out]


def test_an_off_subject_reply_is_written_once_more_with_the_subject_named(local, no_skill, monkeypatch):
    answers = iter(["Picture a crowded room where everyone talks at once.", "Air scatters blue sunlight so the sky looks blue."])
    sent = _transport(monkeypatch, lambda body: next(answers))
    msg = _ask("Explain that differently.", CHAIN)
    assert len(sent) == 2 and _text(msg).startswith("Air scatters")
    assert "Why is the sky blue?" in str(sent[1]["messages"][-1]["content"])


def test_still_off_subject_keeps_the_better_of_the_two_by_overlap(local, no_skill, monkeypatch):
    answers = iter(["A crowded room is loud.", "A crowded party is loud, but light is quiet."])
    sent = _transport(monkeypatch, lambda body: next(answers))
    assert _text(_ask("Explain that differently.", CHAIN)).startswith("A crowded party") and len(sent) == 2


def test_two_unrelated_replies_keep_the_first(local, no_skill, monkeypatch):
    answers = iter(["A crowded room is loud.", "A busy market is noisy."])
    sent = _transport(monkeypatch, lambda body: next(answers))
    assert _text(_ask("Explain that differently.", CHAIN)).startswith("A crowded room") and len(sent) == 2


@pytest.mark.parametrize("said", SIX)
def test_an_on_subject_chained_restyle_costs_one_model_call(local, no_skill, monkeypatch, said):
    sent = _transport(monkeypatch, "Air scatters blue light in the sky.")     # short enough for the Shorter cap too
    _ask(said, CHAIN)
    assert len(sent) == 1


def test_the_stream_is_held_until_a_word_of_the_subject_is_said():
    said = []
    words = chatturn.subject_words("Why is the sky blue? " + SKY_REPLY)
    hold = chatturn.Hold(said.append, [], "Explain that differently.", True, subject=words)
    for piece in ("Picture a crowded room ", "where everyone talks. ", "Sunlight bends too. "):
        hold.feed(piece)
        if "Sunlight" not in piece:
            assert said == []
    assert said and "Sunlight" in "".join(said)


# ------------------------------------------------------------------ review fixes: the check is for chains, the hold is bounded, commentary is filtered
FIRST = _context("answer")


def test_a_first_restyle_is_never_checked_for_the_subject_so_an_analogy_costs_one_call(local, no_skill, monkeypatch):
    sent = _transport(monkeypatch, "Think of it as a desk where you keep papers you are using right now.")
    msg = _ask("Simpler.", FIRST)
    assert len(sent) == 1 and _text(msg).startswith("Think of it as a desk")


def test_a_chained_on_topic_reply_with_a_synonym_only_is_held_at_most_ten_words():
    said = []
    words = chatturn.subject_words("Why is the sky blue? " + SKY_REPLY)
    hold = chatturn.Hold(said.append, CHAIN, "Explain that differently.", True, subject=words, restyle=True)
    for piece in ("Think of it as a desk. ", "You keep the papers ", "you are using right there. ", "Everything else sits in a drawer."):
        hold.feed(piece)
    assert said and "desk" in "".join(said)


def test_a_first_restyle_stream_is_not_held_for_subject_words():
    said = []
    hold = chatturn.Hold(said.append, FIRST, "Simpler.", True, restyle=True)
    hold.feed("Think of it as a desk where papers sit.")
    assert said


def test_filler_words_are_not_subject_words():
    words = chatturn.subject_words("Why is the sky blue? Air molecules scatter short blue wavelengths of sunlight more than red.")
    assert not words & {"more", "short", "use", "used", "work", "term"}
    assert chatturn.subject_overlap("Nothing more to say, in short.", words) == 0
    assert chatturn.subject_words("Los precios con una used work term") == {"precio"}


@pytest.mark.parametrize("text", [
    "The previous explanation focused on the general concept.",
    "My earlier attempt was too vague, so here is more.",
    "To rephrase, a vaccine trains the body.",
    "As I said, the sky scatters blue light.",
    "You asked for more detail, so here it is.",
    "The last time explained how air scatters light.",
    "Let me rephrase that: air scatters blue light.",
    "The first sentence simply stated the basics.",
])
def test_commentary_openers_are_recognised(text):
    assert chatturn.commentary(text)


@pytest.mark.parametrize("text", [
    "Air scatters blue light more than red.",
    "The sky looks blue because of scattering.",
    "In the previous century, scientists measured it.",
    "The first thing to know is that light scatters.",
])
def test_real_answers_are_not_commentary(text):
    assert not chatturn.commentary(text)


@pytest.mark.parametrize("chain", [FIRST, CHAIN])
def test_commentary_is_written_once_more_without_it(local, no_skill, monkeypatch, chain):
    answers = iter(["The previous explanation focused on the sky and light.", "Air scatters blue sunlight, so the sky looks blue."])
    sent = _transport(monkeypatch, lambda body: next(answers))
    msg = _ask("More detail.", chain)
    assert len(sent) == 2 and _text(msg).startswith("Air scatters")
    assert "spoke about your earlier replies" in str(sent[1]["messages"][-1]["content"])


def test_commentary_that_comes_back_again_keeps_the_least_commentary_never_silence(local, no_skill, monkeypatch):
    answers = iter(["The previous explanation was too brief.", "To rephrase."])
    sent = _transport(monkeypatch, lambda body: next(answers))
    text = _text(_ask("More detail.", CHAIN))
    assert len(sent) == 2 and text.strip() and "previous explanation" in text    # the least commentary of two all-commentary tries


def test_the_stream_drops_a_commentary_opener_and_says_what_follows():
    said = []
    hold = chatturn.Hold(said.append, FIRST, "More detail.", True, restyle=True)
    for piece in ("The previous ", "explanation focused ", "on the sky and light. ", "It scatters blue light more."):
        hold.feed(piece)
    assert "".join(said) == "It scatters blue light more."


def test_the_stream_waits_for_the_end_of_an_opener_sentence_but_not_for_content():
    said = []
    hold = chatturn.Hold(said.append, FIRST, "More detail.", True, restyle=True)
    for piece in ("You asked for ", "more detail on how air scatters ", "sunlight, so imagine "):
        hold.feed(piece)
    assert said == []
    hold.feed("a prism.")
    assert hold.open is False or "".join(said).startswith("Imagine a prism")
    said2 = []
    hold = chatturn.Hold(said2.append, FIRST, "More detail.", True, restyle=True)
    hold.feed("Blue light scatters ")
    hold.feed("more than red.")
    assert said2 and said2[0].startswith("Blue light")


def test_a_runaway_opener_is_not_held_forever():
    said = []
    hold = chatturn.Hold(said.append, FIRST, "More detail.", True, restyle=True)
    for _ in range(12):
        hold.feed("you asked for more of the same thing again and again ")
    assert said


def test_the_stream_drops_an_echoed_style_word():
    said = []
    hold = chatturn.Hold(said.append, FIRST, "Shorter.", True, restyle=True)
    for piece in ("Shorter. ", "Air molecules ", "scatter blue light."):
        hold.feed(piece)
    assert "".join(said).startswith("Air molecules")


def test_a_capped_stream_releases_the_first_sentence_then_only_sentences_that_fit():
    said = []
    hold = chatturn.Hold(said.append, FIRST, "Simpler.", True, restyle=True, cap=12)
    for piece in ("Air scatters ", "blue light most. ", "Red light goes straight on through. ", "It is a long story that goes on and on. "):
        hold.feed(piece)
    heard = "".join(said).strip()
    assert heard == "Air scatters blue light most. Red light goes straight on through."
    assert chatturn.fit_length(" ".join(["Air scatters blue light most.", "Red light goes straight on through.", "It is a long story that goes on and on."]), 12) == heard


# ------------------------------------------------------------------ wording: no commentary opener, no echoed style word, length rules
OPENERS = [
    ("You asked for more detail on how air molecules scatter sunlight, so let me fill in what was missing from the brief explanation. "
     "The previous answer mentioned blue light. Blue light scatters more because its waves are short.",
     "Blue light scatters more because its waves are short."),
    ("You asked for a different angle, so imagine the air as a crowd of tiny pebbles.", "Imagine the air as a crowd of tiny pebbles."),
    ("You asked for an example, so I gave you a simple picture rather than the full machinery. Picture a burglar alarm that learns.",
     "Picture a burglar alarm that learns."),
    ("Sure, air scatters blue light.", "Air scatters blue light."),
    ("Certainly! Air scatters blue light.", "Air scatters blue light."),
    ("Of course. Absolutely, air scatters blue light.", "Air scatters blue light."),
    ("Here is a simpler version: air bounces blue light about.", "Air bounces blue light about."),
    ("Here's the example. Air bounces blue light about.", "Air bounces blue light about."),
    ("Let me explain. Air bounces blue light about.", "Air bounces blue light about."),
    ("Let me put it plainly, air bounces blue light about.", "Air bounces blue light about."),
    ("In other words, air bounces blue light about.", "Air bounces blue light about."),
    ("To rephrase, air scatters light.", "Air scatters light."),
    ("My earlier attempt was too brief. Air scatters light.", "Air scatters light."),
    ("As requested, air scatters light in every direction.", "Air scatters light in every direction."),
    ("The first explanation was brief. The earlier reply skipped the reason. Air scatters blue light most.", "Air scatters blue light most."),
]


@pytest.mark.parametrize("text,clean", OPENERS)
def test_a_commentary_opener_is_dropped_and_the_content_kept(text, clean):
    assert chatturn.strip_opener(text) == clean


@pytest.mark.parametrize("text,said", [
    ("Shorter. Air molecules scatter blue light.", "Shorter."), ("Simpler: air bounces blue light about.", "Simpler."),
    ("More detail: air scatters blue light because its waves are short.", "More detail."),
    ("Example: picture a prism on a window sill.", "Give me an example."), ("Again: air scatters blue light.", "Say that again."),
    ("Give me an example. Picture a prism on a window sill.", "Give me an example."),
    ("Explain that differently. Air scatters blue light.", "Explain that differently."),
])
def test_an_echoed_style_word_or_line_is_dropped(text, said):
    clean = chatturn.strip_opener(text, said)
    assert clean and clean[0].isupper() and not chatturn.commentary(clean) and clean.split()[0].rstrip(".:").lower() not in ("shorter", "simpler", "example", "again", "more")


@pytest.mark.parametrize("text", [
    "Air scatters blue light more than red.", "The first thing to know is that light scatters.", "Sure enough, the sky is blue because of it.",
    "In the previous century, scientists measured it.", "Think of it as a desk. You keep the papers you use.",
    "Imagine standing on a beach at sunset.", "Shorter wavelengths scatter more. That is the whole reason.",
    "You asked why: because air scatters blue light.", "Here are the facts people forget, sir, air scatters blue light.".replace("Here are", "Those are"),
])
def test_a_reply_that_says_the_thing_is_left_alone(text):
    assert chatturn.strip_opener(text) == text


def test_all_commentary_leaves_nothing_for_the_caller_to_regenerate():
    assert chatturn.strip_opener("You asked for an example, so I gave you a picture. The previous answer was brief.") == ""


def test_the_answer_sentence_inside_an_opener_clause_is_never_cut():
    assert chatturn.strip_opener("You asked for an example: a fridge keeps milk cold by moving heat out.") == "A fridge keeps milk cold by moving heat out."


def test_length_caps_are_set_in_code_from_the_right_reference():
    history = CHAIN                                                   # original answer 28 words, latest reply 9 words
    assert chatturn.length_cap("Shorter.", history) == len("Think of milk in a glass, it looks white.".split()) - 1
    assert chatturn.length_cap("Simpler.", history) == min(60, len(SKY_REPLY.split()))
    assert chatturn.length_cap("More detail.", history) is None
    long = [{"role": "user", "content": "q"}, {"role": "assistant", "content": " ".join(["Word."] * 90)}]
    assert chatturn.length_cap("Simpler.", long) == 60
    assert chatturn.length_cap("Shorter.", long) == 89
    assert chatturn.length_cap("Shorter.", _ex("Four.")) is None


def test_fit_length_cuts_at_a_sentence_end_never_mid_sentence():
    text = "One two three four. Five six seven eight. Nine ten eleven twelve."
    assert chatturn.fit_length(text, 9) == "One two three four. Five six seven eight."
    assert chatturn.fit_length(text, 3) == "One two three four."           # the first sentence always stays whole
    assert chatturn.fit_length(text, None) == text
    assert chatturn.fit_length("A story without an end", 2) == "A story without an end"


def _reply_texts(monkeypatch, said, history, answers):
    answers = iter(answers)
    sent = _transport(monkeypatch, lambda body: next(answers))
    return sent, _text(_ask(said, history))


def test_a_reply_that_opens_with_commentary_is_cleaned_with_no_second_call(local, no_skill, monkeypatch):
    sent, text = _reply_texts(monkeypatch, "More detail.", CHAIN,
                              ["You asked for more detail on the sky, so let me add what was missing. Air scatters blue sunlight most."])
    assert len(sent) == 1 and text == "Air scatters blue sunlight most."


def test_a_reply_that_is_all_commentary_is_written_once_more(local, no_skill, monkeypatch):
    sent, text = _reply_texts(monkeypatch, "Give me an example.", FIRST,
                              ["You asked for an example, so I gave you a picture.", "Picture a prism splitting the sunlight on a window."])
    assert len(sent) == 2 and text == "Picture a prism splitting the sunlight on a window."


def test_shorter_is_strictly_shorter_than_the_latest_reply_and_ends_at_a_sentence(local, no_skill, monkeypatch):
    reply = "Air scatters blue light. It scatters red light far less than blue. The sky looks blue because of that scattering."
    sent, text = _reply_texts(monkeypatch, "Shorter.", CHAIN, [reply])
    latest = len("Think of milk in a glass, it looks white.".split())
    assert len(sent) == 1 and text == "Air scatters blue light." and len(text.split()) < latest


def test_shorter_rewrites_once_when_one_sentence_is_already_too_long(local, no_skill, monkeypatch):
    long = "Air scatters the short blue wavelengths of the sunlight far more than the long red wavelengths do overall."
    sent, text = _reply_texts(monkeypatch, "Shorter.", CHAIN, [long, "Air scatters blue light."])
    assert len(sent) == 2 and text == "Air scatters blue light." and "At most 8 words" in str(sent[1]["messages"][-1]["content"])


def test_simpler_is_not_longer_than_the_original_and_at_most_sixty_words(local, no_skill, monkeypatch):
    body = " ".join(f"Air scatters blue light number {i}." for i in range(30))
    sent, text = _reply_texts(monkeypatch, "Simpler.", FIRST, [body])
    assert len(text.split()) <= min(60, len(SKY_REPLY.split())) and text.endswith(".") and text.startswith("Air scatters blue light number 0.")


def test_a_style_echo_is_dropped_from_the_finished_reply(local, no_skill, monkeypatch):
    sent, text = _reply_texts(monkeypatch, "Shorter.", FIRST, ["Shorter. Air scatters blue light."])
    assert len(sent) == 1 and text == "Air scatters blue light."


def test_routing_is_unchanged_by_the_wording_rules():
    for said in SIX:
        assert chatturn.restyle(said)
    for said in LOOKALIKES:
        assert chatturn.restyle(said) == ""


def test_the_subject_is_found_when_assistant_content_is_a_list_of_text_block_objects():
    from types import SimpleNamespace
    block = lambda t: [SimpleNamespace(type="text", text=t)]          # what the brain stores for his own replies
    history = [{"role": "user", "content": "Why is the sky blue?"}, {"role": "assistant", "content": block(SKY_REPLY)},
               {"role": "user", "content": "Simpler."}]
    s = chatturn.restyle_subject(history, "Simpler.")
    assert s and s["answer"] == SKY_REPLY and chatturn.subject_check(history, "Simpler.")
    assert chatturn.length_cap("Simpler.", history) == min(60, len(SKY_REPLY.split()))


# ---- review fixes: stream spacing, example openers, abbreviations, over-eager cleanup ----

def _stream(pieces, said="Simpler.", cap=None, history=None):
    out = []
    hold = chatturn.Hold(out.append, FIRST if history is None else history, said, True, restyle=True, cap=cap)
    for p in pieces:
        hold.feed(p)
    return out


@pytest.mark.parametrize("cap", [None, 30])
def test_the_restyle_stream_keeps_the_spaces_between_pieces(cap):
    pieces = ["Air ", "scatters ", "blue ", "light ", "more ", "than ", "red."]
    out = _stream(pieces, cap=cap)
    assert "".join(out) == "".join(pieces)
    assert "morethan" not in "".join(out)


def test_the_stream_after_an_opener_keeps_the_spaces():
    pieces = ["Sure, let me put it simply. ", "Air scatters blue ", "light ", "more ", "than ", "red."]
    joined = "".join(_stream(pieces))
    assert "bluelight" not in joined and "blue light" in joined


def test_a_released_sentence_end_is_not_glued_to_the_next_sentence():
    pieces = ["Air scatters blue light most.", " That ", "is ", "why ", "the ", "sky ", "looks ", "blue."]
    assert "most.That" not in "".join(_stream(pieces, cap=40))


@pytest.mark.parametrize("text,said", [
    ("The first time you ride a bike, you wobble because balance is learned by feel.", "Give me an example"),
    ("Last time you boiled water, the bubbles rose because heat turns water to steam.", "Give me an example"),
    ("I put a pot of water on the stove, and watched bubbles rise as it heated.", "Give me an example"),
    ("I made tea this morning and the steam curled up from the cup.", "Give me an example"),
    ("I tried a balloon once and it grew as I blew into it.", "Give me an example"),
])
def test_example_openers_are_not_commentary(text, said):
    assert chatturn.strip_opener(text, said) == text
    assert not chatturn.commentary(text)


@pytest.mark.parametrize("text", [
    "The first answer was too long, so imagine a bike wobbling as you learn.",
    "Last time I explained it badly, so picture a bike.",
    "I gave you the long version; here is the short one for you today.",
])
def test_exchange_openers_are_still_commentary(text):
    assert chatturn.commentary(text)


def test_an_example_reply_keeps_its_first_sentence():
    text = "The first time you ride a bike, you wobble. Balance is learned by feel."
    assert chatturn.strip_opener(text, "Give me an example") == text


@pytest.mark.parametrize("text,cap,want", [
    ("The U.S. dollar buys less than before because prices rose across many goods and services over time.", 12,
     "The U.S. dollar buys less than before because prices rose across many goods and services over time."),
    ("Use e.g. a balloon: it shows the air pushing outward on all sides evenly.", 8,
     "Use e.g. a balloon: it shows the air pushing outward on all sides evenly."),
    ("Dr. Lee said so. It is true.", 5, "Dr. Lee said so."),
    ("It is cheap vs. a car. It runs on air.", 7, "It is cheap vs. a car."),
])
def test_abbreviations_do_not_end_a_sentence(text, cap, want):
    assert chatturn.fit_length(text, cap) == want
    assert chatturn.sentences("Dr. Lee said so. It is true.") == ["Dr. Lee said so.", "It is true."]


def test_an_abbreviation_does_not_end_the_first_streamed_sentence():
    out = _stream(["The U.S. ", "dollar ", "buys ", "less ", "than ", "before. ", "Prices ", "rose ", "everywhere."], cap=12)
    assert "".join(out).startswith("The U.S. dollar buys less than before.")


def test_a_comma_inside_an_announcement_is_not_the_content_boundary():
    out = chatturn.strip_opener("Here is a simple way, in plain words, to say it: air scatters blue light.")
    assert out == "Air scatters blue light."


@pytest.mark.parametrize("text", [
    "Absolutely nothing escapes a black hole once past the horizon.",
    "No problem arises when the air is clean, so the sky stays blue.",
    "Certainly not the only reason the sky is blue, but the main one is scattering.",
])
def test_content_words_after_an_interjection_word_stay(text):
    assert chatturn.strip_opener(text) == text


@pytest.mark.parametrize("text,clean", [
    ("Sure, air scatters blue light more than red.", "Air scatters blue light more than red."),
    ("Absolutely! Air scatters blue light more than red.", "Air scatters blue light more than red."),
    ("No problem. Air scatters blue light more than red.", "Air scatters blue light more than red."),
])
def test_a_real_interjection_is_still_dropped(text, clean):
    assert chatturn.strip_opener(text) == clean


def test_a_short_answer_after_a_colon_is_kept():
    assert chatturn.strip_opener("Here is the answer: yes.") == "Yes."
