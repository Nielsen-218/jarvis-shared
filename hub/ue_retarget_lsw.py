"""Runs INSIDE UnrealEditor-Cmd (-run=pythonscript) on JARVIS's Living Shadow World worktree (Mark V, 2026-11-09).

Retargets the project's UE mannequin animations onto a JARVIS character with the IK Retargeter (the game's asset pipeline:
"Rig/retarget sample locomotion and attacks using IK Retargeter; validate bone assignments and root bone movement"):
  IK rig for the mannequin source (SKM_Manny_Simple) and for the character, both auto-generated; a retargeter with the default
  op stack, chains auto-mapped, bones auto-aligned; then a batch retarget of the chosen animations into the character's folder.
Everything new lives under /Game/JARVIS/. Each retargeted animation is measured: its skeleton is the character's, it has frames,
and the legs really move between frames.

Config env LSW_RT_CFG = {"dest": "/Game/JARVIS/Characters/<Name>", "mesh": "<skeletal mesh path>", "anims": [...]};
result env LSW_RT_OUT. Log lines LSW_JARVIS_RETARGET_OK / _FAIL.
"""
import json
import os
import traceback

import unreal

cfg = json.load(open(os.environ["LSW_RT_CFG"], encoding="utf-8"))
out = {"ok": False, "steps": []}
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
SOURCE_MESH = "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple"
SOURCE_RIG_DIR = "/Game/JARVIS/Retarget"


def step(msg):
    out["steps"].append(msg)


def ik_rig(path_dir, name, mesh):
    full = f"{path_dir}/{name}"
    if lib.does_asset_exist(full):
        return unreal.load_asset(full)
    rig = unreal.IKRigDefinitionFactory.create_new_ik_rig_asset(path_dir, name)
    ctrl = unreal.IKRigController.get_controller(rig)
    ctrl.set_skeletal_mesh(mesh)
    if not ctrl.apply_auto_generated_retarget_definition():
        raise RuntimeError(f"Unreal could not characterise {mesh.get_name()} for retargeting")
    step(f"{name}: {len(ctrl.get_retarget_chains())} chains, root {ctrl.get_retarget_root()}")
    lib.save_asset(full, only_if_is_dirty=False)
    return rig


def moved(anim, bone):
    n = unreal.AnimationLibrary.get_num_frames(anim)
    if n < 2:
        return False
    a = unreal.AnimationLibrary.get_bone_pose_for_frame(anim, bone, 0, False)
    b = unreal.AnimationLibrary.get_bone_pose_for_frame(anim, bone, max(1, n // 2), False)
    qa, qb = a.rotation, b.rotation
    return abs(qa.x * qb.x + qa.y * qb.y + qa.z * qb.z + qa.w * qb.w) < 0.9995 or (a.translation - b.translation).length() > 0.5


try:
    dest, name = cfg["dest"], cfg["dest"].rsplit("/", 1)[-1]
    target_mesh = unreal.load_asset(cfg["mesh"])
    source_mesh = unreal.load_asset(SOURCE_MESH)
    if not isinstance(target_mesh, unreal.SkeletalMesh) or not isinstance(source_mesh, unreal.SkeletalMesh):
        raise RuntimeError("the character or the mannequin skeletal mesh is missing")
    src_rig = ik_rig(SOURCE_RIG_DIR, "IK_Manny_JARVIS", source_mesh)
    tgt_rig = ik_rig(f"{dest}/Rig", f"IK_{name}", target_mesh)
    rtg_path = f"{dest}/Rig/RTG_Manny_To_{name}"
    if lib.does_asset_exist(rtg_path):
        raise RuntimeError(f"{rtg_path} already exists; JARVIS never overwrites")
    rtg = tools.create_asset(f"RTG_Manny_To_{name}", f"{dest}/Rig", unreal.IKRetargeter, unreal.IKRetargetFactory())
    rc = unreal.IKRetargeterController.get_controller(rtg)
    rc.set_ik_rig(unreal.RetargetSourceOrTarget.SOURCE, src_rig)
    rc.set_ik_rig(unreal.RetargetSourceOrTarget.TARGET, tgt_rig)
    rc.add_default_ops()
    rc.assign_ik_rig_to_all_ops(unreal.RetargetSourceOrTarget.TARGET, tgt_rig)
    rc.auto_map_chains(unreal.AutoMapChainType.FUZZY, True)
    rc.auto_align_all_bones(unreal.RetargetSourceOrTarget.TARGET)
    step(f"retargeter: {rc.get_num_retarget_ops()} ops")
    lib.save_asset(rtg_path, only_if_is_dirty=False)

    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    picked = []
    for path in cfg["anims"]:
        data = reg.get_asset_by_object_path(path + "." + path.rsplit("/", 1)[-1])
        if data and data.is_valid():
            picked.append(data)
        else:
            step(f"missing source animation {path}")
    if not picked:
        raise RuntimeError("none of the source animations exist")
    anims_dir = f"{dest}/Anims"
    # UE 5.8 signature (read from the engine): written straight into the character's folder, referenced assets not copied,
    # nothing overwritten
    made = unreal.IKRetargetBatchOperation.duplicate_and_retarget(
        picked, source_mesh, target_mesh, rtg, search="", replace="", prefix="", suffix=f"_{name}", target_path=anims_dir,
        use_source_path=False, include_referenced_assets=False, overwrite_existing_files=False)
    results = []
    target_skel = target_mesh.get_editor_property("skeleton")
    for data in made:
        p = str(data.package_name)
        anim = unreal.load_asset(p)
        if not isinstance(anim, unreal.AnimSequence):
            continue
        new_path = f"{anims_dir}/{p.rsplit('/', 1)[-1]}"
        if not p.startswith(anims_dir):
            if not lib.rename_asset(p, new_path):
                raise RuntimeError(f"could not move {p} into {anims_dir}")
            anim = unreal.load_asset(new_path)
        lib.save_asset(new_path, only_if_is_dirty=False)
        skel = anim.get_editor_property("skeleton")
        results.append({"anim": new_path, "frames": unreal.AnimationLibrary.get_num_frames(anim),
                        "skeleton_ok": bool(skel and target_skel and skel.get_path_name() == target_skel.get_path_name()),
                        "legs_move": bool(moved(anim, "thigh_l") or moved(anim, "calf_l")),
                        "arms_move": bool(moved(anim, "upperarm_l") or moved(anim, "lowerarm_l"))})
    out["anims"] = results
    out["ok"] = bool(results) and all(r["skeleton_ok"] and r["frames"] > 1 for r in results) and \
        any(r["legs_move"] for r in results)
    unreal.log_warning("LSW_JARVIS_RETARGET_" + ("OK " if out["ok"] else "FAIL ") + json.dumps(
        {"anims": len(results), "legs_move": sum(r["legs_move"] for r in results)}))
except Exception:
    out["error"] = traceback.format_exc()[-1200:]
    unreal.log_warning("LSW_JARVIS_RETARGET_FAIL " + out["error"].splitlines()[-1][:200])
json.dump(out, open(os.environ["LSW_RT_OUT"], "w", encoding="utf-8"), indent=2, default=str)
