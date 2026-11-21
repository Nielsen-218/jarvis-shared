"""Review of chat first (2026-11-12): lines that look like talk but are his answer to Jarvis or a clipped command must not
take the tool-less chat path; a chat reply never claims an action; [ignore] anywhere means silence; an older PC that
does not know the chat profile still answers. Every case has a prior assistant turn and uses the loaded registry."""
import pytest

from core import chatturn, intelligence, pcbridge, warmth
from tools import load_all, registry

load_all()                                               # the real skill list, whatever ran before this file

BRAIN = "qwen3.5:9b"
PERSONA = "PERSONA-CORE You are J.A.R.V.I.S., talking with them out loud."


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_CHAT_FIRST", "JARVIS_CHAT_THINK", "JARVIS_PC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)


def _plan(before, said, first="Morning."):
    history = [{"role": "user", "content": first}, {"role": "assistant", "content": before},
               {"role": "user", "content": said}]
    schemas = intelligence._schemas(intelligence.select_tools(history, registry.schemas()))
    return chatturn.plan("fast", history, schemas, False, None)


def _reply(text, reason="stop"):
    return {"model": BRAIN, "choices": [{"finish_reason": reason, "message": {"content": text}}]}


def _text(msg):
    return " ".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


# ------------------------------------------------------------------------------------ 1. his answer to Jarvis

@pytest.mark.parametrize("asked", ["Shall I set a reminder for you?", "Want me to open the music player?",
                                   "I can look that up for you, sir."])
@pytest.mark.parametrize("said", ["Yes.", "Sure.", "Yep", "Sounds good", "Alright then", "Yeah that would be great",
                                  "Okay, go ahead.", "No.", "Nope, leave it."])
def test_a_short_answer_to_his_question_or_offer_is_not_chat(local, asked, said):
    assert _plan(asked, said) is None


@pytest.mark.parametrize("said", ["Sounds good", "Alright then", "Sure."])
def test_the_same_words_after_a_plain_statement_are_not_an_answer(local, said):
    """Nothing was asked or offered, so they answer nothing. (Whether the turn is then chat still depends on the skills
    selected for it: a short line with any skill selected keeps today's path.)"""
    history = [{"role": "user", "content": "Morning."}, {"role": "assistant", "content": "It rained most of the night."},
               {"role": "user", "content": said}]
    assert not chatturn.command_like(said, history)
    assert chatturn.plan("fast", history, [], False, None) == chatturn.CHAT


# ----------------------------------------------------------------------------------------- 2. clipped commands

CLIPPED = ["Quieter", "Louder", "Be quiet", "Quiet", "Next one", "Dim it", "Lights off", "Brighter", "Hang up", "Back",
           "Again", "Wait", "Hold on", "Not that one"]


@pytest.mark.parametrize("said", CLIPPED)
def test_a_clipped_command_after_an_action_is_not_chat(local, said):
    assert _plan("Playing jazz now, sir.", said, first="Play some jazz.") is None
    assert chatturn.command_like(said, [{"role": "user", "content": "Play some jazz."},
                                        {"role": "assistant", "content": "Playing jazz now, sir."},
                                        {"role": "user", "content": said}])


@pytest.mark.parametrize("said", ["Quieter", "Louder", "Next one", "Lights off", "Hang up", "Not that one"])
def test_a_clipped_command_is_not_chat_after_talk_either(local, said):
    assert _plan("It rained most of the night.", said) is None


@pytest.mark.parametrize("said", ["Ha, fair enough.", "Huh, interesting.", "Thanks."])
def test_a_remark_after_talk_is_still_chat(local, said):
    assert _plan("It rained most of the night.", said) == chatturn.CHAT


def test_the_gateway_does_not_call_a_clipped_command_or_an_answer_plain_talk(local):
    from core import gateway, gateway_context
    for before, said in (("Playing jazz now, sir.", "Quieter"), ("Want me to open the music player?", "Sounds good"),
                         ("I can look that up for you, sir.", "Sure.")):
        history = [{"role": "user", "content": "Morning."}, {"role": "assistant", "content": before}]
        for h in (history, history + [{"role": "user", "content": said}]):
            assert not gateway.plainly_talk(said, gateway_context.build(said, h), h), (before, said)


# ------------------------------------------------------------------------------- 3. a chat reply claims no action

CLAIMS = ["Opening it now.", "Your reminder is set.", "Timer's set for ten minutes.", "Playing jazz now, sir.",
          "Turning it down now, sir.", "Consider it done.", "Yes, I'll do that."]


@pytest.mark.parametrize("claim", CLAIMS)
def test_a_chat_reply_never_claims_or_promises_an_action(claim):
    assert intelligence.chat_unsaid(claim, "", acts=True)
    with pytest.raises(intelligence.LocalFailure):
        intelligence.parse_chat_reply(_reply(claim), "all right", kind=chatturn.CHAT)
    kept = intelligence.parse_chat_reply(_reply("Fair point. " + claim), "all right", kind=chatturn.CHAT)
    assert _text(kept) == "Fair point."


def test_an_explanation_may_still_describe_how_things_are_set():
    text = "The resolver keeps the answer for a while. The address is now cached, so the next lookup is instant."
    assert _text(intelligence.parse_chat_reply(_reply(text), "how does it work?", kind=chatturn.DEPTH)) == text


def test_the_stream_drops_the_same_claims_on_a_chat_turn():
    from brain import llm
    heard = []
    live = llm._SpokenAsWritten(heard.append, unchecked="all right")
    live.as_chat(chatturn.CHAT)
    live.feed("Fair point. Opening it now. Your reminder is set. Quite so. ")
    assert heard == ["Fair point."] or heard == ["Fair point.", "Quite so."]
    assert not any("Opening" in s or "reminder" in s for s in heard)


# ----------------------------------------------------------------------------------------------------- 5. [ignore]

@pytest.mark.parametrize("reply", ["[ignore]", "Hmm. [ignore]", "[ignore] That sounds like the television.",
                                   "That was not for me. [IGNORE]"])
def test_ignore_anywhere_is_exactly_ignore(reply):
    assert _text(intelligence.parse_chat_reply(_reply(reply), "and the home side lost", kind=chatturn.CHAT)) == "[ignore]"


@pytest.mark.parametrize("pieces", [["Hmm. ", "[ignore]"], ["Hmm.", " [ig", "nore]"], ["[ignore] That", " sounds like the television. "],
                                    ["Right. ", "[ignore] ", "More words here. "]])
def test_the_stream_says_nothing_when_ignore_follows(pieces):
    from brain import llm
    heard = []
    live = llm._SpokenAsWritten(heard.append, unchecked="and the home side lost")
    live.as_chat(chatturn.CHAT)
    for piece in pieces:
        live.feed(piece)
    assert heard == []


def test_the_stream_still_speaks_the_first_sentence_once_the_next_begins():
    from brain import llm
    heard = []
    live = llm._SpokenAsWritten(heard.append, unchecked="long day")
    live.as_chat(chatturn.CHAT)
    live.feed("It sounds it. ")
    assert heard == []                                   # what follows might be [ignore]
    live.feed("Sit")
    assert heard == ["It sounds it."]


# --------------------------------------------------------------------------------------------------- 6. relay skew

def test_an_older_pc_that_refuses_the_profile_is_asked_again_without_it(local, monkeypatch):
    monkeypatch.setenv("JARVIS_PC_URL", "http://pc.invalid:8765")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    sent = []

    def json_request(path, method="GET", body=None):
        sent.append(body)
        if "profile" in body:
            raise pcbridge.PCError("invalid_request", 400)
        return _reply("Quite.")
    monkeypatch.setattr(pcbridge, "json_request", json_request)
    history = [{"role": "user", "content": "Morning."}, {"role": "assistant", "content": "Good morning."},
               {"role": "user", "content": "I slept badly, honestly."}]
    msg = intelligence.request("fast", "MANUAL", history, registry.schemas(), chat_system=PERSONA)
    assert _text(msg) == "Quite."
    assert len(sent) == 2 and "profile" in sent[0] and "profile" not in sent[1]
    assert sent[1]["tools"] == [] and sent[1]["messages"] == sent[0]["messages"], "still the chat turn, still no tools"


def test_a_fragment_on_an_older_pc_ends_on_the_plain_line(local, monkeypatch):
    monkeypatch.setenv("JARVIS_PC_URL", "http://pc.invalid:8765")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    sent = []

    def json_request(path, method="GET", body=None):
        sent.append(body)
        raise pcbridge.PCError("invalid_request", 400)
    monkeypatch.setattr(pcbridge, "json_request", json_request)
    history = [{"role": "user", "content": "Morning."}, {"role": "assistant", "content": "Good morning."},
               {"role": "user", "content": "velvet lantern"}]
    msg = intelligence.request("fast", "MANUAL", history, registry.schemas(), chat_system=PERSONA,
                               talk_only="I didn't catch that, sir.")
    assert _text(msg) == "I didn't catch that, sir."
    assert all(body["tools"] == [] for body in sent), "never the manual's path with tools"
