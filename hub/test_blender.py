"""Security tests for the hardened Blender integration (PR #11).

Covers the three defects the review flagged:
  1. the MCP action wrappers must NOT run without a real owner grant (default-deny),
  2. the Blender socket worker must authenticate and confine every path,
  3. a model-provided confirm / MCP annotation is never accepted as authorization.

Pure helpers are tested with a fake `bpy` so no GUI/Blender is needed here; the real
Blender run is hub/blender_selftest.py (headless), shown separately.
"""
import importlib

import pytest

import approval
import blender_worker as W


# --------------------------------------------------------------- approval boundary
@pytest.fixture
def iso_state(tmp_path, monkeypatch):
    """Point the approval store at a throwaway dir so tests never touch real state."""
    monkeypatch.setattr(approval, "STATE", tmp_path)
    monkeypatch.setattr(approval, "STORE", tmp_path / "approvals.json")
    monkeypatch.setattr(approval, "AUDIT", tmp_path / "audit.log")
    return tmp_path


def test_action_denied_without_grant(iso_state):
    ok, info = approval.check_and_consume("render_preview", {"path": "a.png"})
    assert ok is False
    assert info["status"] == "denied_needs_approval"
    assert info["approve_with"].startswith("python hub/approve.py ")


def test_grant_then_consume_single_use(iso_state):
    # deny once (records a pending request), owner grants it, next call consumes it
    ok, info = approval.check_and_consume("render_preview", {"path": "a.png"})
    assert ok is False
    rid = info["approve_with"].split()[-1]
    granted, _ = approval.grant(rid)
    assert granted
    ok2, _ = approval.check_and_consume("render_preview", {"path": "a.png"})
    assert ok2 is True
    # single use: a second attempt is denied again
    ok3, _ = approval.check_and_consume("render_preview", {"path": "a.png"})
    assert ok3 is False


def test_grant_is_bound_to_exact_args(iso_state):
    ok, info = approval.check_and_consume("render_preview", {"path": "a.png"})
    rid = info["approve_with"].split()[-1]
    approval.grant(rid)
    # a grant for path=a.png must NOT authorize path=b.png (different fingerprint)
    ok_other, _ = approval.check_and_consume("render_preview", {"path": "b.png"})
    assert ok_other is False
    # the original args still consume successfully
    ok_same, _ = approval.check_and_consume("render_preview", {"path": "a.png"})
    assert ok_same is True


def test_grant_expires(iso_state, monkeypatch):
    import time
    ok, info = approval.check_and_consume("export_glb", {"path": "s.glb"})
    rid = info["approve_with"].split()[-1]
    approval.grant(rid)
    # jump past the TTL; an expired grant is not honored
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + approval.DEFAULT_TTL + 10)
    ok2, _ = approval.check_and_consume("export_glb", {"path": "s.glb"})
    assert ok2 is False


def test_unknown_request_id_rejected(iso_state):
    ok, msg = approval.grant("deadbeef")
    assert ok is False and "no pending" in msg


# --------------------------------------------------------------- token / confinement
def test_check_token():
    assert W.check_token("s3cret", "s3cret") is True
    assert W.check_token("wrong", "s3cret") is False
    assert W.check_token("", "s3cret") is False
    assert W.check_token("anything", "") is False      # no expected secret => always fail


def test_confine_accepts_inside(tmp_path):
    dest = W.confine("sub/preview.png", str(tmp_path))
    assert dest is not None
    assert str(dest).startswith(str(tmp_path.resolve()))


def test_confine_rejects_traversal(tmp_path):
    assert W.confine("../../escape.png", str(tmp_path)) is None


def test_confine_rejects_absolute_outside(tmp_path):
    outside = str(tmp_path.parent / "elsewhere.png")
    assert W.confine(outside, str(tmp_path)) is None


# --------------------------------------------------------------- handle() with fake bpy
class _Obj:
    def __init__(self, name, type_, loc=(0, 0, 0)):
        self.name, self.type, self.location = name, type_, list(loc)


def _fake_bpy():
    """Just enough bpy surface for handle(): status/list only touch data/context/app."""
    from types import SimpleNamespace
    scene = SimpleNamespace(name="Scene", render=SimpleNamespace(filepath=""))
    return SimpleNamespace(
        app=SimpleNamespace(version_string="5.2.0 (fake)"),
        context=SimpleNamespace(scene=scene),
        data=SimpleNamespace(objects=[_Obj("Cube", "MESH"), _Obj("Camera", "CAMERA", (4, -4, 3))]),
    )


def test_handle_rejects_bad_token(tmp_path):
    res = W.handle({"op": "status", "token": "nope"}, _fake_bpy(), str(tmp_path), "real")
    assert res == {"ok": False, "error": "unauthorized"}


def test_handle_rejects_unknown_op(tmp_path):
    res = W.handle({"op": "rm_rf", "token": "t"}, _fake_bpy(), str(tmp_path), "t")
    assert res["ok"] is False and "unsupported" in res["error"]


def test_handle_status_and_list(tmp_path):
    st = W.handle({"op": "status", "token": "t"}, _fake_bpy(), str(tmp_path), "t")
    assert st["ok"] and st["blender"].startswith("5.2")
    assert "Cube" in st["objects"]
    ls = W.handle({"op": "list", "token": "t"}, _fake_bpy(), str(tmp_path), "t")
    assert ls["ok"] and {o["type"] for o in ls["objects"]} == {"MESH", "CAMERA"}


def test_handle_render_path_escape_rejected(tmp_path):
    res = W.handle({"op": "render", "path": "../../pwn.png", "token": "t"},
                   _fake_bpy(), str(tmp_path), "t")
    assert res["ok"] is False and "escapes" in res["error"]


def test_handle_export_requires_gltf_extension(tmp_path):
    res = W.handle({"op": "export", "path": "scene.png", "token": "t"},
                   _fake_bpy(), str(tmp_path), "t")
    assert res["ok"] is False and (".glb" in res["error"] or ".gltf" in res["error"])


# --------------------------------------------------------------- MCP wrappers default-deny
def test_blender_mcp_wrappers_default_deny(iso_state):
    pytest.importorskip("mcp")
    bm = importlib.import_module("blender_mcp")
    # render_preview / export_glb must refuse without an owner grant, even though an MCP
    # client could set any confirm/annotation. They never reach the worker when denied.
    r = bm.render_preview("preview.png")
    assert r.get("status") == "denied_needs_approval"
    e = bm.export_glb("scene.glb")
    assert e.get("status") == "denied_needs_approval"


def test_hub_mcp_action_default_deny_and_hybrid_task_requires_approval(iso_state, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.delenv("HUB_ENABLE_PC_TASK", raising=False)
    ms = importlib.import_module("mcp_server")
    # open_app is an action: denied without a grant (does not launch anything)
    assert ms.open_app("Notepad").get("status") == "denied_needs_approval"
    # Hybrid intelligence stays available, but MCP action approval is still enforced.
    called = []
    monkeypatch.setattr(ms.H, "run_pc_task", lambda task: called.append(task))
    assert ms.run_pc_task("do something")["status"] == "denied_needs_approval"
    assert not called
