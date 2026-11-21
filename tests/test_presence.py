"""Living Presence Engine 2.0 (core/presence.py + the hud/app.py host + the skills catalogue and endpoints).

The companion used to flee whenever the cursor entered his window — exactly when the user reached for him. These pin
the new model: interactions lease his position, placement moves him only when he is really in the way (never for
the cursor alone) and only to a clearly better spot, his menu grows the window beside him without moving him, and
his Skills menu reads (and runs) the real registered tools.
"""
import json
import time

import pytest

import core.companion as c
from core import presence as p

M = [c.Monitor(0, 0, 1920, 1080)]
TWO = [c.Monitor(0, 0, 1920, 1080), c.Monitor(1920, 0, 1920, 1080)]
SIZE = (87, 87)
BR = (1920 - 87 - 12, 1080 - 87 - 12)        # bottom-right corner spot
BL = (12, 1080 - 87 - 12)


def scene(**kw):
    base = dict(monitors=tuple(M), size=SIZE, current=BR)
    base.update(kw)
    return p.Scene(**base)


# ---------------------------------------------------------------- leases
def test_a_lease_owns_his_position_and_lapses_if_not_renewed():
    lease = p.Lease()
    assert not lease.blocks(0.0)
    assert lease.grant("held", 2.5, 10.0) and lease.holder(11.0) == "held" and lease.blocks(11.0)
    assert lease.holder(13.0) is None                                  # not renewed: lapses (a dead page can't freeze him)
    assert not lease.grant("ambient", 2.5, 20.0)                       # resting states never own him
    lease.grant("thrown", 5, 20.0)
    lease.release("held", 1.0, 21.0)                                   # an OLD interaction's release...
    assert lease.holder(21.5) == "thrown"                              # ...doesn't end the newer one
    lease.release("thrown", 3.0, 22.0)
    assert lease.holder(22.1) is None and lease.blocks(24.0) and not lease.blocks(25.5)   # settles, then frees
    assert p.Lease().grant("menu_open", 999, 0.0) and p.Lease.__init__  # TTL clamped (see below)
    l2 = p.Lease(); l2.grant("menu_open", 999, 0.0)
    assert l2.holder(p.LEASE_MAX_TTL + 0.1) is None


# ---------------------------------------------------------------- what the user is doing
@pytest.mark.parametrize("kw, expected", [
    (dict(process="Code.exe", title="main.py - jarvis"), "coding"),
    (dict(process="WindowsTerminal.exe"), "coding"),
    (dict(process="chrome.exe", title="Inbox - Gmail"), "browsing"),
    (dict(process="chrome.exe", title="Inbox - Gmail", typing=True), "typing"),
    (dict(process="msedge.exe", title="Lo-fi beats - YouTube"), "video"),
    (dict(process="vlc.exe"), "video"),
    (dict(process="WINWORD.EXE"), "reading"),
    (dict(process="Teams.exe"), "meeting"),
    (dict(process="game.exe", fullscreen=True), "gaming"),
    (dict(process="explorer.exe", idle_seconds=90), "idle"),
    (dict(process="explorer.exe", idle_seconds=400), "away"),
    (dict(process="explorer.exe"), "other"),
])
def test_classify_activity(kw, expected):
    assert p.classify_activity(**kw) == expected


def test_activities_match_the_event_contract():
    assert set(p.ACTIVITIES) == set(c.ACTIVITIES)
    assert p.FOCUSED_ACTIVITIES <= set(p.ACTIVITIES)


# ---------------------------------------------------------------- placement
def test_the_cursor_alone_never_moves_him():
    """The old defect: hovering / reaching for him made him run away."""
    on_him = (BR[0] + 40, BR[1] + 40)
    plan = p.plan_placement(scene(cursor=on_him, user_active=True), now=100.0)
    assert plan.target is None and plan.reason == "clear"
    # and the compatibility wrapper agrees (maximized-window / cursor-on-him case included)
    assert c.plan_companion_move(pos=BR, size=SIZE, monitors=M, foreground=None, cursor=on_him, now=100.0) is None


def test_he_waits_while_the_mouse_is_reaching_for_him():
    """Even when a window has come over him: the mouse moving right up to him means the user may be about to grab
    him, so he doesn't walk off just then (he moves once the mouse has settled or gone)."""
    covered = scene(foreground=(1500, 800, 420, 280))
    near = (BR[0] - 60, BR[1] + 20)
    plan = p.plan_placement(scene(foreground=(1500, 800, 420, 280), cursor=near, user_active=True, pointing=True),
                            now=100.0)
    assert plan.target is None and plan.reason == "reaching"
    # the mouse merely resting there (typing, reading) or far away: he gets out of the way as before
    assert p.plan_placement(scene(foreground=(1500, 800, 420, 280), cursor=near, user_active=True), now=100.0).target
    far = (200, 200)
    assert p.plan_placement(scene(foreground=(1500, 800, 420, 280), cursor=far, user_active=True, pointing=True),
                            now=100.0).target
    assert p.plan_placement(covered, now=100.0).target
    # ...and text being typed right under him still wins over a nearby mouse
    typing = scene(foreground=(1000, 600, 920, 480), caret=(BR[0] + 40, BR[1] + 40), cursor=near,
                   user_active=True, pointing=True)
    assert p.plan_placement(typing, now=100.0).reason != "reaching"


def test_an_interaction_lease_makes_placement_wait():
    covered = scene(foreground=(1500, 800, 420, 280))
    assert p.plan_placement(covered, now=100.0).target is not None
    assert p.plan_placement(covered, now=100.0, lease_blocks=True).reason == "interaction"


def test_a_window_over_him_moves_him_to_a_clear_spot_near_by():
    plan = p.plan_placement(scene(current=(12, 12), foreground=(0, 0, 900, 900)), now=100.0)
    assert plan.target is not None and plan.reason == "obstructed"
    assert p._overlap(*plan.target, *SIZE, 0, 0, 900, 900) == 0       # clear of the window
    assert plan.target == BL                                          # the nearest good spot, not "farthest from the cursor"
    assert plan.style in ("walk", "hop")                              # a short trip: walk / hop, not a teleport


def test_hysteresis_no_jitter_between_equal_spots():
    """A maximized editor: he has to sit on it somewhere; a quiet bottom corner is as good as it gets, so he stays."""
    full = (0, 0, 1920, 1080)
    plan = p.plan_placement(scene(current=BR, foreground=full), now=100.0)
    assert plan.target is None and plan.reason in ("no-better-spot", "clear")


def test_maximized_window_he_leaves_the_title_bar_and_window_controls():
    full = (0, 0, 1920, 1080)
    top_right = (1920 - 87 - 12, 12)                                  # over min / max / close
    plan = p.plan_placement(scene(current=top_right, foreground=full), now=100.0)
    assert plan.target is not None
    assert plan.target[1] > 540                                       # down to the quiet bottom edge
    # the decision doesn't depend on the cursor
    assert p.plan_placement(scene(current=top_right, foreground=full, cursor=(10, 1000), user_active=True),
                            now=100.0).target[1] > 540


def test_he_moves_away_from_the_text_being_typed():
    win = (300, 200, 1300, 700)
    here = (1300 - 100, 800)                                           # inside the window near the bottom
    caret = (here[0] + 40, here[1] + 30)
    plan = p.plan_placement(scene(current=here, foreground=win, caret=caret), now=100.0)
    assert plan.target is not None
    tx, ty = plan.target
    assert ((tx + 43 - caret[0]) ** 2 + (ty + 43 - caret[1]) ** 2) ** 0.5 > p.CARET_RADIUS * 0.6


def test_cooldown_and_manual_placement_are_respected():
    covered = scene(current=(12, 12), foreground=(0, 0, 400, 400))
    assert p.plan_placement(covered, now=100.0, last_move=97.0).reason == "cooldown"
    assert p.plan_placement(covered, now=100.0, last_move=90.0).target is not None
    corner = scene(current=(12, 12), foreground=(80, 80, 400, 400))   # a sliver of him covered: not in the way
    assert p.plan_placement(corner, now=100.0).reason == "clear"
    partly = scene(current=(12, 12), foreground=(40, 40, 400, 400))   # half of him covered: in the way, but not badly
    assert p.plan_placement(partly, now=100.0).target is not None
    assert p.plan_placement(partly, now=100.0, manual_until=150.0).reason == "manual"   # he stays where the user put him
    assert p.plan_placement(covered, now=100.0, manual_until=150.0).target is not None   # fully covered: severe


def test_he_stays_on_his_monitor_when_it_has_room():
    plan = p.plan_placement(scene(monitors=tuple(TWO), current=(12, 12), foreground=(0, 0, 900, 900)), now=100.0)
    assert plan.target is not None and plan.target[0] < 1920


def test_the_users_own_spot_pulls_him_back():
    pref = (900, 1080 - 87 - 12)
    best = p.best_spot(scene(current=(12, 12), foreground=(0, 0, 400, 400), preferred=pref))
    assert best == pref


def test_background_windows_alone_dont_crowd_him():
    """He may sit on a window the user isn't using; only the ACTIVE window's content really matters."""
    walls = tuple((x, 0, 640, 1080) for x in (0, 640, 1280))          # three windows tile the screen
    plan = p.plan_placement(scene(current=(900, 500), foreground=(600, 300, 700, 500), others=walls), now=100.0)
    assert plan.target is not None and plan.dock == 0


def test_crowded_desktop_docks_him():
    """Every spot overlaps the active window's content: the least disruptive spot, smaller and translucent — or,
    when even that is mostly covered, tucked behind the screen edge so only a sliver shows."""
    small = (c.Monitor(0, 0, 300, 300),)
    s = p.Scene(small, SIZE, current=(100, 100), foreground=(0, 0, 280, 270))   # not maximized: all of it is content
    plan = p.plan_placement(s, now=100.0)
    assert plan.target is not None and plan.dock >= 1
    if plan.dock == 2:
        assert plan.target[0] < 0 or plan.target[0] + 87 > 300            # peeking past an edge


def test_undock_when_space_frees():
    plan = p.plan_placement(scene(current=(-40, 500)), now=100.0, docked=2)
    assert plan.reason == "undock" and plan.dock == 0 and plan.target is not None


def test_travel_styles():
    assert p.travel_style((0, 0), (200, 0), M) == "walk"
    assert p.travel_style((0, 0), (800, 0), M) == "hop"
    assert p.travel_style((0, 0), (1700, 900), M) == "fly"
    assert p.travel_style((100, 100), (2500, 100), TWO) == "fly"      # to the other monitor
    assert p.travel_style((0, 0), (200, 0), M, reduced_motion=True) == "teleport"


def test_candidates_are_inside_work_areas_and_include_beside_the_window():
    spots = p.candidate_spots(M, 87, 87, foreground=(400, 300, 800, 500), preferred=(500, 900), current=(5000, 5000))
    kinds = {k for (_x, _y, k) in spots}
    assert {"corner", "taskbar", "edge", "beside", "preferred", "current"} <= kinds
    for (x, y, k) in spots:
        if k != "current":
            assert 0 <= x and x + 87 <= 1920 and 0 <= y and y + 87 <= 1080


# ---------------------------------------------------------------- the menu beside him
def test_expand_layout_opens_beside_him_without_moving_him():
    lay = p.expand_layout((100, 900, 87, 87), (238, 238), M)
    wx, wy, ww, wh = lay["window"]
    rx, ry, rw, rh = lay["robot"]
    assert lay["side"] == "right" and (wx + rx, wy + ry, rw, rh) == (100, 900, 87, 87)   # he stays put on screen
    px, py, pw, ph = lay["panel"]
    assert wx + px >= 100 + 87 and (wy + py + ph) == 900 + 87                             # beside him, level with his feet
    assert wx >= 0 and wy >= 0 and wx + ww <= 1920 and wy + wh <= 1080                     # on screen
    right_edge = p.expand_layout((1920 - 87 - 12, 900, 87, 87), (238, 238), M)
    assert right_edge["side"] == "left"                                                    # no room right: opens left
    near_top = p.expand_layout((800, 10, 87, 87), (238, 470), M)
    assert near_top["window"][1] >= 0                                                      # shifted to stay on screen
    assert p.expand_layout((800, 500, 87, 87), (238, 238), M, prefer="left")["side"] == "left"


# ---------------------------------------------------------------- event contract
def test_new_events_validate():
    assert c.validate_event({"type": "activity", "value": "coding"}) == {"type": "activity", "value": "coding"}
    assert c.validate_event({"type": "activity", "value": "hacking"}) is None
    ev = c.validate_event({"type": "relocate", "x": "10", "y": 20, "style": "fly", "dock": 7})
    assert ev == {"type": "relocate", "x": 10, "y": 20, "style": "fly", "dock": 2}
    assert c.validate_event({"type": "relocate", "x": 1, "y": 2, "style": "warp"})["style"] == "walk"
    assert c.validate_event({"type": "relocate", "x": "a", "y": 2}) is None
    assert c.validate_event({"type": "menu"}) == {"type": "menu", "action": "toggle"}
    assert c.validate_event({"type": "menu", "action": "open", "view": "skills"})["view"] == "skills"
    assert "view" not in c.validate_event({"type": "menu", "action": "open", "view": "../evil"})
    assert c.validate_event({"type": "game", "phase": "start", "game": "trivia"})["game"] == "trivia"
    assert c.validate_event({"type": "game", "phase": "explode"}) is None
    from hud.app import COMPANION_LOCAL
    assert {"activity", "relocate", "menu", "game"} <= COMPANION_LOCAL
    assert c.safe_settings({})["work_aware"] is True and c.safe_settings({"hover_greet": 0})["hover_greet"] is False
    assert "read" in c.IDLE_ACTIONS and "hologram" in c.IDLE_ACTIONS


def test_interaction_js_suite_passes():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run([node, str(root / "tests" / "node" / "test_interaction.mjs")], cwd=str(root),
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["failed"] == 0 and summary["passed"] >= 50, summary


# ---------------------------------------------------------------- the host (hud/app.py)
@pytest.fixture
def api(monkeypatch, tmp_path):
    import hud.app as app
    monkeypatch.setattr(c, "default_position_path", lambda: tmp_path / "companion-position.json")
    a = app.Api(mode="companion")
    a._companion = object()
    monkeypatch.setattr(a, "_companion_hwnd", lambda: 42)
    monkeypatch.setattr(app, "_work_area_monitors", lambda: list(M))
    moves = []
    monkeypatch.setattr(app, "_set_window_rect", lambda hwnd, x, y, w, h: moves.append((x, y, w, h)) or True)
    monkeypatch.setattr(app, "_window_rect", lambda hwnd: (100, 900, 87, 87))
    a._moves = moves
    sent = []
    monkeypatch.setattr(a, "_dispatch", lambda msg: sent.append(msg))
    a._sent = sent
    return a


def test_the_api_lease_blocks_and_releases_placement(api):
    assert api.interaction_lease("held", 2500)
    assert api._lease.blocks(time.monotonic())
    api.interaction_release("held", 0)
    assert not api._lease.blocks(time.monotonic() + 0.01)
    assert api.interaction_lease("ambient") is False


def test_overlay_cannot_collapse_a_window_the_menu_took_over(api):
    assert api.companion_expand(382, 108, "", 1.0, "overlay")
    assert api.companion_expand(238, 238, "", 1.0)                       # his menu opens while the card is up
    moves = len(api._moves)
    assert api.companion_collapse("overlay") is False and api._expanded is not None and len(api._moves) == moves
    assert api.companion_collapse() and api._moves[-1] == (100, 900, 87, 87)   # the menu's own close still works
    assert api.companion_expand(382, 108, "", 1.0, "overlay")
    assert api.companion_collapse("overlay") and api._expanded is None


def test_menu_expand_and_collapse_keep_him_where_he_stands(api):
    lay = api.companion_expand(238, 238, "", 1.0)
    assert lay["side"] in ("left", "right")
    x, y, w, h = api._moves[-1]
    assert (x + lay["robot"][0], y + lay["robot"][1]) == (100, 900)    # his box is where he was
    assert api.companion_collapse() and api._moves[-1] == (100, 900, 87, 87)
    lay2 = api.companion_expand(238, 238, "", 2.0)                       # a 200% display: CSS px come back halved
    assert lay2["robot"][2] == 43.5


def test_dock_and_home_are_animated_relocations(api):
    assert api.companion_dock(True)
    ev = api._sent[-1]
    assert ev["type"] == "relocate" and ev["dock"] == 2 and ev["x"] < 0          # peeking from the left edge
    assert api.companion_dock(False) and api._sent[-1]["dock"] == 0
    api._preferred = (500, 500)
    assert api.companion_home() and (api._sent[-1]["x"], api._sent[-1]["y"]) == (500, 500)


def test_drag_sets_his_preferred_spot(api, tmp_path):
    class Win:
        width = height = 87
        def move(self, x, y): pass
    api._companion = Win()
    api._enumerate_monitors = lambda: list(M)
    api.move_to(400, 600)
    assert api._preferred == (400, 600)
    assert json.loads((tmp_path / "companion-preferred.json").read_text()) == {"x": 400, "y": 600}
    import hud.app as app
    assert app._load_preferred() == (400, 600)


def test_skill_prefs_round_trip_and_are_cleaned(api):
    out = api.save_skill_prefs({"favorites": ["read_screen", "read_screen", 7, "x" * 99], "recent": ["get_time"]})
    assert out == {"favorites": ["read_screen"], "recent": ["get_time"]}
    assert api.skill_prefs() == out


def test_games_bridge(api):
    assert not api.report_games("nope")
    assert api.report_games(json.dumps({"games": [{"id": "trivia", "name": "Trivia", "icon": "🧠", "best": "best 9/10"}]}))
    assert api.games()["games"][0]["best"] == "best 9/10"
    shown = []

    class Panel:
        def show(self): shown.append("show")
        def restore(self): pass
        def evaluate_js(self, js): shown.append(js)
    api._window = Panel()
    assert api.play_game("trivia") and '"game": "trivia"' in shown[-1]
    api.companion_game("start", "trivia")
    assert api._sent[-1] == {"type": "game", "phase": "start", "game": "trivia"}


def test_presence_state_snapshot_is_written_for_the_live_app(api, tmp_path):
    api._state_written = 0.0
    assert api.push_renderer_snapshot(json.dumps({"interaction": "menu_open"}))
    data = json.loads((tmp_path / "companion-state.json").read_text())
    assert data["interaction"] == "menu_open" and "lease" in data["presence"]


# ---------------------------------------------------------------- skills: the real registry
def test_catalogue_survives_lazy_registration_during_status(monkeypatch):
    from core import skills_catalog
    from tools import registry
    entry=next(iter(registry._TOOLS.values()))
    snapshot={'first':entry}
    monkeypatch.setattr(registry,'_TOOLS',snapshot)
    def status(*_):
        snapshot['late']=entry
        return 'ready','ready'
    monkeypatch.setattr(skills_catalog,'_status',status)
    assert skills_catalog.build()['count']==1
    assert skills_catalog.build()['count']==2


def test_server_catalogue_does_not_probe_desktop_game_state(monkeypatch):
    from core import skills_catalog,pcbridge
    from tools import gaming
    monkeypatch.setattr(skills_catalog,'required_keys',lambda _:[])
    monkeypatch.setattr(pcbridge,'local_pc',lambda:False)
    calls=[]
    def probe():calls.append('desktop');return False
    monkeypatch.setattr(gaming,'game_mode',probe)
    assert skills_catalog._status('pcview',None,True)==('ready','ready')
    assert calls==[]


def test_the_skills_catalogue_is_the_live_registry():
    import tools
    from core import skills_catalog
    from tools import registry
    tools.load_all()
    cat = skills_catalog.build()
    assert cat["count"] == len(registry._TOOLS) >= 190                  # every registered skill, nothing hand-kept
    names = {s["name"] for s in cat["skills"]}
    assert names == set(registry.names())
    for s in cat["skills"]:
        assert s["title"] and s["category"] and s["description"] and s["status"] in ("ready", "needs_setup", "paused")
    see = next(s for s in cat["skills"] if s["name"] == "see_pc_screen")
    assert "show my PC" in see["examples"] and see["category"] == "PC control"
    focus = next(s for s in cat["skills"] if s["name"] == "focus_mode")
    assert all("'" not in e or e.count("'") == 1 for e in focus["examples"])   # 'stop', 'skip' isn't one phrase
    weather = next(s for s in cat["skills"] if s["name"] == "get_weather")
    assert any(par["name"] == "days" for par in weather["params"])
    assert skills_catalog.friendly("pc_command") == "PC command"


def test_a_missing_required_key_is_reported(monkeypatch):
    import sys
    import tools
    from core import skills_catalog
    tools.load_all()
    assert "JELLYFIN_API_KEY" in skills_catalog.required_keys(sys.modules["tools.homelab"])
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    jelly = [s for s in skills_catalog.build()["skills"] if s["module"] == "homelab"]
    assert any(s["status"] == "needs_setup" and "JELLYFIN_API_KEY" in s["status_label"] for s in jelly)


# ---------------------------------------------------------------- hit mask: only HE takes the mouse
def test_hitmask_grid_reads_his_cells_and_their_colours():
    from hud.app import MASK_RGB, hitmask_grid
    spec = {"cols": 4, "rows": 2, "w": 40, "h": 20, "dpr": 1.5, "bits": ["0110", "1111"],
            "colors": ["000fa0c8f000", "123456789abc"], "rects": [[8, 24, 100, 20]]}
    g = hitmask_grid(spec)
    assert g["size"] == (60, 30) and set(g["cells"]) == {(1, 0), (2, 0), (0, 1), (1, 1), (2, 1), (3, 1)}
    assert g["cells"][(1, 0)] == (0xF * 17, 0xA * 17, 0) and g["cells"][(2, 0)] == (0xC * 17, 0x8 * 17, 0xF * 17)
    assert g["rects"] == [(12, 36, 150, 30)]                                    # his size slider, window px
    plain = hitmask_grid({k: v for k, v in spec.items() if k != "colors"})
    assert set(plain["cells"].values()) == {MASK_RGB}                          # no colours sent: a neutral steel


@pytest.mark.parametrize("bad", [
    None, {}, {"cols": 0, "rows": 2, "w": 40, "h": 20, "bits": ["", ""]},
    {"cols": 4, "rows": 2, "w": 40, "h": 20, "bits": ["0110"]},                 # wrong row count
    {"cols": 4, "rows": 2, "w": 40, "h": 20, "bits": ["0110", "11"]},           # ragged row
    {"cols": 99, "rows": 2, "w": 40, "h": 20, "bits": ["0" * 99] * 2},          # grid too big
    {"cols": 4, "rows": 2, "w": 40, "h": 20, "dpr": 50, "bits": ["1111"] * 2},  # absurd scale
    {"cols": "x", "rows": 2, "w": 40, "h": 20, "bits": []},
])
def test_hitmask_rejects_malformed_masks(bad):
    from hud.app import hitmask_grid
    assert hitmask_grid(bad) is None


def test_hitmask_pixels_keep_the_key_where_he_isnt():
    from hud.app import KEY_RGB, hitmask_grid, hitmask_pixels
    g = hitmask_grid({"cols": 3, "rows": 1, "w": 30, "h": 10, "bits": ["011"], "colors": ["000f00000"]})
    px = hitmask_pixels(g, KEY_RGB)
    assert len(px) == 3 * 4
    assert px[0:4] == bytes((KEY_RGB[2], KEY_RGB[1], KEY_RGB[0], 255))         # not him: stays see-through
    assert px[4:8] == bytes((0, 0, 255, 255))                                    # BGRA of his red cell
    # a cell that would be exactly the key colour is nudged, or the click would fall through it
    black = hitmask_grid({"cols": 1, "rows": 1, "w": 10, "h": 10, "bits": ["1"], "colors": ["000"]})
    assert hitmask_pixels(black, (0, 0, 0))[0:4] == bytes((0, 0, 17, 255))


def test_hitmask_plan_menu_only_once_faded_in_and_nothing_while_yielding():
    from hud.app import hitmask_plan
    lay = {"side": "right", "window": [0, 0, 400, 300], "robot": [0, 50, 200, 250], "panel": [206, 62, 190, 238]}
    spec = {"cols": 2, "rows": 1, "w": 100, "h": 125, "dpr": 2, "bits": ["11"]}
    grid, box, panel = hitmask_plan(spec, lay)
    assert grid and box == (0, 50, 200, 250) and panel is None                  # menu still fading in: not yet
    assert hitmask_plan({**spec, "panel": True}, lay)[2] == [206, 62, 190, 238]
    assert hitmask_plan(spec, None)[1] == (0, 0, 200, 250)                      # collapsed: his box is the window
    assert hitmask_plan({**spec, "panel": True}, lay, yielding=True) == (None, None, None)
    assert hitmask_plan({"panel": True}, None) == (None, None, None)            # collapsed, no cells (mid-throw)
    assert hitmask_plan(None, lay) == (None, None, None)


def test_page_calls_get_words_not_bare_http_statuses():
    from hud.app import _plain_http_error
    assert "doesn't offer that" in _plain_http_error(404, "Not Found")
    assert "doesn't offer that" in _plain_http_error(405, "Method Not Allowed")
    assert "key" in _plain_http_error(401, None)
    assert "try again" in _plain_http_error(500, "Internal Server Error")
    assert _plain_http_error(400, "That skill needs a city.") == "That skill needs a city."   # the server's own words


def test_menu_hotkey_can_take_the_keyboard():
    """Opened with Ctrl+Alt+K his process never received the input, so Windows' foreground lock refused
    SetForegroundWindow and Esc / arrows went to the user's app. companion_focus now falls back to sharing the
    foreground thread's input state (AttachThreadInput) for that one call."""
    import inspect
    import hud.app as app
    src = inspect.getsource(app.Api.companion_focus)
    assert "_force_foreground(hwnd, fg)" in src
    force = inspect.getsource(app._force_foreground)
    assert "AttachThreadInput(ours, theirs, True)" in force and "AttachThreadInput(ours, theirs, False)" in force
    assert app._force_foreground(0, 0) is False                               # no window: no-op


def test_companion_survives_a_gpu_reset_with_his_lighting():
    """A GPU process crash (a driver reset, a game) restores the WebGL context, but the studio environment map was
    rendered ON the GPU: without rebuilding it he came back dim (mean luminance 99 -> 43, measured). Diagnostics
    report the renderer, context losses and WebView2 process failures."""
    from pathlib import Path
    import hud.app as app
    html = (Path(__file__).resolve().parents[1] / "app" / "companion.html").read_text(encoding="utf-8")
    restored = html[html.index('"webglcontextrestored"'):html.index('"webglcontextrestored"') + 300]
    assert "buildEnvironment" in restored and "setTimeout" in restored     # after three.js has re-initialised
    assert "gpu: gpuInfo" in html and "UNMASKED_RENDERER_WEBGL" in html
    assert "webview_failures" in app.Api().presence_state()


def test_companion_hitmask_api_is_safe_without_a_window():
    from hud.app import Api
    api = Api()
    assert api.companion_hitmask({"cols": 2, "rows": 1, "w": 10, "h": 10, "bits": ["11"]}) is False   # no window yet
    assert api._hitmask["bits"] == ["11"]
    assert api.companion_hitmask("junk") is False and api._hitmask is None


def test_companion_page_sends_its_silhouette_after_each_render():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "app" / "companion.html").read_text(encoding="utf-8")
    render = html.index("renderer.render(scene, camera);")
    assert html.index("sampleHitMask(now, renderer.getContext())", render) - render < 120   # same frame: buffer intact
    assert "a.companion_hitmask(spec)" in html and "readPixels" in html
    for owner in ("throwRunning", "held", "flying", "\"yielding\""):                          # no mask mid-flight / carried
        assert owner in html[html.index("function maskSuppressed"):html.index("function sampleHitMask")]
    hide = html[html.index('msg.type === "hide"'):html.index('msg.type === "show"')]
    assert "sendHitMask" in hide and 'ix.set("suspended")' in hide       # hidden: no invisible clickable patch


def test_running_a_skill_goes_through_the_registry():
    import tools
    from core import skills_catalog
    tools.load_all()
    out = skills_catalog.run("owner", "get_time", {})
    assert out["ok"] and out["text"] and out["seconds"] >= 0
    assert skills_catalog.run("owner", "no_such_skill", {})["ok"] is False
    bad = skills_catalog.run("owner", "set_timer", {"seconds": "soon"})
    assert bad["ok"] is False and bad.get("invalid")


def test_skill_endpoints(monkeypatch):
    import tools
    from tests.test_app import client as make_client, pair
    tools.load_all()
    from core import appserver
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    monkeypatch.setattr("audio.tts.mp3_for", lambda text: b"ID3fake")
    from tests.test_app import FakeBrain
    from fastapi.testclient import TestClient
    server = appserver.AppServer(lambda uid: FakeBrain(uid))
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    cl = TestClient(server.app)
    headers = pair(cl)
    assert cl.get("/api/skills").status_code == 401                       # paired devices only
    cat = cl.get("/api/skills", headers=headers).json()
    assert cat["count"] >= 190 and cat["categories"]
    ran = cl.post("/api/skills/run", headers=headers, json={"name": "get_time", "args": {}}).json()
    assert ran["ok"] is True
    explained = cl.post("/api/skills/explain", headers=headers, json={"name": "get_time", "text": "It's 4:20 PM."}).json()
    assert explained["reply"] == "It's 4:20 PM." and explained["audio"]   # no brain explainer: the result itself
    assert cl.post("/api/companion/listen", headers=headers).json()["ok"] is False   # no microphone wired
    woke = []
    server.on_listen = lambda: woke.append(1)
    assert cl.post("/api/companion/listen", headers=headers).json()["ok"] is True and woke == [1]


@pytest.mark.parametrize("extras", [42, {}, [[0, 0, float("inf"), 10]], [[float("nan"), 0, 10, 10]], [[0, 0, 1e100, 10]]])
def test_hitmask_ignores_invalid_extra_rectangles(extras):
    from hud.app import hitmask_grid
    grid = hitmask_grid({"cols": 1, "rows": 1, "w": 10, "h": 10, "bits": ["1"], "rects": extras})
    assert grid is not None and grid["rects"] == []


def test_hitmask_rejects_infinite_dimensions():
    from hud.app import hitmask_grid
    assert hitmask_grid({"cols": float("inf"), "rows": 1, "w": 10, "h": 10, "bits": ["1"]}) is None


def test_fresh_page_releases_old_scene_and_pointer_ownership():
    from hud.app import Api
    import time
    api=Api(mode="companion")
    api._lease.grant("playing",10,time.monotonic())
    api._scene_mode="home";api._scene_rect=(100,100,87,87);api._expanded={"panel":[0,0,200,200]}
    api._habitat={"kind":"folder"};api._physics_until=1e20
    api._reset_page_state()
    assert api._scene_mode is None and api._expanded is None and api._habitat is None
    assert not api._lease.blocks(time.monotonic()) and api._physics_until==0


def test_character_size_inside_home_is_saved_without_collapsing_room():
    from hud.app import Api
    from unittest.mock import Mock
    from core.companion import load_settings
    api=Api(mode="companion");api._companion=Mock();api._scene_mode="home"
    result=api.set_character_size(160)
    assert result["character_px"]==160 and load_settings()["character_px"]==160
    api._companion.resize.assert_not_called()
