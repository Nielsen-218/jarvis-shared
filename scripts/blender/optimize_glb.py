"""Runs INSIDE Blender (headless) — decimate a source GLB into real-time LODs.

    blender --background --python scripts/blender/optimize_glb.py -- \
        --in source.glb --out-dir out --name jarvis --tris 40000 12000 5000 [--max-texture 2048]

For each triangle target the source is re-imported fresh (no cumulative
artifacts), every mesh is collapse-decimated to the same ratio, textures larger
than --max-texture are downscaled, and the result is exported as GLB. Meshes
with shape keys (viseme/mouth morphs) are left undecimated because Blender's
decimate drops shape keys. The last stdout line is `JSON:{...}` with measured
before/after triangle counts, which scripts/optimize_companion_model.py parses.
"""
import argparse
import json
import os
import sys

import bpy


def _args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--name", default="model")
    p.add_argument("--tris", type=int, nargs="+", default=[40000, 12000, 5000])
    p.add_argument("--max-texture", type=int, default=2048)
    return p.parse_args(argv)


def _fresh_import(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in bpy.data.objects if o.type == "MESH"]


def _tris(meshes):
    n = 0
    for o in meshes:
        o.data.calc_loop_triangles()
        n += len(o.data.loop_triangles)
    return n


def _decimate(o, ratio):
    mod = o.modifiers.new("rea_decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o]):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _shrink_textures(limit):
    shrunk = 0
    for img in bpy.data.images:
        w, h = img.size
        if max(w, h) > limit and w and h:
            f = limit / max(w, h)
            img.scale(max(1, int(w * f)), max(1, int(h * f)))
            shrunk += 1
    return shrunk


def main():
    a = _args()
    os.makedirs(a.out_dir, exist_ok=True)
    meshes = _fresh_import(a.src)
    src_tris = _tris(meshes)
    report = {"source": a.src, "source_triangles": src_tris, "source_meshes": len(meshes), "lods": []}
    for level, target in enumerate(a.tris):
        meshes = _fresh_import(a.src)
        ratio = min(1.0, target / max(1, src_tris))
        kept_morphs = []
        for o in meshes:
            if o.data.shape_keys and o.data.shape_keys.key_blocks:
                kept_morphs.append(o.name)        # decimate would delete the morphs
                continue
            if ratio < 1.0:
                _decimate(o, ratio)
        shrunk = _shrink_textures(a.max_texture)
        out = os.path.join(a.out_dir, f"{a.name}-lod{level}.glb")
        bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_apply=True,
                                  export_animations=True, export_skins=True, export_morph=True)
        report["lods"].append({"level": level, "target_triangles": target, "ratio": round(ratio, 4),
                               "triangles": _tris(meshes), "bytes": os.path.getsize(out), "file": out,
                               "undecimated_morph_meshes": kept_morphs, "textures_downscaled": shrunk})
    print("JSON:" + json.dumps(report))


main()
