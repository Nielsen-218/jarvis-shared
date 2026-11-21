"""Three small conversation defects seen live on 2026-11-13 (own invented lines; nothing here reads the owner's data):
a bare "Right." answered "Right."; "Did you close Notepad?" answered with a bare "I don't see a window"; and tool-path replies that
end by handing the turn back ("Just say which program...", "What else would you like me to know?")."""
import pytest

from core import pcfacts, speech


# ---- (i) an acknowledgement is never said back ------------------------------------------------------------------------
@pytest.mark.parametrize("asked, reply", [
    ("Right.", "Right."), ("Right.", "Right, sir."), ("Okay.", "Okay."), ("Okay.", "Okay, sir."), ("Okay, got it.", "Got it."),
    ("Got it.", "Got it, sir."), ("Yeah.", "Yeah."), ("All right.", "All right."), ("Alright.", "Alright, then."),
    ("Thanks.", "Thanks."), ("Sure.", "Sure, sir."), ("I see.", "I see."),
])
def test_his_acknowledgement_said_back_is_replaced_by_a_different_brief_one(asked, reply):
    assert speech.echoes_ack(reply, asked)
    said = []
    voice = speech.Polisher(said.append, asked=asked, chat=lambda: True)
    voice(reply)
    voice.settle()
    voice.finish()
    assert len(said) == 1 and said[0] != reply and not speech.echoes_ack(said[0], asked)
    assert said[0].endswith(".") and len(said[0].split()) <= 4      # brief, in his voice ("sir" on one line in three: core/character.py)
    assert voice.edits == [(reply, said[0])]                                           # and the stored reply is what was said


@pytest.mark.parametrize("asked, reply", [
    ("Right.", "Right, so the capital is Lisbon."), ("Right?", "Right."), ("Right.", "Indeed, sir."), ("Okay.", "Playing jazz now, sir."),
    ("What is the capital of Portugal?", "Lisbon."), ("Right.", "Which part did you mean, sir?"), ("Open Notepad.", "Open, sir."),
    ("Right.", ""), ("Thanks for the help with the report.", "Thanks."),
])
def test_other_replies_are_left_alone(asked, reply):
    assert not speech.echoes_ack(reply, asked)


def test_a_different_reply_is_chosen_than_the_last_one_said():
    first = speech.other_ack("Right.", "sir", "")
    again = speech.other_ack("Right.", "sir", first)
    assert first != again
    assert speech.other_ack("Thanks.", "sir").split(",")[0].rstrip(".") in ("Not at all", "My pleasure", "Any time")


# ---- (ii) "did you close / open" answers the state, clearly ---------------------------------------------------------------
@pytest.fixture
def pc(monkeypatch):
    class Box:
        windows: list = []
        running = False
    box = Box()
    monkeypatch.setattr(pcfacts, "_windows_of_app", lambda app: box.windows)
    monkeypatch.setattr(pcfacts, "_running", lambda app: box.running)
    monkeypatch.setattr(pcfacts, "_title", lambda: "sir")
    monkeypatch.setattr(pcfacts, "_display", lambda app: app.title())
    return box


@pytest.mark.parametrize("want, did, windows, running, expected", [
    ("closed", "did", [], False, "Yes, sir, I don't see a Notepad window any more."),
    ("closed", "did", [{"foreground": False}], False, "No, sir, Notepad is still open."),
    ("closed", "did", [], True, "No, sir, Notepad is still running, though I don't see a window."),
    ("open", "did", [{"foreground": True}], False, "Yes, sir, Notepad is open."),
    ("open", "did", [], False, "No, sir, I don't see a Notepad window."),
    ("open", "did", [], True, "Yes, sir, Notepad is running, though I don't see a window."),
    ("closed", "", [], False, "Yes, sir, I don't see a Notepad window any more."),
    ("open", "", [], False, "No, sir, I don't see a Notepad window."),
    ("open", "", [{"foreground": True}], False, "Yes, sir, Notepad is open. It's in front."),
])
def test_did_you_close_or_open_starts_with_a_clear_yes_or_no_that_matches_what_is_seen(pc, want, did, windows, running, expected):
    pc.windows, pc.running = windows, running
    said = pcfacts.answer({"app": "notepad", "want": want, "did": did})
    assert said == expected
    assert "I closed" not in said and "I opened" not in said and "I did" not in said          # no claim to remember an action
    assert said.split(",")[0] in ("Yes", "No") or said.startswith("Notepad is running")


# ---- (iii) the tool-manual path ends on the answer, not on a menu ------------------------------------------------------------
ANSWER = "Opened Notepad, sir."
CLOSERS = [
    "Just say which program you'd like next.", "Let me know which program you want.",
    "What else would you like me to know?", "What else can I do for you?", "What else would you like me to check?",
    "What would you like to do next?", "What do you want me to do now?", "Is there anything else?", "Is there anything else I can help with?",
    "Anything else?", "Anything else you need, sir?", "Let me know if you need anything else.", "Just say the word.",
    "Feel free to ask for another program.", "I can open, close or search for programs.", "I can launch apps, find files or adjust settings.",
    "Tell me what you'd like next.", "Just let me know what you want next.", "I'm here if you need anything.",
    "Say the word and I will open another one.", "What program would you like next?", "What task would you like to try next?",
    "Give me a shout when you are ready.",
]
KEPT = [
    "Which one did you mean?", "Shall I close it?", "Notepad or Paint?", "Do you mean the file in Documents?", "Which window should I close, the first or the second?",
    "I found two windows, sir. Which one should I close?", "Shall I restart it now?", "Do you want me to delete it?", "Should I send it to Ana?",
    "Which version did you want, 32 or 64 bit?", "Did you mean the work account?", "Is that the right folder?", "It is 21 degrees, sir.",
    "Volume is at 40 percent.", "I opened the second one.", "Say again which tab you meant, one or two.",
    # a request for the missing detail of THIS request is never a hand-back (review finding: these were dropped)
    "Tell me which contact you want to call.", "What program would you like to close?", "Is there anything specific you want it to say?",
    "Tell me which song you want.", "Tell me what time you want the alarm.", "What else should I put in the note?",
    "Just tell me which app to open.", "What would you like to open?", "What else do you want it to say?",
    "Say what name you want on the card.", "Which program would you like to close?", "Tell me what number to dial.",
]


def _tool_reply(sentences, asked="open notepad", keep=False):
    said = []
    voice = speech.Polisher(said.append, asked=asked, chat=lambda: False, keep=lambda: keep)
    for sentence in sentences:
        voice(sentence)
    voice.settle()
    voice.finish()
    return said, voice.edits


@pytest.mark.parametrize("closer", CLOSERS)
def test_a_trailing_offer_or_menu_is_dropped_when_the_reply_already_answers(closer):
    assert speech.manual_closer(closer), closer
    said, edits = _tool_reply([ANSWER, closer])
    assert said == [ANSWER] and (closer, "") in edits


@pytest.mark.parametrize("sentence", KEPT)
def test_a_real_question_or_a_fact_is_never_dropped(sentence):
    assert not speech.manual_closer(sentence), sentence
    said, _ = _tool_reply([ANSWER, sentence])
    assert said == [ANSWER, sentence]


@pytest.mark.parametrize("closer", CLOSERS[:8])
def test_a_closer_is_kept_when_nothing_answered_or_a_question_is_waiting(closer):
    said, _ = _tool_reply([closer])
    assert said == [closer]                                   # never dead air
    said, _ = _tool_reply([ANSWER, closer], keep=True)
    assert said == [ANSWER, closer]                           # an approval is pending: the question stays


def test_a_closer_in_the_middle_of_a_reply_is_said_in_order():
    said, _ = _tool_reply([ANSWER, "Just say which program you'd like next.", "The window is on your second monitor."])
    assert said[0] == ANSWER and said[-1] == "The window is on your second monitor."


def test_the_chat_path_is_judged_by_its_own_rules_not_this_one():
    said = []
    voice = speech.Polisher(said.append, asked="Long day.", chat=lambda: True)
    voice("That sounds like a long one.")
    voice("Just say which program you'd like next.")          # not a chat offer shape: unchanged by the manual filter
    voice.settle()
    voice.finish()
    assert said[0] == "That sounds like a long one."


@pytest.mark.parametrize("pair", [
    ("I can place the call.", "Tell me which contact you want to call."),
    ("Several programs are open.", "What program would you like to close?"),
    ("I have the draft ready.", "Is there anything specific you want it to say?"),
    ("I can play music, sir.", "Tell me which song you want."),
])
def test_a_request_for_the_missing_detail_is_spoken_after_an_answer(pair):
    said, _ = _tool_reply(list(pair), asked="do it")
    assert said == list(pair)
