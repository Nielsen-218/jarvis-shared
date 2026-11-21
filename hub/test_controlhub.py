"""Tests for the Universal Control Hub core (Issue: JARVIS Universal Control Hub).

Fully mocked: no app launches, no volume changes, no real browser/computer-use, no
screen capture side effects. Proves the read/action permission split and the gate.
"""
from pathlib import Path

import controlhub as H


# ---- registry + permission split ----
def test_every_tool_has_a_permission_kind():
    for t in H.TOOLS:
        assert isinstance(t["read"], bool) and isinstance(t["destructive"], bool)


def test_read_vs_action_classification():
    kind = {t["name"]: t["read"] for t in H.TOOLS}
    for name in ("get_pc_status", "take_screenshot", "list_apps", "get_volume"):
        assert kind[name] is True, f"{name} must be read-only"
    for name in ("open_app", "close_app", "set_volume", "open_url", "run_pc_task", "blender_render"):
        assert kind[name] is False, f"{name} must be an action"
    destructive = {t["name"] for t in H.TOOLS if t["destructive"]}
    assert destructive == {"close_app", "run_pc_task"}


# ---- the gate ----
def test_action_needs_confirmation_and_does_not_run(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr("tools.apps.open_app", lambda app: calls.__setitem__("n", calls["n"] + 1) or "Launched")
    res = H.run_tool("open_app", {"app": "notepad"})           # no confirm
    assert res["status"] == "needs_confirmation" and res["tool"] == "open_app" and calls["n"] == 0
    res2 = H.run_tool("open_app", {"app": "notepad"}, confirm=True)  # approved
    assert res2 == {"result": "Launched"} and calls["n"] == 1


def test_read_tool_runs_without_confirm(monkeypatch):
    monkeypatch.setattr("tools.apps.list_apps", lambda contains: "notepad, chrome")
    assert H.run_tool("list_apps", {"contains": "note"}) == {"apps": "notepad, chrome"}


def test_unknown_tool():
    assert "error" in H.run_tool("frobnicate")


def test_bad_arguments_reported(monkeypatch):
    monkeypatch.setattr("tools.media.volume", lambda p: "ok")
    assert "error" in H.run_tool("set_volume", {"wrong": 1}, confirm=True)


# ---- individual tools (mocked backends) ----
def test_get_pc_status_shape():
    st = H.get_pc_status()
    assert "cpu_percent" in st and "memory_percent" in st and "disk_free_gb" in st
    assert isinstance(st["top_processes"], list)


def test_open_and_close_app_reuse_tools(monkeypatch):
    monkeypatch.setattr("tools.apps.open_app", lambda app: f"Launched {app}")
    monkeypatch.setattr("tools.apps.close_app", lambda app: f"Closed {app}")
    assert H.open_app("spotify") == {"result": "Launched spotify"}
    assert H.close_app("spotify") == {"result": "Closed spotify"}


def test_set_and_get_volume_reuse_media(monkeypatch):
    monkeypatch.setattr("tools.media.volume", lambda p: f"Volume is {max(0, p)}%")
    assert H.set_volume(40) == {"result": "Volume is 40%"}
    assert H.get_volume() == {"volume": "Volume is 0%"}


def test_open_url_reuse_system(monkeypatch):
    monkeypatch.setattr("tools.system.open_website", lambda url: f"Opened {url}")
    assert H.open_url("https://x.com") == {"result": "Opened https://x.com"}


def test_run_pc_task_reuse_pcview(monkeypatch):
    monkeypatch.setattr("tools.pcview.control_pc", lambda task: "Did it.")
    assert H.run_pc_task("open spotify") == {"result": "Did it."}


def test_take_screenshot_writes_file(monkeypatch, tmp_path):
    class FakeImg:
        width, height = 1600, 900
        def convert(self, m): return self
        def thumbnail(self, size): pass
        def save(self, path, fmt, quality=80): Path(path).write_bytes(b"\xff\xd8jpg")
    import PIL.ImageGrab
    monkeypatch.setattr(PIL.ImageGrab, "grab", lambda all_screens=False: FakeImg())
    monkeypatch.setattr(H, "STATE", tmp_path)
    out = H.take_screenshot(False)
    assert out["width"] == 1600 and Path(out["path"]).exists()


def test_blender_unavailable_when_not_running(monkeypatch):
    # routes through the authenticated client now: with a token set but no worker
    # listening, it reports unavailable honestly (no crash) instead of writing anything.
    monkeypatch.setenv("JARVIS_BLENDER_TOKEN", "test-token")
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: (_ for _ in ()).throw(ConnectionRefusedError()))
    res = H.blender_render("render")
    assert res.get("available") is False and "Blender" in res["error"]


def test_blender_requires_token(monkeypatch):
    # no token -> the client refuses up front; it never contacts an unauthenticated worker
    monkeypatch.delenv("JARVIS_BLENDER_TOKEN", raising=False)
    res = H.blender_render("status")
    assert res.get("available") is False and "JARVIS_BLENDER_TOKEN" in res["error"]
