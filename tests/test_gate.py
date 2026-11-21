"""scripts/gate.py: main only receives a commit whose full suite passed on that exact commit. Fakes only - no real git
command, merge or test run happens here."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from scripts import gate


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A fake git: HEAD/branch SHAs, a status listing, and a log of every git call made."""
    state = {"status": "", "head": "a" * 40, "branch": "feat/x", "tags": "", "calls": []}

    def fake_git(*args):
        state["calls"].append(args)
        if args[:1] == ("status",):
            return state["status"]
        if args == ("rev-parse", "HEAD") or args[:1] == ("rev-parse",):
            return state["head"]
        if args == ("branch", "--show-current"):
            return state["branch"]
        if args[:2] == ("tag", "--list"):
            return state["tags"]
        return ""

    monkeypatch.setattr(gate, "_git", fake_git)
    monkeypatch.setattr(gate, "RECORDS", tmp_path / "gate")
    monkeypatch.setattr(gate, "_running_game", lambda: "")
    return state


def ran(output, code=0, serial=("no tests ran in 0.01s\n", 5)):
    """A fake pytest: the parallel (or single) run answers `output`; the serial phase answers `serial`."""
    def run(cmd, *a, **k):
        if cmd[-2:] == ["-m", "serial"]:
            return SimpleNamespace(stdout=serial[0], stderr="", returncode=serial[1])
        return SimpleNamespace(stdout=output, stderr="", returncode=code)
    return run


def test_the_suite_runs_in_parallel_then_the_serial_tests_alone(repo, monkeypatch):
    calls = []
    base = ran("100 passed in 3s\n", serial=("3 passed in 2s\n", 0))
    rec = gate.check(runner=lambda cmd, *a, **k: (calls.append(cmd), base(cmd))[1])
    assert ["-n", str(gate.WORKERS), "-m", "not serial"] == calls[0][-4:] and calls[1][-2:] == ["-m", "serial"]
    assert rec["ok"] and rec["passed"] == 103
    # a failure in the serial phase fails the gate
    assert not gate.check(runner=ran("100 passed in 3s\n", serial=("FAILED tests/t.py::x\n1 failed in 1s\n", 1)))["ok"]
    monkeypatch.setattr(gate, "WORKERS", 1)                              # JARVIS_GATE_WORKERS=1: the old single run
    calls.clear()
    assert gate.check(runner=lambda cmd, *a, **k: (calls.append(cmd), base(cmd))[1])["passed"] == 100 and len(calls) == 1


def test_summary_counts_are_read_from_the_last_pytest_line():
    s = gate.parse_summary("...\nFAILED tests/x.py::t - boom\n= 2 failed, 3895 passed, 40 skipped, 5 warnings in 9.1s =\n")
    assert (s["passed"], s["failed"], s["skipped"], s["errors"]) == (3895, 2, 40, 0)
    assert gate.parse_summary("3922 passed, 40 skipped in 788.46s")["failed"] == 0


def test_a_passing_run_is_recorded_against_the_commit(repo):
    rec = gate.check(runner=ran("3922 passed, 40 skipped in 788.46s\n"))
    assert rec["ok"] and rec["sha"] == repo["head"] and gate.read_record(repo["head"])["passed"] == 3922


def test_a_failing_or_empty_run_is_never_ok(repo):
    assert not gate.check(runner=ran("FAILED tests/a.py::b\n1 failed, 10 passed in 1s\n", code=1))["ok"]
    assert not gate.check(runner=ran("no tests ran in 0.01s\n", code=5))["ok"]
    assert not gate.check(runner=ran("10 passed in 1s\n", code=1))["ok"]           # pytest said no, whatever the counts


def test_uncommitted_changes_are_refused(repo):
    repo["status"] = " M core/missions.py\n m teaching-test-work/test_source_changed_by_check_k0/repo"
    with pytest.raises(SystemExit) as e:
        gate.check(runner=ran("1 passed"))
    assert e.value.code == 3


def test_the_fixture_repo_noise_is_not_a_change(repo):
    repo["status"] = " m teaching-test-work/test_source_changed_by_check_k0/repo"
    assert gate.check(runner=ran("1 passed in 1s"))["ok"]


def test_real_git_output_keeps_the_leading_status_column(monkeypatch):
    """Live 2026-11-10: _git() stripped the first line's leading space, so ' m teaching-test-work/...' became
    'eaching-test-work/...' and the gate refused a clean tree."""
    out = SimpleNamespace(returncode=0, stdout=" m teaching-test-work/test_source_changed_by_check_k0/repo\n", stderr="")
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: out)
    assert gate.dirty_paths() == []
    out.stdout = " m teaching-test-work/x/repo\n M core/missions.py\n"
    assert gate.dirty_paths() == ["core/missions.py"]


def test_a_running_game_is_never_disturbed(repo, monkeypatch):
    monkeypatch.setattr(gate, "_running_game", lambda: "Apex Legends")
    with pytest.raises(SystemExit) as e:
        gate.check(runner=ran("1 passed"))
    assert e.value.code == 4


def test_merge_needs_a_passing_record_for_that_exact_commit(repo):
    with pytest.raises(SystemExit) as e:
        gate.merge("feat/x")
    assert e.value.code == 5                                             # never checked
    gate.check(runner=ran("1 failed, 9 passed in 1s", code=1))
    with pytest.raises(SystemExit) as e:
        gate.merge("feat/x")
    assert e.value.code == 5                                             # checked and failed
    gate.check(runner=ran("10 passed in 1s"))
    repo["head"] = "b" * 40                                              # a new commit after the passing run
    with pytest.raises(SystemExit) as e:
        gate.merge("feat/x")
    assert e.value.code == 5
    assert not any(c[:1] == ("merge",) for c in repo["calls"])


def test_a_passing_commit_is_merged_with_a_rollback_tag(repo):
    gate.check(runner=ran("10 passed in 1s"))
    repo["tags"] = "rollback/pre-feat-x-20261110-1900"
    tag = gate.merge("feat/x", now=datetime(2026, 11, 10, 19, 0))
    assert tag == "rollback/pre-feat-x-20261110-1900-2"                  # never reuses an existing tag
    names = [c[0] for c in repo["calls"]]
    assert names.index("tag") < names.index("checkout") < names.index("merge")
    assert not any(c[0] == "push" for c in repo["calls"])


def test_records_are_shared_by_every_worktree(tmp_path, monkeypatch):
    """A branch is checked in its own worktree and merged from the main checkout: both must read the same record."""
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="C:/repo/.git\n"))
    assert gate._shared_records().as_posix().endswith("repo/.git/jarvis-gate")
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=128, stdout=""))
    assert gate._shared_records() == gate.ROOT / "runs" / "gate"
