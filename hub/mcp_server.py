#!/usr/bin/env python3
"""J.A.R.V.I.S. Universal Control Hub — MCP server (thin adapter over controlhub).

Exposes the hub's PC-control tools over MCP (stdio) so Claude Code and Codex can use
the *same* tools JARVIS uses. Read tools are marked read-only (clients can auto-run);
action tools are marked non-read-only / destructive so the client asks you first.

Run standalone:   python hub/mcp_server.py
Register in Claude Code (.mcp.json):
  { "mcpServers": { "jarvis-hub": { "command": "python", "args": ["hub/mcp_server.py"] } } }
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))   # repo root, so controlhub can import tools.*
sys.path.insert(0, str(HERE))          # hub/, for `import controlhub`

import approval  # noqa: E402
import controlhub as H  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402


def _gate(action: str, args: dict, fn):
    """Enforced approval boundary: an action runs only with a real owner grant
    (hub/approve.py). MCP annotations / a model-provided confirm are NOT authorization."""
    ok, info = approval.check_and_consume(action, args)
    return fn() if ok else info

server = MCPServer("jarvis-control-hub")

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_ACTION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


# ---- read tools (safe; client may auto-run) ----
@server.tool(description="PC health: CPU, memory, disk, battery, uptime, busiest processes.", annotations=_READ)
def get_pc_status() -> dict:
    return H.get_pc_status()


@server.tool(description="Capture the screen to a local image file and return its path + size.", annotations=_READ)
def take_screenshot(all_monitors: bool = False) -> dict:
    return H.take_screenshot(all_monitors)


@server.tool(description="List installed applications whose name contains the text (empty = all).", annotations=_READ)
def list_apps(contains: str = "") -> dict:
    return H.list_apps(contains)


@server.tool(description="Read the PC's current master volume.", annotations=_READ)
def get_volume() -> dict:
    return H.get_volume()


# ---- action tools (ENFORCED: owner must approve via hub/approve.py) ----
@server.tool(description="Launch an installed application by name. Requires owner approval.", annotations=_ACTION)
def open_app(app: str) -> dict:
    return _gate("open_app", {"app": app}, lambda: H.open_app(app))


@server.tool(description="Set the PC's master volume (0-100). Requires owner approval.", annotations=_ACTION)
def set_volume(percent: int) -> dict:
    return _gate("set_volume", {"percent": percent}, lambda: H.set_volume(percent))


@server.tool(description="Open an http(s) URL in the default browser. Requires owner approval.", annotations=_ACTION)
def open_url(url: str) -> dict:
    return _gate("open_url", {"url": url}, lambda: H.open_url(url))


@server.tool(description="Render/export via the hardened Blender worker. Requires owner approval.", annotations=_ACTION)
def blender_render(op: str = "render") -> dict:
    return _gate("blender_render", {"op": op}, lambda: H.blender_render(op))


@server.tool(description="Close a running application gracefully (may prompt to save). Requires owner approval.",
             annotations=_DESTRUCTIVE)
def close_app(app: str) -> dict:
    return _gate("close_app", {"app": app}, lambda: H.close_app(app))


@server.tool(description="Hybrid PC task: verified local automation/workflows first, local AI planning "
                        "when needed. Cloud planning requires a separate task-budget grant; no paid per-click loop.",
             annotations=_DESTRUCTIVE)
def run_pc_task(task: str) -> dict:
    return _gate("run_pc_task", {"task": task}, lambda: H.run_pc_task(task))


if __name__ == "__main__":
    server.run("stdio")
