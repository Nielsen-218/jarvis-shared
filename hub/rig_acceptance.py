"""Headless acceptance run for hub/rigging.py. Real Blender, real mesh, no mocks.

  blender --background --factory-startup --python hub/rig_acceptance.py -- IN.glb OUT_DIR [--kind humanoid|quadruped] [--faces N]

Writes OUT_DIR/report.json with every measurement and an explicit PASS/FAIL per stage.
"""
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import rigging as R  # noqa: E402


def arg(flag, default=None):
    a = sys.argv[sys.argv.index("--") + 1:]
    return a[a.index(flag) + 1] if flag in a else default


def import_main(path):
    """Import, keep only the biggest mesh, strip any pre-existing rig so OUR rig is what is tested."""
    R.scene_reset()
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = sorted([o for o in bpy.data.objects if o.type == "MESH"], key=lambda o: -len(o.data.vertices))
    main_ = meshes[0]
    for o in list(bpy.data.objects):
        if o is not main_:
            bpy.data.objects.remove(o)
    for a in list(bpy.data.actions):     # animations that shipped with the file would be exported next to ours
        bpy.data.actions.remove(a)
    for m in list(main_.modifiers):
        main_.modifiers.remove(m)
    main_.vertex_groups.clear()
    main_.parent = None
    return main_


def render(path, w=640, h=640):
    sc = bpy.context.scene
    try:
        sc.render.engine = "BLENDER_EEVEE"
    except TypeError:
        pass
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


def setup_view(rig, mesh):
    P = R.world_verts(mesh)
    c, d = (P.min(0) + P.max(0)) / 2, float(np.ptp(P, axis=0).max())
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    bpy.context.collection.objects.link(cam)
    cam.location = (c[0] + d * 0.9, c[1] - d * 2.2, c[2] + d * 0.3)
    from mathutils import Vector
    cam.rotation_euler = (Vector(c) - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3
    sun.rotation_euler = (0.9, 0.2, 0.6)
    bpy.context.collection.objects.link(sun)
    bpy.context.scene.world = bpy.data.worlds.new("w")
    bpy.context.scene.world.color = (0.3, 0.3, 0.33)


def overlay(rig, mesh, path, side=False):
    """Orthographic mesh render + bones projected to pixels (bones2d JSON, drawn by hub/draw_overlay.py)."""
    from bpy_extras.object_utils import world_to_camera_view
    sc = bpy.context.scene
    P = R.world_verts(mesh)
    c, d = (P.min(0) + P.max(0)) / 2, float(np.ptp(P, axis=0).max())
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.color_type = "MATERIAL"
    cam = bpy.data.objects.new("ocam", bpy.data.cameras.new("ocam"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = d * 1.25
    bpy.context.collection.objects.link(cam)
    if side:
        cam.location = (c[0] + d * 3, c[1], c[2]); cam.rotation_euler = (math.radians(90), 0, math.radians(90))
    else:
        cam.location = (c[0], c[1] - d * 3, c[2]); cam.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.view_layer.update()
    prev = sc.camera
    sc.camera = cam
    render(path)
    w, h = sc.render.resolution_x, sc.render.resolution_y
    lines = []
    for b in rig.data.bones:
        pts = []
        for p in (b.head_local, b.tail_local):
            v = world_to_camera_view(sc, cam, rig.matrix_world @ __import__("mathutils").Vector(p))
            pts.append([round(v.x * w, 1), round((1 - v.y) * h, 1)])
        lines.append({"bone": b.name, "a": pts[0], "b": pts[1], "deform": b.use_deform})
    with open(path + ".bones.json", "w") as f:
        json.dump(lines, f)
    sc.camera = prev
    sc.render.engine = "BLENDER_EEVEE"
    bpy.data.objects.remove(cam)


def main():
    src, out = sys.argv[sys.argv.index("--") + 1:][:2]
    kind = arg("--kind")
    faces = int(arg("--faces", 40000))
    max_faces = int(arg("--max-faces", 0)) or None       # a game budget: caps the finger-density boost
    os.makedirs(out, exist_ok=True)
    rep = {"input": src, "blender": bpy.app.version_string, "stages": {}}
    mesh = import_main(src)
    profile = arg("--profile", "blender")
    # dojo hooks: experiment parameters / a self-written overlay (guard-checked here, inside the sandboxed process) /
    # learned strategies. None of them can reach the judge below (pose tests, validation, export, round trip).
    if arg("--params"):
        R.set_params(json.loads(arg("--params")))
    if arg("--overlay"):
        import overlay_guard
        text = open(arg("--overlay"), encoding="utf-8").read()
        okg, why, _ = overlay_guard.check(text)
        if not okg:
            rep["overlay_rejected"] = why
            json.dump(rep, open(os.path.join(out, "report.json"), "w"), indent=2)
            print("OVERLAY_REJECTED", why)
            return
        R.apply_overlay(text)
    if arg("--variants"):
        import overlay_guard
        vs = json.load(open(arg("--variants"), encoding="utf-8"))
        R.VARIANTS[:] = [v for v in vs if overlay_guard.check(v.get("overlay_source") or "def dojo_noop():" + chr(10) + "    pass" + chr(10))[0]
                         and not R.validate_params(v.get("params") or {})]
    game, rig, r = R.auto_rig(mesh, kind=kind, target_faces=faces, name="char", max_faces=max_faces)
    rep["pipeline"] = r
    kind_used = r["kind_used"]
    S = rep["stages"]
    S["skin_valid"] = bool(r["skin"]["ok"])
    # ---- pose tests (shoulder/elbow/wrist/finger/hip/knee/ankle...)
    poses = R.run_pose_tests(game, rig, kind_used)
    rep["pose_tests"] = poses
    S["pose_tests"] = f"{sum(p['pass'] for p in poses)}/{len(poses)} passed"
    # ---- IK/FK
    ik = R.test_ik(rig, float(np.ptp(R.world_verts(game)[:, 2])), kind_used)
    rep["ik_tests"] = ik
    S["ik_fk"] = f"{sum(i['pass'] for i in ik)}/{len(ik)} passed"
    # ---- visual evidence (rest + one pose)
    setup_view(rig, game)
    render(os.path.join(out, "rest.png"))
    overlay(rig, game, os.path.join(out, "fit_front.png"))
    overlay(rig, game, os.path.join(out, "fit_side.png"), side=True)
    # ---- animation + export + reimport
    R._rest_pose(rig)
    rep["animation"] = R.key_test_animation(rig, kind_used)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    rest_before = R.world_verts(game, bpy.context.evaluated_depsgraph_get())
    bpy.context.scene.frame_set(12)
    bpy.context.view_layer.update()
    posed_before = R.world_verts(game, bpy.context.evaluated_depsgraph_get())
    render(os.path.join(out, "anim_f12.png"))
    names = sorted(b.name for b in rig.data.bones)
    bpy.context.scene.frame_set(1)
    rep["bone_name_map"] = R.apply_profile(rig, [game], profile)   # renaming happens AFTER the measured tests
    exports = {}
    for fmt in ("glb", "fbx"):
        try:
            p = R.export_asset([rig, game], os.path.join(out, "char_rigged"), fmt)
            exports[fmt] = {"path": p, "bytes": os.path.getsize(p)}
        except Exception as e:
            exports[fmt] = {"error": str(e)[:200]}
    rep["exports"] = exports
    S["export"] = "PASS" if all("bytes" in v and v["bytes"] > 1000 for v in exports.values()) else "FAIL"
    # reimport glb in a clean scene, compare skeleton + deformed mesh at frame 12
    rt = {}
    try:
        R.scene_reset()
        bpy.ops.import_scene.gltf(filepath=exports["glb"]["path"])
        arm = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
        msh = [o for o in bpy.data.objects if o.type == "MESH"][0]
        rt["bones"] = len(arm.data.bones)
        rt["bones_expected_deform+ctl"] = len(names)
        rt["vertex_groups"] = len(msh.vertex_groups)
        rt["has_action"] = bool(arm.animation_data and arm.animation_data.action)
        bpy.context.scene.frame_set(12)
        bpy.context.view_layer.update()
        P = R.world_verts(msh, bpy.context.evaluated_depsgraph_get())
        from mathutils.kdtree import KDTree
        H = float(np.ptp(posed_before[:, 2]))
        kd = KDTree(len(P))
        for i, p in enumerate(P):
            kd.insert(p, i)
        kd.balance()
        errs = np.array([kd.find(tuple(p))[2] for p in posed_before[::7]])
        rt["pose_deform_over_H"] = round(float(np.linalg.norm(posed_before - rest_before, axis=1).max() / H), 4)
        rt["verts_reimported"], rt["verts_original"] = len(P), len(posed_before)
        rt["max_pos_error_over_H"] = round(float(errs.max() / H), 5)
        rt["mean_pos_error_over_H"] = round(float(errs.mean() / H), 6)
        rt["pass"] = bool(rt.get("pose_deform_over_H", 0) > 0.01 and rt["has_action"] and rt["vertex_groups"] > 0 and rt.get("max_pos_error_over_H", 1) < 0.02)
    except Exception as e:
        rt["error"] = str(e)[:300]
        rt["pass"] = False
    rep["reimport"] = rt
    S["reimport_animation_roundtrip"] = "PASS" if rt["pass"] else "FAIL"
    with open(os.path.join(out, "report.json"), "w") as f:
        json.dump(rep, f, indent=2, default=str)
    print("ACCEPTANCE", json.dumps(S))
    for p in poses:
        print(("  ok  " if p["pass"] else "  FAIL"), p["test"], "moved", p["moved_fraction"], "stray", p["stray_fraction"],
              "stretch", p["edge_stretch_p99.9"], "compress", p["edge_compress_p0.1"])


if __name__ == "__main__":
    main()
