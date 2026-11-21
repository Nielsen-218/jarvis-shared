"""Desktop practice worlds (core/labworlds): the judges, the world lifecycle and the practice cycle, all with fakes.
Nothing here starts a window, a lab, a model or reads the clock."""
import copy
import json

import pytest

from core import acquire, labpractice, labworlds, methodlang, skillcontracts

SIX = ["close_foreground", "close_app", "use_app", "type_text", "save_document", "open_path"]


def start_state(world):
    """What the lab's probe would read right after the world came up: front window first."""
    wins = sorted(world["windows"], key=lambda w: not w.get("front"))
    return {"windows": [{"title": w["title"], "edit": w.get("edit"), "status": "pressed="} for w in wins], "files": {}}


def solved(world):
    """The correct end state, written by hand per kind (the judge's own logic is not reused)."""
    before = start_state(world)
    after = copy.deepcopy(before)
    kind, params, by = world["kind"], world["params"], {w["title"]: w for w in world["windows"]}
    wins = {w["title"]: w for w in after["windows"]}
    if kind == "close_foreground":
        after["windows"] = after["windows"][1:]
    elif kind == "close_app":
        after["windows"] = [w for w in after["windows"] if not w["title"].endswith(" - " + params["app"])]
    elif kind == "use_app":
        wins[world["expect"]["window"]]["status"] = "pressed=" + world["expect"]["button"]
    elif kind == "type_text":
        wins[world["expect"]["window"]]["edit"] += params["text"]
    elif kind == "save_document":
        t = world["expect"]["window"]
        wins[t]["status"] = "pressed=Save"
        after["files"][params["path"]] = by[t]["edit"]
    elif kind == "open_path":
        wins["scratch - LabFiles"]["status"] = "pressed=Open"
        after["windows"].insert(0, {"title": params["path"] + " - LabView", "edit": None, "status": "viewing " + params["path"]})
    return before, after


OK = {"ok": True, "message": "done"}


@pytest.mark.parametrize("kind", SIX)
def test_every_weakness_has_a_world_a_task_statement_and_a_judge(kind):
    assert skillcontracts.KINDS[kind]["world"] == "desktop"
    for seed in range(1, 25):
        world = skillcontracts.generate(seed, kind)
        assert world == labworlds.generate(seed, kind)                       # same seed, same world
        assert set(world["params"]) == set(labworlds.KINDS[kind]["params"])
        assert sum(1 for w in world["windows"] if w.get("front")) == 1
        assert len({w["title"] for w in world["windows"]}) == len(world["windows"]) >= 2
        assert skillcontracts.goal(kind, world["params"])
        before, after = solved(world)
        verdict = skillcontracts.judge(world, before, after, OK)
        assert verdict["passed"], (seed, verdict)
        assert not labworlds.judge(world, before, copy.deepcopy(before), OK)["passed"]      # nothing done is never a pass
        assert not labworlds.judge(world, before, after, {"ok": False, "error": "gave up"})["passed"]


@pytest.mark.parametrize("kind", SIX)
def test_the_judge_fails_collateral_damage(kind):
    world = labworlds.generate(3, kind)
    before, after = solved(world)
    keep = {"close_foreground": before["windows"][0]["title"]}.get(kind) or world["expect"].get("window") or "scratch - LabFiles"
    spare = next(w for w in after["windows"] if w["title"] != keep and w["edit"] is not None and not w["title"].endswith("LabView")
                 and w["title"] not in world["expect"].get("closed", []))
    lost = copy.deepcopy(after)
    lost["windows"] = [w for w in lost["windows"] if w["title"] != spare["title"]]
    assert "wrongly closed" in " ".join(labworlds.judge(world, before, lost, OK)["diff"])
    typed = copy.deepcopy(after)
    next(w for w in typed["windows"] if w["title"] == spare["title"])["edit"] += "x"
    assert not labworlds.judge(world, before, typed, OK)["checks"]["other_text_kept"]
    pressed = copy.deepcopy(after)
    next(w for w in pressed["windows"] if w["title"] == spare["title"])["status"] = "pressed=Reset"
    assert not labworlds.judge(world, before, pressed, OK)["checks"]["no_other_button_pressed"]
    wrote = copy.deepcopy(after)
    wrote["files"]["stray.txt"] = "x"
    assert not labworlds.judge(world, before, wrote, OK)["checks"]["no_other_file_written"]
    extra = copy.deepcopy(after)
    extra["windows"].append({"title": "other.txt - LabView", "edit": None, "status": ""})
    assert not labworlds.judge(world, before, extra, OK)["checks"]["no_stray_windows"]


def test_the_specific_end_states_are_checked():
    w = labworlds.generate(5, "use_app")
    before, after = solved(w)
    twice = copy.deepcopy(after)
    next(x for x in twice["windows"] if x["title"] == w["expect"]["window"])["status"] += "," + w["expect"]["button"]
    assert not labworlds.judge(w, before, twice, OK)["passed"]               # exactly once
    w = labworlds.generate(5, "type_text")
    before, after = solved(w)
    replaced = copy.deepcopy(after)
    box = next(x for x in replaced["windows"] if x["title"] == w["expect"]["window"])
    box["edit"] = "something else"
    assert not labworlds.judge(w, before, replaced, OK)["checks"]["text_in_the_box"]
    w = labworlds.generate(5, "save_document")
    before, after = solved(w)
    wrong = copy.deepcopy(after)
    wrong["files"][w["params"]["path"]] = "not the text"
    assert not labworlds.judge(w, before, wrong, OK)["checks"]["file_has_the_text"]
    w = labworlds.generate(5, "open_path")
    before, after = solved(w)
    other = copy.deepcopy(after)
    other["windows"][0]["title"] = "wrong.txt - LabView"
    assert not labworlds.judge(w, before, other, OK)["passed"]


@pytest.mark.parametrize("kind", SIX)
def test_the_reference_methods_are_valid_methods(kind):
    methodlang.check(labworlds.REFERENCE[kind])


class FakeProc:
    def __init__(self, ready, alive=True):
        self.pid, self.killed, self._alive, self._ready = 4242, False, alive, ready

    def poll(self):
        return None if self._alive and not self.killed else 1

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def test_world_lifecycle_starts_only_its_own_target_and_closes_it(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_SKILL_LAB", raising=False)
    world = labworlds.generate(2, "save_document")
    with pytest.raises(RuntimeError, match="hidden desktop"):               # never outside a lab job: no window for the owner
        labworlds.build(world, tmp_path, 0)
    started = []

    def popen(cmd, cwd=None):
        started.append(cmd)
        (tmp_path / "scratch-0").mkdir(exist_ok=True)
        (tmp_path / "scratch-0" / "ready").write_text("1")
        return FakeProc(True)
    handle = labworlds.build(world, tmp_path, 0, popen=popen, sleep=lambda s: None)
    assert len(started) == 1 and started[0][1:3] == ["-m", "core.labtarget"]
    spec = json.loads((tmp_path / "world-0.json").read_text(encoding="utf-8"))
    assert spec["scratch"] == str(tmp_path / "scratch-0") and [w["title"] for w in spec["windows"]] == [w["title"] for w in world["windows"]]
    (tmp_path / "scratch-0" / world["params"]["path"]).write_text("saved text", encoding="utf-8")
    snap = labworlds.snapshot(handle, probe=lambda pid: [{"title": "t", "edit": "", "status": ""}] if pid == 4242 else [])
    assert snap["files"] == {world["params"]["path"]: "saved text"} and snap["windows"][0]["title"] == "t"   # 'ready' marker is not a file
    labworlds.teardown(handle)
    assert handle["proc"].killed
    labworlds.teardown(handle)                                               # twice is harmless


def test_a_target_that_never_starts_is_killed_and_reported(tmp_path):
    proc = FakeProc(False, alive=False)
    with pytest.raises(RuntimeError, match="did not start"):
        labworlds.build(labworlds.generate(1, "close_app"), tmp_path, 0, popen=lambda cmd, cwd=None: proc, sleep=lambda s: None)


def test_a_built_in_tool_is_never_run_in_a_desktop_world(tmp_path, monkeypatch):
    world = labworlds.generate(1, "type_text")
    handle = {"proc": FakeProc(True), "pid": 4242, "scratch": str(tmp_path)}
    monkeypatch.setattr(labworlds, "build", lambda *a, **k: handle)
    monkeypatch.setattr(labworlds, "snapshot", lambda h, probe=None: start_state(world))
    monkeypatch.setattr(labworlds.time, "sleep", lambda s: None)
    with pytest.raises(ValueError, match="does not run"):
        labworlds.run_trial(world, {"type": "native"}, tmp_path, 0)
    assert handle["proc"].killed                                             # the world is closed even when the trial breaks
    row = labworlds.run_trial(world, {"type": "noop"}, tmp_path, 0)
    assert row["judge"]["passed"] is False


# ------------------------------------------------------------------------------------------------ practice selection and cycle

def miss(tool, args, at, said="do the thing"):
    return {"said": said, "reason": "step not verified: it did not happen", "at": at,
            "decision": {"intents": [{"steps": [{"tool": tool, "arguments": args}]}]}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire.storage, "DATA", tmp_path / "data")
    monkeypatch.setenv("JARVIS_LEARNED_DIR", str(tmp_path / "learned"))
    monkeypatch.setenv("JARVIS_ACQUIRE_DIR", str(tmp_path / "evidence"))
    return tmp_path


def test_the_six_weaknesses_are_ready_and_ranked_by_how_often_they_really_happen(store):
    counts = {"type_text": 5, "close_app": 3, "use_app": 2, "save_document": 2, "open_path": 1, "close_foreground": 4}
    misses = [miss(t, {}, 1000.0 + i) for t, n in counts.items() for i in range(n)]
    rows = acquire.discover(misses, studied={})
    ready = [r for r in rows if r["status"] == "ready"]
    assert {r["tool"] for r in ready} == set(SIX) and all(r["lab"] for r in ready)
    assert [r["count"] for r in ready] == sorted(counts.values(), reverse=True)
    assert ready[0]["tool"] == "type_text" and ready[1]["tool"] == "close_foreground"


def fake_lab(passes):
    """A lab whose judge passes a method trial when the method source contains `passes`."""
    calls = []

    def lab(trials, label):
        calls.append(label)
        out = []
        for t in trials:
            if t["action"]["type"] == "observe":
                out.append({"result": {"ok": True, "tree": "Window 'x - LabPad'\n  Button 'Close'"}})
            else:
                good = passes in t["action"]["source"]
                out.append({"result": {"ok": True, "trace": []}, "judge": {"passed": good, "checks": {}, "diff": [] if good else ["still open: x"]}})
        return {"status": "done", "results": out}
    lab.calls = calls
    return lab


def learner_of(replies):
    replies = list(replies)
    return acquire.Learner(ask=lambda prompt, temperature, fmt: replies.pop(0) if replies else "", guard=False)


BAD = "```python\ndef run(ui, params):\n    return 'nothing'\n```"
GOOD = "```python\ndef run(ui, params):\n    ui.log('good')\n    return 'closed'\n```"


def test_practice_once_takes_the_most_frequent_desktop_weakness_and_never_puts_it_into_use(store, monkeypatch):
    misses = [miss("close_app", {"app": "x"}, 1000.0 + i) for i in range(3)] + [miss("open_path", {"path": "p"}, 2000.0)]
    monkeypatch.setattr(acquire, "idle_ready", lambda: "")
    real = acquire.discover
    monkeypatch.setattr(acquire, "discover", lambda *a, **k: real(misses, studied={}))
    lab, learner = fake_lab("ui.log('good')"), learner_of(["import os", BAD, GOOD])
    picked = []

    def learn_one(cand):
        picked.append(cand["tool"])
        return acquire.learn(cand, learner=learner, lab=lab)
    rec = acquire.practice_once(ready_check=lambda: "", learn_one=learn_one, now=10_000.0)
    assert picked == ["close_app"]
    assert rec["status"] == "practised" and rec["practice"]["passed"] == rec["practice"]["total"] == len(labpractice.EXAM_SEEDS)
    assert rec["practice"]["in_use"] is False and rec["cloud_calls"] == 0
    assert lab.calls == ["observe", "practice-2", "practice-3", "exam-3"]       # round 1 was refused before it ever ran
    assert acquire.learned_methods() == [] and acquire.candidates_for("close_app") == []
    assert "lab only" in acquire.summary(rec) and "12 lab exam tasks" in acquire.summary(rec)
    # passed this week: the next slot goes to the next most frequent weakness
    stamp = __import__("pathlib").Path(rec["folder"], "cycle.json").stat().st_mtime
    acquire.practice_once(ready_check=lambda: "", learn_one=lambda c: picked.append(c["tool"]) or {"status": "x"}, now=stamp + 30 * 3600)
    assert picked == ["close_app", "open_path"]


def test_a_method_that_never_passes_is_not_learned(store):
    cand = acquire.discover([miss("save_document", {"app": "x", "path": "p"}, 5.0)], studied={})[0]
    rec = acquire.learn(cand, learner=learner_of([BAD] * 10), lab=fake_lab("ui.log('good')"))
    assert rec["status"] == "not_learned" and "practice" not in rec
    assert not labpractice.resting(cand, 10.0)


def test_set_value_is_a_method_primitive_for_edit_boxes_only():
    class El:
        def __init__(self, kind):
            self.element_info = type("I", (), {"control_type": kind, "name": "", "runtime_id": None})()
            self.iface_value = type("V", (), {"SetValue": lambda s, t: setattr(self, "text", t)})()
    ui = methodlang.UIA.__new__(methodlang.UIA)
    ui._els, ui._ids = {}, {}
    box, button = El("Edit"), El("Button")
    ui._els = {1: (box, 1), 2: (button, 1)}
    assert ui.set_value(1, "hello") and box.text == "hello"
    with pytest.raises(ValueError):
        ui.set_value(2, "hello")
    out = methodlang.run("def run(ui, params):\n    ui.set_value(1, params['text'])\n    return 'ok'\n", ui, {"text": "abc"})
    assert out["ok"] and box.text == "abc"


# ------------------------------------------------------------------ review 2026-11-12: a lab run that did not finish is not a pass

@pytest.mark.parametrize("status, keep", [("crashed", 0), ("timeout", 0), ("crashed", 2), ("timeout", 99)])
def test_a_lab_job_that_did_not_finish_is_never_recorded_as_practised(store, status, keep):
    inner = fake_lab("ui.log('good')")

    def lab(trials, label):
        out = inner(trials, label)
        return out if label == "observe" else {"status": status, "results": out["results"][:keep]}
    cand = acquire.discover([miss("close_app", {"app": "x"}, 5.0)], studied={})[0]
    rec = acquire.learn(cand, learner=learner_of([GOOD] * 10), lab=lab)
    assert rec["status"] == "not_learned" and "practice" not in rec
    assert not labpractice.resting(cand, 10.0)             # the real miss is practised again, not rested for a week
    assert "passed all" not in acquire.summary(rec)


def test_only_the_exam_job_failing_is_also_not_a_pass(store):
    inner = fake_lab("ui.log('good')")

    def lab(trials, label):
        out = inner(trials, label)
        return {"status": "crashed", "results": []} if label.startswith("exam") else out
    cand = acquire.discover([miss("close_app", {"app": "x"}, 5.0)], studied={})[0]
    rec = acquire.learn(cand, learner=learner_of([GOOD] * 10), lab=lab)
    assert rec["status"] == "not_learned" and "practice" not in rec


# ------------------------------------------------------------------------------------------------ the world must be found (launcher pid)

_REAL_FAMILY = labworlds._family


@pytest.fixture(autouse=True)
def _no_real_process_lookups(monkeypatch):
    """Tests never look up or kill a real process by a fake pid."""
    monkeypatch.setattr(labworlds, "_family", lambda pid, psutil=None: {pid})


class _P:
    def __init__(self, pid, kids=()):
        self.pid, self._kids = pid, list(kids)

    def children(self, recursive=False):
        return self._kids


class _FakePsutil:
    """A venv launcher (100) whose child (200) is the real interpreter and whose grandchild (300) is something it started."""
    def __init__(self):
        self.tree = {100: _P(100, [_P(200), _P(300)])}

    def Process(self, pid):
        if pid not in self.tree:
            raise RuntimeError("no such process")
        return self.tree[pid]


def test_family_includes_descendants_of_the_launcher_pid():
    assert _REAL_FAMILY(100, psutil=_FakePsutil()) == {100, 200, 300}
    assert _REAL_FAMILY(555, psutil=_FakePsutil()) == {555}                  # gone: just the pid


def test_teardown_ends_the_real_child_behind_the_launcher(monkeypatch):
    killed = []
    monkeypatch.setattr(labworlds, "_family", lambda pid, psutil=None: {pid, 200, 300})
    monkeypatch.setattr(labworlds, "_kill_pid", killed.append)
    handle = {"proc": FakeProc(True), "pid": 100, "scratch": "x"}
    labworlds.teardown(handle)
    assert sorted(killed) == [200, 300] and handle["proc"].killed


def test_a_world_that_does_not_come_up_carries_the_probe_evidence(tmp_path, monkeypatch):
    world = labworlds.generate(1, "close_foreground")
    handle = {"proc": FakeProc(True), "pid": 100, "scratch": str(tmp_path)}
    monkeypatch.setattr(labworlds, "build", lambda *a, **k: handle)
    monkeypatch.setattr(labworlds, "snapshot", lambda h, probe=None: {"windows": [], "files": {}})
    monkeypatch.setattr(labworlds, "_all_windows", lambda: [(200, "a - LabPad"), (200, "")])
    monkeypatch.setattr(labworlds.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError) as err:
        labworlds.run_trial(world, {"type": "noop"}, tmp_path, 0)
    text = str(err.value)
    assert "did not come up" in text and f"expected {len(world['windows'])} windows, found 0" in text
    assert "pid looked for 100" in text and "(200, 'a - LabPad')" in text
    assert handle["proc"].killed


def test_came_up_names_the_front_window_difference():
    world = labworlds.generate(1, "close_foreground")
    front = next(w["title"] for w in world["windows"] if w["front"])
    other = next(w["title"] for w in world["windows"] if not w["front"])
    ok = {"windows": [{"title": front}] + [{"title": w["title"]} for w in world["windows"] if w["title"] != front]}
    assert labworlds._came_up(world, ok) == ""
    swapped = {"windows": [{"title": other}] + [{"title": w["title"]} for w in world["windows"] if w["title"] != other]}
    assert "in front" in labworlds._came_up(world, swapped)


# ------------------------------------------------------------------------------------------------ scoring honesty: a broken lab is not a failed lesson

FAULT = "Traceback (most recent call last):\nRuntimeError: lab: the world did not come up as specified: expected 2 windows, found 0"


def broken_lab(on=("practice",), status="done"):
    """A lab whose worlds never come up (every trial row is a lab_error), exactly as production writes them."""
    inner = fake_lab("ui.log('good')")
    seen = []

    def lab(trials, label):
        seen.append(label)
        if label.split("-")[0] in on:
            return {"status": status, "results": [{"trial": i, "lab_error": FAULT} for i in range(len(trials))]}
        return inner(trials, label)
    lab.seen = seen
    return lab


def test_a_lab_that_never_comes_up_is_an_error_with_cause_lab_not_a_failed_lesson(store):
    cand = acquire.discover([miss("close_foreground", {}, 5.0)], studied={})[0]
    learner = learner_of([GOOD] * 10)
    lab = broken_lab()
    rec = acquire.learn(cand, learner=learner, lab=lab)
    assert rec["status"] == "error" and rec["cause"] == "lab" and rec["error_type"] == "LabFault"
    assert "all 5 trials failed in the lab itself" in rec["error"] and "did not come up" in rec["error"]
    assert lab.seen == ["observe", "practice-1"]                              # stops at once: no feedback loop on a broken lab
    assert learner.calls == 1 and rec["cloud_calls"] == 0
    assert "practice" not in rec and not labpractice.resting(cand, 10.0)


def test_a_lab_fault_never_counts_toward_the_teacher_stall(store, monkeypatch):
    from core import teacher
    monkeypatch.setattr(teacher, "_evidence_root", acquire._evidence_root)
    cand = acquire.discover([miss("close_foreground", {}, 5.0)], studied={})[0]
    for _ in range(4):
        rec = acquire.learn(cand, learner=learner_of([GOOD] * 10), lab=broken_lab())
        assert rec["cause"] == "lab"
    assert teacher.cycles(cand["id"]) == []
    assert teacher.stalled(cand["id"])[0] is False
    # while a real crash still counts, as before
    assert acquire.error_cause(RuntimeError("x")) == "crash" and acquire.error_cause(PermissionError("x")) == "crash"
    assert acquire.error_cause(acquire.LearnerUnavailable("down")) == "model_unavailable"


def test_a_mixed_failure_is_still_the_learners_failure(store):
    inner = fake_lab("ui.log('good')")

    def lab(trials, label):
        out = inner(trials, label)
        if label.startswith("practice"):
            out["results"][0] = {"trial": 0, "lab_error": FAULT}                # one world broke, the rest were judged
        return out
    cand = acquire.discover([miss("close_foreground", {}, 5.0)], studied={})[0]
    rec = acquire.learn(cand, learner=learner_of([BAD] * 10), lab=lab)
    assert rec["status"] == "not_learned"


def test_an_exam_the_lab_could_not_run_at_all_is_a_lab_fault(store):
    cand = acquire.discover([miss("close_foreground", {}, 5.0)], studied={})[0]
    rec = acquire.learn(cand, learner=learner_of([GOOD] * 10), lab=broken_lab(on=("exam",)))
    assert rec["status"] == "error" and rec["cause"] == "lab" and "exam round" in rec["error"]


def test_the_observation_failing_in_the_lab_stops_before_the_learner_is_asked(store):
    cand = acquire.discover([miss("close_foreground", {}, 5.0)], studied={})[0]
    learner = learner_of([GOOD] * 10)
    rec = acquire.learn(cand, learner=learner, lab=broken_lab(on=("observe",)))
    assert rec["cause"] == "lab" and learner.calls == 0


def test_the_next_cycle_retries_a_weakness_whose_last_cycle_was_a_lab_fault(store, monkeypatch):
    import pathlib
    misses = [miss("close_foreground", {}, 1000.0)]
    monkeypatch.setattr(acquire, "idle_ready", lambda: "")
    real = acquire.discover
    monkeypatch.setattr(acquire, "discover", lambda *a, **k: real(misses, studied={}))
    cand = real(misses, studied={})[0]
    rec = acquire.learn(cand, learner=learner_of([GOOD] * 10), lab=broken_lab())
    stamp = pathlib.Path(rec["folder"], "cycle.json").stat().st_mtime
    picked = []
    acquire.practice_once(ready_check=lambda: "", learn_one=lambda c: picked.append(c["tool"]) or {"status": "x"}, now=stamp + 60)
    assert picked == ["close_foreground"]                                      # inside the usual one-a-day window, but the lab was at fault
    rec = acquire.learn(cand, learner=learner_of([BAD] * 10), lab=fake_lab("ui.log('good')"))     # a real not_learned does wait the day
    stamp = pathlib.Path(rec["folder"], "cycle.json").stat().st_mtime
    picked.clear()
    acquire.practice_once(ready_check=lambda: "", learn_one=lambda c: picked.append(c["tool"]) or {"status": "x"}, now=stamp + 60)
    assert picked == []


def test_a_real_exception_is_recorded_by_type_and_an_oserror_is_not_a_model_outage(store):
    cand = acquire.discover([miss("close_foreground", {}, 5.0)], studied={})[0]

    def lab(trials, label):
        raise PermissionError("denied: lab folder")
    rec = acquire.learn(cand, learner=learner_of([GOOD] * 10), lab=lab)
    assert rec["status"] == "error" and rec["cause"] == "crash" and rec["error_type"] == "PermissionError"
    assert rec["error"].startswith("PermissionError: denied")


def test_the_learner_is_told_that_try_except_does_not_exist():
    assert "NO try/except" in methodlang.API_DOC
    prompt = labpractice._prompt("close_foreground", labworlds.generate(600, "close_foreground"), "Window", "", "")
    assert "NO try/except" in prompt and "NO `while`" in prompt


def test_the_watcher_resolves_its_data_root_from_storage_not_from_its_own_depth():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "scripts" / "acquire_watch.py").read_text(encoding="utf-8")
    assert "parents[1]" not in src and "storage.live_root()" in src
    assert "lab_broken" in src and 'cause") == "lab"' in src


@pytest.mark.parametrize("status", ["timeout", "crashed"])
def test_a_job_the_clock_killed_or_that_crashed_with_no_results_is_the_learners_failure(store, status):
    """A method that hangs inside one UIA call is only stopped by the job's wall clock: that must not read as a lab fault."""
    cand = acquire.discover([miss("close_foreground", {}, 5.0)], studied={})[0]
    rec = acquire.learn(cand, learner=learner_of([GOOD] * 10), lab=broken_lab(status=status))
    assert rec["status"] == "not_learned" and rec.get("cause") != "lab"


def test_live_root_is_the_owning_checkout_for_a_worktree_and_itself_otherwise(tmp_path, monkeypatch):
    from core import storage
    monkeypatch.delenv("JARVIS_LIVE_ROOT", raising=False)
    assert storage.live_root(tmp_path / "jarvis" / ".worktrees" / "m-lab") == tmp_path / "jarvis"
    assert storage.live_root(tmp_path / "jarvis") == tmp_path / "jarvis"
    monkeypatch.setenv("JARVIS_LIVE_ROOT", str(tmp_path / "elsewhere"))
    assert storage.live_root(tmp_path / "jarvis") == tmp_path / "elsewhere"


def test_repeated_lab_faults_stop_being_retried_by_practice_once(store, monkeypatch):
    import pathlib
    misses = [miss("close_foreground", {}, 1000.0)]
    monkeypatch.setattr(acquire, "idle_ready", lambda: "")
    real = acquire.discover
    monkeypatch.setattr(acquire, "discover", lambda *a, **k: real(misses, studied={}))
    cand = real(misses, studied={})[0]
    picked = []
    for n in range(acquire.MAX_LAB_FAULT_RETRIES):
        rec = acquire.learn(cand, learner=learner_of([GOOD] * 10), lab=broken_lab())
        stamp = pathlib.Path(rec["folder"], "cycle.json").stat().st_mtime
        picked.clear()
        acquire.practice_once(ready_check=lambda: "", learn_one=lambda c: picked.append(1) or {"status": "x"}, now=stamp + 60)
        assert bool(picked) == (n + 1 < acquire.MAX_LAB_FAULT_RETRIES)       # the last fault in the streak waits its day
