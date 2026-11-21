"""Wave 3: Jarvis answers HIS LATEST message (scripts/latest_answer_eval.py measures it on the real model).

Real qwen3.5:9b replies from the sandboxed eval (2026-11-10) are the inputs here: an answer to an earlier question said
again for a new one ("What's a synonym for happy?" -> "Gracias, sir."), an offer to keep or show what was just said on
nearly every reply ("Shall I save that fact?", "Shall I have you open a map to show it?"), and the polisher splicing
"As I said, sir:" into the middle of a reply."""
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from core import speech

FALCON = [("Let's call my new garage project Falcon.", "Falcon is noted as your garage project, sir.")]
SPANISH = ("How do you say thank you in Spanish?", '"Gracias" is thank you in Spanish, sir.')
MOON = ("What year did the first moon landing happen?", "The first moon landing was in 1969, sir.")
ROOT = ("What's the square root of 144?", "The square root of 144 is 12, sir.")
SLEEP = ("Give me one tip for sleeping better.", "Try to get eight hours of sleep each night, sir.")
FEATHERS = ("Which is heavier, a pound of feathers or a pound of bricks?",
            "A pound of feathers and a pound of bricks weigh exactly the same, sir.")


def history(*pairs):
    out = []
    for q, r in pairs:
        out += [{"role": "user", "content": q}, {"role": "assistant", "content": r}]
    return out


def polisher(earlier, asked, retry=True, **kw):
    said = []
    return said, speech.Polisher(said.append, owner=True, title="sir", asked=asked, earlier=earlier, retry=retry, **kw)


# ---- H4: 'As I said' only ever opens a whole repeat ------------------------------------------------------------------------
def test_a_copy_after_new_content_is_dropped_never_spliced_in_with_as_i_said():
    """copy_probe 2026-11-10: "The first moon landing was in 1969, sir. As I said, sir: Falcon is noted as your garage
    project." The new answer stays; the old line is not said again, with or without his question known."""
    reply = "The first moon landing was in 1969, sir. Falcon is noted as your garage project, sir."
    for asked in ("What year did the first moon landing happen?", ""):
        out = speech.polish(reply, history(*FALCON), asked=asked)
        assert out == "The first moon landing was in 1969, sir.", (asked, out)
        assert "As I said" not in out


def test_the_same_question_again_still_gets_as_i_said_on_a_whole_repeat():
    spiel = "To keep your PC, your plans and your day in order, sir. Someone has to keep the lights on around here."
    out = speech.polish(spiel, history(("Why are you here?", spiel)), asked="Why are you here?")
    assert out == "As I said: To keep your PC, your plans and your day in order, sir."


# ---- H4: an answer to an earlier question is not said for a new one ---------------------------------------------------------
@pytest.mark.parametrize("asked,earlier,draft", [
    ("What's a synonym for happy?", [SPANISH, MOON], ['"Gracias", sir.', "Shall I save that to your notes?"]),
    ("Give me a short motivational line.", [SPANISH, MOON, ROOT], ['"Gracias", sir.']),
    ("How many ounces are in a pound?", [ROOT, SLEEP], ["The square root of 144 is 12, sir."]),
    ("Give me a short motivational line.", [SLEEP, ROOT], ["Try to get eight hours of sleep each night, sir.",
                                                            "A consistent bedtime helps your body know when to rest."]),
    ("How many ounces are in a pound?", [FEATHERS, ROOT], [FEATHERS[1], "Shall I save that fact?"]),
])
def test_an_answer_to_an_earlier_question_is_held_for_one_more_try(asked, earlier, draft):
    """Real eval replies: nothing of the draft is said, and the Brain is told to ask once more."""
    said, p = polisher(earlier, asked)
    for sentence in draft:
        p(sentence)
    p.finish()
    assert said == [] and p.stale
    p.once_more()                                           # the second try is said as written, whatever it is
    p("Sixteen ounces, sir.")
    p.finish()
    assert said == ["Sixteen ounces, sir."] and not p.stale


def test_without_a_second_try_an_old_answer_is_said_rather_than_dead_air():
    said, p = polisher([SPANISH], "What's a synonym for happy?", retry=False)
    p('"Gracias", sir.')
    p.finish()
    assert said == ['"Gracias", sir.'] and not p.stale


def test_an_old_answer_after_the_new_one_is_dropped():
    said, p = polisher([ROOT], "How many ounces are in a pound?")
    p("There are 16 ounces in a pound, sir.")
    p("The square root of 144 is 12, sir.")
    p.finish()
    assert said == ["There are 16 ounces in a pound, sir."] and not p.stale


@pytest.mark.parametrize("asked,earlier,answer", [
    ("How many legs does a spider have?", [SLEEP], "Eight legs, sir."),                   # 'eight' was said before
    ("What's the largest ocean?", [MOON, ROOT], "The Pacific Ocean is the largest, sir."),
    ("What was the capital you mentioned?", [("What's the capital of Australia?", "The capital of Australia is Canberra, sir."),
                                              MOON], "Canberra, sir."),                    # he asks back about it
    ("What's the project called again?", FALCON + [MOON], "Falcon, sir."),
    ("What did we decide at the start?", FALCON + [MOON], "Falcon is noted as your garage project, sir."),
    ("Say that again?", [MOON], "The first moon landing was in 1969, sir."),
    ("What's the capital of Australia?", [("What's the capital of Australia?", "Canberra, sir.")], "Canberra, sir."),
    ("Who's helping me with it?", [("The plan is to build the workbench on Saturday, and my brother Marcus is helping me.",
                                    "Noted: Marcus is helping you build the workbench on Saturday, sir.")],
     "Marcus is helping you build the workbench on Saturday, sir."),
])
def test_a_right_answer_is_never_taken_for_an_old_one(asked, earlier, answer):
    said, p = polisher(earlier, asked)
    p(answer)
    p.finish()
    assert said == [answer] and not p.stale


def test_after_a_tool_ran_an_answer_is_news():
    said, p = polisher([ROOT], "How many ounces are in a pound?", fresh=lambda: True)
    p("The square root of 144 is 12, sir.")
    assert said == ["The square root of 144 is 12, sir."] and not p.stale


def test_another_language_is_left_as_written():
    said, p = polisher([SPANISH], "Ki sinonim pou kontan?", english=False)
    p('"Gracias", sir.')
    assert said == ['"Gracias", sir.'] and not p.stale


def test_exchanges_reads_his_words_and_the_last_reply_of_each_exchange():
    rows = [{"role": "user", "content": [{"type": "text", "text": "<background>\nnotes\n</background>"},
                                         {"type": "text", "text": "Let's call my new garage project Falcon."}]},
            {"role": "assistant", "content": [{"type": "text", "text": "One sec, sir."},
                                              {"type": "tool_use", "id": "t1", "name": "start_project", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Started."}]},
            {"role": "assistant", "content": "Falcon is started, sir."},
            {"role": "user", "content": "What's the capital of Australia?"},
            {"role": "assistant", "content": "Canberra, sir."}]
    assert speech.exchanges(rows) == [("Let's call my new garage project Falcon.", "Falcon is started, sir."),
                                      ("What's the capital of Australia?", "Canberra, sir.")]


# ---- H6: an offer of something he did not ask for is not the end of a reply -------------------------------------------------
@pytest.mark.parametrize("asked,offer", [
    ("What's the capital of Australia?", "Shall I log that as a fact in your notes?"),
    ("What's a good name for a gray cat?", "Shall I save that name to a note?"),
    ("Tell me a fun fact about octopuses.", "Would you like to save that as a note?"),
    ("How many days are in a leap year?", "Shall I mark that date on your calendar?"),
    ("Who's helping me with it?", "Shall I log him as a collaborator for the Falcon project?"),
    ("Give me a short motivational line.", "Shall I set a reminder for you to wind down tonight?"),
    ("What's the capital of Australia?", "Shall I have you open a map to show it?"),
    ("How do you say thank you in Spanish?", "Shall I have you open a dictionary to see more phrases?"),
    ("What's the boiling point of water in Fahrenheit?", "Shall I have you open a thermometer to check your kettle?"),
    ("Name a classic science fiction movie.", "Shall I pull up its details or find it on Jellyfin?"),
    ("How long should I boil an egg?", "Want me to pull up a recipe that keeps the yolk from getting too green?"),
])
def test_an_unasked_offer_to_keep_or_show_it_is_dropped_from_the_end(asked, offer):
    answer = "Here is the answer you asked for, sir."
    assert speech.polish(f"{answer} {offer}", asked=asked) == answer
    assert speech.polish(offer, asked=asked) == offer, "the whole reply: never dead air"


@pytest.mark.parametrize("asked,text", [
    ("Remember that my brother Marcus is helping me.", "Noted, sir. Shall I save that to your notes?"),   # he asked to keep it
    ("Note that the workbench is for Saturday.", "Got it, sir. Want me to add it to the Falcon log?"),
    ("Show me where Canberra is.", "It's in the southeast, sir. Shall I open a map to show it?"),           # he asked to see it
    ("Is Jellyfin okay?", "Jellyfin lost the media drive. Want me to go ahead and restart it?"),          # a step to ask first
    ("Explain photosynthesis.", "That's the gist of photosynthesis. Want me to open a lesson page on it?"),  # the persona's
    ("What can I use for 3D modelling?", "Blender is the free choice, sir. Which do you prefer, Blender or Maya?"),
])
def test_an_offer_he_asked_for_or_a_step_to_ask_first_is_kept(asked, text):
    assert speech.polish(text, asked=asked) == text


def test_an_unasked_offer_is_kept_while_a_question_is_really_waiting():
    text = "The capital of Australia is Canberra, sir. Shall I save that to your notes?"
    assert speech.polish(text, asked="What's the capital of Australia?", keep=lambda: True) == text


# ---- the Brain: one more try, with the note, and history keeps only what was said -------------------------------------------
@pytest.fixture(autouse=True)
def scripted_spanish_line(monkeypatch):
    """These tests script the model's reply to "How do you say ... in Spanish?". The language teacher would answer that
    line itself from its course (core/langintent), before any model, so it is switched off here."""
    from brain import llm
    monkeypatch.setattr(llm.Brain, "_language_request", lambda self, text, emit: False)


def _brain(monkeypatch, replies, notes):
    from brain import llm
    from core import intelligence, pcfast, quick
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    # these tests script the model's reply to "How do you say ... in Spanish?"; the language teacher would answer that
    # line itself from its course (core/langintent), before any model
    monkeypatch.setattr(llm.Brain, "_language_request", lambda self, text, emit: False)

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        notes.append(kw.get("note", ""))
        text = replies.pop(0)
        if on_text:
            on_text(text)
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")
    monkeypatch.setattr(intelligence, "request", request)
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    return b


def test_the_brain_asks_once_more_when_the_draft_answered_an_earlier_question(monkeypatch):
    notes = []
    b = _brain(monkeypatch, ['"Gracias" is thank you in Spanish, sir.', '"Gracias", sir. Shall I save that to your notes?',
                             "Glad, sir."], notes)
    said = []
    b.ask("How do you say thank you in Spanish?", said.append)
    said.clear()
    b.ask("What's a synonym for happy?", said.append)
    assert said == ["Glad, sir."]
    assert notes[0] == notes[1] == "" and "What's a synonym for happy?" in notes[2] and "latest message" in notes[2]
    assert [m["content"] for m in b.messages if m["role"] == "user"][-1] == "What's a synonym for happy?", "the note is never kept"
    last = b.messages[-1]["content"]
    assert (last if isinstance(last, str) else " ".join(x.text for x in last)) == "Glad, sir."
    assert len(b.messages) == 4, "the stale draft is not in the history"


def test_only_one_more_try(monkeypatch):
    notes = []
    b = _brain(monkeypatch, ['"Gracias" is thank you in Spanish, sir.', '"Gracias", sir.', '"Gracias", sir.'], notes)
    b.ask("How do you say thank you in Spanish?", lambda s: None)
    said = []
    b.ask("What's a synonym for happy?", said.append)
    assert said == ['"Gracias", sir.'] and len(notes) == 3, "said as written the second time: never dead air"
    # (2026-11-15: only a LOOP is handed back, see tests/test_conv_master.py: the same non-answer, or a repeat that asks him again)


# ---- H3: a plain question naming no app asks the PC layer's model nothing ---------------------------------------------------
@pytest.mark.parametrize("question", ["What's the capital of Australia?", "What's 17 times 23?", "What's trending right now?",
                                      "What local AI are you using?", "What's a synonym for happy?"])
def test_a_plain_question_naming_no_app_is_not_a_goal_for_an_app(monkeypatch, question):
    """The legacy PC layer (core.pcsemantic, gateway off) asked the model 'open which everyday app?' before EVERY chat turn:
    two local calls per question in the eval. A question for information is never a request to act (its own prompt says
    so), so the call is not made. 'What's trending right now?' and 'What local AI are you using?' are the owner's own."""
    from core import pcsemantic
    asked = []
    monkeypatch.setattr(pcsemantic, "_ask", lambda *a: asked.append(a) or {"action": "open", "app": "calculator"})
    monkeypatch.setattr(pcsemantic, "running", lambda a: False)
    monkeypatch.setattr(pcsemantic, "_everyday_apps", lambda: ["calculator", "paint"])
    monkeypatch.setattr(pcsemantic, "mentioned_apps", lambda text, catalogue=None: [])
    assert pcsemantic.interpret(question) is None and asked == []


def test_a_goal_naming_no_app_is_still_understood(monkeypatch):
    from core import pcsemantic
    monkeypatch.setattr(pcsemantic, "_ask", lambda *a: {"action": "open", "app": "calculator"})
    monkeypatch.setattr(pcsemantic, "running", lambda a: False)
    monkeypatch.setattr(pcsemantic, "_everyday_apps", lambda: ["calculator", "paint"])
    monkeypatch.setattr(pcsemantic, "mentioned_apps", lambda text, catalogue=None: [])
    assert pcsemantic.interpret("I have to crunch a few numbers now") == {
        "steps": [{"tool": "open_app", "arguments": {"app": "calculator"}}]}


def test_a_held_draft_is_said_after_all_when_no_other_try_follows():
    """A tool ran after the draft opened, or he cut in: release() says the draft as written, after what was already said."""
    said, p = polisher([SPANISH], "What's a synonym for happy?")
    p("Okay, sir.")
    p('"Gracias", sir.')
    p("It means thank you.")
    assert said == ["Okay, sir."] and p.stale
    p.release()
    p.finish()
    assert said == ["Okay, sir.", '"Gracias", sir.', "It means thank you."] and not p.stale


def test_a_draft_from_the_cloud_is_never_asked_again(monkeypatch):
    """One more try is a free local call; a paid or cloud-fallback answer is said as written."""
    notes = []
    b = _brain(monkeypatch, ['"Gracias" is thank you in Spanish, sir.', '"Gracias", sir.'], notes)
    from core import intelligence
    real = intelligence.request

    def cloud(*a, **kw):
        out = real(*a, **kw)
        out.model = "gemini-2.5-flash"
        return out
    monkeypatch.setattr(intelligence, "request", cloud)
    b.ask("How do you say thank you in Spanish?", lambda s: None)
    said = []
    b.ask("What's a synonym for happy?", said.append)
    assert said == ['"Gracias", sir.'] and len(notes) == 2


# ---- H6: an offer that drags in another topic is not the end of a reply either -----------------------------------------------
@pytest.mark.parametrize("asked,text", [
    ("What's the largest ocean?", "The Pacific Ocean, sir. Would you like a recipe to use up something in the pantry?"),
    ("How many legs does a spider have?", "Eight legs, sir. Shall I check the pantry and suggest a quick dinner while we wait?"),
    ("What's the freezing point of water in Celsius?",
     "Water freezes at zero degrees Celsius, sir. Shall I check the weather for your commute tomorrow?"),
])
def test_an_offer_about_something_else_is_dropped_from_the_end(asked, text):
    """Real eval replies (2026-11-10): the dinner question of ten turns before came back as an offer on every answer."""
    assert speech.polish(text, asked=asked) == speech._SPLIT.split(text)[0]


@pytest.mark.parametrize("asked,text", [
    ("Is Jellyfin okay?", "Jellyfin lost the media drive. Want me to go ahead and restart it?"),
    ("What's on my shopping list?", "Eggs and milk, sir. Want me to add bread to the shopping list too?"),
    ("Let's call my new garage project Falcon.", "Understood, sir. Shall I start a project log for it now?"),
])
def test_an_offer_about_this_answer_or_a_step_to_ask_first_stays(asked, text):
    assert speech.polish(text, asked=asked) == text


def _spied(monkeypatch):
    """Every history the brain's local requests were given (a copy of each message list)."""
    from core import intelligence
    seen = []
    fake = intelligence.request

    def spy(role, system, history, tools, cancelled, on_text=None, **kw):
        seen.append(list(history))
        return fake(role, system, history, tools, cancelled, on_text=on_text, **kw)
    monkeypatch.setattr(intelligence, "request", spy)
    return seen


def test_the_one_more_try_sees_the_whole_conversation_with_its_background(monkeypatch):
    """Review 2026-11-11: hiding the copied exchange took the conversation's one <background> block (the date, who is
    talking, his memories) with it, and changed the cached prompt prefix for the retry and the next turn."""
    notes = []
    b = _brain(monkeypatch, ['"Gracias" is thank you in Spanish, sir.', "The first moon landing was in 1969, sir.",
                             '"Gracias", sir.', "Glad, sir."], notes)
    b.context = lambda: "Current date and time: Tuesday 10 November. His wife is Maya."
    seen = _spied(monkeypatch)
    b.ask("How do you say thank you in Spanish?", lambda s: None)
    b.ask("What year did the first moon landing happen?", lambda s: None)
    said = []
    b.ask("What's a synonym for happy?", said.append)
    assert said == ["Glad, sir."]
    draft, retry = seen[-2], seen[-1]
    assert "Maya" in str(retry[0]["content"]) and "Spanish" in str(retry[0]["content"])
    assert retry == draft, "the retry shows the model exactly what the draft's request did (only the note differs)"


def test_the_one_more_try_never_puts_an_older_question_before_his_words(monkeypatch):
    """Review 2026-11-11: with the copied exchange (the last one) hidden, 'Shall I turn the volume down, sir?' of two
    exchanges before stood right before 'Sounds good, do it.', and the request's guards (jarvis_asked, select_tools'
    acceptance) judged his yes against it."""
    from core import intelligence
    paris = "The capital of France is Paris, a lovely city, sir."
    notes = []
    b = _brain(monkeypatch, ["Shall I turn the volume down, sir?", paris, paris, "Glad you think so, sir."], notes)
    seen = _spied(monkeypatch)
    b.ask("It's getting really loud in here.", lambda s: None)
    b.ask("What's the capital of France?", lambda s: None)
    b.ask("Sounds good, do it.", lambda s: None)
    assert len(seen) == 4 and notes[-1], "the draft repeated the last answer: one more try"
    assert intelligence.jarvis_asked(seen[-1]) == intelligence.jarvis_asked(seen[-2]) == ""
    assert seen[-1] == seen[-2]


@pytest.mark.parametrize("local_only", [True, False])
def test_a_failed_one_more_try_says_the_draft_never_a_failure_note_or_a_paid_call(monkeypatch, local_only):
    """Review 2026-11-11: both local tiers failing on the retry ended the turn on 'Local intelligence is unavailable...'
    (local only) or made a paid cloud call, in place of the free draft that was there."""
    from core import intelligence
    if local_only:
        monkeypatch.setenv("JARVIS_LOCAL_ONLY", "1")
    else:
        monkeypatch.delenv("JARVIS_LOCAL_ONLY", raising=False)
    notes = []
    b = _brain(monkeypatch, ['"Gracias" is thank you in Spanish, sir.', '"Gracias", sir.'], notes)
    fake = intelligence.request

    def failing(role, system, history, tools, cancelled, on_text=None, **kw):
        if kw.get("note"):
            notes.append(kw["note"])
            raise intelligence.LocalFailure("invalid_response")
        return fake(role, system, history, tools, cancelled, on_text=on_text, **kw)
    monkeypatch.setattr(intelligence, "request", failing)
    cloud = []
    monkeypatch.setattr(b, "_cloud_turn", lambda emit: cloud.append(1))
    b.ask("How do you say thank you in Spanish?", lambda s: None)
    said = []
    b.ask("What's a synonym for happy?", said.append)
    assert said == ['"Gracias", sir.'] and cloud == []
    assert len([n for n in notes if n]) == 2, "both local tiers were tried once more"
    last = b.messages[-1]["content"]
    assert (last if isinstance(last, str) else " ".join(x.text for x in last)) == '"Gracias", sir.'
    assert len(b.messages) == 4


def test_no_other_try_once_a_stopped_tool_loop_removed_the_turn(monkeypatch):
    """Review 2026-11-11: the step limit removes the turn's messages (tool calls included), so 'no tool ran' read true
    and the retry ran on a history ending in the previous reply. The draft and the stop line are said instead."""
    from brain import llm
    from core import intelligence
    from tools import registry
    notes = []
    b = _brain(monkeypatch, ['"Gracias" is thank you in Spanish, sir.'], notes)
    fake = intelligence.request
    rounds = []

    def looping(role, system, history, tools, cancelled, on_text=None, **kw):
        if len(b.messages) <= 2:
            return fake(role, system, history, tools, cancelled, on_text=on_text, **kw)
        notes.append(kw.get("note", ""))
        rounds.append(1)
        text = '"Gracias", sir.' if len(rounds) == 1 else ""
        if text and on_text:
            on_text(text)
        content = ([TextBlock(type="text", text=text)] if text else []) + [
            SimpleNamespace(type="tool_use", id=f"t{len(rounds)}", name=f"probe_{len(rounds)}", input={})]
        return SimpleNamespace(content=content, stop_reason="tool_use")
    monkeypatch.setattr(intelligence, "request", looping)
    monkeypatch.setattr(registry, "run", lambda name, args: ("ok", False))
    monkeypatch.setattr(llm, "MAX_TOOL_ROUNDS", 2)
    b.ask("How do you say thank you in Spanish?", lambda s: None)
    said = []
    b.ask("What's a synonym for happy?", said.append)
    assert not any(notes), "no one more try"
    assert said[0] == '"Gracias", sir.' and "taking more steps" in said[-1]


def test_one_more_try_after_an_acknowledgment_is_heard_once(monkeypatch):
    """'Certainly, sir.' was said before the draft was held: the retry's own 'Certainly, sir.' is not said again, and
    the stored reply is what he heard."""
    notes = []
    b = _brain(monkeypatch, ['"Gracias" is thank you in Spanish, sir.', 'Certainly, sir. "Gracias", sir.',
                             "Certainly, sir. Glad is one, sir."], notes)
    b.ask("How do you say thank you in Spanish?", lambda s: None)
    said = []
    b.ask("What's a synonym for happy?", said.append)
    assert said == ["Certainly, sir.", "Glad is one, sir."]
    last = b.messages[-1]["content"]
    assert (last if isinstance(last, str) else " ".join(x.text for x in last)) == "Certainly, sir. Glad is one, sir."


def test_one_more_try_after_an_acknowledgment_stores_it_before_the_new_answer(monkeypatch):
    notes = []
    b = _brain(monkeypatch, ['"Gracias" is thank you in Spanish, sir.', 'Certainly, sir. "Gracias", sir.',
                             "Glad is one, sir."], notes)
    b.ask("How do you say thank you in Spanish?", lambda s: None)
    said = []
    b.ask("What's a synonym for happy?", said.append)
    assert said == ["Certainly, sir.", "Glad is one, sir."]
    last = b.messages[-1]["content"]
    assert (last if isinstance(last, str) else " ".join(x.text for x in last)) == "Certainly, sir. Glad is one, sir."


DINNER = ("Give me a quick idea for dinner tonight.",
          "How about a quick stir-fry with chicken thighs and frozen veggies, sir? It's cheap, fast, and you can make extra.")


def test_a_suggestion_said_again_for_another_request_is_held_too():
    """Eval 2026-11-10 (final code, run 1): 'Give me a short motivational line.' -> the dinner idea of eleven turns before,
    word for word. A suggestion worded as a question ('How about ...?') is an answer, not an offer."""
    said, p = polisher([DINNER, MOON], "Give me a short motivational line.")
    p("How about a quick stir-fry with chicken thighs and frozen veggies, sir?")
    p.finish()
    assert said == [] and p.stale


def test_a_new_suggestion_is_said():
    said, p = polisher([MOON, SPANISH], "Give me a quick idea for dinner tonight.")
    p("How about chicken tacos with a quick slaw, sir?")
    p.finish()
    assert said == ["How about chicken tacos with a quick slaw, sir?"] and not p.stale


@pytest.mark.parametrize("asked,text", [
    ("What's a synonym for happy?", "A synonym for happy is joyful, sir. Would you like more options?"),
    ("What's a synonym for happy?",
     "Joyful, sir. Shall I have a look at your pantry to see if we can use up something before it spoils?"),
])
def test_an_offer_with_a_topic_of_its_own_is_dropped_whatever_pronoun_it_has(asked, text):
    """Final eval runs 2026-11-10: the dinner topic came back as an offer with an 'it' inside it."""
    assert speech.polish(text, asked=asked) == speech._SPLIT.split(text)[0]


def test_a_short_offer_about_the_answer_stays():
    text = "It says the rent is due Friday, sir. Want me to read it to you?"
    assert speech.polish(text, asked="What does the letter say?") == text


def test_a_reply_that_only_offers_something_unasked_gets_one_more_try():
    """Final eval 2026-11-10: 'What's a good name for a gray cat?' -> 'Shall I save that name to the project log, sir?'"""
    said, p = polisher([MOON], "What's a good name for a gray cat?")
    p("Shall I save that name to the project log, sir?")
    p.settle()
    assert said == [] and p.stale
    p.release()                                        # no other try after all: said as the whole reply (never dead air)
    p.finish()
    assert said == ["Shall I save that name to the project log, sir?"]


@pytest.mark.parametrize("asked,reply", [
    ("Hey Jarvis, how are you?", "Anything else, sir?"),                 # a pure closing offer as the whole reply
    ("Remember that Marcus is helping me.", "Shall I save that to your notes?"),   # he asked to keep it
])
def test_settle_leaves_other_whole_replies_alone(asked, reply):
    said, p = polisher([MOON], asked)
    p(reply)
    p.settle()
    p.finish()
    assert said == [reply] and not p.stale


def test_settle_never_holds_a_question_that_is_really_waiting():
    said, p = polisher([MOON], "What's a good name for a gray cat?", keep=lambda: True)
    p("Shall I save that name to the project log, sir?")
    p.settle()
    p.finish()
    assert said == ["Shall I save that name to the project log, sir?"] and not p.stale


# ---- an earlier topic tacked on after the answer ----------------------------------------------------------------------------
DINNER_ASKED = ("Give me a quick idea for dinner tonight.", "I'll suggest a quick dinner idea, sir.")


@pytest.mark.parametrize("asked,earlier,reply,kept", [
    # eval 2026-11-10 (final code): the dinner topic tacked onto later answers
    ("What year did the first moon landing happen?", [DINNER_ASKED, SPANISH],
     "The first moon landing was in 1969, sir. And for dinner tonight, how about a simple stir-fry with chicken and veggies?",
     "The first moon landing was in 1969, sir."),
    ("What's the freezing point of water in Celsius?", [DINNER_ASKED, MOON],
     "0 degrees Celsius, sir. For dinner tonight, how about a simple stir-fry with chicken and veggies?", "0 degrees Celsius, sir."),
    # copy_probe 2026-11-10: the project re-confirmed on every reply
    ("How do you say thank you in Spanish?", FALCON, "It's gracias, sir. Falcon is noted as your garage project.",
     "It's gracias, sir."),
])
def test_an_earlier_topic_tacked_on_after_the_answer_is_dropped(asked, earlier, reply, kept):
    assert speech.polish(reply, history(*earlier), asked=asked) == kept


@pytest.mark.parametrize("asked,earlier,reply", [
    ("What's the tallest mountain in the world?", [DINNER_ASKED],
     "Mount Everest, sir. It stands at 29,032 feet above sea level."),                    # more about the answer
    ("Who's helping me with it?", [("The plan is to build the workbench on Saturday, and my brother Marcus is helping me.",
                                    "Noted, sir.")], "Marcus is helping you, sir. Your brother is building the workbench on Saturday."),
    ("What did we decide at the start?", FALCON + [MOON], "We named the project, sir. Falcon is your garage project."),
    ("What's for dinner tonight, then?", [DINNER_ASKED], "Chicken stir-fry, sir. It's quick and dinner is sorted tonight."),
])
def test_more_about_the_answer_or_what_he_asks_about_is_kept(asked, earlier, reply):
    assert speech.polish(reply, history(*earlier), asked=asked) == reply


@pytest.mark.parametrize("asked,text", [
    ("How many legs does a spider have?", "Eight, sir. Shall I open a lesson page on arachnids?"),
    ("What color do you get mixing blue and yellow?", "Green, sir. Shall I open a lesson page on color mixing?"),
])
def test_a_lesson_page_after_a_quick_fact_is_an_unasked_offer(asked, text):
    """Eval 2026-11-10: five answers in one run ended on a lesson page nobody asked about. After 'explain' it stays."""
    assert speech.polish(text, asked=asked) == speech._SPLIT.split(text)[0]
    assert speech.polish("That's the gist of it, sir. Want me to open a lesson page on it?", asked="Explain how a spider spins silk.") \
        == "That's the gist of it, sir. Want me to open a lesson page on it?"


# ---- review 2026-11-11: a right answer that builds on what was just said is never taken for an old one -------------------
CANBERRA = ("What's the capital of Australia?", "Canberra, sir.")
TIMES = ("What's 17 times 23?", "391, sir.")
WEATHER = ("What's the weather like?", "It's 62 degrees and sunny in Springfield, sir.")


@pytest.mark.parametrize("asked,earlier,draft", [
    ("Which city did you say?", [CANBERRA, TIMES], ["Canberra, sir."]),
    ("Which city did you tell me?", [CANBERRA, TIMES], ["Canberra, sir."]),
    ("Wait, which city?", [CANBERRA, TIMES], ["Canberra, sir."]),
    ("And who runs SpaceX?", [("Who's the CEO of Tesla?", "Elon Musk is the CEO of Tesla, sir.")], ["Elon Musk, sir."]),
])
def test_an_answer_that_follows_on_from_what_was_said_is_said(asked, earlier, draft):
    said, p = polisher(earlier, asked)
    for sentence in draft:
        p(sentence)
    p.settle()
    p.finish()
    assert said == draft and not p.stale


@pytest.mark.parametrize("asked,earlier,draft", [
    ("Should I bring a jacket?", [WEATHER], ["62 and sunny, sir.", "A light jacket is plenty."]),
    ("Should I wear a jacket?", [WEATHER], ["It's 62 degrees and sunny in Springfield, sir.", "No jacket needed today."]),
])
def test_a_draft_opening_on_the_last_reading_is_held_even_when_its_rest_answers(asked, earlier, draft):
    """Review 2026-11-11 (settle's release branch removed): the reading restated, then the answer to his question, gets
    the one more try (it sees the whole conversation, a free local call) rather than being let through on one shared
    word, which also let '"Gracias" ... Happy to help with more phrases.' through for a synonym of happy."""
    said, p = polisher(earlier, asked)
    for sentence in draft:
        p(sentence)
    p.settle()
    p.finish()
    assert said == [] and p.stale


# ---- review 2026-11-11: real questions are never trimmed as offers ----------------------------------------------------------
@pytest.mark.parametrize("asked,text", [
    # a step he must say yes to first, even with a keep / show word in it
    ("Why is my C drive so full?", "Old backups take 80 GB, sir. Shall I delete the old backups and keep only the newest one?"),
    ("Did my backup finish?", "It failed at 3 AM, sir. Shall I retry it and log the error?"),
    ("When is my meeting with Marcus?", "It's at three, sir. Shall I open your calendar and move it to four?"),
    ("Is Jellyfin okay?", "Jellyfin lost the media drive, sir. Want me to restart it and keep an eye on it?"),
    ("Is Paint still open?", "It is, sir. Shall I close it and save your work first?"),
    ("Why is it lagging?", "qBittorrent is using all the bandwidth, sir. Want me to throttle it and note the limit?"),
    # what he asked to have done, worded as a question: the question back is part of doing it
    ("Can you remind me to call Marcus at 5?", "Certainly, sir. Shall I set a reminder to call Marcus at 5 PM today?"),
    ("Could you add eggs to my shopping list?", "Of course, sir. Shall I add them to your shopping list now?"),
    ("Can you log my workout from this morning?", "Certainly, sir. Shall I log it as a run?"),
    ("Can you bookmark this recipe for later?", "Of course, sir. Shall I save it to your recipes?"),
    ("Can you back up the Falcon project?", "Sure, sir. Shall I save a copy to the D drive first?"),
    # a choice, or what he would like, after he asked for advice
    ("What should I make for dinner?", "A few options, sir. Should we go with chicken or beef?"),
    ("Can you recommend a laptop?", "Happy to, sir. Would you like a gaming model or something for work?"),
    ("What should I make for dinner?", "Plenty of options, sir. Would you like something quick?"),
    # the persona's own offers (core/personality.md)
    ("What size should I get in this?", "Get the large, sir; it's an estimate. Shall I save your real measurements for exact picks?"),
    ("Summarize what I copied.", "It says the rent is due Friday, sir. Shall I put it on your clipboard?"),
    ("Is my download done?", "It finished ten minutes ago, sir. Shall I put it in the movies folder and rescan Jellyfin?"),
    ("Is my download done?", "It finished ten minutes ago, sir. Shall I file it under anime?"),
])
def test_a_real_question_at_the_end_is_kept(asked, text):
    assert speech.polish(text, asked=asked, fresh=lambda: True) == text
    assert speech.polish(text, asked=asked) == text


def test_an_acknowledgment_then_an_unasked_offer_gets_the_one_more_try():
    """Review 2026-11-11: 'Certainly, sir.' was said, so the offer was dropped and he heard only that."""
    said, p = polisher([MOON], "What's a good name for a gray cat?")
    p("Certainly, sir.")
    p("Shall I save that name to the project log?")
    p.settle()
    assert said == ["Certainly, sir."] and p.stale


def test_without_a_retry_an_acknowledgment_and_an_offer_keep_the_offer():
    out = speech.polish("Of course, sir. Shall I open a map to show it?", asked="Where is Canberra?")
    assert out == "Of course, sir. Shall I open a map to show it?"


# ---- review 2026-11-11: tying advice to what he said earlier is part of it ---------------------------------------------------
@pytest.mark.parametrize("earlier,asked,reply", [
    ([("I'm vegetarian and I hate mushrooms.", "Noted, sir.")], "Give me a quick idea for dinner tonight.",
     "How about a black bean chili, sir? No mushrooms in it, and it's vegetarian."),
    ([("My knee has been hurting since the game.", "Sorry to hear that, sir.")], "What's a good workout for today?",
     "Try a brisk walk or a swim, sir. Both go easy on a hurting knee."),
    (FALCON, "What wood should I use for the workbench?", "Pine is cheap and easy to work, sir. It would suit the Falcon garage project nicely."),
])
def test_advice_tied_to_what_he_said_earlier_is_kept(earlier, asked, reply):
    assert speech.polish(reply, history(*earlier), asked=asked) == reply


def test_a_closing_offer_after_only_an_acknowledgment_still_goes():
    """Only an offer of something unasked waits for real news before it is dropped: 'Done, sir.' is a whole reply."""
    assert speech.polish("Done, sir. Anything else?", asked="Close Paint.") == "Done, sir."
    assert speech.polish("Sunny, sir. Shall I save that to your notes?", asked="Could you check the weather?") == "Sunny, sir."


def test_a_copied_answer_whose_rest_only_repeats_its_own_words_is_still_held():
    """'pound' is in his question and in the copied feathers answer: the rest says nothing new about what he asked."""
    said, p = polisher([FEATHERS, ROOT], "How many ounces are in a pound?")
    p(FEATHERS[1])
    p("A pound is a pound either way.")
    p.settle()
    assert said == [] and p.stale


# ---- review 2026-11-11, round 3: what the polisher took out that he needed --------------------------------------------------
# Synthetic paraphrases of the review's scenarios (never his own lines).
def _heard(earlier, asked, draft, **kw):
    """What he hears of a whole draft, fed the way the Brain feeds it (one more try possible, then settle and finish)."""
    said, p = polisher(earlier, asked, **kw)
    for sentence in draft:
        p(sentence)
    p.settle()
    p.finish()
    return said, p


CLEANUP = ("Uninstall Contoso Drive and Contoso Mail.", "Both are installed, sir.")


@pytest.mark.parametrize("asked,draft", [
    # the approval question, named after his earlier request, while he chats about something else
    ("Is admin mode on now?", ["Admin mode is on, sir.",
                               "Before I go ahead, shall I uninstall Contoso Drive and then Contoso Mail?",
                               "A yes is all I need."]),
    ("How's the weather looking today?", ["It's 62 degrees and sunny, sir.", "Shall I try uninstalling Contoso Drive again?"]),
])
def test_a_step_question_about_an_earlier_request_is_never_taken_for_an_old_topic(asked, draft):
    said, p = _heard([CLEANUP], asked, draft)
    assert said == draft and not p.stale


def test_nothing_is_taken_out_as_an_old_topic_while_a_question_is_really_waiting():
    draft = ["It's 62 degrees and sunny, sir.", "Contoso Drive and Contoso Mail are still waiting for your yes."]
    said, _p = _heard([CLEANUP], "How's the weather looking today?", draft, keep=lambda: True)
    assert said == draft


VOLUME = ("Turn the volume down by half.", "Done, sir.")


@pytest.mark.parametrize("asked,earlier,draft", [
    # a correction: what came before is the topic, and the sentence restating it is the answer
    ("No, you really did not.", [VOLUME, ("You didn't do it.", "I did, sir.")],
     ["I made a mistake, sir.", "I did not turn the volume down by half.", "I'll try again."]),
    # an 'and' follow-up
    ("And I can see all of it in the app?", [("Plan my meals and the shopping for the week.", "The plan is ready, sir.")],
     ["The app has all of it, sir.", "It shows the meal plan, the recipes and the shopping list by store."]),
    # his question goes on with the last reply, and so does the sentence
    ("Why does the speaker still sound loud?", [VOLUME, ("Did that work?", "It did, sir. The speaker now reads 40 percent.")],
     ["It's the music app's own slider, sir.", "I turned the volume down by half to 40 percent, but only for Windows."]),
    # 'get' + 'son' are no earlier topic: only specific words count
    ("What's on the meal plan this week?", [("Can you get my son a ticket too?", "Done, sir, two tickets.")],
     ["Chicken, fish and pasta, sir.", "Your son gets a milder version of each one."]),
    # 'PC' is about what he asked
    ("Can my PC send movies to the home lab?",
     [("Which graphics settings should I pick for the racing game?", "Medium graphics settings, sir.")],
     ["Yes, sir, over the network share.", "Your PC needs different graphics settings for streaming, though."]),
])
def test_the_sentence_that_answers_him_is_never_taken_for_an_old_topic(asked, earlier, draft):
    said, p = _heard(earlier, asked, draft)
    assert said == draft and not p.stale


GROCERY = ("What grocery deals are there this week?",
           "The corner market has eggs at 1.99 for 18 this week, sir. Instant rice is 2.95 at the discount store. "
           "Oats are 3.99 at the big grocer.")


def test_a_list_restating_earlier_prices_after_news_is_said_whole():
    """A follow-up that asks for the prices to compare: each line is made of the earlier answer's words, and after the
    reply's own news it is the answer, not an old answer said again (4 of 8 prices went missing in the replay)."""
    draft = ["Here are the prices to beat, sir.", "Eggs: 1.99 for 18.", "Instant rice: 2.95.", "Oats: 3.99, big grocer.",
             "Read me the warehouse club's prices on those three."]
    said, p = _heard([GROCERY], "Which prices should I compare against the warehouse club?", draft)
    assert said == draft and not p.stale


@pytest.mark.parametrize("asked,earlier,draft", [
    ("What about tomorrow?", [WEATHER], ["It's 62 degrees and sunny in Springfield, sir."]),
    ("And what about tomorrow?", [WEATHER], ["It's 62 degrees and sunny in Springfield, sir."]),
    ("And the capital of Canada?", [("What's the capital of Australia?", "The capital of Australia is Canberra, sir.")],
     ["The capital of Australia is Canberra, sir."]),
    # the last reply copied word for word after a follow-on, the last exchange no question
    ("And what about tomorrow?", [("I'm driving to Norfolk later today.",
                                   "Drive safe, sir. It's 62 degrees and sunny in Springfield right now.")],
     ["It's 62 degrees and sunny in Springfield right now."]),
])
def test_a_follow_on_is_not_answered_with_the_last_answer_word_for_word(asked, earlier, draft):
    said, p = _heard(earlier, asked, draft)
    assert said == [] and p.stale


@pytest.mark.parametrize("asked,earlier,draft", [
    ("What's a synonym for happy?", [SPANISH], [SPANISH[1], "Happy to help with more phrases."]),
    ("How many ounces are in a pound?", [ROOT], [ROOT[1], "Ounces and pounds are easy to mix up."]),
])
def test_a_copied_answer_with_a_filler_naming_his_topic_still_gets_the_one_more_try(asked, earlier, draft):
    said, p = _heard(earlier, asked, draft)
    assert said == [] and p.stale


@pytest.mark.parametrize("asked,answer", [
    ("Can dogs eat chocolate?", "No, sir."),
    ("How many moons does Earth have?", "One, sir."),
    ("Is it safe to leave the oven on overnight?", "Not at all, sir."),
    ("What's the opposite of sad?", "Happy, sir."),
])
def test_a_short_answer_in_the_one_more_try_is_never_swallowed_as_an_acknowledgment(asked, answer):
    """'Certainly, sir.' was said before the draft was held: the retry's short answer is said and stored after it, never
    replaced by it (a 'No' turned into a 'Certainly')."""
    said, p = polisher([SPANISH], asked)
    p("Certainly, sir.")
    p('"Gracias", sir.')
    p.settle()
    assert said == ["Certainly, sir."] and p.stale
    p.once_more()
    p(answer)
    p.finish()
    assert said == ["Certainly, sir.", answer]
    assert (answer, f"Certainly, sir. {answer}") in p.edits


def test_the_brain_says_and_stores_a_short_no_after_the_acknowledgment(monkeypatch):
    notes = []
    b = _brain(monkeypatch, [SPANISH[1], 'Certainly, sir. "Gracias", sir.', "No, sir."], notes)
    b.ask(SPANISH[0], lambda s: None)
    said = []
    b.ask("Can dogs eat chocolate?", said.append)
    assert said == ["Certainly, sir.", "No, sir."] and notes[-1]
    last = b.messages[-1]["content"]
    assert (last if isinstance(last, str) else " ".join(x.text for x in last)) == "Certainly, sir. No, sir."


@pytest.mark.parametrize("sentence,nothing", [
    ("No, sir.", False), ("One, sir.", False), ("Not at all, sir.", False), ("It isn't, sir.", False),
    ("Just one, sir.", False), ("Twelve, sir.", False),
    ("Sure thing, sir.", True), ("Good question, sir.", True), ("No problem, sir.", True), ("Noted, sir.", True),
    ("Indeed, sir.", True), ("Copy that, sir.", True), ("Certainly, sir.", True), ("One moment, sir.", True),
    ("Good one, sir.", True),                                   # 'one' is no number here (review 2026-11-11, round 4)
])
def test_what_says_nothing(sentence, nothing):
    assert speech._says_nothing(sentence) is nothing


@pytest.mark.parametrize("opener", ["Sure thing, sir.", "Good question, sir.", "No problem, sir.", "Noted, sir."])
def test_an_old_answer_after_an_opener_still_gets_the_one_more_try(opener):
    said, p = _heard([SPANISH], "What's a synonym for happy?", [opener, '"Gracias", sir.'])
    assert said == [opener] and p.stale
    said, p = _heard([MOON], "What's a good name for a gray cat?", [opener, "Shall I save a name idea to a note?"])
    assert said == [opener] and p.stale


@pytest.mark.parametrize("asked,text", [
    # the question back after his own 'Can you ...?' request
    ("Can you play some music?", "Sure, sir. Your living room speaker is on. Shall I put on your usual playlist?"),
    ("Could you order more coffee?", "Of course, sir. The last order was in October. Should I get the same beans?"),
    # the way forward after the answer admitted a gap
    ("Did you include the warehouse club in the comparison?",
     "Not yet, sir. I don't have their prices yet. Want me to look them up for the bulk items like rice, eggs and oats, "
     "and redo the route if one of them is cheaper?"),
    # 'PC' is what he asked about
    ("How warm is my PC running?", "The processor is at 71 degrees, sir. Want me to open the PC fan curve?"),
    # steps on his PC, apps and messages: asked first
    ("Is the music too loud?", "The player is at 90 percent, sir. Shall I lower the volume?"),
    ("Is my download done?", "It finished a minute ago, sir. Shall I launch the installer?"),
    ("When is my dentist appointment?", "It's at four, sir. Shall I text Ana a heads-up?"),
    ("What time is it?", "It's 11:40, sir. Shall I lock the computer for the night?"),
    # a suggestion worded as a question, after he asked for a pick in everyday words
    ("What's for dinner?", "Something easy tonight, sir. Would you like a chicken stir-fry?"),
    ("I'm hungry.", "Let's fix that, sir. Fancy a grilled cheese?"),
    ("Any thoughts on lunch?", "Something light, sir. Do you want a Greek salad?"),
])
def test_a_question_he_needs_is_never_trimmed_as_an_offer(asked, text):
    assert speech.polish(text, asked=asked) == text
    said, _p = _heard([MOON], asked, speech._SPLIT.split(text))
    assert said == speech._SPLIT.split(text)


@pytest.mark.parametrize("sentence,step", [
    ("Shall I lower the volume?", True), ("Shall I launch the installer?", True), ("Shall I start the download?", True),
    ("Shall I text Ana a heads-up?", True), ("Shall I save the changes before closing it?", True),
    ("Shall I try uninstalling it again?", True), ("Shall I shut down the computer?", True),
    ("Shall I turn the music down?", True), ("Shall I send it to Marcus?", True), ("Shall I mute the call?", True),
    ("Shall I lock the computer for the night?", True), ("Shall I close it and add a note to your log?", True),
    ("Shall I start the backup and log the result?", True), ("Shall I save the project?", True),
    ("Shall I save that fact?", False), ("Shall I open a map to show it?", False), ("Shall I play it for you?", False),
    ("Shall I show you a diagram?", False), ("Shall I send that to your notes?", False),
    ("Shall I start the lesson page?", False),
    # review 2026-11-11, round 4: offers to keep it, worded with a step's verb
    ("Shall I send that fact to your notes?", False), ("Shall I send a copy of that fact to your notes?", False),
    ("Shall I save the project name to your notes?", False), ("Shall I start the Falcon project log now?", False),
    ("Shall I lock in Falcon as the name for your garage project?", False),
    ("Shall I save the text of that line to your notes?", False), ("Shall I save that message to your notes?", False),
])
def test_which_questions_name_a_step_to_ask_first(sentence, step):
    assert speech._a_step(sentence) is step


TIMER = ("How long should I boil an egg?", "About nine minutes for hard-boiled, sir. Want me to set a timer for nine minutes?")


@pytest.mark.parametrize("asked", ["Go ahead.", "Sure.", "Okay.", "Please do.", "Sounds good.", "Yes please"])
def test_his_yes_to_an_offer_is_a_follow_on_never_a_different_question(asked):
    draft = ["Nine minutes it is, sir.", "The timer is running."]
    said, p = _heard([TIMER], asked, draft)
    assert said == draft and not p.stale


# ---- review 2026-11-11, round 3: the one more try never goes to the cloud, and its note is only on his words ---------------
def _local_reply(text, role):
    from core import intelligence
    return {"model": intelligence.model(role),
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def test_a_failed_one_more_try_never_reaches_the_cloud_fallback(monkeypatch):
    """Both local tiers fail on the retry: the free draft is said, and request('advanced') never hands the conversation
    to the cloud provider (it used to, without the note). The real intelligence.request runs; only the model calls are
    stand-ins."""
    import json
    from brain import llm
    from core import cloudmodel, intelligence, pcfast, quick
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.delenv("JARVIS_LOCAL_ONLY", raising=False)
    monkeypatch.delenv("JARVIS_PC_URL", raising=False)
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    replies, local, cloud = iter([SPANISH[1], '"Gracias", sir.']), [], []

    def complete_local(body, cancelled=lambda: False, on_text=None):
        noted = "Reply to their latest message" in json.dumps(body["messages"][-1])
        local.append((body["role"], noted))
        if noted:
            raise intelligence.LocalFailure("invalid_response")
        text = next(replies)
        if on_text:
            on_text(text)
        return _local_reply(text, body["role"])
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    monkeypatch.setattr(cloudmodel, "available", lambda: True)
    monkeypatch.setattr(cloudmodel, "complete", lambda payload, cancelled=lambda: False: cloud.append(payload) or {
        "model": "gemini-cloud", "choices": [{"message": {"role": "assistant", "content": "Joyful, sir."},
                                              "finish_reason": "stop"}]})
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    monkeypatch.setattr(b, "_cloud_turn", lambda emit: cloud.append("claude"))
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.ask(SPANISH[0], lambda s: None)
    said = []
    b.ask("What's a synonym for happy?", said.append)
    assert cloud == [], "no cloud call during the one more try"
    assert ("fast", True) in local and ("advanced", True) in local, "both local tiers were tried once more"
    assert said == ['"Gracias", sir.']
    last = b.messages[-1]["content"]
    assert (last if isinstance(last, str) else " ".join(x.text for x in last)) == '"Gracias", sir.'


def _noted(message, note):
    content = message.get("content")
    return isinstance(content, list) and any(isinstance(p, dict) and p.get("text") == note for p in content)


def test_the_one_more_try_note_is_never_put_on_a_tools_screenshot(monkeypatch):
    """After a tool returned a screenshot, the last user message is its <tool-images> evidence, not his words."""
    from core import intelligence
    monkeypatch.delenv("JARVIS_PC_URL", raising=False)
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    seen = []

    def complete_local(body, cancelled=lambda: False, on_text=None):
        seen.append(body["messages"])
        return _local_reply("Two windows are open, sir: Paint and a browser.", body["role"])
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    note = speech.latest_only("What's on my screen?")
    shot = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    history = [{"role": "user", "content": "What's on my screen?"},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "look_at_screen", "input": {}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                             "content": [{"type": "text", "text": "Screenshot taken."}, shot]}]}]
    try:
        intelligence.request("fast", "You are Jarvis.", history, [], note=note)
    except intelligence.LocalFailure:
        pass                                    # only what the model was sent matters here
    assert seen
    for messages in seen:
        evidence = [m for m in messages if m.get("role") == "user" and "<tool-images>" in str(m.get("content"))]
        assert evidence and not any(_noted(m, note) for m in evidence)
    seen.clear()
    intelligence.request("fast", "You are Jarvis.", [{"role": "user", "content": "What's a synonym for happy?"}], [], note=note)
    assert seen and _noted(seen[0][-1], note), "on his own message the note is there"


# ---- review 2026-11-11, round 4: the eval failures the round-3 gates let back in ---------------------------------------------
# Synthetic paraphrases of the review's scenarios (never his own lines).
FALCON_LINE = "Falcon is noted as your garage project, sir."


@pytest.mark.parametrize("asked,answer", [
    ("And what's the capital of France?", "Paris, sir."),          # a follow-on opener
    ("So how many legs does a spider have?", "Eight, sir."),
    ("What time is it?", "It's 11:40, sir."),                     # one content word
])
def test_the_falcon_line_is_dropped_whatever_way_the_question_opens(asked, answer):
    said, p = _heard(FALCON + [MOON], asked, [answer, FALCON_LINE])
    assert said == [answer] and not p.stale


def test_a_one_word_question_keeps_what_goes_on_with_his_last_exchange():
    """'Did it work?' after turning the volume down: the sentence about the volume is the answer, not an old topic."""
    draft = ["It worked, sir.", "The volume is down by half now."]
    said, p = _heard([VOLUME], "Did it work?", draft)
    assert said == draft and not p.stale


@pytest.mark.parametrize("opener", ["Easy one, sir.", "Here's one, sir.", "Ah, a classic, sir.", "The word is simple, sir."])
def test_an_opener_never_lets_a_copied_short_answer_through(opener):
    """An opener with a word of its own is news, but none about what he asked: the copied 'Gracias' is still an old
    answer, and after news it is dropped (as on main)."""
    said, p = _heard([SPANISH, MOON], "What's a synonym for happy?", [opener, '"Gracias", sir.'])
    assert said == [opener]


def test_good_one_then_a_copied_wrong_answer_gets_the_one_more_try():
    said, p = _heard([ROOT, MOON], "How many ounces are in a pound?", ["Good one, sir.", "12, sir."])
    assert said == ["Good one, sir."] and p.stale


@pytest.mark.parametrize("asked,earlier,answer,offer", [
    ("What's the capital of France?", FALCON + [MOON], "Paris, sir.", "Shall I send that fact to your notes?"),
    ("What's the capital of France?", [MOON], "Paris, sir.", "Shall I send a copy of that fact to your notes?"),
    ("What's the capital of France?", FALCON + [MOON], "Paris, sir.", "Shall I save the project name to your notes?"),
    ("What's the capital of France?", FALCON + [MOON], "Paris, sir.", "Shall I start the Falcon project log now?"),
    ("What's the largest ocean?", FALCON + [MOON], "The Pacific, sir.",
     "Shall I lock in Falcon as the name for your garage project?"),
    ("Give me a short motivational line.", [MOON], "Keep going, sir; small steps add up.",
     "Shall I save the text of that line to your notes?"),
    ("Give me a short motivational line.", [MOON], "Keep going, sir; small steps add up.",
     "Shall I save that message to your notes?"),
])
def test_an_offer_to_keep_it_worded_with_a_steps_verb_is_still_trimmed(asked, earlier, answer, offer):
    said, p = _heard(earlier, asked, [answer, offer])
    assert said == [answer] and not p.stale


@pytest.mark.parametrize("asked,answer,offer", [
    ("Can you convert five miles to kilometers?", "About 8 kilometers, sir.",
     "Would you like a recipe to use up something in the pantry?"),
    ("Could you translate thank you into French?", "Merci, sir.", "Shall I check the weather for your commute tomorrow?"),
    ("Would you happen to know the largest ocean?", "The Pacific, sir.",
     "Would you like a recipe to use up something in the pantry?"),
])
def test_an_offer_about_something_else_after_a_question_worded_as_a_request_is_trimmed(asked, answer, offer):
    assert speech.polish(f"{answer} {offer}", asked=asked) == answer
    said, _p = _heard([MOON], asked, [answer, offer])
    assert said == [answer]


def test_after_a_tool_ran_in_the_one_more_try_a_local_failure_reaches_the_cloud_fallback(monkeypatch):
    """The one more try ran get_weather, so the draft can no longer be said: when both local tiers then fail, the turn
    goes to the cloud fallback (Gemini) as any turn does, never straight to the Claude turn. The real
    intelligence.request runs; only the model calls and the tool are stand-ins."""
    import json
    from brain import llm
    from core import cloudmodel, intelligence, pcfast, quick
    from tools import registry
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.delenv("JARVIS_LOCAL_ONLY", raising=False)
    monkeypatch.delenv("JARVIS_PC_URL", raising=False)
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    steps, events = iter([SPANISH[1], '"Gracias", sir.', "retry"]), []

    def complete_local(body, cancelled=lambda: False, on_text=None):
        step = next(steps, None)
        if step is None:
            events.append(("local-fail", body["role"]))
            raise intelligence.LocalFailure("invalid_response")
        if on_text:
            on_text("One sec, sir." if step == "retry" else step)
        if step != "retry":
            return _local_reply(step, body["role"])
        events.append("retry-tool-call")
        call = {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": json.dumps({"days": 2})}}
        return {"model": intelligence.model(body["role"]),
                "choices": [{"message": {"role": "assistant", "content": "One sec, sir.", "tool_calls": [call]},
                             "finish_reason": "tool_calls"}]}
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    monkeypatch.setattr(cloudmodel, "available", lambda: True)
    monkeypatch.setattr(cloudmodel, "complete", lambda payload, cancelled=lambda: False: events.append("gemini") or {
        "model": "gemini-cloud", "choices": [{"message": {"role": "assistant", "content": "Tomorrow brings 58 and rain, sir."},
                                              "finish_reason": "stop"}]})
    monkeypatch.setattr(registry, "run", lambda name, args: ("Forecast:\nToday: 62F, sunny.\nTomorrow: 58F, rain.", False))
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    monkeypatch.setattr(b, "_cloud_turn", lambda emit: events.append("claude") or emit("Claude answer."))
    b.chat_system = ""           # the manual's path and its one more try are what is scripted here, not the chat path
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.ask(SPANISH[0], lambda s: None)
    said = []
    b.ask("Will it rain tomorrow?", said.append)
    assert events == ["retry-tool-call", ("local-fail", "fast"), ("local-fail", "advanced"), "gemini"]
    assert said == ["One sec, sir.", "Tomorrow brings 58 and rain, sir."]


def test_a_tool_call_with_no_words_never_breaks_the_cloud_fallback():
    # messages_for_local gives an assistant tool call with no text content None; reading what Jarvis last said must not
    # crash there (it did, so a local failure after a tool round skipped Gemini for Claude: review 2026-11-11).
    from core import intelligence
    history = [{"role": "user", "content": "Will it rain tomorrow?"},
               {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                                                                       "function": {"name": "get_weather", "arguments": "{}"}}]},
               {"role": "tool", "tool_call_id": "c1", "content": "Tomorrow: 58F, rain."},
               {"role": "user", "content": "And the day after?"}]
    assert intelligence._last_assistant_text(history) == ""
    assert intelligence.jarvis_asked(history) == ""
