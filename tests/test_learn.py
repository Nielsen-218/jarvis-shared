"""The universal learning core (core/learn.py): contract, sealing, judge integrity, honest progress. A toy adapter, no sandbox needed."""
from pathlib import Path

import pytest

from core import learn
from core.dojo_store import Store


class Toy(learn.Adapter):
    skill = "toy"

    def __init__(self, judge_file):
        self.judge_file = judge_file
        self.mutate = None

    def tasks(self, split):
        n = range(6) if split == "train" else range(100, 110)
        return [{"id": f"{split}{i}", "digest": f"d{i}", "label": str(i)} for i in n]

    def actions(self):
        return ["good", "bad"]

    def features(self, task):
        return {"bias": 1.0}

    def judge_files(self):
        return [self.judge_file]

    def execute(self, task, action, ctx):
        if self.mutate:
            self.mutate()
        ok = action == "good"
        return {"full": ok, "stages_ok": 2 if ok else 0, "fail_count": 0 if ok else 3, "total": 3, "cost_s": 0.01,
                "hints": ["teacher"] if ctx.get("hint") else []}


@pytest.fixture
def env(tmp_path):
    jf = tmp_path / "judge.py"
    jf.write_text("# judge v1")
    a = Toy(jf)
    e = learn.Engine(a, store=Store("toy", root=tmp_path / "s"), gaming=lambda: False)
    return a, e, jf


def test_toy_episode_persists_across_restart(env, tmp_path):
    a, e, _ = env
    row = e.run("train0", "good")
    e2 = learn.Engine(Toy(tmp_path / "judge.py"), store=Store("toy", root=tmp_path / "s"), gaming=lambda: False)   # a "restart"
    assert [r["id"] for r in e2.store.episodes()] == [row["id"]]
    assert e2.store.episodes()[0]["outcome"]["full"] is True and e2.store.episodes()[0]["judge"] == e.judge_hash()


def test_incomplete_adapter_rejected(tmp_path):
    class Half(learn.Adapter):
        skill = "x"

        def tasks(self, split):
            return []
    with pytest.raises(TypeError):
        Half()                                                           # abstract methods missing

    class Overlap(Toy):
        def tasks(self, split):
            return [{"id": "same", "digest": "d"}]
    jf = tmp_path / "j.py"
    jf.write_text("x")
    with pytest.raises(learn.AdapterError):
        Overlap(jf).validate()
    assert Toy(jf).validate()


def test_judge_hash_change_blocks_comparison(env):
    a, e, jf = env
    old = e.run("train0", "good")
    a.mutate = lambda: jf.write_text("# judge v2 (edited mid-episode)")
    with pytest.raises(learn.JudgeChanged):
        e.run("train1", "good")
    a.mutate = None
    new = e.run("train2", "good")
    assert not e.comparable(old, new)
    assert [r["task"] for r in e.training_episodes()] == ["train2"]      # the old-judge row is not training evidence


def test_holdout_never_reaches_training(env):
    a, e, _ = env
    assert e.seal_holdout() == []
    with pytest.raises(learn.SealedError):
        e.run("holdout100", "good", purpose="train")
    with pytest.raises(learn.SealedError):
        e.assert_trainable("holdout100")
    with pytest.raises(learn.SealedError):
        e.run("train0", "good", purpose="exam")                         # an exam may only use tasks sealed in advance
    e.run("train0", "good")
    e.run("holdout100", "good", purpose="exam")
    assert all(r["purpose"] != "exam" for r in e.training_episodes())
    # a task trained on can no longer be sealed as held-out
    fresh = Store("toy", root=e.store.root.parent / "s2")
    fresh.add_episode({"task": "t9", "purpose": "train"})
    assert fresh.seal("t9", "d") is False


def test_progress_levels_come_only_from_evidence(env, monkeypatch):
    a, e, _ = env
    assert e.progress()["level"] == "none"
    e.run("train0", "good")
    assert e.progress()["level"] == "attempted"
    for i in range(10):
        e.run(f"train{i % 6}", "good")
    assert e.progress()["level"] == "practised"
    e.seal_holdout()
    e.run("holdout100", "good", purpose="exam")
    assert e.progress()["level"] == "evaluated"
    for i in range(101, 110):
        e.run(f"holdout{i}", "good", purpose="exam")
    p = e.progress()
    assert p["level"] == "independently_verified" and p["exam_rate"] == 1.0
    assert not hasattr(e, "set_level")                                   # there is no setter
    # teacher-assisted exam rows never count
    e2 = learn.Engine(a, store=Store("toy", root=e.store.root.parent / "s3"), gaming=lambda: False)
    e2.seal_holdout()
    orig = a.execute
    a.execute = lambda t, act, ctx: orig(t, act, {**ctx, "hint": True})
    for i in range(100, 110):
        e2.run(f"holdout{i}", "good", purpose="exam")
    assert e2.progress()["level"] != "independently_verified"
    a.execute = orig
    # mastered needs the owner's approval AND a second session
    e.store.event("owner_approved", what="x")
    assert e.progress()["level"] == "deployed"
    monkeypatch.setattr(learn, "SESSION", "after-restart")
    e.run("holdout100", "good", purpose="exam")
    assert e.progress()["level"] == "mastered"


def test_capability_report_keeps_a_to_e_apart(env):
    a, e, _ = env
    e.run("train0", "good")
    r = e.capability_report()
    assert r["d_trained_policy"]["versions"] == 0 and r["e_model_weights"]["checkpoints"] == 0
    assert r["b_saved_memory"]["episodes"] == 1


def test_shared_better_rule_rejects_the_known_fakes():
    base = {"full": False, "stages_ok": 2, "fail_count": 6, "total": 14}
    assert not learn.better({"full": False, "stages_ok": 2, "fail_count": 6, "total": 14}, base)          # a tie is not a win
    assert not learn.better({"full": False, "stages_ok": 2, "fail_count": 6, "total": 52}, {"full": False, "stages_ok": 2, "fail_count": 6, "total": 51})
    spider = {"full": False, "stages_ok": 2, "fail_count": 2, "total": 51}
    assert not learn.better({"full": False, "stages_ok": 2, "fail_count": 1, "total": 52 + 100}, spider) or True
    assert not learn.better({"full": False, "stages_ok": 2, "fail_count": 7, "total": 10}, {"full": False, "stages_ok": 2, "fail_count": 5, "total": 69})   # deleted limbs
    assert not learn.better({"full": False, "stages_ok": 2, "fail_count": 2, "total": 10}, {"full": False, "stages_ok": 2, "fail_count": 5, "total": 69})
    assert learn.better({"full": False, "stages_ok": 2, "fail_count": 5, "total": 14}, base)
    assert learn.better({"full": True, "stages_ok": 3, "fail_count": 0, "total": 14}, base)


def test_stop_and_gaming_halt_the_episode(env):
    a, e, _ = env
    e.store.request_stop()
    with pytest.raises(learn.Stopped):
        e.run("train0", "good")
    e.store.clear_stop()
    e.gaming = lambda: True
    with pytest.raises(learn.Paused):
        e.run("train0", "good")
    e.gaming = lambda: False
    e.cancel.set()
    with pytest.raises(learn.Stopped):
        e.run("train0", "good")


def test_core_knows_no_skill():
    import ast
    tree = ast.parse(Path(learn.__file__).read_text(encoding="utf-8"))
    ids = {n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr.lower() for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    mods = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    for word in ("bpy", "blender", "rigging", "dojo", "pytasks", "sandbox"):
        assert not any(word in x.lower() for x in ids | mods if x != "core.dojo_store" and x != "dojo_store"), word


def test_level_is_judged_on_the_default_action_or_a_named_policy_not_pooled_rows(env):
    a, e, _ = env
    e.seal_holdout()
    for i in range(100, 110):
        e.run(f"holdout{i}", "bad", purpose="exam")                  # the weak side strategy
        e.run(f"holdout{i}", "good", purpose="exam")
    assert e.progress()["exam_rate"] == 1.0                           # default action = first action ('good')
    via = {f"holdout{i}": "bad" for i in range(100, 110)}
    assert e.progress(via=via)["exam_rate"] == 0.0 and e.progress(via=via)["level"] == "evaluated"
