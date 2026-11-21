# JARVIS Blender reference kit

This feature is prepared on `feat/blender-reference-library`, stacked on PR #13.
It is not merged or deployed. It adds no dependencies, models, paid services or
unrestricted execution endpoint. Production JARVIS and the existing MCP are unchanged.

## Available tools

- `blender_reference(query, max_results=4)`: offline keyword/alias retrieval from
  18 original reference cards with official source URLs and a Blender 5.2 version
  scope. Returns at most five cards. Unsupported topics are reported as missing.
- `blender_project_plan(goal)`: a deterministic reference blueprint, not an AI
  generation call or executable plan. Includes relevant guidance, deliverable checks,
  render/export verification and current capability limits.
- `blender_create_starter(recipe)`: prepares a private standalone `build.py` and
  README for `product_studio`, `room_blockout` or `turntable`. Does not run them.
  User-provided Python, filenames and external recipe paths are never accepted.

The tools load on both desk and server roles without importing `bpy`. The personality
instructs JARVIS to retrieve the relevant local references before planning or
troubleshooting. Calling these tools itself makes no network or paid API requests;
the surrounding conversational model can still cost money. These are retrieval and
workflow resources, not model training or proof of universal Blender expertise.

Examples after approved activation:

- “Jarvis, check your Blender references for realistic skin and garment folds.”
- “Plan a properly scaled mechanical enclosure for Blender.”
- “Prepare a Blender product_studio starter.”
- “Why is my bevel uneven?” / “Check the local guide for glTF export problems.”

## Coverage and provenance

Planning, transforms, hard-surface modeling, subdivision, human anatomy/retopology,
PBR materials, UV/baking, garments/cloth, hair, rigging, animation, lighting, rendering,
Geometry Nodes, mobile export, print checks, Python context/threading and troubleshooting.

`handbook.json` contains original explanatory notes and practical starting advice,
not copied manual chapters or third-party meshes/textures. Official Blender manual/API
links are attached to each topic. Bevel, Principled BSDF, skinning, Geometry Nodes,
glTF and threading references were retrieved through the Agent Reach reader on
2026-10-17. Other topic links are supplemental references. Recipes are original code
in this repository and use no external assets. Preserve project licensing rules.

The installed version must still be checked for version-sensitive APIs and extensions.
If a specific fact is missing, retrieve an official reference instead of inventing it.
This small kit does not replace the entire manual or professional artistic judgement.

## Starter execution and safety

Read the generated script first. Its README gives a command for a **separate** Blender
process using `--background --factory-startup --python-exit-code 1 --python build.py`.
It creates an editable scene and writes `starter.blend` beside the script. Add
`-- --preview` for a 640x480, 24-sample CPU preview. No GPU device is assumed.
Existing `.blend`, preview or manifest outputs cause refusal before any scene change.
Every generation uses a new per-user directory. Do not run it over a working scene.

The output is deliberately labeled a starter/blockout. In particular, it does not
pretend that a beveled prop is a finished human, detailed garment or professional asset.
The turntable uses real linear keyframes. The room has a dimensional table and open
walls; materials are placeholders for later art work. All have a camera and three
area lights. Rendering is optional and can still use local hardware/electricity.

Preparing a script **does not** bypass PC plan approval or authorize its execution.
The existing worker continues to support only status/list/render/export. Arbitrary
model-generated `bpy` execution is not added. Future modeling operations should be
typed, reviewed capabilities with output verification, not a general remote shell.
Previously verified PC workflows can be saved using the existing owner Save action;
do not label a knowledge note or unexecuted recipe a successful learned workflow.

## Actual verification

- **310 Python/MCP tests passed in 16.88s**, including 17 new retrieval/recipe tests.
  No real paid model was called.
- Real isolated Blender **5.2.2 LTS** built all three recipes successfully. All saved
  projects reopened with a camera, three lights and expected object counts (9/12/10).
- Turntable rotation at frame 49 was verified as pi radians. Existing outputs were
  refused on rerun, with their saved file modification times unchanged.
- The product-studio CPU preview rendered successfully and was visually inspected:
  beveled metal enclosure, inset panel, trim, plinth and studio illumination present.
- All generated projects and raw research pages remain in ignored development data,
  not Git. No production scene/service, existing MCP, Android APK, wardrobe or bridge
  was changed.

Remaining: owner review and activation on PC/laptop; physical-phone access to generated
artifacts; real artist/owner review of future complex assets. The toolkit does not
claim to have tested every artistic workflow, rig, simulation, export target or API.
