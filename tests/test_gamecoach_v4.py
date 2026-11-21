"""Nolagin v4 contracts: evidence fusion, icon accuracy and live wiring."""
import json
import time
import numpy as np
import pytest
from PIL import Image
from core.gamecoach import Observation
from core.gamecoach_badges import (BadgeConfig, BadgeDetector, load_badge_file)
from core.gamecoach_reactor import EvidenceBus, TacticalDispatcher
from scripts.gamecoach_live import prepare_sources

class Clock:
    def __init__(self,t=1000.):self.t=t
    def __call__(self):return self.t

def proof(bus,key,value=True,conf=.99,source="pixels",note="visible on the screen",age=0):
    return bus.update(key,value,bus.clock()-age,conf,source,note)

def test_no_tactics_from_single_fact():
    t=Clock()
    bus=EvidenceBus("apex",clock=t)
    assert proof(bus,"health_low")
    assert bus.decide() is None
    assert list(bus.snapshot())==["health_low"]

def test_real_bar_maps_only_one_fact_not_cover_enemy():
    t=Clock()
    bus=EvidenceBus("apex",clock=t)
    o=Observation("low_health",t(),.94,"confirmed color bar at 18%")
    assert bus.feed_bar(o)
    assert set(bus.snapshot())=={"health_low"}
    assert bus.decide() is None

def test_grounded_fact_pair_generates_tactic_and_then_goes_stale():
    t=Clock()
    bus=EvidenceBus("apex",clock=t)
    assert proof(bus,"health_low")
    assert proof(bus,"exposed",note="visible open sightline")
    assert bus.decide().kind=="break_sightline"
    assert bus.decide() is None  # identical evidence deduplicated
    t.t+=.7
    assert bus.decide() is None
    assert bus.snapshot()=={}

def test_new_urgent_signal_beats_lower_one():
    t=Clock()
    bus=EvidenceBus("apex",clock=t)
    proof(bus,"shields_low");proof(bus,"cover_visible")
    assert bus.decide().kind=="reset_shields"
    proof(bus,"health_low");proof(bus,"exposed")
    assert bus.decide().kind=="break_sightline"

def test_false_recent_fact_retracts_old_true():
    t=Clock()
    bus=EvidenceBus("apex",clock=t)
    proof(bus,"health_low");proof(bus,"exposed")
    assert bus.decide()
    t.t+=.1
    assert proof(bus,"exposed",value=False,note="now fully behind cover")
    assert bus.decide() is None

def test_old_duplicate_low_confidence_untrusted_and_unknown_rejected():
    t=Clock()
    bus=EvidenceBus("apex",clock=t)
    assert not proof(bus,"enemy_visible",conf=.3)
    assert not proof(bus,"enemy_visible",source="game_memory")
    assert not proof(bus,"madeup_player_nearby")
    assert not proof(bus,"health_low",age=2)
    assert proof(bus,"health_low")
    t.t+=.08
    assert not bus.update("health_low",True,t.t-.09,.99,"pixels","late source")
    assert bus.stats.ignored>=5

def test_dispatcher_passes_only_grounded_decision_to_speech_pipeline():
    t=Clock()
    calls=[]
    bus=EvidenceBus("cod",clock=t)
    bridge=TacticalDispatcher(bus,lambda o:calls.append(o) or True)
    bridge.fact("objective_timer_low",True,t(),.95,"pixels","HUD timer shows last seconds")
    assert bridge.tick() is None
    bridge.fact("objective_visible",True,t(),.96,"pixels","HUD objective marker visible")
    assert bridge.tick().kind=="play_objective"
    assert len(calls)==1 and calls[0].kind=="play_objective"
    assert bridge.tick() is None

def make_pattern():
    y,x=np.mgrid[0:12,0:20]
    arr=np.empty((12,20,3),dtype=np.uint8)
    arr[:,:,0]=(x*11+y*3)%256
    arr[:,:,1]=(x*4+y*13)%256
    arr[:,:,2]=(x*7+y*17)%256
    return arr

def test_fixed_icon_confirmed_only_after_three_fresh_frames():
    img=make_pattern()
    sensor=BadgeDetector(BadgeConfig("zone_moving",(2,3,22,15)),img)
    screen=np.zeros((20,30,3),np.uint8)
    assert all(sensor.process(screen) is None for _ in range(12))
    screen[3:15,2:22]=img
    assert sensor.process(screen) is None
    assert sensor.process(screen) is None
    first=sensor.process(screen)
    assert first and first.field=="zone_moving" and first.value is True
    assert all(sensor.process(screen) is None for _ in range(10))
    screen[:]=0
    assert sensor.process(screen) is None
    assert sensor.process(screen) is None
    off=sensor.process(screen)
    assert off and off.value is False

def test_fixed_badge_rejects_unrelated_noise():
    img=make_pattern()
    sensor=BadgeDetector(BadgeConfig("teammate_down",(0,0,20,12)),img)
    noise=np.random.default_rng(5).integers(0,256,(12,20,3),dtype=np.uint8)
    for _ in range(15):assert sensor.process(noise) is None

def test_badge_config_rejects_bad_bounds_and_unknown_enemy_info():
    img=make_pattern()
    with pytest.raises(ValueError):
        BadgeDetector(BadgeConfig("enemy_visible",(0,0,20,12)),img)
    with pytest.raises(ValueError):
        BadgeDetector(BadgeConfig("zone_moving",(-1,0,19,12)),img)
    with pytest.raises(ValueError):
        BadgeDetector(BadgeConfig("zone_moving",(0,0,20,12)),np.zeros_like(img))

def test_badge_profile_rejects_non_reviewed_and_traversal(tmp_path):
    img=make_pattern()
    Image.fromarray(img).save(tmp_path/"badge.png")
    data={"field":"zone_moving","rect":[0,0,20,12],"image":"badge.png"}
    j=tmp_path/"profile.json"
    j.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_badge_file(j)
    data["reviewed"]=True
    data["image"]="../badge.png"
    j.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_badge_file(j)
    data["image"]="badge.png"
    j.write_text(json.dumps(data))
    config,patch=load_badge_file(j)
    assert patch.shape==(12,20,3) and config.field=="zone_moving"

def test_badge_and_bar_share_one_bounded_capture(tmp_path):
    health={"kind":"low_health","rect":[30,50,130,60],"rgb":[25,200,20]}
    bar=tmp_path/"bar.json"
    bar.write_text(json.dumps(health))
    template=make_pattern()
    Image.fromarray(template).save(tmp_path/"example.png")
    badge={"field":"zone_moving","rect":[10,20,30,32],"image":"example.png","reviewed":True}
    meta=tmp_path/"badge.json"
    meta.write_text(json.dumps(badge))
    region,bars,badges=prepare_sources([bar],[meta])
    assert region==(10,20,130,60)
    assert bars[0][1].c.rect==(20,30,120,40)
    assert badges[0].cfg.rect==(0,0,20,12)

def test_bar_sensors_do_not_fail_on_dxcam_no_new_frame():
    from core.gamecoach_capture import ScreenSource
    class NoNew:
        def grab(self,region=None):return None
    src=ScreenSource.__new__(ScreenSource)
    src.camera=NoNew()
    src.failed=True
    src.last_error="old"
    assert src.grab() is None
    assert src.failed is False and src.last_error is None

def test_dxcam_exception_sets_real_capture_failure():
    from core.gamecoach_capture import ScreenSource
    class Broken:
        def grab(self,region=None):raise OSError("capture error")
    src=ScreenSource.__new__(ScreenSource)
    src.camera=Broken()
    src.failed=False
    assert src.grab() is None
    assert src.failed is True and src.last_error=="OSError"
