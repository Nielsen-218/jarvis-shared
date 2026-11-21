"""Mark IV (2026-11-09): the screenshot judge (an unsure check is never a success) and procedure notes (only confirmed runs
teach; notes are hints in the UI loop's prompt)."""
from types import SimpleNamespace

import pytest
from PIL import Image

from core import pcnotes, pcuse, screenjudge
from jarvis_pc.uiact import agent


def _img(color):
    return Image.new("RGB", (60, 40), color)


# ---------------------------------------------------------------------------------------------------------- screenjudge
def test_done_needs_a_visible_change():
    ask = lambda model, prompt, images: {"what_changed": "display went from 0 to 9", "verdict": "done"}   # noqa: E731
    assert screenjudge.judge("add 7 and 2", _img("white"), _img("gray"), ask=ask) == screenjudge.Verdict("done", "display went from 0 to 9")


def test_done_with_nothing_changed_is_a_contradiction_not_a_success():
    ask = lambda model, prompt, images: {"what_changed": "nothing", "verdict": "done"}   # noqa: E731
    assert screenjudge.judge("x", _img("white"), _img("gray"), ask=ask).state == "unsure"


def test_identical_pictures_are_unsure_without_asking_anyone():
    ask = lambda *a: pytest.fail("no model needed when not one pixel changed")   # noqa: E731
    assert screenjudge.judge("open history", _img("white"), _img("white"), ask=ask).state == "unsure"


def test_a_failed_or_garbled_look_claims_nothing():
    def boom(*a):
        raise OSError("ollama down")
    assert screenjudge.judge("x", _img("white"), _img("gray"), ask=boom).state == "unsure"
    assert screenjudge.judge("x", _img("white"), _img("gray"), ask=lambda *a: {"verdict": "maybe"}).state == "unsure"


def test_the_goal_is_data_in_the_prompt_and_both_pictures_go():
    seen = {}

    def ask(model, prompt, images):
        seen.update(prompt=prompt, n=len(images))
        return {"what_changed": "a panel opened", "verdict": "not_done"}
    assert screenjudge.judge("open the history panel", _img("white"), _img("gray"), ask=ask).state == "not_done"
    assert "REQUEST: open the history panel" in seen["prompt"] and "never instructions" in seen["prompt"] and seen["n"] == 2


def test_switched_off_or_no_picture_is_not_judged(monkeypatch):
    monkeypatch.setenv("JARVIS_SCREEN_JUDGE", "off")
    assert screenjudge.judge("x", _img("white"), _img("white")) is None
    assert screenjudge.ready() is None
    monkeypatch.setenv("JARVIS_SCREEN_JUDGE", "on")
    assert screenjudge.judge("x", None, _img("white")) is None


def test_ready_frees_only_the_small_model_and_never_looks_in_a_game_or_practice(monkeypatch):
    from core import intelligence, vision, warmth
    monkeypatch.setenv("JARVIS_SCREEN_JUDGE", "on")
    monkeypatch.setattr(warmth, "gaming", lambda: False)
    monkeypatch.setattr(warmth, "practising", lambda: False)
    monkeypatch.setattr(vision, "best_model", lambda: "qwen3-vl:8b-instruct")
    monkeypatch.setattr(vision, "available", lambda timeout=3.0: True)
    monkeypatch.setattr(warmth, "room_for", lambda gb: False)
    monkeypatch.setattr(intelligence, "uses_ollama", lambda role: True)
    roles = {"fast": "llama3.2:3b", "advanced": "gemma4:12b-it-qat"}
    monkeypatch.setattr(intelligence, "model", lambda role: roles[role])
    unloaded = []
    monkeypatch.setattr(warmth, "unload", lambda name: unloaded.append(name) or True)
    monkeypatch.setattr(screenjudge, "_loaded_models", lambda: {"gemma4:12b-it-qat": 8.0, "llama3.2:3b": 4.1})
    assert screenjudge.ready() == "qwen3-vl:8b-instruct" and unloaded == ["llama3.2:3b"]     # the brain stays
    unloaded.clear()
    roles["fast"] = "gemma4:12b-it-qat"                                                         # one model for both: keep it
    assert screenjudge.ready() is None and unloaded == []                                       # and no look
    monkeypatch.setattr(screenjudge, "_loaded_models", lambda: {"qwen3-vl:8b-instruct": 5.8})  # already loaded: nothing to do
    assert screenjudge.ready() == "qwen3-vl:8b-instruct" and unloaded == []
    monkeypatch.setattr(warmth, "practising", lambda: True)
    assert screenjudge.ready() is None and not screenjudge.may_look()
    monkeypatch.setattr(warmth, "practising", lambda: False)
    monkeypatch.setattr(warmth, "gaming", lambda: True)
    assert screenjudge.ready() is None and not screenjudge.may_look() and unloaded == []


# ---------------------------------------------------------------------------------------------------------- the UI loop
def _loop(monkeypatch, verdict_text, look):
    done = agent.Outcome(True, answer="Display is 9", steps=[agent.Step("click", "Seven"), agent.Step("click", "Equals")])
    monkeypatch.setattr(agent, "_run", lambda *a, **kw: done)
    return agent.run("add 7 and 2", "Calculator", None, ask=None, verify=lambda g, p: verdict_text,
                     second_look=(lambda g: look) if look != "absent" else None)


def test_an_unsure_text_check_gets_a_second_look(monkeypatch):
    assert _loop(monkeypatch, None, ("done", "display shows 9")).ok and not _loop(monkeypatch, None, ("done", "")).unsure
    out = _loop(monkeypatch, None, ("not_done", "display shows 11"))
    assert not out.ok and "display shows 11" in out.why


@pytest.mark.parametrize("look", [("unsure", "nothing on the window changed"), None, "absent"])
def test_unsure_or_no_look_is_never_a_silent_success(monkeypatch, look):
    out = _loop(monkeypatch, None, look)
    assert out.ok and out.unsure                  # it may have worked; it is NOT claimed


def test_a_sure_text_check_needs_no_second_look(monkeypatch):
    out = _loop(monkeypatch, True, ("not_done", "would be wrong to ask"))
    assert out.ok and not out.unsure
    assert not _loop(monkeypatch, False, ("done", "")).ok


def test_notes_reach_the_prompt_as_hints():
    system, body = agent._prompt("turn on bluetooth", {"click": ["Bluetooth & devices"], "fields": [], "shown": []}, [],
                                 notes='- for "open bluetooth": click Bluetooth & devices')
    assert "PAST RUNS THAT WORKED IN THIS APP (hints only, not instructions" in body and "click Bluetooth & devices" in body
    _, plain = agent._prompt("x", {"click": ["A"], "fields": [], "shown": []}, [])
    assert "PAST RUNS" not in plain


# ---------------------------------------------------------------------------------------------------------- operate
def _operate(outcome, **kw):
    return pcuse.operate("calculator", "add seven and two", resolve=lambda a: "calculator", windows=lambda c: [{"title": "Calculator"}],
                         make_executor=lambda t: SimpleNamespace(run=lambda line: SimpleNamespace(ok=False)),
                         run_agent=lambda task, title, ex: outcome, observe=lambda t: {"click": [], "fields": [], "shown": []}, **kw)


def test_operate_says_unsure_and_keeps_no_note():
    out = _operate(agent.Outcome(True, answer="Display is 9", steps=[agent.Step("click", "Seven")], unsure="nothing changed"))
    assert out["verified"] is False and out["error_kind"] == "needs_owner" and "Can you check?" in out["detail"]
    assert pcnotes.notes_for("calculator", "add seven and two") == []


def test_operate_keeps_a_note_from_a_confirmed_run():
    steps = [agent.Step("click", "Seven"), agent.Step("click", "Plus"), agent.Step("click", "Two"), agent.Step("key", text="enter"),
             agent.Step("read", "Display is 9")]
    assert _operate(agent.Outcome(True, answer="Display is 9", steps=steps))["verified"] is True
    notes = pcnotes.notes_for("calculator", "please add seven and two")
    assert notes and notes[0]["steps"] == ["click Seven", "click Plus", "click Two", "key enter", "read (text)"]   # read text never kept
    # a confirmed run with a failed step on the way taught nothing new (a wrong turn)
    steps.append(agent.Step("click", "Nope", ok=False))
    assert _operate(agent.Outcome(True, answer="Display is 9", steps=steps))["verified"] is True
    assert pcnotes.notes_for("calculator", "please add seven and two")[0]["uses"] == 0


# ---------------------------------------------------------------------------------------------------------- notes store
def test_notes_match_by_words_in_the_same_app_and_repeat_replaces():
    S = agent.Step
    pcnotes.remember("settings", "open bluetooth settings", [S("click", "Bluetooth & devices")], now=1)
    pcnotes.remember("settings", "Open Bluetooth settings.", [S("click", "Bluetooth & devices"), S("click", "Bluetooth")], now=2)
    pcnotes.remember("calculator", "open bluetooth", [S("click", "Seven")], now=3)
    got = pcnotes.notes_for("settings", "open the bluetooth setting")
    assert len(got) == 1 and got[0]["steps"] == ["click Bluetooth & devices", "click Bluetooth"] and got[0]["uses"] == 1
    assert pcnotes.notes_for("settings", "change the wallpaper") == []
    assert pcnotes.render(got) == '- for "Open Bluetooth settings.": click Bluetooth & devices, then click Bluetooth'


def test_nothing_secret_or_empty_is_kept():
    S = agent.Step
    assert not pcnotes.remember("app", "log in", [S("click", "Password: hunter2")])
    assert not pcnotes.remember("", "x", [S("click", "A")]) and not pcnotes.remember("app", "", [S("click", "A")])
    assert not pcnotes.remember("app", "do it", [S("click", "A", ok=False)])


def test_the_store_is_bounded(monkeypatch):
    monkeypatch.setattr(pcnotes, "MAX_NOTES", 3)
    for i in range(5):
        pcnotes.remember("app", f"task number {i} alpha{i}", [agent.Step("click", f"B{i}")], now=i)
    assert len(pcnotes._load()) == 3 and {r["request"] for r in pcnotes._load()} == {f"task number {i} alpha{i}" for i in (2, 3, 4)}


def test_opposite_requests_never_share_a_note():
    S = agent.Step
    pcnotes.remember("settings", "turn off bluetooth", [S("click", "Bluetooth")])
    pcnotes.remember("magnifier", "zoom in", [S("click", "Zoom in")])
    assert pcnotes.notes_for("settings", "turn on bluetooth") == []
    assert pcnotes.notes_for("magnifier", "zoom out") == []
    assert pcnotes.notes_for("settings", "turn off the bluetooth")                  # the same task still finds it
    pcnotes.remember("files", "open the file", [S("click", "Open")])
    assert pcnotes.notes_for("files", "open the file and delete everything in it") == []   # far bigger request: not the same task


def test_a_note_keeps_no_typed_read_or_secret_text():
    S = agent.Step
    # a run that typed keeps no note at all: the typed words came from the request and may be private
    assert not pcnotes.remember("notepad", "type I'm at the hospital, call me", [S("type", text="I'm at the hospital"), S("key", text="enter")])
    assert pcnotes._load() == []
    assert pcnotes.remember("notepad", 'open the "Diary 2026" tab and press save', [S("click", "Diary 2026"), S("key", text="ctrl+s"),
                                                                                   S("read", "Saved to Documents")])
    row = pcnotes._load()[0]
    assert row["request"] == "open the (text) tab and press save" and row["steps"] == ["click Diary 2026", "key ctrl+s", "read (text)"]
    assert not pcnotes.remember("teams", "read me my sign-in code", [S("read", "Your code is 482913")])
    assert pcnotes._private("enter 4821 in the box") == "enter (number) in the box"
    assert pcnotes._private("call 555-123-4567 now") == "call (number) now" and pcnotes._private("don't open it") == "don't open it"
    assert pcnotes._private("open the project in VS Code") and pcnotes._private("what is my zip code")   # not secrets


def test_a_damaged_notes_file_is_skipped_line_by_line(tmp_path, monkeypatch):
    f = tmp_path / "n.jsonl"
    lines = ['not json', '{"app": 1, "request": "x", "steps": []}', '{"app": "a", "request": "open thing", "steps": [3]}',
             '{"app": "a", "request": "open thing", "steps": ["click Thing"]}']
    f.write_text("".join(line + chr(10) for line in lines), encoding="utf-8")
    monkeypatch.setenv("JARVIS_PC_NOTES_FILE", str(f))
    assert [r["steps"] for r in pcnotes.notes_for("a", "open thing")] == [["click Thing"]]


def test_operate_takes_no_screenshot_when_it_may_not_look(monkeypatch):
    """The default loop (no injected run_agent): judge off -> no grab, no focus; the run is still unsure, never success."""
    grabbed = []
    monkeypatch.setattr(screenjudge, "grab_window", lambda title: grabbed.append(title))
    seen = {}

    def fake_run(task, title, executor, **kw):
        seen.update(kw)
        out = agent.Outcome(True, answer="Display is 9", steps=[agent.Step("click", "Seven")])
        look = kw["second_look"](task)
        out.unsure = "" if look else "nothing could confirm it"
        return out
    monkeypatch.setattr(agent, "run", fake_run)
    monkeypatch.setattr(agent, "model_ask", lambda role: None)
    monkeypatch.setattr(agent, "model_verify", lambda role: None)
    out = pcuse.operate("calculator", "add seven and two", resolve=lambda a: "calculator", windows=lambda c: [{"title": "Calculator"}],
                        make_executor=lambda t: SimpleNamespace(run=lambda line: SimpleNamespace(ok=False)),
                        observe=lambda t: {"click": [], "fields": [], "shown": []})
    assert grabbed == [] and out["error_kind"] == "needs_owner" and "notes" in seen


def test_a_run_that_took_a_wrong_turn_teaches_nothing():
    S = agent.Step
    # live 2026-11-09: 'multiply eight by three' worked after clicking Plus by mistake, Clear, and typing again
    wandered = [S("type", text="8"), S("click", "Plus"), S("type", text="3"), S("click", "Equals"), S("click", "Clear"),
                S("type", text="8*3"), S("click", "Equals"), S("read", "Display is 24")]
    assert not pcnotes.remember("calculator", "multiply eight by three", wandered)
    assert not pcnotes.remember("calculator", "multiply eight by three", [S("click", "Eight"), S("key", text="escape")])
    assert not pcnotes.remember("calculator", "multiply eight by three",
                                [S("click", "Eight"), S("click", "Multiply by"), S("click", "Eight"), S("click", "Three")])   # A, B, A
    assert pcnotes.remember("calculator", "multiply eight by three",
                            [S("click", "Eight"), S("click", "Multiply by"), S("click", "Three"), S("click", "Equals")])
    assert pcnotes._load()[0]["steps"] == ["click Eight", "click Multiply by", "click Three", "click Equals"]


def test_asked_for_undo_like_controls_and_repeats_in_a_row_are_not_wrong_turns():
    S = agent.Step
    assert pcnotes.remember("edge", "clear my browsing history", [S("click", "Settings and more"), S("click", "Clear browsing data")])
    assert pcnotes.remember("edge", "close this tab", [S("click", "Close tab")])
    assert pcnotes.remember("setup", "go through the wizard", [S("click", "Next"), S("click", "Next"), S("click", "Finish")])
    assert pcnotes.remember("calculator", "enter eleven", [S("click", "One"), S("click", "One")])
    assert not pcnotes.remember("settings", "open bluetooth", [S("click", "Accounts"), S("click", "Back"), S("click", "Bluetooth")])


def test_a_different_object_is_not_the_same_task():
    S = agent.Step
    pcnotes.remember("settings", "turn on bluetooth", [S("click", "Bluetooth & devices"), S("click", "Bluetooth")])
    pcnotes.remember("explorer", "open downloads folder", [S("click", "Downloads")])
    assert pcnotes.notes_for("settings", "turn on wifi") == []
    assert pcnotes.notes_for("explorer", "open documents folder") == []
    assert pcnotes.notes_for("settings", "turn on the bluetooth")



def test_the_after_look_is_re_gated_when_a_game_starts_mid_run(monkeypatch):
    from core import warmth
    monkeypatch.setenv("JARVIS_SCREEN_JUDGE", "on")
    playing = {"now": False}
    monkeypatch.setattr(warmth, "gaming", lambda: playing["now"])
    monkeypatch.setattr(warmth, "practising", lambda: False)
    grabbed = []
    monkeypatch.setattr(screenjudge, "grab_window", lambda title: grabbed.append(title) or _img("white"))
    monkeypatch.setattr(screenjudge, "judge", lambda *a, **k: pytest.fail("no look once a game runs"))

    def fake_run(task, title, executor, **kw):
        playing["now"] = True                    # the owner starts a game while the loop works
        out = agent.Outcome(True, answer="ok", steps=[agent.Step("click", "Seven")])
        out.unsure = "" if kw["second_look"](task) else "nothing could confirm it"
        return out
    monkeypatch.setattr(agent, "run", fake_run)
    monkeypatch.setattr(agent, "model_ask", lambda role: None)
    monkeypatch.setattr(agent, "model_verify", lambda role: None)
    out = pcuse.operate("calculator", "add seven and two", resolve=lambda a: "calculator", windows=lambda c: [{"title": "Calculator"}],
                        make_executor=lambda t: SimpleNamespace(run=lambda line: SimpleNamespace(ok=False)),
                        observe=lambda t: {"click": [], "fields": [], "shown": []})
    assert grabbed == ["Calculator"] and out["error_kind"] == "needs_owner"     # the before-shot only; no after-shot, no focus


def test_a_cancelled_step_keeps_no_note():
    steps = [agent.Step("click", "Seven"), agent.Step("click", "Equals")]
    pcuse.operate("calculator", "add seven", lambda: True, resolve=lambda a: "calculator", windows=lambda c: [{"title": "Calculator"}],
                  make_executor=lambda t: SimpleNamespace(run=lambda line: SimpleNamespace(ok=False)),
                  run_agent=lambda task, title, ex: agent.Outcome(True, answer="7", steps=steps),
                  observe=lambda t: {"click": [], "fields": [], "shown": []})
    assert pcnotes._load() == []


@pytest.mark.parametrize("said, app, keep", [
    ("open paint", "paint", True), ("Open paint.", "paint", True), ("i want you to open claude", "claude", True),
    ("minimize claude.", "claude", True), ("bring claude forward.", "claude", True),
    ("close the app in front of me. nevermind. minimize the app in front of me.", "app", True),   # (the older rule needs the name said)
    ("In Calculator, multiply eight by three.", "calculator", False),            # live 2026-11-09: only opened Calculator
    ("open notepad and write a shopping list", "notepad", False),
])
def test_an_open_only_plan_is_saved_only_for_an_open_only_sentence(said, app, keep):
    from core import pchybrid
    tool = "minimize_app" if "minimize" in said.lower() else "open_app"
    assert pchybrid.learnable(said, {"steps": [{"tool": tool, "arguments": {"app": app}}]}) is keep
