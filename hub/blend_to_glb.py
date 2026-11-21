"""Read-only .blend -> GLB for the rigging job (runs headless; never saves the .blend).

  blender -b --factory-startup --python hub/blend_to_glb.py -- IN.blend OUT.glb

Opens the file, keeps the largest mesh (the character), drops other objects and any existing rig, applies
its transforms and exports it with its materials/textures. The original .blend is not modified.
"""
import json
import sys

import bpy

src, out = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.open_mainfile(filepath=src, load_ui=False)
meshes = sorted([o for o in bpy.data.objects if o.type == "MESH"], key=lambda o: -len(o.data.vertices))
if not meshes:
    print("BLEND_TO_GLB " + json.dumps({"ok": False, "error": "no mesh in file"}))
    sys.exit(0)
main = meshes[0]
for o in list(bpy.data.objects):
    if o is not main:
        bpy.data.objects.remove(o)
for m in list(main.modifiers):
    if m.type == "ARMATURE":
        main.modifiers.remove(m)
main.vertex_groups.clear()
main.parent = None
bpy.context.view_layer.objects.active = main
for o in bpy.context.selected_objects:
    o.select_set(False)
main.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True, export_apply=True, export_animations=False)
print("BLEND_TO_GLB " + json.dumps({"ok": True, "mesh": main.name, "verts": len(main.data.vertices),
                                    "faces": len(main.data.polygons), "materials": len(main.data.materials)}))
