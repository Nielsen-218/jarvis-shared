"""Strict local clip-review JSON extraction and grounded timestamp filter.

An Ollama VLM narrative is an unverified hypothesis, not match telemetry.
Every weakness timestamp must exist among the frames actually shown to it.
"""
from __future__ import annotations
import json

CATEGORIES=frozenset({
    "positioning","decision","healing","rotation","teamplay",
    "aim","crosshair","movement","objective","looting",
})

def parse_model_json(raw: str) -> dict:
    if not isinstance(raw,str) or len(raw)>200_000:
        raise ValueError("Local model response is empty or too long")
    s=raw.strip()
    fence=chr(96)*3
    if s.startswith(fence):
        s=s.partition("\n")[2]
        s=s.rsplit(fence,1)[0].strip()
    decoder=json.JSONDecoder()
    # Local models may prepend commentary; bounded scan avoids greedy regex.
    for offset,ch in enumerate(s[:4000]):
        if ch!="{":continue
        try:
            obj,_=decoder.raw_decode(s[offset:])
            if isinstance(obj,dict):return obj
        except (ValueError,RecursionError):
            continue
    raise ValueError("Local model returned no valid JSON object")

def sanitize_review(raw:str,valid_timestamps:list[str])->dict:
    obj=parse_model_json(raw)
    summary=obj.get("summary","")
    if not isinstance(summary,str):summary=""
    allowed=set(valid_timestamps)
    strengths=obj.get("strengths",[])
    if not isinstance(strengths,list):strengths=[]
    strengths=[s[:180] for s in strengths[:5] if isinstance(s,str)]
    weaknesses=obj.get("weak_points",[])
    if not isinstance(weaknesses,list):weaknesses=[]
    points=[]
    for w in weaknesses[:15]:
        if not isinstance(w,dict) or w.get("category") not in CATEGORIES:
            continue
        stamp=w.get("time")
        if not isinstance(stamp,str) or stamp not in allowed:
            continue
        text=w.get("what")
        fix=w.get("fix")
        if not isinstance(text,str) or not text.strip() or not isinstance(fix,str) or not fix.strip():
            continue
        points.append({"category":w["category"],"time":stamp,
                       "what":text[:260],"fix":fix[:260]})
    drills=obj.get("drills",[])
    if not isinstance(drills,list):drills=[]
    drills=[s[:200] for s in drills[:3] if isinstance(s,str)]
    return {"summary":summary[:500],"strengths":strengths,
            "weak_points":points,"drills":drills}
