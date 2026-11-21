#!/usr/bin/env python3
"""Headless Blender self-test for the hardened worker. Run with:

    blender -b -P hub/blender_selftest.py

Builds a tiny benign scene and exercises the worker's handle() with REAL bpy:
status, list, a confined render + glTF export, a rejected out-of-workspace path, and a
rejected unauthenticated request. Prints a JSON block between SELFTEST markers.

Requires env: JARVIS_BLENDER_TOKEN (shared secret) and JARVIS_BLENDER_WORKSPACE (output dir).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_worker as W  # noqa: E402

import bpy  # noqa: E402

TOK = os.environ["JARVIS_BLENDER_TOKEN"]
WS = os.environ["JARVIS_BLENDER_WORKSPACE"]
Path(WS).mkdir(parents=True, exist_ok=True)


def build_scene():
    # start from the default scene; ensure a cube, a camera and a light exist
    if not any(o.type == "MESH" for o in bpy.data.objects):
        bpy.ops.mesh.primitive_cube_add()
    if not any(o.type == "CAMERA" for o in bpy.data.objects):
        bpy.ops.object.camera_add(location=(4, -4, 3))
    bpy.context.scene.camera = next(o for o in bpy.data.objects if o.type == "CAMERA")
    if not any(o.type == "LIGHT" for o in bpy.data.objects):
        bpy.ops.object.light_add(type="SUN", location=(3, 3, 6))
    r = bpy.context.scene.render
    r.engine = "BLENDER_WORKBENCH"   # fast, no GPU needed
    r.resolution_x = r.resolution_y = 96
    r.image_settings.file_format = "PNG"


def main():
    build_scene()
    results = {
        "status": W.handle({"op": "status", "token": TOK}, bpy, WS, TOK),
        "list": W.handle({"op": "list", "token": TOK}, bpy, WS, TOK),
        "render_confined": W.handle({"op": "render", "path": "selftest/render.png", "token": TOK}, bpy, WS, TOK),
        "export_confined": W.handle({"op": "export", "path": "selftest/scene.glb", "token": TOK}, bpy, WS, TOK),
        "reject_outside_workspace": W.handle({"op": "render", "path": "../../escape.png", "token": TOK}, bpy, WS, TOK),
        "reject_unauthenticated": W.handle({"op": "status", "token": "WRONG-TOKEN"}, bpy, WS, TOK),
        "reject_unknown_op": W.handle({"op": "delete_everything", "token": TOK}, bpy, WS, TOK),
    }
    ok = (results["status"].get("ok")
          and results["render_confined"].get("exists")
          and results["export_confined"].get("exists")
          and results["reject_outside_workspace"].get("ok") is False
          and results["reject_unauthenticated"].get("error") == "unauthorized"
          and results["reject_unknown_op"].get("ok") is False)
    print("SELFTEST_BEGIN")
    print(json.dumps({"all_passed": bool(ok), "results": results}, indent=2, default=str))
    print("SELFTEST_END")


main()
