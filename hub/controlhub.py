#!/usr/bin/env python3
"""J.A.R.V.I.S. Universal Control Hub — core.

One shared collection of PC-control tools that JARVIS (phone/brain), Claude Code and
Codex can all use, instead of a separate integration per AI. This module is the pure
core; `mcp_server.py` is a thin MCP adapter over it, and JARVIS can call it directly.

Each tool is tagged **read** (safe, runs automatically) or **action** (consequential —
requires confirmation). `run_tool(name, args, confirm=...)` is the single safe entry:
read tools always run; action tools return a `needs_confirmation` result unless
`confirm=True`. The MCP client's own approval prompt is the confirmation on that path;
the phone/HTTP path must pass an explicit approval.

Reuses existing JARVIS code (tools.apps / tools.media / tools.system / tools.pcview).
Local operations have no AI cost. Hybrid planning requires its own owner budget before
any paid cloud call; a hub action approval does not grant that spending.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"            # git-ignored: screenshots, etc.
ROOT = HERE.parent                # the JARVIS repo (for importing tools.*)


# ------------------------------------------------------------------ read tools
def get_pc_status() -> dict:
    """CPU, memory, disk, battery, uptime and the busiest processes."""
    import shutil
    import time

    import psutil

    mem = psutil.virtual_memory()
    st = {"cpu_percent": psutil.cpu_percent(interval=0.3),
          "memory_percent": mem.percent,
          "memory_used_gb": round(mem.used / 1e9, 1),
          "memory_total_gb": round(mem.total / 1e9, 1),
          "disk_free_gb": round(shutil.disk_usage(os.path.expanduser("~")).free / 1e9, 1),
          "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1)}
    b = psutil.sensors_battery()
    if b:
        st["battery_percent"] = round(b.percent)
        st["plugged_in"] = bool(b.power_plugged)
    procs = sorted(psutil.process_iter(["name", "cpu_percent"]),
                   key=lambda p: p.info.get("cpu_percent") or 0, reverse=True)
    st["top_processes"] = [p.info["name"] for p in procs if p.info.get("name")][:5]
    return st


def take_screenshot(all_monitors: bool = False) -> dict:
    """Capture the screen to a local file (git-ignored) and return its path + size."""
    from PIL import ImageGrab

    img = ImageGrab.grab(all_screens=all_monitors).convert("RGB")
    img.thumbnail((1600, 1600))
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / f"screen-{datetime.now():%Y%m%d-%H%M%S}.jpg"
    img.save(path, "JPEG", quality=80)
    return {"path": str(path), "width": img.width, "height": img.height}


def list_apps(contains: str = "") -> dict:
    """Installed apps whose name contains `contains` (empty = all)."""
    from tools import apps

    return {"apps": apps.list_apps(contains)}


def get_volume() -> dict:
    """The current master volume."""
    from tools import media

    return {"volume": media.volume(-1)}


# ------------------------------------------------------------------ action tools
def open_app(app: str) -> dict:
    """Launch an installed application by name."""
    from tools import apps

    return {"result": apps.open_app(app)}


def close_app(app: str) -> dict:
    """Close a running application gracefully (protected system processes are refused)."""
    from tools import apps

    return {"result": apps.close_app(app)}


def set_volume(percent: int) -> dict:
    """Set the master volume (0-100)."""
    from tools import media

    return {"result": media.volume(int(percent))}


def open_url(url: str) -> dict:
    """Open an http(s) URL in the default browser."""
    from tools import system

    return {"result": system.open_website(url)}


def run_pc_task(task: str) -> dict:
    """Use the same hybrid controller as voice/phone. MCP gate + separate exact-task
    budget/plan grants remain enforced; this never starts a paid screenshot/click loop."""
    from tools import pcview

    return {"result": pcview.control_pc(task)}


def blender_render(op: str = "render", **kw) -> dict:
    """Talk to Blender via the hardened worker (hub/blender_worker.py) through the
    authenticated client. The client sends JARVIS_BLENDER_TOKEN and the worker confines
    every render/export to JARVIS_BLENDER_WORKSPACE, so this path can neither skip auth nor
    write outside the approved workspace. If Blender/the worker isn't running, says so."""
    import sys
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import blender_client as bc
    return bc.call(op, **kw)


# ------------------------------------------------------------------ registry + gate
# kind: read = runs automatically; action = needs confirmation. destructive flags the
# broadest / least-reversible ones for the MCP destructiveHint.
TOOLS: list[dict] = [
    {"name": "get_pc_status", "fn": get_pc_status, "read": True, "destructive": False,
     "summary": "Read CPU/memory/disk/battery/top processes."},
    {"name": "take_screenshot", "fn": take_screenshot, "read": True, "destructive": False,
     "summary": "Capture the screen to a local file."},
    {"name": "list_apps", "fn": list_apps, "read": True, "destructive": False,
     "summary": "List installed applications."},
    {"name": "get_volume", "fn": get_volume, "read": True, "destructive": False,
     "summary": "Read the master volume."},
    {"name": "open_app", "fn": open_app, "read": False, "destructive": False,
     "summary": "Launch an installed application."},
    {"name": "close_app", "fn": close_app, "read": False, "destructive": True,
     "summary": "Close a running application (may prompt to save)."},
    {"name": "set_volume", "fn": set_volume, "read": False, "destructive": False,
     "summary": "Set the master volume."},
    {"name": "open_url", "fn": open_url, "read": False, "destructive": False,
     "summary": "Open an http(s) URL in the browser."},
    {"name": "run_pc_task", "fn": run_pc_task, "read": False, "destructive": True,
     "summary": "Drive the PC via the computer-use agent (click/type)."},
    {"name": "blender_render", "fn": blender_render, "read": False, "destructive": False,
     "summary": "Render/export via the Blender add-on (if running)."},
]
_BY_NAME = {t["name"]: t for t in TOOLS}


def run_tool(name: str, args: dict | None = None, confirm: bool = False) -> dict:
    """Single safe entry point. Read tools always run; action tools require confirm=True
    (the MCP client's approval, or an explicit phone/HTTP approval)."""
    spec = _BY_NAME.get(name)
    if not spec:
        return {"error": f"unknown tool: {name}"}
    args = args or {}
    if not spec["read"] and not confirm:
        return {"status": "needs_confirmation", "tool": name, "summary": spec["summary"], "args": args}
    try:
        return spec["fn"](**args)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:  # never crash the hub; report
        return {"error": f"{name} failed: {e}"}


if __name__ == "__main__":
    import sys

    print(json.dumps(run_tool(sys.argv[1] if len(sys.argv) > 1 else "get_pc_status",
                              confirm="--confirm" in sys.argv), indent=2, default=str))
