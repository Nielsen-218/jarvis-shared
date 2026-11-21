"""Synthetic RGB virtual-desktop benchmark. NOT real game FPS or speech."""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.gamecoach import CoachConfig,LiveCoach,Policy,AdaptiveSampler
from core.gamecoach_capture import BarCalibration,BarDetector

def run(frames=900,resolution='1920x1080',target_fps=144):
    width,height=map(int,resolution.lower().split('x'))
    pixels=np.zeros((height,width,3),dtype=np.uint8)
    x,y=max(5,width//40),max(5,height//16)
    right=x+max(40,width//8)
    d=BarDetector(BarCalibration('low_health',(x,y,right,y+12),(50,220,70)))
    emitted=[]
    p=Policy(CoachConfig(global_cooldown_s=.05,repeated_kind_cooldown_s=.1))
    coach=LiveCoach(emitted.append,p);coach.start()
    sampler=AdaptiveSampler();times=[]
    start=time.perf_counter()
    for i in range(frames):
        filled=.2 if (i//max(1,target_fps//3))%4==1 else .95
        pixels[y:y+12,x:right]=0
        pixels[y:y+12,x:x+int((right-x)*filled)]=[50,220,70]
        t=time.perf_counter()
        event=d.process(pixels,t)
        elapsed=(time.perf_counter()-t)*1000
        times.append(elapsed);sampler.feedback(elapsed)
        if event:coach.submit(event)
        # No sleep or render/game/GPU overhead. Measure only this narrow analysis.
    time.sleep(.025);coach.stop()
    times.sort()
    total=time.perf_counter()-start
    return {'test':'synthetic RGB framebuffer; NOT a real game',
            'resolution':resolution,'virtual_requested_fps':target_fps,
            'frames':frames,'elapsed_s':round(total,4),
            'processing_throughput_fps':round(frames/total,1),
            'analysis_p50_ms':round(times[int(len(times)*.5)],3),
            'analysis_p95_ms':round(times[min(len(times)-1,int(len(times)*.95))],3),
            'adaptive_sample_fps_end':sampler.fps,'coach':p.stats.summary(),
            'measures_real_game_fps':False,'measures_speech_onset':False}

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--frames',type=int,default=900)
    ap.add_argument('--resolution',default='1920x1080')
    ap.add_argument('--fps',type=int,default=144)
    a=ap.parse_args()
    print(json.dumps(run(a.frames,a.resolution,a.fps),indent=2))
