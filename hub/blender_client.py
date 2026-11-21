#!/usr/bin/env python3
"""Authenticated client to the in-Blender worker (hub/blender_worker.py).

Loopback only. Sends the shared token (JARVIS_BLENDER_TOKEN) with every request; if the
worker isn't running, returns a clear, non-crashing message.
"""
import json
import os
import socket

PORT = int(os.environ.get("JARVIS_BLENDER_PORT", "9436"))


def call(op: str, **kw) -> dict:
    token = os.environ.get("JARVIS_BLENDER_TOKEN", "")
    if not token:
        return {"ok": False, "available": False,
                "error": "JARVIS_BLENDER_TOKEN is not set (shared secret for the Blender worker)."}
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=5) as s:
            s.sendall((json.dumps({"op": op, "token": token, **kw}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n") and len(data) < 5_000_000:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode() or "{}")
    except (OSError, ConnectionError, ValueError):
        return {"ok": False, "available": False,
                "error": f"Blender worker not reachable on 127.0.0.1:{PORT}. "
                         f"Open Blender and run hub/blender_worker.py."}
