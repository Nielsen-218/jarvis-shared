"""Nonblocking game vision: at most one pending frame and one completed cue.

Screen capture/real-time sound MUST NOT wait for ONNX inference. Inference
happens on a CPU-only worker thread; stale frames/results are never treated
as current game facts. Safe for sudden game exit; no raw frame persistence.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from threading import Condition, Thread
from time import monotonic
from math import isfinite
from typing import Any, Callable
import numpy as np

@dataclass
class WorkerStats:
    offered: int = 0
    accepted: int = 0
    dropped_busy: int = 0
    rejected_age: int = 0
    returned: int = 0
    empty: int = 0
    faults: int = 0
    slow_inferences: int = 0
    disabled_due_to_load: int = 0

class VisionWorker:
    """Dedicated inference loop with backpressure and bounded result slot.

    One frame copied at producer boundary, no loops over unlimited queues.
    Enforces both sampling interval and maximum age on ingest and output.
    """
    def __init__(self,detector:Any,clock:Callable[[],float]=monotonic,
                 interval_s:float=.25,max_age_s:float=.6,
                 max_inference_s:float=.2,slow_limit:int=4):
        if (not .1<=interval_s<=5 or not .2<=max_age_s<=.65 or
            not .01<=max_inference_s<=2 or not 2<=slow_limit<=20):
            raise ValueError("Invalid inference pacing")
        self.detector=detector
        self.clock=clock
        self.interval_s=interval_s
        self.max_age_s=max_age_s
        self.max_inference_s=max_inference_s
        self.slow_limit=slow_limit
        self._slow_streak=0
        self._disabled_reason=None
        self.stats=WorkerStats()
        self._cv=Condition()
        self._pending:tuple[np.ndarray,float]|None=None
        self._result:Any=None
        self._result_ready=False
        self._last_offered=-float("inf")
        self._busy=False
        self._epoch=0
        self._running=False
        self._thread:Thread|None=None

    def start(self)->None:
        with self._cv:
            if self._running:return
            if self._thread and self._thread.is_alive():
                raise RuntimeError("Prior inference thread is still active")
            self._pending=None
            self._result=None
            self._result_ready=False
            self._last_offered=-float("inf")
            self._busy=False
            self._slow_streak=0
            self._disabled_reason=None
            self._epoch+=1
            self._running=True
            self._thread=Thread(target=self._work,name="nolagin-cpu-vision",daemon=True)
            self._thread.start()

    @property
    def disabled(self)->bool:
        with self._cv:return self._disabled_reason is not None

    def offer(self,frame:np.ndarray,captured_at:float)->bool:
        now=self.clock()
        with self._cv:
            self.stats.offered+=1
            if (not self._running or self._disabled_reason is not None or
                not isinstance(frame,np.ndarray) or
                not isinstance(captured_at,(float,int)) or not isfinite(captured_at) or
                not 0<=now-captured_at<=self.max_age_s):
                self.stats.rejected_age+=1
                return False
            if now-self._last_offered<self.interval_s:
                return False
            # Drop rather than queue. Avoid copying a large screen while busy.
            if self._pending is not None or self._busy:
                self.stats.dropped_busy+=1
                return False
            self._last_offered=now
            self._pending=(frame.copy(),captured_at,self._epoch)
            self.stats.accepted+=1
            self._cv.notify()
            return True

    def poll(self)->Any:
        with self._cv:
            if not self._result_ready:
                return None
            item=self._result
            self._result=None
            self._result_ready=False
        if item is None:
            return None
        stamp=getattr(item,"captured_at",None)
        if not isinstance(stamp,(int,float)) or self.clock()-stamp>self.max_age_s:
            self.stats.rejected_age+=1
            return None
        self.stats.returned+=1
        return item

    def _work(self)->None:
        while True:
            with self._cv:
                self._cv.wait_for(lambda:not self._running or self._pending is not None)
                if not self._running:return
                frame,when,epoch=self._pending
                self._pending=None
                self._busy=True
            if self.clock()-when>self.max_age_s:
                self.stats.rejected_age+=1
                with self._cv:self._busy=False
                continue
            inference_started=self.clock()
            try:
                cue=self.detector.infer(frame,when,clock=self.clock)
                if cue is None:self.stats.empty+=1
            except Exception:
                self.stats.faults+=1
                cue=None
            elapsed=self.clock()-inference_started
            with self._cv:
                self._busy=False
                if not self._running:return
                if epoch!=self._epoch:continue
                if elapsed>self.max_inference_s:
                    self._slow_streak+=1
                    self.stats.slow_inferences+=1
                else:self._slow_streak=0
                if self._slow_streak>=self.slow_limit:
                    # Stop offering and force the live loop back to its small
                    # HUD-only ROI capture rather than burning CPU indefinitely.
                    self._disabled_reason="sustained_slow_cpu_inference"
                    self.stats.disabled_due_to_load+=1
                    self._pending=None
                    self._result=None
                    self._result_ready=False
                    self._running=False
                    return
                self._result=cue
                self._result_ready=True

    def invalidate(self)->None:
        """Discard in-flight results from the previous window/fight context."""
        with self._cv:
            self._epoch+=1
            self._pending=None
            self._result=None
            self._result_ready=False
            self._slow_streak=0

    def stop(self,timeout:float=.3)->bool:
        with self._cv:
            self._running=False
            self._epoch+=1
            self._pending=None
            self._result=None
            self._result_ready=False
            self._cv.notify_all()
            thread=self._thread
        if thread:
            thread.join(timeout)
        return not thread or not thread.is_alive()

    def summary(self)->dict:
        out=vars(self.stats).copy()
        out["worker_active"]=bool(self._thread and self._thread.is_alive())
        out["one_frame_slot"]=True
        out["vision_disabled_reason"]=self._disabled_reason
        out["max_inference_s"]=self.max_inference_s
        return out
