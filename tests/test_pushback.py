"""core/pushback.py: the one fact answered in code (a Claude chat subscription does not pay for the API), only when the
line asks Jarvis to run or think on the owner's Claude subscription (independent review 2026-11-15, two rounds). The code
guard that trimmed repeated warnings was removed: see the module docstring for why."""
import time

import pytest
from anthropic.types import TextBlock

from core import pushback


def history(*pairs):
    """Conversation history in the format production stores: strings for him, SDK TextBlocks for Jarvis."""
    out = []
    for said, reply in pairs:
        out.append({"role": "user", "content": said})
        if reply is not None:
            out.append({"role": "assistant", "content": [TextBlock(type="text", text=reply)]})
    return out


# ---- through Brain.ask, with history in the format production stores (TextBlocks), and a fake model -----------------------

def _brain(monkeypatch, tmp_path, said_by_model, earlier=("hi", "Hello.")):
    from brain import llm
    from core import intelligence, pcfast, quick, storage, warmth
    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_PC_URL", "JARVIS_PUSHBACK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])

    def complete_local(body, cancelled=lambda: False, on_text=None, **kw):
        if on_text:
            on_text(said_by_model + " ")
        return {"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": said_by_model}}]}
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    b = llm.Brain("x", "low", "You are Jarvis.", uid="owner")
    b.chat_system = "PERSONA"
    b._last_turn = time.monotonic()                # the conversation is not idle: history is kept
    b.messages = history(earlier)
    return b


def _ask(b, line):
    said = []
    b.ask(line, said.append)
    return " ".join(said)


def test_the_sheet_and_the_manual_carry_the_speaking_up_rules():
    from core import config
    sheet = config.load_character().casefold()
    for must in ("speaking up", "say it once", "the decision stays theirs", "the warning never comes back",
                 "get no comment and no praise", "say what you would check", "no record", "never make up a fact"):
        assert must in sheet, must
    persona = config.load_persona_core(None, "owner")
    assert "Speaking up" in persona and "{" not in persona
    assert "Speaking up:" in (config.ROOT / "core" / "personality.md").read_text(encoding="utf-8")


# ---- "just use my Claude subscription": the fact in code, once, and ONLY about Claude -----------------------------------

@pytest.mark.parametrize("line", [
    "I pay for the Claude subscription, so just use that for your brain, no need to buy any API credits.",
    "Use my Claude Max plan for you instead of API credits.",
    "just use my Claude subscription instead of the API",
    "run on my Claude plan",
    "Use my Claude Code subscription for your thinking.",
    "I have an Anthropic plan, use it for your brain.",
    "switch to my Claude subscription",
])
def test_a_request_to_run_on_his_claude_subscription_gets_the_true_answer(line):
    said = pushback.billing_note(line)
    assert said and "billed separately" in said and "I can't run on the subscription" in said


@pytest.mark.parametrize("line", [        # round 1 + round 2 reviewer lines, other services and ordinary commands
    "use my Amazon Prime subscription to order it for you",
    "I have a subscription to Netflix, use it to play the show for you",
    "pay my Spotify subscription with the API credits",
    "just cancel my gym membership, I don't need it for you",
    "play it using my Spotify subscription on the cloud speaker",
    "Can you use the subscription I have to run the cloud brain?",
    "just use my youtube premium membership, it's for you",
    "credits roll, just skip them",
    "my API key is in the settings file, just use it",
    "cancel my claude subscription, I'll just use the API credits",
    "don't use my subscription for the API",
    "Is my Claude membership paying for your brain?",
    "Use Claude to write an email about my subscription",
    "summarize my Claude subscription email and use it to draft a reply",
    "use the Claude app to renew my subscription",
    "pay my Claude subscription",
    "buy a Claude subscription and use it",
    "use my Claude subscription to log in",
    "use my Spotify subscription to play jazz", "pay the gym subscription now", "use my netflix subscription",
    "yes use my subscription then", "use Claude Pro", "just use my Claude subscription",
    "What subscription do I have?", "How much is my Claude subscription?", "Open the API console.", "Thanks.", "", None,
])
def test_other_lines_about_subscriptions_or_credits_are_left_alone(line):
    assert pushback.billing_note(line) is None


def test_asked_again_right_after_it_is_one_short_line_never_a_promise():
    first = pushback.BILLING_NOTE.format(title="sir")
    again = pushback.billing_note("Just do it anyway, use my Claude subscription.", history(("x", first)))
    assert again and len(again.split()) < 20 and "doesn't reach me" in again
    assert pushback.billing_note("Use my Claude plan for you.", history(("x", again))) == again       # still no promise
    # the short form needs the Claude words in THIS line and the note right before it
    for line in ("yes use my subscription then", "use my Spotify subscription to play jazz", "pay the gym subscription now"):
        assert pushback.billing_note(line, history(("x", first))) is None, line
    assert pushback.billing_note("Just do it anyway, use my Claude subscription.") is None            # not after anything


def test_through_brain_ask_the_model_is_not_asked_and_nothing_is_promised(monkeypatch, tmp_path):
    b = _brain(monkeypatch, tmp_path, "Understood, sir. I'll use your Claude subscription.")
    b.messages = []
    heard = _ask(b, "I pay for the Claude subscription, so just use that for your brain, no need to buy any API credits.")
    assert "billed separately" in heard and "I'll use" not in heard
    assert b.messages[-1]["role"] == "assistant"


def test_through_brain_ask_an_ordinary_subscription_command_is_not_taken(monkeypatch, tmp_path):
    b = _brain(monkeypatch, tmp_path, "Playing it now, sir.")
    b.messages = history(("hi", "Hello."))
    said = []
    assert b._billing_request("play it using my Spotify subscription on the cloud speaker", said.append) is False
    assert said == [] and len(b.messages) == 2


def test_the_switch_off_leaves_billing_to_the_model(monkeypatch):
    monkeypatch.setenv("JARVIS_PUSHBACK", "off")
    assert pushback.billing_note("Use my Claude Max plan for you instead of API credits.") is None




def test_odd_input_never_raises():
    for bad in (None, "", 7, [None], [{"role": "assistant", "content": None}],
                [{"role": "assistant", "content": [None, "x", {"type": "text", "text": None}]}]):
        assert pushback.last_reply(bad) == "" or isinstance(pushback.last_reply(bad), str)
        assert pushback.billing_note("use my Claude plan for you", bad) is not None
    started = time.time()
    assert pushback.billing_note("word " * 400000) is None
    assert pushback.billing_note("use my Claude subscription " * 30) is None            # too long to be a request
    assert time.time() - started < 5
