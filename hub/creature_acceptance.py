"""Headless acceptance run for ANY creature: understand the anatomy, generate the skeleton, rig, test, animate, export.

  blender --background --factory-startup --python hub/creature_acceptance.py -- IN.glb OUT_DIR [--faces N] [--params JSON]
                                                                                 [--overlay FILE] [--variants FILE] [--profile ue5]

Same report shape and stage names as hub/rig_acceptance.py (skin_valid, pose_tests, ..., export, reimport round trip), so
the tools, the dojo and the Unreal gate treat both alike. The JUDGE is generic_pose_tests + run_motion_tests (protected).
Writes multi-view renders (with the skeleton as geometry) for the visual review."""
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import rigging as R  # noqa: E402
import anatomy as A  # noqa: E402
import rig_acceptance as RA  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

arg = RA.arg


def ortho_view(rig, mesh, path, view):
    """Workbench orthographic render with the skeleton drawn as real geometry (works without PIL)."""
    sc = bpy.context.scene
    P = R.world_verts(mesh)
    c, d = (P.min(0) + P.max(0)) / 2, float(np.ptp(P, axis=0).max())
    bones = []
    for b in rig.data.bones:
        h, t = rig.matrix_world @ b.head_local, rig.matrix_world @ b.tail_local
        L = (t - h).length
        if L < 1e-6:
            continue
        cyl = bpy.data.objects.new("jb", bpy.data.meshes.new("jb"))
        import bmesh
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=d * 0.006, radius2=d * 0.002, depth=L)
        bm.to_mesh(cyl.data)
        bm.free()
        cyl.matrix_world = Matrix.Translation((h + t) / 2) @ (t - h).to_track_quat("Z", "Y").to_matrix().to_4x4()
        cyl.color = (1, 0.1, 0.1, 1) if b.use_deform else (0.2, 0.5, 1, 1)
        bpy.context.collection.objects.link(cyl)
        bones.append(cyl)
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.color_type = "OBJECT"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.show_xray = True
    cam = bpy.data.objects.new("ocam", bpy.data.cameras.new("ocam"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = d * 1.2
    bpy.context.collection.objects.link(cam)
    off = {"front": ((0, -d * 3, 0), (math.radians(90), 0, 0)), "side": ((d * 3, 0, 0), (math.radians(90), 0, math.radians(90))),
           "top": ((0, 0, d * 3), (0, 0, 0))}[view]
    cam.location = (c[0] + off[0][0], c[1] + off[0][1], c[2] + off[0][2])
    cam.rotation_euler = off[1]
    bpy.context.view_layer.update()
    prev = sc.camera
    sc.camera = cam
    mesh.color = (0.62, 0.62, 0.66, 1)
    sc.render.engine = "BLENDER_WORKBENCH"                    # RA.render() would switch to EEVEE (no lights here = black)
    sc.render.resolution_x = sc.render.resolution_y = 512
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    sc.camera = prev
    sc.display.shading.show_xray = False
    bpy.data.objects.remove(cam)
    for cyl in bones:
        bpy.data.objects.remove(cyl)
    sc.render.engine = "BLENDER_EEVEE"


def main():
    src, out = sys.argv[sys.argv.index("--") + 1:][:2]
    faces = int(arg("--faces", 40000))
    os.makedirs(out, exist_ok=True)
    rep = {"input": src, "blender": bpy.app.version_string, "stages": {}, "kind": "creature"}
    mesh = RA.import_main(src)
    profile = arg("--profile", "ue5")
    if arg("--params"):
        R.set_params(json.loads(arg("--params")))
    import overlay_guard
    if arg("--overlay"):
        text = open(arg("--overlay"), encoding="utf-8").read()
        okg, why, _ = overlay_guard.check(text)
        if not okg:
            rep["overlay_rejected"] = why
            json.dump(rep, open(os.path.join(out, "report.json"), "w"), indent=2)
            print("OVERLAY_REJECTED", why)
            return
        R.apply_overlay(text)
    variants = []
    if arg("--variants"):
        vs = json.load(open(arg("--variants"), encoding="utf-8"))
        variants = [v for v in vs if overlay_guard.check(v.get("overlay_source") or "def dojo_noop():" + chr(10) + "    pass" + chr(10))[0]
                    and not R.validate_params(v.get("params") or {})]
    t0 = time.time()
    rep["clean"] = R.clean_mesh(mesh)
    anat_src = R.decimate_copy(mesh, 120000, "anat_src")
    strategies = [("default", None)] + [("learned:" + str(v["id"]), v) for v in variants[:2]]
    best = None
    history = []
    for label, var in strategies:
        old_params, undo = None, None
        if var:
            old_params = R.set_params(var.get("params") or {})
            if var.get("overlay_source"):
                undo = R.apply_overlay(var["overlay_source"])
        rig = None
        game = R.decimate_copy(mesh, faces, "game")          # a FRESH mesh per strategy: skinning rewrites the mesh
        try:
            res = A.analyze_anatomy(anat_src)
            rig = R.build_armature(res["spec"], "Rig")
            skin = R.skin(game, rig)
            fv = skin.get("final_validation") or (skin["attempts"][-1].get("validation") or {})
            if fv.get("empty_bones"):                            # bones that control nothing: remove, then judge again
                skin["pruned_empty"] = A.anatomy_prune_empty(rig, game, fv["empty_bones"])
                skin["final_validation"] = R.validate_skin(game, rig)
                skin["ok"] = skin["final_validation"]["ok"]
            poses = A.generic_pose_tests(game, rig) if skin["ok"] else []
            motion = A.run_motion_tests(game, rig, res["plan"]) if skin["ok"] else []
        except Exception as e:                                   # a strategy that crashes is a failed strategy, not a crash
            history.append({"strategy": label, "error": f"{type(e).__name__}: {str(e)[:160]}"})
            bpy.data.objects.remove(game)
            continue
        finally:
            if undo:
                undo()
            if old_params is not None:
                R.restore_params(old_params)
        allt = poses + motion
        passed = sum(t["pass"] for t in allt)
        entry = {"strategy": label, "skin_ok": skin["ok"], "pass": f"{passed}/{len(allt)}", "bones": len(res["spec"])}
        history.append(entry)
        score = (skin["ok"], passed / max(len(allt), 1))
        if best is None or score > best[0]:
            if best is not None:
                bpy.data.objects.remove(best[3])
                bpy.data.objects.remove(best[7])
            best = (score, res, skin, rig, poses, motion, label, game)
        else:
            bpy.data.objects.remove(rig)
            bpy.data.objects.remove(game)
        if skin["ok"] and allt and passed == len(allt):
            break
    if best is None:
        rep["error"] = "no strategy produced a rig"
        rep["pipeline"] = {"verification": history}
        json.dump(rep, open(os.path.join(out, "report.json"), "w"), indent=2, default=str)
        print("ACCEPTANCE", json.dumps({"skin_valid": False}))
        return
    _, res, skin, rig, poses, motion, label, game = best
    plan = res["plan"]
    rep["pipeline"] = {"kind_used": "creature", "bones": len(res["spec"]), "skin": skin, "chosen_strategy": label, "verification": history,
                       "anatomy": {k: v for k, v in plan.items() if k in ("counts", "legs", "wing_parts", "movement_modes", "symmetry", "elongation", "bones", "voxel")},
                       "branches": plan["branches"], "analysis": {"kind": "creature", "faces": len(mesh.data.polygons), "confidence": plan["symmetry"]["score"]},
                       "pruned_bones": [], "retopo": {"method": "collapse_decimate", "faces_in": len(mesh.data.polygons), "faces_out": len(game.data.polygons)}}
    rep["pose_tests"] = poses
    rep["motion_tests"] = motion
    S = rep["stages"]
    S["skin_valid"] = bool(skin["ok"])
    S["pose_tests"] = f"{sum(p['pass'] for p in poses)}/{len(poses)} passed"
    S["motion_tests"] = f"{sum(p['pass'] for p in motion)}/{len(motion)} passed"
    # ---- visual evidence
    for v in ("front", "side", "top"):
        ortho_view(rig, game, os.path.join(out, f"view_{v}.png"), v)
    # ---- animation (the first movement mode), export, re-import
    mode = plan["movement_modes"][0]
    rep["animation"] = A.key_motion_clip(rig, plan, mode)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    rest_before = R.world_verts(game, bpy.context.evaluated_depsgraph_get())
    bpy.context.scene.frame_set(7)
    bpy.context.view_layer.update()
    posed_before = R.world_verts(game, bpy.context.evaluated_depsgraph_get())
    bpy.context.scene.frame_set(1)
    rep["bone_name_map"] = R.apply_profile(rig, [game], profile)
    rep["pre_export"] = {"vertex_groups": len(game.vertex_groups), "modifiers": [m.type for m in game.modifiers], "parent": getattr(game.parent, "name", None),
                         "weighted_verts": int(sum(1 for v in game.data.vertices if len(v.groups) > 0))}
    exports = {}
    for fmt in ("glb", "fbx"):
        try:
            p = R.export_asset([rig, game], os.path.join(out, "char_rigged"), fmt)
            exports[fmt] = {"path": p, "bytes": os.path.getsize(p)}
        except Exception as e:
            exports[fmt] = {"error": str(e)[:200]}
    rep["exports"] = exports
    S["export"] = "PASS" if all("bytes" in v and v["bytes"] > 1000 for v in exports.values()) else "FAIL"
    rt = {}
    try:
        R.scene_reset()
        bpy.ops.import_scene.gltf(filepath=exports["glb"]["path"])
        arm = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
        msh = [o for o in bpy.data.objects if o.type == "MESH"][0]
        rt["bones"], rt["vertex_groups"] = len(arm.data.bones), len(msh.vertex_groups)
        rt["has_action"] = bool(arm.animation_data and arm.animation_data.action)
        bpy.context.scene.frame_set(7)
        bpy.context.view_layer.update()
        Pn = R.world_verts(msh, bpy.context.evaluated_depsgraph_get())
        from mathutils.kdtree import KDTree
        H = float(np.ptp(posed_before, axis=0).max())
        kd = KDTree(len(Pn))
        for i, p in enumerate(Pn):
            kd.insert(p, i)
        kd.balance()
        errs = np.array([kd.find(tuple(p))[2] for p in posed_before[::7]])
        rt["pose_deform_over_H"] = round(float(np.linalg.norm(posed_before - rest_before, axis=1).max() / H), 4)
        rt["verts_reimported"], rt["verts_original"] = len(Pn), len(posed_before)
        rt["max_pos_error_over_H"] = round(float(errs.max() / H), 5)
        rt["pass"] = bool(rt["pose_deform_over_H"] > 0.01 and rt["has_action"] and rt["vertex_groups"] > 0 and rt["max_pos_error_over_H"] < 0.02)
    except Exception as e:
        rt["error"] = str(e)[:300]
        rt["pass"] = False
    rep["reimport"] = rt
    S["reimport_animation_roundtrip"] = "PASS" if rt["pass"] else "FAIL"
    rep["seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(out, "report.json"), "w") as f:
        json.dump(rep, f, indent=2, default=str)
    print("ACCEPTANCE", json.dumps(S))
    print("ANATOMY", json.dumps(rep["pipeline"]["anatomy"]))
    for p in poses + motion:
        if not p["pass"]:
            print("  FAIL", p["test"], "moved", p.get("moved_fraction"), "stray", p.get("stray_fraction"), "torn", p.get("torn_fraction"), "p99", p.get("edge_stretch_p99"))


if __name__ == "__main__":
    main()
