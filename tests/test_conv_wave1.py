"""Conversation wave 1 (2026-11-14): he knows what he can do, answers "what is using my CPU" with a reading, and hands a
line back once instead of asking the same question a third time. Every line here is the owner's own, from his log."""
import os
import sys
import time

import pytest

from core import procsnap, quick, selfcard, speech


@pytest.fixture
def loaded(monkeypatch):
    """The skills of a full PC, without importing them: the areas are proven by module name only."""
    every = {m for row in selfcard.AREAS for m in row[1]}
    monkeypatch.setattr(selfcard, "_loaded", lambda: set(every))
    return every


# --- what he can do ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["That's funny. That's funny. All right. So what are your skills at the moment",
                                  "what can you do", "What else can you do for me", "tell me what you can do",
                                  "what abilities do you have right now"])
def test_asked_what_he_can_do_he_names_the_real_areas(loaded, said):
    answer = selfcard.answer(said)
    assert answer and "run this PC" in answer and "live game coaching" in answer and "language lessons" in answer
    assert "?" not in answer and len(answer.split()) < 95, answer            # spoken, and no question back


@pytest.mark.parametrize("said", ["Where is the gaming coach", "Or you don't have the gaming coach ability",
                                  "do you have a game coach", "I thought you had a gaming coach"])
def test_the_gaming_coach_is_never_denied(loaded, said):
    answer = selfcard.answer(said)
    assert answer.startswith("I do: I can coach you live in Apex and Call of Duty"), answer


def test_an_area_is_named_only_when_its_skills_are_loaded(monkeypatch):
    monkeypatch.setattr(selfcard, "_loaded", lambda: {"notes", "web"})          # the laptop: no PC skills, no coach
    answer = selfcard.answer("what can you do")
    assert "search the web" in answer and "run this PC" not in answer and "game" not in answer
    assert selfcard.answer("where is the gaming coach") is None                # not claimed, and not denied in code
    monkeypatch.setattr(selfcard, "_loaded", lambda: set())
    assert selfcard.answer("what can you do") is None


@pytest.mark.parametrize("said", ["where is the weather", "where is my study guide", "what skills does a plumber need",
                                  "do you have a minute", "do you have the time", "you have a call at 3",
                                  "do you have any notes on the meeting", "you have a reminder set",
                                  "do you have a timer running", "What have you mastered for PC control",
                                  "what can you do about the noise", "where is the coach station in Springfield",
                                  "where is the tutor page", "do you have the fitness coach page open",
                                  "do you have the tutor open in the browser", "where is the gaming coach window",
                                  "do you have a stylist for my sister", "do you have the fitness trainer's number",
                                  "where is the coach's notes from yesterday",
                                  "Set a timer for five minutes. What can you do", "Close Chrome. What else can you do"])
def test_his_own_things_and_other_questions_are_left_alone(loaded, said):
    assert selfcard.answer(said) is None


def test_the_quick_path_answers_it_before_any_model(loaded, monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)                   # works from the phone path too
    assert "live game coaching" in quick.handle("So what are your skills at the moment?")
    assert quick.handle("Hey Jarvis, where is the gaming coach?").startswith("I do:")
    assert quick.handle("what skills does a plumber need") is None


def test_the_pc_only_question_keeps_its_own_answer(loaded, monkeypatch):
    from core import pcbridge
    from tools import pcskills
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(pcskills, "capability_summary", lambda: "GROUNDED")
    assert quick.handle("What PC control skills do you have?") == "GROUNDED"


# --- what is using the processor and the memory -----------------------------------------------------------------------
def test_cpu_question_is_a_reading(monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(quick, "whats_busy", lambda: "READING")
    assert quick.handle("What's taking all my CPU right now? My CPU and my RAM. What's running on there?") == "READING"
    assert quick.handle("what is using my memory") == "READING"
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)                   # the laptop cannot read the PC's processes
    assert quick.handle("What's taking all my CPU right now?") != "READING"


@pytest.mark.parametrize("said", ["how do I check what is using my cpu", "what is ram", "how much ram should I buy",
                                  "what's using my ram, close it", "what's taking up my memory, then close chrome",
                                  "what is taking my memory in the contract", "Close Chrome. What's using my CPU?"])
def test_advice_about_processors_is_not_a_reading(monkeypatch, said):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(quick, "whats_busy", lambda: "READING")
    assert quick.handle(said) != "READING"


def test_whats_busy_names_the_apps_by_share(monkeypatch):
    snaps = [[(1, "msedge.exe", 2 * 2**30, 0), (2, "UnrealEditor.exe", 6 * 2**30, 0), (3, "System", 1, 0), (4, "new.exe", 5, 0)],
             [(1, "msedge.exe", 2 * 2**30, 10_000_000), (2, "UnrealEditor.exe", 6 * 2**30, 40_000_000),
              (3, "System", 1, 90_000_000), (5, "late.exe", 2**20, 70_000_000)]]
    monkeypatch.setattr(procsnap, "timed", lambda: snaps.pop(0))
    clock = iter([100.0, 101.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(os, "cpu_count", lambda: 10)
    said = quick.whats_busy()
    # one second, ten cores: Unreal used 4 core-seconds = 40 percent, Edge 10; a process first seen now is not counted
    assert said.startswith("The processor is at about 50 percent: Unreal Editor 40, Edge 10."), said
    assert "Unreal Editor 6.0 GB, Edge 2.0 GB" in said and "System" not in said and "late" not in said.split("Memory")[0]


def test_whats_busy_says_so_when_the_table_cannot_be_read(monkeypatch):
    def broken():
        raise OSError("no table")
    monkeypatch.setattr(procsnap, "timed", broken)
    assert quick.whats_busy() == "I couldn't read what is running just now."


@pytest.mark.skipif(sys.platform != "win32", reason="the one-call table is Windows-only")
def test_the_timed_snapshot_counts_this_process_working():
    before = {pid: cpu for pid, _, _, cpu in procsnap._windows_timed()}
    until = time.monotonic() + 0.3
    while time.monotonic() < until:
        sum(i * i for i in range(2000))
    after = {pid: (name, cpu) for pid, name, _, cpu in procsnap._windows_timed()}
    name, cpu = after[os.getpid()]
    used = (cpu - before[os.getpid()]) / 1e7
    assert name.lower().startswith("python") and 0.1 < used < 3, (name, used)
    assert [r[:3] for r in procsnap._windows_timed()][:1] == procsnap._windows()[:1]      # the old reader is unchanged


# --- one line hands it back instead of the same question a third time -------------------------------------------------
_ASKED_NAME = "I see WhatsApp Web on your screen. Can you tell me its name?"


def _polish(sentences, asked, earlier, **kw):
    said = []
    p = speech.Polisher(said.append, asked=asked, earlier=earlier, title="sir", **kw)
    for s in sentences:
        p(s)
    p.finish()
    return said


def test_the_same_question_is_not_asked_again_after_his_short_answer():
    said = _polish(["I see WhatsApp Web on your screen.", "Can you tell me its name?"], "The future.",
                   [("That's called the future.", _ASKED_NAME)])
    assert said == ['I heard "The future", sir, and I cannot place it. Say it another way.'], said


def test_asked_to_hear_it_again_he_still_says_it_again():
    said = _polish(["I see WhatsApp Web on your screen.", "Can you tell me its name?"], "Say that again.",
                   [("The future.", _ASKED_NAME)])
    assert any("WhatsApp Web" in s for s in said) and not any("cannot place" in s for s in said), said


def test_a_repeat_of_a_plain_answer_keeps_as_i_said():
    said = _polish(["The meeting with the bank is at three in the afternoon."], "When is the meeting?",
                   [("When is the meeting?", "The meeting with the bank is at three in the afternoon.")])
    assert said == ["As I said, sir: The meeting with the bank is at three in the afternoon."], said


@pytest.mark.parametrize("asked, last, draft", [
    ("No.", "The laptop is not charging right now. Shall I turn on battery saver for you, sir?",
     ["The laptop is not charging right now.", "Understood."]),
    ("The second one.", "I found two folders named Reports on the desktop. Which one should I open, sir?",
     ["I found two folders named Reports on the desktop.", "Opening the second one now."]),
    ("Notepad.", "Notepad and Paint are both open on the screen. Which app should I close, sir?",
     ["Notepad and Paint are both open on the screen.", "Closing Notepad now."]),
])
def test_a_draft_that_goes_on_to_answer_him_is_said_whole(asked, last, draft):
    said = _polish(draft, asked, [("What about it?", last)])
    assert not any("cannot place" in s for s in said) and said[-1] == draft[-1], said


def test_no_is_an_answer_even_when_the_draft_only_repeats():
    last = "The laptop is not charging right now. Shall I turn on battery saver for you, sir?"
    said = _polish(["The laptop is not charging right now.", "Shall I turn on battery saver for you, sir?"], "No.",
                   [("Is it charging?", last)])
    assert not any("cannot place" in s for s in said), said


def test_the_stored_reply_is_the_one_line_he_heard():
    said = []
    p = speech.Polisher(said.append, asked='The "future".', earlier=[("That's called the future.", _ASKED_NAME)], title="")
    for s in ["I see WhatsApp Web on your screen.", "Can you tell me its name?"]:
        p(s)
    assert said == []                                   # held until the draft ends
    p.finish()
    assert said == ['I heard "The future", and I cannot place it. Say it another way.'], said
    assert ("Can you tell me its name?", "") in p.edits and p.edits[0][0] == "I see WhatsApp Web on your screen."


def test_a_tool_that_ran_after_the_copy_keeps_the_draft_and_blanks_do_not_release_it():
    ran = []
    said = []
    p = speech.Polisher(said.append, asked="The future.", earlier=[("That's called the future.", _ASKED_NAME)],
                        title="sir", fresh=lambda: bool(ran))
    p("I see WhatsApp Web on your screen.")
    p(" ")
    assert said == []                                   # a blank between sentences is no new content
    ran.append("click")
    p.finish()
    assert said and "cannot place" not in " ".join(said) and "WhatsApp Web" in said[0], said


@pytest.mark.parametrize("said", ["what's using my ram, sir", "Jarvis what's using my cpu", "what is using my memory please",
                                  "what's using my ram? thanks", "so what's eating all my memory right now"])
def test_courtesies_around_the_cpu_question_do_not_hide_it(said):
    assert quick._asks_whats_busy(said)


def test_an_order_given_twice_after_yes_sir_is_no_misunderstanding():
    result = "No Apex Legends HUD profile yet, so the coach cannot read your health bar."
    said = _polish([result], "Start Gaming Coach.", [("Start Gaming Coach.", result + " Yes, sir?")])
    assert not any("cannot place" in s for s in said), said


def test_a_new_reading_after_a_tool_is_never_replaced():
    said = _polish(["I see WhatsApp Web on your screen.", "Can you tell me its name?"], "The future.",
                   [("That's called the future.", _ASKED_NAME)], fresh=lambda: True)
    assert said == ["I see WhatsApp Web on your screen.", "Can you tell me its name?"], said
