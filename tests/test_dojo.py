"""The dojo: the guard keeps the judge untouchable, memory survives restarts, the loop keeps only proven improvements."""
import importlib.util
import json
import time
from pathlib import Path

import pytest

from core import dojo
from core.config import ROOT
from core.dojo_store import Store

G = dojo._guard_mod()
PM = dojo._params_mod()


# ------------------------------------------------------------------ the guard
GOOD = '''
import numpy as np

def prune_unsupported(spec, P, H, radius=0.006, min_verts=25):
    return spec, []
'''


def test_guard_accepts_a_geometry_only_overlay():
    ok, why, defined = G.check(GOOD)
    assert ok, why
    assert defined == ["prune_unsupported"]


@pytest.mark.parametrize("src,needle", [
    ("def pose_test(*a):\n    return {'pass': True}\n", "judge"),
    ("def validate_skin(*a):\n    return {'ok': True}\n", "judge"),
    ("def export_asset(*a):\n    pass\n", "judge"),
    ("def humanoid_landmarks(P):\n    return open('x').read()\n", "open"),
    ("import os\ndef humanoid_landmarks(P):\n    return 1\n", "import of os"),
    ("def humanoid_landmarks(P):\n    return P.__class__.__mro__\n", "__"),
    ("def humanoid_landmarks(P):\n    import bpy\n    bpy.ops.wm.save_mainfile()\n    return 1\n", "save_mainfile"),
    ("def humanoid_landmarks(P):\n    eval('1')\n", "eval"),
    ("def not_allowed(P):\n    return 1\n", "not on the replaceable list"),
    ("def humanoid_landmarks(P):\n    while True:\n        pass\n", "while True"),
    ("x = open\n", "only functions"),
    ("def humanoid_landmarks(P):\n    PARAMS['arm_ext_min'] = 9\n", "PARAMS"),
    ("def humanoid_landmarks(P:\n", "syntax"),
])
def test_guard_rejects_unsafe_or_judge_touching_code(src, needle):
    ok, why, _ = G.check(src)
    assert not ok and any(needle in w for w in why), why


def test_guard_allows_new_helpers_and_templates():
    ok, why, _ = G.check("def dojo_arm_tip(P):\n    return P[0]\ndef spec_dragon(L):\n    return []\n")
    assert ok, why


def test_param_validation():
    assert PM.validate_params({"arm_floor_frac": 0.3, "skin_skip": ["direct"]}) is None
    assert "unknown" in PM.validate_params({"pose_threshold": 1})
    assert "outside" in PM.validate_params({"arm_ext_min": 9})
    assert PM.validate_params({"skin_skip": ["nope"]})
    assert PM.validate_params({"arm_floor_frac": None}) is None


# ------------------------------------------------------------------ memory survives restarts
def test_memory_persists_and_recalls(tmp_path):
    s = Store(root=tmp_path / "d")
    s.add_lesson(case="a", signature=["fail:wrist", "stray"], hypothesis="h1", status="refuted", delta="13/14 -> 13/14", change_key="params:x")
    s.add_lesson(case="b", signature=["fail:hip"], hypothesis="h2", status="verified", delta="5/14 -> 9/14", change_key="params:y")
    v = s.add_variant({"arm_floor_frac": 0.3}, overlay_source=GOOD, evidence={"case": "a"})
    s2 = Store(root=tmp_path / "d")                               # a new process, same files
    assert len(s2.lessons()) == 2 and s2.playbook()[0]["id"] == v["id"]
    assert [l["hypothesis"] for l in s2.relevant_lessons(["fail:wrist"])] == ["h1"]
    assert s2.tried("a") == {"params:x"}
    assert v["status"] == "pending_review" and s2.runtime_variants() == []          # JARVIS's own code never runs on real rigs unapproved
    rv = s2.runtime_variants(include_pending=True)                                   # ...but the dojo may keep testing it
    assert rv[0]["params"] == {"arm_floor_frac": 0.3} and "prune_unsupported" in rv[0]["overlay_source"]
    s2.set_variant_status(v["id"], "active", "approved by owner")
    assert s2.runtime_variants()[0]["id"] == v["id"]
    s2.set_variant_status(v["id"], "quarantined", "regressed later")
    assert s2.runtime_variants() == []                            # demoted strategies stop running


def test_kill_switch(tmp_path):
    s = Store(root=tmp_path / "d")
    assert not s.stop_requested()
    s.request_stop()
    assert s.stop_requested()
    s.clear_stop()
    assert not s.stop_requested()


# ------------------------------------------------------------------ the loop (scripted runner + investigator, real logic)
def sc(passed, total=14):
    full = passed == total
    return {"full": full, "stages_ok": 4, "pose": f"{passed}/{total}", "pose_frac": round(passed / total, 4), "fail_count": total - passed, "torn": 0, "stray_excess": 0,
            "failing": [] if full else ["wrist.R"], "key": [int(full), 4, round(passed / total, 4), 0, 0]}


def report_run(passed, total=14):
    return {"score": sc(passed, total), "report": {"stages": {"pose_tests": f"{passed}/{total} passed"}, "pose_tests": [
        {"test": "wrist.R", "pass": False, "moved_fraction": 1, "stray_fraction": 0.036, "edge_stretch_p99": 1.0, "torn_fraction": 0,
         "edge_compress_p0.1": 0.8, "stray_top_bones": [["forearm.R", 670]], "subtree_verts": 99}],
        "pipeline": {"analysis": {"kind": "humanoid", "confidence": 0.8, "faces": 400000}, "landmarks": {"H": 1.8}, "skin": {"attempts": []}}},
        "folder": "x", "seconds": 5.0}


@pytest.fixture
def world(tmp_path, monkeypatch):
    files = []
    for n in ("target", "other"):
        f = tmp_path / f"{n}.glb"
        f.write_bytes(b"x")
        files.append(f)
    cases = [{"name": "target", "kind": "humanoid", "type": "owner_character", "split": "heldout", "resolved": str(files[0])},
             {"name": "other", "kind": "humanoid", "type": "owner_character", "split": "dev", "resolved": str(files[1])}]
    monkeypatch.setattr(dojo, "corpus", lambda: cases)
    store = Store(root=tmp_path / "dojo")
    store.update_case("other", ok=True, last=sc(14), seconds=5)
    return store


def make_runner(effects, breaks_other=()):
    """effects: {json(params): passed_count on target}; production runs (variants) apply the candidate's effect too."""
    calls = []
    def runner(store, case, params=None, overlay=None, variants=None, timeout=0, tag=""):
        calls.append((case["name"], tag, params))
        if case["name"] == "other":
            bad = any(json.dumps(v.get("params"), sort_keys=True) in breaks_other for v in (variants or []))
            return report_run(10 if bad else 14)
        key = json.dumps(params, sort_keys=True) if params else None
        if variants:
            vals = [effects.get(json.dumps(v["params"], sort_keys=True), 13) for v in variants]
            return report_run(max(vals + [13]))
        return report_run(effects.get(key, 13))
    runner.calls = calls
    return runner


def asker_with(batches):
    it = iter(batches)
    def ask(prompt, schema, images=None, temperature=0.4, timeout=0):
        if "short web search query" in prompt:
            return {"query": "blender hanging arms rig"}
        return {"hypotheses": next(it, [])}
    return ask


def hyp(params, cause="the hand joint is too high"):
    return {"cause": cause, "evidence_used": "stray forearm", "kind": "params", "params": params, "expected": "wrist passes"}


def run_session(store, runner, asker, **budget):
    p = dojo.Practice(store, {"research": False, "max_experiments": 10, **budget}, runner=runner, asker=asker, gaming=lambda: False, busy=lambda: None)
    p.start()
    return p.join(30)


def test_loop_keeps_only_proven_improvements_and_remembers_failures(world):
    good = {"arm_floor_frac": 0.3}
    runner = make_runner({json.dumps(good, sort_keys=True): 14})
    asker = asker_with([[hyp({"pose_threshold": 1}), hyp({"arm_ext_min": 1.05}), hyp(good)]])
    out = run_session(world, runner, asker)
    assert out["promoted"] == 1 and out["cases"][0]["passes_now"]
    status = {l["status"] for l in world.lessons()}
    assert {"rejected", "refuted", "verified"} <= status                        # the failures are remembered too
    assert world.runtime_variants()[0]["params"] == good
    assert world.case("target")["ok"] is True and world.case("target")["burned"] is True
    # the regression guard really ran the other (passing) case through the production path
    assert any(c[0] == "other" and c[1] == "reg" for c in runner.calls)


def test_a_fix_that_breaks_another_character_is_not_kept(world):
    bad = {"arm_floor_frac": 0.2}
    runner = make_runner({json.dumps(bad, sort_keys=True): 14}, breaks_other={json.dumps(bad, sort_keys=True)})
    out = run_session(world, runner, asker_with([[hyp(bad)]]))
    assert out["promoted"] == 0 and world.runtime_variants() == []
    assert any(l["status"] == "regressed" and "broke other" in l["delta"] for l in world.lessons())


def test_refuted_ideas_are_not_retried_in_a_later_session(world):
    idea = {"arm_ext_min": 1.05}
    runner = make_runner({})
    run_session(world, runner, asker_with([[hyp(idea)]]))
    n_before = len([c for c in runner.calls if c[1] == "exp"])
    run_session(world, runner, asker_with([[hyp(idea)]]))
    assert len([c for c in runner.calls if c[1] == "exp"]) == n_before          # remembered across sessions: no re-run


def test_a_code_patch_goes_through_the_guard_and_is_sandboxed(world):
    evil = {"cause": "x", "kind": "patch", "function": "pose_test", "source": "def pose_test(*a):\n    return {'pass': True}\n", "expected": "cheat"}
    runner = make_runner({})
    run_session(world, runner, asker_with([[hyp({"arm_ext_min": 1.04})], [evil]]), rounds=2)
    rej = [l for l in world.lessons() if l["status"] == "rejected"]
    assert rej and "judge" in rej[0]["delta"]
    assert not any(c[1] == "exp" and c[2] is None for c in runner.calls)       # the cheating patch never ran


def test_kill_switch_stops_the_session(world):
    runner = make_runner({})
    world.request_stop()
    p = dojo.Practice(world, {"research": False}, runner=runner, asker=asker_with([[hyp({"arm_ext_min": 1.1})]]), gaming=lambda: False, busy=lambda: None)
    p.t0 = time.time()
    assert "stopped by owner" in p._should_stop()


def test_games_and_budgets_stop_practice(world):
    p = dojo.Practice(world, {"max_experiments": 1}, runner=make_runner({}), asker=asker_with([]), gaming=lambda: True, busy=lambda: None)
    p.t0 = time.time()
    assert "game" in p._should_stop()
    p2 = dojo.Practice(world, {"max_experiments": 1}, runner=make_runner({}), asker=asker_with([]), gaming=lambda: False, busy=lambda: None)
    p2.t0, p2.n_exp = time.time(), 1
    assert "budget" in p2._should_stop()


def test_mastery_is_never_claimed_without_fresh_cases(world):
    m = dojo.mastery(world)
    assert "unproven" in m["humanoid"]["label"]


# ------------------------------------------------------------------ the real engine (Blender)
from tools import rigging  # noqa: E402

pytestmark_blender = pytest.mark.skipif(rigging.blender_exe() is None, reason="Blender not installed")
from synth_fixtures import synthetic_meshes  # noqa: F401  (pytest fixture: generates the gitignored synthetic meshes)
SYN = ROOT / "docs" / "rigging" / "synth" / "synthetic_humanoid.glb"


@pytest.fixture
def real_case(tmp_path):
    return Store(root=tmp_path / "d"), {"name": "synthetic_humanoid", "kind": "humanoid", "resolved": str(SYN)}


@pytestmark_blender
@pytest.mark.usefixtures("synthetic_meshes")
def test_real_blender_params_overlay_and_guard(real_case):
    store, case = real_case
    base = dojo.run_case(store, case, tag="base")
    assert base["score"]["full"]
    n_base = len(base["report"]["pipeline"]["pruned_bones"])
    withp = dojo.run_case(store, case, params={"arm_ext_min": 1.1}, tag="p")
    assert withp["score"]["full"]
    # an overlay really changes engine behaviour: it prunes every finger bone (baseline pruned fewer)
    overlay = ("def prune_unsupported(spec, P, H, radius=0.006, min_verts=25):\n"
               "    names = ('thumb', 'index', 'middle', 'ring', 'pinky')\n"
               "    kept = [b for b in spec if not b[0].split('_')[0] in names]\n"
               "    return kept, [b[0] for b in spec if b[0].split('_')[0] in names]\n")
    over = dojo.run_case(store, case, overlay=overlay, tag="o")
    assert len(over["report"]["pipeline"]["pruned_bones"]) == 30 > n_base
    # the judge cannot be replaced: the run refuses before doing anything
    cheat = dojo.run_case(store, case, overlay="def pose_test(*a):\n    return {'pass': True}\n", tag="c")
    assert "overlay rejected" in cheat["error"]
    # a learned strategy as the engine consumes it: default fit passes first, so it is not even needed
    prod = dojo.run_case(store, case, variants=[{"id": "v1", "params": {"arm_ext_min": 1.1}, "overlay_source": None}], tag="v")
    assert prod["score"]["full"]


def test_a_tie_break_is_not_an_improvement_and_old_junk_is_demoted(world):
    a, b = sc(6), sc(6)
    b["key"] = [0, 4, 0.4286, 0, -0.001]                          # same tests passing, fractionally less torn
    assert not dojo.better(b, a) and dojo.better(sc(7), a) and dojo.better(sc(14), sc(13))
    junk = world.add_variant({"crotch_proportion": 0.5}, evidence={"case": "p", "before": "6/14", "after": "6/14"})
    real = world.add_variant({"arm_reg_lo": 1.2}, evidence={"case": "r", "before": "13/14", "after": "14/14"})
    assert dojo.audit_playbook(world) == [junk["id"]]
    assert [v["id"] for v in world.runtime_variants()] == [real["id"]]
    assert any(l["status"] == "demoted" for l in world.lessons())


def test_rejected_parameter_names_are_fed_back_to_the_investigator(world):
    seen = {}
    def ask(prompt, schema, **kw):
        seen["p"] = prompt
        return {"hypotheses": []}
    world.add_lesson(case="x", signature=["fail:hip"], hypothesis="h", status="rejected", delta="unknown parameter torso_half", change_key=None)
    dojo.propose({"failing_tests": [{"test": "hip.L"}], "analysis": {}}, world.lessons(), "params", "", ask)
    assert "torso_half" in seen["p"] and "DO NOT EXIST" in seen["p"]


def test_adding_tests_cannot_fake_an_improvement(world):
    assert not dojo.better(sc(50, 52), sc(49, 51))               # 2 failing before and after: the ratio moved, nothing was fixed
    assert dojo.better(sc(51, 52), sc(49, 51))                   # fewer failing tests is real
    junk = world.add_variant({"anat_simplify": 0.3}, evidence={"case": "s", "before": "49/51", "after": "50/52"})
    assert dojo.audit_playbook(world) == [junk["id"]]


def test_deleting_limbs_to_pass_is_not_an_improvement(world):
    base = sc(64, 69)
    gutted = sc(7, 10)                                            # fewer failing tests, but 85% of the skeleton is gone
    gutted["fail_count"], gutted["total"] = 3, 10
    base["total"] = 69
    assert not dojo.better(gutted, base)
    junk = world.add_variant({"anat_min_branch": 0.25}, evidence={"case": "c", "before": "64/69", "after": "7/10"})
    assert dojo.audit_playbook(world) == [junk["id"]]
