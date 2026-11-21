"""Wave 4 combination review (2026-11-11): on the PC the companion's typed chat and the voice share one Brain per person
(plan upgrade 10) and its one turn at a time (Brain._turn_slot). What waits behind another channel's turn must not answer
what that turn asked (a spoken yes said before the typed question), a stop must not wait behind what it stops, and a
developer's verification turn is never part of his conversation. Synthetic sentences only."""
import threading
import time

import pytest

from core import approvals, notice, turnstate
from tests.test_app import pair, real_brain, voice_server
from tests.test_notice import pc  # noqa: F401  (pc: the notice's PC fixture)
from tests.test_notice_one_thread import daytime, offer  # noqa: F401


def test_a_developers_verification_turn_never_answers_the_news(pc, daytime, monkeypatch):
    from fastapi.testclient import TestClient

    from core import appserver
    from tools import voice
    b, _ = real_brain(monkeypatch)
    other, _ = real_brain(monkeypatch)
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    client = TestClient(appserver.AppServer(lambda uid: b, verify_brain=lambda uid: other).app)
    offer(daytime)
    voice.pair_codes["222222"] = "owner"
    token = client.post("/api/pair", json={"code": "222222", "device": "claude-verify-pc"}).json()["token"]
    client.post("/api/chat", json={"text": "not now", "speak": False}, headers={"Authorization": f"Bearer {token}"})
    assert notice._state["offer"] is not None and "suppressed" not in notice._load()


# ------------------------------------------------------------------ one Brain, two channels: what waits, and what a stop reaches
def _held_pc(started, release, typed_text, reply):
    """A PC layer whose `typed_text` request waits for `release` (a typed turn holding the shared Brain)."""
    def pc(text):
        if typed_text in text:
            started.set()
            release.wait(5)
            return reply()
        if "next launch" in text:
            return "The next launch is on Friday. Should I put it on your calendar?"
        return None
    return pc


def test_a_spoken_yes_queued_behind_a_typed_turn_never_approves_the_typed_question(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def ask_to_delete():
        approvals.request("owner", "delete:downloads-old", "delete 40 old files in Downloads")
        return "That deletes 40 old files in Downloads. Shall I go ahead?"
    b, _ = real_brain(monkeypatch, pc=_held_pc(started, release, "delete the old downloads", ask_to_delete))
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    b.ask("when is the next launch", [].append)                 # the voice asks him something
    assert turnstate.last_question("owner")["kind"] == "question"
    typed = threading.Thread(target=lambda: client.post(
        "/api/chat", json={"text": "delete the old downloads", "speak": False}, headers=headers))
    typed.start()
    assert started.wait(5)
    spoken = threading.Thread(target=b.ask, args=("yes", [].append))   # his yes to the voice's question
    spoken.start()
    time.sleep(0.3)
    assert spoken.is_alive(), "the spoken turn waits for the typed one"
    assert approvals.pending("owner") is None, "nothing waited for a yes when he said it"
    release.set()
    typed.join(5)
    spoken.join(5)
    assert approvals.pending("owner")["approved"] is False, "a yes said before the question approved the deletion"


def test_a_yes_said_after_the_typed_question_still_approves_it(monkeypatch):
    started, release = threading.Event(), threading.Event()
    release.set()

    def ask_to_delete():
        approvals.request("owner", "delete:downloads-old", "delete 40 old files in Downloads")
        return "That deletes 40 old files in Downloads. Shall I go ahead?"
    b, _ = real_brain(monkeypatch, pc=_held_pc(started, release, "delete the old downloads", ask_to_delete))
    _, client = voice_server(monkeypatch, b)
    client.post("/api/chat", json={"text": "delete the old downloads", "speak": False}, headers=pair(client))
    b.ask("yes", [].append)
    assert approvals.pending("owner")["approved"] is True


def _ask_to_delete():
    approvals.request("owner", "delete:downloads-old", "delete 40 old files in Downloads")
    return "That deletes 40 old files in Downloads. Shall I go ahead?"


def test_a_spoken_yes_heard_while_a_typed_turn_ran_never_approves_it_though_it_reaches_the_brain_after(monkeypatch):
    """His yes to the voice's question was captured while the typed turn ran, and that turn ended (asking to delete) while
    Whisper still transcribed it: the voice loop hands ask() the count taken when his words came in (main.hear), so the
    yes is answered as of then, not as of when the worker reached ask()."""
    started, release = threading.Event(), threading.Event()
    b, _ = real_brain(monkeypatch, pc=_held_pc(started, release, "delete the old downloads", _ask_to_delete))
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    b.ask("when is the next launch", [].append)                 # the voice asks him something
    typed = threading.Thread(target=lambda: client.post(
        "/api/chat", json={"text": "delete the old downloads", "speak": False}, headers=headers))
    typed.start()
    assert started.wait(5)
    heard = turnstate.exchanges("owner")                        # main.hear: his 'yes' is captured, nothing waits for it
    assert approvals.pending("owner") is None
    release.set()                                               # the typed turn ends while Whisper transcribes the yes
    typed.join(5)
    assert approvals.pending("owner")["approved"] is False
    b.ask("yes", [].append, heard_upto=heard)                   # main.respond -> worker -> ask
    assert approvals.pending("owner")["approved"] is False, "a yes said before the question approved the deletion"


def _voice_loop_fn(name):
    import ast
    from pathlib import Path

    import main
    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    loop = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "voice_loop")
    return loop, next(n for n in loop.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_voice_loop_counts_the_exchanges_when_his_words_are_captured_and_hands_that_to_ask(monkeypatch):
    """main.voice_loop: hear() takes the count as soon as his audio is captured (before Whisper, which can take seconds),
    and respond() passes it to the Brain's ask(); the loop hands what hear() took to respond()."""
    import ast
    from types import SimpleNamespace
    from unittest.mock import Mock

    import numpy as np

    import main
    from audio import endturn
    from core import inputwatch, pcledger
    monkeypatch.setattr(inputwatch, "note_turn", lambda: None)
    monkeypatch.setattr(pcledger, "latency", lambda *a, **k: 0)
    turnstate.note_exchange("owner", [], [], "The next launch is on Friday. Should I put it on your calendar?")
    before = turnstate.exchanges("owner")

    def transcribe(audio):            # a typed turn ends (asking something) while Whisper transcribes his words
        turnstate.note_exchange("owner", [], [], "That deletes 40 old files in Downloads. Shall I go ahead?")
        return "yes"
    asked = []
    runs_on = SimpleNamespace(ask=lambda command, say, heard_upto=None: asked.append((command, heard_upto)))
    speaking = {"uid": "owner"}
    scope = dict(status=Mock(), timing={}, speaking=speaking, np=np, time=time, ECHO_WINDOW_S=main.ECHO_WINDOW_S,
                 listener=SimpleNamespace(record_command=lambda **kw: np.ones(1600, np.int16), last_endpoint={"waited": True},
                                          watch_for_interruption=lambda alive, level: None, discard=lambda s: None),
                 stt=SimpleNamespace(last_language="en", transcribe=transcribe), endturn=endturn,
                 speaker=SimpleNamespace(last_play_end=0.0, spoken=[], reset_turn=lambda: None, busy=lambda: False,
                                          output_level=lambda: 0),
                 voice=SimpleNamespace(manual_speaker=None, voiceid=SimpleNamespace(enrolled=False)),
                 echo=main.echo, hud=Mock(), users=Mock(), sounds_unfinished=lambda text: False,
                 brain=SimpleNamespace(brain_for=lambda uid: runs_on), say=lambda s: None, log_timing=lambda: None,
                 threading=SimpleNamespace(Thread=lambda target, daemon=True: SimpleNamespace(
                     start=target, is_alive=lambda: False)))
    loop, hear = _voice_loop_fn("hear")
    _, respond = _voice_loop_fn("respond")
    exec(compile(ast.Module(body=[hear, respond], type_ignores=[]), "main.py", "exec"), scope)
    assert scope["hear"](6.0) == "yes"
    assert turnstate.exchanges("owner") == before + 1 and speaking["heard_upto"] == before
    scope["respond"]("yes", heard_upto=speaking["heard_upto"])
    assert asked == [("yes", before)]
    calls = [ast.unparse(n) for n in ast.walk(loop) if isinstance(n, ast.Call) and ast.unparse(n.func) == "respond"
             and n.args and ast.unparse(n.args[0]) == "command" and not n.keywords]
    assert calls == [], f"the loop answers his heard words without the count taken when he said them: {calls}"


def _voice_loop(monkeypatch, b, record_command, transcribe, watch=None, speaker=None, threads=None):
    """main.voice_loop's own hear() and respond(), on the Brain `b`, with a fake mic (record_command, watch_for_interruption),
    Whisper (transcribe) and speaker; a cut-in is always classified as an interruption. respond()'s worker runs inside its
    start() unless `threads` (the threading module) is given. Returns (their scope, the loop's `speaking`)."""
    import ast
    from types import SimpleNamespace
    from unittest.mock import Mock

    import numpy as np

    import main
    from audio import endturn
    from core import diag, inputwatch, pcledger
    monkeypatch.setattr(inputwatch, "note_turn", lambda: None)
    monkeypatch.setattr(pcledger, "latency", lambda *a, **k: 0)
    speaking = {"uid": "owner"}
    scope = dict(status=Mock(), timing={}, speaking=speaking, np=np, time=time, ECHO_WINDOW_S=main.ECHO_WINDOW_S,
                 listener=SimpleNamespace(record_command=record_command, last_endpoint={"waited": True},
                                          watch_for_interruption=watch or (lambda alive, level: None),
                                          discard=lambda s: None),
                 stt=SimpleNamespace(last_language="en", transcribe=transcribe), endturn=endturn,
                 speaker=speaker or SimpleNamespace(last_play_end=0.0, spoken=[], reset_turn=lambda: None,
                                                    busy=lambda: False, output_level=lambda: 0),
                 voice=SimpleNamespace(manual_speaker=None, voiceid=SimpleNamespace(enrolled=False)),
                 echo=main.echo, hud=Mock(), users=Mock(), sounds_unfinished=lambda text: False, diag=diag,
                 brain=SimpleNamespace(brain_for=lambda uid: b), say=lambda s: None, log_timing=lambda: None,
                 cut_in_kind=lambda asked, kind: kind, STOP_WORDS=main.STOP_WORDS, progress=Mock(),
                 bargein=SimpleNamespace(classify_interruption=lambda *a: "interrupt", owner_only=lambda: False,
                                         is_stop=lambda *a: False),
                 threading=threads or SimpleNamespace(Thread=lambda target, daemon=True: SimpleNamespace(
                     start=target, is_alive=lambda: False)))
    exec(compile(ast.Module(body=[_voice_loop_fn("hear")[1], _voice_loop_fn("respond")[1]], type_ignores=[]),
                 "main.py", "exec"), scope)
    return scope, speaking


def _still_playing(playing: dict, line: str):
    """The speaker while the voice's reply `line` still plays (until he cuts it off)."""
    from types import SimpleNamespace
    return SimpleNamespace(last_play_end=0.0, spoken=[], reset_turn=lambda: None, output_level=lambda: 0,
                           busy=lambda: playing["on"], first_audio_at=1.0, duck=lambda v: None, restore=lambda: None,
                           stop=lambda: playing.update(on=False), heard_text=lambda: line, unsaid_text=lambda: "")


def _typed_delete(monkeypatch):
    """The voice has asked him a question in words; the companion's typed 'delete the old downloads' is holding the shared
    Brain (it ends, asking to delete, once `release` is set). -> (brain, the typed thread, started, release)"""
    started, release = threading.Event(), threading.Event()
    b, _ = real_brain(monkeypatch, pc=_held_pc(started, release, "delete the old downloads", _ask_to_delete))
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    typed = threading.Thread(target=lambda: client.post(
        "/api/chat", json={"text": "delete the old downloads", "speak": False}, headers=headers))
    return b, typed, started, release


def test_the_voice_loop_counts_what_was_asked_at_his_first_word_not_after_his_end_of_turn_pause(monkeypatch):
    """He says 'yes' to the voice's calendar question while the typed turn runs; it ends, asking to delete, during his
    end-of-turn pause (0.6 s, up to 3 s with Smart Turn). Counted once the mic had waited that out, the deletion was
    'heard' and his yes approved it (review 2026-11-11)."""
    import numpy as np
    b, typed, started, release = _typed_delete(monkeypatch)
    b.ask("when is the next launch", [].append)
    typed.start()
    assert started.wait(5)

    def record_command(on_speech=None, **kw):
        if on_speech is not None:
            on_speech()                     # his first word
        release.set()                       # ...his last word; the typed turn ends while the mic waits out his pause
        typed.join(5)
        assert approvals.pending("owner") is not None
        return np.ones(1600, np.int16)
    scope, speaking = _voice_loop(monkeypatch, b, record_command, lambda audio: "yes")
    assert scope["hear"](6.0) == "yes"
    scope["respond"]("yes", heard_upto=speaking["heard_upto"])
    assert approvals.pending("owner")["approved"] is False, "a yes begun before the question approved the deletion"


def test_a_yes_said_over_the_voices_last_words_is_counted_as_he_began_not_after_the_cut_ins_whisper(monkeypatch):
    """'...Should I put it on your calendar?' still plays (its worker is done, so the typed turn holds the Brain) and he
    says 'Yes.' over its last word. respond() runs Whisper on the cut-in to classify it; the typed turn ends meanwhile,
    asking to delete. Counted only afterwards, in hear(initial=...), the yes approved the deletion (review 2026-11-11)."""
    import numpy as np
    b, typed, started, release = _typed_delete(monkeypatch)
    playing = {"on": True}

    def watch(alive, level):
        if not playing["on"]:
            return None
        typed.start()                       # sent while he listened to the voice's question
        assert started.wait(5) and approvals.pending("owner") is None
        return [np.ones(1600, np.int16)]    # his 'Yes.' over the last word
    whisper = []

    def transcribe(audio):
        whisper.append(audio)
        if len(whisper) == 1:               # the cut-in's own Whisper (classifying it): the typed turn ends meanwhile
            release.set()
            typed.join(5)
        return "Yes."
    scope, speaking = _voice_loop(monkeypatch, b, lambda **kw: np.ones(1600, np.int16), transcribe, watch,
                                  _still_playing(playing, "The next launch is on Friday. Should I put it on your calendar?"))
    interruption = scope["respond"]("when is the next launch")
    assert interruption is not None and approvals.pending("owner") is not None
    assert scope["hear"](6.0, initial=interruption) == "Yes."
    scope["respond"]("Yes.", heard_upto=speaking["heard_upto"])
    assert approvals.pending("owner")["approved"] is False, "a yes begun before the question approved the deletion"


def test_a_no_said_over_the_voices_last_words_never_presses_dont_save_on_a_typed_close(monkeypatch):
    """The same cut-in, and the companion's typed 'close notepad' hits Notepad's save prompt while the cut-in is
    classified: his 'No.' (to the calendar question) pressed Don't save and lost the unsaved text (review 2026-11-11)."""
    import numpy as np

    from core import saveprompt
    typed_in, release, pressed = threading.Event(), threading.Event(), []

    def close(text):
        typed_in.set()
        release.wait(5)
        return saveprompt.remember("owner", "notepad", 0)
    b = _save_prompt_brain(monkeypatch, close, pressed)

    def typed_turn():
        with b.gathered("typed"):
            b.ask([{"type": "text", "text": "[Sent from their Jarvis phone app.] close notepad"}], [].append)
    typed = threading.Thread(target=typed_turn)
    playing = {"on": True}

    def watch(alive, level):
        if not playing["on"]:
            return None
        typed.start()
        assert typed_in.wait(5) and saveprompt.pending("owner") is None
        return [np.ones(1600, np.int16)]
    whisper = []

    def transcribe(audio):
        whisper.append(audio)
        if len(whisper) == 1:
            release.set()
            typed.join(5)
        return "No."
    line = "The rover landed safely. Should I put the next launch on your calendar for Friday?"
    scope, speaking = _voice_loop(monkeypatch, b, lambda **kw: np.ones(1600, np.int16), transcribe, watch,
                                  _still_playing(playing, line))
    interruption = scope["respond"]("read me the article about the rover")
    assert interruption is not None and saveprompt.pending("owner") is not None
    assert scope["hear"](6.0, initial=interruption) == "No."
    scope["respond"]("No.", heard_upto=speaking["heard_upto"])
    assert pressed == [], f"a 'No.' begun before the save question pressed {pressed}"
    assert saveprompt.pending("owner") is not None, "the save question is still his to answer"


def test_a_yes_cutting_in_on_his_own_question_is_stale_though_that_turn_ends_after_his_yes_was_counted(monkeypatch):
    """Voice only. The voice asked to delete (exchange 1); he asked 'how big are they' (his own turn, still working) and cut
    in with 'yes'. That turn, said BEFORE the yes, ends (cut, asking nothing) after the count the yes carries. Taken as
    'after his words', the yes answered exchange 1 and approved the deletion; on main it was stale and got the one re-ask
    (review 2026-11-11). His own earlier turn moved the conversation on, whenever it was recorded."""
    import numpy as np
    started, release = threading.Event(), threading.Event()

    def pc(text):
        if "delete the old downloads" in text:
            return _ask_to_delete()
        if "how big are they" in text:            # his own turn: slow, and he cuts in before it says anything
            started.set()
            release.wait(5)
            return "About 2 GB, sir."
        return None
    b, _ = real_brain(monkeypatch, pc=pc)
    b.ask("delete the old downloads", [].append)
    workers: list = []

    def thread(target, daemon=True):
        workers.append(threading.Thread(target=target, daemon=daemon))
        return workers[-1]

    def watch(alive, level):
        if len(workers) > 1:
            return None
        assert started.wait(5)
        return [np.ones(1600, np.int16)]          # his 'yes' while it still works
    whisper = []

    def transcribe(audio):
        whisper.append(audio)
        if len(whisper) == 2:                     # hear()'s Whisper: his cut turn ends meanwhile
            release.set()
            workers[0].join(5)
        return "yes"
    from types import SimpleNamespace
    scope, speaking = _voice_loop(monkeypatch, b, lambda **kw: np.ones(1600, np.int16), transcribe, watch,
                                  _still_playing({"on": False}, ""), threads=SimpleNamespace(Thread=thread))
    interruption = scope["respond"]("how big are they")
    assert interruption is not None
    assert scope["hear"](6.0, initial=interruption) == "yes"
    assert speaking["heard_upto"] == 1 and turnstate.exchanges("owner") == 2
    scope["respond"]("yes", heard_upto=speaking["heard_upto"])
    workers[-1].join(5)
    assert approvals.pending("owner")["approved"] is False, "a stale yes approved the deletion without the re-ask"


def test_a_turn_of_his_that_asked_something_after_his_words_stays_after_them():
    """core.turnstate._anchor: only his own turns that asked nothing move what his words follow. One that put something
    up after his words came in (a typed turn he sent earlier, or the voice's own) is still not his to answer."""
    question = "The next launch is on Friday. Should I put it on your calendar?"
    turnstate.note_exchange("owner", [], [], question, channel="")
    heard = turnstate.exchanges("owner")
    plan = {"kind": "plan", "id": "plan-voice-2", "question": "delete the 40 old files in Downloads"}
    turnstate.note_exchange("owner", [], [plan], "Here's my plan: delete the 40 old files. Want me to go ahead?", channel="")
    with turnstate.heard_at("owner", heard, ""):
        state, item = turnstate.answering("owner", [plan])
        assert state == "fresh" and "calendar" in item["question"]
        assert turnstate.asked_after_heard("owner", "plan-voice-2")
    turnstate.note_exchange("owner", [plan], [plan], "", channel="typed")     # another channel's turn, asking nothing
    with turnstate.heard_at("owner", heard, ""):
        assert turnstate.answering("owner", [plan])[1]["kind"] == "question"


def test_words_heard_before_an_exchange_never_run_or_decline_what_it_put_up():
    """core.turnstate.heard_at: a PC plan (or 'which one?') put up by an exchange recorded after his words came in is
    neither his yes's (pcfast runs nothing) nor his no's (decline leaves it); the question he heard is the one answered."""
    from core import pcfast
    turnstate.note_exchange("owner", [], [], "The next launch is on Friday. Should I put it on your calendar?")
    heard = turnstate.exchanges("owner")
    plan = {"kind": "plan", "id": "plan-typed-1", "question": "delete the 40 old files in Downloads"}
    turnstate.note_exchange("owner", [], [plan], "Here's my plan: delete the 40 old files. Want me to go ahead?")
    with turnstate.heard_at("owner", heard):
        state, item = turnstate.answering("owner", [plan])
        assert state == "fresh" and item["kind"] == "question" and "calendar" in item["question"]
        assert turnstate.asked_after_heard("owner", "plan-typed-1")
        assert turnstate.decline("owner", "no", [plan]) is None
        assert pcfast._yes_runs("owner") == (None, None)
    assert turnstate.answering("owner", [plan]) == ("fresh", plan)          # heard after it: it is his to answer


def test_a_spoken_no_to_the_voices_approval_declines_it_though_a_typed_turn_re_asked_it_meanwhile(monkeypatch):
    """The voice asked for an approval; a typed turn asked about it again in its own words while his spoken 'no' to it
    waited. He heard it asked before he spoke, so the no is his answer to it: declined, as a queued yes would approve it."""
    started, release = threading.Event(), threading.Event()

    def pc(text):
        if "delete the old downloads" in text:                     # the voice asks for approval
            approvals.request("owner", "delete:downloads-old", "delete the 40 old files in Downloads")
            return "That deletes the 40 old files in Downloads. Shall I go ahead?"
        if "how big are they" in text:                              # the typed turn asks it again, in words
            started.set()
            release.wait(5)
            return "About 2 GB, sir. Shall I delete the 40 old files in Downloads?"
        return None
    b, _ = real_brain(monkeypatch, pc=pc)
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    b.ask("delete the old downloads", [].append)
    assert approvals.pending("owner") and turnstate.answering("owner")[1]["kind"] == "approval"
    typed = threading.Thread(target=lambda: client.post(
        "/api/chat", json={"text": "how big are they", "speak": False}, headers=headers))
    typed.start()
    assert started.wait(5)
    heard: list = []
    spoken = threading.Thread(target=b.ask, args=("no", heard.append))
    spoken.start()
    time.sleep(0.3)
    release.set()
    typed.join(5)
    spoken.join(5)
    assert heard == ["Okay, I won't."] and approvals.pending("owner") is None


def test_an_item_asked_before_his_words_and_re_asked_after_them_is_still_his_to_answer():
    """core.turnstate.asked_after_heard: 'asked after his words' means FIRST asked after them; a later re-ask (in words, or
    reask()) does not make an item he had already heard someone else's."""
    item = {"kind": "plan", "id": "plan-voice-1", "question": "delete the 40 old files in Downloads"}
    turnstate.note_exchange("owner", [], [item], "Here's my plan: delete the 40 old files in Downloads. Shall I go ahead?")
    heard = turnstate.exchanges("owner")
    assert turnstate.note_exchange("owner", [item], [item], "About 2 GB. Shall I delete the 40 old files in Downloads?") == item
    with turnstate.heard_at("owner", heard):
        assert not turnstate.asked_after_heard("owner", "plan-voice-1")
    turnstate.note_exchange("owner", [], [{**item, "id": "plan-typed-2"}], "Here's my plan: delete them. Shall I go ahead?")
    with turnstate.heard_at("owner", heard):
        assert turnstate.asked_after_heard("owner", "plan-typed-2")     # first asked after his words: never his


def _save_prompt_brain(monkeypatch, close, pressed):
    """A Brain on the PC whose PC layer is the real core.pcfast.handle, except 'close notepad' (`close(text)`: the close
    hit Notepad's own save prompt) and the button press itself (`pressed`). The model reads an article and asks a question."""
    from types import SimpleNamespace

    from anthropic.types import TextBlock

    from brain import llm
    from core import gateway, intelligence, pcbridge, pcfast, quick, saveprompt, thread
    real_handle = pcfast.handle
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(thread, "refresh", lambda uid=None: None)
    monkeypatch.setattr(gateway, "decide", lambda *a, **k: None)
    monkeypatch.setattr(saveprompt, "answer", lambda app, save, hwnd=0, **k: pressed.append((app, save)) or
                        f"{'Saved and closed' if save else 'Closed without saving'} {app}.")
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: close(text) if "close notepad" in text else
                        None if "rover" in text else real_handle(text, history, **kw))
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    replies = ["The rover landed safely. Should I put the next launch on your calendar for Friday?"]
    monkeypatch.setattr(intelligence, "request", lambda role, system, history, tools, cancelled, on_text=None, **kw:
                        SimpleNamespace(content=[TextBlock(type="text", text=replies.pop(0) if replies else "Very good, sir.")],
                                        stop_reason="end_turn"))
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.context = lambda: "It is Tuesday."
    return b


@pytest.mark.parametrize("answer", ["no", "yes"])
def test_a_spoken_answer_queued_behind_a_typed_close_never_answers_its_save_prompt(monkeypatch, answer):
    """He answers the voice's question aloud while the companion's typed 'close notepad' runs; that close then asks 'Save it
    before I close it?'. His queued yes/no was said before that question: neither Save nor Don't save is pressed for it."""
    from core import saveprompt
    typed_in, release, pressed = threading.Event(), threading.Event(), []

    def close(text):
        typed_in.set()
        release.wait(5)
        return saveprompt.remember("owner", "notepad", 0)
    b = _save_prompt_brain(monkeypatch, close, pressed)
    b.ask("read me the article about the rover", [].append)
    assert "calendar" in turnstate.last_question("owner")["question"]

    def typed():
        with b.gathered("typed"):
            b.ask([{"type": "text", "text": "[Sent from their Jarvis phone app.] close notepad"}], [].append)
    t = threading.Thread(target=typed)
    t.start()
    assert typed_in.wait(5)
    assert saveprompt.pending("owner") is None, "nothing asked about saving when he answered aloud"
    v = threading.Thread(target=b.ask, args=(answer, [].append))     # his answer to the calendar question
    v.start()
    time.sleep(0.3)
    assert v.is_alive(), "the spoken turn waits for the typed one"
    release.set()
    t.join(5)
    v.join(5)
    assert pressed == [], f"a {answer!r} said before the save question pressed {pressed}"
    assert saveprompt.pending("owner") is not None, "the save question is still his to answer"
    b.ask(answer, [].append)                                          # said after he heard it: it is the answer
    assert pressed == [("notepad", answer == "yes")]


@pytest.mark.parametrize("task_on", ["typed", "voice"])
def test_a_stop_on_the_other_channel_reaches_the_running_pc_task_at_once(monkeypatch, task_on):
    """On the PC a long PC task runs on one channel and he says (or types) 'stop the PC task' on the other: before, the
    stop waited behind the very task it meant to stop and only ran once it was over."""
    from tools import computer
    aborted = []
    monkeypatch.setattr(computer, "abort", lambda: aborted.append(time.monotonic()))
    started = threading.Event()
    holder = {}

    def pc(text):
        if "tidy my downloads" in text:
            started.set()
            b = holder["b"]
            for _ in range(60):
                if b._cancel.is_set():
                    holder["ran"] = "Stopped."
                    return "Stopped."
                time.sleep(0.05)
            holder["ran"] = "Tidied your downloads."
            return "Tidied your downloads."
        return "Stop requested for computer control." if "stop" in text else None
    b, _ = real_brain(monkeypatch, pc=pc)
    holder["b"] = b
    _, client = voice_server(monkeypatch, b)
    headers = pair(client)
    got: dict = {}
    if task_on == "typed":
        task = threading.Thread(target=lambda: got.update(task=client.post(
            "/api/chat", json={"text": "tidy my downloads folder", "speak": False}, headers=headers).json()["reply"]))
    else:
        task = threading.Thread(target=lambda: b.ask("tidy my downloads folder", lambda s: got.update(task=s)))
    task.start()
    assert started.wait(5)
    t0 = time.monotonic()
    if task_on == "typed":
        heard: list[str] = []
        b.ask("stop the PC task", heard.append)                 # main.respond's worker
        stop_reply = " ".join(heard)
    else:
        stop_reply = client.post("/api/chat", json={"text": "stop the PC task", "speak": False}, headers=headers).json()["reply"]
    took = time.monotonic() - t0
    task.join(5)
    assert took < 1.0, f"the stop waited {took:.1f}s behind the task it meant to stop"
    assert "stop" in stop_reply.lower() and aborted and holder["ran"] == "Stopped."
    # the typed turn still reports what the PC did; the spoken one he cut says nothing more
    assert got.get("task") == ("Stopped." if task_on == "typed" else None)


def test_a_spoken_bare_stop_is_the_voice_loops_and_never_reaches_a_typed_task(monkeypatch):
    """Only the explicit 'stop the PC task' crosses channels from the voice: a bare 'stop' is answered by the voice loop."""
    b, _ = real_brain(monkeypatch)
    with b._cut_lock:
        b._running = "typed"
    try:
        assert b._stop_elsewhere("stop", [].append) is False
        assert b._stop_elsewhere("Jarvis, stop the PC task", [].append) is True
    finally:
        with b._cut_lock:
            b._running = None


def test_a_developers_verification_turn_runs_on_a_brain_of_its_own(monkeypatch):
    """The live-verify recipe pairs a 'claude-verify' device and posts /api/chat to the running PC process: its turns
    never enter his voice conversation, and its question never takes his next 'no'."""
    import main
    from core import appserver
    from fastapi.testclient import TestClient
    voice_b, _ = real_brain(monkeypatch, pc=lambda text: "Which file should I open in it?" if "open paint" in text else None)
    made = []

    def verify_brain(uid):
        b, _ = real_brain(monkeypatch, pc=lambda text: "Which file should I open in it?" if "open paint" in text else None)
        made.append(b)
        return b
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    srv = appserver.AppServer(lambda uid: voice_b, verify_brain=verify_brain)
    client = TestClient(srv.app)
    from tools import voice
    voice.pair_codes["333333"] = "owner"
    token = client.post("/api/pair", json={"code": "333333", "device": "claude-verify-pc"}).json()["token"]
    r = client.post("/api/chat", json={"text": "open paint", "speak": False}, headers={"Authorization": f"Bearer {token}"})
    assert r.json()["reply"] == "Which file should I open in it?"
    assert voice_b.messages == [] and voice_b._last_question is None and len(made) == 1 and made[0].messages
    assert main.pre_brain_route("no", turnstate.open_items("owner", voice_b), meaning=lambda n: "dismiss") == "dismiss"
    headers = pair(client)                                     # his own device still talks to the voice's Brain
    client.post("/api/chat", json={"text": "hello", "speak": False}, headers=headers)
    assert voice_b.messages and len(made) == 1


def test_main_gives_verification_devices_their_own_brain():
    import ast
    from pathlib import Path

    import main
    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "AppServer")
    assert call.args[0].id == "brain_for" and [k.arg for k in call.keywords] == ["verify_brain"]
