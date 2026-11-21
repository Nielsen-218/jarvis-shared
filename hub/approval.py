#!/usr/bin/env python3
"""Enforced, out-of-band approval boundary for the JARVIS Control Hub.

MCP `ToolAnnotations` and a model-supplied `confirm=True` are NOT authorization — a
permissive client could set them. Real approval here is a grant that the OWNER creates
out of band (`python hub/approve.py <id>`), bound to the EXACT action + arguments,
single-use and short-lived. Action tools call `check_and_consume(action, args)`:
  - a matching, unexpired, unused grant -> consumed, returns (True, {})
  - otherwise -> a pending request is recorded and (False, {how-to-approve}) is returned.
Read-only tools never call this. Every decision is appended to an audit log.

Threat model (honest): this defends against a permissive/remote MCP client auto-invoking
actions — notably ChatGPT, which has no shell on the PC and genuinely cannot self-approve.
A local client that ALSO has arbitrary shell access could run approve.py itself; that is
true of any local approval, which is why remote scope is Blender-only and read-first, and
why render/export paths are independently confined in the worker.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

STATE = Path(__file__).resolve().parent / "state"
STORE = STATE / "approvals.json"
AUDIT = STATE / "audit.log"
DEFAULT_TTL = int(os.getenv("HUB_APPROVAL_TTL", "300"))  # seconds a grant stays valid


def fingerprint(action: str, args: dict | None) -> str:
    blob = action + "|" + json.dumps(args or {}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _load() -> dict:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        d.setdefault("pending", {})
        d.setdefault("granted", {})
        return d
    except (FileNotFoundError, ValueError):
        return {"pending": {}, "granted": {}}


def _save(d: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, STORE)


def audit(event: str, **fields) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    try:
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


def request_approval(action: str, args: dict | None = None, ttl: int = DEFAULT_TTL) -> str:
    """Record a pending request; returns its id (the owner approves it by id)."""
    d = _load()
    rid = uuid.uuid4().hex[:8]
    d["pending"][rid] = {"action": action, "args": args or {}, "fp": fingerprint(action, args),
                         "created": time.time(), "ttl": ttl}
    d["pending"] = {k: v for k, v in d["pending"].items() if time.time() - v["created"] < 3600}
    _save(d)
    audit("requested", id=rid, action=action, args=args)
    return rid


def grant(request_id: str) -> tuple[bool, str]:
    """Owner-only: approve a pending request (creates a single-use, time-boxed grant)."""
    d = _load()
    p = d["pending"].pop(request_id, None)
    if not p:
        return False, f"no pending request {request_id}"
    d["granted"][p["fp"]] = {"at": time.time(), "ttl": p["ttl"], "action": p["action"], "args": p["args"]}
    _save(d)
    audit("granted", id=request_id, action=p["action"], fp=p["fp"])
    return True, f"approved {p['action']} (valid {p['ttl']}s, single use)"


def list_pending() -> dict:
    d = _load()
    return {k: {"action": v["action"], "args": v["args"],
                "age_s": round(time.time() - v["created"])} for k, v in d["pending"].items()}


def check_and_consume(action: str, args: dict | None = None) -> tuple[bool, dict]:
    """Action gate. Consume a matching valid grant, or record a pending request and deny."""
    fp = fingerprint(action, args)
    d = _load()
    g = d["granted"].get(fp)
    if g and (time.time() - g["at"] < g["ttl"]):
        del d["granted"][fp]            # single use
        _save(d)
        audit("consumed", action=action, fp=fp)
        return True, {}
    if g:                               # expired
        del d["granted"][fp]
        _save(d)
    rid = request_approval(action, args)
    audit("denied", action=action, fp=fp, id=rid)
    return False, {"status": "denied_needs_approval", "action": action, "args": args or {},
                   "fingerprint": fp, "approve_with": f"python hub/approve.py {rid}",
                   "note": "An owner must run the approve command in a terminal. "
                           "A model-provided confirmation is not accepted."}
