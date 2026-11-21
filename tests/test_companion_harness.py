"""Harness-level tests that do NOT open pywebview.

The harness itself must behave correctly even before any native window is
opened: the FastAPI /control endpoint must validate events, the info-file
must contain the fields Codex expects, and the pump must forward only
schema-valid events to the HUD queue.

A live-smoke test (open the actual window) is intentionally not part of
pytest; see scripts/companion_harness.py for operator instructions.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def harness_module(monkeypatch):
    """Import the harness fresh for each test (it owns a module-level queue
    and a shutdown flag that must not leak between tests)."""
    import importlib, sys
    sys.modules.pop("scripts.companion_harness", None)
    import scripts.companion_harness as h
    importlib.reload(h)
    return h


def test_control_endpoint_validates_events(harness_module):
    from fastapi.testclient import TestClient
    app = harness_module._build_fastapi(ROOT / "app")
    client = TestClient(app)

    # Legit events are queued and acknowledged
    r = client.post("/control", json={"type": "state", "value": "listening"})
    assert r.status_code == 200 and r.json() == {"ok": True, "type": "state"}

    r = client.post("/control", json={"type": "level", "value": 0.42})
    assert r.status_code == 200

    # Malformed events are rejected with 400
    for bad in [
        {"type": "state", "value": "flying"},
        {"type": "level", "value": "not-a-number"},
        {"type": "line", "who": "bot", "text": "x"},
        {"type": "weird"},
        {"nope": True},
    ]:
        r = client.post("/control", json=bad)
        assert r.status_code == 400, bad

    # The shutdown sentinel is accepted even though it isn't in the schema
    r = client.post("/control", json={"type": "quit"})
    assert r.status_code == 200

    # The queued count matches what we accepted
    stats = client.get("/stats").json()
    assert stats["queued"] >= 3
    assert stats["rejected"] >= 5


def test_control_endpoint_queue_full_returns_503(harness_module, monkeypatch):
    from fastapi.testclient import TestClient
    app = harness_module._build_fastapi(ROOT / "app")
    client = TestClient(app)

    # Fill the harness queue to the brim
    for _ in range(harness_module._control_q.maxsize):
        harness_module._control_q.put_nowait({"type": "state", "value": "idle"})

    r = client.post("/control", json={"type": "state", "value": "idle"})
    assert r.status_code == 503
    assert "full" in r.json()["detail"].lower()


def test_static_mount_serves_the_companion_page(harness_module):
    from fastapi.testclient import TestClient
    app = harness_module._build_fastapi(ROOT / "app")
    client = TestClient(app)

    r = client.get("/companion.html")
    assert r.status_code == 200
    body = r.text
    assert "window.companion" in body
    assert "/companion/behavior.js" in body

    r = client.get("/companion/behavior.js")
    assert r.status_code == 200
    assert "export class CompanionState" in r.text


def test_info_file_shape(tmp_path, harness_module):
    info_path = tmp_path / "harness-info.json"
    harness_module._write_info(info_path, pid=12345, port=65123)
    data = json.loads(info_path.read_text(encoding="utf-8"))
    for key in ("harness_version", "pid", "window_title", "port",
                "control_url", "healthz_url", "stats_url",
                "static_root", "companion_url", "started_at"):
        assert key in data, key
    assert data["pid"] == 12345
    assert data["port"] == 65123
    assert data["control_url"].endswith("/control")
    assert data["window_title"] == "Jarvis (harness)"   # disambiguated from live Jarvis


def test_pick_free_port_is_in_range(harness_module):
    port = harness_module._pick_free_port()
    assert 1024 < port < 65536


# ---------------------------- hud/app.py pump: resume defect fix ---------- #

def test_create_companion_window_uses_valid_6char_hex_for_transparent_path():
    """pywebview 6.2.1 rejects 8-char RGBA strings as background_color. The
    transparent branch must pass a 6-char triplet AND transparent=True; the
    opaque fallback uses the known-good #03070c. Caught in the live harness
    log: 'transparent window failed (#00000000 is not a valid hex triplet
    color)'. Guard the regression here so the mistake can't come back."""
    src = (ROOT / "hud" / "app.py").read_text(encoding="utf-8")
    import re
    # The 8-char RGBA form must not appear in any background_color= argument
    # (it may still appear in a comment explaining why it's wrong).
    assert not re.search(r'background_color\s*=\s*"#[0-9a-fA-F]{8}"', src), \
        "8-char hex RGBA is rejected by pywebview 6.2.1 as background_color"
    # The transparent-window branch must pass a 6-char hex AND transparent=True.
    tbranch = re.search(r'background_color="#000000"[^)]*transparent=True', src)
    assert tbranch, "transparent window must use 6-char hex + transparent=True"


def test_hud_pump_pairs_renderer_resume_with_suspend():
    """Earlier revision: pump's 'show' branch only ran window.show() and left
    the renderer suspended. The current additive architecture has split the
    renderer visibility onto its own event pair (companion_show /
    companion_hide), and the show/hide pair now only governs the panel. The
    guard here checks the intent (resume paired with suspend somewhere in the
    pump) without pinning the exact structure: as long as suspend() appears
    somewhere in a hide-shaped branch, resume() must appear in the matching
    show-shaped branch."""
    import ast
    tree=ast.parse((ROOT / "hud" / "app.py").read_text(encoding="utf-8"))
    pump=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=="pump")
    paired={}
    for node in ast.walk(pump):
        if not isinstance(node,ast.If): continue
        values={n.value for n in ast.walk(node.test) if isinstance(n,ast.Constant) and isinstance(n.value,str)}
        for event in values & {"companion_show","companion_hide"}:
            paired[event]=[call.args[0].value for statement in node.body for call in ast.walk(statement)
                if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=="_companion_scheduler"
                and call.args and isinstance(call.args[0],ast.Constant)]
    assert "resume" in paired.get("companion_show",[])
    assert "suspend" in paired.get("companion_hide",[])


# ---------------------------- demo script sanity ---------- #

def test_demo_script_only_contains_validatable_events(harness_module):
    from core import companion as c
    for delay, ev in harness_module.DEMO_SCRIPT:
        assert isinstance(delay, (int, float)) and delay >= 0
        assert c.validate_event(ev) is not None, ev


def test_demo_script_exercises_the_full_lifecycle_and_hide_show(harness_module):
    """The scripted sequence must hit idle, listening, thinking, speaking,
    level pulses, back to idle, companion_hide, companion_show — otherwise
    the smoke test cannot catch the resume defect or the stale-level timeout.
    hide/show on the companion target must use the companion_* events so
    the owner's panel isn't disturbed."""
    seen_states = {ev["value"] for _, ev in harness_module.DEMO_SCRIPT if ev["type"] == "state"}
    assert {"idle", "listening", "thinking", "speaking"}.issubset(seen_states)
    types = [ev["type"] for _, ev in harness_module.DEMO_SCRIPT]
    assert "level" in types and "companion_hide" in types and "companion_show" in types
    assert types.index("companion_hide") < types.index("companion_show"), \
        "companion_hide must come before companion_show"
    # No raw hide/show in the demo — those are panel-targeted and the harness
    # must not touch the panel's visibility in the standard script.
    assert "hide" not in types and "show" not in types, \
        "demo script must use companion_hide/companion_show, not panel hide/show"


# ---------------------------- diagnostics endpoint ---------- #

def test_renderer_state_endpoint_shape_before_and_after_snapshot(harness_module):
    """GET /renderer-state must return available=False when no snapshot has
    been pushed, and the pushed dict after a snapshot arrives."""
    from fastapi.testclient import TestClient
    # Inject a module-level shared dict so the endpoint has something to read.
    harness_module._shared_state = {}
    app = harness_module._build_fastapi(ROOT / "app")
    client = TestClient(app)

    r = client.get("/renderer-state")
    assert r.status_code == 200
    assert r.json()["available"] is False

    harness_module._shared_state["renderer"] = {
        "mode": "speaking", "jaw": 0.6, "level": 0.6, "frames": 128,
        "running": True, "suspended": False, "had_webgl": True,
    }
    r = client.get("/renderer-state")
    body = r.json()
    assert body["available"] is True
    assert body["mode"] == "speaking"
    assert body["jaw"] == 0.6
    assert body["frames"] == 128


def test_control_accepts_companion_hide_and_show(harness_module):
    """companion_hide / companion_show are the two new events the harness
    uses to toggle the companion window independently of the panel."""
    from fastapi.testclient import TestClient
    app = harness_module._build_fastapi(ROOT / "app")
    client = TestClient(app)
    assert client.post("/control", json={"type": "companion_hide"}).status_code == 200
    assert client.post("/control", json={"type": "companion_show"}).status_code == 200


# ---------------------------- Diagnostics / jaw default ---------- #

def test_renderer_closes_mouth_at_idle_by_construction():
    """The face overlay must start with the mouth mesh hidden (the model's baked vent carries the idle pose);
    glb-face.js owns the amplitude threshold that shows it."""
    face = (ROOT / "app" / "companion" / "glb-face.js").read_text(encoding="utf-8")
    assert "mouth.visible = false" in face, "mouth mesh must start hidden"
    assert "visible: open > 0.04" in face, "closed below an amplitude threshold"


def test_renderer_auto_fit_camera_hook_exists():
    """The build/resize path must call fitCameraToRig so Codex's 'head top
    and body bottom clipped in 304x281' observation can't recur."""
    html = (ROOT / "app" / "companion.html").read_text(encoding="utf-8")
    assert "function fitCameraToRig" in html
    assert "fitCameraToRig()" in html


def test_renderer_pushes_diagnostics_snapshot():
    """The renderer must periodically push its state to Python so the
    harness /renderer-state endpoint has something to read."""
    html = (ROOT / "app" / "companion.html").read_text(encoding="utf-8")
    assert "push_renderer_snapshot" in html
    # Snapshot must include the fields Codex (or any observer) needs.
    for needle in ("mode:", "jaw:", "level:", "frames:", "running:", "suspended:", "had_webgl:"):
        assert needle in html, needle


def test_companion_page_boots_exactly_once_and_reports_one_robot():
    """pywebviewready AND DOMContentLoaded both used to call boot(): two renderers built two robots into the same
    scene (one animated, one frozen) - 'two robots on top of each other'. Boot is guarded and the snapshot reports
    the boot count and the number of skinned meshes so the harness can assert both are 1."""
    html = (ROOT / "app" / "companion.html").read_text(encoding="utf-8")
    assert "const bootOnce" in html and 'addEventListener("pywebviewready", bootOnce)' in html
    assert 'addEventListener("pywebviewready", boot)' not in html and 'addEventListener("DOMContentLoaded", boot)' not in html
    assert "boots: bootCount" in html and "skinned_meshes:" in html
