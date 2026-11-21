"""Conversation wave 4a (2026-11-14): his background stays out of lines it does not bear on, on the path the app really
takes. The brain attaches the <background> block to the conversation's FIRST USER MESSAGE (a list of text parts); the
step that leaves his facts out of a plain remark only looked at the system message, so it never did anything live
(probe, qwen3.5:9b: 13 of 33 replies named his projects, one claimed to have scanned his project files)."""
import pytest

from core import chatturn

FACTS = ("What you remember about this person (the rest is looked up with each question):\n"
         "- The owner is working on a project called 'Ember Vale' involving 3D characters.\n"
         "- The owner's wife's name is Dana.\n"
         "- The owner is training for a half marathon.\n")
BACKGROUND = ("<background>\nCurrent date and time: Saturday 14 November 2026, 17:36.\n" + FACTS +
              "How they want you to behave:\n- Prefers short answers.\n"
              "Your latest conversations with this person (oldest first; pick up open threads naturally, don't recite them):\n"
              "- Sat 14 Nov 10:00: Talked about the marathon plan. Still open: the long run on Sunday.\n"
              "Lessons you've learned from past mistakes (follow them):\n- Never recite his projects when he only says hello.\n"
              "</background>")


def _live(said: str, earlier=()):
    """The messages as the brain stores them: the background on the first user message, as a text part."""
    lines = list(earlier) + [said]
    messages = [{"role": "system", "content": "PERSONA"},
                {"role": "user", "content": [{"type": "text", "text": BACKGROUND}, {"type": "text", "text": lines[0]}]}]
    for n, line in enumerate(lines[1:]):
        messages += [{"role": "assistant", "content": [{"type": "text", "text": f"Reply {n}."}]},
                     {"role": "user", "content": [{"type": "text", "text": line}]}]
    return messages


def _background(messages) -> str:
    return next(part["text"] for m in messages if isinstance(m.get("content"), list)
                for part in m["content"] if part.get("text", "").startswith("<background>"))


def _lean(said, kind=chatturn.CHAT, **kw):
    return chatturn.lean_messages(_live(said, **kw), said, kind)


@pytest.mark.parametrize("said, kind", [("Should I take a break?", chatturn.DEPTH), ("What's going on?", chatturn.CHAT),
                                        ("Why is the sky blue?", chatturn.DEPTH), ("Anything on your mind?", chatturn.CHAT),
                                        ("Tell me something interesting.", chatturn.CHAT),
                                        ("Do you think I'm any good at this?", chatturn.CHAT),
                                        ("I am tired.", chatturn.CHAT), ("Can I ask something?", chatturn.CHAT)])
def test_a_plain_remark_gets_no_facts_on_the_live_layout(said, kind):
    out = _lean(said, kind)
    whole = str(out)
    assert "Ember Vale" not in whole and "Dana" not in whole and "marathon" not in whole and "Still open" not in whole
    text = _background(out)
    assert "Prefers short answers." in text and "Never recite his projects" in text and "Current date and time" in text
    assert text.startswith("<background>") and text.rstrip().endswith("</background>")
    assert out[-1]["content"][-1]["text"] == said and out[0]["content"] == "PERSONA"       # nothing else is touched


def test_a_remark_later_in_the_conversation_is_lean_too():
    out = _lean("Do you get tired?", earlier=("Hello.", "Why is the sky blue?"))
    assert "Ember Vale" not in str(out) and out[-1]["content"][-1]["text"] == "Do you get tired?"
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user", "assistant", "user"]


def test_the_stored_messages_are_not_changed():
    messages = _live("Do you get tired?")
    before = str(messages)
    out = chatturn.lean_messages(messages, "Do you get tired?", chatturn.CHAT)
    assert str(messages) == before and out is not messages


def test_the_system_layout_still_works():
    messages = [{"role": "system", "content": "PERSONA\n\n" + BACKGROUND}, {"role": "user", "content": "Do you get tired?"}]
    out = chatturn.lean_messages(messages, "Do you get tired?", chatturn.CHAT)
    assert "Ember Vale" not in str(out) and "Prefers short answers." in out[0]["content"]


@pytest.mark.parametrize("said", ["What am I working on?", "What's my wife's name?", "How should I plan my week?",
                                  "What do you remember about me?", "Who is Dana?", "Remind me what I called the game.",
                                  # no question word in front, so intelligence.about_them says no (review 2026-11-14)
                                  "Am I married?", "Tell me my wife's name", "Can you say my daughter's name?",
                                  "Quel est mon nom ?", "Is it a good idea to show you to my family?"])
def test_a_line_about_him_or_his_things_keeps_everything_untouched(said):
    messages = _live(said)
    assert chatturn.lean_messages(messages, said, chatturn.CHAT) is messages


def test_a_command_turn_is_left_alone():
    messages = _live("Open Blender.")
    assert chatturn.lean_messages(messages, "Open Blender.", "manual") is messages


def test_no_background_anywhere_changes_nothing():
    messages = [{"role": "system", "content": "PERSONA"}, {"role": "user", "content": [{"type": "text", "text": "Hi."}]}]
    assert chatturn.lean_messages(messages, "Hi.", chatturn.CHAT) is messages
    assert chatturn.lean_messages([], "Hi.", chatturn.CHAT) == []


def test_only_the_first_block_is_taken_and_other_parts_are_kept():
    messages = _live("Do you get tired?")
    messages[1]["content"].insert(1, {"type": "text", "text": "<said-unprompted>\nA timer went off.\n</said-unprompted>"})
    out = chatturn.lean_messages(messages, "Do you get tired?", chatturn.CHAT)
    assert [p["text"][:16] for p in out[1]["content"]] == ["<background>\nCur", "<said-unprompted", "Do you get tired"]
