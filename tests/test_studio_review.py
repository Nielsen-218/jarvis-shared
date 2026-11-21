"""Review 2026-11-16 of the studio manager: each test fails on the code before the fix."""
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from core import missions, studio, studiojob
from tests.test_studio import _git, _order, _tick, credit, lsw, no_real_credit, queue  # noqa: F401 - fixtures


def test_a_crashed_suite_is_never_green():
    header = 'CMD: UnrealEditor-Cmd.exe x.uproject "-TestExit=Automation Test Queue Empty"\n'
    ran = "Test Completed. Result={Success} Name={A} Path={LivingShadowWorld.A}\n" * 40
    crashed = header + 'LogInit: Command Line: -TestExit="Automation Test Queue Empty"\n' + ran + "EXIT=-1073741819\n"
    assert not studiojob.digest_tests(crashed)["ok"]
    timed = crashed + "LogExit: Display: **** TestExit: Automation Test Queue Empty ****\nLSW_RUNNER: TIMEOUT after 3480 s\n"
    assert not studiojob.digest_tests(timed)["ok"]
    good = (header + ran + "LogAutomationCommandLine: Display: ...Automation Test Queue Empty 40 tests performed.\n"
            "LogExit: Display: **** TestExit: Automation Test Queue Empty ****\nEXIT=0\n")
    assert studiojob.digest_tests(good)["ok"]


def test_a_build_needs_exit_zero():
    assert not studiojob.digest_build("Result: Succeeded\nLSW_RUNNER: TIMEOUT after 5400 s\nEXIT=-1\nBUILD_EXIT=-1\n")["ok"]
    assert not studiojob.digest_build("Result: Succeeded\nBUILD_EXIT=10\n")["ok"]
    assert studiojob.digest_build("Result: Succeeded\nBUILD_EXIT=0\n")["ok"]


def test_a_reused_pid_is_not_the_job(monkeypatch):
    import psutil
    me = psutil.Process(os.getpid())
    assert studio.alive(os.getpid(), me.create_time())
    assert not studio.alive(os.getpid(), me.create_time() - 3600)      # same pid, another start time
    assert not studio.alive(None)

    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, "Process", denied)
    assert studio.alive(1234, 5.0)                                     # an elevated job we may not read: alive, not restarted


def test_one_job_at_a_time_atomically(monkeypatch):
    assert studiojob.take_lock("WO-1") == "mine"
    assert studiojob.take_lock("WO-2") == "mine"                        # this process already holds it
    lock = studiojob._lock_path()
    data = json.loads(lock.read_text())
    data["pid"] = os.getpid() + 1                                       # another live process holds it
    lock.write_text(json.dumps(data))
    monkeypatch.setattr(studio, "alive", lambda pid, created=0.0: True)
    assert studiojob.take_lock("WO-2") == "busy" and studiojob.take_lock("WO-1") == "same"
    monkeypatch.setattr(studio, "alive", lambda pid, created=0.0: False)    # its holder died
    assert studiojob.take_lock("WO-2") == "mine"
    studiojob.drop_lock()
    assert not lock.exists()


def test_a_second_copy_of_a_running_order_stops(lsw, monkeypatch):
    o = _order(lsw, monkeypatch)
    monkeypatch.setattr(studiojob, "take_lock", lambda oid: "same")
    out = studiojob.run(o["id"], worker=lambda *a: pytest.fail("must not run twice"))
    assert out["state"] == "approved"


def test_a_restarted_job_does_not_pay_the_worker_again(lsw, monkeypatch):
    o = _order(lsw, monkeypatch)
    o.update(worker_done=True, worker_text="did it")
    studio.save(o)
    green = {"ok": True, "stage": "tests", "digest": "",
             "results": {"build": "Succeeded", "automation": "automation: 1/1", "scenarios": []}}
    out = studiojob.run(o["id"], worker=lambda *a: pytest.fail("already paid"), verifier=lambda *a: green,
                        committer=lambda *a: "c1")
    assert out["state"] == "green"


def test_worker_never_sees_jarvis_secrets(monkeypatch, credit):
    monkeypatch.setenv("JARVIS_SERVER_TOKEN", "secret-token")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("SOME_PASSWORD", "p")
    env = studiojob.worker_env(True)
    assert env["ANTHROPIC_API_KEY"] == "test-key-not-real"
    assert not {"JARVIS_SERVER_TOKEN", "GEMINI_API_KEY", "SOME_PASSWORD"} & set(env)
    plain = studiojob.worker_env(False)
    assert "ANTHROPIC_API_KEY" not in plain and "PATH" in {k.upper() for k in plain}


def test_unknown_credit_cost_counts_the_whole_budget(lsw, credit):
    o = studio.draft("give the boss a new grab attack")
    timed = SimpleNamespace(returncode=-1, stdout="", stderr="", timed_out=True)
    w = studiojob.run_worker(o, "x", True, runner=lambda *a, **k: timed)
    assert not w["ok"] and w["cost"] == pytest.approx(15.0) and studio.studio_spent() == pytest.approx(15.0)
    assert "budget it had is counted" in w["text"]


def test_credit_word_in_a_note_is_not_a_request():
    assert studio.pick_worker("the credits roll is too fast")["worker"] == "codex"
    assert studio.pick_worker("add a grab attack, use the credit")["worker"] == "credit"


def test_commit_only_on_the_order_branch_and_never_secrets(lsw):
    o = studio.draft("the boss feels too slow in phase 2")
    wt = lsw.parent / "wt"
    o["worktree"] = str(wt)
    studiojob.ensure_worktree(o)
    (wt / "Source/LivingShadowWorld/Boss/LSWBossTypes.h").write_text("struct FLSWBossTuning { float RunSpeed = 600.f; };\n")
    (wt / ".env").write_text("SECRET=1\n")
    (wt / ".claude").mkdir()
    (wt / ".claude/settings.json").write_text("{}")
    (wt / "Documentation/BuildEvidence").mkdir(parents=True)
    (wt / "Documentation/BuildEvidence/x.log").write_text("log")
    (wt / "worker.log").write_text("log")
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(wt, "config", k, v)
    sha = studiojob.commit(o, True, "ok")
    files = subprocess.run(["git", "-C", str(wt), "show", "--name-only", "--format=", sha], capture_output=True,
                           text=True).stdout.split()
    assert "Source/LivingShadowWorld/Boss/LSWBossTypes.h" in files and "Documentation/StudioOrders/WO-1.md" in files
    assert not [f for f in files if f.startswith((".env", ".claude", "Documentation/BuildEvidence")) or f.endswith(".log")]
    integ = subprocess.run(["git", "-C", str(lsw), "rev-parse", "--short", "integration"], capture_output=True, text=True).stdout
    assert sha not in integ
    _git(wt, "switch", "-q", "-c", "elsewhere")
    with pytest.raises(RuntimeError, match="nothing committed"):
        studiojob.commit(o, True, "ok")


def test_a_half_made_worktree_is_recovered(lsw):
    o = studio.draft("the boss feels too slow in phase 2")
    o["worktree"] = str(lsw.parent / "wt2")
    studiojob.ensure_worktree(o)
    shutil.rmtree(o["worktree"])                                       # a crash left the branch but not the folder
    studiojob.ensure_worktree(o)
    head = subprocess.run(["git", "-C", o["worktree"], "symbolic-ref", "--short", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    assert head == o["branch"]


def test_crash_after_the_yes_keeps_watching_the_started_order(lsw, queue, monkeypatch):
    monkeypatch.setattr(studio, "spawn", lambda o: 1)
    monkeypatch.setattr(studio, "job_alive", lambda oid: True)
    m = missions.add("Studio note: the boss feels too slow in phase 2")
    _tick()
    missions.answer_approval(m["id"], True, missions.get(m["id"])["approval"]["fp"])
    missions.consume_approval(m["id"])                                # spent, as if the PC crashed right after starting it
    studio.set_state(studio.load("WO-1"), "working")
    assert _tick().startswith("yielded")                              # not "blocked: can't tell whether it ran"


def test_crash_before_the_job_started_still_asks_him(lsw, queue, monkeypatch):
    monkeypatch.setattr(studio, "spawn", lambda o: pytest.fail("must not start"))
    m = missions.add("Studio note: the boss feels too slow in phase 2")
    _tick()
    missions.answer_approval(m["id"], True, missions.get(m["id"])["approval"]["fp"])
    missions.consume_approval(m["id"])                                # spent, but the order never left 'draft'
    assert _tick() == "blocked" and studio.load("WO-1")["state"] == "draft"


# ------------------------------------------------------------------------------------------------ review round 2
def test_worker_env_is_an_allow_list(monkeypatch, credit):
    monkeypatch.setenv("QBIT_PASS", "hunter2")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("SOME_SSH", "user@host")
    env = studiojob.worker_env(False)
    assert not {"QBIT_PASS", "OPENAI_API_KEY", "SOME_SSH", "ANTHROPIC_API_KEY"} & set(env)
    assert any(k.upper() == "PATH" for k in env) and any(k.upper() == "USERPROFILE" for k in env)


def test_an_instant_launch_failure_costs_nothing(lsw, credit):
    o = studio.draft("give the boss a new grab attack")
    quick_fail = SimpleNamespace(returncode=1, stdout="", stderr="error: unknown option --bare")
    w = studiojob.run_worker(o, "x", True, runner=lambda *a, **k: quick_fail)
    assert not w["ok"] and w["cost"] == 0 and studio.studio_spent() == 0


def test_a_torn_spend_line_hides_nothing():
    folder = studio.home() / "spend"
    folder.mkdir(parents=True)
    (folder / "WO-1.jsonl").write_text('{"usd": 2}\n{"usd": 3\n5\n{"usd": 4}\n', encoding="utf-8")
    assert studio.studio_spent() == pytest.approx(6.0)


def test_nested_agent_folders_and_plugin_binaries_are_never_staged(lsw):
    o = studio.draft("the boss feels too slow in phase 2")
    wt = lsw.parent / "wt3"
    o["worktree"] = str(wt)
    studiojob.ensure_worktree(o)
    for rel in ("Plugins/X/Binaries/x.dll", "Plugins/X/.claude/s.json", "Source/LivingShadowWorld/Boss/new.h"):
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text("x")
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(wt, "config", k, v)
    sha = studiojob.commit(o, True, "ok")
    files = subprocess.run(["git", "-C", str(wt), "show", "--name-only", "--format=", sha], capture_output=True,
                           text=True).stdout.split()
    assert "Source/LivingShadowWorld/Boss/new.h" in files and not [f for f in files if f.startswith("Plugins/")]


# ------------------------------------------------------------------------------------------------ his exact words
@pytest.mark.parametrize("line,note", [
    ("note for the game: the boss feels too slow in phase 2", "the boss feels too slow in phase 2"),
    ("Take a note for Shadow World, the jump feels floaty", "the jump feels floaty"),
    ("tell the studio that the title menu loses focus", "the title menu loses focus"),
    ("Work order: the shadow ally ignores my orders", "the shadow ally ignores my orders"),
])
def test_note_phrases(monkeypatch, line, note):
    from core import authz
    from tools import gamestudio
    got = []
    monkeypatch.setattr(authz, "is_owner", lambda: True)
    monkeypatch.setattr(gamestudio, "studio_note", lambda n: got.append(n) or "noted")
    assert studio.handle(line) == "noted" and got == [note]


@pytest.mark.parametrize("line,what", [
    ("What's the status of the boss work?", "boss"), ("how is the progress on the jump fix", "jump"),
    ("studio status", ""), ("How's the studio doing?", ""), ("what are the coders working on", "")])
def test_status_phrases(monkeypatch, line, what):
    from core import authz
    from tools import gamestudio
    got = []
    monkeypatch.setattr(authz, "is_owner", lambda: True)
    monkeypatch.setattr(gamestudio, "studio_status", lambda a="": got.append(a) or "status")
    assert studio.handle(line) == "status" and got == [what]


@pytest.mark.parametrize("line", ["take a note", "what's the status of my order from Amazon", "note that I like pizza",
                                  "what's the weather", "how is the boss", "tell me about the studio ghibli movies"])
def test_other_lines_are_not_studio(monkeypatch, line):
    from core import authz
    monkeypatch.setattr(authz, "is_owner", lambda: True)
    assert studio.handle(line) is None


def test_a_household_member_gets_no_studio(monkeypatch):
    from core import authz
    monkeypatch.setattr(authz, "is_owner", lambda: False)
    assert studio.handle("note for the game: the boss is slow") is None


def test_quick_path_reaches_the_studio(monkeypatch):
    from core import quick
    monkeypatch.setattr(studio, "handle", lambda raw: "studio says" if raw.startswith("note for the game") else None)
    assert quick.handle("[Sent from their Jarvis phone app] Jarvis, note for the game: the boss is too slow") == "studio says"


def test_scenarios_the_worker_adds_are_run_too(tmp_path):
    wt = tmp_path / "wt"
    (wt / "Scripts").mkdir(parents=True)
    (wt / "Scripts" / "integration_verify.ps1").write_text(
        '@("BossFight", "/Game/LSW/Boss/BossArena", "n"), @("BossKnockdown30", "/Game/LSW/Boss/BossArena", "n"),\n'
        '@("BossKnockdownShot", "/Game/LSW/Boss/BossArena", "r")\n')
    order = {"worktree": str(wt), "tests": {"scenarios": [{"name": "BossFight", "map": "/Game/LSW/Boss/BossArena", "render": False, "lane": True},
                                                          {"name": "Dummy", "map": "/Game/LSW/Tests/AnatomyArena", "render": False, "lane": False}]}}
    plan = studiojob.scenario_plan(order)
    assert [s["name"] for s in plan] == ["BossFight", "BossKnockdown30", "BossKnockdownShot", "Dummy"]
    assert plan[2]["render"] is True


def test_a_worker_that_changes_nothing_fails_the_order(lsw, monkeypatch):
    monkeypatch.setattr(studio, "note_pid", lambda oid, pid: None)
    monkeypatch.setattr(studiojob, "wait_for_quiet", lambda *a, **k: None)
    o = studio.draft("the boss feels too slow in phase 2")
    o["worktree"] = str(lsw.parent / "wt-noop")
    studio.save(o)
    studio.set_state(o, "approved")
    out = studiojob.run(o["id"], worker=lambda *a: {"ok": True, "text": "I couldn't inspect the code.", "cost": 0},
                        verifier=lambda *a: pytest.fail("nothing to build"), committer=lambda *a: "")
    assert out["state"] == "failed" and "changed no source files" in out["report"]
