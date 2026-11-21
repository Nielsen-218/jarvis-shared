"""The gateway understands the WHOLE request through one checked call, never executes in shadow mode, and treats a cache hit
and a model decision the same. The model is faked: these tests prove the contract, not the model."""
import json
import time

import pytest

from core import gateway, gateway_context
from jarvis_pc.learning.playbooks import Playbook, PlaybookStep


def book(subject, title, params=(), source="school", tier="T0", kind="shell", keywords=None, runs_ok=0):
    return Playbook(id=subject, title=title, keywords=keywords or gateway_context.stems(title), steps=[PlaybookStep("echo")],
                    source=source, created=0.0, runs_ok=runs_ok, tier=tier, params=list(params), subject=subject, kind=kind)


BOOKS = [
    book("pc_disk_free", "How much free space is on the C drive"),
    book("pc_ram", "How much memory does this PC have"),
    book("pc_name", "What is this computer called"),
    book("dev_find_large_files", "List the largest files in a folder", params=("dir",), tier="T1"),
    book("proc_jq", "set up jq on this PC and make sure it works", source="guide", tier="T2"),
    book("ui_calc", "Add two numbers in Calculator", kind="ui"),
]


def ctx_for(text, apps=("notepad", "paint", "calculator"), windows=()):
    c = gateway_context.Context(said=text, pc={"windows": list(windows), "running": []}, pc_text="PC STATE: test", apps_mentioned=[a for a in apps if a in text.casefold()],
                                apps_running=[], skills=gateway_context.skill_candidates(text, BOOKS), recent=[], pending=None)
    return c


def reply(*intents, covers=True):
    return {"intents": [{"kind": "chat", "summary": "", "skill": "", "values": [], "steps": [], "question": "", **i} for i in intents],
            "covers_everything": covers}


# ------------------------------------------------------------------------------------------- skill discovery
def test_parameterized_and_procedure_skills_are_discoverable_with_their_parameter_names():
    cards = gateway_context.skill_candidates("which files are hogging space in my downloads folder", BOOKS)
    ids = [c.id for c in cards]
    assert "dev_find_large_files" in ids and "proc_jq" in ids, ids
    large = next(c for c in cards if c.id == "dev_find_large_files")
    assert large.params == ["dir"] and "needs: dir" in large.line()


def test_only_fitting_skills_are_shown_not_the_whole_store():
    cards = gateway_context.skill_candidates("my drive feels cramped, how much room is left", BOOKS)
    assert cards[0].id == "pc_disk_free"
    assert "pc_ram" not in [c.id for c in cards if c.score >= 1]


def test_a_changing_skill_that_does_more_than_asked_is_refused_and_not_offered_again():
    """2026-11-06: 'create a folder called reports' picked 'create the folder {dir} and put a file note.txt in it'."""
    from jarvis_pc.learning.playbooks import PlaybookStep as Step
    import os
    note = Playbook(id="job_make_note", title="create the folder {dir} and put a file note.txt in it that says {word}",
                    keywords=gateway_context.stems("create folder put file note"), source="school", created=0.0, runs_ok=1, tier="T2",
                    params=["dir", "word"], subject="job_make_note", kind="shell",
                    steps=[Step("New-Item -Path {dir} -ItemType Directory"), Step('Set-Content -Path "{dir}\\note.txt" -Value {word}')])
    text = "create a folder called reports in my Documents"
    c = gateway_context.Context(said=text, pc={"windows": [], "running": []}, pc_text="PC STATE: test", apps_mentioned=[], apps_running=[],
                                skills=gateway_context.skill_candidates(text, [note]), recent=[], pending=None)
    assert "job_make_note" in c.skill_ids()
    home = os.path.expanduser("~")
    offered = []

    def ask(role, said, ctx, cancelled):
        offered.append(ctx.skill_ids())
        if len(offered) == 1:
            return reply({"kind": "skill", "skill": "job_make_note", "values": [{"name": "dir", "value": home + "\\Documents\\reports"},
                                                                                {"name": "word", "value": "reports"}]})
        return reply({"kind": "actions", "steps": [{"tool": "run_terminal", "arguments": {"command": f"mkdir {home}\\Documents\\reports"}}]})
    d = gateway.understand(text, c, ask=ask)
    assert offered[0] == ["job_make_note"] and offered[1] == [], "the refused skill is not offered to the second attempt"
    assert [s["tool"] for s in d.steps()] == ["run_terminal"] and any("note" in a and "job_make_note" in a for a in d.attempts)


# ------------------------------------------------------------------------------------------- exact cache
def test_a_regex_wording_is_a_cache_hit_with_the_same_typed_shape():
    d = gateway.exact("open notepad")
    assert d and d.source == "exact" and d.steps() == [{"tool": "open_app", "arguments": {"app": "notepad"}}]
    assert gateway.exact("what's a good name for a cat") is None


# ------------------------------------------------------------------------------------------- understanding contract
def test_a_two_part_request_yields_two_steps_in_order(monkeypatch):
    monkeypatch.setattr(gateway, "_model_name", lambda i, r: r)
    c = ctx_for("open notepad and type hello world")
    asked = []

    def ask(role, text, ctx, cancelled):
        asked.append(role)
        return reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "notepad"}},
                                                    {"tool": "type_text", "arguments": {"app": "notepad", "text": "hello world"}}]})
    d = gateway.understand("open notepad and type hello world", c, ask=ask)
    assert asked == ["advanced"], "a conjunction is never a fast-model job"
    assert [s["tool"] for s in d.steps()] == ["open_app", "type_text"] and d.covers


def test_a_reply_that_drops_part_of_the_request_is_rejected_and_the_next_tier_is_asked():
    c = ctx_for("open notepad")                      # short, one app: fast first
    calls = []

    def ask(role, text, ctx, cancelled):
        calls.append(role)
        if role == "fast":
            return reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]}, covers=False)
        return reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]},
                     {"kind": "clarify", "question": "What should I type?"})
    d = gateway.understand("open notepad", c, ask=ask)
    assert calls == ["advanced"] and d.source == "advanced", "the fast lane missed (no local model here), so the advanced model decides"
    assert [i.kind for i in d.intents] == ["actions", "clarify"] and d.attempts == ["fast: lane miss"]


def test_a_reply_that_drops_part_of_the_request_is_rejected():
    c = ctx_for("open notepad and type hello world")
    with pytest.raises(ValueError, match="left out"):
        gateway.validate(reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]}, covers=False), c)


def test_the_fast_lane_reuses_the_compact_app_question_for_a_single_named_app(monkeypatch):
    from core import intelligence, pcsemantic
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    monkeypatch.setattr(intelligence, "model", lambda role: "fake-" + role)
    monkeypatch.setattr(pcsemantic, "interpret", lambda text, cancelled=None: {"steps": [{"tool": "close_app", "arguments": {"app": "paint"}}]})
    d = gateway.understand("bye bye paint", ctx_for("bye bye paint"), ask=lambda *a: (_ for _ in ()).throw(AssertionError("advanced asked")))
    assert d.source == "fast" and d.model == "fake-fast" and d.steps() == [{"tool": "close_app", "arguments": {"app": "paint"}}]


def test_a_dropped_clause_is_caught_without_trusting_the_models_claim_and_asked_again():
    text = "open notepad and type hello world"
    c = ctx_for(text)
    one = reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]})     # covers_everything: true, yet half
    with pytest.raises(ValueError, match="type hello world"):
        gateway.validate(one, c)
    asked = []

    def ask(role, text, ctx, cancelled):
        asked.append(text)
        if len(asked) == 1:
            return one
        return reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "notepad"}},
                                                    {"tool": "type_text", "arguments": {"app": "notepad", "text": "hello world"}}]})
    d = gateway.understand(text, c, ask=ask)
    assert len(asked) == 2 and asked[1].startswith("(Your last answer part of the request was left out") and [s["tool"] for s in d.steps()] == ["open_app", "type_text"]
    assert gateway.uncovered("open notepad", [gateway.Intent("actions", steps=[{"tool": "open_app", "arguments": {"app": "notepad"}}])]) == ""
    assert gateway.uncovered("search youtube for cats and dogs", [gateway.Intent("actions", steps=[{"tool": "open_url", "arguments": {"url": "x"}}])]) == "", \
        "'and' inside one request is not a second clause"


def test_the_models_own_commentary_is_not_a_part_of_the_request():
    c = ctx_for("open notepad")
    intents = gateway.validate(reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]},
                                     {"kind": "chat", "summary": "The request is complete"}), c)
    assert [i.kind for i in intents] == ["actions"]
    kept = gateway.validate(reply({"kind": "chat", "summary": "The request is complete"}), c)
    assert [i.kind for i in kept] == ["chat"], "with nothing else there, nothing is dropped"


def test_a_chat_aside_next_to_real_work_does_not_make_the_decision_a_mix():
    d = gateway.Decision([gateway.Intent("chat", "I'm good"), gateway.Intent("actions", steps=[{"tool": "close_app", "arguments": {"app": "calculator"}}])], "advanced")
    assert d.kind == "actions"
    mixed = gateway.Decision([gateway.Intent("actions", steps=[{"tool": "open_app", "arguments": {"app": "notepad"}}]), gateway.Intent("skill", skill="pc_disk_free")], "advanced")
    assert mixed.kind == "multi"


def test_an_app_that_is_not_installed_or_a_skill_not_offered_is_rejected():
    c = ctx_for("open photoshop")
    with pytest.raises(ValueError):
        gateway.validate(reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "photoshop"}}]}), c)
    with pytest.raises(ValueError):
        gateway.validate(reply({"kind": "skill", "skill": "pc_bogus"}), c)


def test_a_parameterized_skill_without_its_value_becomes_one_question():
    text = "list the biggest files"
    c = ctx_for(text)
    intents = gateway.validate(reply({"kind": "skill", "skill": "dev_find_large_files"}), c)
    assert intents[0].kind == "clarify" and "dir" in intents[0].question
    filled = gateway.validate(reply({"kind": "skill", "skill": "dev_find_large_files", "values": [{"name": "dir", "value": "C:\\x"}]}), c)
    assert filled[0].kind == "skill" and filled[0].values == {"dir": "C:\\x"}


def test_planned_verbs_are_understood_and_typed_even_before_they_execute():
    c = ctx_for("find my shadow world project and launch it")
    intents = gateway.validate(reply({"kind": "actions", "steps": [{"tool": "find_project", "arguments": {"name": "shadow world"}},
                                                                   {"tool": "open_path", "arguments": {"path": "<found>"}}]}), c)
    assert [s["tool"] for s in intents[0].steps] == ["find_project", "open_path"]
    with pytest.raises(ValueError):
        gateway.validate(reply({"kind": "actions", "steps": [{"tool": "find_project", "arguments": {"nme": "x"}}]}), c)


def test_simple_means_short_single_target_only():
    assert gateway._simple("open notepad", ctx_for("open notepad"))
    assert gateway._simple("I want to scribble something, start paint", ctx_for("I want to scribble something, start paint")), "an aside is not a second request"
    assert not gateway._simple("I need to crunch some numbers", ctx_for("I need to crunch some numbers")), "a bare goal needs the advanced model"
    assert not gateway._simple("get jq going on this machine", ctx_for("get jq going on this machine"))
    assert not gateway._simple("open notepad and paint", ctx_for("open notepad and paint"))
    assert not gateway._simple("could you please bring up the notepad window for me right now", ctx_for("could you please bring up the notepad window for me right now"))


def test_the_fast_lane_refuses_an_open_for_a_sentence_that_asks_to_do_something_inside(monkeypatch):
    from core import intelligence, pcsemantic
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    monkeypatch.setattr(pcsemantic, "interpret", lambda text, cancelled=None: {"steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]})
    assert gateway.fast_lane("type hello world into notepad", ctx_for("type hello world into notepad")) is None


def test_a_twice_admitted_gap_is_reported_with_the_work_not_hidden():
    c = ctx_for("open notepad")
    calls = []

    def ask(role, text, ctx, cancelled):
        calls.append(text)
        return reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]}, covers=False)
    d = gateway.understand("open notepad", c, ask=ask)
    assert len(calls) == 2 and "ask one question" in calls[1]
    assert d.covers is False and [s["tool"] for s in d.steps()] == ["open_app"]


def test_the_fast_skill_lane_never_answers_an_advice_question_with_a_fact(monkeypatch):
    from core import intelligence, skillrouter
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    monkeypatch.setattr(skillrouter, "_ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    text = "how do I free up disk space on windows"
    assert gateway.fast_lane(text, ctx_for(text)) is None


def test_duplicate_intents_collapse_and_a_skill_id_on_a_chat_intent_is_ignored():
    c = ctx_for("what box am I on")
    intents = gateway.validate(reply({"kind": "skill", "skill": "pc_name"}, {"kind": "skill", "skill": "pc_name"}, {"kind": "chat", "skill": "pc_ram"}), c)
    assert [(i.kind, i.skill) for i in intents] == [("skill", "pc_name"), ("chat", "")]
    assert "ui_calc" not in [cd.id for cd in gateway_context.skill_candidates("add two numbers in calculator", BOOKS)], "UI skills are not offered yet"


def test_fact_skills_are_always_offered_after_the_matches_and_read_screen_may_name_a_window():
    cards = gateway_context.skill_candidates("remind me what I named this pc", BOOKS)
    assert "pc_name" in [c.id for c in cards] and cards[0].score >= 0
    plain = gateway_context.skill_candidates("open paint and set the volume to 20", BOOKS)
    assert all(c.procedure for c in plain), "a plain action sentence gets no fact list (a learned procedure sharing a word may still appear)"
    c = ctx_for("look at my screen", windows=[{"process": "claude", "title": "Claude"}])
    ok = gateway.validate(reply({"kind": "actions", "steps": [{"tool": "read_screen", "arguments": {"app": "claude"}}]}), c)
    assert ok[0].steps[0]["arguments"] == {"app": "claude"}
    gateway.validate(reply({"kind": "actions", "steps": [{"tool": "read_screen", "arguments": {"app": ""}}]}), c)


# ------------------------------------------------------------------------------------------- shadow mode
def test_shadow_records_the_decision_next_to_the_legacy_route_and_runs_nothing(monkeypatch):
    from core import pcledger, pcmethods
    rows = []
    monkeypatch.setattr(pcledger, "usage", lambda component, **f: rows.append((component, f)))
    monkeypatch.setattr(pcmethods, "execute", lambda *a, **k: (_ for _ in ()).throw(AssertionError("executed in shadow")))
    monkeypatch.setattr(gateway, "decide", lambda text, history=None: gateway.Decision(
        [gateway.Intent("actions", steps=[{"tool": "close_app", "arguments": {"app": "paint"}}])], "advanced", model="m", latency_ms=12))
    row = gateway.shadow("I'm done with paint, get rid of it", "pcfast")
    assert rows[0][0] == "gateway_shadow" and row["legacy"] == "pcfast"
    assert row["decision"]["intents"][0]["steps"][0]["tool"] == "close_app"
    assert row["authz"] == {"ask": False, "reasons": [], "effects": ["reversible"]}


def test_shadow_later_is_a_no_op_when_the_gateway_is_off_and_a_background_thread_otherwise(monkeypatch):
    monkeypatch.delenv("JARVIS_GATEWAY", raising=False)
    assert gateway.shadow_later("open paint", "pcfast") is None
    monkeypatch.setenv("JARVIS_GATEWAY", "shadow")
    seen = []
    monkeypatch.setattr(gateway, "shadow", lambda text, legacy, history=None: seen.append((text, legacy)))
    t = gateway.shadow_later("open paint", "pcfast", [{"role": "user", "content": "hi"}])
    t.join(5)
    assert seen == [("open paint", "pcfast")]


def test_the_brain_schedules_a_shadow_after_every_reply(monkeypatch):
    from brain import llm
    from core import pcfast, quick
    monkeypatch.setenv("JARVIS_GATEWAY", "shadow")
    seen = []
    monkeypatch.setattr(gateway, "shadow_later", lambda text, legacy, history=None: seen.append((text, legacy)))
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: "paint has a visible application window.")
    brain = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    said = []
    brain.ask("open paint", said.append)
    assert said == ["paint has a visible application window."] and seen == [("open paint", "pcfast")]


def test_schema_lists_every_verb_and_only_the_offered_skills():
    c = ctx_for("how much free space is left")
    s = gateway.schema(c)
    tools = [v["properties"]["tool"]["const"] for v in s["properties"]["intents"]["items"]["properties"]["steps"]["items"]["oneOf"]]
    assert "open_app" in tools and "find_project" in tools and "type_text" in tools
    assert s["properties"]["intents"]["items"]["properties"]["skill"]["enum"] == c.skill_ids() + [""]
    json.dumps(s)


# ------------------------------------------------------------------------------------------- narration is not a request
@pytest.mark.parametrize("said", [
    # live 2026-11-10 while he showed Jarvis how to save in Blender: -> 'Opened Downloads...' and 'Closed blender 5.2.'
    "I'm going to go in and press save here... I'm going to click on save.",
    "Once I'm done clicking on Save, the window will close. Okay?",
    "Here I am.", "I'll press save.", "That's the save button, right?",
])
def test_his_narration_is_talk_never_a_pc_action(said):
    d = gateway.exact(said)
    assert d is not None and d.kind == "chat" and not d.steps(), said


@pytest.mark.parametrize("said", ["I need you to close Blender", "close blender, okay?", "After I save, close Blender",
                                  "I'm going to need Notepad open", "Is Notepad open, right?",
                                  "When I say practice mode, remind me to stretch, okay?"])
def test_a_request_said_around_narration_still_asks(said):
    assert not gateway.narration(said)
    d = gateway.exact(said)
    assert d is None or d.intents[0].summary != "his own narration"


@pytest.mark.parametrize("said", ["I'm going to bed so turn off the PC.", "Next song, okay?", "Volume to 50, okay?"])
def test_a_command_after_a_lead_in_or_before_a_tag_is_not_narration(said):
    """Review 2026-11-10: with no comma before the command (Whisper's punctuation), or no verb before the tag, a request was
    'his own narration' and nothing was done."""
    assert not gateway.narration(said)


def test_a_command_with_a_tag_question_still_goes_to_the_planner(monkeypatch):
    from pathlib import Path
    from tools import apps as _apps
    real = _apps._installed                                    # Blender counts as installed whatever this PC has
    monkeypatch.setattr(_apps, "_installed", lambda: {**real(), "blender 5.2": Path("blender-launcher.exe")})
    """'close blender, okay?' is a command: exact() leaves it to the model as it did before the narration rule (it is not
    talk), and the plain command is still the exact close."""
    assert gateway.exact("Close blender, okay?") is None
    d = gateway.exact("close blender")
    assert d is not None and d.kind == "actions" and d.steps()[0]["tool"] == "close_app"


def test_while_he_demonstrates_every_line_is_talk(monkeypatch):
    from core import demonstrate
    monkeypatch.setitem(demonstrate._state, "recorder", object())
    d = gateway.exact("open notepad")
    assert d.kind == "chat" and d.intents[0].summary == "watching a demonstration"
    monkeypatch.setitem(demonstrate._state, "recorder", None)
    assert gateway.exact("open notepad").kind == "actions"


def test_the_planner_has_a_wall_clock_ceiling_over_both_tries(monkeypatch):
    """Audit-pc-control defect 5: one advanced call ran 293 s. Past the ceiling the model call stops (its stream checks
    `cancelled`) and the request is reported as not understood, not retried."""
    clock = [1000.0]
    monkeypatch.setattr(gateway, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(gateway, "PLAN_CEILING_S", 30.0)
    calls = []

    def ask(role, text, ctx, cancelled):
        calls.append(role)
        assert not cancelled()
        clock[0] += 45                                  # the model thinks for 45 s...
        assert cancelled(), "...and the stream, checking between chunks, is told to stop"
        raise RuntimeError("cancelled")
    d = gateway.understand("rearrange my whole desktop somehow", ctx_for("rearrange my whole desktop somehow"), ask=ask)
    assert calls == ["advanced"], "no second try after the ceiling"
    assert d.source == "none" and d.intents == [] and any("too slow" in a for a in d.attempts)


def test_a_slow_first_try_inside_the_ceiling_still_gets_its_second_try(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(gateway, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(gateway, "PLAN_CEILING_S", 30.0)
    calls = []

    def ask(role, text, ctx, cancelled):
        calls.append(role)
        clock[0] += 10
        if len(calls) == 1:
            raise RuntimeError("model hiccup")
        return reply({"kind": "actions", "steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]})
    d = gateway.understand("open notepad", ctx_for("open notepad"), ask=ask)
    assert calls == ["advanced", "advanced"] and d.source == "advanced"
