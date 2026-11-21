"""Chat first (2026-11-12): a conversational turn takes the short persona, no tools, conversation sampling, more of the
conversation and a reply budget that fits the question; everything else keeps the manual and the tools. A fake transport
stands in for the model (intelligence.complete_local), so nothing here calls a real one."""
import pytest

from core import chatturn, contextfit, intelligence, warmth
from tools import registry

BRAIN = "qwen3.5:9b"
MANUAL = "MANUAL-OPERATIONS " + "Follow the operating rules. " * 40
PERSONA = "PERSONA-CORE You are J.A.R.V.I.S., talking with them out loud."


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_LOCAL_VISION_MODEL",
                "JARVIS_CHAT_FIRST", "JARVIS_CHAT_THINK", "JARVIS_PC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)


@pytest.fixture
def no_skill(monkeypatch):
    """His words point at no skill (what intelligence.select_tools works out from them and from his data; with a test's
    empty data folder it offers set-up skills for almost any line, so the chat tests pin its answer)."""
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])


def _reply(text, reason="stop", **message):
    return {"model": BRAIN, "choices": [{"finish_reason": reason, "message": dict({"content": text}, **message)}]}


def _transport(monkeypatch, answer):
    """Every body sent to the model is recorded; `answer` is the text it replies with (or a callable of the body)."""
    sent = []

    def complete_local(body, cancelled=lambda: False, on_text=None):
        sent.append(body)
        out = answer(body) if callable(answer) else answer
        if isinstance(out, Exception):
            raise out
        if on_text and isinstance(out, str):
            on_text(out + " ")
        return _reply(out) if isinstance(out, str) else out
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    return sent


def _ask(said, earlier=()):
    history = list(earlier) + [{"role": "user", "content": said}]
    return intelligence.request("fast", MANUAL, history, registry.schemas(), chat_system=PERSONA)


def _text(msg):
    return " ".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _system(body):
    first = body["messages"][0]
    return first["content"] if isinstance(first["content"], str) else str(first["content"])


# ------------------------------------------------------------------------------------------------------- the prompt

@pytest.mark.parametrize("said", ["I had a long day, honestly.", "That film last night was better than I expected.",
                                  "Do you ever get bored?"])
def test_a_chat_turn_gets_the_small_prompt_and_no_tools(local, no_skill, monkeypatch, said):
    sent = _transport(monkeypatch, "Quite.")
    msg = _ask(said)
    assert len(sent) == 1, "one model call, no classifier before it"
    body = sent[0]
    assert body["profile"] == "chat" and body["tools"] == []
    assert _system(body).startswith("PERSONA-CORE") and "MANUAL-OPERATIONS" not in _system(body)
    assert _text(msg) == "Quite." and not any(getattr(b, "type", "") == "tool_use" for b in msg.content)


@pytest.mark.parametrize("said", ["Open Notepad.", "Set a timer for ten minutes.", "What's on my shopping list?"])
def test_a_tool_turn_keeps_the_manual_and_its_tools(local, monkeypatch, said):
    sent = _transport(monkeypatch, "Right away.")
    try:
        _ask(said)
    except intelligence.LocalFailure:
        pass                                             # how the manual's path judges "Right away." is not this test
    assert sent and all("profile" not in body for body in sent)
    assert "MANUAL-OPERATIONS" in _system(sent[0]) and "PERSONA-CORE" not in _system(sent[0])


def test_the_switch_restores_the_manual_for_every_turn(local, monkeypatch):
    monkeypatch.setenv("JARVIS_CHAT_FIRST", "off")
    sent = _transport(monkeypatch, "Quiet enough.")
    _ask("I had a long day, honestly.")
    assert sent and "profile" not in sent[0] and "MANUAL-OPERATIONS" in _system(sent[0])


def test_the_chat_prefix_is_the_same_bytes_from_turn_to_turn(local, no_skill, monkeypatch):
    sent = _transport(monkeypatch, "Quite.")
    first = [{"role": "user", "content": "Morning."}, {"role": "assistant", "content": "Good morning."}]
    _ask("I slept badly.", first)
    _ask("Too much coffee, probably.", first + [{"role": "user", "content": "I slept badly."},
                                                {"role": "assistant", "content": "Quite."}])
    a, b = sent[0]["messages"], sent[1]["messages"]
    assert a[0] == b[0] and a[1:3] == b[1:3], "the persona and the earlier turns do not move: the prompt cache holds"
    native = intelligence._same_prompt_start({"model": BRAIN, "chat": True, "messages": a})
    assert "tools" not in native and native["messages"] == a, "no placeholder tool and no note on a chat turn"


# ----------------------------------------------------------------------------------------------- sampling and depth

def test_chat_turns_are_sampled_like_conversation_and_others_stay_strict(local):
    chat = dict({"model": BRAIN, "messages": [{"role": "user", "content": "hi"}], "temperature": 0.2, "max_tokens": 1536},
                **intelligence.PROFILES["chat"])
    options = intelligence._ollama_body(chat)["options"]
    assert (options["temperature"], options["top_p"], options["top_k"]) == (0.7, 0.8, 20) and options["presence_penalty"] > 0
    strict = intelligence._ollama_body({"model": BRAIN, "messages": [{"role": "user", "content": "hi"}],
                                        "temperature": 0.2, "max_tokens": 1536})["options"]
    assert strict["temperature"] == 0.2 and not {"top_p", "top_k", "presence_penalty"} & set(strict)


@pytest.mark.parametrize("said", ["Why is the sky blue?", "How does a VPN actually work?",
                                  "What's the difference between RAM and storage?", "What should I do about burnout?"])
def test_a_depth_question_gets_the_larger_budget(local, no_skill, monkeypatch, said):
    sent = _transport(monkeypatch, "It scatters.")
    _ask(said)
    assert sent[0]["profile"] == "depth" and sent[0]["tools"] == []
    assert intelligence.PROFILES["depth"]["max_tokens"] >= 2 * intelligence.PROFILES["chat"]["max_tokens"] >= 400
    # 2026-11-21 (owner: "shorter, better answers"): a why / how he did not ask to have explained in full is answered in
    # about fifty words, the rest on "tell me more" (core.speech.spoken_budget); "Explain..." keeps DEPTH_NOTE
    assert chatturn.SHORT_DEPTH_NOTE in str(sent[0]["messages"][-1]["content"]), "told, for this request only, the answer first"


@pytest.mark.parametrize("said", ["How are you?", "Thanks.", "How much is that?", "Long day."])
def test_a_remark_is_not_a_depth_question(said):
    assert not chatturn.depth(said)


def test_thinking_is_only_for_the_profile_that_asks_and_never_reaches_the_reply(local, monkeypatch):
    monkeypatch.setattr(intelligence, "thinks", lambda name: True)
    base = {"model": BRAIN, "messages": [{"role": "user", "content": "why?"}], "temperature": 0.2, "max_tokens": 900}
    assert intelligence._ollama_body(dict(base))["think"] is False
    assert intelligence._ollama_body(dict(base, **intelligence.PROFILES["chat"]))["think"] is False
    assert intelligence._ollama_body(dict(base, **intelligence.PROFILES["think"]))["think"] is True
    msg = intelligence.parse_chat_reply(_reply("<think>He asks why. Consider.</think>Because the air scatters blue light."))
    assert _text(msg) == "Because the air scatters blue light."
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_chat_reply(_reply("<think>never closed, so none of this is an answer"))


def test_a_profile_never_travels_with_tools_or_a_schema(local):
    messages = [{"role": "system", "content": PERSONA}, {"role": "user", "content": "hello"}]
    tool = {"type": "function", "function": {"name": "open_app", "description": "x", "parameters": {"type": "object"}}}
    for payload in ({"role": "fast", "messages": messages, "tools": [tool], "profile": "chat"},
                    {"role": "fast", "messages": messages, "tools": [], "profile": "made-up"},
                    {"role": "fast", "messages": messages, "tools": [], "profile": "chat",
                     "response_format": {"type": "json_schema", "json_schema": {}}}):
        with pytest.raises(intelligence.LocalFailure, match="invalid_request"):
            intelligence.complete_local(payload)


# ------------------------------------------------------------------------------------------------- what is said

LONG = ("It is mostly a matter of scattering. Sunlight looks white but is every colour at once. The air scatters the short "
        "blue waves far more than the long red ones. So blue light reaches your eye from every part of the sky. At sunset "
        "the light crosses much more air. Most of the blue is scattered away before it reaches you. That leaves the reds.")


def test_a_long_answer_is_kept_whole(local, no_skill, monkeypatch):
    _transport(monkeypatch, LONG)
    assert _text(_ask("Why is the sky blue?")) == LONG
    cut = intelligence.parse_chat_reply(_reply(LONG + " And at night the", reason="length"))
    assert _text(cut) == LONG, "an answer the limit cut ends on its last whole sentence"


def test_the_polisher_does_not_shorten_a_long_chat_answer():
    from core import speech
    out = []
    voice = speech.Polisher(out.append, "", asked="Why is the sky blue?", chat=lambda: True)
    for sentence in LONG.split(". "):
        voice(sentence.rstrip(".") + ".")
    voice.finish()
    assert " ".join(out).split() == LONG.split()


@pytest.mark.parametrize("reply", [
    '{"name": "open_app", "arguments": {"name": "notepad"}}',
    '<tool_call>{"name": "open_app", "arguments": {"name": "notepad"}}</tool_call>',
])
def test_a_chat_reply_written_as_a_call_is_refused(reply):
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_chat_reply(_reply(reply))


def test_a_chat_turn_can_never_return_a_pc_action(local, no_skill, monkeypatch):
    call = {"id": "c1", "type": "function", "function": {"name": "open_app", "arguments": '{"name": "notepad"}'}}

    def answer(body):
        if body.get("profile"):
            return _reply("", tool_calls=[call])
        return "I can't tell what you'd like there."
    sent = _transport(monkeypatch, answer)
    with pytest.raises(intelligence.LocalFailure, match="chat_tool_call"):
        intelligence.parse_chat_reply(_reply("", tool_calls=[call]))
    try:
        msg = _ask("I had a long day, honestly.")
    except intelligence.LocalFailure:
        msg = None
    assert sent[0]["profile"] == "chat" and sent[0]["tools"] == []
    assert msg is None or not any(getattr(b, "type", "") == "tool_use" for b in msg.content)


def test_machinery_and_false_claims_are_not_said_but_an_explanation_is():
    name = sorted(n for n in registry.names() if "_" in n)[0]
    assert intelligence.chat_unsaid(f"I can use {name} for that.")
    assert intelligence.chat_unsaid("I've saved that for you.")
    assert intelligence.chat_unsaid("<what-you-know> says you like tea.")
    assert not intelligence.chat_unsaid("The address is now cached, so the next lookup is instant.")
    assert not intelligence.chat_unsaid("Setting a regular bedtime is the part that actually works.")


# --------------------------------------------------------------------------------- short lines, fragments and noise

def _brain(monkeypatch):
    from brain import llm
    from core import pcfast, quick
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    brain = llm.Brain("claude-opus-5-5", "medium", MANUAL, uid="owner")
    brain.chat_system = PERSONA
    return brain


EARLIER = [("That was the whole article.", "No thanks, I'm good."), ("", "Well, that is not ideal."),
           ("", "Did you pick up anything new?"), ("", "Curious pick."), ("", "Fair enough.")]   # synthetic lines


@pytest.mark.parametrize("before, said", EARLIER)
def test_a_short_thing_people_say_gets_a_reply_not_a_quote_back(local, no_skill, monkeypatch, before, said):
    sent = _transport(monkeypatch, "As you wish.")
    brain = _brain(monkeypatch)
    if before:
        brain.messages += [{"role": "user", "content": "Tell me about the article."}, {"role": "assistant", "content": before}]
    heard = []
    brain.ask(said, heard.append)
    spoken = " ".join(heard)
    assert "As you wish." in spoken and "not sure what you meant" not in spoken.lower()
    assert sent and all(body.get("profile") == "chat" and body["tools"] == [] for body in sent)


@pytest.mark.parametrize("fragment", ["quiet pencil", "frozen pencil"])
def test_a_fragment_is_only_ever_talked_about(local, monkeypatch, fragment):
    """A stray two-word phrase once opened an app. It now reaches the chat path only: never a tool, never the manual."""
    sent = _transport(monkeypatch, "I didn't catch that.")
    brain = _brain(monkeypatch)
    heard = []
    brain.ask(fragment, heard.append)
    assert " ".join(heard) == "I didn't catch that."
    assert len(sent) == 1 and sent[0]["profile"] == "chat" and sent[0]["tools"] == []


def test_a_fragment_the_chat_path_cannot_answer_never_reaches_the_tools(local, monkeypatch):
    sent = _transport(monkeypatch, lambda body: intelligence.LocalFailure("timeout"))
    brain = _brain(monkeypatch)
    heard = []
    brain.ask("quiet pencil", heard.append)
    assert len(sent) == 1 and sent[0]["tools"] == [], "no second call with the manual and the tools"
    assert " ".join(heard).lower().startswith("i'm not sure what you meant by 'quiet pencil'")


def test_noise_is_still_met_with_silence(local, no_skill, monkeypatch):
    _transport(monkeypatch, "[ignore]")
    brain = _brain(monkeypatch)
    heard = []
    brain.ask("and that is why the home side lost the second half", heard.append)
    assert all(s.strip().startswith("[ignore]") for s in heard), "main.py and the app server drop an [ignore] line unsaid"


# ---------------------------------------------------------------------------------------- memory of the conversation

def _talk(n):
    out = []
    for i in range(n):
        out += [{"role": "user", "content": f"Remark number {i}: " + "the garden wants weeding again and so on " * 6},
                {"role": "assistant", "content": f"Reply number {i}: " + "it generally does, and it never thanks you " * 6}]
    return out


def test_a_chat_turn_sees_far_more_of_the_conversation():
    messages = [{"role": "system", "content": PERSONA}] + _talk(45) + [{"role": "user", "content": "And the roses?"}]
    assert contextfit.tokens(messages) > 6000
    chat = contextfit.fit(messages, [], chat=True)
    assert chat == messages, "about 8k tokens of earlier talk are shown whole on a chat turn"
    assert contextfit.CHAT_HISTORY >= 8000
    huge = [{"role": "system", "content": PERSONA}] + _talk(140) + [{"role": "user", "content": "And the roses?"}]
    fitted = contextfit.fit(huge, [], chat=True)
    assert contextfit.tokens(fitted) <= contextfit.CONTEXT - contextfit.CHAT_REPLY_ROOM and fitted[-1] == huge[-1]


def test_what_is_known_about_him_is_worded_as_knowledge():
    system = PERSONA + "\n\n<background>\nYou are talking with: Sam (owner).\nSam drinks tea after noon.\n</background>"
    out = intelligence._as_known([{"role": "system", "content": system}, {"role": "user", "content": "Tea?"}])
    text = out[0]["content"]
    assert "<what-you-know>" in text and "<background>" not in text and "(owner)" not in text
    assert "Sam drinks tea after noon." in text and "Historical context only" not in text


# ------------------------------------------------------------------------------- is it conversation? (real selection)

TALK = ["Long day. Work was nonstop from the minute I walked in.", "Be honest, do you ever get bored waiting around for me?",
        "I only get about an hour each evening.", "He's really into barbecue, so I want to cook for him.",
        "like the ending tho... nah", "Hmm, I'm not convinced."]
DEEP = ["How does DNS actually work?", "Is that the same thing as a proxy?", "And how do I stay consistent?",
        "Why do people find it so hard to keep a habit going?"]
WORK = ["What's the weather like tomorrow?", "What time is it?", "Set a timer for ten minutes.", "Open Notepad.",
        "Add milk to my shopping list.", "Remind me to call the dentist at four.", "Turn it up a bit.",
        "How much battery is left?", "What's on my calendar today?", "Play some jazz.", "Find me a recipe for chili.",
        "Close that.", "Can you look that up?", "What's in my pantry?", "Take a note.",
        "How do I get to the airport from here?", "Translate good morning into French.", "Start a focus session.",
        "What did I eat yesterday?", "Search for cheap flights to Miami."]


def _plan(said):
    history = [{"role": "user", "content": said}]
    schemas = intelligence._schemas(intelligence.select_tools(history, registry.schemas()))
    return chatturn.plan("fast", history, schemas, False, None)


@pytest.mark.parametrize("said", TALK)
def test_talk_is_chat_whatever_the_lexical_recall_selected(local, said):
    assert _plan(said) == chatturn.CHAT


@pytest.mark.parametrize("said", DEEP)
def test_a_real_question_is_a_depth_turn(local, said):
    assert _plan(said) == chatturn.DEPTH


@pytest.mark.parametrize("said", WORK)
def test_a_command_or_a_lookup_never_takes_the_chat_path(local, said):
    assert _plan(said) is None


def test_a_skill_named_by_his_words_is_pointed_at():
    tool = [{"type": "function", "function": {"name": "get_weather"}}]
    assert chatturn.pointed_at("Lovely weather we are having.", tool)
    assert not chatturn.pointed_at("Lovely morning we are having.", tool)
    assert chatturn.pointed_at("Lovely morning. Open the browser.", tool), "not plain talk: any selected skill counts"
    assert not chatturn.pointed_at("Open the browser.", [])
