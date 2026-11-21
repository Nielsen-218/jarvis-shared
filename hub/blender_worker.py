#!/usr/bin/env python3
"""JARVIS Blender worker — run this INSIDE Blender (Scripting tab -> Run Script).

Hardened replacement for the old addon. ONE narrow, typed op set over a LOOPBACK-ONLY
socket, with:
  * token authentication (JARVIS_BLENDER_TOKEN; never committed),
  * strict output confinement to JARVIS_BLENDER_WORKSPACE (canonical path, symlink/`..`
    escapes rejected),
  * exactly four ops: status, list, render, export — NO arbitrary Python/bpy exec.

Self-contained (stdlib + bpy only) so it runs inside Blender. The pure helpers
`confine`, `check_token` and `handle` are imported by the venv-side tests and the
headless self-test (handle takes bpy injected), so the security logic is tested
without the GUI.
"""
import hmac
import json
import os
import queue
import socket
import threading
from pathlib import Path

try:
    import bpy
except ImportError:  # importable in the venv for tests; start() needs real bpy
    bpy = None

PORT = int(os.environ.get("JARVIS_BLENDER_PORT", "9436"))
SUPPORTED = ("status", "list", "render", "export")


def check_token(provided: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(str(provided or ""), str(expected))


def confine(requested: str, workspace: str) -> Path | None:
    """Resolve `requested` under `workspace`; return the real path only if it stays
    inside the workspace after resolving symlinks and `..`. Otherwise None."""
    ws = Path(workspace).resolve()
    p = Path(requested)
    if not p.is_absolute():
        p = ws / p
    rp = p.resolve()
    try:
        rp.relative_to(ws)
    except ValueError:
        return None
    return rp


def handle(req: dict, bpy_mod, workspace: str, token: str) -> dict:
    """Dispatch one authenticated request. `bpy_mod` is injected so tests pass a fake."""
    if not check_token(req.get("token", ""), token):
        return {"ok": False, "error": "unauthorized"}
    op = req.get("op")
    if op not in SUPPORTED:
        return {"ok": False, "error": f"unsupported op {op!r}; allowed: {SUPPORTED}"}
    if op == "status":
        return {"ok": True, "blender": bpy_mod.app.version_string,
                "scene": bpy_mod.context.scene.name,
                "objects": [o.name for o in bpy_mod.data.objects][:100]}
    if op == "list":
        return {"ok": True, "objects": [{"name": o.name, "type": o.type,
                                         "location": [round(c, 3) for c in o.location]}
                                        for o in bpy_mod.data.objects][:200]}
    if op == "render":
        dest = confine(str(req.get("path", "render.png")), workspace)
        if dest is None:
            return {"ok": False, "error": "render path escapes the approved workspace"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        bpy_mod.context.scene.render.filepath = str(dest)
        bpy_mod.ops.render.render(write_still=True)
        return {"ok": True, "render": str(dest), "exists": dest.exists()}
    if op == "export":
        if not str(req.get("path", "")).lower().endswith((".glb", ".gltf")):
            return {"ok": False, "error": "export path must end in .glb or .gltf"}
        dest = confine(str(req["path"]), workspace)
        if dest is None:
            return {"ok": False, "error": "export path escapes the approved workspace"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        bpy_mod.ops.export_scene.gltf(filepath=str(dest))
        return {"ok": True, "export": str(dest), "exists": dest.exists()}
    return {"ok": False, "error": "unreachable"}


# ---- live socket server (inside Blender) ----
_jobs: "queue.Queue" = queue.Queue()


def _pump():
    while not _jobs.empty():
        req, reply = _jobs.get()
        try:
            reply["result"] = handle(req, bpy, os.environ["JARVIS_BLENDER_WORKSPACE"],
                                     os.environ.get("JARVIS_BLENDER_TOKEN", ""))
        except Exception as e:
            reply["result"] = {"ok": False, "error": str(e)}
        reply["event"].set()
    return 0.1


def _serve():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))     # loopback only, never a public host
    srv.listen(5)
    print(f"JARVIS Blender worker on 127.0.0.1:{PORT} (workspace={os.environ.get('JARVIS_BLENDER_WORKSPACE')})")
    while True:
        conn, _ = srv.accept()
        with conn:
            try:
                req = json.loads(conn.recv(65536).decode() or "{}")
                reply = {"event": threading.Event()}
                _jobs.put((req, reply))
                reply["event"].wait(timeout=180)
                resp = reply.get("result", {"ok": False, "error": "timed out"})
            except Exception as e:
                resp = {"ok": False, "error": str(e)}
            conn.sendall((json.dumps(resp) + "\n").encode())


def start() -> None:
    if bpy is None:
        raise RuntimeError("Run inside Blender (bpy unavailable).")
    if not os.environ.get("JARVIS_BLENDER_TOKEN"):
        raise RuntimeError("Set JARVIS_BLENDER_TOKEN (shared secret) before starting.")
    os.environ.setdefault("JARVIS_BLENDER_WORKSPACE",
                          str(Path.home() / "ECC" / "jarvis" / "data" / "blender-workspace"))
    Path(os.environ["JARVIS_BLENDER_WORKSPACE"]).mkdir(parents=True, exist_ok=True)
    bpy.app.timers.register(_pump)
    threading.Thread(target=_serve, daemon=True).start()


if __name__ == "__main__":
    start()
