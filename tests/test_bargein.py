"""Barge-in that remembers what he actually HEARD (audio/bargein, audio/tts heard-ledger, Brain.cancel / 'go on').

A fake output stream stands in for the speakers (no audio device), and a stub voice returns fixed-length audio."""
import threading
import time

import numpy as np
import pytest

import main
from audio import bargein, echo, tts
from audio.tts import SAMPLE_RATE, Speaker

CHUNK = SAMPLE_RATE // 20              # the play thread's 50 ms chunk
S1 = "A GPU draws a frame in stages."
S2 = "Then the rasterizer turns every triangle into the pixels it covers."
S3 = "That is the whole pipeline, sir."


class FakeStream:
    def __init__(self):
        self.writes, self.on_write = [], None

    def write(self, data):
        self.writes.append(np.array(data).reshape(-1).copy())
        if self.on_write:
            self.on_write(len(self.writes))

    def abort(self):
        pass

    def close(self):
        pass


@pytest.fixture
def speaker(monkeypatch):
    monkeypatch.setattr(tts, "warm_up_kokoro", lambda voice: None)     # no voice model in tests
    stream = FakeStream()

    class Voice(Speaker):
        def _open_output(self):
            return stream

        def _synthesize(self, text):
            return np.full(20 * CHUNK, 1000, np.int16)                  # every sentence: 1 s = 20 chunks

    s = Voice("kokoro:bm_lewis")
    s.stream = stream
    return s


def idle(s, timeout=5.0):
    deadline = time.monotonic() + timeout
    while s.busy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not s.busy()


def test_a_cut_sentence_counts_only_as_far_as_it_played(speaker):
    gate = threading.Event()

    def on_write(n):
        if n == 1:
            gate.wait(5)                                   # hold sentence 1 until all three are queued
        if n == 20 + 9:                                    # 8 of sentence 2's 20 chunks have played: 40 %
            speaker.stop()
    speaker.stream.on_write = on_write
    for sentence in (S1, S2, S3):
        speaker.speak(sentence)
    time.sleep(0.05)
    gate.set()
    idle(speaker)
    assert speaker.spoken == [S1, S2]                      # both STARTED playing (the echo check still knows them)
    cut = bargein.heard_prefix(S2, 0.4)
    assert cut == "Then the rasterizer turns" and speaker.heard == [S1, cut + "…"]
    assert speaker.heard_text() == f"{S1} Then the rasterizer turns…"
    assert "pixels" not in speaker.heard_text()
    assert speaker.unsaid == [S2, S3]                      # the cut sentence whole: "go on" never starts mid-sentence


def test_what_played_ducked_before_a_real_interruption_was_spoken_over_not_heard(speaker):
    """He starts talking 25 % into sentence 2 ('wait, which GPU do I have?'); while that is heard out, sentence 2 plays
    on ducked to its end and sentence 3 begins. It was an interruption: the cut point is where he began talking."""
    gate = threading.Event()

    def on_write(n):
        if n == 1:
            gate.wait(5)
        if n == 20 + 6:                                    # 5 of sentence 2's 20 chunks have played: he cuts in
            speaker.duck(0.3)
        if n == 40 + 4:                                    # sentence 2 finished (ducked), sentence 3 under way
            speaker.stop()
    speaker.stream.on_write = on_write
    for sentence in (S1, S2, S3):
        speaker.speak(sentence)
    time.sleep(0.05)
    gate.set()
    idle(speaker)
    assert speaker.heard == [S1, bargein.heard_prefix(S2, 0.25) + "…"]
    assert speaker.unsaid == [S2, S3]


def test_after_a_backchannel_the_cut_point_is_where_he_was_stopped(speaker):
    """'mhm' (duck, then restore): the ducked chunks were heard; a later stop cuts where the voice was."""
    gate = threading.Event()

    def on_write(n):
        if n == 1:
            gate.wait(5)
        if n == 5:
            speaker.duck(0.3)
        if n == 9:
            speaker.restore()
        if n == 20 + 8:                                    # 7 of sentence 2's chunks played
            speaker.stop()
    speaker.stream.on_write = on_write
    for sentence in (S1, S2, S3):
        speaker.speak(sentence)
    time.sleep(0.05)
    gate.set()
    idle(speaker)
    assert speaker.heard == [S1, bargein.heard_prefix(S2, 7 / 20) + "…"] and speaker.unsaid == [S2, S3]


def test_a_reply_that_ran_out_while_he_was_heard_out_was_heard_in_full(speaker):
    """'Shall I delete it?' 'Yes.' over the last words: the reply ended during the capture, nothing is cut."""
    speaker.stream.on_write = lambda n: speaker.duck(0.3) if n == 15 else None
    speaker.speak(S1)
    idle(speaker)
    speaker.stop()
    assert speaker.heard == [S1] and speaker.unsaid == []


def test_a_sentence_joins_the_heard_list_only_after_its_last_chunk(speaker):
    seen = []
    speaker.stream.on_write = lambda n: seen.append(list(speaker.heard)) if n == 20 else None
    speaker.speak(S1)
    idle(speaker)
    assert seen == [[]] and speaker.heard == [S1]          # writing its last chunk: not yet heard; after: heard


def test_duck_and_restore_change_the_gain_on_the_chunks_being_played(speaker):
    speaker.stream.on_write = lambda n: speaker.restore() if n == 5 else None
    speaker.duck(0.3)
    speaker.speak(S1)
    idle(speaker)
    peaks = [int(np.abs(w).max()) for w in speaker.stream.writes]
    assert peaks[:5] == [300] * 5 and set(peaks[5:]) == {1000}
    assert speaker.heard == [S1]                           # ducked is still heard


def test_the_outcome_of_each_line_outlives_the_reply_for_the_unprompted_ack(speaker):
    """core/notice counts an unprompted line as announced only once it played to its end; a cut one is offered again.
    The record survives the next reply's reset_turn (he may have woken Jarvis right after it)."""
    queued = time.monotonic()
    assert speaker.outcome(S1, queued) is None                   # not played yet
    speaker.speak(S1)
    idle(speaker)
    speaker.reset_turn()
    assert speaker.outcome(S1, queued) == "heard" and speaker.outcome(S1, time.monotonic() + 1) is None
    gate = threading.Event()

    def on_write(n):
        if n == 20 + 1:
            gate.wait(5)
        if n == 20 + 6:
            speaker.stop()                                        # cut a quarter of the way into S2; S3 never started
    speaker.stream.on_write = on_write
    later = time.monotonic()
    speaker.speak(S2)
    speaker.speak(S3)
    time.sleep(0.05)
    gate.set()
    idle(speaker)
    assert speaker.outcome(S2, later) == "cut" and speaker.outcome(S3, later) == "cut"
    assert speaker.outcome(S1, queued) == "heard"


def test_reset_turn_forgets_the_last_reply(speaker):
    speaker.speak(S1)
    idle(speaker)
    speaker.reset_turn()
    assert speaker.spoken == speaker.heard == speaker.unsaid == [] and speaker.first_audio_at is None


@pytest.mark.parametrize("asked, speaking, working, expected", [
    ("Mhm.", True, False, "backchannel"),
    ("Mm-hm.", True, True, "backchannel"),
    ("Yeah.", True, False, "backchannel"),
    ("I see.", True, False, "backchannel"),
    ("Go on.", True, False, "backchannel"),                 # he is already going on
    ("Yeah, stop.", True, False, "interrupt"),
    ("Wait.", True, False, "interrupt"),                    # a stop word is enough on its own
    ("Kanpe.", True, False, "interrupt"),                   # Kreyol stop word (main.STOP_WORDS)
    ("Jarvis.", True, False, "interrupt"),                  # his name is enough on its own
    ("Okay.", False, False, "interrupt"),                   # the reply is over: "okay" is his turn
    ("", True, True, "empty"),                              # a cough, a door
    ("...", True, False, "empty"),
    (S2, True, False, "echo"),                              # his own sentence coming back from the speakers
    ("What's the hold up?", True, True, "status"),
    ("What's the hold up?", False, True, "status"),
    ("Blender.", True, False, "backchannel"),               # one stray word over his voice does not stop him
    ("No, open Blender instead.", True, False, "interrupt"),
    ("Close ChatGPT.", False, False, "interrupt"),
])
def test_classify_interruption(asked, speaking, working, expected):
    assert bargein.classify_interruption(asked, speaking, working, [S1, S2], main.STOP_WORDS) == expected


# His real one-word barge-ins (data/jarvis.log after "(interrupted)": "Yes." x10, "Go.", "No.", "Nah.", "Bah!" x2) and the
# commands audio/echo._COMMANDS lets through: over his voice, with or without a task running, each one is a turn.
@pytest.mark.parametrize("asked", ["Yes.", "Go.", "No.", "No!", "Nope.", "Nah.", "Bah!", "Shush.", "Hush.", "Halt.",
                                   "Silence.", "Abort.", "Don't.", "Hold.", "Non!", "Arrête.", "Kanpe."])
@pytest.mark.parametrize("working", [False, True])
def test_a_one_word_answer_refusal_or_command_over_his_voice_is_a_turn(asked, working):
    assert bargein.classify_interruption(asked, True, working, [S1, S2], main.STOP_WORDS) == "interrupt"


@pytest.mark.parametrize("asked", ["Wi wi.", "Anhan.", "Dakò.", "Oui oui.", "Ouais.", "D'accord."])
def test_kreyol_and_french_listening_noises_are_backchannels(asked):
    assert bargein.classify_interruption(asked, True, False, [S1, S2], main.STOP_WORDS) == "backchannel"


@pytest.mark.parametrize("said", ["Kontinye.", "Continuez.", "Okay, continue."])
def test_kreyol_and_french_resume_words(said):
    assert bargein.asks_to_resume(said)


WORKING_LINE = "Give me a second, sir, I'm working on it."


@pytest.mark.parametrize("asked", ["Okay.", "Good.", "Sure.", "Mhm."])
def test_a_listening_noise_after_his_working_line_keeps_the_task_running(asked):
    """Live log: 'Please wait for a few minutes while it runs. (interrupted) YOU: good' killed the PC task. Once he has
    said something this turn and the task is still running, an acknowledgement is not a new turn."""
    assert bargein.classify_interruption(asked, False, True, [WORKING_LINE], main.STOP_WORDS) == "backchannel"


def test_before_his_first_word_an_okay_is_still_a_turn():
    assert bargein.classify_interruption("Okay.", False, True, [], main.STOP_WORDS) == "interrupt"


def test_a_real_request_after_his_working_line_still_stops_the_task():
    assert bargein.classify_interruption("No, open Notepad instead.", False, True, [WORKING_LINE],
                                         main.STOP_WORDS) == "interrupt"


@pytest.mark.parametrize("asked, stop", [("Stop!", True), ("No.", True), ("Jarvis.", True), ("Arrête.", True),
                                         ("Yes.", False), ("Open Blender.", False), ("Mhm.", False)])
def test_stop_words_and_his_name_always_get_through(asked, stop):
    """The opt-in voice gate (JARVIS_BARGE_IN_VOICE) never holds these back."""
    assert bargein.is_stop(asked, main.STOP_WORDS) is stop


@pytest.mark.parametrize("said, resume", [
    ("Go on.", True), ("Continue.", True), ("Okay, go on.", True), ("Sorry, go ahead.", True),
    ("What were you saying?", True), ("You were saying?", True), ("Carry on, Jarvis.", True), ("Keep going.", True),
    ("Go ahead.", False),                                   # a yes to a question: approvals decide that one
    ("Go on Amazon and find a charger.", False), ("Continue the download.", False), ("Yes.", False), ("", False),
])
def test_asks_to_resume(said, resume):
    assert bargein.asks_to_resume(said) is resume


def test_heard_prefix_keeps_whole_words_only():
    assert bargein.heard_prefix("Open the downloads folder.", 1.0) == "Open the downloads folder."
    assert bargein.heard_prefix("Open the downloads folder.", 0.0) == ""
    assert bargein.heard_prefix("Open the downloads folder.", 0.5) == "Open the"     # "downl" was not heard whole
    assert bargein.heard_prefix("Open the downloads folder.", 0.05) == ""


# -- the brain keeps only what he heard --------------------------------------------------------------------------------
REPLY = f"{S1} {S2} {S3}"
HEARD = f"{S1} Then the rasterizer turns…"
UNSAID = f"{S2} {S3}"                                  # the cut sentence whole, then the rest (Speaker.stop)


@pytest.fixture
def brain(monkeypatch):
    from brain.llm import Brain
    from core import pcfast, quick
    b = Brain("x", "low", "sys")
    b.messages = [{"role": "user", "content": "Explain how a GPU renders a frame."},
                  {"role": "assistant", "content": REPLY}]
    b._open_turn = 1                                        # the turn that wrote that reply
    b._last_turn = time.monotonic()
    b.front_doors = []
    monkeypatch.setattr(quick, "handle", lambda text: b.front_doors.append(("quick", text)))
    monkeypatch.setattr(pcfast, "handle", lambda text, history, *a, **k: b.front_doors.append(("pcfast", text)))
    monkeypatch.setattr(Brain, "_run", lambda self, emit, start: b.front_doors.append(("model", start)))
    return b


def test_cutting_into_a_finished_reply_leaves_only_what_he_heard(brain):
    brain.cancel(HEARD, UNSAID)
    note = brain.messages[-1]
    assert note["role"] == "assistant" and note["content"].startswith(HEARD)
    assert "pixels" not in note["content"] and "whole pipeline" not in note["content"]
    assert "cut me off" in note["content"] and len(brain.messages) == 2
    brain.cancel(HEARD, UNSAID)                             # once per turn
    assert len(brain.messages) == 2 and brain.messages[-1] == note


def test_a_reply_still_being_written_is_closed_by_its_own_turn(brain):
    brain.messages = brain.messages[:1]                     # the model was still streaming: no reply yet
    with brain._lock:                                       # the worker holds the lock
        brain.cancel(HEARD, UNSAID)
        assert len(brain.messages) == 1                     # not touched from the other thread
        brain._close_interrupted_turn()                     # what ask() does when it sees the flag
    assert brain.messages[-1] == {"role": "assistant",
                                  "content": f"{HEARD} [the user cut me off about here and did not hear the rest]"}


def test_a_stop_after_the_turn_finished_leaves_the_delivered_reply(brain):
    """The phone's stop (core/appserver: brain.cancel('') after a round trip to the PC) can arrive just after the turn
    finished and its reply was delivered: the reply stays in the history as written, now and on the next turn."""
    before = [dict(m) for m in brain.messages]
    brain.cancel("")
    assert brain.messages == before
    brain.ask("Something else.", [].append)
    assert brain.messages[:2] == before


def test_an_interrupted_quick_answer_never_rewrites_an_older_reply(brain):
    brain._open_turn = None                                 # this turn was a quick / PC answer: nothing in history
    before = [dict(m) for m in brain.messages]
    brain.cancel("Opened Bl…", "ender.")
    assert brain.messages == before


def test_go_on_picks_up_where_he_was_cut_off_and_is_recorded(brain):
    brain.cancel(HEARD, UNSAID)
    said = []
    brain.ask("Go on.", said.append)
    assert said[0] == "As I was saying, sir, then the rasterizer turns every triangle into the pixels it covers."
    assert said[1:] == [S3]
    assert brain.front_doors == []                          # answered before quick / PC / gateway
    assert brain.messages[-2:] == [{"role": "user", "content": "Go on."},
                                   {"role": "assistant", "content": " ".join(said)}]
    said.clear()
    brain.ask("Go on.", said.append)                        # the remainder was used up: the usual path
    assert brain.front_doors[:2] == [("quick", "Go on."), ("pcfast", "Go on.")]


def test_go_on_without_a_cut_off_reply_is_left_to_the_rest_of_jarvis(brain):
    brain.ask("Go on.", [].append)
    assert brain.front_doors[0] == ("quick", "Go on.")


def test_a_cut_off_reply_is_only_picked_up_for_ten_minutes(brain, monkeypatch):
    from brain import llm
    brain.cancel(HEARD, UNSAID)
    brain._unsaid = (brain._unsaid[0], time.monotonic() - llm.UNSAID_FOR - 1)
    brain.ask("Continue.", [].append)
    assert brain.front_doors[0] == ("quick", "Continue.")


def _pcfast_says(monkeypatch, brain, replies):
    """pcfast answers these sentences itself (a gateway plan, a scan asking to continue); others reach the brain."""
    from core import pcfast

    def handle(text, history, *a, **k):
        brain.front_doors.append(("pcfast", text))
        return replies.get(text)
    monkeypatch.setattr(pcfast, "handle", handle)


def test_go_on_after_a_later_question_answers_that_question(brain, monkeypatch):
    """Cut off, then 'close notepad and open blender' -> 'Want me to go ahead?'. His 'go on' is the answer to that plan:
    it reaches pcfast / approvals, and the old explanation is not replayed (nor later)."""
    _pcfast_says(monkeypatch, brain, {"Close Notepad and open Blender.": "I'll close Notepad, then open Blender. "
                                                                         "Want me to go ahead?"})
    brain.cancel(HEARD, UNSAID)
    brain.ask("Close Notepad and open Blender.", [].append)
    said = []
    brain.ask("Go on.", said.append)
    assert not any("As I was saying" in s for s in said)
    assert ("pcfast", "Go on.") in brain.front_doors
    said.clear()
    brain.ask("What were you saying?", said.append)
    assert not any("As I was saying" in s for s in said)


@pytest.mark.parametrize("answer", ["Continue.", "Go on."])
def test_a_waiting_question_takes_continue_even_when_it_did_not_end_in_a_question_mark(brain, answer):
    """A later turn asked for a yes ('Say yes and I'll do both.', live log): 'continue' answers it."""
    from core import approvals
    brain.cancel(HEARD, UNSAID)
    time.sleep(0.05)                                        # time.monotonic() ticks every ~16 ms on Windows
    approvals.request(brain.uid, "uninstall:proton", "uninstall Proton Drive and Proton Mail")
    said = []
    brain.ask(answer, said.append)
    assert not any("As I was saying" in s for s in said) and brain.front_doors[0] == ("quick", answer)


def test_a_question_inside_a_later_reply_also_ends_the_old_remainder(brain, monkeypatch):
    _pcfast_says(monkeypatch, brain, {"Close Paint.": "Should I close Paint? It still has an unsaved picture."})
    brain.cancel(HEARD, UNSAID)
    brain.ask("Close Paint.", [].append)
    said = []
    brain.ask("Go on.", said.append)
    assert not any("As I was saying" in s for s in said)


def test_a_pc_plan_shown_for_review_after_the_cut_takes_go_on(brain):
    """The plan waits in the PC ledger (approvals.meant treats it as the waiting question): 'go on' is his answer."""
    from core import pcledger
    brain.cancel(HEARD, UNSAID)
    time.sleep(0.05)
    pcledger.create("plan", {"task": "close notepad and open blender", "plan": {"steps": []}})
    said = []
    brain.ask("Go on.", said.append)
    assert not any("As I was saying" in s for s in said)


def test_a_newer_cut_with_nothing_unsaid_replaces_the_older_remainder(brain):
    """Cut off; a side question is answered; that answer is cut before his first word: 'keep going' then refers to the
    newer cut, which left nothing to pick up, so the old explanation is not replayed."""
    brain.cancel(HEARD, UNSAID)
    brain.ask("What's the weather?", [].append)
    brain.cancel("", "")
    said = []
    brain.ask("Keep going.", said.append)
    assert not any("As I was saying" in s for s in said) and ("quick", "Keep going.") in brain.front_doors


def test_a_side_question_then_go_on_still_picks_up_the_explanation(brain):
    """The point of the remainder: 'wait, which GPU do I have?' answered, then 'okay, go on'."""
    brain.cancel(HEARD, UNSAID)
    brain.ask("Wait, which GPU do I have?", [].append)
    said = []
    brain.ask("Okay, go on.", said.append)
    assert said and said[0].startswith("As I was saying, sir, then the rasterizer")


def test_go_on_never_replays_the_working_line_or_a_promise_of_work_the_cut_stopped(brain):
    """Cut while the turn was still working (the worker holds the lock: computer.abort, the tools not run): the WORKING
    filler and 'I'll uninstall Discord now.' are not said again as if the work were going on."""
    rest = "Give me a second, sir, I'm working on it. I'll uninstall Discord now. That frees about two gigabytes."
    brain.messages = brain.messages[:1]
    with brain._lock:
        brain.cancel("", rest)
        brain._close_interrupted_turn()
    said = []
    brain.ask("Go on.", said.append)
    assert " ".join(said) == "As I was saying, sir, that frees about two gigabytes."


def test_go_on_after_only_a_working_line_was_cut_is_left_to_the_rest_of_jarvis(brain):
    with brain._lock:
        brain.cancel("", "One moment, sir, putting that together now.")
        brain._close_interrupted_turn()
    brain.ask("Go on.", [].append)
    assert brain.front_doors[0] == ("quick", "Go on.")


def test_go_on_replays_words_and_never_approves_a_waiting_action(brain):
    from core import approvals
    approvals.request("owner", "delete:old-trailer.mp4", "delete old-trailer.mp4")
    brain.cancel("Shall I delete old…", "Shall I delete old-trailer.mp4?")
    said = []
    brain.ask("Go on.", said.append)
    assert said == ["As I was saying, sir, shall I delete old-trailer.mp4?"]
    assert approvals.pending("owner")["approved"] is False  # still waiting for a real yes to the whole question


# -- the voice loop: duck, then decide ------------------------------------------------------------------------------------
def _respond(heard_over_him: str, playing: bool = True, busy_after: bool | None = None, heard: str = HEARD,
             unsaid: str = UNSAID, brain_obj=None, voiceid=None, transcribe_error: Exception | None = None, on=None,
             seen: list | None = None):
    """main.respond with a fake mic / voice / Whisper: something is heard over the reply, then the reply ends.

    playing: his voice was playing when it was heard; busy_after: still playing once it was transcribed (default: same).
    brain_obj: the Brain the turn runs on (default: a stub recording cancel calls)."""
    import ast
    import types
    from pathlib import Path
    from unittest.mock import Mock
    from audio import endturn

    loop = next(n for n in ast.parse(Path("main.py").read_text(encoding="utf-8")).body
                if isinstance(n, ast.FunctionDef) and n.name == "voice_loop")
    respond = next(n for n in loop.body if isinstance(n, ast.FunctionDef) and n.name == "respond")
    speaker = Mock(spoken=[S1, S2], first_audio_at=time.monotonic() if playing else None)
    states = [playing, playing if busy_after is None else busy_after]
    speaker.busy.side_effect = lambda: states.pop(0) if len(states) > 1 else states[0]
    speaker.heard_text.return_value = heard
    speaker.unsaid_text.return_value = unsaid
    if seen is not None:
        seen.append(speaker)
    listener = Mock()
    listener.watch_for_interruption.side_effect = [[np.zeros(1280, np.int16)], None]
    listener.record_command.return_value = np.zeros(16000, np.int16)
    stt = Mock()
    stt.transcribe.return_value = heard_over_him
    if transcribe_error is not None:
        stt.transcribe.side_effect = transcribe_error
    cancelled = []
    stub = types.SimpleNamespace(cancel=lambda *a: cancelled.append(a))
    brain = types.SimpleNamespace(brain_for=lambda uid: brain_obj if brain_obj is not None else stub)

    class Thread:
        def __init__(self, target, **kw):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False
    scope = dict(speaker=speaker, listener=listener, status=Mock(), stt=stt, brain=brain, speaking={"uid": "owner"},
                 threading=types.SimpleNamespace(Thread=Thread), log_timing=Mock(), hud=Mock(), progress=Mock(),
                 say=Mock(), bargein=bargein, echo=echo, endturn=endturn, STOP_WORDS=main.STOP_WORDS, cut_in_kind=main.cut_in_kind,
                 voice=types.SimpleNamespace(voiceid=voiceid or types.SimpleNamespace(enrolled=False)))
    exec(compile(ast.Module(body=[respond], type_ignores=[]), "main.py", "exec"), scope)
    kw = {"on": on} if on is not None else {}
    return scope["respond"]("Explain how a GPU renders a frame.", task=lambda: None, **kw), speaker, cancelled


def test_mhm_over_his_reply_dips_his_voice_and_he_carries_on():
    result, speaker, cancelled = _respond("Mhm.")
    assert result is None and cancelled == []
    speaker.duck.assert_called_once()
    speaker.restore.assert_called_once()
    speaker.stop.assert_not_called()


def test_a_cough_over_his_reply_no_longer_stops_him():
    result, speaker, cancelled = _respond("")
    assert result is None and cancelled == [] and not speaker.stop.called


def test_stop_over_his_reply_stops_him_and_the_brain_hears_only_what_was_heard():
    result, speaker, cancelled = _respond("Stop.")
    assert result is not None
    speaker.stop.assert_called_once()
    assert cancelled == [(HEARD, UNSAID)]


def test_a_one_word_answer_as_his_reply_ends_is_a_turn():
    """'Shall I go ahead?' 'Yes.' said over his last word: once nothing is playing it is his turn, not a stray word."""
    result, speaker, cancelled = _respond("Yes.", playing=False)
    assert result is not None and cancelled


def test_a_listening_noise_said_as_his_reply_ends_is_a_turn():
    """Heard while he was still talking, but the reply finished during the capture: the re-check makes it a turn
    ('Shall I?' 'Mhm.'), where over his voice it would have been dropped."""
    result, speaker, cancelled = _respond("Mhm.", playing=True, busy_after=False)
    assert result is not None
    over_him, *_ = _respond("Mhm.", playing=True, busy_after=True)
    assert over_him is None


def test_an_answer_over_his_last_word_leaves_the_question_he_heard_in_full(brain):
    """'Shall I delete the old trailer?' 'Yes.' said over the last word, the question ends during the capture: a real
    Brain through respond keeps the question as he heard it, with no 'did not hear the rest' note."""
    question = "Shall I delete the old trailer?"
    brain.messages = [{"role": "user", "content": "Clean up my videos."}, {"role": "assistant", "content": question}]
    before = [dict(m) for m in brain.messages]
    result, speaker, _ = _respond("Yes.", playing=True, busy_after=False, heard=question, unsaid="", brain_obj=brain)
    assert result is not None
    assert brain.messages == before
    said = []
    brain.ask("Something else.", said.append)               # and the next turn does not rewrite it either
    assert brain.messages[:2] == before


def test_a_failure_while_hearing_him_out_never_leaves_his_voice_ducked():
    """Whisper failing on the interruption (a CUDA error) reaches the voice loop's 'Sorry, something went wrong': the rest
    of the reply and that apology must not play at 30 % volume."""
    seen = []
    with pytest.raises(RuntimeError):
        _respond("", transcribe_error=RuntimeError("CUDA error"), seen=seen)
    seen[0].duck.assert_called_once()
    seen[0].restore.assert_called_once()


def test_the_voice_gate_never_holds_back_a_stop(monkeypatch):
    """JARVIS_BARGE_IN_VOICE=1: a short 'Stop!' over his ducked voice may not match the owner's voiceprint; it stops him
    anyway (and with him the running task). Two words from someone else are still held back."""
    import types
    monkeypatch.setenv("JARVIS_BARGE_IN_VOICE", "1")
    stranger = types.SimpleNamespace(enrolled=True, identify=lambda audio: (None, 0.2))
    result, speaker, cancelled = _respond("Stop!", voiceid=stranger)
    assert result is not None and cancelled == [(HEARD, UNSAID)]
    result, speaker, cancelled = _respond("Open Blender instead.", voiceid=stranger)
    assert result is None and cancelled == []


def test_an_interrupted_translation_is_cancelled_on_the_brain_doing_it():
    """Interpreter mode translates on the owner's brain, whoever is speaking: the cut goes to that brain, never to the
    speaker's own conversation."""
    import types
    on_owner = []
    owner = types.SimpleNamespace(cancel=lambda *a: on_owner.append(a))
    result, speaker, cancelled = _respond("Stop.", on=owner)
    assert on_owner == [(HEARD, UNSAID)] and cancelled == []


def test_interrupting_a_translation_never_rewrites_the_last_reply(brain, monkeypatch):
    """Interpreter mode speaks through the same brain without writing history: cutting into it leaves the earlier,
    fully heard reply alone."""
    from types import SimpleNamespace
    from core import intelligence
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    monkeypatch.setattr(intelligence, "request", lambda *a, **k: SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Bonjou, kijan ou ye?")]))
    before = [dict(m) for m in brain.messages]
    brain.translate("Hello, how are you?", "English", "Haitian Creole", [].append)
    brain.cancel("Bonjou…", "kijan ou ye?")
    assert brain.messages == before
    said = []
    brain.ask("Go on.", said.append)                        # and a later "go on" never speaks the translation
    assert not any("kijan" in s for s in said)


def test_a_mhm_between_two_of_his_sentences_still_counts_as_over_his_voice():
    """Between sentences nothing is playing for a moment, but his reply is not over: 'mhm' there is a backchannel."""
    result, speaker, cancelled = _respond("Mhm.")
    assert speaker.is_playing.called is False and result is None and cancelled == []


def test_before_his_first_word_a_word_is_a_turn():
    """The reply is still being worked out (nothing played yet): 'okay' is his turn, as the design table says."""
    result, speaker, cancelled = _respond("Okay.", playing=False)
    assert result is not None


@pytest.mark.parametrize("asked, spoken, speaking, working, expected", [
    ("Yeah. Understood, sir.", ["Understood, sir."], True, False, "backchannel"),   # the owner's part is a listening noise
    ("Go live. Understood, sir.", ["Understood, sir."], True, False, "interrupt"),
    ("No. Understood, sir.", ["Understood, sir."], True, False, "interrupt"),
    ("Open Blender. As I said, sir.", ["As I said, sir: it is open."], True, False, "interrupt"),  # was all echo
    ("Go ahead. As I said, sir.", ["As I said, sir: want me to go ahead?"], True, False, "echo"),  # never his approval
])
def test_his_words_mixed_into_a_cut_in_are_judged_without_them(asked, spoken, speaking, working, expected):
    assert bargein.classify_interruption(asked, speaking, working, spoken, main.STOP_WORDS) == expected


def test_an_ack_from_before_this_reply_is_taken_out_but_never_eats_his_answer():
    # data/jarvis.log 2026-11-16: "Yes, sir?" -> "How much RAM is free?" -> a cut-in "Yes, sir." (voice match 0.18)
    # before any of the new reply had played: `spoken` was already empty, so it stopped him.
    assert bargein.classify_interruption("Yes, sir.", False, True, [], main.STOP_WORDS, ["Yes, sir?"]) == "echo"
    assert bargein.classify_interruption("Yes.", True, True, ["Shall I close it?"], main.STOP_WORDS,
                                         ["Yes, sir?"]) == "interrupt"       # his one-word answer to this reply


def test_recently_played_keeps_what_played_just_before_this_reply(speaker):
    speaker.speak("Yes, sir?")
    idle(speaker)
    speaker.reset_turn()                                 # the next reply begins: `spoken` forgets the ack
    assert speaker.spoken == []
    assert echo.context(speaker) == ([], ["Yes, sir?"])
    speaker._played[-1][1] -= 30.0                        # it stopped playing half a minute ago
    assert echo.context(speaker) == ([], [])


def test_the_voice_loop_strips_his_words_from_a_heard_line(capsys):
    assert echo.without_mine("Go live. Understood, sir.", ["Understood, sir."]) == "Go live."
    assert "removed my own words" in capsys.readouterr().out
    assert echo.without_mine("Go live.", ["Understood, sir."]) == "Go live."
    assert capsys.readouterr().out == ""                  # nothing removed: nothing logged
    assert echo.without_mine("Yes, sir.", ["Yes, sir?"]) == ""
    assert "ignored my own voice" in capsys.readouterr().out
    assert echo.without_mine("Go live. Understood, sir.", []) == "Go live. Understood, sir."
    # right after his reply (not over it) only "sir" runs go: an answer that reuses his question's words stays whole
    question = ["Is it the model you have open, or a new one I should create from scratch?"]
    assert echo.without_mine("Yes, sir. The new one, please.", question + ["Yes, sir?"],
                             long_runs=False) == "The new one, please."
    answer = "A new one I should create from scratch. With long arms and a big hat."
    assert echo.without_mine(answer, question, long_runs=False) == answer
    assert echo.without_mine(answer, question) == "With long arms and a big hat."        # over his voice: his run


def test_echo_context_works_with_a_plain_speaker():
    class Plain:
        spoken = ["Understood, sir."]
    assert echo.context(Plain()) == (["Understood, sir."], [])
    assert echo.context(object()) == ([], [])
