#!/usr/bin/env python3
"""JARVIS Blender MCP server (stdio) — the SINGLE shared Blender backend for Codex,
Claude Code and (via a remote tunnel) ChatGPT.

Scope is Blender-only on purpose: none of the broader PC-control tools are exposed here,
so a remote/ChatGPT connection can reach Blender and nothing else. Read tools
(blender_status, list_scene_objects) run automatically. Action tools (render_preview,
export_glb) are **enforced** through the out-of-band approval boundary — an MCP
`confirm`/annotation is NOT accepted; the owner must approve in a terminal. Renders and
exports are additionally confined to the workspace by the worker itself.

Run:  JARVIS_BLENDER_TOKEN=... JARVIS_BLENDER_WORKSPACE=... python hub/blender_mcp.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import approval  # noqa: E402
import blender_client as bc  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

server = MCPServer("jarvis-blender")
_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_ACTION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)


@server.tool(description="Blender version, active scene and object names (read-only).", annotations=_READ)
def blender_status() -> dict:
    return bc.call("status")


@server.tool(description="List the current scene's objects (name, type, location). Read-only.", annotations=_READ)
def list_scene_objects() -> dict:
    return bc.call("list")


@server.tool(description="Render a still preview to the approved workspace. Requires owner "
                        "approval (run the printed approve command); renders only inside the workspace.",
             annotations=_ACTION)
def render_preview(path: str = "preview.png") -> dict:
    ok, info = approval.check_and_consume("render_preview", {"path": path})
    if not ok:
        return info
    return bc.call("render", path=path)


@server.tool(description="Export the scene to glTF/GLB inside the approved workspace. Requires "
                        "owner approval; path is confined to the workspace.",
             annotations=_ACTION)
def export_glb(path: str = "export.glb") -> dict:
    ok, info = approval.check_and_consume("export_glb", {"path": path})
    if not ok:
        return info
    return bc.call("export", path=path)


if __name__ == "__main__":
    server.run("stdio")
