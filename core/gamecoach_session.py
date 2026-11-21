"""Nolagin private session coaching feedback from ACTUAL confirmed HUD alerts.

Stores no raw frames, voices, player coordinates, private chat or gamer tags.
Does NOT invent match outcomes or claim calls were audible. Input is the local
live pipeline's accepted sensor transitions and text/voice queue events.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass,field
from time import monotonic
from threading import Lock

FOCUS={
    "break_sightline":("positioning","Plan cover and escape before your next challenge."),
    "contested_low_health":("decision","Break contact when low; don't re-peek before healing."),
    "low_health":("positioning","Enter fights with a planned retreat to cover."),
    "low_shield":("healing","Recharge before challenging the same angle again."),
    "contested_low_shields":("healing","Break line of sight to reset shields."),
    "ring_split_regroup":("rotation","Ping and regroup before ring movement."),
    "knock_split_discipline":("teamplay","Coordinate knock conversions; avoid solo chase."),
    "unsafe_revive_threat":("teamplay","Clear the fight before attempting a contested revive."),
    "rotate_zone":("rotation","Choose a safe ring route before the fight drags on."),
    "low_ammo":("decision","Plan ammo/reload or weapon-switch timing before exposing."),
}

@dataclass
class SessionInsights:
    game:str
    start:float=field(default_factory=monotonic)
    raw_alerts:Counter=field(default_factory=Counter)
    callout_queued:Counter=field(default_factory=Counter)
    voice_queue_accepted:Counter=field(default_factory=Counter)
    session_active:bool=True
    _lock:Lock=field(default_factory=Lock,repr=False)

    def __post_init__(self):
        if self.game not in {"apex","cod"}:raise ValueError("Unknown game")

    def alert(self,kind:str)->None:
        with self._lock:
            if self.session_active and (kind in FOCUS or kind in
                                        {"teammate_down","zone_moving"}):
                self.raw_alerts[kind]+=1

    def callout(self,kind:str,voice_queued:bool)->None:
        with self._lock:
            if self.session_active and isinstance(kind,str) and len(kind)<=64:
                self.callout_queued[kind]+=1
                if voice_queued:self.voice_queue_accepted[kind]+=1

    def finish(self,elapsed_s:float|None=None)->dict:
        with self._lock:
            self.session_active=False
            alerts=self.raw_alerts.copy()
            queued=self.callout_queued.copy()
            speech=self.voice_queue_accepted.copy()
        elapsed=max(0.,monotonic()-self.start) if elapsed_s is None else max(0.,elapsed_s)
        reasons=[]
        # Unique alerts approximate transitions, not all failures or deaths.
        for kind,count in (alerts|queued).most_common():
            if kind in FOCUS:
                category,drill=FOCUS[kind]
                reasons.append({"category":category,"signal":kind,
                                "confirmed_occurrences":int(count),"suggested_drill":drill})
        seen=set()
        focus=[]
        for reason in reasons:
            if reason["category"] not in seen:
                seen.add(reason["category"])
                focus.append(reason)
            if len(focus)==2:break
        return {
            "game":self.game,
            "duration_s":round(elapsed,2),
            "sensor_transitions":dict(alerts),
            "text_callout_sink_events":dict(queued),
            "voice_queue_acceptances":dict(speech),
            "training_suggestions":focus,
            "match_result":"UNKNOWN; ask the player to confirm",
            "evidence_limit":"Based on confirmed HUD transitions and local callout queue events, not a full match review. No causal win/loss attribution.",
            "audible_voice_verified":False,
        }

def describe(summary:dict)->str:
    if not isinstance(summary,dict) or summary.get("game") not in {"apex","cod"}:
        return "No verified local gamecoach session is available."
    suggestions=summary.get("training_suggestions",[])
    if not suggestions:
        return ("I recorded no useful confirmed coach signals last session. "
                "I can't judge your play or guess why you won or lost.")
    rows=[f"{x['category']}: {x['suggested_drill']}" for x in suggestions[:2]
          if isinstance(x,dict) and x.get("category") and x.get("suggested_drill")]
    return ("Based only on recorded coach events: "+" ".join(rows)+
            " These are practice hypotheses, not proven causes of match results. "
            "Tell me what actually happened so I can learn.")
