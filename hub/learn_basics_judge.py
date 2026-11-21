"""Judge for the Blender-basics skill (core/learn_blender_basics.py): run a candidate script in a clean scene, then MEASURE the result.

  blender -b --factory-startup --python hub/learn_basics_judge.py -- SCRIPT.py VARIANT.json OUT.json

The scene holds a tube mesh 'Limb' along VARIANT['axis'] (x/y/z) from VARIANT['start'] to VARIANT['end'] (exposed to the candidate as the
global VARIANT). The candidate must build an armature object 'Rig' with bones 'upper' then 'lower' (lower parented to upper, meeting in
the middle of the tube) and bind 'Limb' to it with automatic weights. Nothing the candidate prints or claims is read: the checks look at
the actual bones, vertex groups and the mesh deformed by bending the elbow 70 degrees. The expected values come from VARIANT only."""
import json
import math
import sys
import traceback

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

AXES = {"x": Vector((1, 0, 0)), "y": Vector((0, 1, 0)), "z": Vector((0, 0, 1))}


def build_limb(v):
    d = AXES[v["axis"]]
    length = v["end"] - v["start"]
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=20, radius1=v["radius"], radius2=v["radius"], depth=length)
    vert_edges = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > length * 0.5]
    bmesh.ops.subdivide_edges(bm, edges=vert_edges, cuts=11, use_grid_fill=False)
    me = bpy.data.meshes.new("Limb")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("Limb", me)
    bpy.context.scene.collection.objects.link(ob)
    z = Vector((0, 0, 1))
    rot = z.rotation_difference(d).to_matrix().to_4x4()
    ob.matrix_world = Matrix.Translation(d * (v["start"] + length / 2)) @ rot
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    me.update()
    return ob


def main():
    script, vjson, out = sys.argv[sys.argv.index("--") + 1:][:3]
    v = json.load(open(vjson))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    limb = build_limb(v)
    length = v["end"] - v["start"]
    d = AXES[v["axis"]]
    res = {"checks": {}, "error": None}
    try:
        exec(compile(open(script, encoding="utf-8").read(), "<candidate>", "exec"), {"bpy": bpy, "VARIANT": dict(v), "__name__": "__candidate__"})
    except BaseException as e:
        res["error"] = "".join(traceback.format_exception_only(type(e), e)).strip()[:300]
    chk = res["checks"]
    rig = bpy.data.objects.get("Rig")
    limb = bpy.data.objects.get("Limb") or limb
    bones = rig.data.bones if rig and rig.type == "ARMATURE" else {}
    up, lo = (bones.get("upper"), bones.get("lower")) if bones else (None, None)
    chk["rig_has_upper_and_lower"] = bool(up and lo)
    chk["lower_is_child_of_upper"] = bool(up and lo and lo.parent == up)
    if up and lo:
        mw = rig.matrix_world
        head_u, tail_u, tail_l = mw @ up.head_local, mw @ up.tail_local, mw @ lo.tail_local
        ax = lambda p: p.dot(d)
        chk["joint_in_the_middle"] = abs(ax(tail_u) - (v["start"] + length / 2)) < 0.1 * length
        chk["bones_span_the_tube"] = abs(ax(head_u) - v["start"]) < 0.15 * length and abs(ax(tail_l) - v["end"]) < 0.15 * length
    else:
        chk["joint_in_the_middle"] = chk["bones_span_the_tube"] = False
    mods = [m for m in limb.modifiers if m.type == "ARMATURE" and m.object == rig] if rig else []
    chk["armature_modifier_binds_limb"] = bool(mods)
    vg = {g.name for g in limb.vertex_groups}
    n = len(limb.data.vertices)
    weighted = sum(1 for vt in limb.data.vertices if any(g.weight > 0 for g in vt.groups)) if {"upper", "lower"} <= vg else 0
    chk["vertex_groups_cover_95_percent"] = bool({"upper", "lower"} <= vg and weighted >= 0.95 * n)
    # deformation: bend the elbow 70 degrees and look at the deformed mesh
    moved_ok = stays_ok = no_tear = False
    try:
        if not (up and lo and mods and chk["vertex_groups_cover_95_percent"]):
            why = []
            if not rig:
                why.append("no object named Rig")
            elif not (up and lo):
                why.append("Rig has bones " + str([b.name for b in rig.data.bones]) + " (need upper and lower)")
            if not mods:
                why.append("Limb has no Armature modifier pointing at Rig")
            if not chk["vertex_groups_cover_95_percent"]:
                why.append("Limb vertex groups are " + str(sorted(vg)) + " (need weights on upper and lower)")
            raise RuntimeError("; ".join(why) or "not bound")
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="POSE")
        pb = rig.pose.bones["lower"]
        pb.rotation_mode = "XYZ"
        # bend about an axis perpendicular to the tube
        perp = Vector((1, 0, 0)) if v["axis"] != "x" else Vector((0, 1, 0))
        local = (lo.matrix_local.to_3x3().inverted() @ rig.matrix_world.to_3x3().inverted() @ perp).normalized()
        pb.rotation_mode = "AXIS_ANGLE"
        pb.rotation_axis_angle = (math.radians(70), local.x, local.y, local.z)
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        ev = limb.evaluated_get(dg).to_mesh()
        P0 = np.array([(limb.matrix_world @ vt.co)[:] for vt in limb.data.vertices])
        P1 = np.array([(limb.matrix_world @ vt.co)[:] for vt in ev.vertices])
        t = (P0 @ np.array(d[:]) - v["start"]) / length
        move = np.linalg.norm(P1 - P0, axis=1) / length
        stays_ok = bool(np.mean(move[t < 0.35] < 0.02) > 0.95)
        moved_ok = bool(np.mean(move[t > 0.8] > 0.1) > 0.9)
        edges = np.array([[e.vertices[0], e.vertices[1]] for e in ev.edges])
        a = np.linalg.norm(P0[edges[:, 0]] - P0[edges[:, 1]], axis=1) + 1e-9
        b = np.linalg.norm(P1[edges[:, 0]] - P1[edges[:, 1]], axis=1)
        no_tear = bool(np.percentile(b / a, 99) < 1.6 and np.mean(b / a > 2.5) <= 0.0015)
    except Exception as e:
        res["pose_error"] = str(e)[:120]
    chk["lower_half_follows_the_bend"] = moved_ok
    chk["upper_half_stays_put"] = stays_ok
    chk["no_torn_edges_when_bent"] = no_tear
    json.dump(res, open(out, "w"))


main()
