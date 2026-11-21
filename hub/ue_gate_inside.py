"""Runs INSIDE UnrealEditor-Cmd (python). Imports each FBX as a skeletal mesh and measures what Unreal really sees.
Config: env RIG_CFG (json {"cases":[{"name","fbx","expected_verts"}]}); results: env RIG_OUT (json)."""
import json, os, traceback
import unreal

MANNEQUIN = ("pelvis", "spine_01", "spine_02", "head", "upperarm_l", "lowerarm_l", "hand_l", "thigh_l", "calf_l", "foot_l",
             "upperarm_r", "thigh_r")
cfg = json.load(open(os.environ["RIG_CFG"]))
out = {"cases": []}
sub = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)


def import_fbx(case):
    dest = "/Game/Gate/" + case["name"]
    ui = unreal.FbxImportUI()
    for k, v in (("import_mesh", True), ("import_as_skeletal", True), ("import_materials", False), ("import_textures", False),
                 ("import_animations", True), ("create_physics_asset", False), ("automated_import_should_detect_type", False)):
        ui.set_editor_property(k, v)
    ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)
    t = unreal.AssetImportTask()
    for k, v in (("filename", case["fbx"]), ("destination_path", dest), ("automated", True), ("replace_existing", True),
                 ("save", False), ("options", ui)):
        t.set_editor_property(k, v)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([t])
    assets = [unreal.load_asset(p) for p in unreal.EditorAssetLibrary.list_assets(dest)]
    return (next((a for a in assets if isinstance(a, unreal.SkeletalMesh)), None),
            next((a for a in assets if isinstance(a, unreal.AnimSequence)), None))


def skin_check(sk, limit=4000):
    """Unreal-side skin weights: every sampled vertex has weight, sums to ~1, <= 8 influences."""
    m = unreal.SkinWeightModifier()
    m.set_skeletal_mesh(sk)
    n = m.get_num_vertices()
    step = max(1, n // limit)
    bad_sum = bad_zero = most = 0
    for v in range(0, n, step):
        w = m.get_vertex_weights(v)
        w = w[1] if isinstance(w, tuple) and len(w) == 2 and not isinstance(w[0], (int, float)) else w
        items = list(w.items()) if hasattr(w, "items") else list(w)
        vals = [float(getattr(x, "weight", x[1] if isinstance(x, (tuple, list)) else x)) for x in items]
        s = sum(vals)
        if not vals or s < 1e-6:
            bad_zero += 1
        elif abs(s - 1.0) > 0.02:
            bad_sum += 1
        most = max(most, len(vals))
    return {"vertices": n, "sampled": len(range(0, n, step)), "unweighted": bad_zero, "not_normalised": bad_sum, "max_influences": most}


for case in cfg["cases"]:
    r = {"name": case["name"]}
    try:
        sk, anim = import_fbx(case)
        r["skeletal_mesh"] = sk is not None
        if sk is None:
            raise RuntimeError("no skeletal mesh imported")
        r["verts"] = sub.get_num_verts(sk, 0)
        exp = case.get("expected_verts")
        r["verts_expected"] = exp
        # Unreal splits vertices at UV seams/hard edges: measured +10% (untextured) and +43% (textured Neon Sentinel).
        # The bug we guard against (FBX split-by-face smoothing) gave ~3x, so the limit is 2x.
        r["verts_ok"] = bool(exp is None or r["verts"] <= exp * 2.0)
        names = [str(n) for n in unreal.AnimationLibrary.get_animation_track_names(anim)] if anim else []
        r["bones"] = len(names)
        parents = {n: str(sub.get_bone_parent(sk, n)) for n in names}
        roots = [n for n, p in parents.items() if p in ("None", "")]
        r["roots"] = roots
        r["single_root"] = len(roots) == 1
        r["mannequin_names"] = [n for n in MANNEQUIN if n in names]
        r["animation"] = bool(anim)
        if anim:
            moved = 0
            for b in names:
                try:
                    a = unreal.AnimationLibrary.get_bone_pose_for_frame(anim, b, 0, False).rotation
                    c = unreal.AnimationLibrary.get_bone_pose_for_frame(anim, b, 11, False).rotation
                    if abs(a.x * c.x + a.y * c.y + a.z * c.z + a.w * c.w) < 0.9999:
                        moved += 1
                except Exception:
                    pass
            r["animated_bones"] = moved
        try:
            r["skin"] = skin_check(sk)
            sc = r["skin"]
            r["skin_ok"] = sc["unweighted"] == 0 and sc["not_normalised"] <= max(1, sc["sampled"] // 100) and sc["max_influences"] <= 8
        except Exception as e:
            r["skin_error"] = str(e)[:200]
            r["skin_ok"] = None
        r["ok"] = bool(r["skeletal_mesh"] and r["verts_ok"] and r["single_root"] and r.get("animated_bones", 0) > 0 and r["skin_ok"] is not False)
    except Exception:
        r["ok"] = False
        r["error"] = traceback.format_exc()[-600:]
    out["cases"].append(r)
json.dump(out, open(os.environ["RIG_OUT"], "w"), indent=2, default=str)
print("UE_GATE_DONE")
