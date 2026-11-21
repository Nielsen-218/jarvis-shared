"""Local/cloud tool calls must be grounded in what the user actually said."""
import json

import pytest

import tools  # noqa: F401  (registers the tools, incl. open_app)
from core import intelligence
from core.intelligence import LocalFailure, last_user_text, parse_reply


def _reply(name, args):
    return {"model": "local", "choices": [{"finish_reason": "tool_calls", "message": {
        "content": None,
        "tool_calls": [{"id": "c1", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


@pytest.mark.parametrize("said", ["haha", "hey charvis", "No, hey Jarvis.", "repair my phone"])
def test_open_app_without_a_launch_verb_is_rejected(said):
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply("open_app", {"app": said}), {"open_app"}, said=said)


@pytest.mark.parametrize("said", ["open spotify", "Jarvis, launch Blender please", "can you start notepad",
                                  "fire up steam"])
def test_open_app_with_a_launch_verb_is_kept(said):
    out = parse_reply(_reply("open_app", {"app": "spotify"}), {"open_app"}, said=said)
    assert out.stop_reason == "tool_use" and out.content[0].name == "open_app"


@pytest.mark.parametrize("said, kept", [
    ("I think I want to repaint the garage door dark blue this weekend.", False),
    ("plan my route to Costco and Aldi", True), ("I need to do groceries later", True),
])
def test_shopping_route_needs_shopping_words(said, kept):
    call = _reply("shopping_route", {"stores": ["Costco", "Aldi"]})
    if kept:
        assert parse_reply(call, {"shopping_route"}, said=said).content[0].name == "shopping_route"
    else:
        with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
            parse_reply(call, {"shopping_route"}, said=said)


def test_grounding_ignores_context_jarvis_attached_itself():
    turn = [{"role": "user", "content": [
        {"type": "text", "text": "<background>\nShopping list: paint, Costco run\n</background>"},
        {"type": "text", "text": "<daily-awareness>\n12:01 You: open spotify\n</daily-awareness>"},
        {"type": "text", "text": "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
                                 "I want to repaint the garage door"}]}]
    assert last_user_text(turn) == "I want to repaint the garage door"
    with pytest.raises(LocalFailure, match="ungrounded_tool_call"):
        parse_reply(_reply("shopping_route", {"stores": ["Costco"]}), {"shopping_route"}, said=last_user_text(turn))


def test_grounding_is_skipped_when_caller_gives_no_words():
    out = parse_reply(_reply("open_app", {"app": "spotify"}), {"open_app"})  # old call shape still works
    assert out.content[0].name == "open_app"


def test_last_user_text_skips_tool_results_and_reads_both_formats():
    anthropic_history = [
        {"role": "user", "content": "open spotify"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "open_app", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]},
    ]
    assert last_user_text(anthropic_history) == "open spotify"
    openai_messages = [{"role": "system", "content": "sys"},
                       {"role": "user", "content": [{"type": "text", "text": "haha"}]}]
    assert last_user_text(openai_messages) == "haha"
    assert last_user_text([]) == ""


def test_grounding_table_only_covers_known_tools():
    from tools import registry
    assert set(intelligence._GROUNDING) <= set(registry.names())
