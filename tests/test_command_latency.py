"""Command-turn latency changes (2026-11-13), each proved with fakes: no model, no server, no clock, no desktop.

What the real model measurement showed (scripts/command_latency.py) and what these tests pin:
  - every local model call built a fresh urllib opener (an HTTPS context that loads the Windows certificate store) 18 times
    per call: one opener per loopback server now;
  - the stop / dismiss guess ran before PC commands the gateway reads in code, and sometimes called a command a goodbye;
  - the planner prompt began with the minute-by-minute clock, so the server's cached prefix was lost every minute;
  - the fast lane and the planner wrote empty keys on every answer (decode time), which are now optional.
"""
import json

import pytest

from core import gateway, gateway_context, intelligence, pcsemantic


# --------------------------------------------------------------------------------------------- one opener per server
def test_the_local_opener_is_built_once_per_server(monkeypatch):
    built = []
    real = intelligence.urllib.request.build_opener
    monkeypatch.setattr(intelligence, "_openers", {})
    monkeypatch.setattr(intelligence.urllib.request, "build_opener", lambda *a, **k: built.append(a) or real(*a, **k))
    first = intelligence._validated_endpoint("http://127.0.0.1:11434/v1")
    for _ in range(20):                                     # one request asks for its endpoint many times over
        again = intelligence._validated_endpoint("http://127.0.0.1:11434/v1/")
    assert again[0] == first[0] == "http://127.0.0.1:11434/v1"
    assert again[1] is first[1] and len(built) == 1
    other = intelligence._validated_endpoint("http://127.0.0.1:1234/v1")
    assert other[1] is not first[1] and len(built) == 2


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://127.0.0.1:11434/v1", "http://127.0.0.1:11434/other",
                                 "http://user:pw@127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1?x=1"])
def test_only_a_plain_loopback_server_gets_an_opener(url, monkeypatch):
    monkeypatch.setattr(intelligence, "_openers", {})
    with pytest.raises(intelligence.LocalFailure):
        intelligence._validated_endpoint(url)
    assert intelligence._openers == {}


def test_the_cached_opener_still_refuses_redirects(monkeypatch):
    monkeypatch.setattr(intelligence, "_openers", {})
    _url, opener = intelligence._validated_endpoint("http://127.0.0.1:11434/v1")
    from core.pcbridge import _NoRedirect
    assert any(isinstance(h, _NoRedirect) for h in opener.handlers)


# ------------------------------------------------------------- the stop / dismiss guess skips what code reads as a command
@pytest.fixture
def asked(monkeypatch):
    """main.echo_free_meaning with a fake model that records every sentence it is asked and answers 'dismiss'."""
    import main
    from core import meaning
    seen = []
    monkeypatch.setattr(meaning, "choose", lambda text, meanings, context="", cancelled=None, ask=None, max_words=25: seen.append(text) or "dismiss")
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: ["notepad", "paint", "spotify", "calculator"])
    return seen


@pytest.mark.parametrize("said", ["focus notepad", "scroll down", "volume down a bit", "hide everything", "minimize spotify",
                                  "shut notepad down for me", "i'm finished with paint", "calculator please"])
def test_a_pc_command_never_waits_on_the_stop_guess(said, asked):
    import main
    assert main.echo_free_meaning(said) is None and asked == []


@pytest.mark.parametrize("said", ["mute it", "unmute", "a bit louder", "quieter please", "volume to thirty", "maximize paint", "skip this one",
                                  "resume the music", "switch to chrome"])
def test_command_verbs_the_list_used_to_miss_never_wait_on_the_stop_guess(said, asked):
    """Live-measured 2026-11-13: "Mute it." came back 'dismiss' 2 runs in 3."""
    import main
    assert main.echo_free_meaning(said) is None and asked == []


@pytest.mark.parametrize("said", ["zip it", "alright that's all for now", "okay we're good", "that'll be all"])
def test_what_is_not_a_command_is_still_guessed(said, asked):
    import main
    assert main.echo_free_meaning(said) == "dismiss" and asked == [said]


def test_a_power_action_keeps_the_guess(asked, monkeypatch):
    """A goodbye can sound like a shutdown: the exact matcher's power plan does not skip the guess."""
    import main
    monkeypatch.setattr(gateway, "exact", lambda text: gateway.Decision(
        [gateway.Intent("actions", "power", steps=[{"tool": "power", "arguments": {"action": "sleep"}}])], "exact"))
    assert main.echo_free_meaning("night night pc") == "dismiss" and asked == ["night night pc"]


def test_a_matcher_failure_means_the_guess_runs_as_before(asked, monkeypatch):
    import main

    def broken(text):
        raise RuntimeError("matcher down")
    monkeypatch.setattr(gateway, "exact", broken)
    assert main.echo_free_meaning("okay we're good") == "dismiss" and asked == ["okay we're good"]


# ------------------------------------------------------------------- the planner prompt: stable first, the clock last
def _ctx(text="open notepad and close paint", windows=()):
    return gateway_context.Context(said=text, pc={"windows": list(windows), "running": []}, pc_text="PC STATE: test",
                                   apps_mentioned=["notepad", "paint"], apps_running=["notepad"], skills=[], recent=[("owner", "hello")],
                                   pending="a guide plan: x")


def test_the_clock_is_the_last_line_of_the_context(monkeypatch):
    monkeypatch.setattr(gateway_context.time, "strftime", lambda fmt: "Friday 2026-11-13 12:01")
    lines = _ctx().render().split("\n")
    assert lines[-1] == "now: Friday 2026-11-13 12:01"
    assert lines[0].startswith("owner's folders")
    assert not any(l.startswith("now:") for l in lines[:-1])


def test_a_new_minute_changes_only_the_last_line(monkeypatch):
    ctx = _ctx()
    monkeypatch.setattr(gateway_context.time, "strftime", lambda fmt: "Friday 2026-11-13 12:01")
    a = ctx.render()
    monkeypatch.setattr(gateway_context.time, "strftime", lambda fmt: "Friday 2026-11-13 12:02")
    b = ctx.render()
    assert a != b and a.rsplit("\n", 1)[0] == b.rsplit("\n", 1)[0], "everything before the clock is the same bytes"


def test_the_same_facts_are_still_in_the_context(monkeypatch):
    """Only the order moved: every line the planner saw before is still there."""
    text = _ctx().render()
    for needle in ("PC STATE: test", "running apps: notepad", "apps the request may mean (installed): notepad, paint",
                   "STORED SKILLS that may fit: none", "RECENT CONVERSATION:\nowner: hello", "PENDING (waiting for the owner's answer): a guide plan: x"):
        assert needle in text, needle


def test_the_planner_message_leads_with_the_tool_list_and_ends_with_the_request(monkeypatch):
    sent = {}

    def request(role, system, history, tools, cancelled, response_format=None, **kw):
        sent["body"] = history[0]["content"]
        sent["system"] = system
        raise RuntimeError("stop here")
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    monkeypatch.setattr(intelligence, "request", request)
    with pytest.raises(RuntimeError):
        gateway._ask("advanced", "open notepad and close paint", _ctx(), lambda: False)
    body = sent["body"]
    assert body.startswith("TOOLS: ") and body.endswith("REQUEST: open notepad and close paint")
    assert body.index("TOOLS: ") < body.index("owner's folders") < body.index("now: ") < body.index("REQUEST: ")
    assert "now:" not in sent["system"]


# ----------------------------------------------------------------- shorter answers: empty keys are optional
def test_the_planner_schema_requires_only_kind_and_summary():
    schema = gateway.schema(_ctx())
    item = schema["properties"]["intents"]["items"]
    assert item["required"] == ["kind", "summary"]
    assert {"skill", "values", "steps", "question"} <= set(item["properties"])          # still allowed, no longer forced


def test_a_short_planner_answer_is_understood():
    ctx = _ctx("open notepad")
    out = gateway.validate({"intents": [{"kind": "actions", "summary": "open notepad", "steps": [
        {"tool": "open_app", "arguments": {"app": "notepad"}}]}], "covers_everything": True}, ctx)
    assert [s["tool"] for s in out[0].steps] == ["open_app"] and out[0].skill == "" and out[0].values == {}
    chat = gateway.validate({"intents": [{"kind": "chat", "summary": "talk"}], "covers_everything": True}, ctx)
    assert chat[0].kind == "chat" and chat[0].steps == [] and chat[0].question == ""


def test_a_skill_or_clarify_answer_still_has_to_carry_its_part():
    ctx = _ctx("how much free space is left")
    with pytest.raises(ValueError):
        gateway.validate({"intents": [{"kind": "skill", "summary": "free space"}], "covers_everything": True}, ctx)      # no skill named
    with pytest.raises(ValueError):
        gateway.validate({"intents": [{"kind": "clarify", "summary": "which?"}], "covers_everything": True}, ctx)        # no question


def test_the_planner_instruction_matches_the_schema():
    assert "leave every empty key out" in gateway._SYSTEM
    assert '"skill": ""' not in gateway._SYSTEM.split("\n")[0]


def test_the_fast_lane_answer_needs_a_goal_only_to_operate():
    schema = pcsemantic.answer_schema(["notepad", "paint"])
    plain, operate = schema["oneOf"]
    assert plain["required"] == ["action", "app"] and "goal" not in plain["properties"]
    assert plain["properties"]["action"]["enum"] == ["open", "close", "focus", "none"]
    assert operate["required"] == ["action", "app", "goal"] and operate["properties"]["action"] == {"const": "operate"}
    assert plain["properties"]["app"] == operate["properties"]["app"] == {"enum": ["notepad", "paint"]}
    json.dumps(schema)


@pytest.mark.parametrize("sentence,answer,plan", [
    ("could you do that notepad thing for me", {"action": "open", "app": "notepad"}, {"steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]}),
    ("could you do that paint thing for me", {"action": "close", "app": "paint"}, {"steps": [{"tool": "close_app", "arguments": {"app": "paint"}}]}),
    ("could you do that notepad thing for me", {"action": "operate", "app": "notepad", "goal": "type hello"},
     {"steps": [{"tool": "use_app", "arguments": {"app": "notepad", "goal": "type hello"}}]}),
])
def test_a_goalless_answer_makes_the_same_plan(sentence, answer, plan, monkeypatch):
    """What the model now writes (no goal key unless it operates) becomes the typed plan the old answer did."""
    pcsemantic._CACHE.clear()
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: ["notepad", "paint"])
    monkeypatch.setattr(pcsemantic, "running", lambda app: False)
    monkeypatch.setattr(intelligence, "enabled", lambda: True)

    class Block:
        type = "text"

        def __init__(self, text):
            self.text = text
    monkeypatch.setattr(intelligence, "request", lambda *a, **k: type("R", (), {"content": [Block(json.dumps(answer))]})())
    assert pcsemantic.interpret(sentence) == plan
    pcsemantic._CACHE.clear()


def test_a_goalless_operate_answer_is_no_plan(monkeypatch):
    pcsemantic._CACHE.clear()
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: ["notepad"])
    monkeypatch.setattr(pcsemantic, "running", lambda app: False)
    monkeypatch.setattr(intelligence, "enabled", lambda: True)

    class Block:
        type = "text"
        text = json.dumps({"action": "operate", "app": "notepad"})
    monkeypatch.setattr(intelligence, "request", lambda *a, **k: type("R", (), {"content": [Block()]})())
    assert pcsemantic.interpret("could you do that notepad thing for me") is None
    pcsemantic._CACHE.clear()
