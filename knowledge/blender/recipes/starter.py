"""Original JARVIS starter. Reviewed code; no network, downloads or arbitrary exec.

Run in a separate factory-startup background Blender. Never overwrites outputs.
The generated scene is a starter/blockout, not a finished production model.
"""
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

RECIPE = "__JARVIS_RECIPE__"


def material(name, color, metallic=0, roughness=.45):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node = mat.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value = (*color, 1)
    node.inputs["Metallic"].default_value = metallic
    node.inputs["Roughness"].default_value = roughness
    return mat


def box(scene, name, size, location, mat, bevel=0):
    x, y, z = (v / 2 for v in size)
    vertices = [(-x,-y,-z), (x,-y,-z), (x,y,-z), (-x,y,-z),
        (-x,-y,z), (x,-y,z), (x,y,z), (-x,y,z)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], [(3,2,1,0), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7), (4,5,6,7)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = location
    mesh.materials.append(mat)
    if bevel:
        mod = obj.modifiers.new("Edge highlights", "BEVEL")
        mod.width, mod.segments = bevel, 3
    return obj


def aim(obj, point):
    obj.rotation_euler = (Vector(point) - obj.location).to_track_quat("-Z", "Y").to_euler()


def light(scene, name, location, energy, size, target):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.shape, data.size = energy, "DISK", size
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = location
    aim(obj, target)


def main():
    if RECIPE not in ("product_studio", "room_blockout", "turntable"):
        raise ValueError("Select a reviewed recipe using blender_create_starter.")
    folder = Path(__file__).resolve().parent
    blend, preview, manifest = (folder / name for name in ("starter.blend", "preview.png", "manifest.json"))
    if any(p.exists() for p in (blend, preview, manifest)):
        raise FileExistsError("Outputs already exist; create a new starter instead of overwriting.")
    scene = bpy.data.scenes.new("JARVIS_" + RECIPE)
    if bpy.context.window:
        bpy.context.window.scene = scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"  # Deterministic low-cost preview; no device assumptions.
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 640, 480
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(preview)
    scene.world = bpy.data.worlds.new("JARVIS neutral world")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.13, .16, .2, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .3
    floor = material("Matte slate", (.075, .09, .115), roughness=.65)
    shell = material("Brushed blue metal", (.075, .22, .32), metallic=.8, roughness=.3)
    trim = material("Dark rubber", (.018, .023, .028), roughness=.65)
    accent = material("Warm metal trim", (.65, .31, .08), metallic=.8, roughness=.28)
    box(scene, "Studio floor", (12,12,.12), (0,0,-.06), floor)
    if RECIPE == "room_blockout":
        wall = material("Warm plaster", (.7,.65,.56), roughness=.9)
        wood = material("Wood color placeholder", (.26,.1,.04), roughness=.5)
        box(scene, "Back wall", (5,.15,2.8), (0,2,1.4), wall)
        box(scene, "Side wall", (.15,4,2.8), (-2.5,0,1.4), wall)
        box(scene, "Table top", (1.8,.8,.08), (0,.2,.76), wood, .02)
        for x in (-.75,.75):
            for y in (-.1,.5):
                box(scene, "Table leg", (.08,.08,.72), (x,y,.36), trim, .008)
        target, position = (0,.2,.9), (6,-8,5)
    else:
        body = box(scene, "Editable device enclosure", (1.25,.7,.9), (0,0,.58), shell, .055)
        panel = box(scene, "Inset face panel", (1.08,.04,.66), (0,-.354,.59), trim, .025)
        strip = box(scene, "Accent strip", (.8,.025,.045), (0,-.382,.43), accent, .012)
        box(scene, "Display plinth", (1.7,1.15,.14), (0,0,.07), trim, .04)
        if RECIPE == "turntable":
            pivot = bpy.data.objects.new("Turntable controller", None)
            scene.collection.objects.link(pivot)
            for obj in (body, panel, strip):
                obj.parent = pivot
            scene.render.fps = 24
            scene.frame_start, scene.frame_end = 1, 96
            pivot.rotation_euler.z = 0
            pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=1)
            pivot.rotation_euler.z = math.tau
            pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=97)
            action = pivot.animation_data.action
            # Blender's layered Actions and legacy Actions expose curves differently.
            curves = list(getattr(action, "fcurves", []))
            for layer in getattr(action, "layers", []):
                for strip_data in layer.strips:
                    bag = strip_data.channelbag(pivot.animation_data.action_slot)
                    if bag:
                        curves.extend(bag.fcurves)
            for curve in curves:
                for key in curve.keyframe_points:
                    key.interpolation = "LINEAR"
            scene.frame_set(1)
        target, position = (0,0,.55), (2.7,-3.8,2.3)
    camera = bpy.data.objects.new("Studio camera", bpy.data.cameras.new("Studio camera"))
    scene.collection.objects.link(camera)
    camera.location = position
    camera.data.lens = 50
    aim(camera, target)
    scene.camera = camera
    light(scene, "Key softbox", (3,-4,5), 700, 4, target)
    light(scene, "Fill softbox", (-3,-1,3), 300, 3, target)
    light(scene, "Rim softbox", (1,4,4), 900, 3, target)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    if "--" in sys.argv and "--preview" in sys.argv[sys.argv.index("--") + 1:]:
        bpy.ops.render.render(write_still=True, scene=scene.name)
    result = {"recipe": RECIPE, "blender": bpy.app.version_string, "scene": scene.name,
        "objects": len(scene.objects), "blend_exists": blend.is_file(), "preview_exists": preview.is_file(),
        "quality": "editable starter; visual review required, not a production-ready asset"}
    manifest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("JARVIS_STARTER_RESULT " + json.dumps(result))


if __name__ == "__main__":
    main()
