"""Objective speech-clarity benchmark: does a listener actually hear what Jarvis meant to say?

Each test sentence is a realistic Jarvis reply containing the things voice assistants get wrong out loud:
temperatures, money, clock times, percentages, units, abbreviations and markdown leftovers. Jarvis's own voice
(Kokoro) speaks it; Jarvis's own ears (faster-whisper large-v3-turbo) transcribe it; we score whether every KEY
fact (the number, the unit, the name) was heard. Fewer missed facts = clearer speech. Run it to compare voices
and rates, and to check the text normalizer (core.speech.spoken) that prepares text for the voice.

Run:  python scripts/voice_clarity.py [--voices kokoro:bm_lewis,kokoro:bm_george] [--rates +0%,+12%,+22%]
      (a Kokoro blend is one voice: kokoro:bm_lewis,bm_fable; pocket:charles is the Pocket TTS voice)
                                       [--raw]   (skip the normalizer, to measure what it fixes)
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# (text Jarvis might say, {key fact: accepted ways a listener may write it})
CASES = [
    ("It's 62°F and sunny in Springfield, with winds around 8 mph.",
     {"62": ["62", "sixty two"], "degrees": ["degree"], "Springfield": ["springfield"], "8": ["8", "eight"],
      "mph": ["mph", "miles per hour", "miles an hour"]}),
    ("Your total comes to $4.29, and the store closes at 9:30 PM.",
     {"4.29": ["4.29", "4 29", "four twenty nine", "4 dollars and 29", "four dollars and twenty nine", "4 dollars 29"],
      "9:30": ["9:30", "9.30", "930", "9 30", "nine thirty"], "PM": ["pm", "p.m", "p m", "in the evening"]}),
    ("You've completed 75% of this week's goal: 3 workouts and 12 miles.",
     {"75": ["75", "seventy five"], "percent": ["percent", "%"], "3": ["3", "three"], "workouts": ["workout"],
      "12": ["12", "twelve"], "miles": ["mile"]}),
    ("**Note:** the meeting with Dr. Patel moved to Tuesday, Oct. 28.",
     {"note": ["note"], "doctor": ["doctor", "dr"], "Patel": ["patel"], "Tuesday": ["tuesday"],
      "October": ["october", "oct"], "28": ["28", "twenty eight", "28th", "twenty eighth"]}),
    ("Pick up eggs, milk, etc. and about 2 lbs of rice.",
     {"eggs": ["egg"], "milk": ["milk"], "et cetera": ["et cetera", "etc", "etcetera"], "2": ["2", "two"],
      "pounds": ["pound", "lbs", "lb"], "rice": ["rice"]}),
    ("The Aldi on Saville Row is about 5 miles away, roughly 12 min.",
     {"Aldi": ["aldi", "aldy"], "Saville": ["saville", "savile", "seville"], "5": ["5", "five"],
      "miles": ["mile"], "12": ["12", "twelve"], "minutes": ["minute", "min"]}),
    ("- Step 1: unplug the board. - Step 2: check the 5V rail.",
     {"step": ["step"], "unplug": ["unplug"], "board": ["board"], "5": ["5", "five"],
      "volts": ["volt", "5v", "five v"], "rail": ["rail"]}),
    ("Good morning, sir. You have 2 reminders and the forecast is clear.",
     {"morning": ["morning"], "sir": ["sir"], "2": ["2", "two"], "reminders": ["reminder"], "forecast": ["forecast"]}),
    ("Your flight to Miami leaves Dec. 20 at 8:05 AM; I'd leave home by 6.",
     {"Miami": ["miami"], "December": ["december", "dec"], "20": ["20", "twenty", "20th", "twentieth"],
      "8:05": ["8:05", "8.05", "805", "8 05", "eight oh five", "eight o five"], "AM": ["am", "a.m", "a m", "in the morning"],
      "6": ["6", "six"]}),
    ("The 2 TB drive is 87% full; about 260 GB free.",
     {"2": ["2", "two"], "terabyte": ["terabyte", "tb", "t b"], "87": ["87", "eighty seven"], "percent": ["percent", "%"],
      "260": ["260", "two hundred sixty", "two hundred and sixty"], "gigabytes": ["gigabyte", "gb", "g b"]}),
    (f"Done: your reminder is set for {date.today().year}-11-14T17:30.",
     {"reminder": ["reminder"], "November": ["november", "nov"], "14": ["14", "fourteen", "14th", "fourteenth"],
      "5:30": ["5:30", "5.30", "530", "5 30", "five thirty", "17:30", "1730"], "PM": ["pm", "p.m", "p m", "evening"]}),
    ("Eggs are cheapest at Lidl this week, about $2.49 a dozen.",
     {"eggs": ["egg"], "Lidl": ["lidl", "lidel", "leedle"], "2.49": ["2.49", "2 49", "two forty nine", "2 dollars and 49"],
      "dozen": ["dozen"]}),
    ("I put the guide at https://example.com/setup/guide?step=2 for you.",
     {"guide": ["guide"], "link": ["link", "example.com", "example dot com"], "you": ["you"]}),
]


def _norm(s: str) -> str:
    s = s.lower().replace("-", " ")
    s = re.sub(r"[^a-z0-9:.%$ ]+", " ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)          # keep decimal points, drop sentence dots
    s = re.sub(r"(?<=\d)(?=[a-z%])|(?<=[a-z%])(?=\d)", " ", s)   # "9.30pm" -> "9.30 pm", "2tb", "75%"
    return " " + re.sub(r"\s+", " ", s).strip() + " "


def heard(transcript: str, variants: list[str]) -> bool:
    """Whole-word match: 'dec' must not count as heard inside 'deck' (Kokoro said "deck 20" for "Dec. 20")."""
    t = _norm(transcript)
    for v in variants:
        v = _norm(v).strip()
        if re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?:s|th|st|nd|rd)?(?![a-z0-9])", t):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", default="kokoro:bm_lewis")
    ap.add_argument("--rates", default="+22%")
    ap.add_argument("--raw", action="store_true", help="speak the text as-is (no spoken-form normalizer)")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--show", action="store_true", help="print each transcript")
    a = ap.parse_args()

    import numpy as np
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        from audio.stt import Transcriber
        from audio import pocketvoice
        from audio.tts import SAMPLE_RATE, kokoro_say
        ears = Transcriber()
    spoken = (lambda s: s)
    if a.raw:  # the voice as it was: no spoken forms, no pronunciation fixes
        from audio import tts
        tts._SAY_AS_RE = re.compile(r"(?!x)x")
    else:
        from core.speech import spoken                    # noqa: F811  (the normalizer under test)

    results = {}
    for voice in re.split(r",(?=\w+:)", a.voices):     # "kokoro:bm_lewis,bm_fable,pocket:charles": 2 voices
        for rate in a.rates.split(","):
            got = total = 0
            missed, secs, words, confidence = [], 0.0, 0, []
            for text, keys in CASES:
                said = spoken(text)
                with contextlib.redirect_stderr(io.StringIO()):
                    pcm = pocketvoice.say(said, voice) if pocketvoice.is_pocket(voice) else kokoro_say(said, voice, rate)
                secs += len(pcm) / SAMPLE_RATE
                words += len(re.findall(r"[A-Za-z0-9']+", said))
                audio = pcm.astype(np.float32) / 32768.0
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    segs = list(ears.model.transcribe(audio, language="en", beam_size=5)[0])
                    transcript = " ".join(s.text for s in segs)
                confidence += [s.avg_logprob for s in segs]
                for key, variants in keys.items():
                    total += 1
                    if heard(transcript, variants):
                        got += 1
                    else:
                        missed.append(f"{key} <- {transcript.strip()[:70]!r}")
                if a.show:
                    print(f"    said {said!r}\n    heard {transcript.strip()!r}")
            score = got / total
            wpm = words / secs * 60 if secs else 0
            sure = sum(confidence) / len(confidence) if confidence else 0.0   # Whisper's mean log-prob: nearer 0 = clearer
            results[f"{voice} {rate}"] = {"facts_heard": round(score, 3), "missed": missed, "wpm": round(wpm),
                                          "listener_confidence": round(sure, 3)}
            print(f"{voice:18} {rate:>5}  facts heard {got}/{total} ({score:.0%})  ~{wpm:.0f} wpm  "
                  f"listener confidence {sure:.3f}")
            for m in missed:
                print(f"      missed {m}")
    if a.json:
        a.json.write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
