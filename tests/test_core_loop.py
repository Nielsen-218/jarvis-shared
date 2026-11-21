"""The core loop on his own words (2026-11-16 core scorecard): the voice loop's text clean-up before routing."""
import pytest

import main


@pytest.mark.parametrize("heard,command", [
    ("Hey Jarvis. Hey Jarvis. Minimize Claude.", "Minimize Claude."),
    ("Hey Jarvis, open Claude.", "open Claude."),
    ("Jarvis. Open ChatGPT.", "Open ChatGPT."),
    ("Minimize Claude.", "Minimize Claude."),
    ("Jarvis", ""),
    ("Tell Jarvis I said hi", "Tell Jarvis I said hi"),
])
def test_every_wake_word_in_front_is_stripped(heard, command):
    assert main.without_wake(heard) == command


# -- the Command Center as he says it and as it gets heard -------------------------------------------------------------
@pytest.mark.parametrize("said", ["Open Community Centre.", "Open Comment Center.", "Opinion Command Center.", "Command. Center.",
                                  "Enjoy. Open comments into.", "Hey, go ahead and open up the hub.", "Open your command center."])
def test_command_center_mishears_open_it(said):
    from core import hud
    assert hud._cc_heard(said)


@pytest.mark.parametrize("said", ["the community centre opens at 9", "I like the hub", "What is the hub?",
                                  "Is the command center open?", "Close the command center", "Open comments in Word",
                                  "Leave a comment on the center line"])
def test_talk_about_centres_is_not_a_screen_request(said):
    from core import hud
    assert not hud._cc_heard(said)


def test_bring_it_forward_means_the_jarvis_window_just_opened(monkeypatch):
    from core import hud
    hud._opened("")
    assert hud._bring_forward("Bring it forward.") == ""            # nothing of Jarvis's opened: the PC layer decides
    assert hud._bring_forward("Bring World Intelligence to the front.") == "World Intelligence"
    hud._opened("the Command Center")
    assert hud._bring_forward("Bring it forward.") == "the Command Center"
    assert hud._bring_forward("I don't see it.") == "the Command Center"
    assert hud._bring_forward("It's not.") == ""
    hud._opened("")


def test_a_window_behind_his_app_is_not_claimed_up(monkeypatch):
    from core import hud
    sent = []
    monkeypatch.setattr(hud, "_send", lambda m: sent.append(m["type"]))
    monkeypatch.setattr(hud, "running", lambda: True)
    monkeypatch.setattr(hud, "in_front", lambda timeout=2.0: False)
    out = hud.own_screen("Open the Command Center.")
    assert "isn't in front" in out and "is up" not in out
    assert sent.count("front") == 1                                  # one more push before saying so
    monkeypatch.setattr(hud, "in_front", lambda timeout=2.0: True)
    assert hud.own_screen("Bring it forward.") == "The Command Center is in front now, sir."
    hud._opened("")


# -- the globe ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("place,title", [("the United States", "United States"), ("USA", "United States"),
                                         ("America", "United States"), ("the UK", "United Kingdom")])
def test_countries_are_found_without_the_town_geocoder(place, title):
    from core import worldintel
    assert worldintel.search_place(place)["items"][0]["title"] == title


# -- app names ---------------------------------------------------------------------------------------------------------
def test_edge_browser_is_edge_not_the_default_browser(monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "_installed", lambda: {"microsoft edge": "msedge.exe", "steam": "steam.exe"})
    assert apps.resolve_app("Edge Browser") == "microsoft edge"
    assert apps.resolve_app("the steam app".removeprefix("the ")) == "steam"
    assert apps.resolve_app("browser") == "browser"


# -- the replay's scoring (scripts/core_replay.py: pure) ---------------------------------------------------------------
def _result(**kw):
    base = {"steps": [], "brain_tools": [], "skills": [], "questions": [], "screen": [], "world": [], "front": 0, "reply": ""}
    base.update(kw)
    return base


def test_replay_scoring():
    from scripts import core_replay as cr
    minimize = {"expect": {"act": "minimize", "app": "claude", "forbid": ["close"]}}
    assert cr.score(minimize, _result(steps=[{"tool": "minimize_app", "arguments": {"app": "claude"}}]))[0]
    assert not cr.score(minimize, _result(steps=[{"tool": "close_app", "arguments": {"app": "claude"}},
                                                 {"tool": "minimize_app", "arguments": {"app": "claude"}}]))[0]
    assert not cr.score(minimize, _result(steps=[{"tool": "minimize_app", "arguments": {"app": "notepad"}}]))[0]
    tab = {"expect": {"act": "close_tab"}}
    assert cr.score(tab, _result(skills=["skill.close_site_tab"]))[0]
    ram = {"expect": {"say": r"\d.*GB.*free"}}
    assert cr.score(ram, _result(reply="11.5 GB free", brain_tools=[{"tool": "get_system_status", "arguments": {}, "read_only": True}]))[0]
    assert not cr.score(ram, _result(reply="31.5 GB of memory."))[0]
    assert cr.score({"expect": {"none": True}}, _result())[0]
    assert cr.score({"expect": {"world": "goto", "arg": "haiti"}}, _result(world=[("goto", {"title": "Haiti"})]))[0]


def test_replay_scoring_tool_kinds():
    from scripts import core_replay as cr
    typing = {"expect": {"tools": "type_text", "arg": "i am here"}}
    assert cr.score(typing, _result(steps=[{"tool": "type_text", "arguments": {"app": "notepad", "text": "I am here"}}]))[0]
    assert not cr.score(typing, _result(steps=[{"tool": "type_text", "arguments": {"app": "notepad", "text": "hello"}}]))[0]
    assert not cr.score(typing, _result(steps=[{"tool": "open_app", "arguments": {"app": "notepad"}}]))[0]
    both = {"expect": {"tools": ["type_text", "save_document"]}}
    assert not cr.score(both, _result(steps=[{"tool": "type_text", "arguments": {"text": "x"}}]))[0]
    assert cr.score(both, _result(steps=[{"tool": "type_text", "arguments": {}}, {"tool": "save_document", "arguments": {}}]))[0]
    pause = {"expect": {"tools": "media_command", "arg": "pause", "forbid": ["close"]}}
    assert cr.score(pause, _result(brain_tools=[{"tool": "media_command", "arguments": {"command": "pause"}}]))[0]
    assert not cr.score(pause, _result(steps=[{"tool": "media_command", "arguments": {"command": "pause"}},
                                              {"tool": "close_app", "arguments": {"app": "edge"}}]))[0]
    site_tab = {"expect": {"act": "close_tab", "app": "youtube"}}
    assert cr.score(site_tab, _result(steps=[{"tool": "close_site_tab", "arguments": {"site": "YouTube"}}]))[0]
    gpu = {"expect": {"say": "RTX", "or_read": "gfx_gpu_name"}}
    assert cr.score(gpu, _result(skills=["gfx_gpu_name"], reply="Done."))[0]
    assert not cr.score(gpu, _result(skills=["pc_ram"], reply="Done."))[0]
    assert not cr.score(gpu, _result(skills=["gfx_gpu_name"], steps=[{"tool": "close_app", "arguments": {"app": "x"}}]))[0]
    sleep = {"expect": {"none": True, "pre": ["dismiss"]}}
    assert cr.score(sleep, _result(pre="dismiss"))[0]
    assert not cr.score(sleep, _result(pre="respond"))[0]


# -- test traffic is marked where it is written --------------------------------------------------------------------------
def test_verification_turns_are_marked_test(monkeypatch, tmp_path):
    import json

    from core import gateway, pcbridge, storage, turntrace
    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    turntrace.record("voice", "Open Claude.", 1.0, 0.2, 0.5)
    turntrace.record("phone", "What time is it?", 2.0, 0.1, 0.1, test=True, device="000000")
    gateway.TEST_TURN.on = True
    try:
        turntrace.record("phone", "storm watch", 3.0, 0.1, 0.1)
    finally:
        gateway.TEST_TURN.on = False
    monkeypatch.setenv("JARVIS_TEST_TRAFFIC", "1")
    turntrace.record("voice", "replayed line", 4.0, 0.1, 0.1)
    rows = [json.loads(l) for l in (tmp_path / "learning" / "turn-timing-pc.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r.get("test", False) for r in rows] == [False, True, True, True]
    assert rows[1]["device"] == "000000" and "device" not in rows[0]


# -- the core scorecard (core/scorecard.py) ------------------------------------------------------------------------------
@pytest.mark.parametrize("said,kind", [
    ("Minimize Notepad.", "minimize"), ("Hey Jarvis, open Spotify.", "open"), ("Get rid of Paint.", "close"),
    ("Bring Notepad forward.", "focus"), ("Open the dashboard.", "screen"), ("Zoom in.", "globe"), ("Go to France.", "globe"),
    ("How much RAM is free?", "pc_question"), ("What time is it?", "info"), ("Open Paint. Never mind.", "cancel"),
    ("Go to sleep.", "other"), ("I think Blade Runner is better than Alien.", "other"), ("You have a short-term memory, huh?", "other"),
])
def test_scorecard_kinds(said, kind):
    from core import scorecard
    assert scorecard.kind(said) == kind


@pytest.mark.parametrize("kind,reply,out", [
    ("open", "Opened notepad.", "done"), ("open", "Stopped. Opened spotify.", "done"), ("close", "Should I close Paint?", "asked"),
    ("minimize", "Understood, sir.", "talk"), ("open", "I lost my train of thought there, sir. Could you say it once more?", "not_understood"),
    ("screen", "World Intelligence didn't confirm, sir.", "failed"), ("pc_question", "11.5 GB of memory is free.", "done"),
    ("pc_question", "I don't have a live list of that.", "talk"), ("open", "", "silent"),
    ("screen", "I opened the Command Center, but it isn't in front of your other windows, sir.", "failed"),
])
def test_scorecard_outcomes(kind, reply, out):
    from core import scorecard
    assert scorecard.outcome(kind, reply) == out


def _turn(ts, said, reply, channel="voice"):
    from core import scorecard
    k = scorecard.kind(said)
    return {"ts": ts, "channel": channel, "said": said, "kind": k, "outcome": scorecard.outcome(k, reply), "mishear": False}


def test_scorecard_first_try_counts_repeats_and_complaints():
    from core import scorecard
    rows = [_turn(0, "Minimize Notepad.", "Closed notepad."), _turn(10, "That's wrong, I said minimize.", "Sorry."),
            _turn(100, "Open Paint.", "Which app?"), _turn(110, "Open Paint.", "Opened paint."),
            _turn(400, "Zoom in.", "Zoomed in."), _turn(405, "Zoom in.", "Zoomed in."),
            _turn(900, "What time is it?", "It's 3:10 PM."), _turn(905, "What day is it?", "It's Sunday, November 15."),
            _turn(2000, "Tell me a joke.", "Here's one.")]
    c = scorecard.card(rows)
    assert c["commands"] == 7 and c["turns"] == 9
    assert c["first_try"] == [5, 7]            # the minimize (complaint) and the first 'Open Paint.' (asked, repeated) failed
    assert c["repeats"] == 1                    # zooming twice and time-then-day are not repeats
    assert c["by_kind"]["open"] == [1, 2] and c["by_kind"]["globe"] == [2, 2]


def test_scorecard_notes_only_his_real_turns(monkeypatch, tmp_path):
    from core import gateway, scorecard, storage, users
    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setattr(scorecard, "_host", lambda: "pc")
    monkeypatch.setattr(users, "get", lambda uid: {"role": "owner" if uid == "owner" else "member"})
    scorecard.note("owner", "Open Notepad.", "Opened notepad.", "voice", now=100.0)
    scorecard.note("maya", "Open Notepad.", "Opened notepad.", "voice", now=101.0)       # not his
    gateway.TEST_TURN.on = True
    try:
        scorecard.note("owner", "Open Paint.", "Opened paint.", "phone", now=102.0)           # a verification device
    finally:
        gateway.TEST_TURN.on = False
    monkeypatch.setenv("JARVIS_TEST_TRAFFIC", "1")
    scorecard.note("owner", "Open Paint.", "Opened paint.", "voice", now=103.0)              # a harness
    rows = scorecard.turns(("pc",))
    assert [(r["said"], r["kind"], r["outcome"]) for r in rows] == [("Open Notepad.", "open", "done")]


def test_review_fixes_core_loop():
    from core import hud, scorecard
    assert main.without_wake("Hey Jarvis. " * 4 + "Minimize Claude.") == "Minimize Claude."
    assert not hud._cc_heard("Community Center.") and not hud._cc_heard("The community center.")
    assert hud._cc_heard("Command Center.")
    assert scorecard.kind("Show me the weather in Paris") == "other" and scorecard.kind("start a timer") == "reminder"
    assert scorecard.outcome("open", "Opening Notepad now.") == "done"
