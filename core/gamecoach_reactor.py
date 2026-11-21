"""Nolagin observation bus: the missing link between real sensors and tactical rules.

Upstream detectors publish *named, supported, time-stamped facts*. No new
facts are inferred from game mechanics or a language model. The planner is
only allowed to choose from recent evidence. Designed to run without GPU/API.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Callable

from core.gamecoach import Observation, PRIORITY
from core.gamecoach_tactics import Evidence, TacticalCoach, Decision, FIELDS

BAR_TO_FACT = {"low_health": "health_low",
               "low_shield": "shields_low",
               "low_ammo": "ammo_low"}
VALID_SOURCES = frozenset({"pixels", "audio"})
MAX_PROOF = 160

@dataclass
class FusionStats:
    incoming: int = 0
    accepted: int = 0
    ignored: int = 0
    expired: int = 0
    choices: int = 0
    coalesced: int = 0

class EvidenceBus:
    """Latest proof for each named fact; stale or invalid values never stick.

    A cue saying low health means low health only. In particular, it cannot
    imply cover, enemy direction, teammate proximity, or a safe rotation.
    """
    def __init__(self, game: str, clock: Callable[[],float]=monotonic,
                 weights: dict[str,float] | None=None, max_age_ms: float=600):
        self.clock=clock
        self.planner=TacticalCoach(game,max_age_ms=max_age_ms,learning_weights=weights)
        self._facts:dict[str,Evidence]={}
        self.stats=FusionStats()
        self._last_signature:tuple | None=None
        self._last_signaled=-float("inf")
        self._quiet_window=.75

    def forget(self)->None:
        self._facts.clear()
        self._last_signature=None

    def update(self, field: str, value: bool|int, captured_at: float,
               confidence: float, source: str, proof: str) -> bool:
        self.stats.incoming+=1
        if (field not in FIELDS or source not in VALID_SOURCES
            or (type(value) is not bool and not
                (type(value) is int and 0<=value<=9))
            or not isinstance(proof,str) or not proof.strip()
            or not isinstance(confidence,(float,int)) or
                not isfinite(confidence) or not .85<=confidence<=1
            or not isinstance(captured_at,(float,int)) or not isfinite(captured_at)):
            self.stats.ignored+=1
            return False
        now=self.clock()
        if not -.03<=now-captured_at<=self.planner.max_age_s:
            self.stats.ignored+=1
            return False
        existing=self._facts.get(field)
        if existing and existing.seen_at>captured_at:
            self.stats.ignored+=1
            return False
        # Replacing with False is essential: older True cannot survive an
        # explicit visual recovery/negative detector signal.
        self._facts[field]=Evidence(value,captured_at,float(confidence),
                                    source,proof[:MAX_PROOF])
        self.stats.accepted+=1
        return True

    def feed_bar(self, obs: Observation)->bool:
        key=BAR_TO_FACT.get(obs.kind)
        if not key:return False
        return self.update(key,True,obs.captured_at,
                           obs.confidence,obs.source,obs.evidence)

    def clear_stale(self,now:float|None=None)->None:
        t=self.clock() if now is None else now
        expire=[key for key,e in self._facts.items()
                if (not e.valid(t,self.planner.max_age_s,False))]
        for key in expire:del self._facts[key]
        self.stats.expired+=len(expire)

    def snapshot(self,now:float|None=None)->dict[str,Evidence]:
        self.clear_stale(now)
        return dict(self._facts)

    def decide(self,now:float|None=None)->Decision|None:
        t=self.clock() if now is None else now
        facts=self.snapshot(t)
        decision=self.planner.choose(facts,t)
        if decision is None:
            self._last_signature=None
            return None
        # A scene of repeated identical facts should produce just one call.
        # A different, higher-priority emergency can break the quiet window.
        signature=(decision.kind,tuple(sorted(decision.evidence)))
        if (signature==self._last_signature and t-self._last_signaled<self._quiet_window):
            self.stats.coalesced+=1
            return None
        self._last_signature=signature
        self._last_signaled=t
        self.stats.choices+=1
        return decision

    def next_observation(self,now:float|None=None)->Observation|None:
        decision=self.decide(now)
        return self.planner.to_observation(decision) if decision else None


class TacticalDispatcher:
    """One dispatch entry point for sensor producers; no network or game input.

    Caller owns the LiveCoach thread/Policy/TTS. Only the caller can submit
    produced observations for audible speech.
    """
    def __init__(self,bus:EvidenceBus,submit:Callable[[Observation],bool]):
        self.bus=bus
        self.submit=submit
        self.submitted=0

    def bar(self,obs:Observation)->bool:
        return self.bus.feed_bar(obs)

    def fact(self,field:str,value:bool|int,captured_at:float,confidence:float,
             source:str,proof:str)->bool:
        return self.bus.update(field,value,captured_at,confidence,source,proof)

    def tick(self)->Observation|None:
        out=self.bus.next_observation()
        if out is not None and self.submit(out):
            self.submitted+=1
        return out
