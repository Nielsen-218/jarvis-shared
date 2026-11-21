"""core/protocols: "when X happens, do Y" kept as data per person, run step by step with each skill's own check.

Owner 2026-11-16: proactive features become recipes instead of new hand-wired scheduler methods. Rules tested here:
his phrase runs a protocol; a question about one never does; a time / came-back trigger never acts until he approved
THAT protocol (the approval binds to the exact steps); nothing destructive; every step is reported as it really went."""
from datetime import datetime

import pytest

from core import protocols as P
from core import quick, storage, users


@pytest.fixture(autouse=True)
def long_running(monkeypatch):
    """Jarvis has been up a while: the came-back startup guard (a restart is not his return) is tested on its own."""
    import time
    monkeypatch.setattr(P, "_STARTED", time.monotonic() - 10_000)
    monkeypatch.setattr(P, "_process_age", lambda: 10_000.0)
    monkeypatch.setattr(P, "_retry_back_until", 0.0)
    from core import acquire
    monkeypatch.setattr(acquire, "running_game", lambda: "")   # never the real process list (a game on the PC during a gate)


@pytest.fixture
def calls(monkeypatch):
    """Recording stand-ins for every step (conftest makes the real ones refuse in tests)."""
    seen = []

    def fake(kind):
        def run(step, ctx):
            seen.append((kind, dict(step)))
            if kind == "say":
                ctx.setdefault("spoken", []).append(step["text"])
            return P.DONE, f"did {kind}"
        return run
    monkeypatch.setattr(P, "ACTIONS", {k: fake(k) for k in P.ACTIONS})
    return seen


def test_real_steps_refuse_inside_tests():
    with pytest.raises(RuntimeError, match="real PC action"):
        P.ACTIONS["open_app"]({"do": "open_app", "target": "apex legends"}, {})


@pytest.mark.parametrize("said", ["gaming protocol", "Jarvis, gaming protocol.", "Hey Jarvis, run the gaming protocol",
                                  "Initiate gaming protocols", "start the gaming protocol now", "Can you run the gaming protocol?",
                                  "[Sent from their Jarvis phone app] gaming protocol"])
def test_his_phrase_runs_the_gaming_protocol_in_order(calls, said):
    reply = quick.handle(said)
    assert reply and reply.startswith("Gaming protocol: all 5 steps done"), reply
    assert [k for k, _ in calls] == ["free_up", "close_tab", "set_volume", "open_app", "open_app"]
    assert calls[-1][1]["target"] == "apex legends", "the game opens last, so it ends up in front"


@pytest.mark.parametrize("said", ["what does the gaming protocol do", "what's in the gaming protocol?", "is the gaming protocol approved?",
                                  "tell me about the gaming protocol", "how did the gaming protocol go", "what protocols do I have",
                                  "what is a network protocol", "why did the gaming protocol open discord?",
                                  "should I run the gaming protocol?"])
def test_a_question_never_runs_a_protocol(calls, said):
    quick_reply = P.handle(said)
    assert calls == [], f"{said!r} ran steps"
    if said == "what is a network protocol":
        assert quick_reply is None, "a general question is the model's, not about one of his protocols"


def test_describe_and_list(calls):
    said = P.handle("what does the gaming protocol do")
    assert "close the YouTube tab" in said and "ChatGPT" not in said and "set the volume to 50%" in said
    assert "open Discord" in said and "open Apex Legends" in said and "do-not-disturb" not in said.lower()
    assert P.handle("list my protocols").startswith("You have 3 protocols: gaming, streaming and storm watch (automatic, approved)")


def test_unknown_protocol_is_said_not_invented(calls):
    assert P.handle("run the house party protocol") == "I don't have a house party protocol. You have: gaming, streaming and storm watch."
    assert P.handle("house party protocol") is None, "a bare unknown name is the model's (it may be about TCP)"
    assert calls == []


def test_create_by_voice_with_time_trigger_needs_approval(calls):
    said = P.handle("create a protocol called night at 11 pm: set the volume to 20 and say good luck and have fun")
    assert said.startswith("Saved the night protocol") and "won't run on its own until you say 'approve the night protocol'" in said
    proto = P.find("night")
    assert proto["steps"] == [{"do": "set_volume", "percent": 20}, {"do": "say", "text": "good luck and have fun"}]
    assert proto["triggers"] == [{"type": "time", "at": "23:00"}]
    at = datetime(2026, 11, 16, 23, 2)
    assert P.due(False, at) == [], "not approved: never due"
    assert P.handle("approve the night protocol").startswith("Approved.")
    assert [p["name"] for p in P.due(False, at)] == ["night"]
    assert P.due(False, datetime(2026, 11, 16, 23, 5)) == [], "fires once a day"
    assert [p["name"] for p in P.due(False, datetime(2026, 11, 17, 23, 0))] == ["night"]
    assert P.due(False, datetime(2026, 11, 18, 23, 30)) == [], "outside its window"


def test_changing_a_protocol_withdraws_its_approval(calls):
    P.handle("create a protocol called morning when I come back to the PC: open spotify")
    P.handle("approve the morning protocol")
    assert [p["name"] for p in P.due(True)] == ["morning"]
    assert P.due(False) == [], "came-back fires only on his return"
    P.handle("create a protocol called morning when I come back to the PC: open spotify, set the volume to 90")
    assert P.due(True) == [], "different steps: the old approval does not carry over"
    assert "waiting for your approval" in P.handle("list my protocols")


def test_withdraw_approval_by_voice(calls):
    P.handle("create a protocol called morning when I get back: open spotify")
    P.handle("approve the morning protocol")
    assert P.handle("stop the morning protocol from running on its own").startswith("Done:")
    assert P.due(True) == []


@pytest.mark.parametrize("said, why", [
    ("create a protocol called wipe: delete my downloads", "delete"),
    ("create a protocol called reset: open notepad and shut down the pc", "delete, force"),
    ("create a protocol called clean: close everything", "delete, force"),
    ("create a protocol called quiet: turn on do not disturb", "do-not-disturb"),
    ("create a protocol called y at 7: open notepad", "AM or PM"),
    ("create a protocol called z: make me a sandwich", "I don't know how"),
    ("create a protocol called v: set the volume to 150", "between 0 and 100"),
    ("create a protocol called t: close the facebook tab", "I can only close tabs"),
])
def test_refused_creations_save_nothing(calls, said, why):
    reply = P.handle(said)
    assert "Nothing was changed" in reply and why in reply, reply
    assert [p["name"] for p in P.protocols()] == ["gaming", "streaming", "storm watch"]


def test_third_person_steps_and_delete(calls):
    said = P.handle("create a study protocol that opens notepad and sets the volume to 30")
    assert said == "Saved the study protocol: it will open Notepad and set the volume to 30%. Say 'study protocol' to run it."
    assert P.handle("delete the study protocol") == "Deleted the study protocol."
    assert P.find("study") is None


def test_deleted_builtin_stays_deleted(calls):
    assert P.handle("delete the gaming protocol") == "Deleted the gaming protocol."
    assert P.find("gaming") is None and P.handle("run the gaming protocol").startswith("I don't have a gaming protocol")
    assert P.handle("gaming protocol") is None
    assert calls == []


def test_protocols_are_per_person(calls):
    wife = users.add("Partner", {"en": "ma'am"})
    with users.acting_as(wife):
        assert P.handle("list my protocols").startswith("You don't have any protocols yet")
        P.handle("create a protocol called tea: say the kettle is on")
        assert P.handle("tea protocol").endswith("the kettle is on")
        assert P.handle("run the gaming protocol").startswith("I don't have a gaming protocol")
        assert P.handle("create a protocol called pc: open notepad").startswith("Saved")
        assert P.handle("pc protocol") == "Protocols that control the PC run for the PC's owner only."
    assert [p["name"] for p in P.protocols()] == ["gaming", "streaming", "storm watch"], "her protocols are in her folder"
    assert (storage.DATA / "users" / wife / "protocols.json").exists()


def test_member_cannot_approve(calls):
    wife = users.add("Partner", {"en": "ma'am"})
    with users.acting_as(wife):
        P.handle("create a protocol called tea at 4 pm: say tea time")
        assert P.handle("approve the tea protocol") == "Only the PC's owner can approve a protocol to run on its own."


def test_failed_and_waiting_steps_are_reported_as_such(monkeypatch):
    def ok(step, ctx):
        return P.DONE, "volume is 50%"

    def boom(step, ctx):
        raise RuntimeError("Windows refused.")

    def waiting(step, ctx):
        return P.WAITING, "asked Steam to start Apex Legends; not confirmed running yet"

    def skipped(step, ctx):
        return P.SKIPPED, "ChatGPT wasn't open"
    monkeypatch.setattr(P, "ACTIONS", {**P.ACTIONS, "set_volume": ok, "open_app": waiting, "close_app": skipped, "close_tab": boom})
    proto = P.find("gaming")
    proto["steps"] = proto["steps"][1:3] + [{"do": "close_app", "target": "chatgpt"}, proto["steps"][-1]]
    entry = P.run(proto)
    said = P.summary(entry)
    assert said.startswith("Gaming protocol: 1 of 4 steps done.")
    assert "Failed: close the YouTube tab: Windows refused." in said
    assert "Waiting: asked Steam" in said and "Nothing to do: ChatGPT wasn't open" in said
    assert "all" not in said.split(".")[0]
    assert P.handle("how did the gaming protocol go") == said


# -- the step wrappers, with the skills faked ------------------------------------------------------------------------
class FakeApps:
    PROTECTED = {"explorer"}

    def __init__(self, *, games=(), running=(), ready=True, installed=("discord", "apex legends", "chatgpt", "notepad")):
        self.games, self.running, self.ready, self.installed = set(games), set(running), ready, set(installed)
        self.launched = []

    def resolve_app(self, q):
        return q if q in self.installed else None

    def _find(self, q):
        return (q, "steam://rungameid/1" if q in self.games else f"C:/{q}.lnk") if q in self.installed else None

    def is_game(self, target):
        return str(target).startswith("steam:")

    def game_processes(self, app):
        return ["proc"] if app in self.games and app in self.running else []

    def launch_and_wait(self, app, timeout=8.0, cancelled=lambda: False):
        self.launched.append((app, timeout))
        if app in self.games:
            return "Asked Steam to start it. Games take a minute or more to show their window, so it is not verified open yet.", False
        return (f"Opened {app}.", True) if self.ready else (f"Launch was requested for {app}, but no visible application window was verified yet.", False)

    def app_running(self, app):
        return app in self.running

    def visible_windows_for_app(self, app):
        return [{"hwnd": 1}] if app in self.running else []


@pytest.fixture
def fake_apps(monkeypatch):
    import tools.apps

    def make(**kw):
        fake = FakeApps(**kw)
        for name in ("resolve_app", "_find", "is_game", "game_processes", "launch_and_wait", "app_running", "visible_windows_for_app"):
            monkeypatch.setattr(tools.apps, name, getattr(fake, name))
        monkeypatch.setattr(tools.apps, "PROTECTED", fake.PROTECTED)
        return fake
    return make


class FakePC:
    def __init__(self, results=None, error=None):
        self.calls, self.results, self.error = [], results or {}, error

    def perform(self, name, args, approved=False, cancelled=lambda: False):
        self.calls.append((name, args, approved))
        if name == "close_app" and getattr(self, "on_close", None):
            self.on_close()
        if self.error:
            raise self.error
        return self.results.get(name, {"verified": True, "detail": "ok", "percent": args.get("percent")})


def test_open_app_done_only_with_a_window(fake_apps):
    fake_apps(ready=True)
    assert P._open_app({"target": "discord"}, {}) == (P.DONE, "opened Discord")
    fake_apps(ready=False)
    status, detail = P._open_app({"target": "discord"}, {})
    assert status == P.FAILED and "no visible application window" in detail
    assert P._open_app({"target": "fortnite"}, {})[0] == P.FAILED


def test_game_is_waiting_then_confirmed_or_failed(fake_apps, calls):
    apps = fake_apps(games={"apex legends"})
    ctx = {}
    status, detail = P._open_app({"target": "apex legends"}, ctx)
    assert status == P.WAITING and "not confirmed" in detail and ctx["games"] == ["apex legends"]
    entry = {"id": "x", "name": "gaming", "steps": [{"step": "open Apex", "status": P.WAITING, "detail": detail, "game": "apex legends"}]}
    P._confirm_games(entry, ["apex legends"], "owner", wait=0.2, poll=0.05)
    assert entry["steps"][0]["status"] == P.FAILED and "didn't start" in entry["steps"][0]["detail"]
    apps.running.add("apex legends")
    entry["steps"][0].update(status=P.WAITING)
    P._confirm_games(entry, ["apex legends"], "owner", wait=1, poll=0.05)
    assert entry["steps"][0]["status"] == P.DONE and "is running" in entry["steps"][0]["detail"]
    assert P.last_run("gaming")["steps"][0]["status"] == P.DONE
    assert P._open_app({"target": "apex legends"}, {}) == (P.DONE, "Apex Legends is already running")


def test_close_app_never_closes_a_game_and_skips_what_is_not_open(fake_apps, monkeypatch):
    pc = FakePC()
    monkeypatch.setattr(P, "_pc", lambda: pc)
    fake_apps(games={"apex legends"}, running={"apex legends"})
    status, detail = P._close_app({"target": "apex legends"}, {})
    assert status == P.FAILED and "don't close games" in detail and pc.calls == []
    fake_apps(running=set())
    assert P._close_app({"target": "chatgpt"}, {}) == (P.SKIPPED, "ChatGPT wasn't open") and pc.calls == []
    apps = fake_apps(running={"chatgpt"})
    pc.on_close = apps.running.clear
    assert P._close_app({"target": "chatgpt"}, {}) == (P.DONE, "closed ChatGPT")
    assert pc.calls == [("close_app", {"app": "chatgpt"}, True)]


def test_close_app_left_open_by_a_save_prompt_is_failed(fake_apps, monkeypatch):
    pc = FakePC({"close_app": {"verified": False, "detail": "Notepad is still open; it may be asking you to save."}})
    monkeypatch.setattr(P, "_pc", lambda: pc)
    fake_apps(running={"notepad"})
    assert P._close_app({"target": "notepad"}, {}) == (P.FAILED, "Notepad is still open; it may be asking you to save")


def test_close_tab_and_volume(monkeypatch):
    pc = FakePC(error=RuntimeError("No visible browser tab identifies itself as Youtube; nothing was closed."))
    monkeypatch.setattr(P, "_pc", lambda: pc)
    assert P._close_tab({"target": "youtube"}, {}) == (P.SKIPPED, "no YouTube tab was showing")
    pc = FakePC(error=RuntimeError("Windows would not bring the requested website tab forward; nothing was closed."))
    monkeypatch.setattr(P, "_pc", lambda: pc)
    with pytest.raises(RuntimeError):
        P._close_tab({"target": "youtube"}, {})           # run() turns it into a failed step
    pc = FakePC({"set_volume": {"verified": True, "percent": 50}})
    monkeypatch.setattr(P, "_pc", lambda: pc)
    assert P._set_volume({"percent": 50}, {}) == (P.DONE, "volume is 50%")


def test_from_the_laptop_the_pc_runs_it(calls, monkeypatch):
    from core import pcbridge, pcfast
    asked = []
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: asked.append(raw) or "Gaming protocol: all 5 steps done.")
    assert P.handle("gaming protocol") == "Gaming protocol: all 5 steps done."
    assert asked == ["gaming protocol"] and calls == [], "the laptop never runs PC steps itself"
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: None)
    said = P.handle("gaming protocol")
    assert "can't tell you whether the gaming protocol ran" in said and "nothing was done" not in said


def test_trigger_never_runs_over_a_game_and_reports(calls, monkeypatch):
    from core import acquire
    P.handle("create a protocol called night at 11 pm: set the volume to 20, say sleep well")
    P.handle("approve the night protocol")
    said = []
    monkeypatch.setattr(acquire, "running_game", lambda: "r5apex.exe")
    P.tick(False, deliver=said.append, now=datetime(2026, 11, 16, 23, 1))
    P.tick(False, deliver=said.append, now=datetime(2026, 11, 16, 23, 2))
    assert calls == [] and said == [] and "you were in a game" in P.last_run("night")["stopped"]
    assert len([r for r in storage.load(P.RUNS, []) if r.get("stopped")]) == 1, "said once, not every tick"
    monkeypatch.setattr(acquire, "running_game", lambda: "")
    P.tick(False, deliver=said.append, now=datetime(2026, 11, 16, 23, 5))     # the game ended inside the window: it runs
    assert [k for k, _ in calls] == ["set_volume", "say"]
    assert said == ["Night protocol: both steps done. did set_volume; did say. sleep well"]


def test_scheduler_hands_triggers_to_the_engine(monkeypatch):
    from core import scheduler
    seen = []
    monkeypatch.setattr(P, "tick", lambda returned, spawn=None: seen.append((returned, spawn)))
    s = scheduler.Scheduler.__new__(scheduler.Scheduler)
    s._protocols(True)
    assert seen and seen[0][0] is True and seen[0][1] == s._spawn
    monkeypatch.setenv("JARVIS_PROTOCOLS", "off")
    s._protocols(True)
    assert len(seen) == 1


def test_one_run_at_a_time(calls, monkeypatch):
    monkeypatch.setattr(P, "_running", {("owner", "gaming")})
    assert P.handle("gaming protocol") == "The gaming protocol is already running."
    assert calls == []


# -- regressions from the independent review (2026-11-16) ------------------------------------------------------------
@pytest.mark.parametrize("said", ["no gaming protocol", "Jarvis no gaming protocol", "ok no gaming protocol", "oh gaming protocol",
                                  "um gaming protocol", "never mind the gaming protocol", "I ran the gaming protocol yesterday",
                                  "I love the gaming protocol", "don't run the gaming protocol", "do not run the gaming protocol",
                                  "the gaming protocol is cool", "use the gaming protocol"])
def test_filler_negation_and_narration_never_run(calls, said):
    P.handle(said)
    quick.handle(said)
    assert calls == [], f"{said!r} ran steps"


def test_stt_slip_still_runs(calls):
    assert P.handle("gamming protocol").startswith("Gaming protocol: all 5 steps done")


def test_stop_is_answered_honestly(calls):
    assert P.handle("stop the gaming protocol") == "The gaming protocol isn't running, so there's nothing to stop."
    assert calls == []


def test_a_time_inside_a_spoken_line_stays_in_the_line(calls):
    P.handle("create a protocol called dinner at 5 pm: say dinner is at 6 pm sharp")
    proto = P.find("dinner")
    assert proto["triggers"] == [{"type": "time", "at": "17:00"}]
    assert proto["steps"] == [{"do": "say", "text": "dinner is at 6 pm sharp"}]
    P.handle("create a protocol called ping: notify me that dinner is at 6:30 pm")
    assert P.find("ping")["triggers"] == [] and P.find("ping")["steps"][0]["text"] == "dinner is at 6:30 pm"


@pytest.mark.parametrize("said", ["create a protocol called a: close it", "create a protocol called b: close this",
                                  "create a protocol called c: open the game", "create a protocol called d: close down everything"])
def test_vague_targets_are_refused(calls, said):
    assert "Nothing was changed" in P.handle(said)


def test_on_the_pc_an_app_must_exist_under_that_name(calls, monkeypatch):
    import tools.apps
    from core import pcbridge
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(tools.apps, "resolve_app", {"file manager": "task manager", "notepad": "notepad", "apex": "apex legends"}.get)
    said = P.handle("create a protocol called files: open file manager")
    assert "I can't find an app called File Manager on the PC" in said and P.find("files") is None
    assert P.handle("create a protocol called pad: open notepad, open apex").startswith("Saved")


def test_replacing_says_what_it_replaced(calls):
    said = P.handle("create a gaming protocol: open notepad")
    assert said.startswith("Replaced the gaming protocol (it used to clear the background for the game, close the YouTube tab")


def test_busy_job_thread_gives_the_trigger_back(calls):
    P.handle("create a protocol called night at 10 pm: say sleep well")
    P.handle("approve the night protocol")
    assert P.tick(False, now=datetime(2026, 11, 16, 22, 1), spawn=lambda name, job: False) == []
    assert [p["name"] for p in P.tick(False, deliver=lambda t: None, now=datetime(2026, 11, 16, 22, 2))] == ["night"]
    assert calls == [("say", {"do": "say", "text": "sleep well"})]


def test_window_crosses_midnight_and_bad_records_do_not_crash(calls):
    P.handle("create a protocol called late at 11:55 pm: say good night")
    P.handle("approve the late protocol")
    assert [p["name"] for p in P.due(False, datetime(2026, 11, 17, 0, 3))] == ["late"]
    assert P.due(False, datetime(2026, 11, 17, 0, 4)) == [], "once"
    items = storage.load(P.FILE, [])
    bad = {"name": "bad", "steps": [{"do": "say", "text": "x"}], "triggers": [{"type": "time", "at": "99:xx"}]}
    bad["approved"] = {"hash": P.fingerprint(bad)}
    storage.save(P.FILE, items + [bad])
    assert P.due(False, datetime(2026, 11, 18, 12, 0)) == []


def test_came_back_not_at_startup_and_not_twice_in_half_an_hour(calls, monkeypatch):
    import time
    P.handle("create a protocol called hello when I come back to the PC: say welcome back")
    P.handle("approve the hello protocol")
    monkeypatch.setattr(P, "_process_age", lambda: 20.0)
    assert P.due(True) == [], "Jarvis just started: that is a restart, not his return"
    monkeypatch.setattr(P, "_process_age", lambda: 10_000.0)
    assert [p["name"] for p in P.due(True, datetime(2026, 11, 16, 9, 0))] == ["hello"]
    assert P.due(True, datetime(2026, 11, 16, 9, 20)) == []
    assert [p["name"] for p in P.due(True, datetime(2026, 11, 16, 9, 31))] == ["hello"]


def test_unattended_close_never_leaves_a_save_question(fake_apps, monkeypatch):
    from core import saveprompt
    withdrawn = []
    monkeypatch.setattr(saveprompt, "withdraw", lambda uid, before=None: withdrawn.append(uid) or True)
    pc = FakePC({"close_app": {"verified": False, "error_kind": "needs_owner", "detail": "Save it before I close it?"}})
    monkeypatch.setattr(P, "_pc", lambda: pc)
    fake_apps(running={"notepad"})
    assert P._close_app({"target": "notepad"}, {"source": "trigger"}) == (P.FAILED, "Notepad is still open with unsaved work, so I left it alone")
    assert withdrawn == ["owner"]
    assert P._close_app({"target": "notepad"}, {"source": "voice"}) == (P.FAILED, "Save it before I close it?"), "he is there: asked"


def test_tray_app_is_not_called_closed(fake_apps, monkeypatch):
    pc = FakePC()
    monkeypatch.setattr(P, "_pc", lambda: pc)
    fake_apps(running={"discord"}, installed=("discord",))
    assert P._close_app({"target": "discord"}, {}) == (P.DONE, "closed Discord's window; it still runs in the background")


def test_loose_catalog_match_is_not_used(fake_apps, monkeypatch):
    import tools.apps
    fake_apps()
    monkeypatch.setattr(tools.apps, "resolve_app", lambda q: "task manager")
    assert P._open_app({"target": "file manager"}, {})[0] == P.FAILED
    assert P._close_app({"target": "file manager"}, {})[0] == P.SKIPPED



# -- round 2 (2026-11-16) --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["run the gaming protocol already", "Jarvis, start the gaming protocol now please",
                                  "okay run gaming protocol", "initiate the gaming protocol sir"])
def test_impatient_and_polite_orders_still_run(calls, said):
    assert P.handle(said).startswith("Gaming protocol: all 5 steps done"), said


def test_a_name_holding_a_negation_word_still_runs(calls):
    P.handle("create a protocol called hate mail: say deleting nothing")
    assert P.handle("run the hate mail protocol").endswith("deleting nothing")


def test_resolve_never_takes_a_shorter_app_for_a_longer_name(monkeypatch):
    import tools.apps
    monkeypatch.setattr(tools.apps, "resolve_app", {"visual studio code": "visual studio", "notepad++": "notepad",
                                                    "apex": "apex legends", "discord": "discord"}.get)
    assert P.resolve("visual studio code") is None and P.resolve("notepad++") is None
    assert P.resolve("apex") == "apex legends" and P.resolve("discord") == "discord"


def test_trigger_close_keeps_his_own_pending_save_question(fake_apps, monkeypatch):
    from core import saveprompt
    saveprompt.remember("owner", "paint")
    mine = saveprompt.pending("owner")
    pc = FakePC({"close_app": {"verified": False, "error_kind": "needs_owner", "detail": "Save it before I close it?"}})
    monkeypatch.setattr(P, "_pc", lambda: pc)
    fake_apps(running={"notepad"})
    P._close_app({"target": "notepad"}, {"source": "trigger"})
    assert saveprompt.pending("owner") is mine, "his Paint question is untouched"
    saveprompt.withdraw("owner")


def test_came_back_that_met_a_busy_thread_is_retried(calls):
    P.handle("create a protocol called hello when I come back to the PC: say welcome back")
    P.handle("approve the hello protocol")
    assert P.tick(True, spawn=lambda name, job: False) == []
    assert [p["name"] for p in P.tick(False, deliver=lambda t: None)] == ["hello"], "the next tick is still his return"
    assert P.tick(False, deliver=lambda t: None) == []


def test_how_it_went_keeps_the_real_run_after_a_game_skip(calls, monkeypatch):
    from core import acquire
    P.handle("create a protocol called night at 11 pm: say sleep well")
    P.handle("approve the night protocol")
    P.tick(False, deliver=lambda t: None, now=datetime(2026, 11, 16, 23, 1))
    monkeypatch.setattr(acquire, "running_game", lambda: "r5apex.exe")
    P.tick(False, deliver=lambda t: None, now=datetime(2026, 11, 17, 23, 1))
    said = P.handle("how did the night protocol go")
    assert said.startswith("Night protocol: the one step done.") and "skipped: you were in a game" in said


def test_a_time_inside_a_line_is_said_back(calls):
    said = P.handle("create a protocol called ping: notify me that dinner is at 6:30 pm")
    assert "I took 'at 6:30 pm' as part of what to say, not as when to run" in said


def test_resolve_keeps_real_short_names(monkeypatch):
    import tools.apps
    table = {"calc": "calculator", "windows terminal": "terminal", "settings app": "settings", "visual studio code": "visual studio"}
    monkeypatch.setattr(tools.apps, "resolve_app", table.get)
    monkeypatch.setattr(tools.apps, "BUILTINS", {"calculator": "calc.exe", "terminal": "wt.exe", "settings": "ms-settings:"})
    assert P.resolve("calc") == "calculator" and P.resolve("windows terminal") == "terminal" and P.resolve("settings app") == "settings"
    assert P.resolve("visual studio code") is None


# -- live check findings (2026-11-16) --------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["Is the gaming protocol approved?", "is the gaming protocol automatic",
                                  "Does the gaming protocol run on its own?"])
def test_approval_question_is_answered_from_the_data(calls, said):
    """Live: 'Is the gaming protocol approved?' reached the model, which said 'Yes, it is approved.'"""
    assert P.handle(said).startswith("The gaming protocol has no time or come-back trigger, so there's nothing to approve")
    P.handle("create a protocol called night at 11 pm: say sleep well")
    assert P.handle("is the night protocol approved?").startswith("No. The night protocol won't run on its own")
    P.handle("approve the night protocol")
    assert P.handle("is the night protocol approved?") == "Yes. The night protocol is approved to run when you say 'night protocol' and every day at 11 PM."
    assert calls == []
    assert P.handle("is the tcp protocol approved?") is None


def test_steam_waiting_on_his_sign_in_is_said_as_that(fake_apps, monkeypatch):
    """Live: Steam was signed out and showed 'Sign in to Steam'; the run only said 'didn't start within 3 minutes'."""
    from tools import pcops
    fake_apps(games={"apex legends"})
    monkeypatch.setattr(pcops, "windows", lambda: [{"process": "steamwebhelper.exe", "title": "Sign in to Steam", "hwnd": 1}])
    monkeypatch.setattr(P, "BLOCKED_GRACE_S", 0.05)
    entry = {"id": "x", "name": "gaming", "steps": [{"step": "open Apex Legends", "status": P.WAITING, "detail": "", "game": "apex legends"}]}
    P._confirm_games(entry, ["apex legends"], "owner", wait=5, poll=0.05)
    assert entry["steps"][0] == {"step": "open Apex Legends", "status": P.FAILED, "game": "apex legends",
                                 "detail": "Apex Legends didn't start: Steam is asking you to sign in"}
    monkeypatch.setattr(pcops, "windows", lambda: [{"process": "msedge.exe", "title": "How to sign in to Steam - Edge", "hwnd": 2}])
    assert P.launcher_blocked() == "", "a web page about Steam is not Steam waiting"


def test_a_running_game_is_never_blamed_on_a_steam_window(fake_apps, monkeypatch):
    apps = fake_apps(games={"apex legends"}, running={"apex legends"})
    monkeypatch.setattr(P, "launcher_blocked", lambda: "Steam is asking for your Steam Guard code")
    monkeypatch.setattr(P, "BLOCKED_GRACE_S", -1)
    entry = {"id": "y", "name": "gaming", "steps": [{"step": "open Apex Legends", "status": P.WAITING, "detail": "", "game": "apex legends"}]}
    P._confirm_games(entry, ["apex legends"], "owner", wait=2, poll=0.05)
    assert entry["steps"][0]["status"] == P.DONE and apps.running
