"""Voice ID: Jarvis learns each household member's voice, knows who's talking,
and ignores everyone else (TV, visitors).

Uses SpeechBrain's ECAPA speaker-embedding model (runs locally). Each person's
voiceprint (data/users/<id>/voiceprint.npy) is the average of ~20 seconds of
them talking. Every command is compared with all voiceprints; the best match
above the threshold says who it is. Confident matches slowly refine that
person's voiceprint, so it keeps up with mic/room changes.
"""
import threading

import numpy as np

from core import storage

PRINT_FILE = "voiceprint.npy"
THRESHOLD_FILE = "voiceprint.json"
THRESHOLD = 0.40        # default similarity needed for a normal sentence
MIN_THRESHOLD, MAX_THRESHOLD = 0.25, 0.45
SHORT_DROP = 0.10       # short commands ("stop", "next") carry less voice information
MIN_ENROLL_SECONDS = 15
SHORT_SECONDS = 1.5
RATE = 16000


class VoiceID:
    def __init__(self, device: str = "cuda"):
        self.device = "cuda:0" if device == "cuda" else device
        self._enc = None
        self._lock = threading.Lock()
        self.prints: dict[str, np.ndarray] = self._load_all()
        self.thresholds: dict[str, float] = self._load_thresholds()

    @property
    def enrolled(self) -> bool:
        return bool(self.prints)

    def _load_all(self) -> dict:
        users_dir = storage.DATA / "users"
        prints = {}
        if users_dir.exists():
            for f in users_dir.glob(f"*/{PRINT_FILE}"):
                prints[f.parent.name] = np.load(f)
        return prints

    def _load_thresholds(self) -> dict:
        import json

        out = {}
        for uid in self.prints:
            f = storage.DATA / "users" / uid / THRESHOLD_FILE
            if f.exists():
                out[uid] = json.loads(f.read_text())["threshold"]
        return out

    def _save(self, uid: str) -> None:
        path = storage.DATA / "users" / uid / PRINT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, self.prints[uid])

    def _encoder(self):
        if self._enc is None:
            from speechbrain.inference.speaker import EncoderClassifier
            from speechbrain.utils.fetching import LocalStrategy

            from core.config import ROOT

            self._enc = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb", savedir=str(ROOT / "models" / "ecapa"),
                run_opts={"device": self.device}, local_strategy=LocalStrategy.COPY)
        return self._enc

    def embed(self, audio: np.ndarray) -> np.ndarray:
        import torch

        samples = torch.tensor(audio.astype(np.float32) / 32768.0).unsqueeze(0)
        with self._lock, torch.no_grad():
            e = self._encoder().encode_batch(samples).squeeze().float().cpu().numpy()
        return e / np.linalg.norm(e)

    def enroll(self, speech: np.ndarray, uid: str = "owner") -> str:
        """Build a person's voiceprint from recorded speech (silences already removed),
        cut into 3-second pieces."""
        import json

        piece = 3 * RATE
        chunks = [speech[i:i + piece] for i in range(0, len(speech) - piece // 2, piece)]
        if len(speech) < MIN_ENROLL_SECONDS * RATE:
            return (f"I only heard about {len(speech) // RATE} seconds of speech; I need at least "
                    f"{MIN_ENROLL_SECONDS}. Let's try again, and keep talking until the second beep.")
        embs = [self.embed(c) for c in chunks]
        vec = np.mean(embs, axis=0)
        self.prints[uid] = vec / np.linalg.norm(vec)
        # This person's own bar: how similar their pieces were to their voiceprint, minus
        # a margin. A consistent voice/mic gets a stricter bar, a variable one a looser bar.
        sims = np.array([float(e @ self.prints[uid]) for e in embs])
        self.thresholds[uid] = float(np.clip(sims.mean() - 2.5 * sims.std() - 0.15, MIN_THRESHOLD, MAX_THRESHOLD))
        self._save(uid)
        (storage.DATA / "users" / uid / THRESHOLD_FILE).write_text(json.dumps({"threshold": self.thresholds[uid]}))
        return f"Voiceprint saved from {len(speech) // RATE} seconds of speech."

    def identify(self, audio: np.ndarray) -> tuple[str | None, float]:
        """Who is speaking? Returns (person id, or None if nobody matches; best similarity)."""
        if not self.prints:
            return None, 0.0
        e = self.embed(audio)
        scores = {uid: float(vec @ e) for uid, vec in self.prints.items()}
        uid = max(scores, key=scores.get)
        score = scores[uid]
        short = len(audio) / RATE < SHORT_SECONDS
        bar = self.thresholds.get(uid, THRESHOLD) - (SHORT_DROP if short else 0)
        if score < bar:
            return None, score
        if score > 0.6 and not short:  # confident: let this voiceprint adapt a little
            vec = 0.95 * self.prints[uid] + 0.05 * e
            self.prints[uid] = vec / np.linalg.norm(vec)
            self._save(uid)
        return uid, score

    def check(self, audio: np.ndarray) -> tuple[bool, float]:
        """Is this anyone from the household? (accepted, similarity)"""
        if not self.prints:
            return True, 1.0
        uid, score = self.identify(audio)
        return uid is not None, score

    def forget(self, uid: str | None = None) -> None:
        for u in ([uid] if uid else list(self.prints)):
            self.prints.pop(u, None)
            (storage.DATA / "users" / u / PRINT_FILE).unlink(missing_ok=True)
