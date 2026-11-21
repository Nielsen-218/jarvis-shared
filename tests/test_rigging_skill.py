"""JARVIS rigging skill: pure logic + a REAL Blender run (skipped if Blender is absent)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))
import rigging as R  # noqa: E402

from tools import ALL, registry, rigging as T  # noqa: E402


def test_skills_registered():
    assert {"rigging", "pcops"} <= set(ALL)
    names = {t["name"] for t in registry.schemas()} if hasattr(registry, "schemas") else set()
    assert not names or {"rig_character", "image_to_character", "rigging_skills", "rigging_lessons", "observe_pc"} <= names


def test_anatomy_manifest_humanoid_and_quadruped():
    h = ["root", "pelvis", "spine_01", "spine_02", "spine_03", "neck", "head", "clavicle.L", "upperarm.L", "forearm.L",
         "hand.L", "index_01.L", "thigh.L", "calf.L", "foot.L", "toe.L", "thigh.R", "calf.R", "foot.R"]
    m = R.anatomy_manifest(h)
    ids = {l["id"] for l in m["limbs"]}
    assert ids == {"arm.L", "leg.L", "leg.R"}
    arm = next(l for l in m["limbs"] if l["id"] == "arm.L")
    assert [j["joint"] for j in arm["sever_joints"]] == ["shoulder", "elbow", "wrist"] and "index_01.L" in arm["bones"]
    assert all(l["independent_function_after_sever"] and l["regeneration"]["stump_bone"] for l in m["limbs"])
    q = R.anatomy_manifest(["root", "pelvis", "fore_shoulder.L", "fore_upperleg.L", "fore_lowerleg.L", "tail_01", "tail_02"], "quadruped")
    assert {"foreleg.L", "tail"} <= {l["id"] for l in q["limbs"]}


def test_stage_ok_requires_all_passing():
    assert T._stage_ok("pose_tests", "14/14 passed") and not T._stage_ok("pose_tests", "13/14 passed")
    assert not T._stage_ok("pose_tests", "0/0 passed") and T._stage_ok("export", "PASS") and not T._stage_ok("export", "FAIL")


def test_ledger_and_status_never_claim_without_evidence(tmp_path, monkeypatch):
    store = {}
    monkeypatch.setattr(T.storage, "load_global", lambda n, d: store.get(n, d))
    monkeypatch.setattr(T.storage, "save_global", lambda n, v: store.__setitem__(n, v))
    assert all(v["state"] == "untested" for k, v in T.skills_status().items() if k != "note")
    ok = {"pipeline": {"kind_used": "humanoid", "bones": 20, "skin": {"final_method": "armature_auto_via_voxel_weld_proxy"},
                       "pruned_bones": ["index_01.L"], "analysis": {"faces": 740000}},
          "stages": {"skin_valid": True, "pose_tests": "14/14 passed", "ik_fk": "4/4 passed", "export": "PASS",
                     "reimport_animation_roundtrip": "FAIL"}, "pose_tests": [{"test": "elbow.L"}]}
    T.record_run(ok, "x.glb", tmp_path, False)
    s = T.skills_status()
    assert s["skin_valid"]["state"] == "verified" and s["reimport_animation_roundtrip"]["state"] == "failed"
    assert s["fingers"]["state"] == "untested"            # no finger test ran
    assert not any(v.get("state") == "mastered" for v in s.values() if isinstance(v, dict))
    text = " ".join(l["text"] for l in store[T.LEDGER]["lessons"])
    assert "weld" in text and "740000" in text and "pruned" in text


@pytest.mark.real_jobs                                      # calls the real function, but only its input validation: it returns before any work starts
def test_rig_character_rejects_bad_input(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "_allowed_roots", lambda: [Path("C:/definitely-not-here")])
    assert "existing .glb" in T.rig_character(str(tmp_path / "nope.glb"))
    f = tmp_path / "m.glb"
    f.write_bytes(b"x")
    assert "outside the folders" in T.rig_character(str(f))


@pytest.mark.skipif(T.blender_exe() is None, reason="Blender not installed")
def test_real_blender_rig_of_synthetic_humanoid(tmp_path):
    import subprocess
    exe = T.blender_exe()
    glb = tmp_path / "h.glb"
    subprocess.run([str(exe), "-b", "--factory-startup", "--python", str(ROOT / "hub" / "synth_meshes.py"), "--", "humanoid", str(glb)],
                   check=True, capture_output=True, timeout=300)
    out = tmp_path / "out"
    subprocess.run([str(exe), "-b", "--factory-startup", "--python", str(ROOT / "hub" / "rig_acceptance.py"), "--", str(glb), str(out)],
                   check=True, capture_output=True, timeout=600)
    rep = json.loads((out / "report.json").read_text())
    assert all(T._stage_ok(k, v) for k, v in rep["stages"].items()), rep["stages"]
    assert (out / "char_rigged.glb").stat().st_size > 1000 and (out / "char_rigged.fbx").stat().st_size > 1000
    assert rep["reimport"]["pose_deform_over_H"] > 0.01          # animation is non-trivial


@pytest.mark.skipif(T.blender_exe() is None or not (ROOT / "hub" / "ue_check.py").exists(), reason="Blender not installed")
def test_real_unreal_import_gate(tmp_path):
    """ue5 profile output must import into real headless Unreal with one root bone, no vertex tripling, good skin."""
    import importlib.util, subprocess
    spec = importlib.util.spec_from_file_location("ue_check", ROOT / "hub" / "ue_check.py")
    ue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ue)
    if ue.ue_cmd() is None:
        pytest.skip("Unreal not installed")
    exe = T.blender_exe()
    glb = tmp_path / "h.glb"
    subprocess.run([str(exe), "-b", "--factory-startup", "--python", str(ROOT / "hub" / "synth_meshes.py"), "--", "humanoid", str(glb)],
                   check=True, capture_output=True, timeout=300)
    out = tmp_path / "out"
    subprocess.run([str(exe), "-b", "--factory-startup", "--python", str(ROOT / "hub" / "rig_acceptance.py"), "--", str(glb), str(out), "--profile", "ue5"],
                   check=True, capture_output=True, timeout=600)
    rep = json.loads((out / "report.json").read_text())
    g = T._unreal_gate(out, rep)
    if g and g.get("skipped"):
        pytest.skip(f"the Mark V guard declined the headless Unreal run: {g['skipped']}")
    assert g and g["ok"], g
    assert g["single_root"] and g["skin_ok"] and g["verts"] <= g["verts_expected"] * 2.0


def test_generated_models_carry_the_licence_note():
    base = {"ok": True, "kind": "humanoid", "bones": 20, "skin_method": "armature_auto_direct", "output_folder": "C:/x/y",
            "stages": {"pose_tests": "14/14 passed", "ik_fk": "4/4 passed"}, "failing_pose_tests": []}
    assert "Licence note" not in T._speak(base)                              # the owner's own file: no note
    spoken = T._speak({**base, "generated_shape": "C:/x/shape.glb", "license_note": T.HUNYUAN_NOTE})
    assert "EU, UK or South Korea" in spoken and "provenance.json" in spoken


def test_pruned_finger_bones_are_reported_not_silent():
    d = {"ok": True, "kind": "humanoid", "bones": 40, "skin_method": "armature_auto_direct", "output_folder": "C:/x/y",
         "stages": {"pose_tests": "16/16 passed", "ik_fk": "4/4 passed"}, "pruned_bones": ["finger_index.L", "finger_index.R"]}
    assert "removed 2 finger or toe bones" in T._speak(d)


def test_model_is_offered_one_rigging_front_door():
    from core import intelligence
    assert {"rig_character", "image_to_character"} <= intelligence._INTERNAL_CRAFT_TOOLS
    assert "rig_my_character" not in intelligence._INTERNAL_CRAFT_TOOLS
