"""The gateway carries the WHOLE decision out through the existing executor: consecutive actions as one plan, skills with their
values, a question stops the rest, a found path flows into the next step, a tie becomes the owner's choice."""
import pytest

from core import gateway, pcagent, pchybrid


def step(tool, **args):
    return {"tool": tool, "arguments": args}


# ------------------------------------------------------------------------------------------------- pcagent: observations
def test_a_found_path_fills_the_next_step_and_widens_what_a_recovery_may_touch():
    seen = []

    def execute(s, approved=False, cancelled=None, execution_id=None):
        seen.append(s)
        if s["tool"] == "find_project":
            return {"verified": True, "detail": "found", "found": r"C:\p\Game.uproject"}
        return {"verified": True, "detail": "opened " + s["arguments"]["path"]}
    out = pcagent.pursue("find my game project and launch it", {"steps": [step("find_project", name="game"), step("open_path", path="<found>")]},
                         execute=execute, replan=lambda *a: None)
    assert out["state"] == "completed" and seen[1]["arguments"]["path"] == r"C:\p\Game.uproject"
    assert out["observed"] == [r"C:\p\Game.uproject"]


def test_a_tie_stops_with_the_choices_and_keeps_the_rest_of_the_plan():
    def execute(s, **kw):
        return {"verified": False, "detail": "Which one?", "error_kind": "needs_choice", "choices": [r"C:\a\G.uproject", r"C:\b\G.uproject"]}
    out = pcagent.pursue("launch g", {"steps": [step("find_project", name="g"), step("open_path", path="<found>")]}, execute=execute, replan=lambda *a: None)
    assert out["state"] == "needs_choice" and out["choices"] == [r"C:\a\G.uproject", r"C:\b\G.uproject"]
    assert out["remaining"] == [step("open_path", path="<found>")]


def test_the_owners_answer_picks_a_candidate_by_number_or_by_words():
    gateway._CHOICES["owner"] = {"task": "launch g", "choices": [r"C:\Alpha\G.uproject", r"C:\Beta\G.uproject"], "remaining": [step("open_path", path="<found>")],
                                 "t": __import__("time").time()}
    assert gateway.choose("owner", "the beta one")["plan"]["steps"] == [step("open_path", path=r"C:\Beta\G.uproject")]
    gateway._CHOICES["owner"] = {"task": "launch g", "choices": [r"C:\Alpha\G.uproject", r"C:\Beta\G.uproject"], "remaining": [], "t": __import__("time").time()}
    picked = gateway.choose("owner", "the first")
    assert picked["plan"]["steps"] == [step("open_path", path=r"C:\Alpha\G.uproject")] and picked["observed"] == [r"C:\Alpha\G.uproject"]
    assert gateway.choose("owner", "the first") is None, "answered once"
    gateway._CHOICES["owner"] = {"task": "x", "choices": ["C:\\Alpha\\G", "C:\\Beta\\G"], "remaining": [], "t": __import__("time").time()}
    assert gateway.choose("owner", "what's the weather") is None and "owner" in gateway._CHOICES, "an unrelated message leaves the choice open"


# ------------------------------------------------------------------------------------------------- gateway.execute
def test_consecutive_action_intents_run_as_one_plan_and_a_question_stops_the_rest(monkeypatch):
    calls = []

    def run(task, cancelled=None, *, plan=None, route=None, observed=()):
        calls.append((task, plan, route))
        return {"state": "completed", "detail": "did " + ", ".join(s["tool"] for s in plan["steps"])}
    monkeypatch.setattr(pchybrid, "run", run)
    d = gateway.Decision([gateway.Intent("actions", steps=[step("open_app", app="notepad")]),
                          gateway.Intent("actions", steps=[step("type_text", app="notepad", text="hi")]),
                          gateway.Intent("clarify", question="Save it where?"),
                          gateway.Intent("actions", steps=[step("close_app", app="notepad")])], "advanced")
    text = gateway.execute(d, "open notepad, type hi, save it and close it")
    assert len(calls) == 1 and [s["tool"] for s in calls[0][1]["steps"]] == ["open_app", "type_text"] and calls[0][2] == "gateway"
    assert text == "did open_app, type_text Save it where?"


def test_a_stopped_plan_says_what_was_not_done(monkeypatch):
    monkeypatch.setattr(pchybrid, "run", lambda task, cancelled=None, *, plan=None, route=None, observed=(): {"state": "stopped", "detail": "Notepad did not open."})
    d = gateway.Decision([gateway.Intent("actions", summary="open notepad", steps=[step("open_app", app="notepad")]),
                          gateway.Intent("skill", summary="tell free space", skill="pc_disk_free")], "advanced")
    text = gateway.execute(d, "open notepad and tell me how much free space I have")
    assert text == "Notepad did not open. I stopped there, so I did not go on to: tell free space."


def test_a_cancel_reaches_the_running_plan_and_stops_the_parts_after_it(monkeypatch):
    """2026-11-06: Brain.cancel() never reached a gateway plan, so a cancelled 'type ... then save' ran on into save_document."""
    from core import pcfast
    from tools import computer
    seen = {}

    def run(task, cancelled=None, *, plan=None, route=None, observed=()):
        computer.abort()                                    # the owner interrupts while the first part runs
        seen["cancel_seen"] = cancelled()
        return {"state": "completed", "detail": "Opened Notepad."}
    monkeypatch.setattr(pchybrid, "run", run)
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    d = gateway.Decision([gateway.Intent("actions", summary="open notepad", steps=[step("open_app", app="notepad")]),
                          gateway.Intent("skill", summary="tell free space", skill="pc_disk_free")], "advanced")
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: d)
    monkeypatch.setattr(pcfast.pcbridge, "require_owner", lambda uid=None: "owner")
    computer.abort()                                        # a stale stop from the last turn
    text = pcfast.handle("open notepad and tell me how much free space I have")
    assert seen["cancel_seen"] is True, "the step sees the interruption"
    assert text == "Opened Notepad. Stopped; I did not go on to: tell free space."
    computer._abort.clear()


def test_a_cancel_during_understanding_runs_nothing(monkeypatch):
    from core import pcfast
    from tools import computer
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    monkeypatch.setattr(pchybrid, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran after a cancel")))

    def decide(raw, history=None, cancelled=None):
        computer.abort()
        return gateway.Decision([gateway.Intent("actions", steps=[step("open_app", app="notepad")])], "advanced")
    monkeypatch.setattr(gateway, "decide", decide)
    monkeypatch.setattr(pcfast.pcbridge, "require_owner", lambda uid=None: "owner")
    assert pcfast.handle("open notepad and type a long paragraph") == "Stopped."
    computer._abort.clear()


def test_a_chat_only_decision_is_left_to_the_brain_and_a_tie_is_remembered(monkeypatch):
    assert gateway.execute(gateway.Decision([gateway.Intent("chat", "a joke")], "fast"), "tell me a joke") is None
    monkeypatch.setattr(pchybrid, "run", lambda task, cancelled=None, *, plan=None, route=None, observed=(): {
        "state": "needs_choice", "detail": "Found two. Which one?", "choices": ["C:\\a", "C:\\b"], "remaining": [step("open_path", path="<found>")]})
    gateway._CHOICES.clear()
    text = gateway.execute(gateway.Decision([gateway.Intent("actions", steps=[step("find_project", name="g"), step("open_path", path="<found>")])], "advanced"), "launch g")
    assert text == "Found two. Which one?" and gateway._CHOICES["owner"]["choices"] == ["C:\\a", "C:\\b"]


# ------------------------------------------------------------------------------------------------- pchybrid: one policy by effect
def test_the_gateway_route_asks_by_effect_and_runs_the_rest_unasked(monkeypatch):
    from core import pcledger, pcmethods, pcbridge
    monkeypatch.setattr(pcbridge, "require_owner", lambda: None)
    ran = []
    monkeypatch.setattr(pcmethods, "execute", lambda s, **kw: ran.append(s["tool"]) or {"verified": True, "detail": "ok " + s["tool"]})
    out = pchybrid.run("open notepad and type hi", plan={"steps": [step("open_app", app="notepad"), step("type_text", app="notepad", text="hi")]}, route="gateway")
    assert out["state"] == "completed" and ran == ["open_app", "type_text"]
    out = pchybrid.run("get rid of that tool", plan={"steps": [step("uninstall_program", name="ripgrep")]}, route="gateway")
    assert out["state"] == "needs_review" and "irreversible" in out["why"] and ran == ["open_app", "type_text"]
    assert out["detail"] == "Should I uninstall ripgrep?"                # said like a person, no "Plan: 1)"
    out = pchybrid.run("uninstall ripgrep", plan={"steps": [step("uninstall_program", name="ripgrep")]}, route="gateway")
    assert out["state"] == "completed" and ran[-1] == "uninstall_program"            # the owner's own command: no question
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda: [])                                 # the test never reads the real desktop
    out = pchybrid.run("close whatever is open", plan={"steps": [step("close_app", app="paint")]}, route="gateway")
    assert out["state"] == "needs_review" and "did not name" in out["why"]
    pending = pcledger.pending()
    assert pending and pending[0]["data"]["route"] == "gateway"


def test_a_pending_gateway_plan_runs_on_the_owners_yes_through_the_legacy_entry(monkeypatch):
    from core import pcledger, pcmethods, pcbridge
    monkeypatch.setattr(pcbridge, "require_owner", lambda: None)
    ran = []
    monkeypatch.setattr(pcmethods, "execute", lambda s, **kw: ran.append(s["tool"]) or {"verified": True, "detail": "ok"})
    first = pchybrid.run("get rid of that tool", plan={"steps": [step("uninstall_program", name="ripgrep")]}, route="gateway")
    pcledger.approve(first["approval_id"], "plan")
    again = pchybrid.run("get rid of that tool")              # what pcfast's "yes" path calls
    assert again["state"] == "completed" and ran == ["uninstall_program"] and again["route"] == "gateway"


# ------------------------------------------------------------------------------------------------- verbs: typed and worded
def test_every_new_verb_is_typed_has_an_effect_and_words():
    from core import authz, pcactions
    for tool in ("find_project", "open_path", "find_files", "type_text", "save_document", "read_screen", "run_terminal", "investigate_slowness"):
        assert tool in pcactions.SCHEMAS and tool in authz.EFFECTS and tool in pchybrid._STEP_WORDS
    pcactions.validate({"steps": [step("read_screen", app="")]})          # the whole screen
    with pytest.raises(ValueError):
        pcactions.validate({"steps": [step("type_text", app="", text="hi")]})
    assert pchybrid.plan_words({"steps": [step("find_project", name="shadow world"), step("open_path", path="<found>")]}) == \
        "Plan: 1) find the project 'shadow world'. 2) open <found>."
    assert authz.effect(step("save_document", app="notepad", path=r"C:\definitely\not\there.txt")) == "reversible"


# ------------------------------------------------------------------------------------------------- 'Right away, sir' (upgrade 3)
# Logged silences: 95.2 s for "I need you to type in Claude message box. Hurry up.", 27.6 s for "follow the instruction on my
# screen". A slow PC turn now says a provisional line within a second of the work starting, then how it is going.
import threading  # noqa: E402
import time  # noqa: E402

from core import intelligence  # noqa: E402

FAST = dict(ACK_SECONDS=0.15, PROGRESS_FIRST=5.0, PROGRESS_EVERY=5.0)


@pytest.fixture
def quick_timers(monkeypatch):
    for name, value in FAST.items():
        monkeypatch.setattr(gateway, name, value)
    gateway._opening_at[0] = -1                     # "Right away, sir" first
    yield
    from tools import computer
    computer._abort.clear()


def gateway_brain(monkeypatch, decision, run):
    """A Brain whose PC layer is the real pcfast + gateway.execute, with the decision and the executor faked."""
    from brain import llm
    from core import pcfast, quick
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(gateway, "decide", lambda raw, history=None, cancelled=None: decision)
    monkeypatch.setattr(pcfast.pcbridge, "require_owner", lambda uid=None: "owner")
    monkeypatch.setattr(pchybrid, "run", run)
    return llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")


def close_chatgpt():
    return gateway.Decision([gateway.Intent("actions", "close ChatGPT", steps=[step("close_app", app="chatgpt")])], "fast")


def slow_run(seconds, detail="Closed ChatGPT.", state="completed", underway=True, during=None):
    def run(task, cancelled=None, *, plan=None, route=None, observed=(), on_start=None):
        if underway and on_start:
            on_start()                                  # past the review question: the work is running
        if during:
            during()
        time.sleep(seconds)
        return {"state": state, "detail": detail}
    return run


def test_a_slow_pc_turn_says_the_acknowledgment_then_the_result(monkeypatch, quick_timers):
    b = gateway_brain(monkeypatch, close_chatgpt(), slow_run(0.6))
    heard = []
    b.ask("close ChatGPT", heard.append)
    assert heard == ["Right away, sir: closing ChatGPT.", "Closed ChatGPT."]
    assert b.messages[-1] == {"role": "assistant", "content": "Right away, sir: closing ChatGPT. Closed ChatGPT."}


def test_a_fast_pc_turn_says_only_its_result(monkeypatch, quick_timers):
    b = gateway_brain(monkeypatch, close_chatgpt(), slow_run(0.0))
    heard = []
    b.ask("close ChatGPT", heard.append)
    time.sleep(0.3)                                     # longer than the acknowledgment's delay: still nothing more
    assert heard == ["Closed ChatGPT."]


def test_a_plan_going_to_review_is_never_acknowledged(monkeypatch, quick_timers):
    """'Right away, sir: closing ChatGPT.' followed by 'Should I close ChatGPT?' would be a lie: the review comes first."""
    b = gateway_brain(monkeypatch, close_chatgpt(), slow_run(0.6, "Should I close ChatGPT?", "needs_review", underway=False))
    heard = []
    b.ask("close whatever that is", heard.append)
    assert heard == ["Should I close ChatGPT?"]


def test_a_question_first_is_the_whole_reply(monkeypatch, quick_timers):
    d = gateway.Decision([gateway.Intent("clarify", question="Which window, sir?"),
                          gateway.Intent("actions", steps=[step("close_app", app="chatgpt")])], "advanced")
    b = gateway_brain(monkeypatch, d, slow_run(0.6))
    heard = []
    b.ask("close the window", heard.append)
    assert heard == ["Which window, sir?"]


def test_nothing_is_said_after_he_cuts_in_not_even_stopped(monkeypatch, quick_timers):
    monkeypatch.setattr(gateway, "PROGRESS_FIRST", 0.45)
    holder = {}

    def cut_in_soon():
        threading.Timer(0.3, lambda: holder["b"].cancel("Right away, sir: closing ChatGPT.")).start()
    b = gateway_brain(monkeypatch, close_chatgpt(), slow_run(0.9, detail="Stopped before any step executed.", during=cut_in_soon))
    holder["b"] = b
    heard = []
    b.ask("close ChatGPT", heard.append)
    assert heard == ["Right away, sir: closing ChatGPT."], "the acknowledgment came before he cut in; nothing after it"
    assert b.messages[-1]["content"].startswith("Right away, sir: closing ChatGPT. [interrupted before I answered]")


def test_a_long_turn_reports_progress_at_most_three_times(monkeypatch, quick_timers):
    """The schedule is 10 s, then every 15 s, three at most (10/25/40 s: a 45 s run hears all three); here scaled down."""
    monkeypatch.setattr(gateway, "ACK_SECONDS", 0.05)
    monkeypatch.setattr(gateway, "PROGRESS_FIRST", 0.2)
    monkeypatch.setattr(gateway, "PROGRESS_EVERY", 0.2)
    b = gateway_brain(monkeypatch, close_chatgpt(), slow_run(1.6))
    heard = []
    b.ask("close ChatGPT", heard.append)
    progress_lines = [h for h in heard if h.startswith("I'm closing ChatGPT")]
    assert heard[0] == "Right away, sir: closing ChatGPT." and heard[-1] == "Closed ChatGPT."
    assert len(progress_lines) == 3 and len(heard) == 5, heard
    assert not any(intelligence.claims_done(h) for h in heard[:-1]), "provisional lines never claim the work is done"


def test_the_timer_never_speaks_after_the_result_is_in(monkeypatch):
    rounds = []
    for delay in (0.8, 0.0):                            # the real delay, and none at all (the timer races finish())
        monkeypatch.setattr(gateway, "ACK_SECONDS", delay)
        for _ in range(200):
            said = []
            ack = gateway.Ack(said.append, "Right away, sir.")
            ack.go()
            ack.finish()
            rounds.append((said, len(said)))
    time.sleep(0.2)
    assert all(len(said) == at_finish for said, at_finish in rounds), "a line came after finish()"


def fixture_decisions():
    G, I = gateway.Decision, gateway.Intent
    return [G([I("actions", steps=[step("close_app", app="chatgpt")])], "fast"),
            G([I("actions", steps=[step("open_app", app="notepad"), step("type_text", app="notepad", text="hello")])], "advanced"),
            G([I("actions", steps=[step("set_volume", percent=30)])], "exact"),
            G([I("guide", "install", values={"url": "https://github.com/a/b"})], "exact"),
            G([I("guide", "follow the steps")], "exact"),
            G([I("skill", "free space", skill="pc_disk_free")], "fast"),
            G([I("actions", steps=[step("focus_app", process="msedge.exe")]), I("actions", steps=[step("type_text", app="claude", text="hi")]),
               I("skill", skill="x")], "advanced"),
            G([I("actions", steps=[step("use_app", app="blender", goal="add a cube")])], "advanced"),
            G([I("actions", steps=[step("minimize_foreground")])], "exact"),
            G([I("actions", steps=[step("run_terminal", command="dir")])], "advanced"),
            G([I("actions", steps=[step("uninstall_program", name="ripgrep")])], "advanced"),
            G([I("actions", steps=[step("media_command", command="pause")])], "exact"),
            G([I("actions", steps=[step("power", action="restart")])], "exact"),
            G([I("actions", steps=[step("browser_search", query="x", site="google")])], "exact"),
            G([I("actions", steps=[step("open_path", path="C:/x")])], "exact"),
            G([I("actions", steps=[step("save_document", app="notepad", path="a.txt")])], "exact"),
            G([I("actions", steps=[step("find_project", name="shadow world"), step("open_path", path="<found>")])], "advanced"),
            G([I("actions", steps=[step("frobnicate")])], "exact"),
            G([I("actions", steps=[step("close_site_tab", site="youtube")])], "exact"),
            G([I("actions", steps=[step("open_app", app="microsoft edge"), step("browse_open", query="claude", site="google"),
                                   step("type_text", app="claude", text="x"), step("close_app", app="paint")])], "advanced")]


def test_acknowledgments_are_short_provisional_and_never_twice_in_a_row():
    lines = [gateway.ack_line(d) for d in fixture_decisions()]
    for line in lines:
        assert line and len(line.split()) < 10, line
        assert not intelligence.claims_done(line), line
    assert all(a != b for a, b in zip(lines, lines[1:]))
    assert gateway.ack_line(gateway.Decision([gateway.Intent("chat", "a joke")], "fast")) == ""
    assert gateway.ack_line(gateway.Decision([gateway.Intent("clarify", question="Which one?")], "fast")) == ""


def test_the_progress_report_says_the_step_in_plain_words():
    seen = []
    pcagent.pursue("close chatgpt", {"steps": [step("close_app", app="chatgpt")]},
                   execute=lambda s, **kw: {"verified": True, "detail": "Closed ChatGPT."}, replan=lambda *a: None,
                   progress=seen.append)
    assert seen == ["step 1 of 1, closing ChatGPT"]


def test_a_progress_report_never_reads_out_what_is_typed_or_run():
    """Review 2026-11-10: the report spoke "type 'Draft a reply to my lawyer about the custody hearing'" and "run in the
    terminal: setx OPENAI_API_KEY sk-abc123" aloud at 10 s, into history and the awareness log."""
    from core import progress
    seen = []
    secret, command = "Draft a reply to my lawyer about the custody hearing on Friday", "setx OPENAI_API_KEY sk-abc123"
    steps = [step("open_app", app="claude"), step("type_text", app="claude", text=secret), step("run_terminal", command=command),
             step("open_path", path="C:/Users/me/Private/custody.docx")]
    pcagent.pursue("draft it", {"steps": steps}, execute=lambda s, **kw: {"verified": True, "detail": "ok"},
                   replan=lambda *a: None, progress=seen.append)
    assert seen == ["step 1 of 4, opening Claude", "step 2 of 4, typing that into Claude",
                    "step 3 of 4, running that in the terminal", "step 4 of 4, opening that"]
    progress.start(gateway.doing(gateway.Decision([gateway.Intent("actions", steps=steps)], "advanced")))
    try:
        for line in seen:
            progress.step(line)
            spoken = progress.report("en")
            assert "lawyer" not in spoken and "sk-abc123" not in spoken and "custody" not in spoken, spoken
    finally:
        progress.done()


def test_every_pc_tool_has_a_progress_phrase_and_none_reads_working_on_working():
    """Review 2026-11-10: 22 tools (the slow Unreal / Shadow World ones among them) were 'I'm working on working on that'."""
    from core import pcactions, progress
    assert not set(pcactions.SCHEMAS) - set(gateway._GERUND), "a slow tool would be reported as 'working on that'"
    d = gateway.Decision([gateway.Intent("actions", steps=[step("shadow_world_game_test")])], "advanced")
    progress.start(gateway.doing(d) or "working on that")
    try:
        assert progress.report("en").startswith("I'm testing the game, ")
    finally:
        progress.done()


def test_a_routine_close_that_takes_a_second_says_only_its_result():
    """turn-timing-pc.jsonl: close_app 0.77 s median, 38% over 0.8 s; an acknowledgment then delayed the result ~2 s."""
    said = []
    ack = gateway.Ack(said.append, "Straight away, sir: closing Settings.")
    ack.go()
    time.sleep(1.2)
    ack.finish()
    assert said == []


# ------------------------------------------------------------------------------------------------- the miss line (upgrade 4)
@pytest.fixture
def fresh_misses(monkeypatch):
    monkeypatch.setattr(gateway, "_MISSED", {})
    monkeypatch.setattr(gateway, "record_miss", lambda *a, **k: None)


def test_a_missed_part_is_said_back_in_his_words_not_a_template(monkeypatch, fresh_misses):
    """2026-11-08: "I'm not sure that covers everything you asked; tell me what I missed." twice in a row, naming nothing."""
    monkeypatch.setattr(pchybrid, "run", lambda task, cancelled=None, *, plan=None, route=None, observed=(): {
        "state": "completed", "detail": "Ran the PC scan."})
    scan = gateway.Intent("actions", "scan the PC", steps=[step("investigate_slowness")])
    out = gateway.execute(gateway.Decision([scan], "advanced", covers=False, missing="look for"), "Scan my PC and look for.")
    assert out == "Ran the PC scan. I didn't catch what you meant by 'look for', sir."
    out = gateway.execute(gateway.Decision([scan], "advanced", covers=False), "Scan my PC and look for.")
    assert out == "Ran the PC scan. I didn't follow the rest, sir. What else did you want?"
    from core import pcsemantic
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: [])    # no catalogue: core.heard rewrites nothing, on any machine
    out = gateway.execute(gateway.Decision([], "none", covers=False), "Close chat GPT.")
    assert out.startswith("I couldn't work out how to do 'Close chat GPT', sir, so I haven't touched anything.")   # nothing ran: said so
    assert out.endswith("Name the app or the first step and I'll take it from there.")      # and what he can say next (core.pchelp)
    assert "covers everything" not in out and "tell me what I missed" not in out


def test_when_nothing_ran_he_hears_that_nothing_ran_not_just_the_missing_part(monkeypatch, fresh_misses):
    """Review 2026-11-10: both attempts left out 'type hello world', nothing ran, and he heard only "I didn't catch what you
    meant by 'type hello world'", so he took Notepad to be open."""
    ran = []
    monkeypatch.setattr(pchybrid, "run", lambda *a, **k: ran.append(1) or {"state": "completed", "detail": "x"})
    d = gateway.Decision([], "none", covers=False, missing="type hello world")
    out = gateway.execute(d, "open notepad and type hello world")
    assert not ran
    assert out == "I haven't done any of it yet, sir: I couldn't work out the part about 'type hello world'."


def test_the_same_miss_twice_is_not_the_identical_line(monkeypatch, fresh_misses):
    """'Close chat GPT.' (log 11713) missed and was said again: the second answer says it failed again."""
    first = gateway.execute(gateway.Decision([], "none", covers=False), "Close chat GPT.")
    second = gateway.execute(gateway.Decision([], "none", covers=False), "close chat gpt")
    assert first != second and "haven't touched anything" in second and "Still no luck" in second


@pytest.mark.parametrize("detail,state", [("Should I close ChatGPT?", "needs_review"), ("Which one: Alpha or Beta?", "needs_choice")])
def test_a_question_waiting_is_never_followed_by_a_second_one(monkeypatch, fresh_misses, detail, state):
    """jarvis.log 11821/12322/12486/12490: '<question> I'm not sure that covers everything...': two questions, so his next
    'yes' or 'the browser' was ambiguous."""
    monkeypatch.setattr(gateway, "_CHOICES", {})
    monkeypatch.setattr(pchybrid, "run", lambda *a, **k: {"state": state, "detail": detail, "choices": ["a", "b"], "remaining": []})
    d = gateway.Decision([gateway.Intent("actions", steps=[step("close_app", app="chatgpt")])], "advanced", covers=False)
    assert gateway.execute(d, "close whatever that is and the other thing") == detail
    clarify = gateway.Decision([gateway.Intent("clarify", question="Which window would you like me to minimize?")], "advanced",
                               covers=False, missing="look for")
    assert gateway.execute(clarify, "minimize it and look for") == "Which window would you like me to minimize?"


def test_the_understanding_keeps_the_part_it_could_not_cover():
    ctx = gateway_context_for("open notepad and type hello world")

    def ask(role, text, ctx_, cancelled):
        return {"intents": [{"kind": "actions", "summary": "open notepad", "skill": "", "values": [],
                             "steps": [step("open_app", app="notepad")], "question": ""}], "covers_everything": True}
    d = gateway.understand("open notepad and type hello world", ctx, ask=ask)
    assert d.covers is False and d.missing == "type hello world" and not d.intents


def gateway_context_for(text):
    from core import gateway_context
    return gateway_context.Context(said=text, pc={"windows": [], "running": []}, pc_text="PC STATE: test",
                                   apps_mentioned=["notepad"] if "notepad" in text else [], apps_running=[], skills=[], recent=[],
                                   pending=None)


def test_a_slow_understanding_plays_one_soft_cue_and_a_fast_or_cancelled_one_none(monkeypatch):
    from core import pcfast
    monkeypatch.setattr(pcfast, "EARCON_SECONDS", 0.1)
    cues = []
    assert pcfast._with_earcon(lambda: time.sleep(0.4) or "slow", lambda: cues.append(1), lambda: False) == "slow"
    assert cues == [1]
    assert pcfast._with_earcon(lambda: "fast", lambda: cues.append(2), lambda: False) == "fast"
    pcfast._with_earcon(lambda: time.sleep(0.4), lambda: cues.append(3), lambda: True)
    time.sleep(0.2)
    assert cues == [1], "no cue for a fast turn, none after a cancel"


def test_a_finished_pc_run_notes_its_route_state_and_failing_tool_for_the_timing_row(monkeypatch):
    from core import pcbridge, pcmethods, turntrace
    monkeypatch.setattr(pcbridge, "require_owner", lambda: None)
    turntrace._outcomes.clear()
    monkeypatch.setattr(pcmethods, "execute", lambda s, **kw: {"verified": s["tool"] == "open_app", "detail": "x"})
    pchybrid.run("open notepad and type hi", plan={"steps": [step("open_app", app="notepad"), step("type_text", app="notepad", text="hi")]},
                 route="gateway")
    row = turntrace._outcomes[-1][1]
    assert row["route"] == "gateway" and row["tools"][:2] == ["open_app", "type_text"] and row["verified"] is False
    assert row["failed"] == "type_text"
    turntrace._outcomes.clear()
