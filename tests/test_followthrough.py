"""Follow-through on his orders (live 2026-11-20 23:46-00:30, data/jarvis.log).

A. "And Streaming Protocol." went to the model ("Got it, sir.") and nothing ran: the protocols matcher did not know the
   spoken "And", nor an order said as one sentence of several ("Hey Jarvis. I'm sorry Seth. Streaming Protocol.").
B. His repeat of an order that was not carried out is owned and done (core/followthrough.handle).
C. His message cut into fragments (each cut off the reply to the one before, unheard) is answered once (followthrough.carry).
"""
import time
from datetime import datetime

import pytest

from core import followthrough as F
from core import intelligence
from core import protocols as P
from core import quick, thread


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(kind):
        def run(step, ctx):
            seen.append((kind, dict(step)))
            return P.DONE, f"did {kind}"
        return run
    monkeypatch.setattr(P, "ACTIONS", {k: fake(k) for k in P.ACTIONS})
    from core import acquire, pcbridge
    monkeypatch.setattr(acquire, "running_game", lambda: "")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)       # the PC, where his protocols run (the laptop: its own tests)
    return seen


# -- A: the matcher --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["And Streaming Protocol.", "And streaming protocol.", "streaming protocol",
                                  "Start the streaming protocol.", "run streaming protocol", "Also the streaming protocol, please.",
                                  "Hey Jarvis. I'm sorry Seth. Streaming Protocol.", "I said streaming protocol.",
                                  "And streaming protocol. That's what I was telling you. Understood, sir. Yeah, whatever, man. "
                                  "You're not here for shit. You can shut the fuck up. That's what you can do. All right. Let's see."])
def test_his_streaming_orders_run_the_streaming_protocol(calls, said):
    reply = quick.handle(said)
    assert reply and reply.startswith("Streaming protocol:"), (said, reply)
    assert [k for k, _ in calls][0] == "free_up"


@pytest.mark.parametrize("said", ["And no streaming protocol.", "Streaming protocol. No, wait.", "Gaming protocol. Actually, don't.",
                                  "What does the streaming protocol do? Gaming protocol.", "Gaming protocol. Streaming protocol.",
                                  "I ran the streaming protocol yesterday.", "And the streaming protocol was great.",
                                  "Streaming protocol? What is that.", "And um streaming protocol."])
def test_what_is_not_one_order_runs_nothing(calls, said):
    reply = quick.handle(said)
    assert not calls, (said, reply)


@pytest.mark.parametrize("said", ["Streaming. Protocol.", "And streaming. Protocol."])
def test_a_name_whisper_cut_off_from_protocol_is_rejoined(calls, said):
    assert quick.handle(said).startswith("Streaming protocol:")


def test_a_cut_off_name_that_is_not_his_runs_nothing(calls):
    assert quick.handle("Network. Protocol.") is None and not calls


def test_a_retraction_before_the_order_does_not_stop_it(calls):
    assert quick.handle("No, not gaming. Streaming protocol.").startswith("Streaming protocol:")


def test_early_line_is_said_before_a_pc_protocol_runs(calls, monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    said = []
    with P.early_say(said.append):
        reply = quick.handle("And streaming protocol.")
    assert said == ["Starting the streaming protocol, sir."], said
    assert reply.startswith("Streaming protocol:")


def test_no_early_line_without_a_live_voice(calls, monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    assert quick.handle("streaming protocol").startswith("Streaming protocol:")


def test_the_voice_brain_says_the_early_line_then_the_result_once(calls, monkeypatch):
    """brain/llm.py: the early line reaches the speaker while the steps run, and history keeps both (no second copy)."""
    from types import SimpleNamespace

    from anthropic.types import TextBlock

    from brain import llm
    from core import pcbridge, pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(intelligence, "request", lambda *a, **k: SimpleNamespace(
        content=[TextBlock(type="text", text="Got it, sir.")], stop_reason="end_turn"))
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    b.context = lambda: ""
    heard = []
    b.ask("And streaming protocol.", heard.append)
    assert heard[0] == "Starting the streaming protocol, sir.", heard
    assert len(heard) == 2 and heard[1].startswith("Streaming protocol:"), heard
    assert b.messages[-1]["content"].startswith("Starting the streaming protocol, sir. Streaming protocol:")


# -- A: the claim filter ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reply", ["Got it, sir.", "Understood, sir: I'm here for the streaming protocol, ready to help however you need.",
                                   "Understood, sir: protocol engaged."])
def test_a_bare_acknowledgment_of_a_protocol_order_is_not_said(reply):
    assert intelligence.bare_on_action(reply, "And Streaming Protocol.")


@pytest.mark.parametrize("said", ["I love the gaming protocol.", "What is a network protocol?", "The streaming protocol was great.",
                                  "Open Notepad."])
def test_talk_about_a_protocol_is_not_an_order(said):
    assert not intelligence.bare_on_action("Glad to hear it, sir.", said)


def test_an_honest_line_or_question_still_passes():
    assert not intelligence.bare_on_action("Do you want me to run it, sir?", "And streaming protocol.")
    assert not intelligence.bare_on_action("I don't have a house party protocol.", "run the house party protocol")


# -- B: the repeat of an order not carried out -----------------------------------------------------------------------
def _row(said, ago, reply="", cut=True, now=None):
    now = time.time() if now is None else now
    thread.append("owner", said, reply, "voice", cut=cut, now=now - ago)


def _ran(name, ago, monkeypatch):
    at = datetime.fromtimestamp(time.time() - ago).isoformat(timespec="seconds")
    monkeypatch.setattr(P, "last_run", lambda n, ran=False: {"name": name, "started": at} if n == name else None)


def test_the_live_sequence_asks_end_or_run_because_it_was_already_on(calls, monkeypatch):
    """23:48 live: the streaming protocol had run 2 minutes before and he was live, and Whisper had heard his "end" as
    "and" six times (23:43-23:45). Neither guess is made: one question with words that can't be misheard."""
    _ran("streaming", 160, monkeypatch)
    _row("start streaming protocol.", 140)
    _row("And Streaming Protocol.", 14)
    reply = quick.handle("And streaming protocol. That's what I was telling you. You're not here for shit.")
    assert reply.startswith("Sorry, sir, I may have misheard: did you say end the streaming protocol?"), reply
    assert "'stop the streaming protocol'" in reply and "'run the streaming protocol'" in reply
    assert not calls


def test_his_angry_repeat_owns_the_miss_and_runs_it(calls, monkeypatch):
    """'And Streaming Protocol.' (nothing ran), then the angry repeat; the protocol was not on (last run long ago)."""
    _ran("streaming", 3 * 3600, monkeypatch)
    _row("And Streaming Protocol.", 14, reply="Got it, sir.", cut=False)
    _row("Yes, sir.", 13, cut=True)
    reply = quick.handle("Streaming protocol. That's what I was telling you. You're not here for shit.")
    assert reply.startswith("Sorry, sir: I didn't start the streaming protocol when you asked. Streaming protocol:"), reply
    assert calls and calls[0][0] == "free_up"


def test_and_the_stream_while_live_asks_instead_of_reading_the_status(monkeypatch):
    from core import obsstream, pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(obsstream, "still_live", lambda: True)
    for said in ("And the stream.", "And stream.", "Hey Jarvis. And the stream."):
        assert quick.handle(said).startswith("You're live, sir. Did you mean end the stream?"), said
    monkeypatch.setattr(obsstream, "still_live", lambda: False)
    assert F.handle("And the stream.") is None
    monkeypatch.setattr(obsstream, "still_live", lambda: True)
    assert F.handle("And the stream was great.") is None


def test_stop_the_stream_is_the_unmistakable_answer():
    from core import obsstream
    assert obsstream.kind("Stop the stream.") == "end"


def test_frustration_alone_retries_the_order_that_did_not_run(calls, monkeypatch):
    monkeypatch.setattr(P, "last_run", lambda n, ran=False: None)
    _row("And Streaming Protocol.", 20, reply="Got it, sir.", cut=False)
    said = []
    with P.early_say(said.append):
        reply = quick.handle("That's what I was telling you!")
    assert said == ["Sorry, sir: I didn't start the streaming protocol when you asked.", "Starting the streaming protocol, sir."], said
    assert reply.startswith("Streaming protocol:")


def test_an_order_that_did_run_is_not_apologised_for(calls, monkeypatch):
    _ran("streaming", 30, monkeypatch)
    _row("start streaming protocol.", 10)
    reply = quick.handle("streaming protocol")
    assert reply.startswith("Streaming protocol:"), reply      # just runs again, as he asked; no "Sorry"


def test_frustration_with_nothing_owed_is_left_to_the_conversation(calls, monkeypatch):
    monkeypatch.setattr(P, "last_run", lambda n, ran=False: None)
    _row("What's the weather?", 20, reply="Sunny, sir.", cut=False)
    assert F.handle("You're not listening to me.") is None
    _row("And Streaming Protocol.", 60 * 30, reply="Got it, sir.", cut=False)   # too long ago
    assert F.handle("That's what I was telling you.") is None
    assert not calls


@pytest.mark.parametrize("said", ["I said I'm tired.", "I told you my name yesterday", "how are you", "It didn't rain today."])
def test_ordinary_talk_is_not_frustration(said):
    assert not F.frustrated(said)
    assert F.handle(said) is None


def test_a_running_protocol_is_not_started_again_with_an_apology(calls, monkeypatch):
    monkeypatch.setattr(P, "last_run", lambda n, ran=False: None)
    _row("And Streaming Protocol.", 20)
    monkeypatch.setattr(P, "_running", {("owner", "streaming")})
    assert F.handle("streaming protocol") is None              # busy: protocols answers "already running"


# -- C: his fragments become one turn --------------------------------------------------------------------------------
def test_his_bedtime_message_is_one_turn():
    said = F.carry("Be a partner, not in AI.", "Make it happen. Use whatever means necessary.", heard_any=False, age=2.0)
    said = F.carry(said, "I'm going to bed, so be automatic on everything. If you need anything from me, say it now.", False, 38.0 - 20)
    assert said.startswith("Be a partner, not in AI. Make it happen.") and said.endswith("say it now.")


@pytest.mark.parametrize("previous", ["Open Discord.", "Pause the music.", "Hey Jarvis, start streaming protocol.",
                                      "And Streaming Protocol.", "delete the file", "So turn the volume down"])
def test_an_order_that_may_have_run_is_never_sent_again(previous):
    assert F.carry(previous, "and Spotify", heard_any=False, age=2.0) == "and Spotify"


def test_no_merge_when_he_heard_the_reply_or_waited():
    assert F.carry("Be a partner.", "Thanks.", heard_any=True, age=1.0) == "Thanks."
    assert F.carry("Be a partner.", "Thanks.", heard_any=False, age=F.CARRY_S + 1) == "Thanks."
    assert F.carry("", "Thanks.", heard_any=False, age=1.0) == "Thanks."


# -- review 2026-11-21 (independent review of 8ee65354) --------------------------------------------------------------
def _owed():
    """An owed order: 'And Streaming Protocol.' just before, answered 'Got it, sir.', nothing run since."""
    _row("And Streaming Protocol.", 20, reply="Got it, sir.", cut=False)


@pytest.mark.parametrize("said", ["I said hi to Maya", "I said it was fine", "nothing happened at school today",
                                  "how many times has Seth called", "what did I just say to her", "I already said that",
                                  "You didn't do it, Seth", "you never listen to the radio"])
def test_1_ordinary_lines_are_not_frustration_even_with_an_order_owed(calls, monkeypatch, said):
    monkeypatch.setattr(P, "last_run", lambda n, ran=False: None)
    _owed()
    assert not F.frustrated(said), said
    assert F.handle(said) is None and not calls, said


@pytest.mark.parametrize("said", ["That's what I was telling you.", "You didn't do it, Jarvis.", "Did you hear me?",
                                  "I told you to do it. Come on, man.", "Nothing happened.", "Do it."])
def test_1_his_whole_line_frustration_still_counts(said):
    assert F.frustrated(said), said


def test_2_a_cancel_or_another_request_in_between_means_nothing_is_owed(calls, monkeypatch):
    monkeypatch.setattr(P, "last_run", lambda n, ran=False: None)
    _row("Streaming protocol.", 60, reply="Got it, sir.", cut=False)
    _row("No wait, cancel that, don't.", 40, reply="Okay, I won't.", cut=False)
    _row("What's the weather?", 20, reply="Sunny, sir.", cut=False)
    assert F.handle("That's what I was telling you.") is None
    assert not calls


def test_3_the_retry_sends_the_order_itself_not_his_frustration(calls, monkeypatch):
    monkeypatch.setattr(P, "last_run", lambda n, ran=False: None)
    _owed()
    sent = []
    monkeypatch.setattr(P, "start", lambda proto, raw: sent.append(raw) or "Streaming protocol: all 7 steps done.")
    reply = F.handle("That's what I was telling you.")
    assert sent == ["run the streaming protocol"], sent
    assert "Starting it now" not in reply and reply.endswith("Streaming protocol: all 7 steps done.")


def test_3_8_the_laptop_never_retries_and_says_honestly_why(calls, monkeypatch):
    from core import pcbridge, pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(P, "last_run", lambda n, ran=False: None)
    asked = []
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: asked.append(raw) or "Streaming protocol: all 7 steps done.")
    _owed()
    reply = F.handle("That's what I was telling you.")
    assert reply == ("Sorry, sir: I can't tell from here whether the PC ran the streaming protocol. "
                     "Say 'run the streaming protocol' and the PC will run it.")
    assert F.handle("Streaming protocol.") is None, "his own new order goes to the PC as before, once"
    assert asked == [] and not calls


@pytest.mark.parametrize("said", ["Gaming protocol. Why did you do that?", "Gaming protocol. What do you think of it.",
                                  "Streaming protocol. Please don't.", "Streaming protocol. Or not.", "Streaming protocol. Never.",
                                  "Streaming protocol. Hold.", "Streaming protocol. Hmm.", "Streaming protocol. Um.",
                                  "Streaming protocol. Wait."])
def test_4_an_order_followed_by_anything_but_filler_runs_nothing(calls, said):
    reply = quick.handle(said)
    assert not calls, (said, reply)


def test_4_the_live_tail_after_his_order_is_filler():
    tail = ("That's what I was telling you. Understood, sir. Yeah, whatever, man. You're not here for shit. "
            "You can shut the fuck up. That's what you can do. All right. Let's see.")
    assert P.run_in_sentences("Streaming protocol. " + tail)["name"] == "streaming"


@pytest.mark.parametrize("said", ["Streaming protocol. No, wait.", "Run the house party protocol. Never mind.",
                                  "Gaming protocol. Why did you do that?"])
def test_5_an_acknowledgment_of_a_retracted_order_is_fine(said):
    assert not intelligence.bare_on_action("Okay, sir.", said), said
    assert intelligence.bare_on_action("Okay, sir.", "Streaming protocol.")


def test_6_the_voice_loop_carries_only_a_plain_turn_onto_a_plain_turn():
    now = 100.0
    plain = F.keep_fragment("Be a partner, not in AI.", True, heard_any=False, route="respond", items=[], taken=now - 2)
    words = F.turn_words(plain, "Make it happen.", from_cut=True, route="respond", items=[], now=now)
    assert words == "Be a partner, not in AI. Make it happen."
    assert F.keep_fragment("x", False, heard_any=False, route="respond", items=[], taken=now) is None, "not cut: nothing kept"
    assert F.turn_words(plain, "Make it happen.", from_cut=False, route="respond", items=[], now=now) == "Make it happen."
    assert F.turn_words(plain, "No.", from_cut=True, route="decline", items=[], now=now) == "No."
    approval = [{"kind": "approval"}]
    yes = F.keep_fragment("Yes.", True, heard_any=False, route="respond", items=approval, taken=now - 1)
    assert F.turn_words(yes, "Sure, go ahead.", from_cut=True, route="respond", items=[], now=now) == "Sure, go ahead."
    assert F.turn_words(plain, "Sure, go ahead.", from_cut=True, route="respond", items=approval, now=now) == "Sure, go ahead."
    declined = F.keep_fragment("Be a partner.", True, heard_any=False, route="decline", items=[], taken=now - 1)
    assert F.turn_words(declined, "Make it happen.", from_cut=True, route="respond", items=[], now=now) == "Make it happen."
    asked = F.keep_fragment("What time is it?", True, heard_any=False, route="respond", items=[], taken=now - 1)
    assert F.turn_words(asked, "Seth, put that down.", from_cut=True, route="respond", items=[], now=now) == "Seth, put that down."
    assert F.turn_words(asked, "Hey Maya, come here.", from_cut=True, route="respond", items=[], now=now) == "Hey Maya, come here."
    assert F.turn_words(asked, "Okay, in Springfield.", from_cut=True, route="respond", items=[], now=now) == "What time is it? Okay, in Springfield."


def test_6_main_voice_loop_uses_the_carry_path():
    """main.voice_loop itself: the fragment kept after respond() and the words carried go through turn_words/keep_fragment,
    and the kept fragment is cleared at the start of every turn (so a stop / dismiss in between drops it)."""
    import ast
    import inspect
    import textwrap

    import main
    src = inspect.getsource(main.voice_loop)
    tree = ast.parse(textwrap.dedent(src))
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert {"turn_words", "keep_fragment"} <= called
    assert "previous, fragment = fragment, None" in src
    assert src.index("previous, fragment = fragment, None") < src.index("route = pre_brain_route(norm, items)")


def test_7_a_refusal_is_an_answer_and_nothing_is_owed(calls, monkeypatch):
    monkeypatch.setattr(P, "last_run", lambda n, ran=False: None)
    _row("And Streaming Protocol.", 20, reply="Protocols that control the PC run for the PC's owner only.", cut=False)
    assert F.handle("That's what I was telling you.") is None
    assert F.handle("Streaming protocol.") is None
    _row("Gaming protocol.", 10, reply="Gaming protocol: 4 of 5 steps done.", cut=False)
    assert F.handle("You didn't do it, Jarvis.") is None
    assert not calls
