"""The API brain and teacher (owner 2026-11-21): Claude answers harder questions and plans PC jobs the local planner cannot;
every API use is logged; what the API planned and ran verified is kept, reused with no model call, shown to the local
planner as an example, and left open as a miss for the nightly re-study (which never calls the API). Nothing here calls
the real API: a fake client or a fake cloudmodel.complete stands in."""
import json
from types import SimpleNamespace

import pytest

from core import apiassist, chatturn, cloudmodel, gateway, intelligence, misses, warmth
from tools import registry

BRAIN = "qwen3.5:9b"
MANUAL = "MANUAL-OPERATIONS " + "Follow the operating rules. " * 40
PERSONA = "PERSONA-CORE You are J.A.R.V.I.S., talking with them out loud."


@pytest.fixture
def cloud(monkeypatch):
    """Claude is the cloud brain (the owner's settings), local-first, never local-only."""
    monkeypatch.setenv("JARVIS_CLOUD_FALLBACK", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.delenv("JARVIS_LOCAL_ONLY", raising=False)
    monkeypatch.delenv("JARVIS_PC_URL", raising=False)
    monkeypatch.setattr(intelligence, "local_only", lambda: False)
    monkeypatch.setitem(warmth._state, "game", False)


def _reply(text, **message):
    return {"model": "claude-opus-5-5", "choices": [{"finish_reason": "stop", "message": dict({"content": text}, **message)}],
            "usage": {}, "usd": 0.01}


# ------------------------------------------------------------------------------------------------ the Claude adapter

def test_local_messages_become_claude_messages():
    msgs = [{"role": "system", "content": "SYSTEM"},
            {"role": "assistant", "content": "Earlier reply."},
            {"role": "user", "content": [{"type": "text", "text": "Open Paint."},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "open_app", "arguments": "{\"app\": \"paint\"}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "Paint is open."}]
    system, out = cloudmodel.to_claude(msgs)
    assert system == "SYSTEM"
    assert out[0]["role"] == "user", "Claude's first message must be the user's"
    roles = [m["role"] for m in out]
    assert all(a != b for a, b in zip(roles, roles[1:])), "no two turns in a row from the same side"
    blocks = [b for m in out for b in m["content"]]
    assert {"type": "tool_use", "id": "c1", "name": "open_app", "input": {"app": "paint"}} in blocks
    assert {"type": "tool_result", "tool_use_id": "c1", "content": "Paint is open."} in blocks
    image = next(b for b in blocks if b["type"] == "image")
    assert image["source"] == {"type": "base64", "media_type": "image/png", "data": "QUJD"}


def test_the_plan_schema_is_made_claude_safe():
    schema = {"type": "object", "properties": {"s": {"type": "string", "maxLength": 60},
                                               "steps": {"type": "array", "maxItems": 8, "items": {"oneOf": [{"const": "a"}]}}},
              "additionalProperties": False}
    out = cloudmodel.claude_schema(schema)
    text = json.dumps(out)
    assert "maxLength" not in text and "maxItems" not in text and "oneOf" not in text
    assert out["properties"]["steps"]["items"] == {"anyOf": [{"const": "a"}]}
    assert out["additionalProperties"] is False


class _FakeMessages:
    def __init__(self, sent, reply):
        self.sent, self.reply = sent, reply

    def create(self, **body):
        self.sent.append(body)
        return self.reply


class _FakeClient:
    def __init__(self, sent, reply):
        self.beta = SimpleNamespace(messages=_FakeMessages(sent, reply))

    def with_options(self, **_):
        return self


def _claude_reply(blocks, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(content=blocks, stop_reason=stop, model="claude-opus-5-5", usage=usage)


def test_a_plan_request_sends_the_schema_and_comes_back_as_json(cloud, monkeypatch):
    sent = []
    monkeypatch.setattr(cloudmodel, "_client", _FakeClient(sent, _claude_reply([SimpleNamespace(type="text", text='{"a": 1}')])))
    recorded = []
    from core import usage
    monkeypatch.setattr(usage, "record", lambda model, u, **kw: recorded.append((model, kw)) or 0.0006)
    monkeypatch.setattr(usage, "over_budget", lambda: False)
    payload = {"role": "cloud", "messages": [{"role": "system", "content": "PLAN"}, {"role": "user", "content": "Do it."}],
               "tools": [], "response_format": {"type": "json_schema", "json_schema": {"schema": {"type": "object", "properties": {
                   "a": {"type": "integer", "maximum": 5}}, "additionalProperties": False}}}}
    out = cloudmodel.claude_complete(payload)
    body = sent[0]
    assert body["model"] == "claude-opus-5-5" and body["system"] == "PLAN"
    assert body["output_config"]["format"]["schema"]["properties"]["a"] == {"type": "integer"}
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in body["betas"]
    assert out["choices"][0]["message"]["content"] == '{"a": 1}' and out["usd"] == 0.0006
    assert recorded and recorded[0][1]["component"] == "cloud_brain_plan", "every call is metered"


def test_tool_calls_come_back_in_the_local_shape(cloud, monkeypatch):
    sent = []
    block = SimpleNamespace(type="tool_use", id="t1", name="open_app", input={"app": "paint"})
    monkeypatch.setattr(cloudmodel, "_client", _FakeClient(sent, _claude_reply([block], stop="tool_use")))
    from core import usage
    monkeypatch.setattr(usage, "record", lambda *a, **k: 0.0)
    monkeypatch.setattr(usage, "over_budget", lambda: False)
    tools = [{"type": "function", "function": {"name": "open_app", "description": "Open an app.",
                                               "parameters": {"type": "object", "properties": {"app": {"type": "string"}}}}}]
    out = cloudmodel.claude_complete({"messages": [{"role": "user", "content": "Open Paint."}], "tools": tools})
    assert sent[0]["tools"][0]["name"] == "open_app" and "input_schema" in sent[0]["tools"][0]
    choice = out["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"]) == {"app": "paint"}


def test_the_monthly_budget_still_stops_the_api(cloud, monkeypatch):
    from core import usage
    monkeypatch.setattr(usage, "over_budget", lambda: True)
    with pytest.raises(intelligence.LocalFailure, match="cloud_budget_reached"):
        cloudmodel.claude_complete({"messages": [{"role": "user", "content": "hi"}]})


def test_the_laptop_asks_the_pc_to_make_the_call(cloud, monkeypatch):
    from core import pcbridge
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("JARVIS_PC_URL", "https://pc.example")
    monkeypatch.setattr(pcbridge, "local_pc", lambda: False)
    relayed = []
    monkeypatch.setattr(pcbridge, "json_request", lambda path, method, body: relayed.append((path, body)) or _reply("From the PC."))
    monkeypatch.setattr(cloudmodel, "_client", None)
    assert cloudmodel.available(), "no key on the laptop: the PC's key answers"
    out = cloudmodel.claude_complete({"messages": [{"role": "user", "content": "hi"}]})
    assert relayed[0][0] == "/api/pc/cloud" and out["choices"][0]["message"]["content"] == "From the PC."
    assert cloudmodel._client is None, "the laptop never builds its own API client"


def test_the_reply_shape_crosses_the_relay_as_json(cloud, monkeypatch):
    block = SimpleNamespace(type="text", text="Hello.")
    out = cloudmodel._openai_shape(_claude_reply([block]))
    json.dumps(out)                                       # the PC route returns it as JSON to the laptop


def test_local_only_keeps_the_api_off(monkeypatch):
    monkeypatch.setenv("JARVIS_CLOUD_FALLBACK", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(intelligence, "local_only", lambda: True)
    assert not cloudmodel.available() and not intelligence.hard_first()


# ------------------------------------------------------------------------------------------- harder questions go first

def _local_never(monkeypatch):
    calls = []

    def complete_local(body, cancelled=lambda: False, on_text=None):
        calls.append(body)
        return {"model": BRAIN, "choices": [{"finish_reason": "stop", "message": {"content": "Local answer."}}]}
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    return calls


def test_a_hard_question_is_answered_by_the_api_and_logged(cloud, monkeypatch):
    local = _local_never(monkeypatch)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    asked = []
    monkeypatch.setattr(cloudmodel, "complete", lambda payload, cancelled=lambda: False: asked.append(payload) or
                        _reply("Light scatters off the air, blue the most."))
    msg = intelligence.request("fast", MANUAL, [{"role": "user", "content": "Why is the sky blue?"}], registry.schemas(),
                               chat_system=PERSONA)
    assert "scatters" in " ".join(b.text for b in msg.content)
    assert asked and not local, "the API answered; the local model was not asked first"
    assert asked[0]["tools"] == [] and "PERSONA-CORE" in str(asked[0]["messages"][0]["content"])
    rows = apiassist.recent()
    assert rows and rows[-1]["kind"] == "chat" and rows[-1]["why"] == "hard_question"
    assert rows[-1]["said"] == "Why is the sky blue?"


def test_small_talk_stays_local(cloud, monkeypatch):
    local = _local_never(monkeypatch)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    monkeypatch.setattr(cloudmodel, "complete", lambda *a, **k: pytest.fail("small talk never goes to the API"))
    intelligence.request("fast", MANUAL, [{"role": "user", "content": "I had a long day, honestly."}], registry.schemas(),
                         chat_system=PERSONA)
    assert local


def test_an_api_failure_falls_back_to_the_local_answer(cloud, monkeypatch):
    local = _local_never(monkeypatch)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])

    def broken(*a, **k):
        raise intelligence.LocalFailure("cloud_timeout")
    monkeypatch.setattr(cloudmodel, "complete", broken)
    msg = intelligence.request("fast", MANUAL, [{"role": "user", "content": "How does a VPN actually work?"}],
                               registry.schemas(), chat_system=PERSONA)
    assert local and "Local answer." in " ".join(b.text for b in msg.content)
    assert "failed" in apiassist.recent()[-1]["why"], "the failed API try is written down too"


def test_the_switch_sends_hard_questions_local_again(cloud, monkeypatch):
    monkeypatch.setenv("JARVIS_CLOUD_HARD", "off")
    local = _local_never(monkeypatch)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    monkeypatch.setattr(cloudmodel, "complete", lambda *a, **k: pytest.fail("off means off"))
    intelligence.request("fast", MANUAL, [{"role": "user", "content": "Why is the sky blue?"}], registry.schemas(),
                         chat_system=PERSONA)
    assert local


# ----------------------------------------------------------------------------------------- PC plans the local brain can't

class _Ctx:
    built_ms = 1.0
    said = ""

    def skill_ids(self):
        return []

    skills = []


def test_the_api_plans_what_the_local_planner_could_not(cloud, monkeypatch):
    plan = {"intents": [{"kind": "actions", "summary": "open paint", "steps": [{"tool": "open_app", "arguments": {"app": "paint"}}]}],
            "covers_everything": True}
    roles = []

    def ask(role, text, ctx, cancelled):
        roles.append(role)
        if role == "cloud":
            return plan
        raise ValueError("local could not plan it")
    monkeypatch.setattr(gateway, "_simple", lambda text, ctx: False)
    monkeypatch.setattr(gateway, "validate", lambda data, ctx, request=None: [gateway.Intent("actions", "open paint",
                                                                                              steps=data["intents"][0]["steps"])])
    decision = gateway.understand("Get me something to draw with.", _Ctx(), ask=ask)
    assert roles[-1] == "cloud" and roles.count("cloud") == 1
    assert decision.source == "cloud" and decision.steps()[0]["tool"] == "open_app"


def test_a_good_local_plan_never_calls_the_api(cloud, monkeypatch):
    roles = []

    def ask(role, text, ctx, cancelled):
        roles.append(role)
        return {"intents": [{"kind": "chat", "summary": "talk"}], "covers_everything": True}
    monkeypatch.setattr(gateway, "_simple", lambda text, ctx: False)
    monkeypatch.setattr(gateway, "validate", lambda data, ctx, request=None: [gateway.Intent("chat", "talk")])
    decision = gateway.understand("Tell me about Paint.", _Ctx(), ask=ask)
    assert "cloud" not in roles and decision.source == "advanced"


def test_the_nightly_re_study_never_calls_the_api(cloud, monkeypatch):
    roles = []

    def ask(role, text, ctx, cancelled):
        roles.append(role)
        raise ValueError("local could not plan it")
    monkeypatch.setattr(gateway, "_simple", lambda text, ctx: False)
    with intelligence.background_inference():
        gateway.understand("Get me something to draw with.", _Ctx(), ask=ask)
    assert "cloud" not in roles


# ------------------------------------------------------------------------------------------------ learning what it taught

def _cloud_decision(steps):
    return gateway.Decision([gateway.Intent("actions", "open paint", steps=steps)], "cloud")


def test_a_taught_plan_is_kept_and_reused_with_no_model_call(cloud, monkeypatch):
    steps = [{"tool": "open_app", "arguments": {"app": "paint"}}]
    assert apiassist.taught("Open Paint so I can draw.", _cloud_decision(steps))
    assert apiassist.plan_for("Jarvis, open Paint so I can draw please.") == steps, "same words, said a bit differently"
    monkeypatch.setattr(gateway, "_check_step", lambda step, ctx: step)
    decision = gateway.taught_decision("open paint so i can draw", _Ctx())
    assert decision is not None and decision.source == "taught" and decision.steps() == steps
    assert apiassist.examples_for("open paint so i can sketch")[0]["steps"] == steps, "similar wordings see the example"


@pytest.mark.parametrize("said", ["Close it.", "Do that again with Paint.", "Get me something to draw with."])
def test_a_request_that_points_at_the_moment_is_never_replayed(cloud, said):
    steps = [{"tool": "open_app", "arguments": {"app": "paint"}}]
    assert not apiassist.taught(said, _cloud_decision(steps)), "what 'it' means changes every time"
    assert apiassist.plan_for(said) is None
    row = [r for r in misses._read(misses.MISSES) if r.get("route") == "api"]
    assert row, "still written down to practise"


def test_a_target_he_never_named_is_never_replayed(cloud):
    steps = [{"tool": "open_app", "arguments": {"app": "spotify"}}]
    assert not apiassist.taught("Put on the music player.", _cloud_decision(steps))


def test_a_partial_plan_is_not_kept(cloud):
    partial = gateway.Decision([gateway.Intent("actions", "open notepad", steps=[{"tool": "open_app", "arguments": {"app": "notepad"}}])],
                               "cloud", covers=False)
    assert not apiassist.replayable("Open Notepad and email my boss.", partial)


def test_a_taught_miss_stays_open_for_practice(cloud):
    steps = [{"tool": "open_app", "arguments": {"app": "paint"}}]
    said = "Open Paint so I can draw."
    misses.record(said, "part of the request was not understood", route="gateway")    # the local miss a moment before
    apiassist.taught(said, _cloud_decision(steps))
    row = [r for r in misses._read(misses.MISSES) if r.get("said") == said and r.get("route") == "api"]
    assert row and row[-1]["reason"] == apiassist.TAUGHT_REASON, "written even right after the local miss"
    misses.recovered(said, "gateway")                       # the turn's success
    still = [r for r in misses.rows() if r.get("route") == "api"]
    assert still, "it worked because the API planned it: the API miss is not recovered"


def test_gemini_never_becomes_the_planner(cloud, monkeypatch):
    monkeypatch.setenv("JARVIS_CLOUD_FALLBACK", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    roles = []

    def ask(role, text, ctx, cancelled):
        roles.append(role)
        raise ValueError("local could not plan it")
    monkeypatch.setattr(gateway, "_simple", lambda text, ctx: False)
    gateway.understand("Open the thing.", _Ctx(), ask=ask)
    assert "cloud" not in roles


def test_one_api_try_per_request_even_when_think_falls_back(cloud, monkeypatch):
    _local_never(monkeypatch)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    monkeypatch.setattr(chatturn, "plan", lambda *a, **k: chatturn.THINK)
    tries = []

    def broken(payload, cancelled=lambda: False):
        tries.append(payload)
        raise intelligence.LocalFailure("cloud_timeout")
    monkeypatch.setattr(cloudmodel, "complete", broken)
    try:
        intelligence.request("fast", MANUAL, [{"role": "user", "content": "Why do stars twinkle?"}], registry.schemas(),
                             chat_system=PERSONA)
    except intelligence.LocalFailure:
        pass
    assert len(tries) == 1


def test_the_brains_one_more_try_never_pays(cloud, monkeypatch):
    _local_never(monkeypatch)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    monkeypatch.setattr(cloudmodel, "complete", lambda *a, **k: pytest.fail("a retry with a note never calls the API"))
    intelligence.request("fast", MANUAL, [{"role": "user", "content": "Why is the sky blue?"}], registry.schemas(),
                         chat_system=PERSONA, note="Answer only the newest question.")


def test_tool_blocks_become_text_for_a_tool_free_call():
    msgs = [{"role": "user", "content": "What's on my list?"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                                                                    "function": {"name": "read_list", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "eggs, milk"},
            {"role": "user", "content": "Why those?"}]
    _system, out = cloudmodel.to_claude(msgs, with_tools=False)
    kinds = {b["type"] for m in out for b in m["content"]}
    assert kinds == {"text"}, "no tool blocks in a request that defines no tools"


def test_tool_results_lead_a_mixed_user_turn():
    msgs = [{"role": "user", "content": "Open Paint."},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                                                                    "function": {"name": "open_app", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "done"},
            {"role": "user", "content": "Thanks."}]
    _system, out = cloudmodel.to_claude(msgs)
    last = out[-1]
    assert last["role"] == "user" and last["content"][0]["type"] == "tool_result"


def test_a_plan_that_no_longer_checks_out_is_planned_fresh(cloud, monkeypatch):
    apiassist._append("api-taught", {"at": 1.0, "said": "Open my old editor.", "key": apiassist.norm("Open my old editor."),
                                     "steps": [{"tool": "open_app", "arguments": {"app": "gone"}}]})

    def refuse(step, ctx):
        raise ValueError("app not installed: gone")
    monkeypatch.setattr(gateway, "_check_step", refuse)
    assert gateway.taught_decision("Open my old editor.", _Ctx()) is None


def test_the_learning_window_is_three_to_seven(monkeypatch):
    from datetime import datetime
    from core import scheduler
    monkeypatch.delenv("JARVIS_LEARN_UNTIL", raising=False)
    monkeypatch.delenv("JARVIS_LEARN_FROM", raising=False)
    monkeypatch.delenv("JARVIS_LEARN_HOURS", raising=False)
    assert scheduler.in_learning_hours(datetime(2026, 11, 22, 3, 0))
    assert scheduler.in_learning_hours(datetime(2026, 11, 22, 6, 59))
    assert not scheduler.in_learning_hours(datetime(2026, 11, 22, 7, 0))
    assert not scheduler.in_learning_hours(datetime(2026, 11, 22, 2, 59))
