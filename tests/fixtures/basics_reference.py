# TEACHER'S REFERENCE (validates the judge only; never shown to JARVIS, never used in any prompt or lesson)
import bpy
from mathutils import Vector
AX = {"x": Vector((1, 0, 0)), "y": Vector((0, 1, 0)), "z": Vector((0, 0, 1))}[VARIANT["axis"]]
s, e = VARIANT["start"], VARIANT["end"]
limb = bpy.data.objects["Limb"]
arm = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", arm)
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
up = arm.edit_bones.new("upper")
up.head, up.tail = AX * s, AX * ((s + e) / 2)
lo = arm.edit_bones.new("lower")
lo.head, lo.tail = up.tail.copy(), AX * e
lo.parent = up
lo.use_connect = True
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.object.select_all(action="DESELECT")
limb.select_set(True)
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
