"""Microphone input: wake word, recording what you say, and noticing when you
talk over Jarvis.

The mic stays open in the background and feeds a queue, so there is no gap
between hearing "Hey Jarvis" and recording what you say next. Speech is
detected with the Silero voice-activity model (bundled with openWakeWord).
"""
import queue
import threading
import time
from collections import deque
from typing import Callable

import numpy as np

from audio.endturn import THRESHOLD

RATE = 16000
FRAME = 1280  # 80 ms, the frame size openWakeWord expects
FRAME_SECS = FRAME / RATE
PRE_ROLL = 1.0            # audio kept from before the first speech frame, so a soft first word isn't clipped
MIN_PRE_ROLL_FRAMES = 3   # ~0.25 s: the old pre-roll, always kept
# Two-delay end of turn (audio/endturn): at the first pause the model is asked whether he has finished; if the words so far
# sound unfinished it is asked again at SMART_RECHECK, and SMART_MAX_SILENCE of silence ends the turn whatever it says.
SMART_RECHECK = 2.0
SMART_MAX_SILENCE = 3.0
# At the first check the model hears only this much of the pause after his last speech frame: it was trained on turns cut
# ~0.2 s after speech (Pipecat runs it on a 0.2 s VAD stop) and drifts to "finished" on longer silence (measured with
# scripts/endpoint_eval.py: "I need you to type in" 0.09 with 0.1 s of silence, 0.72 with 0.64 s). The re-check at
# SMART_RECHECK hears the whole pause: two seconds of silence is itself a sign he has finished.
SMART_TAIL = 0.16


class Listener:
    def __init__(self, wake_threshold: float = 0.5, use_wake_word: bool = True,
                 barge_in_ratio: float = 2.0, speech_threshold: float = 0.5):
        import openwakeword
        import sounddevice as sd
        from openwakeword.vad import VAD

        self.wake_threshold = wake_threshold
        self.speech_threshold = speech_threshold
        self.barge_in_ratio = barge_in_ratio

        openwakeword.utils.download_models(["hey_jarvis"])
        self.vad = VAD()
        self.wake_model = None
        if use_wake_word:
            from openwakeword.model import Model

            self.wake_model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

        self.muted = False  # tray "mute microphone": nothing is heard while set
        # End-of-turn model (audio/endturn.SmartTurn, set once loaded): audio -> probability he has finished, or None.
        self.end_of_turn: Callable[[np.ndarray], float | None] | None = None
        self.last_endpoint: dict = {}  # how the last record_command ended (for the turn's timing and the voice loop)
        self.level = 0.0  # current mic loudness 0..1, for the HUD
        self.wake_event = threading.Event()  # set by the summon hotkey
        self.suspend_interruptions = False  # e.g. while recording a voiceprint
        self._tap: list[np.ndarray] | None = None  # dictation recording
        self.aware_sink: Callable[[np.ndarray], None] | None = None  # Daily Awareness room listening (opt-in)
        self._q: queue.Queue = queue.Queue()
        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                      blocksize=FRAME, callback=self._on_audio)
        self._stream.start()

    # -- public -----------------------------------------------------------
    def flush(self) -> None:
        """Drop buffered audio."""
        while not self._q.empty():
            self._q.get_nowait()
        self.vad.reset_states()

    def discard(self, seconds: float) -> None:
        """Skip the next few frames (the tail of Jarvis's voice still echoing)."""
        for _ in range(int(seconds / FRAME_SECS)):
            self._next()

    def wait_for_wake_word(self) -> None:
        self.flush()
        if self.wake_model is None:
            input("\n[Press Enter and speak] ")
            self.flush()
            return
        print("\n[mic] sleeping - say 'Hey Jarvis'...")
        self.wake_model.reset()
        while True:
            try:
                frame = self._q.get(timeout=0.2)
            except queue.Empty:
                frame = None
            if self.wake_event.is_set():  # summoned with the hotkey
                self.wake_event.clear()
                self.flush()
                return
            if frame is not None and self._wake(frame):
                self.wake_model.reset()
                return

    def record_command(self, start_timeout: float = 6.0, max_seconds: float = 20.0,
                       silence_after: float = 0.6,
                       initial: list[np.ndarray] | None = None, smart: bool = False,
                       pre_roll: float = PRE_ROLL, not_before: float = 0.0,
                       on_speech: Callable[[], None] | None = None) -> np.ndarray | None:
        """Record from when you start speaking until you have finished.

        Returns None if you don't start speaking within start_timeout seconds.
        `initial` continues a recording that has already started (an interruption).
        smart: with the end-of-turn model loaded, a pause of `silence_after` ends the command only when what he said
        sounds finished; otherwise it waits (asking again at SMART_RECHECK) up to SMART_MAX_SILENCE. The model is only
        asked during silence, never while he is talking. Without the model (or smart=False) the pause alone ends it.
        not_before: monotonic time before which the mic was hearing Jarvis himself; the pre-roll never reaches back
        past it (his last words must not become the start of the owner's command).
        on_speech: called once, at his first speech frame (not with `initial`: that speech began before this call).
        """
        frames: list[np.ndarray] = list(initial or [])
        started = bool(initial)
        silent = 0                         # silent frames since he last spoke (counted, so 0.6 s is exactly 8 frames)
        silent_for = 0.0
        waited = 0.0
        keep = max(MIN_PRE_ROLL_FRAMES, int(round(pre_roll / FRAME_SECS)))
        model = self.end_of_turn if smart else None
        checks: list[float] = []
        asked: set[float] = set()          # the checks already made in this pause
        self.last_endpoint = {"mode": "smart" if model else "silence", "silence_s": 0.0, "checks": checks,
                              "waited": False}

        def done(how: str) -> np.ndarray:
            self.last_endpoint.update(how=how, silence_s=round(silent_for, 2), seconds=round(len(frames) * FRAME_SECS, 2))
            return np.concatenate(frames)

        while True:
            frame = self._next()
            speaking = self._is_speech(frame)
            if not started:
                frames = (frames + [frame])[-keep:]
                if speaking:
                    started = True
                    if on_speech is not None:
                        on_speech()
                    if not_before:      # no frame from before he stopped talking (the queue is near real time)
                        fresh = int((time.monotonic() - not_before) / FRAME_SECS) + 1
                        frames = frames[-max(MIN_PRE_ROLL_FRAMES, min(len(frames), fresh)):]
                else:
                    waited += FRAME_SECS
                    if waited > start_timeout:
                        return None
                continue
            frames.append(frame)
            if speaking:
                silent = 0
                asked.clear()
            else:
                silent += 1
            silent_for = round(silent * FRAME_SECS, 6)
            if len(frames) * FRAME_SECS >= max_seconds:
                return done("max_seconds")
            if speaking or silent_for < silence_after:
                continue
            if model is None:
                return done("pause")
            if silent_for >= SMART_MAX_SILENCE:
                return done("max_silence")
            for at in (silence_after, SMART_RECHECK):
                if silent_for >= at and at not in asked:
                    asked.add(at)
                    heard = frames
                    if at == silence_after:     # his words plus a short tail of the pause (see SMART_TAIL)
                        tail = max(1, int(round(SMART_TAIL / FRAME_SECS)))
                        heard = frames[:len(frames) - silent + min(tail, silent)]
                    p = model(np.concatenate(heard))
                    if p is None:           # the model could not answer: the pause rule, as before
                        return done("pause")
                    checks.append(round(p, 3))
                    if p >= THRESHOLD:
                        return done("complete")
                    self.last_endpoint["waited"] = True   # sounded unfinished: he gets more time

    def watch_for_interruption(self, busy: Callable[[], bool],
                               output_level: Callable[[], float]) -> list[np.ndarray] | None:
        """While Jarvis is replying, listen for the user talking over him.

        The mic also hears Jarvis through the speakers. Since we know how loud
        Jarvis is at each moment (output_level), we learn how much of that the
        mic picks up and only count speech well above the expected echo - or
        "Hey Jarvis". Returns the frames that started the interruption, or None
        once Jarvis has finished.
        """
        if self.wake_model is not None:
            self.wake_model.reset()
        coupling: deque[float] = deque(maxlen=60)  # mic level / speaker level, echo only
        recent: deque[np.ndarray] = deque(maxlen=6)
        votes: deque[bool] = deque(maxlen=5)
        while busy():
            try:
                frame = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            recent.append(frame)
            if self._wake(frame):
                return list(recent)
            level = _rms(frame)
            ref = output_level()
            if len(coupling) >= 8:
                expected = float(np.percentile(coupling, 90)) * ref
            else:
                expected = float("inf") if ref > 200 else 0.0  # still learning the echo
            louder = level > max(expected * self.barge_in_ratio, 200.0)
            vote = louder and self._is_speech(frame) and not self.suspend_interruptions
            votes.append(vote)
            if sum(votes) >= 3:  # 3 of the last 5 frames (~0.4 s) are clearly the user
                return list(recent)
            if not vote and ref > 200:
                coupling.append(level / ref)
        return None

    # -- helpers ----------------------------------------------------------
    def record_speech(self, seconds: float) -> np.ndarray:
        """Record for a fixed time and return only the frames that contain speech."""
        self.flush()
        speech = []
        for _ in range(int(seconds / FRAME_SECS)):
            frame = self._next()
            if self._is_speech(frame):
                speech.append(frame)
        return np.concatenate(speech) if speech else np.zeros(0, np.int16)

    def start_tap(self) -> None:
        """Start capturing audio for dictation (in parallel with everything else)."""
        self._tap = []

    def stop_tap(self) -> np.ndarray | None:
        frames, self._tap = self._tap, None
        return np.concatenate(frames) if frames else None

    def _on_audio(self, data, frames, time_info, status) -> None:
        frame = data[:, 0].copy()
        self.level = min(1.0, _rms(frame) / 3000)
        if self._tap is not None:
            self._tap.append(frame)
        if not self.muted:
            self._q.put(frame)
            if self.aware_sink is not None:  # it only queues; muting the mic mutes this too
                self.aware_sink(frame)

    def _next(self) -> np.ndarray:
        return self._q.get()

    def _is_speech(self, frame: np.ndarray) -> bool:
        return self.vad.predict(frame, frame_size=640) >= self.speech_threshold

    def _wake(self, frame: np.ndarray) -> bool:
        if self.wake_model is None:
            return False
        return max(self.wake_model.predict(frame).values()) >= self.wake_threshold


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
