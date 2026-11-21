"""Temporal enemy evidence: do not call an isolated model prediction a fact.

Accept only two distinct recent frames with game-trained enemy detections,
plus the model's human-validated manifest. Negative/unknown frames are not
transformed into a claim that the entire area is clear.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from core.gamecoach_enemy_detector import EnemyCue

@dataclass
class EnemyFilterStats:
    observations:int=0
    confirmed:int=0
    insufficient:int=0
    expired:int=0

class EnemyEvidenceFilter:
    def __init__(self,min_confirmations:int=2,max_gap_s:float=.8):
        if min_confirmations<2 or min_confirmations>5 or max_gap_s<=0 or max_gap_s>2:
            raise ValueError("Unsupported temporal confirmation")
        self.min_confirmations=min_confirmations
        self.max_gap_s=max_gap_s
        self._last_at:float|None=None
        self._hits=0
        self._last_emitted_at:float|None=None
        self._min_count=9
        self._min_confidence=1.
        self.stats=EnemyFilterStats()

    def observe(self,cue:EnemyCue,now:float)->tuple[int,float,str]|None:
        self.stats.observations+=1
        if (not isinstance(cue,EnemyCue) or type(cue.visible_count) is not int
            or not 1<=cue.visible_count<=9 or not isfinite(cue.captured_at)
            or not isfinite(cue.confidence) or cue.confidence<.85
            or not -.03<=now-cue.captured_at<=.6):
            self.reset()
            self.stats.insufficient+=1
            return None
        if self._last_at is None or not 0<cue.captured_at-self._last_at<=self.max_gap_s:
            self._hits=0
            self._min_count=9
            self._min_confidence=1.
        self._last_at=cue.captured_at
        self._hits+=1
        self._min_count=min(self._min_count,cue.visible_count)
        self._min_confidence=min(self._min_confidence,cue.confidence)
        if self._hits<self.min_confirmations:
            self.stats.insufficient+=1
            return None
        # Evidence count: choose lower count across supporting frames rather
        # than asserting persistent 2+ when only one frame had two.
        self.stats.confirmed+=1
        result=(self._min_count,self._min_confidence,
                f"game-specific visible enemies corroborated across {self._hits} frames")
        self.reset()
        return result

    def reset(self):
        self._hits=0
        self._last_at=None
        self._min_count=9
        self._min_confidence=1.
