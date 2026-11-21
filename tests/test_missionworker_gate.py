"""Mark VI C10: the mission worker's one gate (may_work), its backoff, its budget and the mission-active marker. Fakes only:
no real input, GPU, game or model is touched."""
import pytest

from core import acquire, craftjobs, inputwatch, intelligence, missionworker as mw, pcmethods, warmth


@pytest.fixture
def calm(monkeypatch):
    """Everything says: the owner is away and the PC is free."""
    monkeypatch.setattr(warmth, "gaming", lambda: False)
    monkeypatch.setattr(warmth, "practising", lambda: False)
    monkeypatch.setattr(inputwatch, "keyboard_mouse_idle", lambda: 5000.0)
    monkeypatch.setattr(inputwatch, "controller_idle", lambda: 5000.0)
    monkeypatch.setattr(inputwatch, "controller_used_within", lambda s: False)
    monkeypatch.setattr(inputwatch, "turn_idle", lambda: 5000.0)
    monkeypatch.setattr(acquire, "gpu_busy", lambda samples=3: False)
    monkeypatch.setattr(pcmethods, "abandoned", lambda: [])
    monkeypatch.setattr(craftjobs, "gpu_state", lambda need_mb=7000, **k: {"ok": True})
    monkeypatch.delenv("JARVIS_MISSION_QUIET_WORK", raising=False)
    return monkeypatch


def test_a_calm_pc_may_work(calm):
    assert mw.may_work() == "" and mw.may_work(heavy_gpu=True) == ""


@pytest.mark.parametrize("patch, reason", [
    ((warmth, "gaming", lambda: True), "game"),
    ((warmth, "practising", lambda: True), "practice"),
    ((acquire, "gpu_busy", lambda samples=3: True), "gpu busy"),
    ((pcmethods, "abandoned", lambda: [("a", None, None), ("b", None, None)]), "stuck steps"),
])
def test_each_gate_says_why(calm, patch, reason):
    calm.setattr(*patch)
    assert mw.may_work() == reason


def test_the_game_wins_over_everything_else(calm):
    calm.setattr(warmth, "gaming", lambda: True)
    calm.setattr(inputwatch, "keyboard_mouse_idle", lambda: 1.0)
    assert mw.may_work() == "game"


def test_light_use_at_the_pc_never_holds_missions(calm):
    """Owner 2026-11-10: 'if I don't say nothing and I'm opening browsers and stuff it's fine' - his keyboard and mouse
    no longer hold light steps; his words (missions.set_hold) and a game do."""
    calm.setattr(inputwatch, "keyboard_mouse_idle", lambda: 0.5)
    assert mw.may_work() == ""


def test_a_heavy_graphics_job_still_waits_for_him_and_steps_aside(calm):
    """A 7 GB graphics job is not a light task: it never starts while he types, and a running one yields when he's back."""
    calm.setattr(inputwatch, "keyboard_mouse_idle", lambda: 0.5)
    assert mw.may_work(heavy_gpu=True) == "owner at the PC"
    assert mw.may_work(check_gpu=False, heavy_running=True) == "owner at the PC"


def test_a_controller_in_use_means_a_game_even_an_unknown_one(calm):
    calm.setattr(inputwatch, "controller_used_within", lambda s: True)
    assert mw.may_work() == "controller in use"


def test_talking_with_jarvis_gives_him_the_brain_first(calm):
    calm.setattr(inputwatch, "turn_idle", lambda: 5.0)            # he spoke to Jarvis 5 s ago
    assert mw.may_work() == "talking with him"
    calm.setattr(inputwatch, "turn_idle", lambda: mw.TALK_QUIET_S + 1)
    assert mw.may_work() == ""


def test_a_controller_never_seen_is_not_in_use(monkeypatch):
    """The live worker waited 10 minutes after every restart because an unseen pad counted as 'maybe in use'."""
    monkeypatch.setattr(inputwatch, "start", lambda: None)
    monkeypatch.setattr(inputwatch, "_last_use", None)
    assert inputwatch.controller_used_within(600) is False
    monkeypatch.setattr(inputwatch, "_last_use", inputwatch.time.monotonic() - 5)
    assert inputwatch.controller_used_within(600) is True


def test_graphics_memory_matters_only_for_heavy_jobs(calm):
    calm.setattr(craftjobs, "gpu_state", lambda need_mb=7000, **k: {"ok": False})
    assert mw.may_work() == "" and mw.may_work(heavy_gpu=True) == "gpu memory"


def test_gpu_load_is_skipped_inside_our_own_model_calls(calm):
    calm.setattr(acquire, "gpu_busy", lambda samples=3: True)
    assert mw.may_work(check_gpu=False) == ""


def test_talking_to_jarvis_from_the_phone_only_waits_a_minute(calm):
    """Phone turns are answered by the PC's one local brain too, so a mission gives it to him first - but only for
    TALK_QUIET_S, never the old 10-minute idle wait (the mission request itself is such a turn)."""
    calm.setattr(inputwatch, "idle_seconds", lambda: 0.0)
    calm.setattr(inputwatch, "turn_idle", lambda: 0.0)          # the mission request itself just arrived
    assert mw.may_work() == "talking with him"
    calm.setattr(inputwatch, "turn_idle", lambda: 61.0)
    assert mw.TALK_QUIET_S <= 60 and mw.may_work() == ""


def test_quiet_hours_stop_work_only_when_switched_off(calm):
    from core import notify
    calm.setattr(notify, "in_quiet_hours", lambda p, now=None: True)
    calm.setattr(notify, "prefs", lambda uid: {"quiet": {"on": True}})
    assert mw.may_work() == ""
    calm.setenv("JARVIS_MISSION_QUIET_WORK", "off")
    assert mw.may_work() == "quiet hours"


def test_backoff_doubles_and_caps():
    now = [0.0]
    b = mw.Backoff(clock=lambda: now[0])
    assert b.ready()
    waits = [b.fail() for _ in range(6)]
    assert waits == [30, 60, 120, 240, 300, 300]
    assert not b.ready()
    now[0] = 10_000
    assert b.ready()
    b.reset()
    assert b.fail() == 30


def _budget(**kw):
    now = [0.0]
    b = mw.MissionBudget({"minutes": 1, "steps": 2, "model_calls": 2, **kw}, clock=lambda: now[0])
    return b, now


def test_the_budget_stops_at_each_limit():
    b, now = _budget()
    b.resume()
    b.step(); b.step()
    with pytest.raises(mw.OutOfBudget) as e:
        b.step()
    assert e.value.which == "steps"
    with pytest.raises(mw.OutOfBudget):            # used up stays used up, even for another kind of charge
        b.model_call("fast")
    b, now = _budget()
    b.resume()
    b.model_call("fast"); b.model_call("fast")
    with pytest.raises(mw.OutOfBudget) as e:
        b.model_call("fast")
    assert e.value.which == "model_calls"
    b, now = _budget()
    b.resume()
    now[0] = 61
    with pytest.raises(mw.OutOfBudget) as e:
        b.check()
    assert e.value.which == "minutes"


def test_a_local_advanced_call_is_counted_not_fatal_and_the_cloud_stays_refused():
    """Review findings 6/16 (2026-11-10): refusing 'advanced' latched the budget as used up, so every clause that reached
    gateway.understand/replan killed the mission. A local advanced call is now just a counted call; $0 holds because the
    cloud fallback is refused while the meter is set."""
    b, _ = _budget()
    b.model_call("advanced")
    assert b.calls == 1 and b.exhausted is None
    b.check()                                       # not used up
    with b.metering():
        with pytest.raises(intelligence.LocalFailure, match="mission_no_cloud"):
            intelligence._cloud_fallback({}, set(), lambda: False)


def test_the_real_gateway_reaches_lane_d_instead_of_a_budget_block(monkeypatch, tmp_path):
    """Review findings 6/16: the real gateway.decide on a long goal (understand -> 'advanced', refused by the local model
    here) leaves the mission alive: lane d runs and the mission is not 'blocked: budget'."""
    from types import SimpleNamespace
    from core import gateway, missionjudge, skillrouter, craftintent, users
    from tools import vault
    goal = "check whether my disk is filling up and tell me what is big in my downloads folder please"
    m = {"run": "r1", "goal": goal, "budget": None}
    sent = []
    monkeypatch.setattr(vault, "_call", lambda method, path, body=None, query=None: sent.append((path, body)) or (
        {"state": "running", "paused": False} if path.endswith("/heartbeat") else {}))
    users.set_current("owner")
    monkeypatch.setattr(gateway, "asking_clauses", lambda g: [g])
    monkeypatch.setattr(intelligence, "pc_fact_playbook", lambda text: None)
    monkeypatch.setattr(skillrouter, "route", lambda text, cancelled=None: None)
    monkeypatch.setattr(craftintent, "match", lambda text: None)
    monkeypatch.setattr(intelligence, "enabled", lambda: True)
    roles = []

    def request(role, system, history, tools, cancelled=lambda: False, **k):
        roles.append(role)
        intelligence._meter.fn(role)                                   # what complete_local does
        if role != "fast":
            raise intelligence.LocalFailure("local_request_failed")  # the advanced tier fails: never the cloud here
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Your disk has 120 GB free.")])
    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(missionjudge, "_model", lambda: "fake")
    monkeypatch.setattr(missionjudge, "_ask", lambda model, prompt: {"verdict": "done", "why": "answered"})
    run = mw.MissionRun(m, gate=lambda **k: "")
    monkeypatch.setattr(run, "allowed_schemas", lambda history: [])
    out = run.go()
    assert "advanced" in roles and "fast" in roles                     # the gateway asked, then lane d answered
    finish = [b for p, b in sent if p.endswith("/finish")][-1]
    assert "budget" not in finish["detail"] and run.budget.exhausted is None, finish
    assert any(e["what"] == "answer" and "120 GB" in e["detail"] for e in run.evidence)   # lane d ran
    assert out in ("done", "blocked")


def test_waiting_time_is_not_counted():
    b, now = _budget()
    b.resume(); now[0] = 20; b.pause()
    now[0] = 5000                                  # waited for the gate (a game) for over an hour
    b.resume(); now[0] = 5030
    assert b.active_seconds() == 50
    b.check()                                      # 50 s of a 60 s budget: still fine


def test_model_calls_are_charged_at_complete_local_on_this_thread_only(monkeypatch):
    b, _ = _budget(model_calls=1)
    seen = []
    monkeypatch.setattr(intelligence, "uses_ollama", lambda role: seen.append(role) or False)
    monkeypatch.setattr(intelligence, "model", lambda role: "x")
    monkeypatch.setattr(intelligence, "configured_ollama_models", lambda: ["x"])
    body = {"role": "fast", "messages": "not a list", "tools": []}
    with b.metering():
        with pytest.raises(intelligence.LocalFailure):          # invalid request: but it was charged first
            intelligence.complete_local(body)
        with pytest.raises(mw.OutOfBudget):
            intelligence.complete_local(body)
        with pytest.raises(mw.OutOfBudget):
            intelligence.complete_local(dict(body, role="advanced"))
    assert b.calls == 1
    with pytest.raises(intelligence.LocalFailure):              # outside the block: nobody is charged
        intelligence.complete_local(body)
    assert b.calls == 1


def test_the_cloud_fallback_is_refused_inside_a_mission():
    b, _ = _budget()
    with b.metering():
        with pytest.raises(intelligence.LocalFailure, match="mission_no_cloud"):
            intelligence._cloud_fallback({}, set(), lambda: False)


def test_practice_yields_while_a_mission_runs(monkeypatch):
    from core import skilllab
    monkeypatch.setattr(skilllab, "available", lambda: "")
    monkeypatch.setattr("core.scheduler.idle_seconds", lambda: 99999.0)
    monkeypatch.setattr(acquire, "running_game", lambda: "")
    monkeypatch.setattr(acquire, "gpu_busy", lambda samples=3: False)
    assert acquire.idle_ready() == ""
    with mw.mission_active():
        assert mw.active() and acquire.idle_ready() == "a mission is running"
    assert not mw.active() and acquire.idle_ready() == ""


def test_a_stale_marker_is_a_crash_leftover(monkeypatch):
    import os
    mw.touch_marker()
    old = mw.marker_path().stat().st_mtime - 4 * 3600
    os.utime(mw.marker_path(), (old, old))
    assert not mw.active()
