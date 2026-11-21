"""Fast speech-to-text with NVIDIA Parakeet TDT 0.6B v3 (via sherpa-onnx, runs locally on the CPU).

On the laptop this replaces Whisper 'small' as the first-pass transcriber: it is both faster and more accurate
on a CPU (about 6.3% word-error vs Whisper's 7.4%, ~1.6-4x quicker), which is what makes phone voice notes feel
instant. It covers English and the major European languages (incl. French) but not Haitian Creole, so the
Transcriber falls back to Whisper for Kreyol. Audio never leaves the machine.

Model: sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 (download it into JARVIS_PARAKEET_DIR / models/).
"""
import glob
import os
from pathlib import Path

import numpy as np

from core.config import ROOT

DEFAULT_DIR = ROOT / "models" / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"


def model_dir() -> Path:
    return Path(os.getenv("JARVIS_PARAKEET_DIR") or DEFAULT_DIR)


def available() -> bool:
    d = model_dir()
    return d.is_dir() and bool(glob.glob(str(d / "encoder*.onnx"))) and (d / "tokens.txt").exists()


class Parakeet:
    def __init__(self, num_threads: int = 0):
        import sherpa_onnx

        d = model_dir()
        enc = sorted(glob.glob(str(d / "encoder*.onnx")))[0]
        dec = sorted(glob.glob(str(d / "decoder*.onnx")))[0]
        join = sorted(glob.glob(str(d / "joiner*.onnx")))[0]
        self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=enc, decoder=dec, joiner=join, tokens=str(d / "tokens.txt"),
            num_threads=num_threads or max(2, (os.cpu_count() or 4) - 2),
            sample_rate=16000, feature_dim=80, decoding_method="greedy_search",
            model_type="nemo_transducer")
        print(f"[stt] Parakeet TDT ready ({d.name})")

    def transcribe(self, samples: np.ndarray) -> str:
        """samples: float32 mono at 16 kHz in [-1, 1]."""
        stream = self.rec.create_stream()
        stream.accept_waveform(16000, samples)
        self.rec.decode_stream(stream)
        return (stream.result.text or "").strip()
