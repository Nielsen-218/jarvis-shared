"""Deterministic offline frame-replay evaluation for Project Nolagin.

JSON cases reference private screenshot files by relative path OR synthetic
bar fills. Neither images nor speech are uploaded or written to reports.

Case schema:
{
  "calibration":{"kind":"low_health","rect":[0,0,100,10],"rgb":[15,200,20]},
  "frames":[{"ms":0,"fill":1.0},{"ms":150,"fill":0.2},
            {"ms":300,"image":"frames/003.png"}],
  "expected":[{"frame":2,"kind":"low_health"}]
}
A synthetic fill is explicitly labeled synthetic; it proves state-machine logic,
NOT that a real Apex or COD HUD matches the chosen pixel color.
"""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
from statistics import mean
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.gamecoach import CoachConfig,Policy
from core.gamecoach_capture import BarDetector
from scripts.gamecoach_live import calibration

class ReplayClock:
    def __init__(self):self.t=1000.
    def __call__(self):return self.t

def evaluate(case:dict,root:Path=Path('.'))->dict:
    cfg=calibration(case['calibration'])
    sensor=BarDetector(cfg)
    clock=ReplayClock()
    policy=Policy(CoachConfig(global_cooldown_s=0,repeated_kind_cooldown_s=0),
                  clock=clock)
    events=[];times=[]
    x1,y1,x2,y2=cfg.rect
    w,h=x2-x1,y2-y1
    frames=case.get('frames',[])
    for i,item in enumerate(frames):
        ms=float(item['ms'])
        if i and ms<=float(frames[i-1]['ms']):
            raise ValueError('Replay timestamps must strictly increase')
        clock.t=1000.+ms/1000.
        start=time.perf_counter()
        if 'image' in item:
            from PIL import Image
            frame=np.asarray(Image.open(root/item['image']).convert('RGB'))
        elif 'fill' in item:
            fill=float(item['fill'])
            if not 0<=fill<=1:raise ValueError('Fill must be 0 to 1')
            frame=np.zeros((max(y2,1),max(x2,1),3),dtype=np.uint8)
            frame[y1:y2,x1:x1+round(w*fill)]=cfg.rgb
        else:
            raise ValueError('Replay frame requires image or synthetic fill')
        obs=sensor.process(frame,clock())
        times.append((time.perf_counter()-start)*1000)
        if obs:
            call=policy.decide(obs)
            if call:events.append({'frame':i,'kind':call.kind,'ms':ms})
    target={(int(x['frame']),x['kind']) for x in case.get('expected',[])}
    actual={(x['frame'],x['kind']) for x in events}
    times.sort()
    return {'frames':len(frames),'events':events,
            'true_positives':len(actual & target),
            'misses':len(target-actual),'false_positives':len(actual-target),
            'passed':actual==target,
            'analysis_only_p95_ms':round(times[int((len(times)-1)*.95)],4) if times else None,
            'uses_real_frames':any('image' in x for x in frames),
            'note':'Replay only; excludes screen capture, GPU contention and speech'}

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('case',type=Path)
    ap.add_argument('--report',type=Path)
    a=ap.parse_args()
    result=evaluate(json.loads(a.case.read_text(encoding='utf-8')),a.case.parent)
    print(json.dumps(result,indent=2))
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True)
        a.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    sys.exit(0 if result['passed'] else 1)
