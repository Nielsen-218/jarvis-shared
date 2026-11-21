"""Mark VI review (2026-11-10, docs/plans/mark6-review-findings.json): one regression test per confirmed finding that is not
covered next to its module's own tests. Fakes only: the "laptop" is the real store behind a fake tools.vault._call (the
`world` fixture), nothing opens an app, no model runs, no key or window message reaches a real window."""
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from core import approvals, craftjobs, gateway, intelligence, missionjudge, missionpolicy, missions, missionworker as mw, pcmethods, users
from jarvis_pc.guardrails.stepfence import BackgroundUnavailable, background_only
from tests.test_missionworker import CALM, GOAL, _decide, _tool, world  # noqa: F401  (the fake laptop + fake model)
from tools import registry, vault

STEPS = [{"tool": "list_windows", "arguments": {}}, {"tool": "open_app", "arguments": {"name": "notepad"}},
         {"tool": "pc_status", "arguments": {}}]


def _fp(mid):
    return missions.get(mid)["approval"]["fp"]


def _typed(world, monkeypatch, on=None):
    """Lane c with STEPS: list_windows runs, open_app parks, pc_status is the rest. `on(tool, approved)` runs first."""
    executed = []

    def execute(step, *, approved=False, cancelled=lambda: False, execution_id=None):
        executed.append((step["tool"], approved))
        if on:
            on(step["tool"], approved)
        return {"verified": True, "detail": f"{step['tool']} ok"}
    monkeypatch.setattr(pcmethods, "execute", execute)
    monkeypatch.setattr(gateway, "decide", _decide(STEPS))
    return executed


# ---- finding 0: an approval names the step he saw
def test_f0_a_stale_sheet_cannot_approve_a_step_he_never_saw(world):
    m = missions.add("tidy up")
    run = missions.claim_next("pc")["run"]
    missions.park_for_approval(run, {"tool": "step_a"}, "A?")
    seen_a = _fp(m["id"])
    missions.answer_approval(m["id"], True, seen_a)                      # approved on device 1
    run2 = missions.claim_next("pc")["run"]
    missions.park_for_approval(run2, {"tool": "step_b"}, "B?")         # the mission moved on to step B
    with pytest.raises(ValueError, match="changed since you opened it"):
        missions.answer_approval(m["id"], True, seen_a)                  # device 2's sheet still shows A
    with pytest.raises(ValueError):
        missions.answer_approval(m["id"], True)                          # no fingerprint: never a yes
    got = missions.get(m["id"])
    assert got["state"] == "waiting_approval" and got["approval"]["answer"] is None


def test_f0_the_phone_sends_the_fingerprint_it_showed():
    from pathlib import Path
    body = (Path(__file__).resolve().parent.parent / "app" / "index.html").read_text(encoding="utf-8")
    assert "JSON.stringify({ approved, fp: a.fp" in body


# ---- findings 1 + 18: an approved step that ran is checkpointed; a later yield or crash never re-asks or re-runs it
def test_f1_a_yield_after_the_approved_step_continues_with_the_rest(world, monkeypatch):
    yielded = []

    def gate(**k):
        if ("open_app", True) in executed and not yielded:
            yielded.append(1)
            return "game"                                              # a game starts before the rest (pc_status)
        return ""
    executed = _typed(world, monkeypatch)
    m = missions.add("check my windows")
    assert mw.tick(gate=CALM) == "parked"
    missions.answer_approval(m["id"], True, _fp(m["id"]))
    assert mw.tick(gate=gate) == "yielded: game"
    cp = missions.get(m["id"])["checkpoint"]
    assert cp["approved_ran"] is True and cp["clause"] == 0 and cp["rest"] == [{"tool": "pc_status", "arguments": {}}]
    mw._backoff.reset()
    assert mw.tick(gate=CALM) == "done"
    assert executed == [("list_windows", False), ("open_app", True), ("pc_status", False)]   # open_app exactly once
    assert world.laptop.verbs().count("park") == 1                                          # never asked again


def test_f18_a_crash_after_the_approved_step_resumes_from_the_saved_checkpoint(world, monkeypatch):
    crashed = []

    def on(tool, approved):
        if tool == "pc_status" and not crashed:
            crashed.append(1)
            world.laptop.down = True                                   # the PC loses the laptop mid-rest (a crash)
    executed = _typed(world, monkeypatch, on)
    m = missions.add("check my windows")
    mw.tick(gate=CALM)
    missions.answer_approval(m["id"], True, _fp(m["id"]))
    world.laptop.down = False
    assert mw.tick(gate=CALM) == "laptop unreachable"
    world.laptop.down = False
    assert missions.get(m["id"])["checkpoint"]["approved_ran"] is True    # saved right after it ran
    assert missions.recover(stale_s=0) == [m["id"]]
    mw._backoff.reset()
    assert mw.tick(gate=CALM) == "done"
    assert [e for e in executed if e[0] == "open_app"] == [("open_app", True)]
    assert world.laptop.verbs().count("park") == 1


def test_f1_a_spent_yes_whose_step_may_not_have_run_is_never_guessed_at(world, monkeypatch):
    def on(tool, approved):
        if tool == "open_app":
            world.laptop.down = True                                   # gone before the 'it ran' checkpoint is saved
    executed = _typed(world, monkeypatch, on)
    m = missions.add("check my windows")
    mw.tick(gate=CALM)
    missions.answer_approval(m["id"], True, _fp(m["id"]))
    assert mw.tick(gate=CALM) == "laptop unreachable"
    world.laptop.down = False
    missions.recover(stale_s=0)
    mw._backoff.reset()
    assert mw.tick(gate=CALM) == "blocked"
    got = missions.get(m["id"])
    assert "interrupted around the step you approved" in got["result"]
    assert [e for e in executed if e[0] == "open_app"] == [("open_app", True)]           # never run twice
    assert world.laptop.verbs().count("park") == 1                                        # never asked again


# ---- finding 2: a tool that asks for its own yes never leaks a question into his voice slot, never counts as done
def test_f2_self_asking_tools_are_never_offered_for_a_phone_yes(world):
    m = missions.add("remove the old game")
    run = mw.MissionRun(missions.claim_next("pc"), gate=CALM)
    with pytest.raises(mw._Blocked, match="needs you at the PC"):
        run.park_tool(0, {"tool": "uninstall_program", "arguments": {"name": "old game"}})
    assert m


@pytest.mark.parametrize("his_question", [False, True])
def test_f2_a_tool_that_asks_after_approval_is_blocked_and_his_slot_is_untouched(world, monkeypatch, his_question):
    monkeypatch.setitem(registry._TOOLS, "frob", _tool("frob", lambda: approvals.request(users.current(), "frob", "frob it")))
    mine = {"key": "his", "what": "his own thing", "t": time.monotonic(), "approved": False}
    monkeypatch.setattr(approvals, "_pending", {"owner": dict(mine)} if his_question else {})
    missions.add("frob it")
    run = mw.MissionRun(missions.claim_next("pc"), gate=CALM)
    run.ctx.approved = missionpolicy.fingerprint("frob", {})           # what resume() sets for his approved step
    with pytest.raises(mw._Blocked, match="asks for your yes"):
        run.tool(0, "frob", {})
    assert approvals._pending == ({"owner": mine} if his_question else {})
    assert run.evidence[-1]["ok"] is False                              # never recorded as done


# ---- findings 3 + 17: a pause RELEASES the running mission (no retry spent, progress kept)
def test_f3_a_pause_mid_run_releases_with_the_checkpoint(world):
    m = missions.add(GOAL)
    world.on_scan = lambda: (missions.set_paused(True), setattr(world, "on_scan", None))
    assert mw.tick(gate=CALM) == "yielded: missions are paused"
    got = missions.get(m["id"])
    assert got["state"] == "queued" and got["retries"] == 0
    assert any(e["what"] == "security_checkup" for e in got["checkpoint"]["evidence"])
    assert missions.runs(m["id"])[-1]["outcome"] == "yielded"
    assert missions.recover(stale_s=0) == []                           # not a crash: nothing left running
    assert missions.claim_next("pc") is None                           # paused: nobody picks it up
    missions.set_paused(False)
    mw._backoff.reset()
    assert mw.tick(gate=CALM) == "parked"


# ---- finding 4: someone else at the PC never acts on the owner's missions
def test_f4_mission_tools_refuse_anyone_but_the_owner(monkeypatch):
    from core import authz
    from tools import missions as mtools
    calls = []
    monkeypatch.setattr(vault, "_remote", lambda: ("http://laptop", "t"))
    monkeypatch.setattr(vault, "_call", lambda *a, **k: calls.append(a) or {"missions": [], "mission": {"goal": "g", "id": "x"}})
    monkeypatch.setattr(authz, "is_owner", lambda uid=None: False)
    for out in (mtools.start_mission("uninstall X"), mtools.mission_status(), mtools.cancel_mission("that"),
                mtools.pause_missions(True)):
        assert out == mtools.NOT_OWNER
    assert calls == []
    monkeypatch.setattr(authz, "is_owner", lambda uid=None: True)
    users.set_current("owner")
    mtools.start_mission("tidy downloads")
    mtools.pause_missions(True)
    assert all(c[2].get("for_uid") == "owner" for c in calls)            # relayed for the owner himself, said so


# ---- finding 5: no console window pops up for a shell step or a craft job
def test_f5_shell_steps_start_without_a_console_window(monkeypatch):
    from jarvis_pc.actions import shell
    seen = {}

    def popen(argv, **kw):
        seen.update(kw)
        raise OSError("not really")
    monkeypatch.setattr(shell.subprocess, "Popen", popen)
    guard = SimpleNamespace(authorize=lambda c, cwd=None, intent="": SimpleNamespace(allowed=True),
                            record_result=lambda *a, **k: None)
    shell.run(guard, "Get-Date")
    if os.name == "nt":
        assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert seen["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP


def test_f5_craft_jobs_start_without_a_console_window(monkeypatch):
    seen = {}

    def popen(cmd, **kw):
        seen.update(kw)
        raise OSError("not really")
    monkeypatch.setattr(craftjobs.subprocess, "Popen", popen)
    with pytest.raises(OSError):
        craftjobs.run(["blender.exe", "--background"])
    if os.name == "nt":
        assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW


# ---- finding 7: a web read only of an address a tool returned (never one the model built from his data)
def test_f7_read_web_pages_parks_an_address_nobody_returned():
    ctx = missionpolicy.MissionContext()
    leak = {"urls": ["https://x.example/?q=owner+notes+text"]}
    assert ctx.guard("search_notes", {"query": "x"}) is None
    assert ctx.guard("read_web_pages", leak) == missionpolicy.PARKED_TEXT
    assert ctx.parked == {"tool": "read_web_pages", "arguments": leak}
    ctx = missionpolicy.MissionContext()
    ctx.see_urls("1. Driver news - https://news.example/nvidia?id=621. Another: https://b.example/x.")
    assert ctx.guard("read_web_pages", {"urls": ["https://news.example/nvidia?id=621"]}) is None
    assert ctx.guard("read_web_pages", {"urls": ["https://news.example/nvidia?id=621", "https://evil.example/?d=1"]}) \
        == missionpolicy.PARKED_TEXT


def test_f7_a_typed_web_read_of_an_unknown_address_is_not_unattended(world):
    missions.add("read https://docs.example/page for me")
    run = mw.MissionRun(missions.claim_next("pc"), gate=CALM)
    assert run.step_unattended({"tool": "read_web_page", "arguments": {"url": "https://docs.example/page"}})
    assert not run.step_unattended({"tool": "read_web_page", "arguments": {"url": "https://evil.example/?d=notes"}})


# ---- finding 8: the gate runs before his yes is spent
def test_f8_a_game_at_the_approved_step_keeps_the_approval(world):
    m = missions.add(GOAL)
    mw.tick(gate=CALM)
    missions.answer_approval(m["id"], True, _fp(m["id"]))
    answers = iter(["", "game"])                                        # tick's gate, then the approved step's beat
    assert mw.tick(gate=lambda **k: next(answers, "")) == "yielded: game"
    got = missions.get(m["id"])
    assert got["approval"]["answer"] == "approved" and not got["approval"]["used"]        # his yes is still there
    mw._backoff.reset()
    assert mw.tick(gate=CALM) == "done"
    assert world.ran.count("cleanup") == 1 and world.laptop.verbs().count("park") == 1


# ---- findings 9 + 10: launching, opening and closing refuse in background mode (an approved step included)
@pytest.fixture
def launches(monkeypatch):
    from tools import apps, software, system
    spy = []
    from tools import pcops
    monkeypatch.setattr(pcops, "windows", lambda visible_only=True: [])      # a Notepad the owner has open must not change this test
    monkeypatch.setattr(apps, "_find", lambda q: ("notepad", "notepad.exe"))
    monkeypatch.setattr(apps.subprocess, "Popen", lambda *a, **k: spy.append(("Popen", a)))
    monkeypatch.setattr(apps.os, "startfile", lambda *a, **k: spy.append(("startfile", a)), raising=False)
    monkeypatch.setattr(software.subprocess, "Popen", lambda *a, **k: spy.append(("explorer", a)))
    monkeypatch.setattr(system.webbrowser, "open", lambda *a, **k: spy.append(("webbrowser", a)))
    monkeypatch.setattr(apps, "_close_visible_windows", lambda app: spy.append(("close", app)) or 1)
    return spy


def test_f9_launch_open_and_close_refuse_in_background_and_launch_nothing(launches, monkeypatch, tmp_path):
    from core import pcactions, pcverbs
    from tools import apps, software, system
    monkeypatch.setattr(pcverbs, "_in_user_space", lambda p: True)
    monkeypatch.setattr(pcactions, "_foreground_identity", lambda: launches.append(("front",)) or (1, "notepad.exe"))
    with background_only():
        for call in (lambda: apps.launch_and_wait("steam"), lambda: apps.open_app("notepad"),
                     lambda: system.open_website("https://example.com"), lambda: apps.close_app("notepad"),
                     lambda: software.launch_as_user(str(tmp_path)),
                     lambda: pcactions._open_browser_url("https://example.com", lambda: False),
                     lambda: pcactions._close_foreground_window(),
                     lambda: pcverbs.open_path({"path": str(tmp_path)})):
            with pytest.raises(BackgroundUnavailable):
                call()
    assert launches == []


def test_f10_an_approved_open_app_step_reports_needs_you_at_the_pc(launches, monkeypatch):
    from core import pcactions
    monkeypatch.setattr(pcactions, "process_running", lambda *a: False)
    with background_only():
        out = pcmethods.execute({"tool": "open_app", "arguments": {"app": "notepad"}}, approved=True)
    assert out["verified"] is False and out.get("error_kind") == "background_unavailable", out
    assert launches == []


def test_f9_outside_background_a_launch_still_launches(launches):
    from tools import apps
    apps.launch_and_wait("notepad", timeout=0)
    assert launches                                                      # the owner's own voice turn is unchanged


# ---- finding 15: the mission's meter and background inference ride onto the pcmethods step worker
def test_f15_the_step_worker_carries_the_mission_meter_and_background_inference():
    fn = lambda role: None  # noqa: E731
    with intelligence.mission_meter(fn), intelligence.background_inference():
        seen = pcmethods._bounded("pc_status", lambda stop: (getattr(intelligence._meter, "fn", None),
                                                             getattr(intelligence._background, "on", False)), lambda: False)
    assert seen == (fn, True)
    outside = pcmethods._bounded("pc_status", lambda stop: getattr(intelligence._meter, "fn", None), lambda: False)
    assert outside is None


# ---- finding 19: the budget is per mission (kept across claims), and a yield is slowed down
def test_f19_the_budget_is_carried_across_claims(world):
    m = missions.add("report disk space", budget={"model_calls": 5})
    run = missions.claim_next("pc")["run"]
    missions.release(run, {"done_clauses": [], "used": {"seconds": 10, "steps": 1, "calls": 5}})
    assert mw.tick(gate=CALM) == "blocked"
    assert missions.get(m["id"])["result"].startswith("budget: 5 model calls used")
    assert world.ran == []


def test_f19_the_checkpoint_carries_what_was_used(world):
    missions.add(GOAL)
    assert mw.tick(gate=CALM) == "parked"
    used = missions.get(missions.listing()[0]["id"])["checkpoint"]["used"]
    assert used["calls"] >= 1 and used["steps"] >= 1


# ---- finding 20: every model call (the judge included) waits for the gate and sees his cancel/pause
def test_f20_the_judge_waits_for_the_gate(world, monkeypatch):
    judged = []
    monkeypatch.setattr(missionjudge, "_ask", lambda model, prompt: judged.append(1) or {"verdict": "done", "why": "x"})
    after = []

    def gate(**k):
        if "disk" not in world.ran:
            return ""
        after.append(1)
        return "" if len(after) == 1 else "game"                       # lane d reads the answer, then a game starts
    m = missions.add("report disk space")
    assert mw.tick(gate=gate) == "yielded: game"
    assert judged == [] and missions.get(m["id"])["checkpoint"]["done_clauses"] == [0]


def test_f20_a_cancel_is_seen_before_the_next_model_round(world, monkeypatch):
    rounds = []
    real = intelligence.request

    def counting(*a, **k):
        rounds.append(1)
        return real(*a, **k)
    monkeypatch.setattr(intelligence, "request", counting)
    m = missions.add("report disk space")
    world.on_scan = None
    monkeypatch.setitem(registry._TOOLS, "get_system_status",
                        _tool("get_system_status", lambda: missions.cancel(m["id"]) and "C: 120 GB free"))
    assert mw.tick(gate=CALM) == "stopped"
    assert len(rounds) == 1                                             # no second model round after his cancel


# ---- finding 21: a heavy craft job never outlives the mission's wait
def test_f21_the_craft_job_is_cancelled_when_the_budget_runs_out_while_waiting(world, monkeypatch):
    missions.add("make a character from my picture")
    run = mw.MissionRun(missions.claim_next("pc"), gate=CALM)
    cancelled = []
    monkeypatch.setattr(craftjobs, "get", lambda jid=None: {"id": "j1", "state": "running", "quiet": True})
    monkeypatch.setattr(craftjobs, "cancel", lambda jid: cancelled.append(jid))
    monkeypatch.setattr(mw, "CRAFT_POLL_S", 0.0)
    run.craft_job = {"id": "j1", "state": "running", "quiet": True}
    run.budget.exhausted = mw.OutOfBudget("minutes", "budget: 60 minutes of work used")
    with pytest.raises(mw.OutOfBudget):
        run.after_tool(0, "image_to_character", True)
    assert cancelled == ["j1"]
