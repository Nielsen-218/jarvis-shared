"""Static safety check for code JARVIS writes for himself (an 'overlay' of replacement functions for hub/rigging.py).

Principles:
* The JUDGE is untouchable: pose tests, skin validation, IK tests, export, round trip, profile, animation are protected,
  so an experiment can never improve its score by changing how it is measured.
* Overlays compute geometry only: no files, no network, no processes, no eval/exec, no dunder tricks, no Blender operators
  that load/save/export/run scripts. They cannot write reports either (no open()).
* Only allow-listed functions may be replaced, plus new helpers/templates named dojo_* / spec_*.
Pure Python (ast): runs outside Blender, unit-tested."""
import ast

ALLOWED_REPLACE = {
    "humanoid_landmarks", "humanoid_spec", "quadruped_spec", "prune_unsupported", "skin_nearest_segment", "clean_weights",
    "decimate_copy", "_slice_clusters", "analyze", "clean_mesh", "build_armature", "transfer_weights",
    # anatomy understanding / skeleton generation (hub/anatomy.py): the ideas JARVIS may improve
    "anatomy_voxelize", "anatomy_thickness", "anatomy_center_path", "anatomy_extract_branches", "anatomy_simplify",
    "anatomy_build_tree", "anatomy_symmetry", "anatomy_classify", "anatomy_build_spec", "anatomy_sheets", "anatomy_prune_empty", "anatomy_refine_roles",
}
PROTECTED = {
    "pose_test", "run_pose_tests", "_run_pose_tests", "test_ik", "export_asset", "apply_profile", "key_test_animation",
    "validate_skin", "weight_matrix", "scene_reset", "auto_rig", "_auto_rig_once", "skin", "set_params", "restore_params",
    "apply_overlay", "validate_params", "P", "P_", "world_verts", "mesh_edges", "_rest_pose", "_rot_world", "_tail_world",
    "_descendants", "add_ik", "ue_name", "anatomy_manifest", "split_limb",
    # the judge for arbitrary skeletons and the whole-pipeline entry points
    "generic_pose_tests", "run_motion_tests", "key_motion_clip", "_gait_assignments", "_chains", "analyze_anatomy", "_slab_flatness",
    "_paint", "_dilate6", "_erode6", "anatomy_geodesic",
}
NEW_PREFIXES = ("dojo_", "spec_")
IMPORT_OK = {"numpy", "math", "mathutils", "bmesh", "bpy", "collections", "itertools", "functools", "heapq"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
                "builtins", "urllib", "requests", "ctypes", "pickle", "marshal", "PARAMS", "PARAM_SPEC", "R"}
BANNED_ATTRS = {"save_mainfile", "save_as_mainfile", "open_mainfile", "system", "popen", "remove", "unlink", "rmtree",
                "write", "write_text", "write_bytes", "read_text", "read_bytes", "fromfile", "tofile", "save", "load",
                "savez", "savetxt", "loadtxt", "loadz", "libraries", "app", "context_override", "temp_override"}
BANNED_OPS_PREFIX = ("wm", "export_scene", "import_scene", "script", "preferences", "file", "render", "image", "sequencer")
MAX_BYTES = 40_000


def check(source):
    """Return (ok, problems[list], defined_names[list])."""
    problems = []
    if len(source.encode("utf-8")) > MAX_BYTES:
        return False, ["overlay too large"], []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, [f"syntax error: {e.msg} (line {e.lineno})"], []
    defined = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            n = node.name
            defined.append(n)
            if n in PROTECTED:
                problems.append(f"{n} is part of the judge/safety layer and cannot be replaced")
            elif n not in ALLOWED_REPLACE and not n.startswith(NEW_PREFIXES):
                problems.append(f"{n} is not on the replaceable list (allowed: {sorted(ALLOWED_REPLACE)} or new dojo_*/spec_* helpers)")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                if m.split(".")[0] not in IMPORT_OK:
                    problems.append(f"import of {m} is not allowed")
        elif isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            pass                                              # docstring
        elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            pass                                              # module constants
        else:
            problems.append("only functions, imports and UPPERCASE constants are allowed at top level")
    if not defined:
        problems.append("overlay defines no functions")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            problems.append(f"use of {node.id} is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED_ATTRS or (node.attr.startswith("__") and node.attr.endswith("__")):
                problems.append(f"attribute .{node.attr} is not allowed")
            if node.attr in ("ops",) and isinstance(node.value, ast.Name) and node.value.id == "bpy":
                pass
        elif isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            problems.append("global/nonlocal is not allowed")
        elif isinstance(node, (ast.While,)):
            # unbounded loops can hang the run; the runner also has a hard timeout, but be strict about the obvious
            if isinstance(node.test, ast.Constant) and node.test.value:
                problems.append("while True is not allowed (use bounded loops)")
    # bpy.ops.<category>.* : block categories that touch files/render/preferences
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "ops":
            if node.attr.startswith(BANNED_OPS_PREFIX):
                problems.append(f"bpy.ops.{node.attr} is not allowed")
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return (not out), out, defined
