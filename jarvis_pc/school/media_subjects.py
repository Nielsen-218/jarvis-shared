"""School subjects for creative software and multimedia (blueprint area 18), graded against audio files the harness synthesises.

The teacher inspects a real media file with ffprobe (the gate lets ffprobe describe one local file and nothing else). The harness
writes a WAV with known duration, sample rate and channel count, so the answer is checked against what it put in the file.
Nothing is converted or written; the filtering rule (-show_entries) keeps a lazy "dump everything" answer from passing.
"""
from __future__ import annotations

import math
import os
import shutil
import struct
import wave
from typing import List

from .curriculum import Absent, Context, HIGH_SCHOOL, Num, Subject, Task

_DUMP_MARKERS = Absent(("codec_name", "sample_fmt", "bit_rate", "[stream]", "[format]"))


def _make_wav(c: Context, seconds: int, rate: int, channels: int) -> str:
    path = os.path.join(c.folder(c.word(6)), c.word(7) + ".wav")
    frames = bytearray()
    for i in range(rate * seconds):
        v = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += struct.pack("<h", v) * channels
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def _task(c: Context, ask: str, pick: str) -> Task:
    if not shutil.which("ffprobe"):
        return Task(ask, {}, [], None, 0)
    seconds = c.rng.randint(2, 9)
    rate = c.rng.choice([8000, 16000, 22050, 44100])
    channels = c.rng.choice([1, 2])
    path = _make_wav(c, seconds, rate, channels)
    want = {"duration": Num(seconds, 0.01), "rate": Num(rate, 0.0), "channels": Num(channels, 0.0)}[pick]
    return Task(ask, {"file": path}, [want, _DUMP_MARKERS], None, 0)


def _duration(c: Context) -> Task:
    return _task(c, "how many seconds long is the audio file {file}", "duration")


def _rate(c: Context) -> Task:
    return _task(c, "what is the sample rate of the audio file {file}", "rate")


def _channels(c: Context) -> Task:
    return _task(c, "how many audio channels does the file {file} have", "channels")


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("media_duration", "read an audio file's length", hs, _duration, ("audio length", "ffprobe duration")),
        Subject("media_sample_rate", "read an audio file's sample rate", hs, _rate, ("sample rate", "audio quality")),
        Subject("media_channels", "count an audio file's channels", hs, _channels, ("mono or stereo", "audio channels")),
    ]
