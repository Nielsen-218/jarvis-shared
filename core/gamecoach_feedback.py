"""Nolagin tactical feedback: per-person evidence, bounded and honest.

Ratings are explicit statements by the player, not inferred wins or losses.
Repeated feedback breaks ties among equally urgent decisions but cannot hide
survival warnings, fabricate a game event, or override a higher urgency tier.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime,timezone
from core.gamecoach_tactics import TACTICS

STORE="gaming/gamecoach_feedback.json"

def submit(game:str,kind:str,helpful:bool,reason:str="")->dict:
    if game not in {"apex","cod"} or kind not in TACTICS or type(helpful) is not bool:
        raise ValueError("Need Apex/COD, known specific callout, and a helpful/not helpful rating")
    if not isinstance(reason,str):raise ValueError("Reason must be text")
    from core import storage
    rows=storage.load(STORE,[])
    if not isinstance(rows,list):rows=[]
    row={"game":game,"kind":kind,"category":TACTICS[kind][2],
         "helpful":helpful,"reason":reason.strip()[:160],
         "source":"owner_confirmed",
         "at":datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rows.append(row)
    storage.save(STORE,rows[-300:])
    return row

def category_preferences(game:str,rows:list)->tuple[dict[str,float],dict[str,int]]:
    if game not in {"apex","cod"}:raise ValueError("Game not supported")
    grouped=defaultdict(list)
    for row in rows[-300:]:
        if (not isinstance(row,dict) or row.get("source")!="owner_confirmed"
            or row.get("game")!=game or row.get("kind") not in TACTICS
            or type(row.get("helpful")) is not bool):
            continue
        kind=row["kind"]
        grouped[TACTICS[kind][2]].append(row["helpful"])
    bias={}
    samples={}
    for category,values in grouped.items():
        samples[category]=len(values)
        if len(values)<3:
            continue
        helpful_rate=sum(values)/len(values)
        # Small bounded ranking adjustment. More data is needed to be sure.
        bias[category]=round((helpful_rate-.5)*.8,3)
    return bias,samples

def current_preferences(game:str)->tuple[dict[str,float],dict[str,int]]:
    from core import storage
    rows=storage.load(STORE,[])
    return category_preferences(game,rows if isinstance(rows,list) else [])

def combine_training_weights(trained:dict,preferences:dict)->dict[str,float]:
    return {key:round(max(-.4,min(.4,float(trained.get(key,0))+float(preferences.get(key,0)))),3)
            for key in set(trained)|set(preferences)}
