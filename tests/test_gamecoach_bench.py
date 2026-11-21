"""Strict A/B/A benchmark gates and real detection-onset timestamp tests."""
import numpy as np
import pytest
from scripts.gamecoach_performance_gate import compare
from core.gamecoach_capture import BarCalibration,BarDetector
from core.gamecoach_badges import BadgeConfig,BadgeDetector

def run(avg=144,low=95,frame=13.2,coach=False):
    return {"game":"apex","resolution":"2560x1440","graphics_preset":"competitive",
            "coach_enabled":coach,"duration_seconds":720,
            "avg_fps":avg,"p1_low_fps":low,"p99_frametime_ms":frame}

def test_fps_gates_remain_incomplete_without_actual_onset_and_capture_metrics():
    a=run();on=run(143,94,13.3,True);b=run(144,95,13.2)
    s=compare(a,on,b)
    assert s["status"]=="INCOMPLETE"
    assert s["checks"]["actual_audio_onset_p95_ms"] is None
    assert s["checks"]["average_fps"]

def test_actual_well_measured_pass():
    a=run();on=run(143,94,13.3,True);b=run(144,95,13.2)
    on["capture_detector_p95_ms"]=9
    on["actual_audio_onset_p95_ms"]=280
    assert compare(a,on,b)["status"]=="PASS"

def test_frameview_gate_fails_on_bad_fps():
    x=compare(run(),dict(run(130,75,17,True),
                         capture_detector_p95_ms=9,actual_audio_onset_p95_ms=280),run())
    assert x["status"]=="FAIL"
    assert x["checks"]["average_fps"] is False
    assert x["checks"]["one_percent_low"] is False

def test_drifting_off_baselines_fail_closed():
    x=compare(run(),dict(run(144,95,13.2,True),
                         capture_detector_p95_ms=9,actual_audio_onset_p95_ms=280),
              run(160,120,13.2))
    assert x["status"]=="FAIL"
    assert x["checks"]["baseline_repeatability"] is False

def test_missing_short_or_mismatched_runs_are_invalid():
    with pytest.raises(ValueError):
        compare(run(),run(coach=True),run()|{"duration_seconds":100})
    with pytest.raises(ValueError):
        compare(run(),run(coach=True)|{"game":"cod"},run())
    with pytest.raises(ValueError):
        compare(run(),run(coach=True)|{"avg_fps":0},run())
    with pytest.raises(ValueError):
        compare(run(),run(coach=True)|{"coach_enabled":False},run())

def test_bar_capture_time_is_first_low_frame_not_last():
    d=BarDetector(BarCalibration("low_health",(0,0,100,10),(20,200,50)))
    full=np.zeros((10,100,3),dtype=np.uint8)
    full[:]=[20,200,50]
    for _ in range(2):d.process(full,1000.)
    low=full.copy()
    low[:,25:]=0
    assert d.process(low,1001.) is None
    assert d.process(low,1001.08) is None
    obs=d.process(low,1001.16)
    assert obs and obs.captured_at==1001.

def test_badge_capture_time_is_first_visible_frame_not_last():
    y,x=np.mgrid[0:12,0:20]
    img=np.stack([x*8,y*14,(x+y)*6],axis=2).astype(np.uint8)
    d=BadgeDetector(BadgeConfig("zone_moving",(0,0,20,12)),img)
    assert d.process(img,1000.) is None
    assert d.process(img,1000.08) is None
    sig=d.process(img,1000.16)
    assert sig and sig.captured_at==1000.
