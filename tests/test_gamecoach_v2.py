"""Project Nolagin v2 regressions: evidence, concurrency, replay and speech."""
import json
import math
import threading
import time
from pathlib import Path
import numpy as np
from core.gamecoach import (Callout, CoachConfig, CoachStats, LiveCoach,
                             Observation, Policy, AdaptiveSampler)
from core.gamecoach_capture import BarCalibration, BarDetector
from core.gamecoach_voice import VoiceSink
from scripts.gamecoach_replay import evaluate

def full(frame,c=(20,200,50),fill=1):
    frame[:,:,:]=0
    frame[:,0:round(frame.shape[1]*fill),:]=c
    return frame

def make_detector():
    return BarDetector(BarCalibration("low_health",(0,0,100,10),(20,200,50)))

def test_low_without_seen_full_is_not_a_health_alarm():
    d=make_detector();f=np.zeros((10,100,3),dtype=np.uint8)
    for i in range(30):assert d.process(full(f,fill=.2)) is None
    assert not d.baseline_seen

def test_occlusion_and_black_hud_are_unknown():
    d=make_detector();f=np.zeros((10,100,3),dtype=np.uint8)
    for _ in range(2):d.process(full(f))
    for _ in range(8):assert d.process(full(f,fill=0)) is None
    for _ in range(8):
        full(f,fill=.2)
        f[:,70:80,:]=[20,200,50]  # disconnected colored patch: not a bar
        assert d.process(f) is None

def test_one_alert_per_danger_transition_then_rearm():
    d=make_detector();f=np.zeros((10,100,3),dtype=np.uint8)
    for _ in range(2):assert d.process(full(f,fill=1)) is None
    alerts=[d.process(full(f,fill=.25)) for _ in range(20)]
    assert len([x for x in alerts if x])==1
    assert d.process(full(f,fill=1)) is None
    assert d.process(full(f,fill=.25)) is None  # a one-frame HUD flash doesn't rearm
    for _ in range(2):d.process(full(f,fill=1))
    assert [d.process(full(f,fill=.25)) for _ in range(3)][-1] is not None

def test_replay_matches_expected_confirmed_alert():
    case={'calibration':{'kind':'low_health','rect':[0,0,100,10],'rgb':[20,200,50]},
          'frames':[{'ms':i*120,'fill':x} for i,x in enumerate([1,1,.2,.2,.2,.2,0,0,1,1,.2,.2,.2])],
          'expected':[{'frame':4,'kind':'low_health'},{'frame':12,'kind':'low_health'}]}
    r=evaluate(case)
    assert r['passed'] and r['true_positives']==2 and not r['uses_real_frames']

def test_replay_report_fails_if_false_positive():
    case={'calibration':{'kind':'low_health','rect':[0,0,100,10],'rgb':[20,200,50]},
          'frames':[{'ms':i*100,'fill':x} for i,x in enumerate([1,1,.2,.2,.2])],
          'expected':[]}
    r=evaluate(case)
    assert not r['passed'] and r['false_positives']==1

def test_observations_require_finite_measurements():
    p=Policy(clock=lambda:100)
    assert p.decide(Observation("low_health",100,math.nan,"some bar")) is None
    assert p.decide(Observation("low_health",math.inf,1,"some bar")) is None
    assert p.stats.rejected_stale==1 and p.stats.rejected_weak==1

def test_only_latest_or_higher_priority_pending():
    gate=threading.Event();busy=threading.Event();out=[]
    def sink(c):
        out.append(c.kind)
        if len(out)==1:
            busy.set();gate.wait(timeout=1)
    c=LiveCoach(sink,Policy(CoachConfig(global_cooldown_s=0,repeated_kind_cooldown_s=0)))
    c.start()
    c.submit(Observation('low_shield',time.monotonic(),1.,'HUD'))
    assert busy.wait(.6)
    assert c.submit(Observation('low_health',time.monotonic(),1.,'HUD'))
    assert not c.submit(Observation('low_ammo',time.monotonic(),1.,'HUD'))
    gate.set()
    time.sleep(.08);c.stop()
    assert out[:2]==['low_shield','low_health']
    assert c.policy.stats.discarded_lower_priority==1

def test_capture_failures_reduce_sampling_and_stop():
    s=AdaptiveSampler()
    for _ in range(s.failure_limit):s.feedback(90,capture_ok=False)
    assert s.unavailable and s.fps==1

def test_telemetry_memory_bounded():
    s=CoachStats()
    for i in range(10000):s.latency_ms.append(float(i))
    assert len(s.latency_ms)==2048
    assert s.summary()['p99_event_to_sink_return_ms']<10000

class FakeEngine:
    def __init__(self):
        self.started=None
        self.spoken=[]
    def setProperty(self,*args):pass
    def connect(self,topic,callback):
        self.started=callback
        return 1
    def say(self,text):self.spoken.append(text)
    def runAndWait(self):
        if self.started:self.started('test')
    def disconnect(self,token):self.started=None

def call(kind='low_health',age=0):
    now=time.monotonic()
    return Callout(kind,'Low health. Find cover.',now-age,now,'pixels')

def test_voice_engine_starts_and_records_only_engine_event():
    engine=FakeEngine()
    s=VoiceSink(engine_factory=lambda:engine)
    s.start()
    assert s(call())
    for _ in range(60):
        if s.stats.engine_started:break
        time.sleep(.005)
    s.stop()
    assert engine.spoken and s.stats.engine_started==1
    assert s.stats.summary()['engine_start_p95_ms'] is not None
    assert 'NOT DAC' in s.stats.summary()['measurement_note']

def test_voice_drops_expired_warning_instead_of_late_speaking():
    def init():
        time.sleep(.04)
        return FakeEngine()
    s=VoiceSink(max_age_ms=10,engine_factory=init)
    s.start()
    assert s(call())
    time.sleep(.1);s.stop()
    assert s.stats.dropped_stale>=1
    assert s.stats.engine_started==0

def test_voice_does_not_accept_stale_event():
    s=VoiceSink(engine_factory=FakeEngine)
    s.start()
    assert not s(call(age=1))
    s.stop()
    assert s.stats.dropped_stale==1


def test_coach_service_requires_game_profile(tmp_path):
    from core.gamecoach_service import CoachService
    s=CoachService(root=tmp_path,platform='win32')
    assert 'calibration' in s.start('apex').lower() or 'profile' in s.start('apex').lower()
    assert s.status()=='Gaming coach is off.'


def test_coach_service_starts_once_and_stops_on_request(tmp_path):
    from core.gamecoach_service import CoachService
    started=threading.Event()
    finished=threading.Event()
    def runner(path, stop, voice):
        assert path.name=='coach-apex.json' and voice
        started.set()
        stop.wait(timeout=2)
        finished.set()
        return {'stopped':True}
    (tmp_path/'coach-apex.json').write_text('{}')
    s=CoachService(root=tmp_path,platform='win32',runner=runner)
    assert 'starting' in s.start('apex').lower()
    assert started.wait(.5)
    assert 'already running' in s.start('apex').lower()
    assert 'coaching apex' in s.status().lower()
    assert 'stopped' in s.stop().lower()
    assert finished.wait(.5)
    assert s.last_report=={'stopped':True}


def test_coach_service_restricts_game_keys(tmp_path):
    from core.gamecoach_service import CoachService
    s=CoachService(root=tmp_path,platform='win32')
    assert 'Choose' in s.start('../.env')
    assert 'Choose' in s.calibrate('../.env')
    assert 'Choose' in s.start('unknown')


def test_critical_health_preempts_lower_priority_cooldown():
    class Clock:
        t=1000.
        def __call__(self):return self.t
    clock=Clock()
    policy=Policy(CoachConfig(global_cooldown_s=5,repeated_kind_cooldown_s=10),clock=clock)
    assert policy.decide(Observation('low_shield',clock(),1.,'visible bar')) is not None
    clock.t+=.1
    urgent=policy.decide(Observation('low_health',clock(),1.,'visible bar'))
    assert urgent and urgent.kind=='low_health'
    clock.t+=.1
    assert policy.decide(Observation('low_health',clock(),1.,'visible bar')) is None
    assert policy.decide(Observation('low_ammo',clock(),1.,'visible bar')) is None
