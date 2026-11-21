"""The studio manager (core/studio, core/studiojob, tools/gamestudio, the studio lane of core/missionworker), with fakes:
a tiny git repo stands in for Living Shadow World, the mission store is the real one behind a fake laptop, and the
worker, the engine and the commit are fakes. No worktree is made in the real game, no worker runs, no money is spent."""
import json
import os
import subprocess
import urllib.error
from types import SimpleNamespace

import pytest

from core import missions, missionworker as mw, studio, studiojob, usage, users
from tools import vault

VERIFY_PS1 = '''$scen = @(
  @("AnatomyCombat", "/Game/LSW/Tests/AnatomyCombatArena", "n"), @("BossFight", "/Game/LSW/Boss/BossArena", "n"),
  @("UXOpeningSkip", "/Game/LSW/UI/Maps/LSW_Opening", "r"), @("UXTitleMenu", "", "n"))
'''


@pytest.fixture(autouse=True)
def no_real_credit(monkeypatch):
    """The owner's .env declares the real credit and key: a test only sees what it sets itself."""
    for v in ("ANTHROPIC_API_KEY", "JARVIS_TEACHER_CREDIT_USD", "JARVIS_TEACHER_CREDIT_AS_OF", "JARVIS_TEACHER_CREDIT_EXPIRES",
              "JARVIS_USAGE_FILE", "JARVIS_UE_ROOT"):
        monkeypatch.delenv(v, raising=False)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def lsw(tmp_path, monkeypatch):
    """A fake game repo with an `integration` branch: boss files, a scenario name, the coordinator's verify script."""
    repo = tmp_path / "lsw"
    (repo / "Source/LivingShadowWorld/Boss").mkdir(parents=True)
    (repo / "Source/LivingShadowWorld/Shadows").mkdir(parents=True)
    (repo / "Scripts").mkdir()
    (repo / "Source/LivingShadowWorld/Boss/LSWBossTypes.h").write_text(
        "struct FLSWBossTuning { float RunSpeed = 520.f; float CooldownSeconds = 5.f; int Phase2Below; };\n")
    (repo / "Source/LivingShadowWorld/Boss/LSWBossScenarios.cpp").write_text('Register("BossFight"); // Phase speed\n')
    (repo / "Source/LivingShadowWorld/Shadows/LSWShadowAlly.cpp").write_text("void Follow(); // shadow ally\n")
    (repo / "Scripts/integration_verify.ps1").write_text(VERIFY_PS1)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    _git(repo, "branch", "integration")
    monkeypatch.setenv("JARVIS_LSW_REPO", str(repo))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return repo


@pytest.fixture
def credit(monkeypatch):
    monkeypatch.setenv("JARVIS_TEACHER_CREDIT_USD", "98.87")
    monkeypatch.setenv("JARVIS_TEACHER_CREDIT_AS_OF", "2026-01-01")
    monkeypatch.setenv("JARVIS_TEACHER_CREDIT_EXPIRES", "2099-12-31")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("JARVIS_MONTHLY_BUDGET", "1000")
    monkeypatch.setattr(studio, "api_key_ok", lambda: True)            # no network in tests


# ------------------------------------------------------------------------------------------------ the order
def test_lane_follows_the_words():
    assert studio.pick_lane("the boss feels too slow in phase 2") == "boss"
    assert studio.pick_lane("the shadow ally ignores my attack order") == "soul"
    assert studio.pick_lane("after I reload a save my arm injury is gone") == "persist"
    assert studio.pick_lane("the title menu loses focus") == "ux"
    assert studio.pick_lane("the ragdoll looks stiff when he gets knocked down") == "physanim"
    assert studio.pick_lane("my sword swing feels weak") == "core"
    assert studio.pick_lane("something feels off") == "core"


def test_draft_is_precise_and_reads_only(lsw):
    head_before = subprocess.run(["git", "-C", str(lsw), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    o = studio.draft("the boss feels too slow in phase 2", mission="m1")
    assert o["id"] == "WO-1" and o["lane"] == "boss" and o["state"] == "draft"
    assert o["branch"].startswith("studio/wo-1-boss-slow-phase")
    assert o["files"][0]["path"].endswith("LSWBossTypes.h")              # RunSpeed/Cooldown/Phase: the knobs
    names = [s["name"] for s in o["tests"]["scenarios"]]
    assert names[0] == "BossFight" and set(names) == {"AnatomyCombat", "BossFight", "UXOpeningSkip", "UXTitleMenu"}
    skip = next(s for s in o["tests"]["scenarios"] if s["name"] == "UXOpeningSkip")
    assert skip["render"] is True
    assert any("three extraction attempts" in c for c in o["acceptance"])
    assert any("nothing is merged into main or integration" in c for c in o["acceptance"])
    assert o["worker"] == "codex" and o["difficulty"] == "medium"
    # read-only on the game: no branch, no worktree, same HEAD
    assert "studio/" not in subprocess.run(["git", "-C", str(lsw), "branch"], capture_output=True, text=True).stdout
    assert subprocess.run(["git", "-C", str(lsw), "rev-parse", "HEAD"], capture_output=True, text=True).stdout == head_before
    assert studio.draft("the shadow ally ignores me")["id"] == "WO-2"


def test_preview_fits_the_phone_sheet_and_names_everything(lsw):
    o = studio.draft("the boss feels too slow in phase 2 " + "really " * 40)
    p = studio.preview(o)
    assert len(p) <= 1000
    for part in ("WO-1", "Boss & Enemy-AI", "LSWBossTypes.h", "Codex", "studio/wo-1", "Approve"):
        assert part in p


def test_yes_binds_to_the_exact_order(lsw, monkeypatch):
    spawned = []
    monkeypatch.setattr(studio, "spawn", lambda o: spawned.append(o["id"]) or 1)
    o = studio.draft("the boss feels too slow in phase 2")
    key = o["key"]
    changed = dict(o, worker="credit")
    studio.save(changed)                                              # the order changed after he looked
    with pytest.raises(ValueError):
        studio.start(o, key)
    assert spawned == [] and studio.load("WO-1")["state"] == "draft"
    studio.save(o)
    assert "approved and started" in studio.start(o, key)
    assert spawned == ["WO-1"] and studio.load("WO-1")["state"] == "approved"
    assert "already started" in studio.start(o, key)                  # a second yes never starts it twice
    assert spawned == ["WO-1"]


def test_worker_choice():
    assert studio.pick_worker("use codex: the jump is floaty")["worker"] == "codex"
    assert studio.pick_worker("the boss is too slow")["worker"] == "codex"           # tuning = medium
    assert studio.pick_worker("give the boss a new grab attack")["worker"] == "claude"  # hard, no credit declared
    assert studio.pick_worker("give the boss a new grab attack, use claude")["worker"] == "claude"


def test_hard_work_goes_to_the_expiring_credit_until_the_cap(credit, monkeypatch):
    w = studio.pick_worker("give the boss a new grab attack")
    assert w["worker"] == "credit" and "credit" in w["why"]
    studio.record_cost(79.5)
    c = studio.credit_state()
    assert c["spent"] == pytest.approx(79.5) and not c["ok"] and "cap" in c["why"]
    assert studio.pick_worker("give the boss a new grab attack")["worker"] == "claude"


def test_expired_credit_is_never_used(credit, monkeypatch):
    monkeypatch.setenv("JARVIS_TEACHER_CREDIT_EXPIRES", "2020-01-01")
    assert not studio.credit_state()["ok"]


def test_cost_lands_in_the_usage_ledger_as_studio(credit):
    studio.record_cost(1.25)
    month = usage._load()[__import__("datetime").date.today().strftime("%Y-%m")]
    assert month["components"]["studio"]["cost"] == pytest.approx(1.25)
    assert studio.studio_spent() == pytest.approx(1.25)


# ------------------------------------------------------------------------------------------------ digests
def test_build_digest():
    log = ("Building LivingShadowWorldEditor...\nC:\\w\\Source\\LivingShadowWorld\\Boss\\LSWBossTypes.h(12): error C2065: 'Foo': "
           "undeclared identifier\nC:\\w\\Source\\LivingShadowWorld\\Boss\\LSWBossTypes.h(12): error C2065: 'Foo': undeclared "
           "identifier\nResult: Failed (OtherCompilationError)\nBUILD_EXIT=6\n")
    d = studiojob.digest_build(log)
    assert not d["ok"] and d["errors"] == 1 and "Source/LivingShadowWorld/Boss/LSWBossTypes.h(12): error C2065" in d["summary"]
    assert studiojob.digest_build("Result: Succeeded\nEXIT=0\nBUILD_EXIT=0\n")["ok"]


def test_test_digest():
    ok = "Test Completed. Result={Success} Name={A} Path={LivingShadowWorld.Boss.A}\n" * 3 + "LogExit: Display: **** TestExit: Automation Test Queue Empty ****\n"
    assert studiojob.digest_tests(ok)["ok"] and studiojob.digest_tests(ok)["summary"].startswith("automation: 3/3")
    bad = ok.replace("**** TestExit: Automation Test Queue Empty ****", "") + "Test Completed. Result={Fail} Name={B} Path={LivingShadowWorld.Boss.B}\n"
    d = studiojob.digest_tests(bad)
    assert not d["ok"] and d["failed"] == ["LivingShadowWorld.Boss.B"] and "never finished" in d["summary"]


def test_scenario_digest():
    good = "[LSW][Scenario:BossFight] RESULT=PASS (50 checks, 0 failed)"
    assert studiojob.digest_scenario("BossFight", good)["summary"] == "scenario BossFight: PASS (50/50 checks)"
    bad = "LogLSW: Display: [LSW][Scenario:BossFight] FAIL phase 2 lunge not used\n[LSW][Scenario:BossFight] RESULT=FAIL (50 checks, 1 failed)"
    d = studiojob.digest_scenario("BossFight", bad)
    assert not d["ok"] and "FAIL phase 2 lunge" in d["summary"] and "49/50" in d["summary"]


# ------------------------------------------------------------------------------------------------ the job
def _order(lsw, monkeypatch, note="the boss feels too slow in phase 2"):
    monkeypatch.setattr(studiojob, "ensure_worktree", lambda o: None)
    monkeypatch.setattr(studiojob, "wait_for_quiet", lambda *a, **k: None)
    monkeypatch.setattr(studio, "note_pid", lambda oid, pid: None)
    o = studio.draft(note)
    studio.set_state(o, "approved")
    return o


def test_job_green_first_time(lsw, monkeypatch):
    o = _order(lsw, monkeypatch)
    prompts = []

    def worker(order, prompt, first):
        prompts.append((first, prompt))
        return {"ok": True, "text": "Phase 2: RunSpeed 520 -> 600; test LivingShadowWorld.Boss.Phase2Speed.", "cost": 0}

    verified = {"ok": True, "stage": "tests", "digest": "", "results": {"build": "LivingShadowWorldEditor build: Succeeded",
                "automation": "automation: 72/72 passed", "scenarios": ["scenario BossFight: PASS (50/50 checks)"]}}
    out = studiojob.run(o["id"], worker=worker, verifier=lambda order, tag: verified, committer=lambda order, g, s: "abc1234")
    assert out["state"] == "green" and out["commit"] == "abc1234" and out["fix_rounds"] == 0
    assert prompts[0][0] is True and "WORK ORDER WO-1" in prompts[0][1] and "Never commit" in prompts[0][1]
    assert "Green" in out["report"] and "72/72" in out["report"] and "play.bat" in out["report"]
    assert studio.load("WO-1")["state"] == "green"


def test_job_gives_the_worker_the_digest_not_the_log(lsw, monkeypatch):
    o = _order(lsw, monkeypatch)
    prompts, results = [], iter([
        {"ok": False, "stage": "build", "digest": "build: FAILED\nSource/.../LSWBossTypes.h(12): error C2065", "results": {}},
        {"ok": True, "stage": "tests", "digest": "", "results": {"build": "Result: Succeeded", "automation": "automation: 1/1", "scenarios": []}}])
    worker = lambda order, prompt, first: prompts.append(prompt) or {"ok": True, "text": "done", "cost": 0}  # noqa: E731
    out = studiojob.run(o["id"], worker=worker, verifier=lambda order, tag: next(results), committer=lambda *a: "def5678")
    assert out["state"] == "green" and out["fix_rounds"] == 1
    assert "error C2065" in prompts[1] and "never weaken or delete a test" in prompts[1]


def test_job_stays_red_after_the_fix_rounds_and_says_so(lsw, monkeypatch):
    o = _order(lsw, monkeypatch)
    red = {"ok": False, "stage": "tests", "digest": "automation: 70/71 passed\nFAILED LivingShadowWorld.Boss.X",
           "results": {"build": "Result: Succeeded", "automation": "automation: 70/71 passed", "scenarios": []}}
    committed = []
    out = studiojob.run(o["id"], worker=lambda *a: {"ok": True, "text": "tried", "cost": 0}, verifier=lambda *a: red,
                        committer=lambda order, green, s: committed.append(green) or "bad0001")
    assert out["state"] == "red" and out["fix_rounds"] == studiojob.MAX_FIX_ROUNDS and committed == [False]
    assert out["report"].startswith("Not green") and "play.bat" not in out["report"]


def test_job_failure_always_leaves_a_final_state(lsw, monkeypatch):
    o = _order(lsw, monkeypatch)

    def boom(*a):
        raise OSError("disk full")
    out = studiojob.run(o["id"], worker=boom, verifier=lambda *a: None, committer=lambda *a: "")
    assert out["state"] == "failed" and "disk full" in out["report"]


def test_worker_commands(lsw, credit, monkeypatch):
    o = studio.draft("give the boss a new grab attack")
    assert o["worker"] == "credit" and o["budget_usd"] == 15
    argv, env = studiojob.worker_cmd(o, "p", True, 12.0, studio.home() / "x.txt")
    assert os.path.basename(argv[0]).lower().startswith("claude") and argv[1] == "-p" and "--bare" in argv and argv[argv.index("--max-budget-usd") + 1] == "12.00"
    assert env["ANTHROPIC_API_KEY"] == "test-key-not-real"
    o["worker"] = "claude"
    argv, env = studiojob.worker_cmd(o, "p", True, 0, studio.home() / "x.txt")
    assert "ANTHROPIC_API_KEY" not in env and "--bare" not in argv and "--max-budget-usd" not in argv
    o["worker"] = "codex"
    argv, env = studiojob.worker_cmd(o, "p", True, 0, studio.home() / "x.txt")
    assert os.path.basename(argv[0]).lower().startswith("codex") and argv[1] == "exec" and "ANTHROPIC_API_KEY" not in env and argv[-1] == "-"


def test_credit_run_records_its_cost_and_resumes_its_session(lsw, credit, monkeypatch):
    o = studio.draft("give the boss a new grab attack")
    calls = []

    def runner(argv, **kw):
        calls.append((argv, kw["input"]))
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"type": "result", "subtype": "success", "is_error": False, "result": "Added a grab.", "total_cost_usd": 2.5,
             "session_id": "sess-1"}))
    w = studiojob.run_worker(o, "do it", True, runner=runner)
    assert w == {"ok": True, "text": "Added a grab.", "cost": 2.5}
    assert o["spent_usd"] == 2.5 and studio.studio_spent() == pytest.approx(2.5)
    studiojob.run_worker(o, "fix it", False, runner=runner)
    assert "--resume" in calls[1][0] and calls[1][0][calls[1][0].index("--resume") + 1] == "sess-1"
    assert calls[1][0][calls[1][0].index("--max-budget-usd") + 1] == "12.50"   # 15 per order - 2.50 spent


def test_credit_run_refused_when_the_order_budget_is_used(lsw, credit):
    o = studio.draft("give the boss a new grab attack")
    o["spent_usd"] = 14.8
    w = studiojob.run_worker(o, "x", False, runner=lambda *a, **k: pytest.fail("must not run"))
    assert not w["ok"] and "credit" in w["text"]


def test_budget_error_is_not_success(lsw, credit):
    o = studio.draft("give the boss a new grab attack")
    out = SimpleNamespace(returncode=1, stderr="", stdout=json.dumps({"subtype": "error_max_budget_usd", "is_error": True,
                                                                      "total_cost_usd": 15.0, "result": ""}))
    w = studiojob.run_worker(o, "x", True, runner=lambda *a, **k: out)
    assert not w["ok"] and "error_max_budget_usd" in w["text"] and studio.studio_spent() == pytest.approx(15.0)


# ------------------------------------------------------------------------------------------------ status, voice
def test_status_answers_from_the_real_orders(lsw):
    assert "no studio work orders" in studio.status("boss")
    o = studio.draft("the boss feels too slow in phase 2")
    studio.set_state(o, "working")
    assert "with the worker" in studio.status("boss") and "Codex" in studio.status("boss")
    o["report"] = "Green: build succeeded, tests 72/72."
    studio.set_state(o, "green")
    assert "done and green" in studio.status("boss") and "72/72" in studio.status("boss")
    assert "no work order about the camera" in studio.status("the camera")
    assert "Work order 1" in studio.status("") and "Work order 1" in studio.status("work order 1")


def test_voice_note_queues_a_studio_mission(tmp_path, monkeypatch):
    from tools import gamestudio
    users.set_current("owner")
    monkeypatch.setattr(gamestudio, "_owner", lambda: True)
    monkeypatch.setattr(vault, "_remote", lambda: None)
    said = gamestudio.studio_note("the boss feels too slow in phase 2")
    assert "nothing is built until you approve" in said
    (m,) = missions.listing()
    assert m["goal"] == "Studio note: the boss feels too slow in phase 2" and studio.is_note(m["goal"])
    assert "What's the note" in gamestudio.studio_note("hm")


def test_voice_refuses_a_household_member(monkeypatch):
    from tools import gamestudio
    monkeypatch.setattr(gamestudio, "_owner", lambda: False)
    assert gamestudio.studio_note("the boss is slow") == gamestudio.NOT_OWNER
    assert gamestudio.studio_status("boss") == gamestudio.NOT_OWNER


def test_tool_module_is_registered():
    import tools
    assert "gamestudio" in tools.ALL and "gamestudio" not in tools.DESK_ONLY


# ------------------------------------------------------------------------------------------------ the mission lane
class Laptop:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body=None, query=None):
        body = json.loads(json.dumps(body or {}))
        rest = path[len("/api/missions/worker"):]
        self.calls.append(rest)
        if rest == "/claim":
            return json.loads(json.dumps({"mission": missions.claim_next("pc"), "paused": missions.paused()}))
        _, run, verb = rest.split("/")
        out = {"heartbeat": lambda: missions.heartbeat(run, body.get("progress", ""), body.get("checkpoint")),
               "park": lambda: {"mission": missions.park_for_approval(run, body["step"], body["preview"], body.get("checkpoint"))},
               "finish": lambda: {"mission": missions.finish(run, body["outcome"], body.get("detail", ""))},
               "release": lambda: {"mission": missions.release(run, body.get("checkpoint"))},
               "approval": lambda: {"approval": missions.consume_approval_for_run(run)}}[verb]()
        return json.loads(json.dumps(out))


@pytest.fixture
def queue(tmp_path, monkeypatch):
    users.set_current("owner")
    laptop = Laptop()
    monkeypatch.setattr(vault, "_call", laptop)
    monkeypatch.setattr(vault, "_remote", lambda: ("http://laptop", "token"))
    mw._backoff.reset()
    return laptop


def _tick():
    mw._backoff.reset()
    return mw.tick(gate=lambda **k: "")


def test_mission_lane_end_to_end(lsw, queue, monkeypatch):
    spawned = []
    monkeypatch.setattr(studio, "spawn", lambda o: spawned.append(o["id"]) or 4242)
    alive = {"v": True}
    monkeypatch.setattr(studio, "job_alive", lambda oid: alive["v"])
    m = missions.add("Studio note: the boss feels too slow in phase 2")

    assert _tick() == "parked"                                   # 1. drafted on the PC and sent for his OK
    got = missions.get(m["id"])
    assert got["state"] == "waiting_approval" and "Work order WO-1" in got["approval"]["preview"]
    assert spawned == [] and studio.load("WO-1")["state"] == "draft"
    assert _tick() == "nothing queued"                            # nothing runs without his yes

    missions.answer_approval(m["id"], True, got["approval"]["fp"])  # 2. his yes on the phone
    out = _tick()
    assert out.startswith("yielded") and spawned == ["WO-1"]     # 3. the job started; the mission waits for it
    assert studio.load("WO-1")["state"] == "approved"
    assert _tick().startswith("yielded") and spawned == ["WO-1"]   # still running: never started twice

    o = studio.load("WO-1")
    o["report"] = "Green: build succeeded, tests 72/72."
    studio.set_state(o, "green")                                  # 4. the job finished green
    assert _tick() == "done"
    done = missions.get(m["id"])
    assert done["state"] == "done" and "72/72" in done["result"]


def test_mission_lane_denied_runs_nothing(lsw, queue, monkeypatch):
    monkeypatch.setattr(studio, "spawn", lambda o: pytest.fail("must not start"))
    m = missions.add("Studio note: the boss feels too slow in phase 2")
    assert _tick() == "parked"
    missions.answer_approval(m["id"], False, missions.get(m["id"])["approval"]["fp"])
    assert missions.get(m["id"])["state"] == "blocked" and _tick() == "nothing queued"
    assert studio.load("WO-1")["state"] == "draft"


def test_mission_lane_restarts_a_dead_job_once(lsw, queue, monkeypatch):
    spawned = []
    monkeypatch.setattr(studio, "spawn", lambda o: spawned.append(o["id"]) or 1)
    monkeypatch.setattr(studio, "job_alive", lambda oid: False)
    m = missions.add("Studio note: the boss feels too slow in phase 2")
    _tick()
    missions.answer_approval(m["id"], True, missions.get(m["id"])["approval"]["fp"])
    _tick()                                                       # started (spawn 1); the job is not alive at the check
    assert spawned == ["WO-1", "WO-1"]                            # dead -> started again once
    assert _tick() == "blocked" and "stopped twice" in missions.get(m["id"])["result"]
    assert studio.load("WO-1")["state"] == "failed"


def test_mission_lane_without_the_game_repo_says_why(queue, monkeypatch, tmp_path):
    m = missions.add("Studio note: the boss feels too slow in phase 2")
    assert _tick() == "blocked"
    assert "couldn't write the work order" in missions.get(m["id"])["result"]


def test_a_rejected_api_key_never_picks_the_credit(credit, monkeypatch):
    monkeypatch.setattr(studio, "api_key_ok", lambda: False)
    c = studio.credit_state()
    assert not c["ok"] and "rejects the API key" in c["why"]
    assert studio.pick_worker("give the boss a new grab attack")["worker"] == "claude"


def test_key_check_is_cached_and_tolerates_network_trouble(monkeypatch):
    import anthropic
    calls = []

    class Fake:
        def __init__(self, **k):
            self.messages = self

        def count_tokens(self, **k):
            calls.append(1)
            raise anthropic.APIConnectionError(request=None) if False else OSError("offline")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-net")
    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    studio._KEY_CHECK.clear()
    assert studio.api_key_ok() and studio.api_key_ok() and len(calls) == 1
