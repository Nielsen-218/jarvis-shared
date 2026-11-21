"""Mark VI C9: unattended = read-only allowlist; everything else parks; the guard is per thread."""
import threading

from core import missionpolicy as mp
from tools import registry


def test_read_only_runs_and_changes_park():
    assert mp.unattended("get_system_status", {}) and mp.unattended("security_checkup", {})
    for name in ("pc_cleanup", "open_app", "read_screen", "control_pc", "install_program", "add_to_list", "remember"):
        assert not mp.unattended(name, {}), name
    assert not mp.unattended("where_is_rigged_character", {"open_folder": True})
    assert mp.unattended("where_is_rigged_character", {"open_folder": False})


def test_the_guard_parks_the_first_risky_step_and_its_body_never_runs(monkeypatch):
    ran = []
    registry._TOOLS["fake_cleanup"] = {"fn": lambda **a: ran.append("cleanup") or "deleted", "slow": False,
                                       "schema": {"name": "fake_cleanup", "input_schema": {"type": "object", "properties": {}}}}
    registry._TOOLS["get_time_fake"] = {"fn": lambda **a: "noon", "slow": False,
                                        "schema": {"name": "get_time_fake", "input_schema": {"type": "object", "properties": {}}}}
    monkeypatch.setattr(mp, "UNATTENDED_TOOLS", mp.UNATTENDED_TOOLS | {"get_time_fake"})
    monkeypatch.setattr(registry, "validate", lambda n, a: None)
    ctx = mp.MissionContext()
    try:
        with registry.guarded(ctx.guard):
            assert registry.run("get_time_fake", {}) == ("noon", False)
            out, err = registry.run("fake_cleanup", {})
        assert err and out.startswith("PARKED") and ran == [] and ctx.parked == {"tool": "fake_cleanup", "arguments": {}}
        approved = mp.MissionContext(approved=mp.fingerprint("fake_cleanup", {}))
        with registry.guarded(approved.guard):
            assert registry.run("fake_cleanup", {}) == ("deleted", False)        # his OK: that exact step, once
            assert registry.run("fake_cleanup", {})[0].startswith("PARKED")      # never twice
        assert ran == ["cleanup"]
    finally:
        registry._TOOLS.pop("fake_cleanup", None)
        registry._TOOLS.pop("get_time_fake", None)


def test_the_guard_never_touches_another_thread(monkeypatch):
    seen = []
    ctx = mp.MissionContext()
    with registry.guarded(ctx.guard):
        t = threading.Thread(target=lambda: seen.append(getattr(registry._guard, "fn", None)))
        t.start()
        t.join()
    assert seen == [None] and getattr(registry._guard, "fn", None) is None


def test_typed_steps_only_observe():
    assert mp.unattended_step({"tool": "pc_status", "arguments": {}})
    assert not mp.unattended_step({"tool": "open_app", "arguments": {"app": "notepad"}})
    assert not mp.unattended_step({"tool": "close_app", "arguments": {"app": "notepad"}})
