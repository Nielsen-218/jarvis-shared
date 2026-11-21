"""Read-only desktop capture and conservative calibrated, visible-pixel HUD detector."""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any
import numpy as np
from core.gamecoach import Observation

@dataclass(frozen=True)
class BarCalibration:
    kind: str
    rect: tuple[int,int,int,int]  # desktop left, top, right, bottom; ROI-relative in detector
    rgb: tuple[int,int,int]
    tolerance: int = 48
    low_threshold: float = 0.28
    confirmations: int = 3
    # Safety: never call an obscured/unknown bar "low"; witness a full bar first.
    baseline_required: bool = True
    full_threshold: float = 0.75

class BarDetector:
    """Observe full->low transitions. The bar must fill continuously from its left.

    A missing/black/occluded bar is UNKNOWN, not evidence of low health.
    Call once per *new captured frame*; no timers artificially invent frames.
    """
    def __init__(self, calibration: BarCalibration):
        if calibration.kind not in {"low_health", "low_shield", "low_ammo"}:
            raise ValueError("Unsupported bar cue")
        if (not 0 < calibration.low_threshold < calibration.full_threshold < 1
            or calibration.confirmations < 2 or not 0 <= calibration.tolerance <= 120):
            raise ValueError("Invalid calibration")
        if len(calibration.rect)!=4 or len(calibration.rgb)!=3:
            raise ValueError("Invalid calibration geometry")
        self.c=calibration
        self._low_count=0
        self._full_count=0
        self._armed=not calibration.baseline_required
        self._triggered=False
        self._first_low_at=None
        # V7: distinguish one-time audible transition from continuously
        # re-verified LOW state. Previous logic forgot low health after 600ms
        # even while the HUD still showed low health.
        self._state_low_count=0
        self._currently_low=False
        self._last_state_stamp=None

    def reset(self)->None:
        """Invalidate any prior HUD state after focus, game or resolution changes."""
        self._low_count=0
        self._full_count=0
        self._armed=not self.c.baseline_required
        self._triggered=False
        self._first_low_at=None
        self._state_low_count=0
        self._currently_low=False
        self._last_state_stamp=None

    @property
    def baseline_seen(self)->bool:
        return self._armed or self._triggered

    def current_low_observation(self)->Observation|None:
        """Fresh pixel evidence of a sustained LOW HUD bar, not another shout.

        Call ONLY immediately after process() on a newly captured frame.
        Occlusion, absent pixels and middle-level HUD never count as low.
        """
        if not self._currently_low or self._last_state_stamp is None:
            return None
        return Observation(self.c.kind,self._last_state_stamp,.92,
                           "HUD bar re-verified low on consecutive new frames",
                           "pixels")

    def process(self,frame:np.ndarray,timestamp:float|None=None)->Observation|None:
        if not isinstance(frame,np.ndarray) or frame.ndim!=3 or frame.shape[2]<3:
            self._low_count=self._full_count=0
            self._state_low_count=0
            self._currently_low=False
            self._last_state_stamp=None
            return None
        x1,y1,x2,y2=self.c.rect
        h,w=frame.shape[:2]
        if not (0<=x1<x2<=w and 0<=y1<y2<=h):
            self._low_count=self._full_count=0
            return None
        strip=frame[y1:y2,x1:x2,:3].astype(np.int16)
        target=np.asarray(self.c.rgb,dtype=np.int16)
        matched=np.all(np.abs(strip-target)<=self.c.tolerance,axis=2)
        columns=np.mean(matched,axis=0) >= .65
        # A colored minimap/text with scattered pixels is not a filled bar.
        leading=int(np.argmax(~columns)) if not np.all(columns) else len(columns)
        total=int(columns.sum())
        fraction=leading/len(columns)
        contiguous=total<=leading+max(2,int(len(columns)*.03))
        visible=contiguous and fraction>=.08
        is_full=visible and fraction>=self.c.full_threshold
        is_low=visible and fraction<=self.c.low_threshold
        self._full_count=self._full_count+1 if is_full else 0
        # Every frame requires new *visible* contiguous pixels for persistent
        # tactical evidence. Transition alerts remain once-per-full-to-low.
        self._state_low_count=self._state_low_count+1 if is_low and self._armed else 0
        self._currently_low=self._state_low_count>=self.c.confirmations
        self._last_state_stamp=(timestamp if timestamp is not None else monotonic()) if self._currently_low else None
        if self._full_count>=2:
            self._armed=True
            self._triggered=False
            self._low_count=0
            self._state_low_count=0
            self._currently_low=False
            self._last_state_stamp=None
            return None
        if not self._armed or self._triggered:
            self._low_count=0
            self._first_low_at=None
            return None
        if is_low:
            if self._low_count==0:
                self._first_low_at=timestamp if timestamp is not None else monotonic()
            self._low_count+=1
        else:
            self._low_count=0
            self._first_low_at=None
        if self._low_count<self.c.confirmations:
            return None
        self._low_count=0
        self._triggered=True
        event_at=self._first_low_at
        self._first_low_at=None
        return Observation(self.c.kind, event_at if event_at is not None else monotonic(),
                           .92, f"HUD bar visible at {fraction:.0%} after confirmed full bar", "pixels")

class ScreenSource:
    """DXGI desktop duplication when DXcam is installed; Pillow fallback.

    On DXcam, None means no *new* frame. Never reuse a cached old frame as current
    game evidence. Frames are not saved or transmitted.
    """
    def __init__(self):
        self.camera: Any=None
        self.backend="pillow"
        self.failed=False
        self.last_error=None
        try:
            import dxcam
            self.camera=dxcam.create(output_color="RGB")
            if self.camera is not None:
                self.backend="dxcam"
        except Exception:
            self.camera=None

    def grab(self,region:tuple[int,int,int,int]|None=None)->np.ndarray|None:
        # DXcam None usually means no *new* frame (paused/static desktop),
        # NOT an I/O failure. Don't count a stationary game menu as a crash.
        self.failed=False
        self.last_error=None
        if self.camera is not None:
            try:
                return self.camera.grab(region=region)
            except Exception as exc:
                self.failed=True
                self.last_error=type(exc).__name__
                return None
        from PIL import ImageGrab
        try:
            return np.asarray(ImageGrab.grab(bbox=region,all_screens=False).convert("RGB"))
        except (OSError,ValueError) as exc:
            self.failed=True
            self.last_error=type(exc).__name__
            return None

    def close(self):
        if self.camera is not None:
            self.camera.release()
            self.camera=None
