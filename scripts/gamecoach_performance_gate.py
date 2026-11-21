"""Nolagin A/B/A gaming benchmark gate. Never fabricates hardware results.

Input JSON generated *from actual FrameView reports*, same game scene, settings
and capture duration. Fail closed when mandatory fields or units are missing.

python scripts/gamecoach_performance_gate.py offA.json on.json offB.json
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

REQUIRED=("avg_fps","p1_low_fps","p99_frametime_ms","duration_seconds")
MATCH=("game","resolution","graphics_preset")

def _number(d,key):
    n=d.get(key)
    if isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or n<=0:
        raise ValueError(f"Missing positive measured number: {key}")
    return float(n)

def compare(off_a:dict,on:dict,off_b:dict)->dict:
    for key in MATCH:
        if not all(isinstance(d.get(key),str) and d.get(key) for d in (off_a,on,off_b)):
            raise ValueError(f"Missing match descriptor: {key}")
        if len({d[key] for d in (off_a,on,off_b)}) != 1:
            raise ValueError(f"Not comparable: {key} differs across runs")
    for d in (off_a,on,off_b):
        for key in REQUIRED:_number(d,key)
        if d["duration_seconds"]<600:
            raise ValueError("Each hardware capture must be at least 600 seconds")
    if not (off_a.get("coach_enabled") is False and
            on.get("coach_enabled") is True and
            off_b.get("coach_enabled") is False):
        raise ValueError("Required run order: coach OFF, ON, OFF")
    base={key:(_number(off_a,key)+_number(off_b,key))/2
          for key in ("avg_fps","p1_low_fps","p99_frametime_ms")}
    drift=max(abs(_number(off_a,"avg_fps")-_number(off_b,"avg_fps"))/base["avg_fps"],
              abs(_number(off_a,"p1_low_fps")-_number(off_b,"p1_low_fps"))/base["p1_low_fps"])
    avg_loss=100*(base["avg_fps"]-_number(on,"avg_fps"))/base["avg_fps"]
    low_loss=100*(base["p1_low_fps"]-_number(on,"p1_low_fps"))/base["p1_low_fps"]
    frametime_increase=100*(_number(on,"p99_frametime_ms")-base["p99_frametime_ms"])/base["p99_frametime_ms"]
    # Capture detector and measured physical audio onset must not be confused
    # with text sink/pyttsx3 callback latency from CI.
    detail={"baseline_fps":round(base["avg_fps"],2),
            "on_fps":round(_number(on,"avg_fps"),2),
            "avg_fps_regression_pct":round(avg_loss,2),
            "one_percent_low_regression_pct":round(low_loss,2),
            "p99_frametime_regression_pct":round(frametime_increase,2),
            "baseline_drift_pct":round(drift*100,2)}
    checks={
        "baseline_repeatability":drift<=.05,
        "average_fps":avg_loss<=2,
        "one_percent_low":low_loss<=3,
        "p99_frametime":frametime_increase<=5,
    }
    optional={"capture_detector_p95_ms":15,"actual_audio_onset_p95_ms":350}
    for key,target in optional.items():
        try:checks[key]=_number(on,key)<=target
        except ValueError:checks[key]=None
    status=("PASS" if all(v is True for v in checks.values()) else
            "FAIL" if any(v is False for v in checks.values()) else "INCOMPLETE")
    return {"status":status,"checks":checks,"metrics":detail,
            "source":"user-supplied actual hardware measurements; not calculated from synthetic replay",
            "anti_cheat_approval":"not established by benchmarking"}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("off_a",type=Path)
    ap.add_argument("on",type=Path)
    ap.add_argument("off_b",type=Path)
    ap.add_argument("--out",type=Path)
    a=ap.parse_args()
    rows=[json.loads(p.read_text(encoding="utf-8")) for p in
          (a.off_a,a.on,a.off_b)]
    result=compare(*rows)
    print(json.dumps(result,indent=2))
    if a.out:
        a.out.parent.mkdir(parents=True,exist_ok=True)
        a.out.write_text(json.dumps(result,indent=2),encoding="utf-8")
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__":
    raise SystemExit(main())
