"""Runs INSIDE UnrealEditor-Cmd (-run=pythonscript) on JARVIS's own Living Shadow World worktree (Mark V, 2026-11-09).

Mode "import": imports one rigged FBX as a skeletal mesh into a NEW folder under /Game/JARVIS/Characters/<Name> and saves it.
    Refuses if that folder already has assets (never overwrites). No materials-only or animation-only imports; the rig's
    own test animation is not imported (it is a pipeline check, not game content).
Mode "verify": a FRESH process loads what is on disk under that folder and measures it (proves the import was saved).

Config: env LSW_IMPORT_CFG = json {"mode", "fbx", "dest"}; result: env LSW_IMPORT_OUT (json). Prints LSW_JARVIS_* lines
(the team's log convention: LSW_ lines decide pass/fail).
"""
import json
import os
import traceback

import unreal

cfg = json.load(open(os.environ["LSW_IMPORT_CFG"], encoding="utf-8"))
out = {"mode": cfg["mode"], "dest": cfg["dest"], "ok": False}
dest = cfg["dest"]
lib = unreal.EditorAssetLibrary


def measure():
    paths = [str(p) for p in lib.list_assets(dest, recursive=True, include_folder=False)]
    assets = [unreal.load_asset(p) for p in paths]
    meshes = [a for a in assets if isinstance(a, unreal.SkeletalMesh)]
    out["assets"] = sorted(p.split(".")[0] for p in paths)
    out["kinds"] = sorted({type(a).__name__ for a in assets if a is not None})
    if len(meshes) != 1:
        raise RuntimeError(f"expected exactly one skeletal mesh under {dest}, found {len(meshes)}")
    sk = meshes[0]
    sub = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
    out["mesh"] = sk.get_path_name()
    out["verts"] = sub.get_num_verts(sk, 0)
    out["lods"] = sub.get_lod_count(sk)
    comp = unreal.SkeletalMeshComponent()
    comp.set_skeletal_mesh_asset(sk)
    out["bones"] = comp.get_num_bones()
    names = [str(comp.get_bone_name(i)) for i in range(out["bones"])]
    out["root"] = names[0] if names else ""
    out["has_pelvis"] = "pelvis" in names
    skel = sk.get_editor_property("skeleton")
    out["skeleton"] = skel.get_path_name() if skel else ""
    # Textured or grey? A material instance with its colour map bound (Mark V: UE 5.8's Interchange FBX importer could not
    # read embedded FBX textures and imported a grey material while saying nothing in Python).
    mats = [a for a in assets if isinstance(a, unreal.MaterialInstanceConstant)]
    out["materials"] = [m.get_path_name() for m in mats]
    bound = {}
    for m in mats:
        for slot in ("DiffuseColorMap", "NormalMap"):
            try:
                tex = unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(m, slot)
            except Exception:
                tex = None
            if tex:
                bound[slot] = tex.get_path_name()
    out["textures_bound"] = bound
    out["textured"] = "DiffuseColorMap" in bound


try:
    if cfg["mode"] == "import":
        if lib.does_directory_exist(dest) and lib.list_assets(dest, recursive=True, include_folder=False):
            raise RuntimeError(f"{dest} already has assets; JARVIS never overwrites")
        ui = unreal.FbxImportUI()
        for k, v in (("import_mesh", True), ("import_as_skeletal", True), ("import_materials", True), ("import_textures", True),
                     ("import_animations", False), ("create_physics_asset", False), ("automated_import_should_detect_type", False)):
            ui.set_editor_property(k, v)
        ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)
        task = unreal.AssetImportTask()
        # UE naming (TEAM.md premium bar): SK_<Name>; Unreal names the skeleton SK_<Name>_Skeleton after it
        for k, v in (("filename", cfg["fbx"]), ("destination_path", dest), ("destination_name", "SK_" + dest.rsplit("/", 1)[-1]),
                     ("automated", True), ("replace_existing", False), ("save", True), ("options", ui)):
            task.set_editor_property(k, v)
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        out["imported"] = [str(p) for p in task.get_editor_property("imported_object_paths")]
        if not out["imported"]:
            raise RuntimeError("Unreal imported nothing (see LogFbx / LogInterchange in the log)")
        name = dest.rsplit("/", 1)[-1]
        mats = [unreal.load_asset(p) for p in out["imported"]]
        mats = [m for m in mats if isinstance(m, unreal.MaterialInstanceConstant)]
        for m in mats[:1]:                                         # UE naming: MI_<Name>
            new = f"{dest}/MI_{name}"
            if lib.rename_asset(m.get_path_name().split(".")[0], new):
                out["material"] = new
        # The textures come in as separate image files (extracted from the rig's GLB by the host), then go into the slots.
        for t in cfg.get("textures") or []:
            ti = unreal.AssetImportTask()
            for k, v in (("filename", t["file"]), ("destination_path", dest), ("destination_name", t["asset"]), ("automated", True),
                         ("replace_existing", False), ("save", False)):
                ti.set_editor_property(k, v)
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([ti])
            tex = unreal.load_asset(f"{dest}/{t['asset']}")
            if not isinstance(tex, unreal.Texture2D):
                raise RuntimeError(f"the texture {t['asset']} did not import")
            if t["kind"] == "normal":
                tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
                tex.set_editor_property("srgb", False)
            mi = unreal.load_asset(out.get("material") or "")
            if isinstance(mi, unreal.MaterialInstanceConstant):
                unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(mi, t["slot"], tex)
                unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(mi, t["slot"] + "Weight", 1.0)
                unreal.MaterialEditingLibrary.update_material_instance(mi)
        lib.save_directory(dest, only_if_is_dirty=False, recursive=True)
    measure()
    out["ok"] = True
    unreal.log_warning("LSW_JARVIS_" + cfg["mode"].upper() + "_OK " + json.dumps({k: out.get(k) for k in ("verts", "bones", "lods", "textured")}))
except Exception:
    out["error"] = traceback.format_exc()[-800:]
    unreal.log_warning("LSW_JARVIS_" + cfg["mode"].upper() + "_FAIL " + out["error"].splitlines()[-1][:200])
json.dump(out, open(os.environ["LSW_IMPORT_OUT"], "w", encoding="utf-8"), indent=2, default=str)
