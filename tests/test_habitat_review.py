"""Read-only issue84 review regressions: desired safety invariants, currently red.

No desktop input, native mutation, UIA worker, real files or live assistant calls.
Run: python -B -m pytest tests/test_habitat_review.py -q -p no:cacheprovider
Failures are deliberately unmarked so the parent can reproduce and fix them.
"""
from types import SimpleNamespace

import pytest

from core.companion import Monitor
from core import desktop_habitat as habitat


MONITORS = [Monitor(0, 0, 1920, 1080)]


def cached_probe(monkeypatch, surfaces=(), bars=()):
    """Pin cache time and bypass all native calls and worker creation."""
    monkeypatch.setattr(habitat.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(habitat, "_taskbars", lambda: list(bars))
    probe = habitat.ShellProbe()
    probe._at = 100.0
    probe._uia = list(surfaces)
    return probe


def host_api(monkeypatch, probe, favorite=None):
    """Exercise the real host selection method with in-memory dependencies."""
    from hud import app

    api = object.__new__(app.Api)
    api._expanded = None
    api._shell_probe = probe
    api._shell_snapshot = {"surfaces": [], "protected": []}
    api._protected = []
    api._fav_surface = favorite
    api._habitat = None
    api._companion_hwnd = lambda: 999
    api._dispatch = lambda message: None
    monkeypatch.setattr(app, "_window_rect", lambda hwnd: (20, 20, 100, 100))
    monkeypatch.setattr(app, "_full_monitors", lambda: [(0, 0, 1920, 1080)])
    return app, api


def test_favorite_folder_hidden_by_application_is_not_selected(monkeypatch):
    folder = habitat.Surface("folder:review", "folder", (400, 400, 80, 80))
    probe = cached_probe(monkeypatch, [folder])
    app, api = host_api(monkeypatch, probe, favorite=folder.id)
    monkeypatch.setattr(app, "_enum_app_windows", lambda ours: [(1, (0, 0, 1920, 1080), "synthetic cover")])
    result = api.companion_habitat("favorite")
    assert not result["ok"], "favorite bypasses folder occlusion because blocked is populated only for explicit kind=folder"


def test_cached_search_inherits_current_taskbar_autohide(monkeypatch):
    bar = habitat.Surface("bar", "taskbar", (0, 1040, 1920, 40), auto_hide=True)
    search = habitat.Surface("search:bar", "search", (600, 1040, 240, 40), auto_hide=False)
    probe = cached_probe(monkeypatch, [search], [bar])
    snapshot = probe.snapshot()
    anchor = habitat.choose_anchor(snapshot["surfaces"], (100, 100), MONITORS, kind="search")
    assert anchor is None, "cached search still anchors despite its owning taskbar now being auto-hide"


def test_window_seat_is_not_inside_small_higher_window(monkeypatch):
    probe = cached_probe(monkeypatch)
    # The cover obscures only 8% of the lower window's top ledge, but all
    # of the center seat. It is too narrow to become a surface itself.
    cover = (560, 300, 80, 200)
    lower = (100, 400, 1000, 400)
    snapshot = probe.snapshot([(1, cover), (2, lower)])
    anchor = habitat.choose_anchor(snapshot["surfaces"], (100, 100), MONITORS, kind="window")
    if anchor:
        box = (anchor["x"], anchor["y"], 100, 100)
        assert habitat._overlap(box, cover) == 0, "ledge-wide 20% threshold allows a fully obstructed chosen seat"


def test_explicit_selection_publishes_protected_controls_immediately(monkeypatch):
    bar = habitat.Surface("bar", "taskbar", (0, 1040, 1920, 40))
    probe = cached_probe(monkeypatch, bars=[bar])
    app, api = host_api(monkeypatch, probe)
    monkeypatch.setattr(app, "_enum_app_windows", lambda ours: [])
    assert api.companion_habitat("taskbar")["ok"]
    assert bar.rect in api._protected, "selection dispatches movement with stale/empty protection until next presence poll"


@pytest.mark.parametrize("size", [(100, 100), (400, 400)])
def test_supported_size_does_not_move_contact_plane_off_real_taskbar(size):
    bar = habitat.Surface("bar", "taskbar", (0, 1040, 1920, 40))
    anchor = habitat.anchor_for(bar, size, MONITORS)
    # This is the function's own nominal seat-contact convention, not a
    # claim that 72% matches the rig's projected pelvis on the desktop.
    if anchor:
        assert anchor["y"] + int(size[1] * .72) == bar.rect[1], "monitor clamp silently moves the intended contact off the real ledge; reject or adapt pose"


def test_offscreen_cached_folder_is_not_clamped_into_fake_surface():
    folder = habitat.Surface("folder:moved", "folder", (1900, 400, 20, 80))
    anchor = habitat.anchor_for(folder, (100, 100), MONITORS)
    if anchor:
        # Nominal peek alignment must survive bounds handling, or reject.
        assert anchor["x"] == folder.rect[0] + folder.rect[2] - 32, "clamp changes peek contact/occlusion alignment instead of declining the surface"


def test_explicit_folder_selection_rejects_large_cover_control(monkeypatch):
    """Positive control: the same covered folder is rejected in explicit mode."""
    folder = habitat.Surface("folder:review", "folder", (400, 400, 80, 80))
    probe = cached_probe(monkeypatch, [folder])
    app, api = host_api(monkeypatch, probe)
    monkeypatch.setattr(app, "_enum_app_windows", lambda ours: [(1, (0, 0, 1920, 1080), "synthetic cover")])
    assert not api.companion_habitat("folder")["ok"]
