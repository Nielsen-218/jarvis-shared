"""A pronoun is not an app and "go ahead" is not a seek (live 2026-11-15, found by probing the deployed gateway): "Yes, open it." opened
"git cmd" (the app resolver matched the word "it"), "launch them" would have opened Steam, and "Sure, go ahead." was read as "skip
forward 10 seconds" by the media recogniser. With nothing to refer to, nothing is done: the brain answers in words."""
import pytest

from core import gateway, mediastate, pchybrid


@pytest.mark.parametrize("said", ["open it", "Yes, open it.", "launch them", "start that", "run this", "Sure, open those.", "open something",
                                  "please open it", "open it up"])
def test_a_pronoun_is_never_an_app_to_open(said):
    plan = pchybrid.simple_plan(said)
    assert not plan or all(step["tool"] != "open_app" for step in plan["steps"]), plan


@pytest.mark.parametrize("said", ["Sure, go ahead.", "go ahead", "Yes, go ahead.", "okay go ahead"])
def test_go_ahead_alone_is_permission_not_a_seek(said):
    assert mediastate.parse_seek(said) is None
    plan = pchybrid.simple_plan(said)
    assert not plan or all(step["tool"] != "media_command" for step in plan["steps"]), plan


@pytest.mark.parametrize("said, seek", [("skip ahead", ("forward", 10)), ("rewind", ("back", 10)), ("fast forward 30 seconds", ("forward", 30)),
                                        ("go forward 10 seconds", ("forward", 10)), ("go back 20 seconds", ("back", 20)),
                                        ("rewind one minute", ("back", 60))])
def test_real_seek_commands_still_work(said, seek):
    assert mediastate.parse_seek(said) == seek


@pytest.mark.parametrize("said, app", [("open Notepad", "notepad"), ("launch Steam", "steam"), ("open the calculator", None)])
def test_named_apps_still_open(said, app):
    plan = pchybrid.simple_plan(said)
    assert plan and plan["steps"][0]["tool"] == "open_app"
    if app:
        assert plan["steps"][0]["arguments"]["app"] == app


@pytest.mark.parametrize("said", ["Yes, open it.", "open it", "Sure, go ahead.", "launch them"])
def test_the_gateway_decides_nothing_for_them(said, monkeypatch):
    monkeypatch.setenv("JARVIS_GATEWAY", "on")
    decision = gateway.decide(said, [{"role": "user", "content": "People say x. What is going on?"},
                                     {"role": "assistant", "content": "If you want the real headlines, say so and I'll pull them up."}])
    steps = [s for i in (decision.intents if decision else []) for s in (getattr(i, "steps", None) or [])]
    assert not [s for s in steps if s["tool"] in ("open_app", "media_command")], steps
