"""Deliberate-failure recovery test (real Blender). Introduces two defects and checks that the pipeline
DETECTS each one and repairs it, rather than shipping a broken skin.

  blender -b --factory-startup --python hub/rig_recovery_test.py -- SYNTH_DIR OUT.json
Defects:
  A  disconnected overlapping shells (no voxel weld): heat-diffusion skinning cannot solve
  B  wrong landmark override (crotch+legs misplaced): bones fall outside the mesh
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import rigging as R  # noqa: E402
import synth_meshes as SM  # noqa: E402


def run(label, weld, overrides=None):
    R.scene_reset()
    ob = SM.build("humanoid", weld=weld)
    game, rig, rep = R.auto_rig(ob, kind="humanoid", target_faces=40000, name="rec", ik=False, overrides=overrides)
    att = rep["skin"]["attempts"]
    poses = R.run_pose_tests(game, rig, "humanoid") if rep["skin"]["ok"] else []
    return {"case": label, "attempts": [(a["method"], a["validation"].get("ok"), a["error"]) for a in att],
            "final_method": rep["skin"]["final_method"], "skin_ok": rep["skin"]["ok"],
            "pose_tests": f"{sum(p['pass'] for p in poses)}/{len(poses)}",
            "failing": [p["test"] for p in poses if not p["pass"]],
            "verification": rep.get("verification"), "chosen_strategy": rep.get("chosen_strategy"),
            "detected_defect": any(not a["validation"].get("ok", False) for a in att[:-1]) or not att[0]["validation"].get("ok", False) or len(rep.get("verification", [])) > 1}


def main():
    out = sys.argv[-1]
    res = [run("control_welded_clean", True),
           run("A_disconnected_shells", False),
           run("B_bad_landmarks", True, {"crotch_z": 1.2, "leg_off": 0.5})]
    json.dump(res, open(out, "w"), indent=2)
    for r in res:
        print("RECOVERY", r["case"], "| defect detected:", r["detected_defect"], "| final:", r["final_method"],
              "| skin_ok:", r["skin_ok"], "| poses:", r["pose_tests"], r["failing"])


main()
