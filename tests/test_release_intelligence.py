"""Release 2026-11-07: defects found in the owner's own use of the live assistant (production log 2026-11-05/06)."""
import json
import threading
import time

import pytest

from core import gateway, guided_learning, intelligence, pcsemantic, pctasks, quick
from core.gateway import Intent


# --- one model call at a time: the owner's turn waits, background work never queues ---------------------------------
def test_foreground_waits_for_a_running_call(monkeypatch):
    monkeypatch.setattr(intelligence, "FOREGROUND_WAIT_S", 5.0)
    intelligence._inference_lock.acquire()
    threading.Timer(0.6, intelligence._inference_lock.release).start()
    started = time.monotonic()
    assert intelligence._acquire_inference() is True          # the old 2 s limit said "Local intelligence is unavailable"
    intelligence._inference_lock.release()
    assert time.monotonic() - started >= 0.5


def test_background_work_gives_way_at_once():
    intelligence._inference_lock.acquire()
    try:
        started = time.monotonic()
        with intelligence.background_inference():
            assert intelligence._acquire_inference() is False
        assert time.monotonic() - started < 0.2
    finally:
        intelligence._inference_lock.release()


def test_foreground_wait_stops_when_cancelled(monkeypatch):
    monkeypatch.setattr(intelligence, "FOREGROUND_WAIT_S", 5.0)
    intelligence._inference_lock.acquire()
    try:
        with pytest.raises(intelligence.LocalFailure):
            intelligence._acquire_inference(lambda: True)
    finally:
        intelligence._inference_lock.release()


@pytest.mark.parametrize("value,runs", [("on", False), ("off", False), ("shadow", True)])
def test_shadow_decision_only_in_shadow_mode(monkeypatch, value, runs):
    monkeypatch.setenv("JARVIS_GATEWAY", value)
    monkeypatch.setattr(gateway, "shadow", lambda *a, **k: None)
    t = gateway.shadow_later("open notepad", "pcfast")
    assert (t is not None) is runs
    if t:
        t.join(2)


# --- "which apps are open": a filter word meaning "everything" -------------------------------------------------------
@pytest.mark.parametrize("word", ["all", "everything", "*", "", "apps"])
def test_list_windows_everything(monkeypatch, word):
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda: [{"title": "Steam", "process": "steam.exe"}, {"title": "Notes", "process": "notepad.exe"}])
    assert json.loads(pcops.list_windows(word))["count"] == 2


def test_list_windows_says_when_nothing_matches(monkeypatch):
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda: [{"title": "Steam", "process": "steam.exe"}])
    out = json.loads(pcops.list_windows("Maya"))
    assert out["count"] == 0 and "contains=''" in out["note"]


# --- installed games are apps ----------------------------------------------------------------------------------------
def _steam(tmp_path, monkeypatch):
    apps_dir = tmp_path / "Steam" / "steamapps"
    apps_dir.mkdir(parents=True)
    (apps_dir / "appmanifest_1172470.acf").write_text('"AppState"\n{\n\t"appid"\t\t"1172470"\n\t"name"\t\t"Apex Legends"\n}\n')
    (apps_dir / "appmanifest_228980.acf").write_text('"AppState"\n{\n\t"appid"\t\t"228980"\n\t"name"\t\t"Steamworks Common Redistributables"\n}\n')
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))


def test_steam_games_are_found(tmp_path, monkeypatch):
    from tools import apps
    _steam(tmp_path, monkeypatch)
    assert apps._steam_games() == {"apex legends": "steam://rungameid/1172470"}
    assert apps.is_game("steam://rungameid/1172470") and not apps.is_game("notepad.exe")


def test_open_apex_resolves_to_the_steam_game(tmp_path, monkeypatch):
    from tools import apps
    _steam(tmp_path, monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "pd"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "ad"))
    apps._installed.cache_clear()
    try:
        assert apps._find("Apex") == ("apex legends", "steam://rungameid/1172470")
        assert apps.resolve_app("Apex Legends") == "apex legends"
        assert pcsemantic.mentioned_apps("go ahead and open Apex for me", ["apex legends", "notepad"]) == ["apex legends"]
    finally:
        apps._installed.cache_clear()


def test_one_word_does_not_select_a_normal_app(monkeypatch):
    monkeypatch.setattr(pcsemantic, "_is_game", lambda name: False)
    assert pcsemantic.mentioned_apps("open memory", ["windows memory diagnostic"]) == []


def test_files_means_file_explorer():
    from tools import apps
    assert apps.resolve_app("files") == "files" and apps.BUILTINS["files"] == "explorer.exe"


# --- "go ahead" is permission, "open files and go to downloads" is one step ----------------------------------------
def _intent(*steps, summary=""):
    return Intent("actions", summary, "", {}, [{"tool": t, "arguments": a} for t, a in steps], "")


def test_go_ahead_is_not_a_request():
    assert gateway.asking_clauses("go ahead and open Apex for me") == ["open Apex for me"]
    assert gateway.asking_clauses("just do it") == []


def test_one_folder_step_covers_open_files_and_go_to_downloads():
    it = _intent(("open_path", {"path": r"C:\Users\me\Downloads"}))
    assert gateway.uncovered("Open files and go to downloads", [it]) == ""


def test_a_dropped_part_is_still_caught():
    it = _intent(("open_app", {"app": "notepad"}))
    assert gateway.uncovered("open notepad and type hello world", [it]) != ""
    assert gateway.uncovered("open notepad and type it", [it]) != ""          # nothing but a verb: stay strict
    lying = _intent(("open_app", {"app": "notepad"}), summary="open notepad and type hello world")
    assert gateway.uncovered("open notepad and type hello world", [lying]) != ""   # the model's summary is not the plan


def test_open_files_then_folder_is_one_explorer_window():
    steps = [{"tool": "open_app", "arguments": {"app": "files"}}, {"tool": "open_path", "arguments": {"path": "C:/x"}}]
    assert gateway._one_explorer(steps) == steps[1:]
    keep = [{"tool": "open_app", "arguments": {"app": "notepad"}}, {"tool": "open_path", "arguments": {"path": "C:/x"}}]
    assert gateway._one_explorer(keep) == keep


# --- a guide from an hour ago does not take the owner's "yes" --------------------------------------------------------
@pytest.fixture
def guide_store(monkeypatch):
    box = {}
    monkeypatch.setattr(guided_learning, "_state", lambda: dict(box.get("v", {})))
    monkeypatch.setattr(guided_learning, "_save", lambda v: box.__setitem__("v", dict(v)))
    return box


def _active(box, at):
    box["v"] = {"status": "active", "topic": "t", "source": "s", "steps": ["one long step here", "two long step here",
                                                                          "three long step"], "index": 0, "at": at}


def test_guide_follows_while_it_is_the_conversation(guide_store):
    _active(guide_store, time.time())
    assert guided_learning.handle("okay").startswith("Step 2 of 3")


def test_another_request_pauses_the_guide(guide_store):
    _active(guide_store, time.time())
    assert guided_learning.handle("close my usb to ttl tab") is None
    assert guided_learning.handle("Yes.") is None                  # live 2026-11-05: this yes belonged to the tab question
    assert guided_learning.handle("next step").startswith("Step 2 of 3")


def test_an_old_guide_answers_only_when_named(guide_store):
    _active(guide_store, time.time() - guided_learning.IDLE_S - 5)
    assert guided_learning.handle("yes") is None
    assert guided_learning.handle("continue the guide").startswith("Step 2 of 3")


# --- "what PC skills do you have" is read from the stores ------------------------------------------------------------
@pytest.mark.parametrize("said", ["What have you mastered for PC control?", "What PC control skills do you have?",
                                  "what can you do on my computer", "Hey Jarvis, what skills do you have for the PC?"])
def test_capability_question_is_grounded(monkeypatch, said):
    from tools import pcskills
    monkeypatch.setattr(pcskills, "capability_summary", lambda: "GROUNDED")
    assert quick.handle(said) == "GROUNDED"


def test_unrelated_question_is_not_a_capability_question(monkeypatch):
    from tools import pcskills
    monkeypatch.setattr(pcskills, "capability_summary", lambda: "GROUNDED")
    assert quick.handle("what skills does a plumber need") is None


# --- phone requests get the gateway, on the owner's own words only ---------------------------------------------------
def test_relay_uses_the_owners_words():
    assert pctasks.gateway_text("open notepad", "[Sent from their Jarvis phone app] Open Notepad.") == "Open Notepad."
    assert pctasks.gateway_text("open notepad", "what's the weather and open notepad") == ""


# --- "play the third video": the search opened in the browser leaves a list to pick from ----------------------------
def test_opened_youtube_search_keeps_a_result_list(monkeypatch):
    from core import pcactions
    seen = []
    monkeypatch.setattr(pcactions, "_open_browser_url", lambda url, cancelled: {"verified": True, "detail": "YouTube search is open."})
    monkeypatch.setattr(pcactions, "remember_listing", lambda site, query: seen.append((site, query)))
    pcactions.perform("open_url", {"url": "https://www.youtube.com/results?search_query=cocomelon"})
    assert seen == [("youtube", "cocomelon")]


# --- EXPERIENCE -> FAILURE ANALYSIS: what Jarvis could not do is written down and shown as the next study list -------
def test_a_miss_is_recorded_and_listed(tmp_path, monkeypatch):
    from core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    gateway.record_miss("[Sent from their Jarvis phone app] play the third video", "part of the request was not understood")
    rows = gateway.recent_misses()
    assert rows and rows[-1]["said"] == "play the third video"
    from tools import pcskills

    class FakeSchool:
        def status(self):
            return "School: fine."
    monkeypatch.setattr(pcskills, "school", lambda: FakeSchool())
    assert "'play the third video'" in pcskills.pc_school_status()


def test_a_not_covered_decision_is_a_miss(tmp_path, monkeypatch):
    from core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    d = gateway.Decision.__new__(gateway.Decision)
    d.intents, d.covers = [Intent("clarify", "which one?", "", {}, [], "Which one?")], False
    monkeypatch.setattr(gateway.Decision, "brief", lambda self: {"covers": False})
    gateway.execute(d, "open the thing and the other thing")
    assert gateway.recent_misses()[-1]["reason"] == "part of the request was not understood"


# --- "close it" closes what Jarvis opened, never the owner's own window in front -------------------------------------
@pytest.fixture
def desk(monkeypatch):
    from core import pcactions
    state = {"front": (100, "ChatGPT.exe"), "shown": {100, 200}, "closed": []}
    monkeypatch.setattr(pcactions, "_foreground_identity", lambda: state["front"])
    monkeypatch.setattr(pcactions, "_window_shown", lambda h: h in state["shown"])
    monkeypatch.setattr(pcactions, "_window_class", lambda h: "cabinetwclass")
    import jarvis_pc.guardrails.stepfence as sf
    monkeypatch.setattr(sf, "fence", lambda what: None)

    class W:
        def __init__(self, h): self.h = h
        def close(self): state["closed"].append(self.h); state["shown"].discard(self.h)

    class D:
        def __init__(self, backend): pass
        def window(self, handle): return W(handle)
    import pywinauto
    monkeypatch.setattr(pywinauto, "Desktop", D)
    pcactions._OPENED.clear(); pcactions._LAST_OPEN.clear()
    yield state
    pcactions._OPENED.clear(); pcactions._LAST_OPEN.clear()


def test_close_it_closes_the_window_jarvis_opened(desk):
    from core import pcactions
    desk["front"] = (200, "explorer.exe")
    pcactions.note_opened({"tool": "open_path", "arguments": {"path": "C:/Users/me/Downloads"}})
    desk["front"] = (100, "ChatGPT.exe")                        # the owner clicked back into ChatGPT
    out = pcactions._close_foreground_window()
    assert desk["closed"] == [200] and out["verified"] and out["detail"] == "Closed File Explorer."


def test_close_it_with_nothing_of_jarvis_open_closes_the_front_window(desk):
    from core import pcactions
    out = pcactions._close_foreground_window()                   # owner 2026-11-07: refusing their own window was wrong
    assert desk["closed"] == [100] and out["detail"] == "Closed ChatGPT."


def test_close_this_window_means_the_front_one(desk, monkeypatch):
    from core import pcactions, pcbridge
    desk["front"] = (200, "explorer.exe")
    pcactions.note_opened({"tool": "open_path", "arguments": {"path": "C:/Users/me/Downloads"}})
    desk["front"] = (100, "ChatGPT.exe")
    monkeypatch.setattr(pcbridge, "original_message", lambda: "I need you to quit this file window that I'm looking at")
    pcactions._close_foreground_window()
    assert desk["closed"] == [100]


def test_the_desktop_is_never_closed(desk, monkeypatch):
    from core import pcactions
    desk["front"] = (300, "explorer.exe")
    monkeypatch.setattr(pcactions, "_window_class", lambda h: "progman")
    with pytest.raises(RuntimeError, match="protected"):
        pcactions._close_foreground_window()
    assert desk["closed"] == []


def test_a_background_launch_is_not_recorded_as_jarvis_window(desk, monkeypatch):
    from core import pcactions
    monkeypatch.setattr(pcactions, "_own_window", lambda step: (0, ""))                 # its window can't be found
    pcactions.note_opened({"tool": "open_app", "arguments": {"app": "calculator"}})   # ChatGPT still in front
    assert pcactions._OPENED == []


# --- the phone asks the PC about the PC; it never answers PC facts from imagination --------------------------------
def _laptop(monkeypatch, reply):
    from core import pcbridge, pcfast
    from tools import pcview
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(pcbridge, "original_message", lambda: None)
    sent = []

    def pc_command(name, arguments):
        sent.append((name, arguments))
        return [{"type": "text", "text": reply}]
    monkeypatch.setattr(pcview, "pc_command", pc_command)
    return pcfast, sent


def test_laptop_asks_the_pc_about_its_ram(monkeypatch):
    pcfast, sent = _laptop(monkeypatch, "31.5 GB of memory.")
    assert pcfast.handle("[Sent from their Jarvis phone app] how much RAM is on my PC.") == "31.5 GB of memory."
    assert sent == [("control_computer", {"task": "how much RAM is on my PC.", "probe": True})]


def test_pc_says_not_mine_and_the_laptop_answers(monkeypatch):
    pcfast, sent = _laptop(monkeypatch, "[not a PC request]")
    assert pcfast.ask_pc("what's a good computer to buy") is None


def test_unreachable_pc_is_said_not_guessed(monkeypatch):
    pcfast, sent = _laptop(monkeypatch, "The PC is unreachable. It must be awake, connected to Tailscale, and running Jarvis.")
    assert pcfast.ask_pc("graphic card do I have?").startswith("I can't reach the PC right now")


def test_probe_is_answered_by_the_pc_gateway(monkeypatch):
    from core import pcfast, pchybrid
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    monkeypatch.setattr(pcfast, "handle", lambda said, history=None, **kw: "31.5 GB of memory." if "RAM" in said else None)
    monkeypatch.setattr(pchybrid, "last_result", lambda: None)
    monkeypatch.setattr(quick, "handle", lambda text: None)      # this test is the probe relay; the memory reading has its own (tests/test_ram_reading.py)
    box = {}
    jobs = pctasks.Jobs.__new__(pctasks.Jobs)
    jobs.lock, jobs.active = threading.Lock(), None
    for said, want in (("how much RAM is on my PC.", "31.5 GB of memory."), ("tell me a joke about computers", "[not a PC request]")):
        jobs.jobs = {"j": {"uid": "owner", "name": "control_computer", "cancel_event": threading.Event(), "state": "running", "probe": True}}
        monkeypatch.setattr(pctasks.users, "acting_as", lambda uid: __import__("contextlib").nullcontext())
        from tools import gaming
        monkeypatch.setattr(gaming, "game_mode", lambda: False)
        jobs._execute("j", {"task": said}, said)
        assert jobs.jobs["j"]["detail"] == want, jobs.jobs["j"]


def test_probe_passes_the_relay_validation(monkeypatch):
    """The real entry the laptop calls (start), not just the worker: the probe flag must not be refused as an argument."""
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: uid)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    jobs = pctasks.Jobs()
    ran = []
    monkeypatch.setattr(jobs, "_execute", lambda jid, arguments, message: ran.append((arguments, jobs.jobs[jid]["probe"])))
    monkeypatch.setattr(threading, "Thread", lambda target, args, **k: type("T", (), {"start": lambda self: target(*args)})())
    import uuid
    jobs.start("owner", "control_computer", {"task": "how much RAM is on my PC.", "probe": True}, uuid.uuid4().hex, "how much RAM is on my PC.")
    assert ran == [({"task": "how much RAM is on my PC."}, True)]


# --- owner 2026-11-07: "he says too much" / the screen read read out symbols ----------------------------------------
def test_screen_summary_is_plain_words(monkeypatch):
    from core import pcstate
    snap = {"foreground": {"process": "claude", "title": "Claude", "minimized": False},
            "windows": [{"process": "pythonw", "title": "Jarvis", "minimized": False},
                        {"process": "claude", "title": "Claude", "minimized": False},
                        {"process": "msedge", "title": "New tab - Personal - Microsoft Edge", "minimized": False},
                        {"process": "explorer", "title": "Downloads", "minimized": False},
                        {"process": "explorer", "title": "Program Manager", "minimized": False}], "running": []}
    assert pcstate.spoken(snap) == "In front: Claude. Also open: Edge, File Explorer (Downloads)."


def test_ocr_symbols_are_not_read_out():
    from core import pcverbs
    assert pcverbs.readable("Visible text: 三 | 口 | <> | ← | D | File | IP3 | JARVIS PC-control skill set | >_") == "JARVIS PC-control skill set"


def test_failure_reply_has_no_filler(monkeypatch):
    from core import pcmethods
    out = pcmethods.execute.__code__.co_consts
    assert not any(isinstance(c, str) and "paid escalation" in c for c in out)


# --- "Jarvis, follow this": instructions on the screen / in a video --------------------------------------------------
def test_follow_this_is_recognised():
    from core import screenguide
    assert screenguide.asked("Jarvis follow this instruction") and screenguide.asked("do what the video says")
    assert not screenguide.asked("follow up on that email") and not screenguide.asked("I follow you on twitter")


def test_commands_on_screen_go_to_the_guide_follower(monkeypatch):
    from core import screenguide
    from tools import pcskills
    text = "Install jq\n```powershell\nwinget install --id jqlang.jq -e\n```\nThen check it with jq --version"
    monkeypatch.setattr(screenguide, "read_instructions", lambda: ("the page 'Install jq'", text))
    monkeypatch.setattr(screenguide, "_owner_now", lambda: False)    # someone else (the owner's own command runs at once)
    seen = []
    monkeypatch.setattr(pcskills, "pc_follow_guide", lambda goal, instructions, confirmed=False: seen.append(instructions) or
                        "NEEDS_CONFIRMATION: Plan: 1) winget install jqlang.jq. Ask the user; it only goes ahead if their next message is a clear yes.")
    out = screenguide.follow("Jarvis follow this instruction")
    assert seen == [text] and out.startswith("I read the steps on the page 'Install jq'") and "NEEDS_CONFIRMATION" not in out
    assert out.endswith("Say yes and I'll do it.")


def test_nothing_readable_is_said(monkeypatch):
    from core import screenguide
    monkeypatch.setattr(screenguide, "read_instructions", lambda: ("", ""))
    assert screenguide.follow("follow this").startswith("I couldn't read any instructions")


def test_prose_steps_wait_for_a_yes(monkeypatch):
    from core import approvals, screenguide, users
    from tools import pcskills
    monkeypatch.setattr(screenguide, "read_instructions", lambda: ("your screen (Help)", "Open Paint, then open Notepad. That is all you need to do here."))
    d = gateway.Decision([Intent("actions", "", "", {}, [{"tool": "open_app", "arguments": {"app": "paint"}},
                                                        {"tool": "open_app", "arguments": {"app": "notepad"}}], "")], "advanced")
    monkeypatch.setattr(gateway, "decide", lambda text, history=None, cancelled=None: d)
    ran = []
    monkeypatch.setattr(gateway, "execute", lambda decision, said, cancelled=None: ran.append(said) or "Opened both.")
    monkeypatch.setattr(users, "current", lambda: "owner")
    monkeypatch.setattr(screenguide, "_owner_now", lambda: False)    # e.g. after "ask me first" (the owner's command runs at once)
    pcskills._PENDING.clear()
    out = screenguide.follow("follow the steps")
    assert out.endswith("Shall I open Paint, then open Notepad?") and ran == []   # one natural question; nothing runs before the yes
    approvals.on_user_message("owner", "yes")
    assert pcskills.run_pending("owner") == "Opened both." and len(ran) == 1


def test_memory_hogs_is_a_short_true_answer(monkeypatch):
    out = quick.handle("Open File Explorer and I need you to go in there and look for the most demanding memory.")
    assert out.startswith("Using the most memory: ") and out.count(" GB") >= 2 and len(out) < 200
    assert quick.handle("how do I free up the most memory") is None
    total = quick.handle("how much RAM is on my PC.")                       # the status answers it now (free and total), or nothing here
    assert total is None or "GB" in total


def test_an_app_opened_behind_is_recorded_by_its_own_window(desk, monkeypatch):
    from core import pcactions
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda name: "calculator")
    open_ = [{"hwnd": 400, "process": "ApplicationFrameHost.exe", "title": "Calculator"}]
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda name: list(open_))
    monkeypatch.setattr(apps, "close_app", lambda name: (desk["closed"].append(400), open_.clear(), 1)[-1])
    desk["shown"].add(400)
    pcactions.note_opened({"tool": "open_app", "arguments": {"app": "calculator"}})   # Claude stayed in front
    out = pcactions._close_foreground_window()                                         # "close it"
    assert desk["closed"] == [400] and 100 not in desk["closed"] and out["detail"] == "Closed calculator."


def test_close_it_right_after_an_untracked_open_never_closes_the_front_window(desk, monkeypatch):
    """Live 2026-11-07 (twice): Calculator opened behind Claude, 'close it' closed Claude."""
    from core import pcactions
    monkeypatch.setattr(pcactions, "_own_window", lambda step: (0, ""))
    pcactions.note_opened({"tool": "open_app", "arguments": {"app": "calculator"}})
    with pytest.raises(RuntimeError, match="can't find the calculator window"):
        pcactions._close_foreground_window()
    assert desk["closed"] == []


def test_close_it_finds_the_opened_app_at_close_time(desk, monkeypatch):
    from core import pcactions
    found = {"v": (0, "")}
    monkeypatch.setattr(pcactions, "_own_window", lambda step: found["v"])
    pcactions.note_opened({"tool": "open_app", "arguments": {"app": "calculator"}})   # window not there yet
    found["v"] = (400, "ApplicationFrameHost.exe"); desk["shown"].add(400)
    from tools import apps
    open_ = [{"hwnd": 400}]
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda name: list(open_))
    monkeypatch.setattr(apps, "close_app", lambda name: (desk["closed"].append(400), open_.clear(), 1)[-1])
    pcactions._close_foreground_window()
    assert desk["closed"] == [400]


def test_the_owners_follow_the_steps_runs_them_at_once(monkeypatch):
    """Owner 2026-11-07: "when I say do something there are no rules" — his command is the approval."""
    from core import screenguide
    monkeypatch.setattr(screenguide, "read_instructions", lambda: ("your screen (Help)", "Open Paint, then open Notepad. That is all you need to do here."))
    d = gateway.Decision([Intent("actions", "", "", {}, [{"tool": "open_app", "arguments": {"app": "paint"}},
                                                        {"tool": "open_app", "arguments": {"app": "notepad"}}], "")], "advanced")
    monkeypatch.setattr(gateway, "decide", lambda text, history=None, cancelled=None: d)
    ran = []
    monkeypatch.setattr(gateway, "execute", lambda decision, said, cancelled=None: ran.append(said) or "Opened both.")
    monkeypatch.setattr(screenguide, "_owner_now", lambda: True)
    assert screenguide.follow("follow the steps") == "Opened both." and len(ran) == 1


def test_screen_prose_is_never_run_as_commands(monkeypatch):
    """Live 2026-11-07: a page read off the screen became 12 'commands' (each line of text)."""
    from core import screenguide
    text = "\n".join(["Visible text: Jarvis follow test", "File", "C:/Users/me/follow-test.html", "How to get ready to sketch",
                      "Follow these two steps on your PC.", "Open the Calculator app.", "Then open Microsoft Paint."])
    monkeypatch.setattr(screenguide, "read_instructions", lambda: ("your screen", text))
    seen = []
    monkeypatch.setattr(screenguide, "_prose", lambda goal, t, where, now=False: seen.append(t) or "PROSE")
    assert screenguide.follow("follow this") == "PROSE" and "File" not in seen[0].splitlines()


def test_close_it_on_an_app_opened_in_front_closes_it_by_name(desk, monkeypatch):
    """Live 2026-11-07 01:24: Calculator came to the front and was recorded by its inner window; closing that window left
    Calculator open. The app name is kept with the window, so it closes the way 'close calculator' does."""
    from core import pcactions
    from tools import apps
    desk["front"] = (500, "CalculatorApp.exe"); desk["shown"].add(500)
    pcactions.note_opened({"tool": "open_app", "arguments": {"app": "calculator"}})
    open_ = [{"hwnd": 9}]
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda name: list(open_))
    monkeypatch.setattr(apps, "close_app", lambda name: (desk["closed"].append(name), open_.clear(), "")[-1])
    out = pcactions._close_foreground_window()
    assert desk["closed"] == ["calculator"] and out["detail"] == "Closed calculator."


def test_the_gateway_is_told_when_to_take_over_an_app():
    assert "use_app does a task INSIDE one listed app" in gateway._SYSTEM
    assert gateway._DOING.search("take over the calculator") and gateway._DOING.search("turn on dark mode in settings")
    assert pcactionsSCHEMA_SENSITIVE()


def pcactionsSCHEMA_SENSITIVE():
    from core import pcactions
    return pcactions.SCHEMAS["use_app"][1] is True          # always shown for the owner's yes before the loop runs
