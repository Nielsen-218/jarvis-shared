# VERIFIED PROCEDURE blender.two_bone_limb_bind (authored by Claude as a teacher; verified by the independent judge, see its manifest).
# Builds a two-bone armature along a straight, axis-aligned tube and binds the tube to it WITHOUT context-dependent operator sequences:
# bones from edit_bones, weights from vertex_groups.add, binding through an Armature modifier. It then BENDS the lower bone 70 degrees,
# measures the deformed mesh itself, and walks a recovery ladder of blend widths when its own measurement fails.
# Scope: straight tube along x, y or z, exactly two bones. Anything else is out of scope and must not use this procedure.
import math

import bpy
from mathutils import Vector

AXES = {"x": Vector((1, 0, 0)), "y": Vector((0, 1, 0)), "z": Vector((0, 0, 1))}
MAX_THICKNESS = 0.135                                    # radius / length: swept on x and z tubes, 0.13 passes and 0.15 tears


class SkillError(Exception):
    pass


def _check(mesh_name, rig_name, axis, start, end, if_exists):
    ob = bpy.data.objects.get(mesh_name)
    if ob is None or ob.type != "MESH":
        raise SkillError("mesh_missing: no mesh object named " + mesh_name)
    if len(ob.data.vertices) < 8:
        raise SkillError("mesh_too_small: fewer than 8 vertices")
    if axis not in AXES:
        raise SkillError("bad_axis: axis must be x, y or z")
    if not end > start:
        raise SkillError("bad_range: end must be greater than start")
    d = AXES[axis]
    side = Vector((0, 0, 1)) if axis != "z" else Vector((1, 0, 0))
    pts = [ob.matrix_world @ v.co for v in ob.data.vertices]
    width = max(max(p.dot(u) for p in pts) - min(p.dot(u) for p in pts) for u in (side, d.cross(side)))
    if width / 2 / (end - start) > MAX_THICKNESS:        # measured limit: thicker tubes tear at a 70 degree bend with this weighting
        raise SkillError("too_thick: radius/length is " + str(round(width / 2 / (end - start), 3)) + ", verified only up to " + str(MAX_THICKNESS))
    stale = bpy.data.objects.get(rig_name)
    if stale is not None:
        if if_exists != "rename_old":
            raise SkillError("rig_exists: an object named " + rig_name + " is already in the scene")
        stale.name = rig_name + ".old"                 # non-destructive: the old object is kept under another name
    return ob


def _build_rig(rig_name, names, d, start, end):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    arm = bpy.data.armatures.new(rig_name)
    rig = bpy.data.objects.new(rig_name, arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    up = arm.edit_bones.new(names[0])
    up.head, up.tail = d * start, d * ((start + end) / 2)
    lo = arm.edit_bones.new(names[1])
    lo.head, lo.tail = up.tail.copy(), d * end
    lo.parent = up
    lo.use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def _bind(mesh, rig, names, d, start, end, band):
    for m in list(mesh.modifiers):
        if m.type == "ARMATURE":
            mesh.modifiers.remove(m)
    for n in names:
        old = mesh.vertex_groups.get(n)
        if old is not None:
            mesh.vertex_groups.remove(old)
    gu = mesh.vertex_groups.new(name=names[0])
    gl = mesh.vertex_groups.new(name=names[1])
    mw = mesh.matrix_world
    length = end - start
    for v in mesh.data.vertices:
        t = ((mw @ v.co).dot(d) - start) / length
        x = min(1.0, max(0.0, (t - (0.5 - band)) / (2 * band)))
        wl = x * x * (3 - 2 * x)                         # smooth hand-over from the upper to the lower bone around the joint
        gu.add([v.index], 1.0 - wl, "REPLACE")
        gl.add([v.index], wl, "REPLACE")
    mod = mesh.modifiers.new(name="Armature", type="ARMATURE")
    mod.object = rig
    mesh.parent = rig
    mesh.matrix_parent_inverse = rig.matrix_world.inverted()


def _measure(mesh, rig, names, d, start, end):
    """Bend the lower bone 70 degrees and look at the deformed mesh: -> (ok, details)."""
    length = end - start
    perp = Vector((0, 1, 0)) if d.x > 0.5 else Vector((1, 0, 0))
    bone = rig.data.bones[names[1]]
    local = (bone.matrix_local.to_3x3().inverted() @ rig.matrix_world.to_3x3().inverted() @ perp).normalized()
    pb = rig.pose.bones[names[1]]
    pb.rotation_mode = "AXIS_ANGLE"
    pb.rotation_axis_angle = (math.radians(70), local.x, local.y, local.z)
    bpy.context.view_layer.update()
    ev = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    mw = mesh.matrix_world
    p0 = [mw @ v.co for v in mesh.data.vertices]
    p1 = [mw @ v.co for v in me.vertices]
    ts = [(p.dot(d) - start) / length for p in p0]
    moves = [(a - b).length / length for a, b in zip(p1, p0)]
    near = [m for t, m in zip(ts, moves) if t < 0.35]
    far = [m for t, m in zip(ts, moves) if t > 0.8]
    stays = sum(1 for m in near if m < 0.02) / max(len(near), 1)
    follows = sum(1 for m in far if m > 0.1) / max(len(far), 1)
    ratios = []
    for e in me.edges:
        a, b = e.vertices[0], e.vertices[1]
        ratios.append((p1[a] - p1[b]).length / ((p0[a] - p0[b]).length + 1e-9))
    ratios.sort()
    p99 = ratios[int(0.99 * (len(ratios) - 1))] if ratios else 0.0
    torn = sum(1 for r in ratios if r > 2.5) / max(len(ratios), 1)
    ev.to_mesh_clear()
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)       # leave the rig in its rest pose
    bpy.context.view_layer.update()
    ok = stays > 0.95 and follows > 0.9 and p99 < 1.6 and torn <= 0.0015
    return ok, {"upper_stays": round(stays, 3), "lower_follows": round(follows, 3), "p99_stretch": round(p99, 3), "torn": round(torn, 4)}


def run(mesh_name="Limb", rig_name="Rig", bone_names=("upper", "lower"), axis="z", start=0.0, end=1.0,
        bands=(0.15, 0.10, 0.05), if_exists="error"):
    mesh = _check(mesh_name, rig_name, axis, start, end, if_exists)
    d = AXES[axis]
    rig = _build_rig(rig_name, bone_names, d, start, end)
    tried = []
    for band in bands:                                   # recovery ladder: next blend width when the measured bend is not clean
        _bind(mesh, rig, bone_names, d, start, end, band)
        ok, detail = _measure(mesh, rig, bone_names, d, start, end)
        tried.append({"band": band, "ok": ok, **detail})
        if ok:
            report = {"ok": True, "band": band, "tried": tried}
            mesh["skill_report"] = str(report)
            return report
    mesh["skill_report"] = str({"ok": False, "tried": tried})
    raise SkillError("verification_failed: no blend width gave a clean bend " + str(tried[-1]))
