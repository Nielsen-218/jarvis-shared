"""Measure local Kokoro-to-audio-driver stages without recording or sending audio.

The optional driver probe writes a short silent buffer.  It measures submission
to PortAudio, not physical speaker onset; a microphone loopback is required to
make an audibility claim.  Run with the project environment, for example:

    $env:HF_HUB_OFFLINE=1; $env:TRANSFORMERS_OFFLINE=1
    python scripts/voice_playback_profile.py --runs 30 --driver --report data/bench/voice-playback.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(np.ceil(percentile / 100 * len(ordered))) - 1))
    return round(ordered[index], 2)


def _summary(rows: list[dict], key: str) -> dict:
    values = [row[key] for row in rows if row.get(key) is not None]
    return {"n": len(values), "p50_ms": _percentile(values, 50), "p95_ms": _percentile(values, 95)}


def _synthesize_once(text: str, voice: str, rate: str) -> dict:
    # This deliberately mirrors kokoro_say while retaining stage boundaries.
    from audio import tts

    name = tts.kokoro_voice(voice).split(":", 1)[1]
    if name[0] in "ab":
        text = tts._SAY_AS_RE.sub(lambda match: f"[{match.group(1)}](/{tts._SAY_AS[match.group(1)]}/)", text)
    started = time.monotonic()
    pipe = tts._kokoro_pipe(name[0])
    with tts._kokoro_lock:
        chunks = iter(pipe(text, voice=name, speed=tts._speed(rate)))
        try:
            _graphemes, _phonemes, first = next(chunks)
            first_chunk_at = time.monotonic()
            parts = [first.numpy() if hasattr(first, "numpy") else np.asarray(first)]
        except StopIteration:
            first_chunk_at = None
            parts = []
        parts.extend(audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio)
                     for _graphemes, _phonemes, audio in chunks)
    generated_at = time.monotonic()
    if parts:
        pcm = tts._trim((np.clip(np.concatenate(parts), -1, 1) * 32767).astype(np.int16))
    else:
        pcm = np.zeros(0, dtype=np.int16)
    finished = time.monotonic()
    return {
        "first_wave_chunk_ms": round((first_chunk_at - started) * 1000, 2) if first_chunk_at else None,
        "wave_generation_ms": round((generated_at - started) * 1000, 2),
        "wave_prepare_ms": round((finished - generated_at) * 1000, 2),
        "synthesis_total_ms": round((finished - started) * 1000, 2),
        "samples": int(pcm.size),
    }


def _driver_probe() -> dict:
    """Time open/start and a silent first write, preserving physical-audio caveat."""
    import sounddevice as sd
    from audio.tts import SAMPLE_RATE

    started = time.monotonic()
    stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    opened = time.monotonic()
    stream.start()
    started_stream = time.monotonic()
    try:
        stream.write(np.zeros((max(1, SAMPLE_RATE // 100), 1), dtype=np.int16))
        written = time.monotonic()
        return {"device": sd.query_devices(kind="output")["name"], "stream_open_ms": round((opened - started) * 1000, 2),
                "stream_start_ms": round((started_stream - opened) * 1000, 2),
                "first_silent_write_ms": round((written - started_stream) * 1000, 2),
                "reported_output_latency_ms": round(float(stream.latency) * 1000, 2),
                "physical_onset_measured": False}
    finally:
        stream.abort()
        stream.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--voice", default="kokoro:bm_lewis")
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--driver", action="store_true", help="Open output once and submit silence; no audible speech")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        raise SystemExit("--runs must be 1-100")

    from audio import tts
    cold_started = time.monotonic()
    tts.kokoro_reset()
    cold = _synthesize_once("JARVIS is ready.", args.voice, args.rate)
    cold["cold_total_ms"] = round((time.monotonic() - cold_started) * 1000, 2)
    rows = [_synthesize_once("Calculator is open and verified locally.", args.voice, args.rate)
            for _ in range(args.runs)]
    report = {"measurement": "Kokoro waveform readiness and local driver submission; no microphone or physical audibility claim",
              "voice": args.voice, "rate": args.rate, "public_network_required": False, "raw_audio_saved": False,
              "cold": cold, "warm": {key: _summary(rows, key) for key in
                  ("first_wave_chunk_ms", "wave_generation_ms", "wave_prepare_ms", "synthesis_total_ms")},
              "runs": rows}
    if args.driver:
        report["driver"] = _driver_probe()
    rendered = json.dumps(report, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
