import pytest
from core.desktop_habitat import Surface, anchor_for, choose_anchor, protected_local
from core.companion import Monitor

MON = [Monitor(0, 0, 1920, 1080)]

def test_real_bottom_taskbar_anchor_and_autohide_fallback():
    surface = Surface("bar", "taskbar", (0, 1040, 1920, 40), edge="bottom")
    a = anchor_for(surface, (100, 100), MON)
    assert a and a["y"] < 1040 and a["y"] + 100 > 1040
    assert a["pose"] == "ledge_sit"
    assert anchor_for(Surface("bar", "taskbar", (0, 1078, 1920, 2), auto_hide=True), (100, 100), MON) is None

@pytest.mark.parametrize("edge,rect", [("top", (0, 0, 1920, 40)), ("left",(0,0,40,1080)), ("right",(1880,0,40,1080))])
def test_oriented_bar_never_requires_fake_control(edge, rect):
    a=anchor_for(Surface("bar", "taskbar", rect, edge=edge), (100,100), MON)
    assert a and a["pose"] == "ledge_hang"
    assert 0 <= a["x"] <= 1820 and 0 <= a["y"] <= 980

def test_negative_monitor_and_preferred_real_surface():
    mons=[Monitor(-1920,0,1920,1080),*MON]
    bar=Surface("second", "taskbar", (-1920,1040,1920,40))
    a=choose_anchor([bar], (100,100), mons, preferred="second")
    assert -1920 <= a["x"] < 0
    assert a["surface_id"] == "second"

def test_no_geometry_means_no_invented_taskbar():
    assert choose_anchor([], (100,100), MON) is None

def test_blocked_window_edges_are_rejected():
    surface=Surface("window", "window", (100,300,500,300))
    assert anchor_for(surface,(100,100),MON,blocked=[(0,0,1920,1080)]) is None

def test_protected_taskbar_and_search_are_clipped_in_window_coordinates():
    assert protected_local([(0,1040,1920,40),(600,1040,200,40)], (590,990,100,100)) == [(0,50,100,40),(10,50,90,40)]
    assert protected_local([(0,0,10,10)],(100,100,100,100)) == []


@pytest.mark.parametrize("edge,rect",[("left",(0,0,40,1080)),("right",(1880,0,40,1080))])
def test_large_vertical_frame_declines_instead_of_shifting_contact(edge,rect):
    assert anchor_for(Surface("bar","taskbar",rect,edge=edge),(400,400),MON) is None


def test_projected_pelvis_contact_aligns_to_real_ledge():
    bar=Surface("bar","taskbar",(0,1040,1920,40))
    a=anchor_for(bar,(140,140),MON,contact=(.5,.8))
    assert a["y"]+int(140*.8)==1040


def test_native_probe_does_not_poison_existing_hud_rect_abi():
    import os
    if os.name != "nt": pytest.skip("Native Windows ABI regression")
    from core.desktop_habitat import _taskbars
    from hud.app import _window_rect
    bars=_taskbars()
    if not bars: pytest.skip("No interactive taskbar")
    assert _window_rect(bars[0].hwnd) == bars[0].rect
