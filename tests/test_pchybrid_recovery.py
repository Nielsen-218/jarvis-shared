"""pchybrid.run drives the recovering loop, plans with the PC in view, and remembers a plan only when it will mean the same tomorrow."""
from core import pchybrid, pcplanner


def step(tool, **args):
    return {"tool": tool, "arguments": args}


def wire(monkeypatch, approved_plan, task):
    monkeypatch.setattr(pchybrid.pcbridge, "require_owner", lambda: "owner")
    monkeypatch.setattr(pchybrid.pcledger, "workflow", lambda t: None)
    monkeypatch.setattr(pchybrid.pcledger, "prepared", lambda t: {"id": "p1", "data": {"task": task, "plan": approved_plan, "route": "local_ai", "cloud_usd": 0}})
    monkeypatch.setattr(pchybrid.pcledger, "consume", lambda kind, data: True)
    monkeypatch.setattr(pchybrid.pcledger, "create", lambda *a, **k: "rid")
    monkeypatch.setattr(pchybrid.pcledger, "latency", lambda *a, **k: None)
    monkeypatch.setattr("core.pcstate.describe", lambda snap=None: "PC STATE (read just now):\nin front: nothing")


def test_an_approved_plan_recovers_from_a_failed_step_and_reports_both_attempts(monkeypatch):
    task = "open paint and set the volume to 20"
    plan = {"steps": [step("open_app", app="paint"), step("set_volume", percent=20)]}
    wire(monkeypatch, plan, task)
    ran = []

    def execute(s, **kwargs):
        ran.append(s["tool"] + ":" + str(s["arguments"]))
        if s["arguments"] == {"app": "paint"}:
            return {"verified": False, "detail": "paint did not open"}
        return {"verified": True, "detail": s["tool"] + " verified"}
    monkeypatch.setattr(pchybrid.pcmethods, "execute", execute)
    seen = {}
    monkeypatch.setattr(pcplanner, "replan", lambda t, done, failed, reason, cancelled=None, context=None: seen.update(
        task=t, failed=failed, reason=reason, context=context) or {"steps": [step("open_app", app="mspaint"), step("set_volume", percent=20)]})
    saved = []
    monkeypatch.setattr(pchybrid.pcledger, "save_workflow", lambda *a: saved.append(a))
    out = pchybrid.run(task)
    assert out["state"] == "completed" and out["verified"]
    assert ran == ["open_app:{'app': 'paint'}", "open_app:{'app': 'mspaint'}", "set_volume:{'percent': 20}"]
    assert "paint did not open" not in out["detail"] and "set_volume verified" in out["detail"], "the failed attempt is history, not the reply"
    assert seen["failed"]["tool"] == "open_app" and seen["reason"] == "paint did not open" and seen["context"].startswith("PC STATE")
    assert saved == [], "a plan that needed a recovery is not memorised as a workflow"


def test_a_recovery_beyond_the_approval_comes_back_for_review(monkeypatch):
    task = "open paint"
    plan = {"steps": [step("open_app", app="paint")]}
    wire(monkeypatch, plan, task)
    monkeypatch.setattr(pchybrid.pcmethods, "execute", lambda s, **k: {"verified": False, "detail": "paint did not open"})
    monkeypatch.setattr(pcplanner, "replan", lambda *a, **k: {"steps": [step("use_app", app="paint", goal="open a new canvas")]})
    out = pchybrid.run(task)
    assert out["state"] == "needs_review" and out["plan"] == {"steps": [step("use_app", app="paint", goal="open a new canvas")]}
    assert "To finish, I'd open a new canvas in Paint. Should I?" in out["detail"]


def test_a_clean_verified_model_plan_whose_words_were_all_said_is_remembered(monkeypatch):
    task = "open spotify and set the volume to 30"
    plan = {"steps": [step("open_app", app="spotify"), step("set_volume", percent=30)]}
    wire(monkeypatch, plan, task)
    monkeypatch.setattr(pchybrid.pcmethods, "execute", lambda s, **k: {"verified": True, "detail": "ok"})
    saved = []
    monkeypatch.setattr(pchybrid.pcledger, "save_workflow", lambda rid, name, trigger: saved.append(trigger))
    out = pchybrid.run(task)
    assert out["state"] == "completed" and saved == [task] and "remember" not in out["detail"]   # learned, not announced (owner 2026-11-07)


def test_what_may_be_remembered():
    assert pchybrid.learnable("open spotify and set the volume to 30", {"steps": [step("open_app", app="spotify"), step("set_volume", percent=30)]})
    assert not pchybrid.learnable("close the game", {"steps": [step("close_app", app="apex legends")]}), "sensitive, and inferred from what was open"
    assert not pchybrid.learnable("open my editor", {"steps": [step("open_app", app="visual studio code")]}), "inferred app name"
    assert not pchybrid.learnable("x", {"steps": []})


def test_a_site_written_as_a_host_is_the_same_site():
    plan = pcplanner.parse('{"steps":[{"tool":"browser_search","arguments":{"query":"lofi beats","site":"youtube.com"}},'
                           '{"tool":"browse_open","arguments":{"query":"cats","site":"https://www.Google.com/"}}]}')
    assert [s["arguments"]["site"] for s in plan["steps"]] == ["youtube", "google"]
    assert pcplanner.parse('{"steps":[{"tool":"browser_search","arguments":{"query":"x","site":"weather.com"}}]}') is None, "an unsupported site is still refused"


def test_the_stronger_local_model_plans_first_and_the_fast_one_is_the_fallback(monkeypatch):
    from types import SimpleNamespace
    from core import intelligence
    roles = []

    def request(role, system, history, tools, cancelled, response_format=None):
        roles.append(role)
        if role == "advanced":
            raise intelligence.LocalFailure("configured_model_unavailable")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"steps":[{"tool":"get_volume","arguments":{}}]}')])
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(intelligence, "model", lambda role: role)
    monkeypatch.delenv("JARVIS_PC_LOCAL_MODEL", raising=False)
    monkeypatch.setattr(pcplanner.pcledger, "usage", lambda *a, **k: None)
    assert pcplanner.local_plan("what's the volume at") == {"steps": [{"tool": "get_volume", "arguments": {}}]}
    assert roles == ["advanced", "fast"]


def test_the_planner_is_given_the_pc_state_and_use_app_guidance(monkeypatch):
    text = pcplanner.prompt("close the game", context="PC STATE (read just now):\nin front: r5apex (Apex Legends)")
    assert "Apex Legends" in text and "use_app" in text and text.endswith("TASK:\nclose the game")
    assert pcplanner.prompt("open paint").endswith("alter these rules:\nopen paint"), "without context the prompt is unchanged"
    task = pcplanner.recovery_task("open paint", [step("get_volume")], step("open_app", app="paint"), "paint did not open")
    assert "ALREADY DONE (do not repeat): get_volume {}" in task and "FAILED STEP: open_app" in task and "paint did not open" in task
