"""Learning from demonstration (Mark IV, 2026-11-09): "watch me <task>" ... "done" -> a typed plan the owner says yes to;
a verified try-run saves it as a workflow for his phrase, and the phrase replays it without asking again."""
import pytest

from core import demonstrate, pcactions, pchybrid, pcledger, pcuse

QUAD = {"title": "Quad Remote - Session 4", "process": "QuadRemote.exe"}
CALC = {"title": "Calculator", "process": "ApplicationFrameHost.exe"}


def click(name, window=QUAD, titlebar=False):
    return {"kind": "click", "name": name, "type": "Button", "titlebar": titlebar, "window": window}


def key(k, window=QUAD, mods=(), password=False):
    return {"kind": "key", "key": k, "mods": list(mods), "window": window, "password": password}


def test_minimize_by_title_bar_becomes_minimize_app():
    steps, problems = demonstrate.compile_steps([click("Minimize", titlebar=True)])
    assert steps == [{"tool": "minimize_app", "arguments": {"app": "quadremote"}}] and problems == []


def test_clicks_typing_and_keys_compile_to_existing_typed_steps():
    events = [click("Seven", CALC), click("Plus", CALC), key("4", CALC), key("2", CALC), key("x", CALC), key("backspace", CALC),
              key("enter", CALC), key("s", CALC, mods=["ctrl"])]
    steps, problems = demonstrate.compile_steps(events)
    assert problems == []
    assert [s["arguments"]["goal"] for s in steps] == ["click on Seven", "click on Plus", 'type "42"', "press enter", "press ctrl+s"]
    assert all(s["tool"] == "use_app" and s["arguments"]["app"] == "calculator" for s in steps)
    pcactions.validate({"steps": steps})


def test_a_password_field_an_unnamed_spot_and_jarvis_itself_are_never_steps():
    steps, problems = demonstrate.compile_steps([
        key("h", QUAD, password=True), key("i", QUAD, password=True),
        click("", QUAD),
        click("Send", {"title": "Jarvis", "process": "pythonw.exe"}),
        click("Start", {"title": "", "process": "explorer.exe"})])
    assert steps == []
    assert any("password" in p for p in problems) and any("no name" in p for p in problems)


def test_a_double_click_is_one_step_and_quotes_survive():
    steps, _ = demonstrate.compile_steps([click("Open"), click("Open"), key('"'), key("a")])
    assert [s["arguments"]["goal"] for s in steps] == ["click on Open", 'type "\\"a"']
    assert pcuse._ONE_TYPE.match(steps[1]["arguments"]["goal"])


class FakeRecorder:
    def __init__(self, events=()):
        self.events, self.stopped = list(events), False

    def stop(self):
        self.stopped = True
        return self.events


@pytest.fixture
def pc(monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    # Blender counts as installed here whatever this PC has (it was uninstalled 2026-11-20 and these tests failed)
    from pathlib import Path
    from tools import apps as _apps
    real = _apps._installed
    monkeypatch.setattr(_apps, "_installed", lambda: {**real(), "blender 5.2": Path(r"C:\Program Files\Blender Foundation\Blender 5.2lender-launcher.exe")})
    monkeypatch.setitem(demonstrate._state, "recorder", None)
    monkeypatch.setitem(demonstrate._state, "task", "")


def test_the_conversation(pc, monkeypatch):
    rec = FakeRecorder([click("Minimize", titlebar=True)])
    monkeypatch.setattr(demonstrate, "Recorder", lambda: rec)
    stored = []
    monkeypatch.setattr(demonstrate, "_store", lambda task, plan: stored.append((task, plan)))
    assert demonstrate.handle("What time is it?") is None
    assert demonstrate.handle("Watch me.") .startswith("Tell me what")
    assert demonstrate.handle("Okay, watch me minimize Quad.") == "Okay, I'm watching. Say 'done' when you've finished."
    assert demonstrate.handle("What's the weather?") is None             # other turns go on as normal while it watches
    assert demonstrate.handle("Done.") == "Got it, 1 step in Quadremote. Should I try it now?"
    assert rec.stopped and stored == [("minimize Quad", {"steps": [{"tool": "minimize_app", "arguments": {"app": "quadremote"}}]})]
    assert demonstrate.handle("Done.") is None                          # nothing is being watched any more


def test_an_incomplete_demonstration_saves_nothing_and_says_why(pc, monkeypatch):
    monkeypatch.setattr(demonstrate, "Recorder", lambda: FakeRecorder([click("Open"), click("", QUAD)]))
    monkeypatch.setattr(demonstrate, "_store", lambda task, plan: pytest.fail("must not store"))
    demonstrate.handle("let me show you how to open the session")
    out = demonstrate.handle("that's it")
    assert "can't learn that one yet" in out and "no name" in out and "Nothing was saved" in out


def test_cancel(pc, monkeypatch):
    rec = FakeRecorder([click("Open")])
    monkeypatch.setattr(demonstrate, "Recorder", lambda: rec)
    demonstrate.handle("watch me open the session")
    assert demonstrate.handle("never mind") == "Okay, I stopped watching. Nothing was saved." and rec.stopped
    assert not demonstrate.recording()


def test_done_through_the_voice_loop_reaches_the_demonstration(pc, monkeypatch):
    """Live 2026-11-10: 'Jarvis, watch me save this on Blender.' ... 'Done.' -> 'Of course, sir.' and the mic slept: the voice
    loop's dismiss filter (main.py) took 'done' before core.demonstrate saw it. Now what is open decides."""
    import main
    from core import meaning, turnstate
    monkeypatch.setattr(meaning, "choose", lambda said, *a, **k: "dismiss")     # what the meaning model said for 'done'
    rec = FakeRecorder([click("Minimize", titlebar=True)])
    monkeypatch.setattr(demonstrate, "Recorder", lambda: rec)
    finished = []
    real_finish = demonstrate.finish
    monkeypatch.setattr(demonstrate, "finish", lambda **kw: finished.append(1) or real_finish(store=lambda task, plan: None))
    assert demonstrate.handle("Jarvis, watch me minimize Quad.").startswith("Okay, I'm watching.")
    items = turnstate.open_items("owner")
    assert [i["kind"] for i in items] == ["demonstrate"]
    heard = "Done."
    norm = heard.lower().strip(" .!?,")
    assert main.pre_brain_route(norm, items) == "respond"
    assert demonstrate.handle(heard) == "Got it, 1 step in Quadremote. Should I try it now?" and finished == [1] and rec.stopped
    assert main.pre_brain_route(norm, turnstate.open_items("owner")) == "dismiss", "nothing open: as before"


class StoppedRecorder(FakeRecorder):
    """A recording that stopped listening by itself (MAX_SECONDS): he walked away, or the mic slept before his 'done'."""
    listening = False


def test_a_recording_that_stopped_by_itself_never_keeps_jarvis_watching(pc, monkeypatch):
    """Review 2026-11-10: the recorder stops itself at 180 s but stayed 'recording', so every later 'close Blender' was chat
    ('watching a demonstration') and short dismissals never ended the conversation, until a restart."""
    import time
    import main
    from core import gateway, turnstate
    rec = FakeRecorder([click("Minimize", titlebar=True)])
    monkeypatch.setattr(demonstrate, "Recorder", lambda: rec)
    demonstrate.handle("watch me minimize Quad")
    assert gateway.exact("close blender").intents[0].summary == "watching a demonstration"
    rec.listening = False                                   # MAX_SECONDS passed: it stopped by itself
    assert not demonstrate.recording()
    assert gateway.exact("close blender").kind == "actions", "his PC commands act again"
    assert demonstrate.pending(), "'done' can still finish what was recorded"
    monkeypatch.setitem(demonstrate._state, "at", time.monotonic() - demonstrate.FINISH_SECONDS - 1)
    assert not demonstrate.pending() and demonstrate._state["recorder"] is None and rec.stopped, "abandoned: dropped"
    assert turnstate.open_items("owner") == []
    assert main.pre_brain_route("thanks", turnstate.open_items("owner"), meaning=lambda n: None) == "dismiss"


def test_past_max_seconds_a_recording_stops_capturing_even_if_it_never_stopped_itself(pc, monkeypatch):
    """The worker checks MAX_SECONDS only when no input arrives for a moment: past it, his keys are never captured further."""
    import time
    rec = FakeRecorder([click("Minimize", titlebar=True)])
    rec.listening = True
    monkeypatch.setattr(demonstrate, "Recorder", lambda: rec)
    demonstrate.handle("watch me minimize Quad")
    monkeypatch.setitem(demonstrate._state, "at", time.monotonic() - demonstrate.MAX_SECONDS - 10)
    assert not demonstrate.recording()
    assert demonstrate.pending() and rec.stopped, "capture stopped; 'done' can still finish it"


def test_done_after_the_recording_stopped_by_itself_still_finishes_it(pc, monkeypatch):
    rec = StoppedRecorder([click("Minimize", titlebar=True)])
    monkeypatch.setattr(demonstrate, "Recorder", lambda: rec)
    monkeypatch.setattr(demonstrate, "_store", lambda task, plan: None)
    demonstrate.handle("watch me minimize Quad")
    assert not demonstrate.recording() and demonstrate.pending()
    assert demonstrate.handle("Done.") == "Got it, 1 step in Quadremote. Should I try it now?"


def test_his_narration_while_watched_gets_a_short_okay(pc, monkeypatch):
    """Review 2026-11-10: every narrated step was a full chat turn, and Jarvis talked over the next step."""
    monkeypatch.setattr(demonstrate, "Recorder", lambda: FakeRecorder())
    demonstrate.handle("watch me save this in Blender")
    assert demonstrate.handle("I'm going to go in and press save here... I'm going to click on save.") == "Okay."
    assert demonstrate.handle("What's the weather?") is None
    demonstrate.cancel()
    assert demonstrate.handle("I'm going to click on save.") is None, "not watching: as before"


def test_done_said_over_his_reply_still_ends_the_demonstration(pc, monkeypatch):
    """Review 2026-11-10: a one-word 'Done.' over his voice was a backchannel and thrown away (audio/bargein)."""
    import main
    from audio import bargein
    monkeypatch.setattr(demonstrate, "Recorder", lambda: FakeRecorder())
    assert bargein.classify_interruption("Done.", True, False, ["Okay."]) == "backchannel"
    assert main.cut_in_kind("Done.", "backchannel") == "backchannel", "nothing watched: as before"
    demonstrate.handle("watch me save this in Blender")
    assert main.cut_in_kind("Done.", "backchannel") == "interrupt"
    assert main.cut_in_kind("Finished.", "backchannel") == "interrupt"
    assert main.cut_in_kind("Mhm.", "backchannel") == "backchannel"
    assert main.cut_in_kind("Done.", "echo") == "echo"


def test_the_mic_stays_awake_while_he_demonstrates(pc, monkeypatch):
    """Review 2026-11-10: the follow-up window stayed 30 s, so a quiet 45 s demonstration ended in sleep and 'Done.' was lost."""
    import main
    assert main.followup_window(False, 30.0) == 30.0 and main.followup_window(True, 30.0) == 6.0
    assert main.followup_window(False, 30.0, eyes_on=True) == 600.0
    monkeypatch.setattr(demonstrate, "Recorder", lambda: FakeRecorder())
    demonstrate.handle("watch me save this in Blender")
    assert main.followup_window(False, 30.0) >= demonstrate.MAX_SECONDS
    assert main.followup_window(True, 30.0) >= demonstrate.MAX_SECONDS


def test_only_on_the_pc(monkeypatch):
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setitem(demonstrate._state, "recorder", None)
    assert demonstrate.handle("watch me minimize Quad") == "I can only watch you on the PC."


def test_workflow_phrases_match_without_the_full_stop():
    assert pcledger._trigger("Minimize Quad.") == pcledger._trigger("minimize quad") == "minimize quad"


def test_a_demonstrated_workflow_is_approved_for_exactly_its_own_plan(monkeypatch):
    plan = {"steps": [{"tool": "minimize_app", "arguments": {"app": "quadremote"}}]}
    monkeypatch.setattr(pcledger, "workflow", lambda t: {"trigger": "minimize quad", "plan": plan, "demonstrated": True})
    assert pchybrid.demonstrated("Minimize Quad.", plan)
    assert not pchybrid.demonstrated("Minimize Quad.", {"steps": [{"tool": "close_app", "arguments": {"app": "quadremote"}}]})
    monkeypatch.setattr(pcledger, "workflow", lambda t: {"trigger": "minimize quad", "plan": plan})      # learned silently, not shown
    assert not pchybrid.demonstrated("Minimize Quad.", plan)


class Ex:
    def __init__(self, ok=True):
        self.lines, self.ok = [], ok

    def run(self, line):
        self.lines.append(line)
        from types import SimpleNamespace
        return SimpleNamespace(ok=self.ok)


@pytest.mark.parametrize("goal, line, detail", [
    ("press enter", "key enter", "Pressed enter in Calculator."),
    ("Press the Tab key.", "key tab", "Pressed tab in Calculator."),
    ("press ctrl+s", "hotkey ctrl+s", "Pressed ctrl+s in Calculator."),
    ('type "42"', 'type "42"', "Typed it in Calculator."),
])
def test_one_key_or_text(goal, line, detail):
    ex = Ex()
    assert pcuse.one_key(goal, ex, display="Calculator")["detail"] == detail and ex.lines == [line]


def test_press_a_word_is_a_button_not_a_key_and_a_refused_key_is_none():
    ex = Ex()
    assert pcuse.one_key("press Seven", ex) is None and ex.lines == []
    assert pcuse.one_key("press alt+f4", Ex(ok=False)) is None


def test_a_title_bar_button_named_after_its_app_still_counts():
    # Calculator's is "Minimize Calculator" (live recording 2026-11-09)
    steps, _ = demonstrate.compile_steps([click("Minimize Calculator", CALC, titlebar=True)])
    assert steps == [{"tool": "minimize_app", "arguments": {"app": "calculator"}}]


def test_a_finished_multi_step_job_is_one_line():
    plan = {"steps": [{"tool": "use_app", "arguments": {"app": "calculator", "goal": g}}
                      for g in ("click on Seven", "click on Plus", "click on Two", "click on Equals")]}
    assert pchybrid.done_line(plan) == "Done: 4 steps in Calculator."
    assert pchybrid.done_line({"steps": [{"tool": "investigate_slowness", "arguments": {}}] * 3}) == "Done: 3 steps."
