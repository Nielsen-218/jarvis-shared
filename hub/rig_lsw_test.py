"""Real-Blender test of LSW anatomy support: manifest + sever an arm and a leg, verify independence."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import rigging as R  # noqa: E402
import synth_meshes as SM  # noqa: E402
from mathutils import Vector  # noqa: E402

out = sys.argv[-1]
R.scene_reset()
ob = SM.build("humanoid")
game, rig, rep = R.auto_rig(ob, kind="humanoid", target_faces=40000, name="lsw", ik=False)
man = R.anatomy_manifest([b.name for b in rig.data.bones], "humanoid")
res = {"manifest_limbs": [l["id"] for l in man["limbs"]], "cases": []}
H = float(np.ptp(R.world_verts(game)[:, 2]))
for root in ("upperarm.L", "thigh.R"):
    before_v = len(game.data.vertices)
    r = R.split_limb(game, rig, root)
    case = {"root": root, **{k: v for k, v in r.items() if k != "limb_bones"}}
    if r["ok"]:
        limb, lrig = bpy.data.objects[r["limb_object"]], bpy.data.objects[r["limb_armature"]]
        R._rest_pose(lrig)
        dg = bpy.context.evaluated_depsgraph_get()
        rest = R.world_verts(limb, dg)
        body_rest = R.world_verts(game, dg)
        R._rot_world(lrig, root, Vector((0, 1, 0)), 0.9)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        moved = float(np.linalg.norm(R.world_verts(limb, dg) - rest, axis=1).max() / H)
        body_moved = float(np.linalg.norm(R.world_verts(game, dg) - body_rest, axis=1).max() / H)
        case["limb_moves_alone_over_H"] = round(moved, 3)
        case["body_unaffected_over_H"] = round(body_moved, 4)
        case["pass"] = bool(moved > 0.03 and body_moved < 0.005 and r["limb_verts"] > 50 and len(game.data.vertices) > 0.5 * before_v)
    else:
        case["pass"] = False
    res["cases"].append(case)
json.dump({**res, "manifest": man}, open(out, "w"), indent=2)
for c in res["cases"]:
    print("LSW", c["root"], "PASS" if c["pass"] else "FAIL", c)
