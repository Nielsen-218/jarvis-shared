"""Free-form app requests: grounded in the real catalogue, decided by a constrained local choice, executed by the existing typed plans."""
import pytest

from core import pchybrid, pcsemantic

CATALOGUE = ["windows memory diagnostic", "wsl settings", "libreoffice calc", "microsoft edge", "calculator", "notepad", "google chrome", "file explorer", "visual studio code", "spotify", "paint"]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    pcsemantic._CACHE.clear()
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: CATALOGUE)
    yield
    pcsemantic._CACHE.clear()


@pytest.mark.parametrize("sentence,app", [
    ("I'm done with Edge", "microsoft edge"), ("get me a calculator", "calculator"), ("bring up calc", "calculator"),
    ("hey jarvis i don't need chrome anymore", "google chrome"), ("switch over to spotify", "spotify"),
    ("pull up file explorer", "file explorer"), ("I want to scribble in paint", "paint"),
])
def test_an_app_is_found_from_the_sentence_without_a_phrase_list(sentence, app):
    assert pcsemantic.mentioned_apps(sentence, CATALOGUE)[0] == app


@pytest.mark.parametrize("sentence", ["what's the weather today", "open it", "I'm done", "how are you doing", "tell me a joke", "start"])
def test_grammar_and_chat_never_count_as_an_app(sentence):
    assert pcsemantic.mentioned_apps(sentence, CATALOGUE) == []


@pytest.mark.parametrize("sentence", ["open spotify and set the volume to 30", "open notepad, then paint", "close it and open paint",
                                      "open chrome and then go to github", "launch notepad and tell me what the volume is at"])
def test_two_requests_in_one_sentence_are_a_compound_for_the_planner(sentence, monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: next((a for a in CATALOGUE if value.casefold().strip() in a), None))
    assert pcsemantic.compound(sentence)
    calls = []
    monkeypatch.setattr(pcsemantic, "_ask", lambda *a: calls.append(a) or {"action": "open", "app": "spotify"})
    assert pcsemantic.interpret(sentence) is None and calls == [], "a one-action choice would silently drop the other half"


@pytest.mark.parametrize("sentence", ["search youtube for cats and dogs", "open paint", "I'm done with chrome", "what's the weather and the time",
                                      "open the file explorer and nothing else"])
def test_one_request_with_an_and_inside_is_not_a_compound(sentence, monkeypatch):
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda value: next((a for a in CATALOGUE if value.casefold().strip() in a), None))
    assert not pcsemantic.compound(sentence)


def _decide(monkeypatch, action, app, is_open, calls):
    monkeypatch.setattr(pcsemantic, "running", lambda name: is_open)

    def ask(text, options, cancelled):
        calls.append(options)
        return {"action": action, "app": app}
    monkeypatch.setattr(pcsemantic, "_ask", ask)


def test_close_becomes_the_same_typed_close_plan_the_regexes_make(monkeypatch):
    calls = []
    _decide(monkeypatch, "close", "calculator", True, calls)
    assert pcsemantic.interpret("I'm done with the calculator") == {"steps": [{"tool": "close_app", "arguments": {"app": "calculator"}}]}
    assert calls and ("calculator", True) in calls[0], "the model is shown which apps are open"


def test_open_of_an_app_that_is_not_open_starts_it(monkeypatch):
    _decide(monkeypatch, "open", "notepad", False, [])
    assert pcsemantic.interpret("I need to jot something in notepad") == {"steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]}


def test_open_of_an_app_that_is_already_open_brings_it_forward_instead_of_launching_a_second_copy(monkeypatch):
    _decide(monkeypatch, "open", "notepad", True, [])
    assert pcsemantic.interpret("show me notepad")["steps"][0]["tool"] == "focus_app"


def test_none_unknown_apps_and_a_missing_model_mean_no_plan_and_normal_chat(monkeypatch):
    _decide(monkeypatch, "none", "calculator", True, [])
    monkeypatch.setattr(pcsemantic, "_ask", lambda *a: None)
    assert pcsemantic.interpret("tell me about the calculator") is None
    assert pcsemantic.interpret("what's the weather") is None


def test_social_talk_never_costs_a_model_call(monkeypatch):
    monkeypatch.setattr(pcsemantic, "_ask", lambda *a: (_ for _ in ()).throw(AssertionError("must not call the model for social talk")))
    assert pcsemantic.interpret("thanks a lot, that was great") is None


def test_the_plan_reaches_the_normal_planner_so_execution_and_verification_are_unchanged(monkeypatch):
    _decide(monkeypatch, "close", "google chrome", True, [])
    text = "that's enough browsing, I'm finished with chrome"
    assert pchybrid.simple_plan(text) is None, "no fixed wording matches"
    plan = pcsemantic.interpret(text)
    assert pchybrid.simple_plan(text) == plan
    from core import pcactions
    pcactions.validate(plan)
    assert pchybrid.direct_owner_close(plan, "local_automation")


def test_show_me_an_app_that_is_not_open_starts_it_instead_of_trying_to_switch_to_nothing(monkeypatch):
    _decide(monkeypatch, "focus", "calculator", False, [])
    assert pcsemantic.interpret("show me the calculator") == {"steps": [{"tool": "open_app", "arguments": {"app": "calculator"}}]}


def test_a_short_name_that_begins_a_windows_builtin_means_the_builtin():
    from tools import apps
    assert apps.resolve_app("calc") == "calculator"
    assert apps.resolve_app("calculator") == "calculator"


@pytest.mark.parametrize("sentence", ["how much memory is in here", "which linux flavor did I set up in wsl"])
def test_one_shared_word_does_not_select_an_app_with_a_longer_name(sentence):
    assert pcsemantic.mentioned_apps(sentence, CATALOGUE) == []


def test_a_goal_with_no_app_named_may_only_open_one_of_windows_simple_apps(monkeypatch):
    seen = {}

    def ask(text, options, cancelled):
        seen["names"] = [o[0] for o in options]
        return {"action": "open", "app": "calculator"}
    monkeypatch.setattr(pcsemantic, "_ask", ask)
    monkeypatch.setattr(pcsemantic, "running", lambda a: False)
    monkeypatch.setattr(pcsemantic, "_everyday_apps", lambda: ["calculator", "paint"])
    assert pcsemantic.interpret("I need to crunch some numbers") == {"steps": [{"tool": "open_app", "arguments": {"app": "calculator"}}]}
    assert seen["names"] == ["calculator", "paint"]


def test_a_goal_with_no_app_named_can_never_close_anything(monkeypatch):
    monkeypatch.setattr(pcsemantic, "_ask", lambda *a: {"action": "close", "app": "paint"})
    monkeypatch.setattr(pcsemantic, "running", lambda a: True)
    monkeypatch.setattr(pcsemantic, "_everyday_apps", lambda: ["calculator", "paint"])
    assert pcsemantic.interpret("I am so over this") is None


def test_a_task_inside_a_named_app_becomes_the_reviewed_use_app_tool(monkeypatch):
    monkeypatch.setattr(pcsemantic, "running", lambda a: False)
    monkeypatch.setattr(pcsemantic, "_ask", lambda *a: {"action": "operate", "app": "calculator", "goal": "work out 17 times 23"})
    plan = pcsemantic.interpret("use the calculator to work out 17 times 23")
    assert plan == {"steps": [{"tool": "use_app", "arguments": {"app": "calculator", "goal": "work out 17 times 23"}}]}
    from core import pcactions
    pcactions.validate(plan)
    assert pcactions.SCHEMAS["use_app"][1] is True, "always sensitive: shown for the owner's yes before anything runs"
    assert not pchybrid.direct_owner_close(plan, "local_automation")


def test_an_empty_goal_is_not_an_operate_request(monkeypatch):
    monkeypatch.setattr(pcsemantic, "running", lambda a: False)
    monkeypatch.setattr(pcsemantic, "_ask", lambda text, options, c: None)
    assert pcsemantic.interpret("use the calculator") is None
