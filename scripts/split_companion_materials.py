"""Split the single-material companion robot GLB into per-region material SLOTS so the
renderer can tint each part independently (Phase F — per-part colour).

The shipped Hunyuan3D robot is one mesh with one baked-texture material, so colour could
only be a whole-body tint. This gives the eyes, chest core and ear modules their own named
materials — `eye_glow`, `chest_core`, `accent_primary` — which `glb-adapter.js` already
recognises (it tints glow slots via EMISSIVE, so it works over the baked texture). Every
slot keeps the original baked texture, so the model still looks like itself; the glow slots
additionally carry the palette's emissive colour (magenta eyes, cyan chest/ears — the concept
robot's own colours), which the Studio/profile can then change per part.

Geometry is NOT modified: vertex POSITIONS are preserved, so the runtime position-based skin
(`computeSkinWeights`) and the face landmarks still line up. Only material assignment changes.

Run headless:
    blender --background --python scripts/split_companion_materials.py -- \
        --in app/companion/models/robot/robot-lod1.glb \
        --out app/companion/models/robot/robot-lod1-multimat.glb \
        --rig app/companion/rigs/robot-rig.json
"""
from __future__ import annotations

import argparse
import json
import sys
from math import dist

import bpy  # type: ignore  (provided by Blender)


def _args(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out", dest="dst", required=True)
    p.add_argument("--rig", dest="rig", required=True)
    return p.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])


def _clear():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for item in list(coll):
            coll.remove(item)


def _best_frame(me, obj, landmarks):
    """glTF (+Y up) imports into Blender (+Z up). Find which axis mapping puts the eye
    landmarks on real geometry, so selection is frame-robust instead of assumed."""
    candidates = {
        "xyz": lambda v: (v[0], v[1], v[2]),
        "x_-z_y": lambda v: (v[0], -v[2], v[1]),   # the usual glTF->Blender for +Y-up
        "x_z_-y": lambda v: (v[0], v[2], -v[1]),
    }
    mw = obj.matrix_world
    centers = [mw @ poly.center for poly in me.polygons]
    eye = landmarks["eyeL"]["center"]
    r = landmarks["eyeL"]["radius"]
    best, best_n = None, -1
    for name, fn in candidates.items():
        tx, ty, tz = fn(eye)
        n = sum(1 for c in centers if dist((c.x, c.y, c.z), (tx, ty, tz)) < r * 1.8)
        if n > best_n:
            best, best_n = (name, fn), n
    return best[0], best[1], best_n


def main():
    a = _args(sys.argv)
    rig = json.loads(open(a.rig, encoding="utf-8").read())
    face = rig["face"]
    joints = rig["joints"]

    _clear()
    bpy.ops.import_scene.gltf(filepath=a.src)
    obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
    me = obj.data
    base = me.materials[0]
    # Name it "shell" (NOT "shell_light"): glb-adapter.inferMaterialSlot() tests /light/ (the accent
    # regex) before /shell/, so "shell_light" would misclassify the whole body as accent_primary.
    base.name = "shell"

    frame_name, fn, eye_hits = _best_frame(me, obj, face)
    print(f"[split] frame={frame_name} eye_hits={eye_hits} verts={len(me.vertices)} polys={len(me.polygons)}")
    if eye_hits == 0:
        raise SystemExit("[split] could not locate the eyes in any frame — aborting (no silent no-op)")

    mw = obj.matrix_world

    # Representative landmark points in Blender world space.
    def pt(v):
        x, y, z = fn(v)
        return (x, y, z)

    eyeL, eyeR = pt(face["eyeL"]["center"]), pt(face["eyeR"]["center"])
    rEye = max(face["eyeL"]["radius"], face["eyeR"]["radius"]) * 1.7
    earL, earR = pt(joints["earL"]["pivot"]), pt(joints["earR"]["pivot"])
    rEar = 0.032

    # Chest core: front-centre of the torso. Find the front-most surface in the torso band.
    torso_y = joints["torso"]["pivot"][1]
    band = [mw @ poly.center for poly in me.polygons
            if abs((mw @ poly.center).z - pt((0, torso_y, 0))[2]) < 0.025]
    # the "forward" axis is whichever of the horizontal axes the eyes stick out along
    fwd_axis = max(range(3), key=lambda i: abs(eyeL[i]) if i != _up_axis(fn) else -1) if False else _fwd_axis(fn)
    chest_front = max((c[fwd_axis] for c in band), default=0.06)
    chest = list(pt((0, torso_y + 0.012, 0)))
    chest[fwd_axis] = chest_front
    chest = tuple(chest)
    rChest = 0.034

    # Build the three glow materials as copies of the shell (so each keeps the baked texture),
    # then give them the concept palette's emissive colour.
    palette = {
        "eye_glow": (1.0, 0.212, 0.808),      # #ff36ce magenta
        "chest_core": (0.0, 0.898, 1.0),      # #00e5ff cyan
        "accent_primary": (0.0, 0.898, 1.0),  # #00e5ff cyan
    }
    slot_index = {"shell_light": 0}
    for name, rgb in palette.items():
        m = base.copy()
        m.name = name
        _set_emission(m, rgb, 1.0)
        me.materials.append(m)
        slot_index[name] = len(me.materials) - 1

    counts = {k: 0 for k in slot_index}
    for poly in me.polygons:
        c = mw @ poly.center
        p = (c.x, c.y, c.z)
        if dist(p, eyeL) < rEye or dist(p, eyeR) < rEye:
            slot = "eye_glow"
        elif dist(p, earL) < rEar or dist(p, earR) < rEar:
            slot = "accent_primary"
        elif dist(p, chest) < rChest:
            slot = "chest_core"
        else:
            slot = "shell_light"
        poly.material_index = slot_index[slot]
        counts[slot] += 1
    print(f"[split] face counts: {counts}")
    if counts["eye_glow"] == 0 or counts["chest_core"] == 0:
        raise SystemExit("[split] a glow slot got no faces — landmarks off, aborting")

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(filepath=a.dst, export_format="GLB", use_selection=True,
                              export_materials="EXPORT", export_image_format="AUTO")
    print(f"[split] wrote {a.dst}")


def _up_axis(fn):
    # which output axis the glTF +Y maps to
    v = fn((0, 1, 0))
    return max(range(3), key=lambda i: abs(v[i]))


def _fwd_axis(fn):
    # which output axis the glTF +Z (forward, out of the face) maps to
    v = fn((0, 0, 1))
    return max(range(3), key=lambda i: abs(v[i]))


def _set_emission(mat, rgb, strength):
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if not bsdf:
        return
    for key in ("Emission Color", "Emission"):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
            break
    if "Emission Strength" in bsdf.inputs:
        bsdf.inputs["Emission Strength"].default_value = strength


if __name__ == "__main__":
    main()
