"""Wave 4 combination review (2026-11-11): on the PC the companion's typed chat and the voice are ONE conversation
(one Brain per person: plan upgrade 10), and proactive noticing (core/notice) speaks into it. What broke only because
these parts run together: the offer and his next reply, the line in the thread and in what is open, and the companion's
own voice. Synthetic sentences only."""
import time

import pytest

from core import attention, inferencegate, notice, notify, thread, turnstate
from tests.test_app import pair, real_brain, voice_server
from tests.test_notice import HERE, Reviewer, finished_job, pc, say  # noqa: F401  (pc: the notice's PC fixture)


@pytest.fixture
def daytime(monkeypatch):
    """Real clock (his typed reply is answered at time.time()), never his quiet hours."""
    monkeypatch.setattr(notify, "in_quiet_hours", lambda *a, **k: False)
    return time.time()


def say_unprompted(b, pc_loop):
    """main.say_unprompted as the voice loop wires it: said aloud, then noted in his conversation (Brain.note_unprompted)."""
    def voice(text):
        pc_loop["said"].append(text)
        b.note_unprompted(text, "unprompted")
    return voice


def offer(now, n="1", line="Sir, rigging Vanguard finished."):
    finished_job(n, now=now)
    assert notice.tick(now=now, sit=HERE, ask=Reviewer(say([f"craft-{n}"], line))) == "said"


# ------------------------------------------------------------------ the notice offer and the typed half of the thread
def test_a_typed_reply_in_the_companion_is_his_next_reply_to_the_news(pc, daytime, monkeypatch):
    """The notice line, then he types in the companion: that typed line answered it. A spoken 'no' 45 s later (to the
    typed reply's question, or 'Jarvis, stop' to its voice) never quiets finished craft jobs for a day."""
    b, _ = real_brain(monkeypatch)
    _, client = voice_server(monkeypatch, b)
    offer(daytime)
    client.post("/api/chat", json={"text": "great, export the rig to unreal", "speak": False}, headers=pair(client))
    assert notice._state["offer"] is None, "the typed reply left the offer open for the next spoken line"
    assert notice.heard_reply("owner", "no", now=daytime + 45) == ""
    assert "suppressed" not in notice._load()


def test_a_typed_not_now_declines_the_news_and_is_no_voice(pc, daytime, monkeypatch):
    b, _ = real_brain(monkeypatch)
    _, client = voice_server(monkeypatch, b)
    offer(daytime)
    client.post("/api/chat", json={"text": "not now", "speak": False}, headers=pair(client))
    assert notice._load()["suppressed"]["finished:craft"]["level"] == 1
    assert notice._state["voice"][1] == 0.0, "a typed line is not a voice heard at the PC"



@pytest.mark.parametrize("line", ["Sir, your tea timer is done.",                      # tools.system.timer_done
                                  "Sir, focus block 1 done. Take a 5-minute break.",   # core.focus
                                  "Game mode on, sir. I'll keep quiet."])              # GameWatcher
def test_a_timer_focus_or_game_line_after_the_news_takes_his_next_reply(pc, line, monkeypatch):
    """Those lines reach main.say_unprompted directly (never core.attention): his 'stop' / 'I know' to them is theirs."""
    from tests.test_notice import NOON
    b, _ = real_brain(monkeypatch)
    offer(NOON)
    b.note_unprompted(line, "unprompted")          # main.say_unprompted's note, for every unprompted line
    assert notice.heard_reply("owner", "stop", now=NOON + 45) == ""
    assert "suppressed" not in notice._load()


def test_every_unprompted_speaker_in_main_goes_through_say_unprompted():
    """The note above is reached by all of them: system.announce (timers, focus), the scheduler and the game watcher."""
    import inspect

    import main
    src = inspect.getsource(main.voice_loop)
    assert "brain.brain_for(uid).note_unprompted(text" in src
    for wired in ("system.announce = lambda msg: say_unprompted(", "Scheduler(say=say_unprompted",
                  "GameWatcher(say=say_unprompted)", "system.speak = say_unprompted"):
        assert wired in src


def test_the_news_line_is_a_row_of_the_thread_so_the_phone_sees_what_his_answer_answered(pc, monkeypatch):
    from tests.test_notice import NOON
    b, _ = real_brain(monkeypatch, pc=lambda text: "Opening the render folder, sir." if "open it" in text else None)
    monkeypatch.setattr(attention, "_voice", say_unprompted(b, pc))
    offer(NOON)
    b.ask("yes open it", [].append)
    rows = thread.own("owner", window=10 **10)
    assert [(r["said"], r["reply"]) for r in rows] == [("", "Sir, rigging Vanguard finished."),
                                                       ("yes open it", "Opening the render folder, sir.")]
    assert 'Jarvis: "Sir, rigging Vanguard finished." (unprompted)' in "\n".join(thread._lines(rows))


def test_only_unprompted_lines_that_invite_his_answer_are_rows_of_the_thread(monkeypatch):
    """Timers, focus switches, game mode and reminders reach Brain.note_unprompted too: as thread rows, one focus session
    pushed his real PC exchanges out of the phone's background (thread.LIMIT) and came back as news on his next phone
    turn (review 2026-11-11). A line that asks him something stays a row: his answer elsewhere has its question."""
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)               # the PC
    b, _ = real_brain(monkeypatch, pc=lambda t: "Opened the render folder, sir." if "render folder" in t else None)
    monkeypatch.setattr(thread, "refresh", lambda uid=None: None)
    b.ask("open the render folder", [].append)                            # real exchanges at the PC
    b.ask("what should I call the castle scene", [].append)
    for n in range(1, 4):                                                  # main.say_unprompted -> note_unprompted
        b.note_unprompted(f"Sir, focus block {n} done. Take a 5-minute break.", "unprompted")
        b.note_unprompted(f"Sir, break over. Focus block {n + 1} starts now.", "unprompted")
    b.note_unprompted("Game mode on, sir. I'll keep quiet.", "unprompted")
    b.note_unprompted("Sir, the backup finished. Shall I open its log?", "unprompted")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)             # the laptop, building the phone's background
    shown = thread.background("owner", now=time.time() + 1)
    assert "render folder" in shown and "castle scene" in shown, shown
    assert "focus block" not in shown and "Game mode" not in shown
    assert 'Jarvis: "Sir, the backup finished. Shall I open its log?" (unprompted)' in shown


def test_the_news_question_is_open_in_the_voice_loops_own_view(pc, monkeypatch):
    """main.voice_loop reads what is open through the Brain (turnstate.open_items(uid, brain)): his 'okay' to the
    notice's question goes straight to the brain, never through the meaning model (which may read it as a dismissal)."""
    import main
    from tests.test_notice import NOON
    b, _ = real_brain(monkeypatch)
    monkeypatch.setattr(attention, "_voice", say_unprompted(b, pc))
    notice.publish("open:abc", "Still open from Monday: renew the domain.", "low", question=True, now=NOON)
    r = Reviewer(say(["open-abc"], "Sir, still open from Monday: renew the domain. Want to pick it up?"))
    assert notice.tick(now=NOON, sit=HERE, ask=r) == "said"
    items = turnstate.open_items("owner", b)
    assert [i["kind"] for i in items] == ["question"] and "pick it up" in items[0]["question"]

    def meaning(norm):
        raise AssertionError(f"the meaning model was asked about {norm!r}")
    assert main.pre_brain_route("okay", items, meaning=meaning) == "respond"


@pytest.mark.parametrize("answer", ["yeah, go on", "carry on", "okay, continue"])
def test_go_on_to_the_news_question_answers_it_and_never_replays_an_older_cut_off_reply(pc, answer, monkeypatch):
    from tests.test_notice import NOON
    b, _ = real_brain(monkeypatch)
    monkeypatch.setattr(attention, "_voice", say_unprompted(b, pc))
    b._unsaid = ("Sunday brings heavy rain all day.", time.monotonic())        # he cut a reply a minute ago
    notice.publish("open:abc", "Still open from Monday: renew the domain.", "low", question=True, now=NOON)
    r = Reviewer(say(["open-abc"], "Sir, still open from Monday: renew the domain. Want to pick it up?"))
    assert notice.tick(now=NOON, sit=HERE, ask=r) == "said"
    heard: list[str] = []
    b.ask(answer, heard.append)
    assert not any("as i was saying" in s.lower() or "rain" in s.lower() for s in heard), heard


# ------------------------------------------------------------------ the companion's own voice and the notice gate
def test_a_turn_counts_as_just_answered_from_its_end_not_its_start(monkeypatch):
    from types import SimpleNamespace

    from anthropic.types import TextBlock
    ended = {}

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        time.sleep(0.2)
        ended["at"] = time.monotonic()
        return SimpleNamespace(content=[TextBlock(type="text", text="Dinner is at seven, sir.")], stop_reason="end_turn")
    b, _ = real_brain(monkeypatch, request=request)
    b.ask("when is dinner", [].append)
    assert b._last_turn >= ended["at"], "a model turn of 2 s or more left core.notice's 2 s grace over as it was answered"


@pytest.mark.parametrize("path, body", [("/api/chat", {"text": "when is dinner", "speak": True}),
                                        ("/api/say", {"text": "Dinner is at seven, sir. The table is booked."})])
def test_nothing_is_said_unprompted_while_the_companion_voices_a_reply(pc, path, body, monkeypatch):
    """The companion plays the reply's MP3 in its own window, unseen by the voice loop's speaker: the notice waits."""
    from tests.test_notice import NOON
    monkeypatch.setattr(notice, "TURN_IDLE_S", 0.0)          # only the companion's own voice is looked at here
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: b"ID3fake")
    b, _ = real_brain(monkeypatch)
    _, client = voice_server(monkeypatch, b)
    assert client.post(path, json=body, headers=pair(client)).json()["audio"]
    finished_job(now=NOON)
    r = Reviewer(say(["craft-1"], "Sir, rigging Vanguard finished."))
    assert notice.tick(now=NOON, sit=HERE, ask=r) == "mid-turn" and r.calls == [] and pc["said"] == []
    notice._state["voiced_until"] = 0.0                     # once it has been heard
    assert notice.tick(now=NOON + 60, sit=HERE, ask=r) == "said"


def test_a_skill_explanation_is_his_turn_for_the_card(monkeypatch):
    """/api/skills/explain asks the local model on the voice's Brain: no background model work (the notice's reviewer)
    may run beside it, and nothing unprompted is said over it."""
    from types import SimpleNamespace

    from anthropic.types import TextBlock

    from core import intelligence
    seen = []

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        seen.append(inferencegate.busy())
        return SimpleNamespace(content=[TextBlock(type="text", text="Your drive has 40 GB free, sir.")], stop_reason="end_turn")
    b, _ = real_brain(monkeypatch, request=request)
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    out = []
    b.explain_result("disk space", "C: 40 GB free", out.append)
    assert out and seen == [True] and not inferencegate.busy()
