"""Does Jarvis wait for the end of a hesitant sentence? An offline end-of-turn check: no microphone, no speakers.

The owner's own hesitant lines (data/jarvis.log 2026-11-09 and 11-10, each split into separate turns live, e.g. "Scan my
PC and look for." / "Look for." / "Things that are wasting my memory...") are spoken by Kokoro on the CPU with a real
pause inserted where he paused. The audio is fed, 80 ms frame by frame, through the listener's real endpointing
(audio/listener.record_command with the Silero VAD) twice: the old 0.6 s pause rule, and Smart Turn v3.2
(audio/endturn). For each line it reports how many turns it became (1 is right) and how long after his last word the turn
was committed. The owner's short commands check the other side: the model must not make them wait.

A synthetic voice is a stand-in for his: the live check (his real voice, five readings of the Nov 9 sentence) is still
the acceptance test. Nothing here touches the running JARVIS, the GPU, the microphone or the speakers.

Run:  python scripts/endpoint_eval.py [--voice kokoro:am_michael] [--pause 0.8] [--transcribe] [--json out.json]
"""
from __future__ import annotations

import os

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"      # Kokoro (and Whisper with --transcribe) on the CPU: the live JARVIS owns the GPU
                                               # ("" would unset it on Windows and leave the GPU visible)
os.environ.setdefault("HF_HUB_OFFLINE", "1")   # the voice model is already on disk; never download here
os.environ["JARVIS_SMART_TURN"] = "on"         # this measures the model, which JARVIS itself only uses when switched on

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audio import endturn                                 # noqa: E402
from audio.listener import FRAME, FRAME_SECS, RATE, Listener   # noqa: E402

# (first part, second part): his words, split where he paused. Source lines in data/jarvis.log.
HESITANT = [
    ("Scan my PC and look for", "things that are wasting my memory."),                      # 2026-11-09 17:5x
    ("You can delete that and look for anything useless that can hold memory of my PC",
     "that's using my RAM."),                                                                # 2026-11-09 17:5x
    ("So anything that I don't need for either", "Jarvis, Blender, or anything like that."),  # 2026-11-09 17:5x
    ("I need you to type in", "Claude message box."),                                       # 2026-11-09
    ("Jarvis, follow the instruction on my screen where Claude is telling me",
     "where to go to open."),                                                                # 2026-11-10 13:2x
    ("Once I'm done clicking on Save,", "the window will close."),                         # 2026-11-10 11:4x
]
SHORT = ["Close ChatGPT.", "Open Blender.", "Stop.", "What time is it?", "Minimize the app in front of me.",
         "Pair my phone."]


def _to_mic(pcm: np.ndarray) -> np.ndarray:
    """Kokoro's 24 kHz -> int16 at the microphone's 16 kHz."""
    from scipy.signal import resample_poly
    return np.clip(resample_poly(pcm.astype(np.float32), 2, 3), -32768, 32767).astype(np.int16)


def speak(text: str, voice: str) -> np.ndarray:
    from audio.tts import kokoro_say
    return _to_mic(kokoro_say(text, voice))


def speak_hesitating(first: str, second: str, voice: str) -> list[np.ndarray]:
    """The WHOLE sentence in one breath, cut after the last word of `first` (Kokoro's own word timings). Synthesising the
    fragment on its own gives it a sentence-final fall, which the model rightly hears as finished; a real hesitation
    stops mid-melody, which this keeps."""
    import re

    from audio.tts import SAMPLE_RATE, _kokoro_pipe, _kokoro_lock, _speed, _trim, kokoro_voice
    name = kokoro_voice(voice).split(":", 1)[1]
    pipe = _kokoro_pipe(name[0])
    want = len(re.findall(r"[\w']+", first))
    chunks, cut, offset, seen = [], None, 0, 0
    with _kokoro_lock:
        for result in pipe(f"{first} {second}", voice=name, speed=_speed("+0%")):
            audio = result.audio.numpy() if hasattr(result.audio, "numpy") else np.asarray(result.audio)
            for token in result.tokens or []:
                if cut is None and re.search(r"\w", token.text or ""):
                    seen += len(re.findall(r"[\w']+", token.text))
                    if seen >= want and token.end_ts is not None:
                        cut = offset + int(token.end_ts * SAMPLE_RATE)
            chunks.append(audio)
            offset += len(audio)
    whole = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype(np.int16)
    if cut is None:
        return [speak(first, voice), speak(second, voice)]
    # Kokoro's padding goes from the outer ends only; the cut itself is left exactly where the word ended.
    return [_to_mic(_trim(whole[:cut], tail=60.0)), _to_mic(_trim(whole[cut:], lead=60.0))]


def last_loud(pcm: np.ndarray, threshold: int = 300) -> int:
    loud = np.nonzero(np.abs(pcm.astype(np.int32)) > threshold)[0]
    return int(loud[-1]) + 1 if len(loud) else 0


def scene(parts: list[np.ndarray], pause: float, rng: np.random.Generator, lead: float = 0.6,
          trail: float = 4.0) -> tuple[np.ndarray, float]:
    """Room noise, his words with `pause` seconds between the parts, then silence. Returns (audio, his last word in s)."""
    gap = np.zeros(int(pause * RATE), np.int16)
    pieces = [np.zeros(int(lead * RATE), np.int16)]
    for n, part in enumerate(parts):
        if n:
            pieces.append(gap)
        pieces.append(part)
    end = sum(len(p) for p in pieces[:-1]) + last_loud(parts[-1])
    pieces.append(np.zeros(int(trail * RATE), np.int16))
    audio = np.concatenate(pieces).astype(np.float32) + rng.normal(0, 25, sum(len(p) for p in pieces))   # a quiet room
    return np.clip(audio, -32768, 32767).astype(np.int16), end / RATE


class Replay(Listener):
    """The real listener's endpointing on recorded frames (no microphone is opened)."""

    def __init__(self, audio: np.ndarray, model=None):        # noqa: super().__init__ would open the microphone
        from openwakeword.vad import VAD
        self.vad = VAD()
        self.speech_threshold = 0.5
        self.end_of_turn = model
        self.last_endpoint = {}
        usable = len(audio) // FRAME * FRAME
        self.frames = list(audio[:usable].reshape(-1, FRAME))
        self.read = 0
        self.check_ms: list[float] = []

    def _next(self) -> np.ndarray:
        if self.read >= len(self.frames):
            raise EOFError
        self.read += 1
        return self.frames[self.read - 1]


def turns(audio: np.ndarray, model) -> list[dict]:
    """Every turn the endpointer cuts the scene into: where it was committed and why."""
    timed = None
    if model is not None:
        def timed(clip):
            started = time.perf_counter()
            p = model(clip)
            listener.check_ms.append((time.perf_counter() - started) * 1000)
            return p
    listener = Replay(audio, timed)
    out = []
    while True:
        try:
            clip = listener.record_command(start_timeout=3.0, smart=model is not None)
        except EOFError:
            break
        if clip is None:
            break
        out.append({"committed_s": round(listener.read * FRAME_SECS, 2), "how": listener.last_endpoint.get("how"),
                    "checks": list(listener.last_endpoint.get("checks", [])), "clip": clip})
    for t in out:
        t["check_ms"] = listener.check_ms
    return out


def transcriber():
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")

    def read(clip: np.ndarray) -> str:
        segments, _ = model.transcribe(endturn.pad_tail(clip).astype(np.float32) / 32768.0, language="en", beam_size=1,
                                       vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()
    return read


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--voice", default="kokoro:am_michael", help="a Kokoro voice already on disk")
    parser.add_argument("--pause", type=float, default=0.8, help="seconds of hesitation inserted mid-sentence")
    parser.add_argument("--transcribe", action="store_true", help="also read each turn with Whisper 'small' on the CPU")
    parser.add_argument("--tail", type=float, help="seconds of the pause the first check hears (listener.SMART_TAIL)")
    parser.add_argument("--json", type=Path, help="write the results here")
    args = parser.parse_args(argv)
    if args.tail is not None:
        from audio import listener as listener_module
        listener_module.SMART_TAIL = args.tail

    model = endturn.SmartTurn.load()
    if model is None:
        print("Smart Turn model unavailable: only the pause rule can be measured.")
    read = transcriber() if args.transcribe else None
    rng = np.random.default_rng(7)
    from audio import listener as listener_module
    results = {"voice": args.voice, "pause_s": args.pause, "tail_s": listener_module.SMART_TAIL, "hesitant": [], "short": []}

    print(f"Hesitant lines, {args.pause:.1f} s pause mid-sentence ({args.voice} on the CPU). 1 turn = he was waited for.")
    for first, second in HESITANT:
        audio, end = scene(speak_hesitating(first, second, args.voice), args.pause, rng)
        row = {"said": f"{first} ... {second}"}
        for name, m in (("pause_rule", None), ("smart_turn", model)):
            if name == "smart_turn" and model is None:
                continue
            got = turns(audio, m)
            row[name] = {"turns": len(got), "commit_after_s": round(got[-1]["committed_s"] - end, 2) if got else None,
                         "checks": [t["checks"] for t in got], "how": [t["how"] for t in got]}
            if read:
                row[name]["heard"] = [read(t["clip"]) for t in got]
        results["hesitant"].append(row)
        smart = row.get("smart_turn", {})
        print(f"- {row['said']}\n    pause rule: {row['pause_rule']['turns']} turn(s)"
              + (f" | smart turn: {smart['turns']} turn(s), committed {smart['commit_after_s']:.2f} s after the last word,"
                 f" checks {smart['checks']}" if smart else ""))
        for name in ("pause_rule", "smart_turn"):
            for heard in row.get(name, {}).get("heard", []):
                print(f"      {name}: {heard!r}")

    print("\nShort commands: the model must not hold them (commit delay after the last word).")
    check_ms: list[float] = []
    for text in SHORT:
        audio, end = scene([speak(text, args.voice)], 0.0, rng)
        row = {"said": text}
        for name, m in (("pause_rule", None), ("smart_turn", model)):
            if name == "smart_turn" and model is None:
                continue
            got = turns(audio, m)
            row[name] = {"turns": len(got), "commit_after_s": round(got[-1]["committed_s"] - end, 2) if got else None,
                         "checks": [t["checks"] for t in got]}
            if got and name == "smart_turn":
                check_ms += got[-1]["check_ms"]
        results["short"].append(row)
        smart = row.get("smart_turn", {})
        print(f"- {text!r}: pause rule {row['pause_rule']['commit_after_s']} s"
              + (f" | smart turn {smart['commit_after_s']} s, checks {smart['checks']}" if smart else ""))

    if model is not None:
        hes = results["hesitant"]
        waited = sum(r["smart_turn"]["turns"] == 1 for r in hes)
        before = sum(r["pause_rule"]["turns"] == 1 for r in hes)
        extra = [r["smart_turn"]["commit_after_s"] - r["pause_rule"]["commit_after_s"] for r in results["short"]
                 if r["smart_turn"]["commit_after_s"] is not None and r["pause_rule"]["commit_after_s"] is not None]
        per = float(np.median(check_ms)) if check_ms else 0.0
        results["summary"] = {"hesitant_one_turn_smart": waited, "hesitant_one_turn_pause_rule": before,
                              "hesitant_total": len(hes), "short_extra_wait_max_s": round(max(extra), 2) if extra else None,
                              "check_ms_median": round(per, 1)}
        print(f"\nSummary: hesitant lines kept whole {waited}/{len(hes)} with Smart Turn vs {before}/{len(hes)} with the "
              f"pause rule; short commands wait at most {max(extra) if extra else 0:.2f} s longer in frames, plus "
              f"~{per:.0f} ms per check on one CPU core.")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
