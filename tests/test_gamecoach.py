"""Offline regression coverage for the gamecoach's evidence and concurrency gates."""
import time
import numpy as np
import pytest
from core.gamecoach import AdaptiveSampler,CoachConfig,LiveCoach,Observation,Policy
from core.gamecoach_capture import BarCalibration,BarDetector

class Clock:
    def __init__(self,t=1000.): self.t=t
    def __call__(self): return self.t

def obs(clock,kind='low_health',age=.05,confidence=.99,evidence='HUD shows low bar',source='pixels'):
    return Observation(kind,clock()-age,confidence,evidence,source)

def test_rule_emits_visible_cue():
    t=Clock();p=Policy(clock=t)
    c=p.decide(obs(t))
    assert c and c.kind=='low_health' and 'cover' in c.text
    assert p.stats.observations==1

@pytest.mark.parametrize('o,field',[
    ({'kind':'enemy_behind_wall'},'rejected_unsupported'),
    ({'evidence':''},'rejected_unsupported'),
    ({'source':'game_memory'},'rejected_unsupported'),
    ({'confidence':.4},'rejected_weak'),
    ({'age':1},'rejected_stale'),
    ({'age':-1},'rejected_stale')])
def test_unobserved_cues_never_speak(o,field):
    t=Clock();p=Policy(clock=t)
    assert p.decide(obs(t,**o)) is None
    assert getattr(p.stats,field)==1

def test_deduplicate_and_ratelimit():
    t=Clock();p=Policy(CoachConfig(global_cooldown_s=0,repeated_kind_cooldown_s=0,max_callouts_per_minute=2),clock=t)
    assert p.decide(obs(t))
    assert p.decide(obs(t,kind='low_shield'))
    assert p.decide(obs(t,kind='low_ammo')) is None
    t.t+=61
    assert p.decide(obs(t,kind='low_ammo'))

def test_kind_cooldown():
    t=Clock();p=Policy(clock=t)
    assert p.decide(obs(t))
    t.t+=4
    assert p.decide(obs(t)) is None
    t.t+=9
    assert p.decide(obs(t))

def test_quiet_mode():
    t=Clock();p=Policy(CoachConfig(quiet=True),clock=t)
    assert p.decide(obs(t)) is None

def test_sampler_reduces_load_and_recovers():
    s=AdaptiveSampler()
    assert s.feedback(32)==3
    assert s.feedback(32)==1
    for _ in range(40):s.feedback(4)
    assert s.fps==3

def test_colorbar_requires_three_consistent_frames():
    d=BarDetector(BarCalibration('low_health',(10,10,110,20),(12,225,15)))
    f=np.zeros((100,130,3),dtype=np.uint8)
    f[10:20,10:110,:]=[12,225,15]
    assert d.process(f) is None
    assert d.process(f) is None  # two full frames arm the detector
    f[10:20,35:110,:]=[3,3,3]
    assert d.process(f) is None
    assert d.process(f) is None
    signal=d.process(f)
    assert signal and signal.kind=='low_health' and '25%' in signal.evidence
    assert d.process(f) is None
    f[10:20,10:110,:]=[12,225,15]
    assert d.process(f) is None
    assert d.process(f) is None  # rearm only after two full frames
    f[10:20,35:110,:]=[3,3,3]
    for _ in range(2):assert d.process(f) is None
    assert d.process(f) is not None

def test_queue_stays_bounded_even_if_output_blocks():
    out=[]
    def slow(c):out.append(c);time.sleep(.02)
    p=Policy(CoachConfig(global_cooldown_s=0,repeated_kind_cooldown_s=0))
    c=LiveCoach(slow,p);c.start()
    for _ in range(120):
        c.submit(Observation('low_health',time.monotonic(),1.,'synthetic HUD','pixels'))
    time.sleep(.14)
    c.stop()
    assert 1<=len(out)<=8
    assert p.stats.overwritten>100 and p.stats.errors==0

def test_hidden_bar_does_not_generate_health_alerts():
    d=BarDetector(BarCalibration('low_shield',(0,0,10,4),(0,200,250),confirmations=2))
    assert d.process(np.zeros((4,10,3),dtype=np.uint8)) is None
    assert d.process(np.zeros((4,10,3),dtype=np.uint8)) is None
    assert d.process(np.zeros((4,3,3),dtype=np.uint8)) is None
