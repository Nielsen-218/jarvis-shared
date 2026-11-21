"""Conversation replay 5 (2026-11-13): what a replay of recent conversational lines through the real brain showed, fixed at
the root. Fake transport, synthetic lines only; nothing here calls a model or reads the owner's words."""
import pytest

from core import chatturn, intelligence
from tools import registry
from tests.test_chat_first import MANUAL, PERSONA, _transport, _text, local, no_skill  # noqa: F401  (fixtures)

SCHEMAS = [{"type": "function", "function": {"name": "set_volume"}}]
TALK = [{"role": "user", "content": "Morning."}, {"role": "assistant", "content": "Morning."}]


def _hist(said, before="Quite so.", tooled=False):
    out = [{"role": "user", "content": "Hm."}]
    if tooled:
        out.append({"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]})
        out.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]})
    out.append({"role": "assistant", "content": before})
    out.append({"role": "user", "content": said})
    return out


# ------------------------------------------------------------------------------------------ fragments are not commands
@pytest.mark.parametrize("said", ["Oh.", "Okay, got it.", "Sigh.", "Right.", "Thanks. That actually helps.", "Exactement.",
                                  "Yeah, okay.", "That helps."])
def test_a_bare_acknowledgement_with_a_skill_recalled_is_talk(said):
    assert chatturn.fragment_talk(said)
    assert not chatturn.command_like(said, _hist(said), SCHEMAS)


@pytest.mark.parametrize("said", ["It's too loud", "That's frozen", "Jazz maybe", "Louder", "Claude hello there"])
def test_other_short_lines_with_a_skill_recalled_keep_the_tools(said):
    assert not chatturn.fragment_talk(said)
    assert chatturn.command_like(said, _hist(said, "It rained most of the night."), SCHEMAS) or said == "Claude hello there"


def test_his_yes_to_an_offer_is_still_not_talk():
    said = "Okay."
    assert chatturn.command_like(said, _hist(said, "Shall I set a reminder?"), SCHEMAS)


def test_an_ok_right_after_an_action_is_still_a_command_context():
    assert chatturn.command_like("Okay.", _hist("Okay.", "Playing jazz now, sir."), SCHEMAS)


def test_an_ok_after_a_tool_ran_keeps_the_tools():
    assert chatturn.command_like("Right.", _hist("Right.", "Your timer is on.", tooled=True), SCHEMAS)


def test_a_fragment_after_chat_is_planned_as_chat(local, monkeypatch):
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: SCHEMAS)
    for said in ("Okay, got it.", "Oh.", "Sigh."):
        assert chatturn.plan("fast", _hist(said), SCHEMAS, False, None) == chatturn.CHAT


# ------------------------------------------------------------------------------------------ overheard speech
LONG = ("so what I need is for the new version to look a lot better than the old one and to feel smoother when I use it "
        "every morning and I want the whole thing planned properly before anything gets changed or rebuilt this week")


def test_a_long_line_without_a_question_or_his_name_is_overheard():
    assert len(LONG.split()) >= chatturn.OVERHEARD_WORDS
    assert chatturn.overheard(LONG, _hist(LONG))


@pytest.mark.parametrize("said", [LONG + "?", "Jarvis, " + LONG, LONG.replace("so what", "hey jarvis what"),
                                  "so what I need is the new version to look better"])
def test_a_question_his_name_or_a_short_line_is_not_overheard(said):
    assert not chatturn.overheard(said, _hist(said))


def test_after_a_question_only_a_very_long_line_is_overheard():
    assert not chatturn.overheard(LONG, _hist(LONG, "Which one did you mean?"))
    very_long = LONG + " " + LONG
    assert len(very_long.split()) >= chatturn.OVERHEARD_LONG
    assert chatturn.overheard(very_long, _hist(very_long, "Which one did you mean?"))


def test_after_a_tool_a_long_line_is_not_overheard():
    assert not chatturn.overheard(LONG, _hist(LONG, "Done.", tooled=True))


def test_an_overheard_line_takes_the_chat_path_with_the_ignore_note(local, monkeypatch):
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: SCHEMAS)
    history = _hist(LONG)
    assert chatturn.plan("fast", history, SCHEMAS, False, None) == chatturn.CHAT
    assert chatturn.note(chatturn.CHAT, history) == chatturn.OVERHEARD_NOTE
    assert "[ignore]" in chatturn.OVERHEARD_NOTE


def test_an_overheard_line_the_model_ignores_is_silent(local, no_skill, monkeypatch):
    sent = _transport(monkeypatch, "[ignore]")
    msg = intelligence.request("fast", MANUAL, _hist(LONG), registry.schemas(), chat_system=PERSONA)
    assert _text(msg).strip() == "[ignore]"
    assert sent[0]["profile"] == "chat" and chatturn.OVERHEARD_NOTE in str(sent[0]["messages"][-1]["content"])


# ------------------------------------------------------------------------------------------ the lookup on a remark
def _with_recall(said):
    return [{"role": "user", "content": "Is working from home better?"},
            {"role": "assistant", "content": "Mostly, yes."},
            {"role": "user", "content": [{"type": "text", "text": "<retrieved-memory>\nHistorical context only.\n"
                                                                  "- Studies for a networking exam.\n</retrieved-memory>"},
                                         {"type": "text", "text": said}]}]


def test_without_recall_takes_the_lookup_off_the_newest_message_only():
    messages = [{"role": "user", "content": [{"type": "text", "text": "<retrieved-memory>\nold\n</retrieved-memory>"},
                                             {"type": "text", "text": "first"}]},
                {"role": "assistant", "content": "ok"}] + _with_recall("Even for creative work?")[2:]
    out = chatturn.without_recall(messages)
    assert out[0] == messages[0], "an older message keeps what it had"
    assert [p["text"] for p in out[-1]["content"]] == ["Even for creative work?"]
    assert len(messages[-1]["content"]) == 2, "the input is not changed"


def test_without_recall_leaves_plain_text_alone():
    messages = [{"role": "user", "content": "Hi"}]
    assert chatturn.without_recall(messages) == messages


def _recalled_sent(monkeypatch, said):
    sent = _transport(monkeypatch, "Quite.")
    intelligence.request("fast", MANUAL, _with_recall(said), registry.schemas(), chat_system=PERSONA)
    return str(sent[0]["messages"][-1]["content"])


def test_a_remark_gets_no_memory_lookup_to_recite(local, no_skill, monkeypatch):
    sent = _recalled_sent(monkeypatch, "Even for creative work?")
    assert "networking exam" not in sent


def test_a_question_about_him_keeps_its_lookup(local, no_skill, monkeypatch):
    sent = _transport(monkeypatch, "You are studying for a networking exam.")
    history = _with_recall("What am I studying right now?")
    intelligence.request("fast", MANUAL, history, registry.schemas(), chat_system=PERSONA)
    if sent:                         # (a question about him may also go to the manual's path, which keeps it as well)
        assert "networking exam" in str(sent[0]["messages"][-1]["content"])


# ------------------------------------------------------------------------------------------ a common word is no skill
def _skills(*names):
    return [{"type": "function", "function": {"name": n}} for n in names]


@pytest.mark.parametrize("said, name", [
    ("Yeah, it took all weekend. Grey, in the end.", "end_workout"),
    ("Ha, fair. I'm going to hang some pictures next.", "jarvis_study_next_source"),
    ("How far apart are those two?", "bind_two_bone_limb"),
    ("I've been learning guitar for about a month.", "jarvis_learning_status"),
    ("Okay, what did I say about tomorrow?", "language_say"),
])
def test_one_common_word_of_a_longer_skill_name_does_not_point_at_it(said, name):
    assert chatturn.plain_talk(said)
    assert not chatturn.pointed_at(said, _skills(name))


@pytest.mark.parametrize("said, name", [("Do you like the weather here?", "get_weather"), ("End my workout.", "end_workout"),
                                        ("Is there a timer running?", "set_timer"), ("Quiz status please", "quiz_status")])
def test_a_skill_named_by_all_its_words_is_still_pointed_at(said, name):
    assert chatturn.pointed_at(said, _skills(name))


@pytest.mark.parametrize("said", ["I'm going to hang some pictures next.", "We watched a movie last night.",
                                  "My sister called me this morning.", "It's a long drive to Tampa."])
def test_everyday_words_in_a_statement_are_not_a_pc_topic(said):
    assert chatturn.plain_talk(said)


@pytest.mark.parametrize("said", ["Show me the pictures.", "I need to call the dentist.", "Can you call my sister?",
                                  "Where is my picture?", "My screen is frozen.", "The volume is too low.",
                                  "I want the weather for tomorrow."])
def test_a_request_or_a_problem_with_the_same_words_is_not_plain_talk(said):
    assert not chatturn.plain_talk(said)


# ------------------------------------------------------------------------------------------ the background on a remark
BACKGROUND = ("PERSONA\n<background>\nCurrent date and time: Friday.\nWhat you remember about this person (the rest is looked "
              "up with each question):\n- Is building a game called Halden Reach.\n- Has a son named Theo.\n"
              "How they want you to behave:\n- Prefers short answers.\nHousehold facts (shared):\n- A list on the fridge.\n"
              "Your latest conversations with this person (oldest first; pick up open threads naturally, don't recite "
              "them):\n- Fri 13 Nov 09:00: Talked about the rig. Still open: finish the weights.\n"
              "Lessons you've learned from past mistakes (follow them):\n- Keep it short.\n</background>")


def _lean(said, kind=chatturn.CHAT):
    messages = [{"role": "system", "content": BACKGROUND}, {"role": "user", "content": said}]
    return chatturn.lean_messages(messages, said, kind)[0]["content"]


@pytest.mark.parametrize("said", ["Do you get tired?", "Which would you pick if you had to?", "Even for creative work?"])
def test_a_plain_remark_gets_his_habits_and_lessons_but_not_his_facts_or_old_conversations(said):
    text = _lean(said)
    assert "Halden Reach" not in text and "Theo" not in text and "Still open" not in text
    assert "Prefers short answers" in text and "A list on the fridge" in text and "Keep it short" in text
    assert text.startswith("PERSONA") and text.rstrip().endswith("</background>")


@pytest.mark.parametrize("said", ["What's my son's name?", "Do you remember what I told you about the game?",
                                  "What am I working on?"])
def test_a_question_about_him_keeps_everything(said):
    assert "Halden Reach" in _lean(said)


def test_a_depth_turn_about_the_world_is_lean_too_but_one_about_him_keeps_everything():
    assert "Halden Reach" not in _lean("Do you think grey is boring?", chatturn.DEPTH)
    assert "Halden Reach" in _lean("How should I plan my week?", chatturn.DEPTH)


def test_lean_messages_changes_nothing_else():
    messages = [{"role": "user", "content": "hi"}]
    assert chatturn.lean_messages(messages, "hi", chatturn.CHAT) == messages
    assert chatturn.lean_messages([], "hi", chatturn.CHAT) == []


def test_a_chat_turn_sends_no_facts_for_a_remark_and_all_of_them_for_a_question_about_him(local, no_skill, monkeypatch):
    sent = _transport(monkeypatch, "Quite.")
    chat_system = BACKGROUND
    intelligence.request("fast", MANUAL, [{"role": "user", "content": "Do you get tired?"}], registry.schemas(),
                         chat_system=chat_system)
    system = str(sent[0]["messages"][0]["content"])
    assert "Halden Reach" not in system and "Prefers short answers" in system
    intelligence.request("fast", MANUAL, [{"role": "user", "content": "What is my son's name?"}], registry.schemas(),
                         chat_system=chat_system)
    again = str(sent[1]["messages"][0]["content"])
    assert "Halden Reach" in again or "MANUAL-OPERATIONS" in again, "a question about him is never answered from less"


# ------------------------------------------------------------------------------------------ Jarvis is not a service on call
@pytest.mark.parametrize("sentence", ["I am here and ready to listen.", "I'm ready when you are.", "What would you like to discuss?",
                                      "Understood, sir. I am listening.", "I am at your service.", "How may I help?",
                                      "I'm waiting for your next instruction.", "I am still here."])
def test_a_sentence_that_puts_jarvis_on_call_is_not_said_in_chat(sentence):
    assert intelligence.invents_present(sentence, "Thank you.")


@pytest.mark.parametrize("sentence, said", [("Yes, I am here and listening.", "Are you here and listening?"),
                                            ("I'm listening, go on.", "Are you listening to me?"),
                                            ("The ready state of the oven is a green light.", "How do I know it's ready?")])
def test_answering_a_question_about_it_may_say_it(sentence, said):
    assert not intelligence.invents_present(sentence, said)


def test_a_chat_reply_that_ends_on_call_loses_only_that_sentence(local, no_skill, monkeypatch):
    _transport(monkeypatch, "Not at all, sir. I am here and ready to listen.")
    msg = intelligence.request("fast", MANUAL, [{"role": "user", "content": "Thank you."}], registry.schemas(), chat_system=PERSONA)
    assert _text(msg).strip() == "Not at all, sir."


def test_not_knowing_what_to_say_is_telling_not_asking_the_pc():
    assert chatturn.plain_talk("I don't know what to say when I call her.")
    assert not chatturn.plain_talk("I don't know how to open the file.")


# ------------------------------------------------------------------------------------------ review fixes (2026-11-13)
PHONE = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
ORDER = ("Open my email and find the message from the landlord about the lease renewal and then draft a reply saying that "
         "I can sign on Friday and also set a reminder for Thursday evening so I do not forget to bring the paperwork")
PLEASE = ("I was thinking that tonight could be a quiet one so please turn the lights in the hall down a bit and put on "
          "something calm while I finish the last of the paperwork for the week and then let me know when it is nine")
STATEMENT = ("I am at the hardware store and they have two kinds of paint, the matte one is cheaper but the guy said the "
             "satin lasts longer in a kitchen, so I am leaning toward the satin one for the walls")


@pytest.mark.parametrize("said", [ORDER, PLEASE])
def test_a_long_order_is_never_overheard(said, monkeypatch):
    assert len(said.split()) >= chatturn.OVERHEARD_WORDS
    assert chatturn.orders(said) or chatturn.asks_to_act(said)
    assert not chatturn.overheard(said, _hist(said))
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    assert chatturn.plan("fast", _hist(said), [], False, None) is None      # the manual's path with the tools runs it


def test_an_enormous_line_with_a_command_word_in_it_is_still_a_briefing_for_someone_else():
    said = LONG + " " + " ".join(["please remember that the section about the colours matters a great deal"] * 8)
    assert len(said.split()) > chatturn.OVERHEARD_ORDER_MAX
    assert chatturn.overheard(said, _hist(said))


def test_a_phone_or_typed_message_is_never_overheard(local, monkeypatch):
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: SCHEMAS)
    assert chatturn.overheard(STATEMENT, _hist(STATEMENT))                     # the same words spoken: judged
    for history in (_hist(PHONE + STATEMENT),
                    _hist(STATEMENT)[:-1] + [{"role": "user", "content": [{"type": "text", "text": PHONE + STATEMENT}]}]):
        assert intelligence.last_user_text(history) == STATEMENT
        assert not chatturn.overheard(intelligence.last_user_text(history), history)
        assert chatturn.note(chatturn.CHAT, history) != chatturn.OVERHEARD_NOTE


def test_a_line_in_another_language_is_not_judged_overheard():
    said = ("je suis au magasin de bricolage et ils ont deux sortes de peinture la mate est moins chere mais le vendeur "
            "dit que la satinee dure plus longtemps dans une cuisine alors je pense prendre la satinee pour les murs")
    assert len(said.split()) >= chatturn.OVERHEARD_WORDS
    assert not chatturn.overheard(said, _hist(said))


@pytest.mark.parametrize("said", ["Do you remember what I told you about the contractor?", "Who is Maya?",
                                  "When is the dentist appointment", "What's the plan for Saturday",
                                  "You remember the book I mentioned?", "Remember what I said about Ava's party"])
def test_a_question_that_looks_something_up_keeps_its_lookup(said):
    assert chatturn.wants_recall(said)


@pytest.mark.parametrize("said", ["Even for creative work?", "Do you get tired?", "Which would you pick?",
                                  "What would you do tonight?", "Is working from home better?"])
def test_a_plain_remark_or_opinion_question_still_loses_its_lookup(said):
    assert not chatturn.wants_recall(said)


def test_the_chat_path_keeps_the_lookup_for_a_who_is_question(local, no_skill, monkeypatch):
    sent = _transport(monkeypatch, "She is your neighbour, sir.")
    intelligence.request("fast", MANUAL, _with_recall("Who is Maya?"), registry.schemas(), chat_system=PERSONA)
    assert "<retrieved-memory>" in str(sent[0]["messages"][-1]["content"])
    assert chatturn.KNOWN_NOTE in str(sent[0]["messages"][-1]["content"])
    sent = _transport(monkeypatch, "Mostly, sir.")
    intelligence.request("fast", MANUAL, _with_recall("Even for creative work?"), registry.schemas(), chat_system=PERSONA)
    assert "<retrieved-memory>" not in str(sent[0]["messages"][-1]["content"])
