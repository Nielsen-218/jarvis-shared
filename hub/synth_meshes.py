"""Synthetic ground-truth test creatures (SYNTHETIC - not image-derived), built inside Blender.

  blender -b --python hub/synth_meshes.py -- humanoid|quadruped OUT.glb [--no-weld]

Parts are unioned with a voxel remesh into ONE watertight mesh (the realistic shape of generator
output). --no-weld keeps the parts as disconnected overlapping shells, a deliberate defect that
makes Blender's heat-diffusion skinning fail (used by the recovery test).
"""
import math
import sys

import bpy
from mathutils import Matrix, Vector


def _part(kind, a, b, r):
    a, b = Vector(a), Vector(b)
    mid, d = (a + b) / 2, b - a
    if kind == "cyl":
        bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=r, depth=max(d.length, 1e-4), location=mid)
    else:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, radius=r, location=mid)
        bpy.context.active_object.scale = (1, 1, max(d.length / (2 * r), 0.3)) if d.length > 0 else (1, 1, 1)
    o = bpy.context.active_object
    if d.length > 1e-6:
        o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    return o


def humanoid():
    H = 1.8
    parts = [("ell", (0, 0, 0.95), (0, 0, 1.44), 0.17), ("ell", (0, 0, 0.84), (0, 0, 1.10), 0.16),
             ("cyl", (0, 0, 1.40), (0, 0, 1.52), 0.055), ("ell", (0, 0, 1.47), (0, 0, 1.78), 0.11)]
    for s in (1, -1):
        sh, el, wr = (s * 0.12, 0, 1.40), (s * 0.48, 0, 1.40), (s * 0.74, 0, 1.40)
        parts += [("cyl", sh, el, 0.045), ("cyl", el, wr, 0.038), ("ell", wr, (s * 0.83, 0, 1.40), 0.04)]
        for fi, (dy, dz) in enumerate(((-0.035, 0.0), (0.03, 0.0), (0.012, 0.0), (-0.012, 0.0), (-0.034, 0.0))):
            pass
        for fi, off in enumerate((-0.03, -0.015, 0.0, 0.015, 0.03)):
            ln = 0.075 if fi not in (0, 4) else 0.06
            y = off if fi else -0.04
            parts.append(("cyl", (s * 0.82, y, 1.40), (s * (0.82 + ln), y, 1.40 - (0.02 if fi == 0 else 0)), 0.0085))
        hip, kn, an = (s * 0.10, 0, 0.90), (s * 0.10, 0.01, 0.48), (s * 0.10, 0, 0.09)
        parts += [("cyl", (hip[0], 0, 0.95), kn, 0.075), ("cyl", kn, an, 0.055), ("ell", (s * 0.10, -0.04, 0.05), (s * 0.10, -0.17, 0.05), 0.055)]
    return parts


def quadruped():
    parts = [("ell", (0, -0.55, 0.78), (0, 0.55, 0.78), 0.22), ("cyl", (0, 0.5, 0.82), (0, 0.78, 1.02), 0.1),
             ("ell", (0, 0.76, 1.0), (0, 1.0, 1.0), 0.12), ("cyl", (0, -0.55, 0.8), (0, -1.0, 0.72), 0.04),
             ("cyl", (0, -1.0, 0.72), (0, -1.3, 0.6), 0.03)]
    for sx in (1, -1):
        for y in (0.38, -0.38):
            parts += [("cyl", (sx * 0.17, y, 0.72), (sx * 0.17, y, 0.4), 0.06), ("cyl", (sx * 0.17, y, 0.4), (sx * 0.17, y, 0.05), 0.04)]
    return parts


def build(kind, weld=True, voxel=0.006):
    parts = humanoid() if kind == "humanoid" else quadruped()
    objs = [_part(*p) for p in parts]
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    ob = bpy.context.active_object
    ob.name = f"synthetic_{kind}"
    if weld:
        ob.data.remesh_voxel_size = voxel
        bpy.ops.object.voxel_remesh()
    bpy.ops.object.shade_smooth()
    return ob


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ob = build(a[0], weld="--no-weld" not in a)
    print("SYNTH", a[0], len(ob.data.vertices), len(ob.data.polygons))
    bpy.ops.export_scene.gltf(filepath=a[1], export_format="GLB")
