"""One record of the conversation (upgrade 1, 2026-11-10): a turn answered before the conversation model (a quick answer,
a PC action, the gateway's question or plan) and the lines Jarvis says unprompted are part of the conversation the brain,
the gateway and the journal read. Live 2026-11-08: "Close chat GPT" -> two misses -> "What task were you trying to get me
to do?": none of the PC turns were in Brain.messages. No audio, no model: quick/pcfast and the model are faked."""
import threading
import time
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from brain import llm
from core import asides, contextfit, gateway_context, intelligence, journal, pcfast, quick


@pytest.fixture(autouse=True)
def no_stale_stop():
    """Brain.cancel() also stops PC control (tools.computer.abort): never carried into another test."""
    yield
    try:
        from tools import computer
        computer._abort.clear()
    except Exception:  # noqa: BLE001
        pass


def brain(monkeypatch, pc=None, model_reply="Paint wasn't open, sir.", seen=None):
    """A Brain whose quick answers are off, whose PC layer answers with `pc(text)` and whose model is a fake."""
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: pc(text) if pc else None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        if seen is not None:
            seen.append([dict(m) for m in history])
        return SimpleNamespace(content=[TextBlock(type="text", text=model_reply)], stop_reason="end_turn")
    monkeypatch.setattr(intelligence, "request", request)
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request", "_web_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.context = lambda: "It is Tuesday. The owner likes short answers."
    return b


def test_a_pc_turn_is_kept_in_the_conversation_as_plain_strings(monkeypatch):
    b = brain(monkeypatch, pc=lambda text: "Closed paint." if text == "close paint" else None)
    heard = []
    before = b._last_turn
    b.ask("close paint", heard.append)
    assert heard == ["Closed paint."], "spoken once"
    assert b.messages[-2:] == [{"role": "user", "content": "close paint"}, {"role": "assistant", "content": "Closed paint."}]
    assert b._last_turn > before, "the conversation is alive: no archive on the next message"


def test_the_gateway_sees_the_pc_turn_for_close_it(monkeypatch):
    """2026-11-08: after a PC turn, 'close it' had nothing to refer to: the gateway's RECENT CONVERSATION was empty."""
    from core import pcstate
    monkeypatch.setattr(pcstate, "snapshot", lambda limit=12: {"foreground": None, "windows": [], "running": []})
    monkeypatch.setattr(pcstate, "describe", lambda snap=None: "PC STATE: test")
    b = brain(monkeypatch, pc=lambda text: "Closed paint." if text == "close paint" else None)
    b.ask("close paint", [].append)
    ctx = gateway_context.build("close it", b.messages, books=[])
    assert ("owner", "close paint") in ctx.recent and ("jarvis", "Closed paint.") in ctx.recent
    assert "jarvis: Closed paint." in ctx.render()


def test_a_side_turn_he_cut_off_is_never_spoken_and_history_says_so(monkeypatch):
    """Nov 9: a string of 'Stopped.' spoken over his next words. A cancelled side turn says nothing."""
    started, release = threading.Event(), threading.Event()

    def slow_pc(text):
        started.set()
        release.wait(5)
        return "Stopped."
    b = brain(monkeypatch, pc=slow_pc)
    heard = []
    worker = threading.Thread(target=b.ask, args=("open notepad and type a long paragraph", heard.append))
    worker.start()
    assert started.wait(5)
    b.cancel("")                              # the owner cut in while the PC part was still deciding
    release.set()
    worker.join(5)
    assert heard == [], "nothing is said after a cancel, not even 'Stopped.'"
    assert b.messages[-2:] == [{"role": "user", "content": "open notepad and type a long paragraph"},
                               {"role": "assistant", "content": llm.INTERRUPTED_BEFORE}]


def test_a_cut_off_side_turn_keeps_what_the_pc_part_reported_unsaid(monkeypatch):
    b = brain(monkeypatch, pc=lambda text: b.cancel("") or "Opened Notepad. Stopped; I did not go on to: tell free space.")
    heard = []
    b.ask("open notepad and tell me how much free space I have", heard.append)
    assert heard == []
    note = b.messages[-1]["content"]
    assert note.startswith(llm.INTERRUPTED_BEFORE) and "Opened Notepad." in note and "unsaid" in note


def test_a_conversation_that_opens_with_a_pc_turn_still_gets_its_background_once(monkeypatch):
    seen = []
    b = brain(monkeypatch, pc=lambda text: "Closed paint." if text == "close paint" else None, seen=seen)
    b.ask("close paint", [].append)
    assert not b._has_background(), "a side turn adds no background"
    b.ask("what did you just try to do?", [].append)
    b.ask("and why?", [].append)

    def backgrounds(history):
        return sum(1 for m in history for p in (m["content"] if isinstance(m["content"], list) else [m["content"]])
                   if str(p.get("text", "") if isinstance(p, dict) else p).lstrip().startswith("<background>"))
    assert len(seen) == 2
    assert backgrounds(seen[0]) == 1 and backgrounds(seen[1]) == 1, "injected once, on the first brain turn"
    assert any(m["content"] == "Closed paint." for m in seen[0] if m["role"] == "assistant"), "the brain sees the PC turn"


def test_an_unprompted_line_reaches_the_next_message_as_jarvis_words(monkeypatch):
    seen = []
    b = brain(monkeypatch, seen=seen, model_reply="Your reminder, sir: call the bank at three.")
    b.note_unprompted("Your 3pm reminder: call the bank.")
    b.ask("what?", [].append)
    user = b.messages[0]["content"]
    assert isinstance(user, list) and user[0]["text"].startswith(asides.TAG) and user[-1] == {"type": "text", "text": "what?"}
    assert asides.lines(user[0]["text"]) == ["Your 3pm reminder: call the bank."]
    # never his words (grounding), but kept in what the local model is sent
    assert intelligence.last_user_text(b.messages[:1]) == "what?"
    assert contextfit._question(b.messages[0])
    fitted = contextfit.fit([{"role": "system", "content": "You are Jarvis."}] + b.messages)
    assert any(asides.TAG in str(p) for m in fitted for p in (m["content"] if isinstance(m["content"], list) else [m["content"]]))
    assert ("jarvis", "Your 3pm reminder: call the bank.") in gateway_context._recent(b.messages)
    assert b._take_asides() == "", "taken once"


def test_an_unprompted_line_rides_with_a_side_turn_too_and_old_ones_expire(monkeypatch):
    b = brain(monkeypatch, pc=lambda text: "Closed paint.")
    b.note_unprompted("Now that your game is over: two reminders.")
    b._asides.appendleft((time.monotonic() - llm.ASIDE_SECONDS - 5, "unprompted", "an hour-old digest"))
    b.ask("close paint", [].append)
    user = b.messages[0]["content"]
    assert asides.lines(user[0]["text"]) == ["Now that your game is over: two reminders."]
    assert user[1]["text"] == "close paint" and b.messages[1]["content"] == "Closed paint."


def test_idle_past_the_timeout_archives_before_the_side_turn(monkeypatch):
    archived = []
    monkeypatch.setattr(journal, "archive_async", lambda uid, old: archived.append(list(old)))
    monkeypatch.setattr(journal, "archive_local", lambda uid, old: archived.append(list(old)))
    b = brain(monkeypatch, pc=lambda text: "Closed paint.")
    monkeypatch.setattr(intelligence, "enabled", lambda: False)
    old = [{"role": "user", "content": "open paint"}, {"role": "assistant", "content": "Opened paint."}]
    b.messages = list(old)
    b._last_turn = time.monotonic() - b.timeout - 5
    b.ask("close paint", [].append)
    assert archived == [old]
    assert b.messages == [{"role": "user", "content": "close paint"}, {"role": "assistant", "content": "Closed paint."}]


def test_a_side_turn_never_leaves_two_user_messages_in_a_row(monkeypatch):
    b = brain(monkeypatch, pc=lambda text: "Closed paint.")
    b._last_turn = time.monotonic()
    b.messages = [{"role": "user", "content": "open paint"}]           # a turn that left no reply
    b.ask("close paint", [].append)
    roles = [m["role"] for m in b.messages]
    assert roles == ["user", "assistant"] and [p["text"] for p in b.messages[0]["content"]] == ["open paint", "close paint"]
    b.messages = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}]
    b.ask("close paint", [].append)
    assert [m["role"] for m in b.messages] == ["user", "assistant", "user", "assistant"]


def test_the_journal_lists_the_pc_request_and_what_jarvis_reported(monkeypatch):
    """journal-pc.json 2026-11-10T15:40 kept 'Yes, go ahead and do it. / You see that?' and lost the PC request."""
    b = brain(monkeypatch, pc=lambda text: {"follow the instructions on my screen": "I read the steps on your screen. "
                                                                                   "Shall I open Paint?"}.get(text))
    b.ask("follow the instructions on my screen", [].append)
    b.ask("Yes, go ahead and do it.", [].append)
    prompts = []

    def no_model(role, system, history, tools, cancelled, **kw):
        prompts.append(history[0]["content"])
        raise intelligence.LocalFailure("local_unavailable")
    monkeypatch.setattr(intelligence, "request", no_model)
    entry = journal.archive_local("owner", b.messages)
    assert "follow the instructions on my screen" in entry["summary"]
    assert prompts and "JARVIS: I read the steps on your screen. Shall I open Paint?" in prompts[0]


def test_the_journal_shows_an_unprompted_line_as_jarvis_not_as_his_words():
    msgs = [{"role": "user", "content": [{"type": "text", "text": asides.block([("unprompted", "Your 3pm reminder: call the bank.")])},
                                         {"type": "text", "text": "what?"}]},
            {"role": "assistant", "content": "Your reminder to call the bank, sir."}]
    text, turns = journal.transcript(msgs)
    assert "(Jarvis had said, unprompted: Your 3pm reminder: call the bank.)" in text and "USER: what?" in text
    assert journal.their_words(msgs) == ["what?"] and turns == 1


@pytest.mark.parametrize("bad", ["", "   ", "[ignore] not for me"])
def test_nothing_or_overheard_speech_is_not_noted(monkeypatch, bad):
    b = brain(monkeypatch)
    b.note_unprompted(bad)
    assert b._take_asides() == ""


# ---- review fixes 2026-11-10 ---------------------------------------------------------------------------------------------
@pytest.fixture
def no_questions_waiting(monkeypatch):
    from core import approvals, gateway, saveprompt
    from tools import pcskills
    monkeypatch.setattr(approvals, "_pending", {})
    monkeypatch.setattr(pcskills, "_PENDING", {})
    monkeypatch.setattr(gateway, "_CHOICES", {})
    monkeypatch.setattr(saveprompt, "_PENDING", {})


def test_a_question_he_never_heard_cannot_take_his_yes(monkeypatch, no_questions_waiting):
    """'Follow the instructions on my screen' (27.6 s), he cuts in: the guide's question is not said, so the plan, its
    approval, a PC plan for review, 'which one?' and 'save it first?' armed by that turn are all withdrawn. Before, a later
    plain 'yes' (to 'Want the hourly forecast?') ran PC steps taken from untrusted screen text he never heard about."""
    from core import approvals, gateway, pcledger, saveprompt, users
    from tools import pcskills
    made = {}

    def guide(text):
        approvals.request("owner", "pc-screen-guide:1", "open Paint")
        pcskills._await_yes(SimpleNamespace(key="pc-screen-guide:1", run=lambda: "ran it", summary="open Paint"), "owner")
        made["plan"] = pcledger.create("plan", {"task": "close paint"})
        gateway._CHOICES["owner"] = {"task": "x", "choices": ["a", "b"], "remaining": [], "t": time.time()}
        saveprompt._PENDING["owner"] = {"app": "paint", "hwnd": 0, "t": time.time()}
        b.cancel("")
        return "I read the steps on your screen. Shall I open Paint?"
    b = brain(monkeypatch, pc=guide)
    heard = []
    b.ask("follow the instructions on my screen", heard.append)
    assert heard == []
    assert approvals.pending("owner") is None and pcskills.pending_guide("owner") is None
    assert "owner" not in gateway._CHOICES and saveprompt.pending("owner") is None
    with users.acting_as("owner"):
        assert made["plan"] not in [p["id"] for p in pcledger.pending()]


def test_a_question_he_heard_before_stays_open_when_a_later_turn_is_cut_off(monkeypatch, no_questions_waiting):
    from core import approvals
    approvals.request("owner", "delete:trailer", "delete the trailer")
    b = brain(monkeypatch, pc=lambda text: b.cancel("") or "Stopped.")
    b.ask("open notepad and type a long paragraph", [].append)
    assert approvals.pending("owner")["key"] == "delete:trailer", "asked earlier and heard: still his to answer"


def test_on_the_phone_a_stopped_turn_still_reports_what_the_pc_did(monkeypatch):
    """Review 2026-11-10: the phone's 'stop' cancels the brain while the request holds it; the gathered reply came back
    empty, and he was never told Notepad had been opened."""
    report = "Opened Notepad. Stopped; I did not go on to: type my notes."
    b = brain(monkeypatch, pc=lambda text: b.cancel("") or report)
    b.live = False
    heard = []
    b.ask("open notepad and type my notes", heard.append)
    assert heard == [report]
    assert b.messages[-1] == {"role": "assistant", "content": report}


def test_the_phone_and_telegram_get_no_provisional_lines(monkeypatch):
    """Their reply is gathered and sent when the turn ends: 'Right away, sir' and three 'Still going' lines arrived after
    the result. The voice brain still gets them."""
    from core.appserver import AppServer
    from core.phone import PhoneBot
    for channel in (AppServer, PhoneBot):
        host = SimpleNamespace(brains={}, make_brain=lambda uid: SimpleNamespace(live=True))
        assert channel._brain(host, "owner").live is False
    got = []
    b = brain(monkeypatch)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, say=None, earcon=None: got.append(say) or "Closed ChatGPT.")
    b.ask("close ChatGPT", [].append)
    b.live = False
    b.ask("close ChatGPT", [].append)
    assert callable(got[0]) and got[1] is None


def test_a_kreyol_pc_turn_gets_no_english_acknowledgment(monkeypatch):
    got = []
    b = brain(monkeypatch)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, say=None, earcon=None: got.append(say) or "Mwen fèmen l.")
    b.ask("Fèmen ChatGPT la tanpri", [].append)
    assert got == [None]


def test_the_gateway_sees_what_was_said_unprompted_before_it_decides(monkeypatch):
    """'Sir, your reminder: open the Unreal project.' then 'Okay, open it.': the gateway had no referent for 'it'."""
    seen = {}

    def handle(text, history=None, **kw):
        seen["recent"] = gateway_context._recent(history)
        return "Opened the Unreal project."
    b = brain(monkeypatch)
    monkeypatch.setattr(pcfast, "handle", handle)
    b.note_unprompted("Sir, your reminder: open the Unreal project.")
    b.ask("Okay, open it.", [].append)
    assert seen["recent"][-1] == ("jarvis", "Sir, your reminder: open the Unreal project.")
    user = b.messages[0]["content"]
    assert asides.lines(user[0]["text"]) == ["Sir, your reminder: open the Unreal project."] and len(b.messages) == 2
    assert b._take_asides() == "", "recorded once, with the turn"


class GapLock:
    """A lock that, once turn A has let go of it, lets turn B (already waiting) in before A may take it again: the
    window in which a queued ask() used to clear the cancel of the turn he had cut off."""

    def __init__(self, a):
        self._inner, self.a = threading.Lock(), a
        self.a_let_go, self.b_in = threading.Event(), threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        me = threading.current_thread()
        if me is self.a and self.a_let_go.is_set():
            self.b_in.wait(2)
        ok = self._inner.acquire(blocking, timeout)
        if ok and me is not self.a:
            self.b_in.set()
        return ok

    def release(self):
        self._inner.release()
        if threading.current_thread() is self.a:
            self.a_let_go.set()

    def locked(self):
        return self._inner.locked()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def test_a_next_turn_waiting_on_the_lock_cannot_let_a_cut_off_turn_speak(monkeypatch):
    """Review 2026-11-10: the cancel was checked in a second hold of the lock; the next ask() could take it in between,
    clear the cancel, and the cut-off turn then said 'Stopped.' over his new command."""
    heard_a, heard_b = [], []

    def pc(text):
        if text == "open notepad and type a long paragraph":
            second.start()
            time.sleep(0.2)                           # his next request is now waiting on the lock
            b.cancel("")
            return "Stopped."
        return "Opened Notepad."
    b = brain(monkeypatch, pc=pc)
    b._lock = GapLock(threading.current_thread())
    second = threading.Thread(target=b.ask, args=("never mind, open notepad", heard_b.append))
    b.ask("open notepad and type a long paragraph", heard_a.append)
    second.join(5)
    assert heard_a == [], "nothing from the turn he cut off"
    assert heard_b == ["Opened Notepad."]
    assert b.messages[1]["content"] == llm.INTERRUPTED_BEFORE and b.messages[-1]["content"] == "Opened Notepad."


def test_an_unprompted_line_goes_to_the_person_it_is_for_not_the_last_speaker(monkeypatch):
    """Review 2026-11-10: lines were filed under speaking['uid'] (whoever spoke last, never reset), so the owner's reminder
    landed in Maya's awareness log, conversation and journal, and his own 'what?' had nothing to refer to."""
    import ast
    from pathlib import Path

    import main
    from core import users
    monkeypatch.setattr(users, "all_users", lambda: ["owner", "maya"])
    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    loop = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "voice_loop")
    fn = next(n for n in loop.body if isinstance(n, ast.FunctionDef) and n.name == "say_unprompted")
    filed, noted = [], []
    scope = dict(outer_say=lambda text: None, remember_line=lambda uid, who, text: filed.append(uid),
                 brain=SimpleNamespace(brain_for=lambda uid: SimpleNamespace(
                     note_unprompted=lambda text, source="unprompted": noted.append(uid))),
                 diag=SimpleNamespace(swallowed=lambda *a: None), speaking={"uid": "maya"},
                 unprompted_for=getattr(main, "unprompted_for", lambda: "maya"))
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "main.py", "exec"), scope)
    scope["say_unprompted"]("A reminder, sir: pick up the ring from the jeweler.")    # the owner's (household timers too)
    with users.acting_as("maya"):                                                  # the scheduler delivers hers as her
        scope["say_unprompted"]("A reminder, ma'am: call the school.")
    assert filed == ["owner", "maya"] and noted == ["owner", "maya"]


def test_a_cut_in_while_the_pc_layer_decided_is_never_answered_over(monkeypatch):
    """Integration of the voice and conversation branches: he cut in while quick/pcfast were deciding (the lock was free
    between the two holds), pcfast passed, and the brain path cleared the flag again and answered over him."""
    holder = {}

    def pc_decides_while_he_cuts_in(text):
        if text.startswith("tell me"):
            holder["b"].cancel("")      # his barge-in lands in the gap
        return None                     # not a PC turn: on to the brain
    b = brain(monkeypatch, pc=pc_decides_while_he_cuts_in, model_reply="Here is a long answer, sir.")
    holder["b"] = b
    heard = []
    b.ask("tell me about graphics cards", heard.append)
    assert heard == []
    b.ask("what time is it in Paris?", heard.append)       # the next turn is his again
    assert heard == ["Here is a long answer, sir."]


def test_the_channel_note_never_reaches_the_gateways_recent_conversation_from_a_typed_turn_stored_as_blocks():
    """On the PC the companion's typed turns are the voice's conversation (plan upgrade 10). A typed turn stored as
    blocks (its <background>, a frame beside it) kept "[Sent from their Jarvis phone app...]" in the RECENT CONVERSATION
    the voice's next PC request is planned with: a wrong channel, and his words' end cut at 160 characters."""
    from core import gateway_context
    note = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] "
    words = "I keep thinking about the long drive tomorrow and whether the car will need new tyres before we leave town"
    history = [{"role": "user", "content": [{"type": "text", "text": "<background> It is Tuesday. </background>"},
                                            {"type": "text", "text": note + words}]},
               {"role": "assistant", "content": "Very good, sir."},
               {"role": "user", "content": note + "and the spare one"}]
    assert gateway_context._recent(history) == [("owner", words), ("jarvis", "Very good, sir."), ("owner", "and the spare one")]
