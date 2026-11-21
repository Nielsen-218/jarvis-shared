"""Chat voice (live 2026-11-12): he parroted the example lines of his persona (one stock line twice in a row, a bare
acknowledgement as the reply to an open question) and greeted with "Evening." at eight in the morning. The persona now
describes shapes instead of lines, a plain chat reply that repeats one of his last few is written once more, and every
chat turn is told the hour in a per-request note. A fake transport stands in for the model; the clock is pinned."""
import re
from datetime import datetime

import pytest

from core import chatturn, config, intelligence, warmth
from tools import registry

BRAIN = "qwen3.5:9b"
MANUAL = "MANUAL-OPERATIONS " + "Follow the operating rules. " * 40
PERSONA = "PERSONA-CORE You are J.A.R.V.I.S., talking with them out loud."
MORNING, AFTERNOON = datetime(2026, 11, 12, 8, 10), datetime(2026, 11, 12, 15, 30)
EVENING, NIGHT, SMALL_HOURS = datetime(2026, 11, 12, 20, 50), datetime(2026, 11, 12, 23, 20), datetime(2026, 11, 13, 2, 40)


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_LOCAL_VISION_MODEL",
                "JARVIS_CHAT_FIRST", "JARVIS_CHAT_THINK", "JARVIS_PC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    monkeypatch.setattr(chatturn, "_now", lambda: EVENING)


def _transport(monkeypatch, *answers):
    """The model answers with `answers` in turn (the last one again after that); every body sent is recorded."""
    sent, left = [], list(answers)

    def complete_local(body, cancelled=lambda: False, on_text=None):
        sent.append(body)
        out = left.pop(0) if len(left) > 1 else left[0]
        if isinstance(out, Exception):
            raise out
        if on_text:
            for word in out.split(" "):
                on_text(word + " ")
        return {"model": BRAIN, "choices": [{"finish_reason": "stop", "message": {"content": out}}]}
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    return sent


def _ask(said, earlier=(), heard=None):
    history = list(earlier) + [{"role": "user", "content": said}]
    msg = intelligence.request("fast", MANUAL, history, registry.schemas(), chat_system=PERSONA,
                               on_text=heard.append if heard is not None else None)
    return " ".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _note(body):
    last = body["messages"][-1]["content"]
    return last if isinstance(last, str) else " ".join(p.get("text", "") for p in last)


def _chat(*pairs):
    out = []
    for you, jarvis in pairs:
        out += [{"role": "user", "content": you}, {"role": "assistant", "content": jarvis}]
    return out


# ------------------------------------------------------------------------------------------------------- the clock

@pytest.mark.parametrize("now, part", [(MORNING, "morning"), (AFTERNOON, "afternoon"), (EVENING, "evening"),
                                       (NIGHT, "night"), (SMALL_HOURS, "night"), (datetime(2026, 11, 12, 5, 0), "morning"),
                                       (datetime(2026, 11, 12, 12, 0), "afternoon"), (datetime(2026, 11, 12, 17, 0), "evening"),
                                       (datetime(2026, 11, 12, 22, 0), "night")])
def test_the_part_of_the_day(now, part):
    assert chatturn.day_part(now) == part


@pytest.mark.parametrize("now, words", [(MORNING, "it is Thursday morning"), (AFTERNOON, "it is Thursday afternoon"),
                                        (EVENING, "it is Thursday evening"), (NIGHT, "it is Thursday night"),
                                        (SMALL_HOURS, "it is the small hours of Friday"),
                                        (datetime(2026, 11, 14, 11, 57), "it is Saturday morning")])
def test_the_clock_note_says_the_weekday_and_the_part_of_the_day(now, words):
    note = chatturn.clock_note(now)
    assert words in note and not re.search(r"\d", note), "no hour to recite: the part of the day is what a greeting needs"


@pytest.mark.parametrize("now", [MORNING, AFTERNOON, EVENING, NIGHT])
def test_a_chat_turn_is_told_the_hour_on_his_newest_message_only(local, monkeypatch, now):
    monkeypatch.setattr(chatturn, "_now", lambda: now)
    sent = _transport(monkeypatch, "Hello.")
    _ask("Hey Jarvis.", _chat(("You there?", "I am.")))
    body = sent[0]
    assert chatturn.clock_note(now) in _note(body)
    assert all("For reference only: it is" not in str(m["content"]) for m in body["messages"][:-1])


def test_the_clock_does_not_move_the_cached_prefix(local, monkeypatch):
    sent = _transport(monkeypatch, "Hello.", "Quiet, mostly.")
    earlier = _chat(("You there?", "I am."))
    monkeypatch.setattr(chatturn, "_now", lambda: MORNING)
    _ask("Hey Jarvis.", earlier)
    monkeypatch.setattr(chatturn, "_now", lambda: NIGHT)
    _ask("Hey Jarvis.", earlier)
    a, b = sent[0]["messages"], sent[1]["messages"]
    assert a[:-1] == b[:-1] and a[-1] != b[-1], "only the note on his newest message differs between two hours"


def test_a_tool_turn_gets_no_clock_note(local, monkeypatch):
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: tools)
    sent = _transport(monkeypatch, "Right away.")
    try:
        _ask("Open Notepad.")
    except intelligence.LocalFailure:
        pass
    assert sent and all("For reference only: it is" not in str(body["messages"]) for body in sent)


@pytest.mark.parametrize("now, written, heard", [
    (MORNING, "Evening.", "Morning."),
    (MORNING, "Good evening, sir. Quiet so far.", "Good morning, sir. Quiet so far."),
    (AFTERNOON, "Morning, sir.", "Afternoon, sir."),
    (AFTERNOON, "Good afternoon.", "Good afternoon."),
    (EVENING, "Good morning.", "Good evening."),
    (EVENING, "Evening.", "Evening."),
    (NIGHT, "Good afternoon. You are up late.", "Hello. You are up late."),
    (NIGHT, "Evening.", "Evening."),
    (SMALL_HOURS, "Morning.", "Hello."),
    (SMALL_HOURS, "Good evening.", "Hello."),
    (SMALL_HOURS, "Evening, sir. Still up?", "Hello, sir. Still up?"),
    (SMALL_HOURS, "Hello.", "Hello."),
    (datetime(2026, 11, 13, 4, 12), "Good evening.", "Hello."),
])
def test_a_greeting_matches_the_pinned_clock(local, monkeypatch, now, written, heard):
    monkeypatch.setattr(chatturn, "_now", lambda: now)
    _transport(monkeypatch, written)
    spoken = []
    assert _ask("Hey Jarvis.", heard=spoken) == heard
    assert "".join(spoken).strip() in ("", heard), "what was streamed is the corrected greeting too"


def test_his_own_greeting_said_back_is_left_alone(local, monkeypatch):
    monkeypatch.setattr(chatturn, "_now", lambda: MORNING)
    _transport(monkeypatch, "Good evening.")
    assert _ask("Good evening.") == "Good evening."


@pytest.mark.parametrize("reply", ["Tomorrow morning is the better time for it.", "The evening news will cover it.",
                                   "I would leave it until the afternoon."])
def test_a_time_of_day_that_is_not_a_greeting_is_not_touched(reply):
    assert chatturn.right_greeting(reply, "", MORNING if "morning" not in reply else EVENING) == reply
    assert not chatturn.wrong_greeting(reply, "", NIGHT)


# --------------------------------------------------------------------------------------- never the same line twice

TIRED = _chat(("Long week, honestly.", "Then let the day end early. Nothing here needs you."))


def test_a_repeated_reply_is_written_once_more_and_nothing_of_the_first_is_spoken(local, monkeypatch):
    sent = _transport(monkeypatch, "Then let the day end early. Nothing here needs you.", "Bed sounds like the sound decision.")
    spoken = []
    assert _ask("I just want to go to bed.", TIRED, heard=spoken) == "Bed sounds like the sound decision."
    assert len(sent) == 2 and "".join(spoken).strip() == ""
    assert "You already said" in _note(sent[1]) and "Then let the day end early" in _note(sent[1])
    assert sent[1]["messages"][:-1] == sent[0]["messages"][:-1], "the retry keeps the cached prefix"


@pytest.mark.parametrize("again", ["then let the day end early, nothing here needs you",
                                   "Then let the day end early. Nothing here needs you now.",
                                   "Sleep, then. Nothing here needs you."])
def test_nearly_the_same_counts_as_the_same(again):
    assert chatturn.same_reply(again, "Then let the day end early. Nothing here needs you.")


@pytest.mark.parametrize("reply, earlier", [("Bed sounds like the sound decision.", "Then let the day end early."),
                                            ("Hello.", "Hello again, sir."), ("Quiet, mostly.", "Quite well, mostly idle.")])
def test_a_different_line_is_not_a_repeat(reply, earlier):
    assert not chatturn.same_reply(reply, earlier)


def test_only_his_last_few_replies_are_compared(local, monkeypatch):
    old = _chat(("Thanks.", "Any time.")) + _chat(*[(f"Remark {i}.", f"Reply number {i} entirely.") for i in range(4)])
    sent = _transport(monkeypatch, "Any time.")
    assert _ask("Thanks.", old) == "Any time." and len(sent) == 1


def test_a_second_attempt_that_repeats_again_is_still_said_never_silence(local, monkeypatch):
    sent = _transport(monkeypatch, "Then let the day end early. Nothing here needs you.")
    assert _ask("I just want to go to bed.", TIRED) == "Then let the day end early. Nothing here needs you."
    assert len(sent) == 2, "written once more, exactly once"


def test_a_failed_second_attempt_leaves_the_first_reply(local, monkeypatch):
    sent = _transport(monkeypatch, "Then let the day end early. Nothing here needs you.", intelligence.LocalFailure("timeout"))
    assert _ask("I just want to go to bed.", TIRED) == "Then let the day end early. Nothing here needs you."
    assert len(sent) == 2


def test_an_open_question_is_answered_not_acknowledged(local, monkeypatch):
    sent = _transport(monkeypatch, "Fair enough.", "Quiet, which I count as a success.")
    spoken = []
    assert _ask("How is it going?", heard=spoken) == "Quiet, which I count as a success."
    assert len(sent) == 2 and "does not answer it" in _note(sent[1]) and "".join(spoken).strip() == ""


@pytest.mark.parametrize("said", ["No, that's all right.", "Makes sense.", "Is that so?"])
def test_an_acknowledgement_is_right_for_a_decline_or_a_remark(local, monkeypatch, said):
    sent = _transport(monkeypatch, "Very good.")
    assert _ask(said) == "Very good." and len(sent) == 1


def test_a_fresh_reply_streams_as_it_is_written(local, monkeypatch):
    sent = _transport(monkeypatch, "Bed sounds like the sound decision.")
    spoken = []
    assert _ask("I just want to go to bed.", TIRED, heard=spoken) == "Bed sounds like the sound decision."
    assert len(sent) == 1 and "".join(spoken).strip() == "Bed sounds like the sound decision."


def test_a_reply_that_starts_like_an_earlier_one_and_then_differs_is_released(local, monkeypatch):
    earlier = _chat(("Long week.", "Then let the day end early, and leave the rest of it to tomorrow, when it will look smaller."))
    _transport(monkeypatch, "Then let the kettle do the work for a while.")
    spoken = []
    assert _ask("I could use a break.", earlier, heard=spoken) == "Then let the kettle do the work for a while."
    assert "".join(spoken).strip() == "Then let the kettle do the work for a while."


def test_an_explanation_is_not_compared(local, monkeypatch):
    answer = "It is water, not smoke. The engines leave hot vapour behind. It freezes at once. So you see a line of cloud."
    sent = _transport(monkeypatch, answer)
    assert _ask("Why do planes leave white trails?", _chat(("Why do planes leave trails?", answer))) == answer
    assert len(sent) == 1


def test_the_hold_releases_only_what_can_no_longer_be_a_repeat():
    out = []
    hold = chatturn.Hold(out.append, _chat(("Hi.", "Hello. Quiet here.")), "Hey.", now=EVENING)
    for piece in ("Hello. ", "Quiet "):
        hold.feed(piece)
    assert out == [] and not hold.spoke
    hold.feed("as ")
    hold.feed("ever.")
    assert "".join(out) == "Hello. Quiet as ever." or (out == [] and not hold.spoke)


# ----------------------------------------------------------------------------------------------------- the persona

def test_the_persona_has_no_whole_reply_to_copy():
    persona = config.load_persona_core(None, "owner")
    for stock in ("Fair enough.", "take it easy", "The rest can wait", "Evening.", "Any time.", "Nothing to report",
                  "Quiet, in fact", "Very good."):
        assert stock not in persona, stock
    quoted = re.findall(r'"[A-Z][^"\n]*[a-z][.?!]"', persona)
    assert not quoted, f"a whole quoted sentence is a line he will repeat: {quoted}"


def test_the_persona_asks_for_dry_wit_without_servility_and_fresh_lines():
    persona = config.load_persona_core(None, "owner")
    # The voice is the character sheet since 2026-11-14 (core/character.md, the owner's choices: light humor that is
    # never on him, no help-desk talk); "British understatement" and "nobody's servant" produced mockery and are gone.
    assert "Humor is light and rare" in persona and "standing by" in persona
    assert "never say the same sentence twice" in persona
    assert "is a question" in persona and "answer it" in persona


# ---------------------------------------------------------------------------------- review 2026-11-12 (second pass)

@pytest.mark.parametrize("answer", ["Right, so sunlight scatters off the air, and blue scatters the most of all.",
                                    "Good question. Sunlight scatters off the air, and blue scatters the most.",
                                    "Certainly not by accident: the air scatters blue light more than red.",
                                    "Indeed it is an old question, and the air is the answer to it."])
def test_an_answer_that_only_opens_like_an_acknowledgement_is_not_held_back(answer):
    out = []
    hold = chatturn.Hold(out.append, [], "Why is the sky blue?", now=EVENING)
    words = answer.split(" ")
    for word in words[:5]:
        hold.feed(word + " ")
    assert hold.spoke and "".join(out).strip() == " ".join(words[:5])


@pytest.mark.parametrize("reply", ["Fair enough.", "Fair enough, sir.", "Quite so, sir.", "Understood."])
def test_a_bare_acknowledgement_to_an_open_question_is_still_held(reply):
    out = []
    hold = chatturn.Hold(out.append, [], "How is it going?", now=EVENING)
    for word in reply.split(" "):
        hold.feed(word + " ")
    assert out == [] and not hold.spoke
    assert chatturn.stale(reply, "How is it going?", []) == ("unanswered", "")


def test_an_answer_that_opens_with_an_earlier_one_word_reply_is_not_held_back():
    out = []
    hold = chatturn.Hold(out.append, _chat(("That went well.", "Indeed.")), "Tell me about the moon.", now=EVENING)
    for word in "Indeed I can: it is a quarter of the width of the Earth.".split(" ")[:5]:
        hold.feed(word + " ")
    assert hold.spoke and "".join(out).startswith("Indeed I can:")


@pytest.mark.parametrize("said, reply, earlier", [
    ("Is that right?", "Yes.", _chat(("Is it Thursday?", "Yes."))),
    ("And is the door locked too?", "It is.", _chat(("Is the window shut?", "It is."))),
    ("Did I get that wrong?", "No, sir.", _chat(("Was that too loud?", "No, sir."))),
    ("So that is correct, right?", "Indeed.", _chat(("It is a prime, isn't it?", "Indeed."))),
    ("Sorry, what did you say?", "The kettle has the right idea.", _chat(("I need a break.", "The kettle has the right idea."))),
    ("Can you repeat that?", "Fair enough.", _chat(("I will pass.", "Fair enough."))),
    ("What is two and two?", "Four.", _chat(("What is two and two?", "Four."))),
])
def test_a_repeat_that_fits_the_line_is_left_alone(said, reply, earlier):
    history = earlier + [{"role": "user", "content": said}]
    assert chatturn.stale(reply, said, history) == ("", "")
    assert chatturn.stale(reply, said, earlier) == ("", "")          # the history may or may not end with his line


@pytest.mark.parametrize("said, reply, earlier", [
    ("How is it going?", "Fair enough.", _chat(("I will pass.", "Fair enough."))),
    ("No thanks.", "Fair enough.", _chat(("Not today.", "Fair enough."))),
    ("I am worn out.", "Then let the day end early. It will keep.", _chat(("Long day.", "Then let the day end early. It will keep."))),
    ("Is it ever going to end?", "Then let the day end early. It will keep.", _chat(("Long day.", "Then let the day end early. It will keep."))),
])
def test_a_stock_line_said_again_to_a_different_line_is_still_written_once_more(said, reply, earlier):
    assert chatturn.stale(reply, said, earlier + [{"role": "user", "content": said}])[0] == "repeat"


def test_a_yes_said_again_to_the_next_yes_or_no_question_is_asked_once(local, monkeypatch):
    sent = _transport(monkeypatch, "Yes.")
    assert _ask("Is that right?", _chat(("Is it Thursday?", "Yes."))) == "Yes." and len(sent) == 1


def test_the_note_for_a_repeat_keeps_the_answer_itself():
    note = chatturn.again_note("repeat", "Fair enough.")
    assert "different thought" not in note and "stays" in note
