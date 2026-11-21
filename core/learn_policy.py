"""Reinforcement learning for the learning core: a LEARNED, VERSIONED policy that chooses which strategy to try on a task.

This is genuine RL in the contextual-bandit sense, not "remembering what worked":
  state       the adapter's feature vector for the task (observable BEFORE acting)
  action      one of the adapter's permitted strategies
  reward      computed from the independent judge's verdict (never self-reported): full pass, partial progress, time cost, penalties
  transition  one-step episodes (a bandit); `Policy.update` already takes next_state/done so a sequential method can replace LinUCB
  policy      LinUCB (disjoint linear models, numpy): a weight matrix per action that is UPDATED from the rewards of the action it chose
  evaluation  the greedy learned policy against baselines on SEALED held-out tasks with a paired bootstrap (see `evaluate`)
Policies are JSON files with a content hash, a parent version, the exact training episode ids and the judge hash; they load identically
after a restart. Nothing here changes any model's weights."""
import hashlib
import json
import random
import time

import numpy as np

REWARD = {"full": 1.0, "partial": 0.4, "time_scale_s": 10.0, "time_weight": 0.3, "error_penalty": 0.2, "shrink_penalty": 1.0}


def reward(outcome, ref_total=None):
    """Reward from the judge's outcome. Fixed constants, written before any result was seen; the judge is immutable.
    +1 for a full pass, up to +0.4 for fewer failures; -0.3 at most for elapsed time; -0.2 for an invalid/errored action that failed;
    -1 for 'passing' by shrinking what is tested (the deletion hack)."""
    if ref_total and outcome["total"] < 0.8 * ref_total:
        return -REWARD["shrink_penalty"]
    r = REWARD["full"] if outcome["full"] else REWARD["partial"] * (1 - outcome["fail_count"] / max(outcome["total"], 1))
    r -= REWARD["time_weight"] * min(outcome.get("cost_s", 0.0) / REWARD["time_scale_s"], 1.0)
    if outcome.get("error") and not outcome["full"]:
        r -= REWARD["error_penalty"]
    return round(r, 5)


class Policy:
    """Interface so a sequential RL method can replace the bandit without touching the engine."""
    def choose(self, x, explore=True):
        raise NotImplementedError

    def update(self, x, action, r, next_state=None, done=True):
        raise NotImplementedError


class LinUCB(Policy):
    def __init__(self, actions, feature_names, alpha=0.4, lam=1.0, parent=None):
        self.actions, self.names, self.alpha, self.lam, self.parent = list(actions), list(feature_names), alpha, lam, parent
        d = len(self.names)
        self.A = {a: lam * np.eye(d) for a in self.actions}
        self.b = {a: np.zeros(d) for a in self.actions}
        self.n = {a: 0 for a in self.actions}
        self.updates = 0
        self.train_episodes = []
        self.judge = None
        self.evaluation = None

    def vec(self, feats):
        return np.array([float(feats.get(n, 0.0)) for n in self.names])

    def scores(self, feats, explore=True):
        x = self.vec(feats)
        out = {}
        for a in self.actions:
            Ainv = np.linalg.inv(self.A[a])
            out[a] = float(Ainv.dot(self.b[a]).dot(x) + (self.alpha * np.sqrt(x.dot(Ainv).dot(x)) if explore else 0.0))
        return out

    def choose(self, feats, explore=True):
        s = self.scores(feats, explore)
        best = max(s.values())
        return next(a for a in self.actions if s[a] == best)         # ties: the adapter's action order (deterministic)

    def update(self, feats, action, r, next_state=None, done=True):
        x = self.vec(feats)
        self.A[action] += np.outer(x, x)
        self.b[action] += r * x
        self.n[action] += 1
        self.updates += 1

    # ---- persistence (versioned, content-hashed)
    def to_dict(self):
        body = {"algo": "linucb", "alpha": self.alpha, "lam": self.lam, "actions": self.actions, "features": self.names,
                "A": {a: self.A[a].tolist() for a in self.actions}, "b": {a: self.b[a].tolist() for a in self.actions},
                "n": self.n, "updates": self.updates, "train_episodes": self.train_episodes, "judge": self.judge, "parent": self.parent}
        h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:10]
        return {"version": f"lin{self.updates:05d}-{h}", "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "evaluation": self.evaluation, **body}

    @classmethod
    def from_dict(cls, d):
        p = cls(d["actions"], d["features"], d["alpha"], d["lam"], parent=d.get("parent"))
        p.A = {a: np.array(d["A"][a]) for a in p.actions}
        p.b = {a: np.array(d["b"][a]) for a in p.actions}
        p.n, p.updates, p.train_episodes, p.judge, p.evaluation = d["n"], d["updates"], d["train_episodes"], d.get("judge"), d.get("evaluation")
        p.version = d["version"]
        return p


def _lookup(episodes, purpose_ok):
    """(task, action) -> the latest episode, among the allowed purposes."""
    out = {}
    for e in episodes:
        if purpose_ok(e):
            out[(e["task"], e["action"])] = e
    return out


def train(engine, epochs=8, seed=0, alpha=0.4, parent=None):
    """Learn from the stored TRAINING episodes of the current judge. Each step the policy picks an action for a task and is told ONLY that
    action's judged reward (bandit feedback); the other actions' results stay hidden from it. Sealed/exam rows are never read."""
    adapter, store = engine.adapter, engine.store
    rows = engine.training_episodes()                                  # train/drill, not sealed, current judge
    table = _lookup(rows, lambda e: True)
    tasks = sorted({t for t, _ in table})
    if not tasks:
        raise RuntimeError("no training episodes under the current judge")
    feats = {t["id"]: adapter.features(t) for t in adapter.tasks("train") if t["id"] in tasks}
    acts = [a for a in adapter.actions() if any(k[1] == a for k in table)]          # only strategies that were actually tried in training
    pol = LinUCB(acts, list(next(iter(feats.values()))), alpha=alpha, parent=parent)
    pol.judge = engine.judge_hash()
    noop_total = {t: table[(t, a)]["outcome"]["total"] for (t, a) in table if a == "noop"}
    rng = random.Random(seed)
    used = []
    for _ in range(epochs):
        order = tasks[:]
        rng.shuffle(order)
        for t in order:
            a = pol.choose(feats[t], explore=True)
            ep = table.get((t, a))
            if ep is None:
                continue                                                # that action was never judged on this task: no feedback
            engine.assert_trainable(t)
            pol.update(feats[t], a, reward(ep["outcome"], noop_total.get(t)))
            used.append(ep["id"])
    pol.train_episodes = sorted(set(used))
    return pol


def save(engine, pol):
    d = pol.to_dict()
    engine.store.save_policy(d)
    return d["version"]


def load(engine, version=None):
    d = engine.store.load_policy(version)
    return LinUCB.from_dict(d) if d else None


# ------------------------------------------------------------------ evaluation on sealed held-out tasks
def bootstrap_ci(diffs, n=10000, seed=1):
    rng = np.random.default_rng(seed)
    d = np.array(diffs, float)
    means = rng.choice(d, size=(n, len(d)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def evaluate(engine, pol, default_action, record=True):
    """Compare on the SEALED held-out tasks (exam episodes of every action must already exist): the greedy learned policy against
      default       the fixed action declared before training
      best_single   the single action with the best mean reward on the TRAINING tasks (chosen without touching the holdout)
      random        the mean over all actions
      oracle        best action per task (an upper bound, not a baseline)
    Verdict: the learned policy 'improves' only if the paired-bootstrap 95% CI of (learned - strongest baseline) is above zero."""
    adapter, store = engine.adapter, engine.store
    head = engine.judge_hash()
    ex = _lookup(store.episodes(), lambda e: e["purpose"] == "exam" and e["judge"] == head and not e["assisted"])
    tr = _lookup(engine.training_episodes(), lambda e: True)
    acts = list(pol.actions)
    hold = [t for t in adapter.tasks("holdout") if all((t["id"], a) in ex for a in acts)]
    if len(hold) < len(adapter.tasks("holdout")):
        raise RuntimeError(f"exam episodes missing for {len(adapter.tasks('holdout')) - len(hold)} sealed tasks")
    noop_total = {t["id"]: ex[(t["id"], "noop")]["outcome"]["total"] for t in hold if (t["id"], "noop") in ex}
    R = {(t["id"], a): reward(ex[(t["id"], a)]["outcome"], noop_total.get(t["id"])) for t in hold for a in acts}
    train_mean = {}
    for a in acts:
        vals = [reward(e["outcome"]) for (t, act), e in tr.items() if act == a]
        train_mean[a] = float(np.mean(vals)) if vals else -9
    best_single = max(train_mean, key=train_mean.get)
    choice = {t["id"]: pol.choose(adapter.features(t), explore=False) for t in hold}
    per = {
        "learned": [R[(t["id"], choice[t["id"]])] for t in hold],
        "default": [R[(t["id"], default_action)] for t in hold],
        "best_single": [R[(t["id"], best_single)] for t in hold],
        "random": [float(np.mean([R[(t["id"], a)] for a in acts])) for t in hold],
        "oracle": [max(R[(t["id"], a)] for a in acts) for t in hold]}
    means = {k: round(float(np.mean(v)), 4) for k, v in per.items()}
    strongest = max(("default", "best_single", "random"), key=lambda k: means[k])
    diffs = [l - b for l, b in zip(per["learned"], per[strongest])]
    lo, hi = bootstrap_ci(diffs)
    hind = max(acts, key=lambda a: sum(R[(t["id"], a)] for t in hold))                # NOT a baseline (it peeks at the holdout): reported for honesty
    hind_diffs = [l - R[(t["id"], hind)] for l, t in zip(per["learned"], hold)]
    hlo, hhi = bootstrap_ci(hind_diffs)
    full = {k: sum(1 for t in hold if ex[(t["id"], choice[t["id"]] if k == "learned" else (default_action if k == "default" else best_single))]["outcome"]["full"]) for k in ("learned", "default", "best_single")}
    result = {"policy": getattr(pol, "version", None) or pol.to_dict()["version"], "n_tasks": len(hold), "judge": head, "mean_reward": means,
              "best_single_action": best_single, "default_action": default_action, "strongest_baseline": strongest, "full_passes": full,
              "learned_minus_strongest": {"mean": round(float(np.mean(diffs)), 4), "ci95": [round(lo, 4), round(hi, 4)]},
              "hindsight_best_single": {"action": hind, "mean_reward": round(float(np.mean([R[(t["id"], hind)] for t in hold])), 4),
                                        "learned_minus_it": {"mean": round(float(np.mean(hind_diffs)), 4), "ci95": [round(hlo, 4), round(hhi, 4)]},
                                        "note": "the best fixed action chosen AFTER seeing the holdout; not a valid baseline, shown so the result is not oversold"},
              "improves": bool(lo > 0 and len(hold) >= 40), "choices": {a: sum(1 for c in choice.values() if c == a) for a in acts},
              "note": "improves requires >=40 sealed tasks and a 95% CI above zero against the strongest baseline"}
    if record:
        pol.evaluation = result
        store.event("policy_evaluation", **result)
    return result
