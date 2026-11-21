"""Project Reliability, batch 1: the held-out benchmark's misses (data/learning/core-holdout.json), each fixed at its root and
pinned here with real-style lines both ways (the fix fires on his line; nearby conversation and PC requests keep their route).
The model is faked where one is involved: these prove the code paths, the replay proves the model."""
from pathlib import Path

import pytest

import main
from core import gateway, heard, intelligence, pcactions, pchybrid, pcplanner, webintent
from tools import apps
from tests.test_gateway import ctx_for, reply


# ---------------------------------------------------------------- 1. kind "skill" with no skill id but valid steps (h008, h014)
def test_a_skill_with_no_id_but_valid_steps_is_checked_as_actions():
    c = ctx_for("How many apps are on the screen right now?")
    intents = gateway.validate(reply({"kind": "skill", "summary": "Count open windows",
                                      "steps": [{"tool": "list_windows", "arguments": {}}]}), c)
    assert [(i.kind, [s["tool"] for s in i.steps]) for i in intents] == [("actions", ["list_windows"])]


def test_a_skill_with_no_id_and_no_steps_is_still_refused():
    with pytest.raises(ValueError, match="skill not offered"):
        gateway.validate(reply({"kind": "skill", "summary": "storage"}), ctx_for("show me the storage that i have on my pc"))


def test_a_skill_named_but_not_offered_is_still_refused_even_with_steps():
    with pytest.raises(ValueError, match="skill not offered"):
        gateway.validate(reply({"kind": "skill", "skill": "pc_bogus", "steps": [{"tool": "pc_status", "arguments": {}}]}),
                         ctx_for("show me the storage that i have on my pc"))


def test_an_empty_decision_never_answers_done(monkeypatch):
    from core import pchelp
    monkeypatch.setattr(pchelp, "next_plan", lambda said: None)
    out = gateway._execute(gateway.Decision([], "none", covers=False, attempts=["advanced: skill not offered"] * 2),
                           "How many apps are on the screen right now?", lambda: False, "owner", None)
    assert out and out.strip(" .").casefold() != "done"
    assert "haven't" in out.casefold() or "don't have" in out.casefold() or "won't guess" in out.casefold()


# ---------------------------------------------------------------- 2. a tab named by its title (h009)
@pytest.mark.parametrize("site, want", [("um usb to ttl", "usb to ttl"), ("my usb to ttl tab", "usb to ttl"),
                                         ("youtube.com", "youtube"), ("the github page", "github")])
def test_a_tab_named_by_its_title_is_a_valid_close(site, want):
    plan = pcplanner.canonical({"steps": [{"tool": "close_site_tab", "arguments": {"site": site}}]})
    pcactions.validate(plan)
    assert plan["steps"][0]["arguments"]["site"] == want


@pytest.mark.parametrize("site", ["all", "browser", "edge", "tabs", "x", "!!!", "it"])
def test_a_vague_tab_name_is_refused(site):
    with pytest.raises(ValueError):
        pcactions.validate(pcplanner.canonical({"steps": [{"tool": "close_site_tab", "arguments": {"site": site}}]}))


def test_the_open_site_list_is_unchanged_for_other_tools():
    with pytest.raises(ValueError):
        pcactions.validate({"steps": [{"tool": "browser_search", "arguments": {"site": "usb to ttl", "query": "x"}}]})


# ---------------------------------------------------------------- 3. "close close github" (h041), "close all files" (h034)
@pytest.mark.parametrize("line, want", [("close close github", "close github"), ("Close, close Notepad.", "Close Notepad."),
                                         ("Jarvis, open open Spotify", "Jarvis, open Spotify")])
def test_a_doubled_command_verb_is_one(line, want):
    assert heard.normalise(line) == want


@pytest.mark.parametrize("line", ["closer close", "close the door, close it", "open opener", "close closet", "play it, play it again"])
def test_words_that_only_look_doubled_are_left_alone(line):
    assert heard.normalise(line) == line


def test_close_close_github_closes_the_github_tab_not_an_app():
    plan = pchybrid.simple_plan(heard.normalise("close close github"))
    assert plan["steps"] == [{"tool": "close_site_tab", "arguments": {"site": "github"}}]


@pytest.mark.parametrize("name", ["github", "close github", "youtube", "netflix"])
def test_a_website_name_never_fuzzy_matches_an_app(monkeypatch, name):
    monkeypatch.setattr(apps, "_installed", lambda: {"git gui": Path("g.lnk"), "logitune": Path("l.lnk"), "netflix desktop": Path("n.lnk")})
    assert apps.resolve_app(name) == ("netflix desktop" if name == "netflix" else None)


def test_real_apps_still_resolve(monkeypatch):
    monkeypatch.setattr(apps, "_installed", lambda: {"logitune": Path("l.lnk"), "github desktop": Path("gd.lnk")})
    assert apps.resolve_app("github") == "github desktop"       # an installed app that really has the name
    assert apps.resolve_app("logi tune") == "logitune"           # a close spelling of an app is still that app
    assert apps.resolve_app("calc") == "calculator"


@pytest.mark.parametrize("line", ["close all files", "close files", "close all the files", "close file explorer", "close all folders"])
def test_close_all_files_means_file_explorer(line):
    assert pchybrid.simple_plan(line)["steps"] == [{"tool": "close_app", "arguments": {"app": "file explorer"}}]


def test_closing_files_closes_folder_windows_only_never_a_process(monkeypatch):
    import subprocess
    import sys
    import types
    from jarvis_pc.guardrails import stepfence
    posted, killed = [], []
    monkeypatch.setattr(stepfence, "require_foreground", lambda what: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: killed.append(a))
    rows = [{"hwnd": 1, "process": "explorer.exe", "title": "Downloads"}, {"hwnd": 2, "process": "explorer.exe", "title": "Program Manager"},
            {"hwnd": 3, "process": "FileSyncHelper.exe", "title": "files"}]
    classes = {1: "CabinetWClass", 2: "Progman", 3: "Other"}
    gui = types.SimpleNamespace(GetClassName=lambda h: classes[h], PostMessage=lambda h, m, a, b: posted.append(h))
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(WM_CLOSE=16))
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda: rows)
    for name in ("files", "file explorer"):
        posted.clear()
        assert apps.close_app(name) == "Asked 1 File Explorer window(s) to close."
        assert posted == [1]
    assert killed == []


# ---------------------------------------------------------------- 4. "check for / look for" is a web search (h055)
@pytest.mark.parametrize("line", ["Go ahead and check for Halal food, Haitian food, Caribbean food.", "Look for cheap flights to Miami.",
                                  "Can you check for vegan restaurants in Norfolk?"])
def test_check_for_something_in_the_world_is_a_search(line):
    assert webintent.kind(line) == "search"
    assert webintent.search_plan(line)[0] and not webintent.search_plan(line)[0][0].casefold().startswith(("check", "look"))


@pytest.mark.parametrize("line", ["check for updates", "Check for Windows updates.", "check my CPU", "look for my keys", "check for viruses",
                                  "Look for the save button.", "I will check for it later", "look for notepad", "check for new emails",
                                  "Check for errors in the log", "check for that"])
def test_check_for_pc_work_or_his_things_is_not_a_search(line):
    assert webintent.kind(line) is None


# ---------------------------------------------------------------- 5. "go to sleep" as its own sentence (h057)
def _route(line):
    command = main.fix_misheard(main.without_wake(line))
    return main.pre_brain_route(command.lower().strip(" .!?,"), [], meaning=lambda n: None)


@pytest.mark.parametrize("line", ["Jarvis, go to sleep. I'm not asking anything from you. Okay.", "go to sleep now. Thanks.",
                                  "Okay, go to sleep. Good night.", "go to sleep"])
def test_go_to_sleep_with_trailing_talk_dismisses(line):
    assert _route(line) == "dismiss"


@pytest.mark.parametrize("line", ["Go to sleep. Open Notepad first.", "Go to sleep? Why would I.", "I want to go to sleep. Turn the lights off."])
def test_go_to_sleep_with_a_request_or_question_after_it_is_answered(line):
    assert _route(line) == "respond"


def test_the_globe_never_flies_to_sleep(monkeypatch):
    from core import worldintel
    monkeypatch.setattr(worldintel, "workspace_open", lambda: True)
    monkeypatch.setattr(worldintel, "search_place", lambda p: pytest.fail(f"searched the map for {p!r}"))
    monkeypatch.delenv("JARVIS_ROLE", raising=False)
    for line in ("go to sleep. I'm not asking anything from you. Okay", "go to bed", "Go to sleep."):
        assert worldintel.voice(line) is None


# ---------------------------------------------------------------- 6. "we zoom YouTube" (h004), "Pause YouTube." inside a tutorial (h002)
@pytest.mark.parametrize("line, want", [("Yes, we zoom YouTube", "resume YouTube"), ("we zoom youtube.", "resume youtube"),
                                         ("rezoom the video", "resume the video")])
def test_resume_heard_as_we_zoom(line, want):
    assert main.fix_misheard(line) == want


@pytest.mark.parametrize("line", ["We zoom in on the map", "zoom YouTube", "we zoom", "we zoom out a bit on YouTube"])
def test_other_zoom_lines_are_left_alone(line):
    assert main.fix_misheard(line) == line


def test_a_media_command_sentence_inside_tutorial_speech_is_carried_out():
    d = gateway.media_sentence("It's different from the UV sphere here. Pause YouTube. You can see the name just there. Collection, Icosphere.")
    assert d is not None and d.intents[0].steps == [{"tool": "media_command", "arguments": {"command": "pause"}}]


@pytest.mark.parametrize("line", ["Pause the video here and try it yourself. Then come back.", "Pause YouTube.", "I paused YouTube. It was loud.",
                                  "Pause YouTube. Play YouTube.", "Now press play. Then add a cube."])
def test_tutorial_speech_and_single_or_mixed_commands_are_not_taken_here(line):
    assert gateway.media_sentence(line) is None


# ---------------------------------------------------------------- 7. claims without action (h012, h054)
@pytest.mark.parametrize("line", ["how we're looking on gaming is ever is anything going to interfere with my gaming right now",
                                  "Is anything going to interfere with my gaming?", "Is my PC ready for gaming?",
                                  "is there anything slowing down my games"])
def test_a_gaming_readiness_question_is_a_real_look(line):
    d = gateway.exact(line)
    assert d is not None and d.intents[0].steps == [{"tool": "investigate_slowness", "arguments": {}}]
    assert gateway._only_looks(d.intents[0].steps)


@pytest.mark.parametrize("line", ["I love gaming.", "Is anything going to interfere with my sleep?", "My wife says gaming interferes with dinner.",
                                  "Anything new in gaming news?", "Will it rain during my match?"])
def test_gaming_talk_is_not_a_pc_check(line):
    assert not gateway.gaming_check(line)


def test_unreal_engine_is_found_where_epic_installs_it(monkeypatch, tmp_path):
    for version in ("UE_5.3", "UE_5.8"):
        exe = tmp_path / "Epic Games" / version / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    assert apps._unreal_editors() == {"unreal engine": tmp_path / "Epic Games" / "UE_5.8" / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"}
    (tmp_path / "Epic Games").rename(tmp_path / "gone")
    assert apps._unreal_editors() == {}


@pytest.mark.parametrize("sentence, said", [("Unreal Engine is opening now.", "Open Unreal Engine"),
                                             ("Unreal Engine is launching, sir.", "Open Unreal Engine"),
                                             ("No interference right now, sir.", "is anything going to interfere with my gaming right now"),
                                             ("Your gaming performance looks clean.", "is anything going to interfere with my gaming")])
def test_a_chat_reply_never_claims_an_opening_or_a_check_that_did_not_run(sentence, said):
    assert intelligence.chat_unsaid(sentence, said, acts=True)


@pytest.mark.parametrize("sentence, said", [("The store is opening at nine.", "when does the store open"),
                                             ("Gaming is a great hobby.", "I love gaming"),
                                             ("No problems, glad you liked it.", "thanks for the help"),
                                             ("Everything looks clear.", "is the road clear")])
def test_ordinary_chat_sentences_are_still_said(sentence, said):
    assert not intelligence.chat_unsaid(sentence, said, acts=True)


# ---------------------------------------------------------------- review 2026-11-21 (independent python reviewer)
@pytest.mark.parametrize("site", ["this", "the", "and", "com", "mail", "untitled", "localhost", "all tabs", "other tabs", "a b c", "2048"])
def test_words_that_name_no_one_tab_are_refused(site):
    with pytest.raises(ValueError):
        pcactions.validate(pcplanner.canonical({"steps": [{"tool": "close_site_tab", "arguments": {"site": site}}]}))


def test_the_planner_writing_files_for_close_means_file_explorer():
    plan = pcplanner.canonical({"steps": [{"tool": "close_app", "arguments": {"app": "all files"}}]})
    assert plan["steps"][0]["arguments"]["app"] == "file explorer"
    kept = pcplanner.canonical({"steps": [{"tool": "open_app", "arguments": {"app": "files"}}]})
    assert kept["steps"][0]["arguments"]["app"] == "files"            # opening Files is unchanged


@pytest.mark.parametrize("line", ["Open Spotify. Play the music.", "Close YouTube. Pause YouTube.", "Pause YouTube. Actually never mind.",
                                  "Pause YouTube. Can you open notepad.", "Pause YouTube. Jarvis, what time is it."])
def test_a_media_sentence_beside_another_request_leaves_the_whole_line_to_the_planner(line):
    assert gateway.media_sentence(line) is None


@pytest.mark.parametrize("line", ["go to sleep. what's the weather", "go to sleep. what time is it", "go to sleep. can you open notepad",
                                  "go to sleep. i need you to open notepad", "go to sleep. i want chrome open"])
def test_go_to_sleep_then_a_question_or_request_is_answered(line):
    assert _route(line) == "respond"


@pytest.mark.parametrize("line", ["check for overlapping vertices in the mesh", "check for non-manifold geometry", "check for anything",
                                  "look for jarvis", "look for a pattern in the data"])
def test_check_for_work_words_is_not_a_search(line):
    assert webintent.kind(line) is None


def test_a_hyphenated_name_after_a_verb_is_not_a_doubled_verb():
    assert heard.normalise("play play-doh") == "play play-doh"


def test_ready_to_play_music_is_not_a_gaming_check():
    assert not gateway.gaming_check("Is my PC ready to play music?")
    assert gateway.gaming_check("Is my PC ready to game?")


def test_an_opening_claim_is_only_dropped_when_he_asked_to_open_something():
    assert not intelligence.chat_unsaid("The store is opening now.", "when does the store open", acts=True)
    assert intelligence.chat_unsaid("Notepad is opening now.", "open notepad", acts=True)


def test_closing_files_without_pywin32_says_so(monkeypatch):
    import builtins
    from jarvis_pc.guardrails import stepfence
    monkeypatch.setattr(stepfence, "require_foreground", lambda what: None)
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name))
                        if name in ("win32gui", "win32con") else real(name, *a, **k))
    assert "left them alone" in apps.close_app("file explorer")
