"""Nolagin v7 regression: sustained health warnings remain REAL, not stale."""
import numpy as np
from core.gamecoach_capture import BarCalibration,BarDetector
from core.gamecoach_reactor import EvidenceBus
from core.gamecoach_tactics import Evidence,TacticalCoach

def frame(fill=1.,color=(20,200,50)):
    pixels=np.zeros((10,100,3),dtype=np.uint8)
    if fill>0:pixels[:,:round(100*fill),:]=color
    return pixels

def test_low_health_is_reverified_for_later_enemy_threat_and_never_repeated_as_edge():
    detector=BarDetector(BarCalibration("low_health",(0,0,100,10),(20,200,50)))
    times=[1000.0+i*.09 for i in range(2)]
    for t in times:detector.process(frame(),t)
    alerts=0
    for i in range(16):
        now=1000.3+i*.09
        event=detector.process(frame(.2),now)
        if event:alerts+=1
        persistent=detector.current_low_observation()
        if i<2:assert persistent is None
        else:
            assert persistent is not None
            assert persistent.captured_at==now
            assert persistent.kind=="low_health"
    assert alerts==1
    bus=EvidenceBus("apex",clock=lambda:1001.65)
    assert bus.feed_bar(detector.current_low_observation())
    assert bus.update("enemy_visible",True,1001.65,.99,"pixels","two recent distinct frames")
    assert bus.decide().kind=="contested_low_health"

def test_occluded_hud_is_unknown_not_persistent_low():
    detector=BarDetector(BarCalibration("low_shield",(0,0,100,10),(20,200,50)))
    for _ in range(2):detector.process(frame())
    for _ in range(3):detector.process(frame(.18))
    assert detector.current_low_observation()
    detector.process(frame(0))
    assert detector.current_low_observation() is None
    # A single returning colored pixel pattern cannot instantly re-confirm low.
    detector.process(frame(.18))
    assert detector.current_low_observation() is None
    detector.process(frame(.18))
    assert detector.current_low_observation() is None
    detector.process(frame(.18))
    assert detector.current_low_observation()

def test_reset_stops_reusing_old_low_frames_and_requires_full_again():
    detector=BarDetector(BarCalibration("low_ammo",(0,0,100,10),(20,200,50)))
    for _ in range(2):detector.process(frame())
    for _ in range(3):detector.process(frame(.2))
    assert detector.current_low_observation()
    detector.reset()
    assert detector.current_low_observation() is None
    for _ in range(5):detector.process(frame(.2))
    assert detector.current_low_observation() is None


def test_cpu_vision_automatically_disables_after_sustained_slowness():
    from time import monotonic,sleep
    from core.gamecoach_vision_worker import VisionWorker
    from core.gamecoach_enemy_detector import EnemyCue
    class Slow:
        def infer(self,frame,captured_at,clock):
            sleep(.035)
            return EnemyCue(1,captured_at,.98,"mock model")
    w=VisionWorker(Slow(),interval_s=.1,max_inference_s=.01,slow_limit=2)
    w.start()
    try:
        frame=np.zeros((32,32,3),np.uint8)
        for iteration in range(2):
            # Enforce the sample interval; never queue an unbounded backlog.
            deadline=monotonic()+2.
            while monotonic()<deadline and not w.offer(frame,monotonic()):
                sleep(.015)
            assert monotonic()<deadline
            if iteration==0:
                while monotonic()<deadline and w.poll() is None:sleep(.01)
                assert monotonic()<deadline
            else:
                while monotonic()<deadline and not w.disabled:sleep(.01)
                assert w.disabled
        summary=w.summary()
        assert summary["vision_disabled_reason"]=="sustained_slow_cpu_inference"
        assert summary["disabled_due_to_load"]==1
        assert summary["slow_inferences"]>=2
        assert not w.offer(frame,monotonic())
    finally:
        assert w.stop(1)
