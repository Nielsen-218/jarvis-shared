"""Does giving Whisper the owner's app names (faster-whisper `hotwords`) help, and does it hurt anything?

Kokoro speaks the clips (several voices), with and without background noise, plus noise-only clips. Each clip is
transcribed twice by the same large-v3-turbo model: plain, and with hotwords. Measured:
  - names:  the app name comes out right ("Close ChatGPT" -> contains "chatgpt", letters only)
  - plain:  ordinary sentences stay the same (word similarity to the script, 0-100)
  - noise:  noise-only clips produce no words (any hotword showing up there is a hallucination)
Synthetic voices are cleaner than the owner's microphone: this is a regression check, not a measure of live accuracy.

    python scripts/stt_hotwords_bench.py [--quick]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VOICES = ["kokoro:am_michael", "kokoro:af_heart", "kokoro:bm_lewis", "kokoro:am_adam", "kokoro:af_bella"]
COMMANDS = [  # (spoken, the name that must come out)
    ("Close ChatGPT.", "chatgpt"), ("Minimize Claude.", "claude"), ("Open Claude.", "claude"),
    ("Close Discord.", "discord"), ("Open Blender.", "blender"), ("Open Unreal Engine.", "unreal"),
    ("Launch Apex Legends.", "apex"), ("Close Microsoft Edge.", "edge"), ("Open Steam.", "steam"),
    ("Close the calculator.", "calculator"), ("Open Shadow World.", "shadowworld"), ("Close GitHub.", "github"),
    ("Jarvis, troubleshoot yourself.", "jarvis"), ("Hey Jarvis, what's on my screen?", "jarvis"),
    ("Minimize ChatGPT.", "chatgpt"), ("Open Spotify.", "spotify"), ("Close YouTube.", "youtube"),
    ("Open Command Center.", "commandcenter"), ("Open World Intelligence.", "worldintelligence"),  # 2026-11-16: his Hub names
]
PLAIN = [
    "What local AI are you using?", "I need you to build this character from my right monitor.",
    "I thought there's still a new sound.", "Yeah, we should be good all around guys.",
    "Do you need an API key to be able to do that?", "Shut down the PC. I'm all done. Good night.",
    "What's the weather like in Springfield tomorrow?", "Remind me to buy groceries at five.",
    "Tell me a joke about robots.", "How long would it take to drive to Richmond?",
]


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").casefold())


def _similar(a: str, b: str) -> float:
    from rapidfuzz import fuzz
    return float(fuzz.token_sort_ratio(re.sub(r"[^\w\s]", "", a.casefold()), re.sub(r"[^\w\s]", "", b.casefold())))


def _speak(text: str, voice: str) -> np.ndarray:
    from scipy.signal import resample_poly
    from audio.tts import kokoro_say
    samples = kokoro_say(text, voice, "+0%").astype(np.float32) / 32768.0
    return resample_poly(samples, 2, 3).astype(np.float32)          # 24 kHz -> 16 kHz


def _noisy(samples: np.ndarray, snr_db: float, rng) -> np.ndarray:
    noise = rng.standard_normal(len(samples)).astype(np.float32)
    noise = np.convolve(noise, np.ones(8) / 8, mode="same")         # a little low-passed: closer to room hiss than pure white
    power = float(np.mean(samples ** 2)) or 1e-8
    noise *= np.sqrt(power / (10 ** (snr_db / 10)) / (float(np.mean(noise ** 2)) or 1e-8))
    return np.clip(samples + noise, -1, 1).astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="2 voices, no noisy copies")
    ap.add_argument("--out", default=str(ROOT / "runs" / "stt-hotwords-bench.json"))
    args = ap.parse_args()
    from faster_whisper import WhisperModel
    from audio import stt
    stt._add_cuda_dlls()
    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    words = stt.hotwords()
    print(f"hotwords ({len(words)} chars): {words}")
    rng = np.random.default_rng(7)
    voices = VOICES[:2] if args.quick else VOICES
    conditions = [None] if args.quick else [None, 15.0, 5.0]

    def hear(samples, hot):
        segments, _ = model.transcribe(samples, language="en", beam_size=5, vad_filter=True,
                                       **({"hotwords": hot} if hot else {}))
        return " ".join(s.text.strip() for s in segments).strip()

    rows = []
    started = time.monotonic()
    for voice in voices:
        clips = [("name", t, want, _speak(t, voice)) for t, want in COMMANDS] + [("plain", t, t, _speak(t, voice)) for t in PLAIN]
        for kind, text, want, clean in clips:
            for snr in conditions:
                audio = clean if snr is None else _noisy(clean, snr, rng)
                for mode, hot in (("plain", None), ("hotwords", words)):
                    heard = hear(audio, hot)
                    ok = (want in _letters(heard)) if kind == "name" else None
                    rows.append({"kind": kind, "voice": voice, "snr": snr, "mode": mode, "said": text, "heard": heard,
                                 "ok": ok, "similar": _similar(text, heard) if kind == "plain" else None})
    for i in range(10 if not args.quick else 4):                        # noise only, at a speech-like level
        length = int(16000 * (1.5 + i * 0.3))
        audio = _noisy(np.zeros(length, np.float32) + 1e-3 * rng.standard_normal(length).astype(np.float32), 0.0, rng) * 0.05
        for mode, hot in (("plain", None), ("hotwords", words)):
            heard = hear(audio, hot)
            rows.append({"kind": "noise", "voice": "", "snr": None, "mode": mode, "said": "", "heard": heard, "ok": heard == "",
                         "similar": None})

    def summary(mode):
        names = [r for r in rows if r["mode"] == mode and r["kind"] == "name"]
        plain = [r for r in rows if r["mode"] == mode and r["kind"] == "plain"]
        noise = [r for r in rows if r["mode"] == mode and r["kind"] == "noise"]
        by_snr = {}
        for r in names:
            key = "clean" if r["snr"] is None else f"snr{int(r['snr'])}"
            by_snr.setdefault(key, []).append(r["ok"])
        return {"names_right": f"{sum(r['ok'] for r in names)}/{len(names)}",
                "names_by_noise": {k: f"{sum(v)}/{len(v)}" for k, v in by_snr.items()},
                "plain_similarity_avg": round(sum(r["similar"] for r in plain) / max(1, len(plain)), 1),
                "plain_worse_than_90": sum(1 for r in plain if r["similar"] < 90),
                "noise_silent": f"{sum(r['ok'] for r in noise)}/{len(noise)}",
                "noise_heard": [r["heard"] for r in noise if r["heard"]]}

    report = {"hotwords": words, "seconds": round(time.monotonic() - started, 1), "plain": summary("plain"),
              "hotwords_mode": summary("hotwords"),
              "changed": [{"said": a["said"], "voice": a["voice"], "snr": a["snr"], "plain": a["heard"], "hotwords": b["heard"]}
                          for a, b in zip(rows[0::2], rows[1::2]) if a["heard"] != b["heard"]]}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"report": report, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("seconds", "plain", "hotwords_mode")}, indent=1))
    print(f"{len(report['changed'])} clips changed; details in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
