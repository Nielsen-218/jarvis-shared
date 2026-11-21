"""The RL pathway: reward from the judge, a LinUCB policy UPDATED from experience, versioning, persistence, honest held-out evaluation."""
import json
import random

import numpy as np
import pytest

from core import learn, learn_policy as LP
from core.dojo_store import Store


def out(full, fail=0, total=10, cost=1.0, err=None):
    return {"full": full, "stages_ok": 2 if full else 1, "fail_count": fail, "total": total, "cost_s": cost, "error": err}


def test_reward_comes_from_the_judge_and_penalises_hacks():
    assert LP.reward(out(True, 0, 10, 0.0)) == 1.0
    assert LP.reward(out(True, 0, 10, 0.5)) > LP.reward(out(True, 0, 10, 20))             # time costs
    assert LP.reward(out(False, 8)) < LP.reward(out(False, 2))                              # fewer failures is better
    assert LP.reward(out(False, 3, 10, 1, err="Traceback")) < LP.reward(out(False, 3, 10, 1))   # invalid action
    assert LP.reward(out(True, 0, 3), ref_total=10) == -1.0                                 # "passing" by shrinking the tests


class Skill(learn.Adapter):
    """Two kinds of task; action 'a' works on kind 0, action 'b' on kind 1, 'c' never. A fixed default can only be right half the time."""
    skill = "synthetic"

    def __init__(self, judge):
        self.judge = judge

    def tasks(self, split):
        base = 0 if split == "train" else 1000
        n = 60 if split == "train" else 50
        return [{"id": f"{split}{i}", "digest": f"d{base + i}", "kind": (i * 7 + (3 if split == "holdout" else 0)) % 2} for i in range(n)]

    def actions(self):
        return ["noop", "a", "b", "c"]

    def default_action(self):
        return "a"

    def features(self, task):
        return {"bias": 1.0, "kind": float(task["kind"])}

    def judge_files(self):
        return [self.judge]

    def execute(self, task, action, ctx):
        ok = (action == "a" and task["kind"] == 0) or (action == "b" and task["kind"] == 1)
        return {"full": ok, "stages_ok": 2 if ok else 1, "fail_count": 0 if ok else 4, "total": 10, "cost_s": 1.0}


@pytest.fixture
def engine(tmp_path):
    j = tmp_path / "j.py"
    j.write_text("judge")
    e = learn.Engine(Skill(j), store=Store("synthetic", root=tmp_path / "s"), gaming=lambda: False)
    e.seal_holdout()
    for t in e.adapter.tasks("train"):
        for a in e.adapter.actions():
            e.run(t["id"], a)
    return e


def test_policy_is_updated_from_rewards_and_learns_when_each_action_works(engine):
    pol = LP.train(engine, epochs=6, seed=3)
    assert pol.updates > 0 and sum(pol.n.values()) == pol.updates
    assert pol.choose({"bias": 1.0, "kind": 0.0}, explore=False) == "a"
    assert pol.choose({"bias": 1.0, "kind": 1.0}, explore=False) == "b"
    untrained = LP.LinUCB(engine.adapter.actions(), ["bias", "kind"])
    assert untrained.updates == 0 and not np.allclose(untrained.b["a"], pol.b["a"])         # weights really changed


def test_training_reads_only_training_episodes_never_sealed_ones(engine):
    for t in engine.adapter.tasks("holdout"):
        for a in engine.adapter.actions():
            engine.run(t["id"], a, purpose="exam")
    pol = LP.train(engine, epochs=2)
    ids = set(pol.train_episodes)
    bad = {e["id"] for e in engine.store.episodes() if e["purpose"] == "exam"}
    assert ids and not (ids & bad)


def test_policy_beats_the_fixed_default_on_sealed_holdout_and_survives_a_restart(engine, tmp_path):
    pol = LP.train(engine, epochs=6, seed=1)
    for t in engine.adapter.tasks("holdout"):
        for a in engine.adapter.actions():
            engine.run(t["id"], a, purpose="exam")
    res = LP.evaluate(engine, pol, engine.adapter.default_action())
    assert res["n_tasks"] == 50 and res["improves"] is True and res["learned_minus_strongest"]["ci95"][0] > 0
    assert res["mean_reward"]["learned"] > res["mean_reward"]["default"]
    LP.save(engine, pol)
    # a restart: a brand-new engine/store object loads the same versioned policy and makes identical choices
    e2 = learn.Engine(Skill(tmp_path / "j.py"), store=Store("synthetic", root=tmp_path / "s"), gaming=lambda: False)
    again = LP.load(e2)
    assert again.version == pol.to_dict()["version"] and again.evaluation["improves"] is True
    for t in e2.adapter.tasks("holdout"):
        assert again.choose(e2.adapter.features(t), explore=False) == pol.choose(e2.adapter.features(t), explore=False)
    d = json.loads(next((e2.store.root / "policies").glob("*.json")).read_text())
    assert d["judge"] == e2.judge_hash() and d["train_episodes"] and d["updates"] > 0


def test_no_improvement_is_reported_when_there_is_none(engine):
    """If every action is equally good the CI must straddle zero: the verdict must not claim an improvement."""
    class Flat(Skill):
        def execute(self, task, action, ctx):
            return {"full": action != "noop", "stages_ok": 2, "fail_count": 0, "total": 10, "cost_s": 1.0}
    j = engine.adapter.judge
    e = learn.Engine(Flat(j), store=Store("flat", root=engine.store.root.parent / "flat"), gaming=lambda: False)
    e.seal_holdout()
    for sp, purpose in (("train", "train"), ("holdout", "exam")):
        for t in e.adapter.tasks(sp):
            for a in e.adapter.actions():
                e.run(t["id"], a, purpose=purpose)
    pol = LP.train(e, epochs=4)
    res = LP.evaluate(e, pol, "a")
    assert res["improves"] is False


def test_bootstrap_ci_is_sane():
    lo, hi = LP.bootstrap_ci([0.5] * 40)
    assert lo == hi == 0.5
    rng = random.Random(1)
    lo, hi = LP.bootstrap_ci([rng.choice([-1, 1]) for _ in range(40)])
    assert lo < 0 < hi
