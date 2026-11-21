"""CPU-friendly optional SAPI5 speech queue for local gaming coach.

Speech is opt-in only. A dedicated worker owns the pyttsx3 engine, which may
block. Old calls are discarded *before* being spoken, and no GPU/paid TTS used.
The engine started-utterance callback is NOT a measurement of physical audio.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass,field
from threading import Condition,Thread
from time import monotonic
from typing import Callable
from core.gamecoach import Callout, PRIORITY

@dataclass
class VoiceStats:
    queued: int=0
    engine_started: int=0
    dropped_stale: int=0
    overwritten: int=0
    rejected_lower_priority: int=0
    engine_errors: int=0
    engine_start_latency_ms: deque[float]=field(default_factory=lambda:deque(maxlen=1024))

    def summary(self)->dict:
        xs=sorted(self.engine_start_latency_ms)
        return {"queued":self.queued,"engine_started":self.engine_started,
                "dropped_stale":self.dropped_stale,"overwritten":self.overwritten,
                "rejected_lower_priority":self.rejected_lower_priority,
                "engine_errors":self.engine_errors,
                "engine_start_p95_ms":round(xs[int((len(xs)-1)*.95)],2) if xs else None,
                "measurement_note":"TTS started-utterance callback, NOT DAC/audio onset"}

class VoiceSink:
    """Latest-priority one-slot voice output; engine never touches producer thread."""
    def __init__(self,max_age_ms:float=650.,rate:int=230,
                 engine_factory:Callable|None=None,clock:Callable[[],float]=monotonic):
        self.max_age_ms=max_age_ms
        self.rate=rate
        self.engine_factory=engine_factory
        self.clock=clock
        self.stats=VoiceStats()
        self._cv=Condition()
        self._pending:Callout|None=None
        self._running=False
        self._worker:Thread|None=None

    def start(self)->None:
        with self._cv:
            if self._running:return
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("Old voice worker has not exited")
            self._running=True
            self._worker=Thread(target=self._run,daemon=True,name="jarvis-gamecoach-sapi")
            self._worker.start()

    def __call__(self,call:Callout)->bool:
        with self._cv:
            if not self._running:return False
            if (self.clock()-call.captured_at)*1000>self.max_age_ms:
                self.stats.dropped_stale+=1
                return False
            if self._pending is not None:
                if (PRIORITY.get(call.kind,0)<PRIORITY.get(self._pending.kind,0)
                    and (self.clock()-self._pending.captured_at)*1000<=self.max_age_ms):
                    self.stats.rejected_lower_priority+=1
                    return False
                self.stats.overwritten+=1
            self._pending=call
            self.stats.queued+=1
            self._cv.notify()
            return True

    def _run(self)->None:
        try:
            if self.engine_factory is None:
                import pyttsx3
                engine=pyttsx3.init(driverName="sapi5")
            else:
                engine=self.engine_factory()
            engine.setProperty("rate",self.rate)
        except Exception:
            self.stats.engine_errors+=1
            with self._cv:
                self._running=False
                self._pending=None
                self._cv.notify_all()
            return
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._pending is not None or not self._running)
                if not self._running:return
                call,self._pending=self._pending,None
            if call is None:continue
            if (self.clock()-call.captured_at)*1000>self.max_age_ms:
                self.stats.dropped_stale+=1
                continue
            try:
                # pyttsx3 invokes callbacks in this engine's event loop.
                state={"started":False}
                def on_start(name):
                    if state["started"]:return
                    state["started"]=True
                    self.stats.engine_started+=1
                    self.stats.engine_start_latency_ms.append(
                        (self.clock()-call.captured_at)*1000)
                token=engine.connect("started-utterance",on_start)
                try:
                    engine.say(call.text)
                    engine.runAndWait()
                finally:
                    engine.disconnect(token)
            except Exception:
                self.stats.engine_errors+=1

    def stop(self,timeout:float=.4)->None:
        # Never stop() the SAPI engine from outside its own thread.
        with self._cv:
            self._running=False
            self._pending=None
            self._cv.notify_all()
        if self._worker:self._worker.join(timeout)
