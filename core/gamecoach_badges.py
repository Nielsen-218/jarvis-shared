"""Pixel-registered HUD icon sensor, not object detection or game-memory access.

A user captures an icon *while it is present*, then the detector checks only
that fixed rectangle on later frames. This cannot locate players, classify
cover, infer distance/direction, or reveal any hidden game information.

No OpenCV/CUDA in the hot path: small NumPy color difference on a cropped ROI.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from time import monotonic
import numpy as np

# Only HUD state facts with observable, distinctive static badges.
# 'enemy_visible' deliberately excluded: a static UI template is NOT proof
# of an actual enemy present on-screen.
TEMPLATE_FACTS=frozenset({
    "zone_moving", "teammate_down", "confirmed_enemy_knock",
    "objective_visible", "objective_timer_low",
})

@dataclass(frozen=True)
class BadgeConfig:
    field: str
    rect: tuple[int,int,int,int]  # left, top, right, bottom, relative to ROI
    min_similarity: float=.95
    confirmations: int=3
    disappear_confirmations: int=3

@dataclass(frozen=True)
class BadgeSignal:
    field: str
    value: bool
    captured_at: float
    confidence: float
    source: str
    proof: str

class BadgeDetector:
    """Verified fixed-position template transition. No expanding search over screen."""
    def __init__(self,config:BadgeConfig,template:np.ndarray):
        if config.field not in TEMPLATE_FACTS:
            raise ValueError("Unsupported HUD badge: must be a specifically visible status icon")
        a,b,c,d=config.rect
        if not (0<=a<c and 0<=b<d) or c-a>400 or d-b>200:
            raise ValueError("Invalid badge rectangle")
        if not .92<=config.min_similarity<=.999 or config.confirmations<2 or config.disappear_confirmations<2:
            raise ValueError("Invalid badge confirmation thresholds")
        if not isinstance(template,np.ndarray) or template.dtype!=np.uint8 or template.shape!=(d-b,c-a,3):
            raise ValueError("The template must match the ROI RGB shape exactly")
        if float(template.std())<18:
            raise ValueError("Uninformative low-contrast badge template")
        self.cfg=config
        self.template=template.copy()
        self._on=0
        self._off=0
        self._visible=False
        self._first_on_at=None
        self._first_off_at=None
        self.last_score=0.

    def reset(self)->None:
        self._on=0
        self._off=0
        self._visible=False
        self._first_on_at=None
        self._first_off_at=None

    def process(self,frame:np.ndarray,captured_at:float|None=None)->BadgeSignal|None:
        x1,y1,x2,y2=self.cfg.rect
        if not isinstance(frame,np.ndarray) or frame.ndim!=3 or frame.shape[2]<3:
            return None
        if not (0<=x1<x2<=frame.shape[1] and 0<=y1<y2<=frame.shape[0]):
            return None
        # int16 avoids uint8 wraparound on subtraction.
        a=frame[y1:y2,x1:x2,:3].astype(np.int16,copy=False)
        distance=np.abs(a-self.template.astype(np.int16)).mean()
        score=float(1-distance/255)
        self.last_score=score
        matching=score>=self.cfg.min_similarity
        stamp=monotonic() if captured_at is None else captured_at
        if matching:
            if self._on==0:self._first_on_at=stamp
            self._on+=1
            self._off=0
            self._first_off_at=None
        else:
            if self._off==0:self._first_off_at=stamp
            self._off+=1
            self._on=0
            self._first_on_at=None
        if not self._visible and self._on>=self.cfg.confirmations:
            self._visible=True
            # Model confidence here is only confidence in pixel-pattern
            # similarity, NOT in the semantic meaning of this badge.
            return BadgeSignal(self.cfg.field,True,self._first_on_at or stamp,min(1.,score),
                               "pixels",f"calibrated on-screen {self.cfg.field} badge at {score:.1%} similarity")
        if self._visible and self._off>=self.cfg.disappear_confirmations:
            self._visible=False
            return BadgeSignal(self.cfg.field,False,self._first_off_at or stamp,.99,
                               "pixels",f"calibrated {self.cfg.field} badge absent for {self._off} fresh frames")
        return None

def load_badge_file(path,roi_origin:tuple[int,int]=(0,0))->tuple[BadgeConfig,np.ndarray]:
    """Load local JSON profile and adjacent PNG. Path never leaves local PC."""
    from pathlib import Path
    from json import loads
    from PIL import Image
    f=Path(path)
    raw=loads(f.read_text(encoding="utf-8"))
    if raw.get("reviewed") is not True:
        raise ValueError("Badge template requires explicit visual calibration")
    x1,y1,x2,y2=(int(v) for v in raw["rect"])
    origin_x,origin_y=roi_origin
    rect=(x1-origin_x,y1-origin_y,x2-origin_x,y2-origin_y)
    # Use only a sibling filename, never absolute/.. paths provided by JSON.
    name=str(raw["image"])
    if not name.endswith(".png") or "/" in name or "\\" in name or ".." in name:
        raise ValueError("Badge image must be an adjacent .png filename")
    arr=np.asarray(Image.open(f.parent/name).convert("RGB"))
    return BadgeConfig(str(raw["field"]),rect,
                       min_similarity=float(raw.get("min_similarity",.95)),
                       confirmations=int(raw.get("confirmations",3))),arr
