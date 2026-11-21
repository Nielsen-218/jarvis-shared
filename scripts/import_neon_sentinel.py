"""Convert the owner's Meshy FBX to a self-contained companion GLB. Run with Blender --background --python ... -- SOURCE OUTPUT."""
import bpy, sys, json
from pathlib import Path
source, target = map(Path, sys.argv[sys.argv.index('--')+1:])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=str(next(source.glob('*Merged_Animations.fbx'))))
for image in bpy.data.images:
    name = Path(image.filepath).name
    matches = list(source.glob('*_' + name))
    if matches:
        image.filepath = str(matches[0]); image.reload()
        w,h=image.size
        if max(w,h)>1024: image.scale(round(w*1024/max(w,h)),round(h*1024/max(w,h)))
        image.pack()
for obj in bpy.context.scene.objects:
    if obj.type == 'MESH':
        bpy.context.view_layer.objects.active=obj
        mod=obj.modifiers.new('Desktop LOD','DECIMATE'); mod.ratio=min(1,24000/max(1,len(obj.data.polygons)))
        bpy.ops.object.modifier_apply(modifier=mod.name)
# Preserve the supplied clips; the runtime uses native animation instead of replacing bone rest rotations.
for action in bpy.data.actions: action.name=action.name.split('|')[-1]
arm=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
arm.animation_data.action=next(a for a in bpy.data.actions if a.name=='Agree_Gesture')
bpy.context.scene.frame_set(1)
target.parent.mkdir(parents=True,exist_ok=True)
bpy.ops.export_scene.gltf(filepath=str(target),export_format='GLB',export_animations=True,export_animation_mode='ACTIONS',export_frame_range=False,export_force_sampling=True,export_optimize_animation_size=True,export_image_format='AUTO')
print('EXPORTED:'+str(target))
