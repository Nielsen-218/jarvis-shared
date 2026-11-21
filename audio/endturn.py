"""Has he finished, or only paused mid-thought? End-of-turn detection with Smart Turn v3.2 on the CPU.

A fixed 0.6 s of silence ended every command, so a long, hesitant sentence was split into fragment turns (live
2026-11-09: "Scan my PC and look for." / "Look for." / "Things that are wasting my memory..." became three turns, each
answered on its own). The listener (audio/listener.record_command) now asks this model at the first pause and keeps
listening when the words so far sound unfinished.

The model is pipecat-ai/smart-turn-v3 (BSD 2-Clause, Daily): an 8 MB int8 ONNX classifier over the last 8 s of the
utterance, ~40 ms on one CPU core. It is a file on disk (models/smart_turn/, not in git), pinned by SHA-256. Every
failure fails OPEN: no file, a different file, no onnxruntime or an error while running means complete() returns None
and the listener keeps today's 0.6 s silence rule.

OFF unless JARVIS_SMART_TURN=on: the design's acceptance (short commands commit within 0.1 s of the pause rule, then 20
short commands live) is not met yet. scripts/endpoint_eval.py with bm_george had 2 of 6 short commands wait 1.4 s and
2.4 s longer ("Open Blender." scored as unfinished at 0.6 s and 2.0 s, committed at the 3.0 s cap). Turn it on for the
live check; make it the default only once that check passes.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import numpy as np

RATE = 16000
WINDOW_S = 8                                   # the model reads the last 8 s, left-padded with zeros
THRESHOLD = 0.5                                # probability at or above this: he has finished
MODEL = Path(__file__).resolve().parent.parent / "models" / "smart_turn" / "smart-turn-v3.2-cpu.onnx"
SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"   # smart-turn-v3.2-cpu.onnx as shipped by Pipecat
TAIL_PAD_S = 0.5                               # silence added after a clip before Whisper reads it


def enabled() -> bool:
    return os.getenv("JARVIS_SMART_TURN", "off").strip().casefold() in ("on", "1", "yes", "true")


def window(audio: np.ndarray) -> np.ndarray:
    """int16 (or float in [-1, 1]) mono 16 kHz -> float32, exactly the last WINDOW_S seconds, zeros on the left."""
    x = np.asarray(audio)
    x = x.astype(np.float32) / 32768.0 if x.dtype == np.int16 else x.astype(np.float32)
    n = WINDOW_S * RATE
    return x[-n:] if x.size >= n else np.pad(x, (n - x.size, 0))


def features(audio: np.ndarray) -> np.ndarray:
    """Whisper log-mel features (80 x 800) of the model's window, as the model was trained on."""
    from audio._whisper_features import compute_whisper_log_mel_features
    return compute_whisper_log_mel_features(window(audio), do_normalize=True)


def pad_tail(audio: np.ndarray, seconds: float = TAIL_PAD_S) -> np.ndarray:
    """The clip plus a little silence, so Whisper does not clip the last word (Pipecat pads 0.5 s the same way)."""
    if audio is None or not seconds:
        return audio
    return np.concatenate([audio, np.zeros(int(seconds * RATE), dtype=audio.dtype)])


class SmartTurn:
    """complete(audio) -> probability that he has finished, or None when the model cannot answer (fail open)."""

    def __init__(self, session):
        self._session = session
        self._input = session.get_inputs()[0].name

    @classmethod
    def load(cls, path: Path | str = MODEL, sha256: str = SHA256) -> "SmartTurn | None":
        if not enabled():
            return None
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError:
            print(f"[endturn] no end-of-turn model at {path}; pauses end a command after 0.6 s as before")
            return None
        if sha256 and hashlib.sha256(data).hexdigest() != sha256:
            print("[endturn] the end-of-turn model is not the pinned file; not loaded (0.6 s pauses as before)")
            return None
        try:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.intra_op_num_threads = 1       # one core: a brief blip, never a game's frame time
            options.inter_op_num_threads = 1
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session = ort.InferenceSession(data, sess_options=options, providers=["CPUExecutionProvider"])
            return cls(session)
        except Exception as error:  # noqa: BLE001  (fail open: today's endpointing)
            print(f"[endturn] end-of-turn model unavailable ({type(error).__name__}: {str(error)[:80]})")
            return None

    def complete(self, audio: np.ndarray) -> float | None:
        try:
            out = self._session.run(None, {self._input: features(audio)[None]})
            p = float(np.asarray(out[0]).reshape(-1)[0])
        except Exception as error:  # noqa: BLE001
            print(f"[endturn] check failed ({type(error).__name__}); pause rule instead")
            return None
        return p if 0.0 <= p <= 1.0 else None

    __call__ = complete


def load_in_background(on_ready) -> threading.Thread:
    """Load without delaying start-up; on_ready(model or None) is called when done."""
    def run():
        on_ready(SmartTurn.load())
    thread = threading.Thread(target=run, daemon=True, name="endturn-load")
    thread.start()
    return thread
