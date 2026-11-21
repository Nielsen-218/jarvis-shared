# Companion models

Optimized glTF 2.0 binaries (`.glb`) the desktop companion can wear through the
`glb-model-v1` adapter (`app/companion/glb-adapter.js`). Files here are served
statically at `/companion/models/<name>.glb`; a profile selects one with:

```json
{ "adapter_id": "glb-model-v1", "model_url": "/companion/models/jarvis/jarvis-lod0.glb" }
```

Keep **source** models (the raw Meshy export, ~1.1M vertices) out of this folder
and out of git. Produce the real-time set with the Blender pipeline:

```bash
python scripts/optimize_companion_model.py path/to/meshy-source.glb --out app/companion/models/jarvis --tris 40000 12000 5000
```

That writes `jarvis-lod0.glb` (desktop), `jarvis-lod1.glb`, `jarvis-lod2.glb`
and `report.json` with measured triangle counts. Meshes that carry shape keys
(mouth/viseme morphs) are not decimated, so facial controls survive.


## Shipped: the concept robot (`robot/`)

`robot/robot-lod1.glb` (12k triangles, 5 MB, textured) is the default companion and is the only model tracked in git
(`.gitignore` keeps every other file here out). It is the multi-view Hunyuan3D shape painted from the concept board
(`docs/companion/concept-turnaround.webp`), decimated with `scripts/optimize_companion_model.py`. `robot-lod0.glb`
(40k) and `robot-lod2.glb` (5k) are local only.

It is a single static mesh. `app/companion/rigs/robot-rig.json` (built by `scripts/build_companion_rig.py`, joint
pivots measured with `scripts/model_probe.py`) tells the renderer how to skin it at load time (`robot-skin.js`)
and where to put the eyelids and mouth (`glb-face.js`). Look at it in isolation with `app/companion-lab.html`.
