"""The first clause of a streamed local reply is said ahead of its sentence (brain.llm.first_clause, _SpokenAsWritten)."""
from brain import llm


def stream(text, chat="", **kw):
    said = []
    live = llm._SpokenAsWritten(said.append, **kw)
    if chat:
        live.as_chat(chat)
    for i in range(0, len(text), 5):                 # the model's pieces arrive a few characters at a time
        live.feed(text[i:i + 5])
    return live, said


def test_clause_is_released_before_the_sentence_ends():
    live, said = stream("Well, that depends on what you mean by fast, and how far you plan to go")
    assert said and said[0] == "Well, that depends on what you mean by fast,"
    assert live.units == 0 and live.prefix.startswith("Well")


def test_whole_sentence_still_counts_once():
    live, said = stream("Honestly it is a bit of both, depending on the day. Next thing.")
    assert said[0] == "Honestly it is a bit of both,"
    assert said[1] == "depending on the day."
    assert live.units == 1 and live.prefix == ""


def test_short_clause_waits_for_the_sentence():
    _, said = stream("Well, it depends on the weather today. And more.")
    assert said == ["Well, it depends on the weather today."]


def test_numbers_do_not_split():
    assert llm.first_clause("The total comes to 3.5 million, which is about right") ==         ("The total comes to 3.5 million,", "which is about right")
    assert llm.first_clause("It was 1,000 people at 3:30 in the afternoon today") is None


def test_title_stays_with_its_clause():
    assert llm.first_clause("Everything looks fine from here, sir, nothing to report today") ==         ("Everything looks fine from here, sir,", "nothing to report today")
    assert llm.first_clause("Everything looks fine from here, sir") is None


def test_abbreviation_and_quotes():
    assert llm.first_clause('He said "wait for me, I am nearly there" and left') is None
    assert llm.first_clause("Ask Dr. Smith, who knows the area very well indeed") is None   # "Dr." reads as a sentence end


def test_sentence_end_first_is_not_a_clause():
    assert llm.first_clause("That is fine. Then, as usual, we go on and on") is None


def test_french_and_creole_clauses():
    live, said = stream("Bien entendu que oui, je peux vous expliquer comment cela fonctionne ici")
    assert said and said[0].startswith("Bien entendu que oui,")
    live, said = stream("Wi mesye, mwen ka ede w ak sa a, men fok ou ban m plis detay")
    assert said and said[0].endswith("ak sa a,")


def test_action_claim_waits_for_whole_sentence():
    _, said = stream("I have opened the browser for you, and it is ready now. ")
    assert said == [] or said[0].endswith("now.")
    _, said = stream("I'll open the browser for you, as soon as I can. ", idle=True)
    assert not said


def test_ignore_is_never_said():
    _, said = stream("[ignore]")
    assert said == []
    _, said = stream("Hmm, that was not meant for me at all, [ignore]", chat="chat")
    assert all("ignore" not in s for s in said)


def test_final_text_does_not_repeat_the_clause(monkeypatch):
    live, said = stream("Well, that depends on what you mean by fast, and how")
    first = "Well, that depends on what you mean by fast, and how far you go."
    parts = llm._SENTENCE_END.split(first)[live.units:]
    assert live.prefix and parts[0].startswith(live.prefix)
    assert parts[0][len(live.prefix):].lstrip(" ,;:") == "and how far you go."


# -- review findings (2026-11-13) ---------------------------------------------------------------------------------------
import pytest
from types import SimpleNamespace

from core import intelligence


@pytest.mark.parametrize("size", [1, 3, 5, 9])
@pytest.mark.parametrize("kind", ["chat", "depth", "think"])
@pytest.mark.parametrize("text", ["Sounds like background chatter, nothing for me here. [ignore]",
                                  "That sounds like the TV, not me. [ignore]"])
def test_a_chat_reply_is_never_released_early_it_may_end_in_ignore(size, kind, text):
    """The model writes [ignore] after its remark: a chat reply's first clause waits for the whole reply's verdict."""
    said = []
    live = llm._SpokenAsWritten(said.append)
    live.as_chat(kind)
    for i in range(0, len(text), size):
        live.feed(text[i:i + size])
    assert said == [] and live.held


def test_a_chat_reply_still_says_its_sentences_whole():
    live, said = stream("Honestly it is a bit of both, depending on the day. Then more words arrive here. ", chat="chat")
    assert said[0] == "Honestly it is a bit of both, depending on the day." and live.units >= 1 and live.prefix == ""


EARLIER = "It's raining in Springfield right now, with a high of fifty degrees."


def test_a_clause_that_opens_like_an_earlier_reply_waits_for_its_sentence():
    live, said = stream("It is raining in Springfield right now, with a high of fifty degrees. And", earlier=[EARLIER])
    assert said == ["It is raining in Springfield right now, with a high of fifty degrees."]
    assert live.prefix == "" and live.units == 1


def test_an_unrelated_earlier_reply_does_not_hold_a_clause():
    _, said = stream("Well, that depends on what you mean by fast, and how far you plan to go", earlier=[EARLIER])
    assert said and said[0] == "Well, that depends on what you mean by fast,"


def test_a_near_copy_of_an_earlier_answer_still_reaches_the_polishers_retry():
    """The Polisher's repeat test reads whole sentences: a clause said ahead of one slipped past it for good."""
    from core import speech
    heard = []
    voice = speech.Polisher(heard.append, EARLIER, asked="What's the weather tomorrow?", english=True,
                            earlier=[("What's the weather today?", EARLIER)], retry=True)
    live = llm._SpokenAsWritten(voice, earlier=[EARLIER])
    text = "It is raining in Springfield right now, with a high of fifty degrees. And more."
    for i in range(0, len(text), 5):
        live.feed(text[i:i + 5])
    voice.settle()
    assert heard == [] and voice.stale


def test_after_clause_survives_a_swapped_title():
    assert llm.after_clause("I am doing fine, sir, and ready for whatever comes next.", "I am doing fine, mesye,") == \
        "and ready for whatever comes next."
    assert llm.after_clause("I am doing fine, mesye, and ready.", "I am doing fine, mesye,") == "and ready."
    assert llm.after_clause("A different first sentence entirely, and more.", "I am doing fine, mesye,") is None
    assert llm.after_clause("I am doing fine, sir.", "I am doing fine, mesye,") == ""


def _brain(monkeypatch, script):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    b._local_role, b._cloud_calls = "fast", 0
    b.messages = [{"role": "user", "content": "How are you doing today?"}]
    calls = []

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        calls.append(role)
        return script(on_text)
    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    return b, calls


def test_the_final_text_does_not_repeat_a_clause_whose_title_was_swapped(monkeypatch):
    from anthropic.types import TextBlock
    raw = "I am doing fine, mesye, and ready for whatever comes next."

    def script(on_text):
        for i in range(0, len(raw), 5):
            on_text(raw[i:i + 5])
        return SimpleNamespace(content=[TextBlock(type="text", text=intelligence._titled(raw))], stop_reason="end_turn")
    b, _ = _brain(monkeypatch, script)
    said = []
    b._stream_turn(said.append)
    assert said[0].startswith("I am doing fine,")
    assert " ".join(said).count("I am doing fine") == 1, said


def test_a_failure_after_only_a_clause_goes_on_to_the_next_tier(monkeypatch):
    """A clause is not an answer: the turn is not ended on it, and the next tier does not say a clause of its own."""
    def script(on_text):
        text = "Okay that works fine, I turned your lights off for you."
        for i in range(0, len(text), 5):
            on_text(text[i:i + 5])
        raise intelligence.LocalFailure("invalid_response")
    b, calls = _brain(monkeypatch, script)
    from core import usage
    monkeypatch.setattr(usage, "over_budget", lambda: False)
    monkeypatch.setattr(b, "_cloud_turn", lambda emit: emit("<next tier>") or None)
    said = []
    msg = b._stream_turn(said.append)
    assert calls == ["fast", "advanced"]
    assert said == ["Okay that works fine,", "<next tier>"], said
    assert msg is None or "Okay that works fine," not in getattr(msg.content[0], "text", "")


def test_a_failure_after_a_whole_sentence_still_ends_on_it(monkeypatch):
    def script(on_text):
        on_text("Of course, sir. ")
        on_text('Also {"name": "nope"')
        raise intelligence.LocalFailure("invalid_tool_call")
    b, _ = _brain(monkeypatch, script)
    said = []
    msg = b._stream_turn(said.append)
    assert said == ["Of course, sir."] and msg.content[0].text == "Of course, sir."
