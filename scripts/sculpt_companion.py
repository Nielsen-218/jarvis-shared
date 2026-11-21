"""Free-form mesh sculpting for the companion robot, via headless Blender (Phase F — geometry).

Proportions move BONES; this moves VERTICES — real sculpt-brush edits (inflate / grab / smooth /
pinch) with a smooth radial falloff, the same maths a sculpt brush uses. Because the companion is
skinned at load by VERTEX POSITION (`computeSkinWeights`), an arbitrarily reshaped mesh still rigs
and animates — so sculpting is compatible with the rig as long as topology, UVs and the baked
texture are preserved (they are: we only move existing vertices).

A stroke is {mode, center:[x,y,z] (model frame, +Y up), radius, strength (m), dir?:[x,y,z]}.
`BUILDS` are named stroke sets (shippable body shapes that bone-scaling can't make — rounder,
leaner). Run headless:

    blender --background --python scripts/sculpt_companion.py -- \
        --in app/companion/models/robot/robot-lod1-multimat.glb \
        --out app/companion/models/robot/robot-lod1-rounder.glb \
        --build rounder

or pass --strokes a JSON file / inline JSON for a custom free-form sculpt.
"""
from __future__ import annotations

import argparse
import json
import sys

try:                              # available only inside Blender; the BUILDS table imports fine without it
    import bmesh  # type: ignore
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ModuleNotFoundError:       # importing this module for its BUILDS presets (e.g. in tests)
    bmesh = bpy = None
    def Vector(xyz):              # minimal stand-in so to_blender() stays importable
        return tuple(xyz)


# model frame (+Y up) -> Blender (+Z up): the glTF importer rotates +Y-up onto +Z-up.
def to_blender(p):
    return Vector((p[0], -p[2], p[1]))


# Named body shapes. Centres are in the model frame (y-up); the rig is ~0.30 m tall.
BUILDS = {
    "rounder": [  # puff the head + torso outward for a cuter, rounder look (gentle inflate, smoothed clean)
        {"mode": "inflate", "center": [0, 0.205, 0.0], "radius": 0.080, "strength": 0.0045},
        {"mode": "inflate", "center": [0, 0.108, 0.0], "radius": 0.080, "strength": 0.0050},
        {"mode": "smooth",  "center": [0, 0.205, 0.0], "radius": 0.090, "strength": 0.8},
        {"mode": "smooth",  "center": [0, 0.108, 0.0], "radius": 0.090, "strength": 0.9},
        {"mode": "smooth",  "center": [0, 0.108, 0.0], "radius": 0.090, "strength": 0.9},
    ],
    "lean": [  # draw the torso + upper legs in for a slimmer build
        {"mode": "inflate", "center": [0, 0.108, 0.0], "radius": 0.080, "strength": -0.0045},
        {"mode": "inflate", "center": [0, 0.065, 0.0], "radius": 0.060, "strength": -0.0035},
        {"mode": "smooth",  "center": [0, 0.100, 0.0], "radius": 0.100, "strength": 0.9},
        {"mode": "smooth",  "center": [0, 0.100, 0.0], "radius": 0.100, "strength": 0.9},
    ],
}


def _args(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out", dest="dst", required=True)
    p.add_argument("--build", dest="build", default=None, help=f"one of {list(BUILDS)}")
    p.add_argument("--strokes", dest="strokes", default=None, help="inline JSON list or a path to one")
    return p.parse_args(argv[argv.index("--") + 1:] if "--" in argv else [])


def _load_strokes(a):
    if a.build:
        if a.build not in BUILDS:
            raise SystemExit(f"[sculpt] unknown build {a.build!r}; have {list(BUILDS)}")
        return BUILDS[a.build]
    if a.strokes:
        text = a.strokes
        if not text.lstrip().startswith("["):
            text = open(a.strokes, encoding="utf-8").read()
        return json.loads(text)
    raise SystemExit("[sculpt] pass --build or --strokes")


def _clear():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for item in list(coll):
            coll.remove(item)


def _falloff(d, r):
    if d >= r:
        return 0.0
    x = d / r
    return (1.0 - x * x) ** 2          # smooth ("smooth" brush) falloff


def sculpt(bm, strokes):
    bm.verts.ensure_lookup_table()
    bm.normal_update()
    moved = 0
    for s in strokes:
        c = to_blender(s["center"])
        r = float(s["radius"])
        strength = float(s.get("strength", 0.0))
        mode = s.get("mode", "inflate")
        direction = to_blender(s["dir"]) if s.get("dir") else None
        for v in bm.verts:
            d = (v.co - c).length
            w = _falloff(d, r)
            if w <= 0.0:
                continue
            if mode == "inflate":
                v.co += v.normal * (strength * w)
            elif mode == "grab" and direction is not None:
                v.co += direction * (strength * w)
            elif mode == "pinch":
                to_c = c - v.co
                if to_c.length > 1e-6:
                    v.co += to_c.normalized() * (strength * w)
            elif mode == "smooth":
                nb = [e.other_vert(v).co for e in v.link_edges]
                if nb:
                    avg = sum(nb, Vector((0, 0, 0))) / len(nb)
                    v.co += (avg - v.co) * (strength * w)
            moved += 1
        bm.normal_update()
    return moved


def main():
    a = _args(sys.argv)
    strokes = _load_strokes(a)
    _clear()
    bpy.ops.import_scene.gltf(filepath=a.src)
    obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
    me = obj.data

    # Weld coincident vertices first. The glТF import splits verts at UV/normal/material seams; sculpting
    # each along its own normal would pull those duplicates apart (gaps / loose verts). Merging by distance
    # makes seam-coincident verts share one position+normal so they move together. (Per-face material_index
    # is preserved; the exporter re-splits by normal/UV on the way out.)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.00005)
    bpy.ops.object.mode_set(mode="OBJECT")
    v0 = len(me.vertices)

    bm = bmesh.new()
    bm.from_mesh(me)
    moved = sculpt(bm, strokes)
    bm.to_mesh(me)
    bm.free()
    me.update()
    print(f"[sculpt] strokes={len(strokes)} vertex-hits={moved} verts={v0} (topology unchanged: {len(me.vertices) == v0})")
    if len(me.vertices) != v0:
        raise SystemExit("[sculpt] topology changed — aborting")
    if moved == 0:
        raise SystemExit("[sculpt] no vertices moved — centres/radii off, aborting")

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(filepath=a.dst, export_format="GLB", use_selection=True,
                              export_materials="EXPORT", export_image_format="AUTO")
    print(f"[sculpt] wrote {a.dst}")


if __name__ == "__main__":
    main()
