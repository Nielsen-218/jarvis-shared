"""One policy, keyed by effect: the same step gets the same answer whatever route produced it."""
from core import authz, pcactions


def step(tool, **args):
    return {"tool": tool, "arguments": args}


def test_every_typed_tool_has_an_effect():
    missing = [t for t in pcactions.SCHEMAS if t not in authz.EFFECTS]
    assert not missing, missing


def test_observe_and_plain_open_run_without_a_question():
    v = authz.decide([step("list_windows"), step("open_app", app="calculator")], "I need to crunch some numbers")
    assert not v.ask and v.effects == {0: "observe", 1: "reversible"}


def test_the_same_close_step_gets_the_same_answer_from_any_route():
    plan = [step("close_app", app="paint")]
    for said in ("close paint", "I'm done with paint, get rid of it"):
        assert not authz.decide(plan, said).ask, said
    assert authz.decide(plan, "close the thing on the left").ask      # paint was never named


def test_the_owners_command_is_the_approval_even_for_what_cannot_be_undone():
    """Owner 2026-11-07: "when I say do something there are no rules"."""
    plan = [step("open_app", app="paint"), step("uninstall_program", name="ripgrep")]
    assert not authz.decide(plan, "open paint and uninstall ripgrep", owner=True).ask
    v = authz.decide(plan, "open paint and uninstall ripgrep", owner=False)          # someone else in the house: asks
    assert v.ask and "irreversible" in v.reasons[0] and "not the owner" in v.reasons[0]
    assert authz.decide(plan, "open paint and uninstall it", owner=True).ask          # ripgrep never named: not his command
    assert authz.decide(plan, "open paint and look at ripgrep", owner=True).ask       # named, but he never said to remove it
    assert authz.decide(plan, "open paint and uninstall ripgrep", owner=True, autonomous=False).ask   # "ask me first" holds
    assert not authz.decide(plan, "uninstall it", owner=False, approved=True).ask
    assert not authz.decide([step("install_program", name="VLC")], "install VLC", owner=True).ask


def test_naming_the_target_is_not_enough_the_owner_must_have_asked_for_that_change():
    said = "create a folder called jarvis-test in my Downloads"
    for cmd, ask in (("mkdir ~/Downloads/jarvis-test", False), ("Remove-Item ~/Downloads/jarvis-test -Recurse", True),
                     (r"mkdir C:\Windows\jarvis-test", True)):
        assert authz.decide([step("run_terminal", command=cmd)], said, owner=True).ask is ask, cmd
    assert not authz.decide([step("run_terminal", command="Remove-Item ~/Downloads/jarvis-test -Recurse")],
                            "delete the jarvis-test folder in my Downloads", owner=True).ask


def test_ask_me_first_turns_reversible_steps_into_questions():
    assert authz.decide([step("open_app", app="paint")], "open paint", autonomous=False).ask


def test_observed_names_extend_the_scope_and_unsaved_windows_protect_work():
    found = r"C:\Users\x\LivingShadowWorld\LivingShadowWorld.uproject"
    open_it = [step("open_path", path=found)]
    assert authz.decide(open_it, "find my shadow world project and launch it").ask        # a path nobody named yet
    assert not authz.decide(open_it, "find my shadow world project and launch it", observed=[found]).ask
    close = [step("close_app", app="notepad")]
    assert not authz.decide(close, "close notepad").ask
    assert authz.decide(close, "close notepad", observed_titles=["*Untitled - Notepad"], owner=False).ask
    assert not authz.decide(close, "close notepad", observed_titles=["*Untitled - Notepad"], owner=True).ask   # its save prompt stays


def test_terminal_commands_are_reversible_unless_they_destroy_or_move_things():
    assert authz.effect(step("run_terminal", command="git status")) == "reversible"
    assert authz.effect(step("run_terminal", command="Remove-Item -Recurse build")) == "irreversible"
    assert authz.effect(step("run_terminal", command="winget install jqlang.jq")) == "irreversible", "an install changes the PC"
    assert authz.effect(step("frobnicate")) == "irreversible", "an unknown verb never runs unasked"


def test_recovery_may_only_observe_or_act_within_scope():
    assert authz.allowed_recovery(step("read_screen", app="notepad"), "type hi in notepad")
    assert authz.allowed_recovery(step("type_text", app="notepad", text="hi"), "type hi in notepad")
    assert not authz.allowed_recovery(step("type_text", app="word", text="hi"), "type hi in notepad")
    assert not authz.allowed_recovery(step("uninstall_program", name="x"), "uninstall x", owner=False)
    assert authz.allowed_recovery(step("uninstall_program", name="xtool"), "uninstall xtool", owner=True)
    assert not authz.allowed_recovery(step("close_app", app="msedge"), "close github", owner=True)   # broader than he said


def test_minimize_is_never_a_close():
    """Live 2026-11-08: "Minimize Claude" closed Claude. A close step needs a close word; minimize has its own action."""
    from core import pchybrid
    for said in ("Minimize Cloud.", "minimize Claude", "hide this"):
        assert authz.decide([step("close_foreground")], said, owner=True).ask, said
    assert not authz.decide([step("minimize_app", app="claude")], "minimize claude", owner=True).ask
    assert pchybrid.simple_plan("minimize this") == {"steps": [{"tool": "minimize_foreground", "arguments": {}}]}
    assert pchybrid.simple_plan("hide Discord")["steps"][0]["tool"] == "minimize_app"


def test_use_app_runs_unasked_when_the_app_is_named_and_asks_when_it_is_not():
    """Pins the live policy the use_app/pcsemantic comments once contradicted ("always shown for the owner's yes").
    If the owner decides the fast lane's 'operate' must ask first, this is the test to change on purpose."""
    plan = [step("use_app", app="calculator", goal="add 2 and 3")]
    assert authz.EFFECTS["use_app"] == "reversible"
    assert not authz.decide(plan, "in calculator add 2 and 3", owner=True).ask
    assert authz.decide(plan, "do something in that thing on the left", owner=True).ask
