"""Jarvis gaming coach: opt-in real screen HUD monitor and optionally CPU SAPI5 voice.

No game process interaction, injections, gameplay input, GPU model or paid API.
Use on a game replay/video first, then compare live game fps against no-coach.
"""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.gamecoach import AdaptiveSampler,LiveCoach,Policy,CoachConfig,PRIORITY
from core.gamecoach_capture import BarCalibration,BarDetector,ScreenSource
from core.gamecoach_voice import VoiceSink
from core.gamecoach_reactor import EvidenceBus,TacticalDispatcher
from core.gamecoach_badges import load_badge_file,BadgeDetector
from core.gamecoach_enemy_detector import GameEnemyDetector,load_config
from core.gamecoach_enemy_filter import EnemyEvidenceFilter
from core.gamecoach_vision_worker import VisionWorker
from core.gamecoach_focus import game_in_foreground
from core.gamecoach_session import SessionInsights

def calibrate(path:Path,kind:str):
    import cv2,numpy as np
    source=ScreenSource()
    try:
        frame=None
        for _ in range(5):
            frame=source.grab()
            if frame is not None:break
            time.sleep(.05)
        if frame is None:raise RuntimeError('Screen capture unavailable')
        x,y,w,h=(int(v) for v in cv2.selectROI('JARVIS: select FULL colored HUD bar, press ENTER',
                                               cv2.cvtColor(frame,cv2.COLOR_RGB2BGR),fromCenter=False))
        cv2.destroyAllWindows()
        if w<15 or h<2:raise ValueError('No usable ROI selected; calibration not saved')
        sample=frame[y:y+h,x:x+max(1,w//5),:3]
        rgb=[int(a) for a in np.median(sample.reshape(-1,3),axis=0)]
        data={'kind':kind,'rect':[x,y,x+w,y+h],'rgb':rgb,
              'tolerance':48,'low_threshold':.28,'full_threshold':.75,
              'confirmations':3,'baseline_required':True,'reviewed':False}
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data,indent=2),encoding='utf-8')
        print('Saved calibration:',path,'; verify ROI before live use')
    finally:
        source.close()

def calibration(raw:dict)->BarCalibration:
    return BarCalibration(raw['kind'],tuple(raw['rect']),tuple(raw['rgb']),
                          tolerance=int(raw.get('tolerance',48)),
                          low_threshold=float(raw.get('low_threshold',.28)),
                          confirmations=int(raw.get('confirmations',3)),
                          baseline_required=bool(raw.get('baseline_required',True)),
                          full_threshold=float(raw.get('full_threshold',.75)))

def prepare_detectors(paths:list[Path]):
    """Build simultaneous ROI detectors, one bounded desktop capture for all."""
    if not 1<=len(paths)<=3:raise ValueError("Calibrate one to three HUD bars")
    configs=[calibration(json.loads(p.read_text(encoding='utf-8'))) for p in paths]
    if len({c.kind for c in configs})!=len(configs):
        raise ValueError("Duplicate HUD calibration kinds")
    left=min(c.rect[0] for c in configs)
    top=min(c.rect[1] for c in configs)
    right=max(c.rect[2] for c in configs)
    bottom=max(c.rect[3] for c in configs)
    if (left<0 or top<0 or right<=left or bottom<=top or
            right-left>3840 or bottom-top>2160):
        raise ValueError("HUD calibration box is outside supported capture bounds")
    items=[]
    for c in configs:
        a,b,d,e=c.rect
        relative=(a-left,b-top,d-left,e-top)
        detector=BarDetector(BarCalibration(c.kind,relative,c.rgb,c.tolerance,
                             c.low_threshold,c.confirmations,
                             c.baseline_required,c.full_threshold))
        items.append((c.kind,detector))
    return (left,top,right,bottom),items

def prepare_sources(paths:list[Path],badge_paths:list[Path]|None=None):
    region,bars=prepare_detectors(paths)
    if not badge_paths:return region,bars,[]
    from json import loads
    base_x,base_y,right,bottom=region
    profiles=[(p,loads(p.read_text(encoding="utf-8"))) for p in badge_paths]
    for _,raw in profiles:
        x1,y1,x2,y2=(int(v) for v in raw["rect"])
        if x1<0 or y1<0 or x2<=x1 or y2<=y1:
            raise ValueError("Invalid badge bounds")
        base_x=min(base_x,x1)
        base_y=min(base_y,y1)
        right=max(right,x2)
        bottom=max(bottom,y2)
    if right-base_x>3840 or bottom-base_y>2160:
        raise ValueError("Badge and HUD calibrations extend beyond capture budget")
    shift_x,shift_y=region[0]-base_x,region[1]-base_y
    relocated=[]
    for kind,detector in bars:
        from core.gamecoach_capture import BarCalibration
        old=detector.c
        a,b,c,d=old.rect
        moved=BarCalibration(old.kind,(a+shift_x,b+shift_y,c+shift_x,d+shift_y),
                             old.rgb,old.tolerance,old.low_threshold,
                             old.confirmations,old.baseline_required,old.full_threshold)
        relocated.append((kind,BarDetector(moved)))
    badges=[]
    for p,_ in profiles:
        cfg,template=load_badge_file(p,(base_x,base_y))
        badges.append(BadgeDetector(cfg,template))
    return (base_x,base_y,right,bottom),relocated,badges

def live(path:Path,seconds:float,quiet:bool,voice:bool,report:Path|None=None,
         stop_event=None,extra_paths:list[Path]|None=None,game:str="apex",
         badge_paths:list[Path]|None=None,training_weights:dict|None=None,
         vision_manifest:Path|None=None,focus_check=None)->dict:
    region,detectors,badges=prepare_sources([path]+list(extra_paths or []),badge_paths)
    sampler=AdaptiveSampler(fps=12,min_fps=2,max_fps=16,slow_budget_ms=15)
    source=ScreenSource()
    vision_worker=None
    vision_filter=None
    if vision_manifest is not None:
        # Opt-in only: no generic object model can identify enemies reliably.
        cfg=load_config(vision_manifest,game)
        vision_worker=VisionWorker(GameEnemyDetector(cfg),interval_s=.4)
        vision_filter=EnemyEvidenceFilter(min_confirmations=2,max_gap_s=.85)
        vision_worker.start()
    speaker=VoiceSink() if voice and not quiet else None
    if speaker:speaker.start()
    focus_check=focus_check or game_in_foreground
    session=SessionInsights(game)
    def sink(event):
        if not focus_check(game):return
        print(f'[{time.strftime("%H:%M:%S")}] {event.text} '
              f'(event→sink {(time.monotonic()-event.captured_at)*1000:.0f}ms)')
        speech_accepted=bool(speaker(event)) if speaker else False
        session.callout(event.kind,speech_accepted)
    coach=LiveCoach(sink,Policy(CoachConfig(quiet=quiet)))
    coach.start()
    bus=EvidenceBus(game,weights=training_weights)
    dispatch=TacticalDispatcher(bus,coach.submit)
    start=time.monotonic();sample_ms=[];received=0;signals=0;no_change=0;errors=0
    focus_pauses=0
    was_focused=False
    try:
        while seconds<=0 or time.monotonic()-start<seconds:
            if stop_event is not None and stop_event.is_set():break
            if not focus_check(game):
                if was_focused:
                    bus.forget()
                    for _,detector in detectors:detector.reset()
                    for sensor in badges:sensor.reset()
                    if vision_filter:vision_filter.reset()
                    if vision_worker:vision_worker.invalidate()
                was_focused=False
                focus_pauses+=1
                if stop_event is not None:stop_event.wait(.25)
                else:time.sleep(.25)
                continue
            was_focused=True
            then=time.monotonic()
            # One DXGI capture. Full frame is opt-in ONLY when a trained enemy
            # model exists; the ordinary HUD mode keeps a small screen crop.
            vision_active=bool(vision_worker and not vision_worker.disabled)
            frame=source.grab(region=None if vision_active else region)
            capture_error=bool(getattr(source,"failed",False))
            if frame is not None:
                received+=1
                captured_at=time.monotonic()  # CPU observation time, not Present time.
                if vision_active:
                    vision_worker.offer(frame,captured_at)
                    x1,y1,x2,y2=region
                    if y2>frame.shape[0] or x2>frame.shape[1]:
                        raise ValueError("HUD rectangle outside the captured primary monitor")
                    hud_frame=frame[y1:y2,x1:x2]
                else:
                    hud_frame=frame
                raw_events=[]
                for _,detector in detectors:
                    signal=detector.process(hud_frame,captured_at)
                    # Each new frame revalidates the low state. A low bar
                    # continuing after .6s remains evidence even if the
                    # original edge callout has long since expired.
                    fresh=detector.current_low_observation()
                    if fresh:
                        dispatch.bar(fresh)
                    if signal:
                        signals+=1
                        session.alert(signal.kind)
                        raw_events.append(signal)
                for sensor in badges:
                    event=sensor.process(hud_frame,captured_at)
                    if event:
                        signals+=1
                        if event.value:session.alert(event.field)
                        dispatch.fact(event.field,event.value,event.captured_at,
                                      event.confidence,event.source,event.proof)
                if vision_active:
                    cue=vision_worker.poll()
                    if cue:
                        confirmed=vision_filter.observe(cue,captured_at)
                        if confirmed:
                            count,confidence,proof=confirmed
                            dispatch.fact("enemy_visible",True,cue.captured_at,
                                          confidence,"pixels",proof)
                            dispatch.fact("visible_enemy_count",count,cue.captured_at,
                                          confidence,"pixels",proof)
                tactic=dispatch.tick()
                # One spoken decision per frame: don't say "low health" followed
                # immediately by its more specific tactical override.
                if tactic is None and raw_events:
                    strongest=max(raw_events,key=lambda s:PRIORITY[s.kind])
                    coach.submit(strongest)
            elif not capture_error:
                no_change+=1
            else:
                errors+=1
            elapsed=(time.monotonic()-then)*1000
            sample_ms.append(elapsed)
            sampler.feedback(elapsed,capture_ok=not capture_error)
            if sampler.unavailable:
                print('Screen capture is unavailable repeatedly; coach stopped to avoid overhead.')
                break
            pause=max(0,1/sampler.fps-elapsed/1000)
            if stop_event is not None:stop_event.wait(pause)
            else:time.sleep(pause)
    except KeyboardInterrupt:
        pass
    finally:
        source.close()
        if vision_worker:vision_worker.stop()
        coach.stop()
        if speaker:speaker.stop()
    elapsed_s=time.monotonic()-start
    session_summary=session.finish(elapsed_s)
    sample_ms.sort()
    summary={'backend':source.backend,'calibrated_signals':[kind for kind,_ in detectors],
             'calibrated_badges':[sensor.cfg.field for sensor in badges],
             'unchanged_polls':no_change,'capture_errors':errors,
             'unfocused_pauses':focus_pauses,
             'tactical':vars(bus.stats),
             'session_insights':session_summary,
             'enemy_vision':(vision_worker.summary()|{"detector":vision_worker.detector.summary(),
                               "corroboration":vars(vision_filter.stats)}) if vision_worker else
                               {"enabled":False,"reason":"no validated game-specific ONNX model configured"},
             'received_new_frames':received,
             'sensor_events':signals,'capture_failed_out':sampler.unavailable,
             'capture_detector_p95_ms':round(sample_ms[int((len(sample_ms)-1)*.95)],2) if sample_ms else None,
             'coach':coach.policy.stats.summary(),
             'speech':speaker.stats.summary() if speaker else {'enabled':False},
             'warning':'NOT game FPS, p99 game frame time or actual audio DAC onset'}
    print(json.dumps(summary,indent=2))
    if report is not None:
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--file',default='data/gaming/coach-calibration.json')
    ap.add_argument('--also',type=Path,action='append',default=[],help='Additional calibrated bar files (max 2)')
    ap.add_argument('--badge',type=Path,action='append',default=[],help='Optional calibrated HUD icon JSON')
    ap.add_argument('--game',choices=['apex','cod'],default='apex')
    ap.add_argument('--vision-manifest',type=Path,help='Opt-in trained and validated local model manifest')
    ap.add_argument('--calibrate',action='store_true')
    ap.add_argument('--kind',choices=['low_health','low_shield','low_ammo'],default='low_health')
    ap.add_argument('--seconds',type=float,default=30)
    ap.add_argument('--quiet',action='store_true')
    ap.add_argument('--voice',action='store_true',help='Use CPU Windows SAPI5 locally; NOT enabled by default')
    ap.add_argument('--report',type=Path)
    a=ap.parse_args()
    if a.calibrate:calibrate(Path(a.file),a.kind)
    else:live(Path(a.file),a.seconds,a.quiet,a.voice,a.report,
              extra_paths=a.also,badge_paths=a.badge,game=a.game,
              vision_manifest=a.vision_manifest)
