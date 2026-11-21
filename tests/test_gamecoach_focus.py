"""Nolagin focus safety and stale in-flight output regression tests."""
import time
from threading import Event

import numpy as np
from core.gamecoach_focus import game_in_foreground
from core.gamecoach_vision_worker import VisionWorker
from core.gamecoach_enemy_detector import EnemyCue
from core.gamecoach_capture import BarDetector,BarCalibration
from core.gamecoach_badges import BadgeDetector,BadgeConfig

def test_game_focus_requires_known_game_process():
    assert game_in_foreground("apex",process_lookup=lambda:"r5apex.exe")
    assert game_in_foreground("apex",process_lookup=lambda:"R5APEX_DX12.EXE")
    assert game_in_foreground("cod",process_lookup=lambda:"CODHQ.EXE")
    assert not game_in_foreground("apex",process_lookup=lambda:"chrome.exe")
    assert not game_in_foreground("cod",process_lookup=lambda:"jarvis.exe")
    assert not game_in_foreground("apex",platform="linux")
    assert not game_in_foreground("unknown",process_lookup=lambda:"cod.exe")
    assert not game_in_foreground("apex",process_lookup=lambda:1/0)

def test_bar_must_rearm_after_app_switch():
    d=BarDetector(BarCalibration("low_health",(0,0,100,10),(20,200,40)))
    full=np.full((10,100,3),[20,200,40],np.uint8)
    low=full.copy()
    low[:,20:]=0
    for _ in range(2):assert d.process(full) is None
    for _ in range(2):assert d.process(low) is None
    assert d.process(low) is not None
    d.reset()
    for _ in range(5):assert d.process(low) is None
    for _ in range(2):assert d.process(full) is None
    for _ in range(2):assert d.process(low) is None
    assert d.process(low) is not None

def test_icon_cannot_stay_visible_when_game_loses_focus():
    y,x=np.mgrid[0:12,0:20]
    image=np.stack([x*9,y*14,(x+y)*7],axis=-1).astype(np.uint8)
    detector=BadgeDetector(BadgeConfig("zone_moving",(0,0,20,12)),image)
    for _ in range(2):assert detector.process(image) is None
    assert detector.process(image).value is True
    detector.reset()
    for _ in range(2):assert detector.process(image) is None
    assert detector.process(image).value is True

def test_completed_vision_from_pre_alt_tab_epoch_is_never_delivered():
    began=Event()
    release=Event()
    class Slow:
        def infer(self,frame,captured_at,clock):
            began.set()
            release.wait(1)
            return EnemyCue(2,captured_at,.99,"old game frame")
    worker=VisionWorker(Slow(),interval_s=.1)
    worker.start()
    try:
        assert worker.offer(np.zeros((24,24,3),np.uint8),time.monotonic())
        assert began.wait(1)
        worker.invalidate()
        release.set()
        # Wait for the old result to be processed and discarded.
        deadline=time.monotonic()+1.
        while time.monotonic()<deadline:
            assert worker.poll() is None
            time.sleep(.01)
        assert worker.stats.returned==0
    finally:
        release.set()
        assert worker.stop(1)
