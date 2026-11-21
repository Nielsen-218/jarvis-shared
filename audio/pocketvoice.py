"""A more human English voice: Kyutai's Pocket TTS, local, on the CPU (no graphics memory, no internet once cached).

JARVIS_VOICE=pocket:<voice> (e.g. ``pocket:charles``, a young English male voice from the VCTK corpus) makes it his
English voice. Measured 2026-11-21 on this PC (ECC/voice-samples-20261121): first sound 0.04-0.11 s after the text when
streamed, about 6x faster than real time on 4 CPU threads, 0 MB of graphics memory (the brain keeps the card), facts
heard 66/67 by Whisper, automatic naturalness (UTMOSv2) 3.5-3.6 against 3.3-3.4 for the old bm_lewis +22% and 3.6-3.8
for the Kokoro blend kokoro:bm_lewis,bm_fable (the recommended default; this voice is the option to try by ear: it is
made from a real person's recorded voice, and a MOS predictor cannot say which of the two he will prefer). Kokoro stays loaded as the fallback:
when this voice cannot load, fails, or falls behind real time (a game holding the CPU), audio.tts uses Kokoro for
COOLDOWN seconds and tries this one again later. It has no speed setting (JARVIS_VOICE_RATE applies to Kokoro only).

Licences: pocket-tts code MIT; weights kyutai/pocket-tts-without-voice-cloning CC-BY-4.0 (no voice cloning used: the
voices are the precomputed catalogue voices; VCTK voices are CC-BY-4.0). Personal use in this household is fine.

The model reads text as written, so a few forms Kokoro's own front end handled get spoken words here first
(``lm_spoken``): money, percentages and a bare ampersand. Measured: "$2.49 a dozen" lost "a dozen" (the model ended the
sentence early after the money) until it was written as words.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time

import numpy as np

PREFIX = "pocket:"
DEFAULT_VOICE = "charles"
SAMPLE_RATE = 24000             # audio.tts plays 24 kHz; Pocket TTS makes 24 kHz (checked at load)
COOLDOWN = 300.0                # after a failure (or falling behind), Kokoro speaks for this long, then this is tried again
FIRST_CHUNK_TIMEOUT = 1.0       # seconds: no first sound by then (CPU taken) -> this sentence is Kokoro's
SLOW_RTF = 0.9                  # making a sentence took 90% of its own length: playback would stutter
SLOW_STRIKES = 2                # two such sentences in a row -> the cooldown
PHONE_CHARS = 140               # a phone reply (whole MP3, made by the PC for the laptop) longer than this is Kokoro's:
                                # core.pcbridge waits 2 s + 1 s per 400 characters, and this voice is not that fast
TARGET_RMS = 0.064             # loudness of the voiced part, Kokoro's measured level (catalogue voices differ 4x)
WARM_LINE = "Good evening, sir. Everything is ready, and the house is quiet."

_lock = threading.Lock()        # one generation at a time (the model's state is not thread-safe)
_model = None
_states: dict = {}
_gain: dict = {}
_down_until = 0.0
_slow = 0
_load_error = ""
_worker = None                  # the thread still inside the model, if a stopped sentence has not let go of it yet


def is_pocket(voice) -> bool:
    return isinstance(voice, str) and voice.startswith(PREFIX)


def voice_name(voice: str) -> str:
    name = voice[len(PREFIX):].strip() if is_pocket(voice) else ""
    return name or DEFAULT_VOICE


def installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("pocket_tts") is not None


def usable() -> bool:
    """Worth trying now: the package is there and no recent failure put it in its cooldown."""
    return time.monotonic() >= _down_until and installed()


def cool_down(reason: str) -> None:
    global _down_until, _slow
    _down_until = time.monotonic() + COOLDOWN
    _slow = 0
    print(f"[tts] Pocket voice {reason}; Kokoro speaks for {COOLDOWN:.0f} s")


def reset() -> None:
    """Forget the cooldown and slowness count (tests, a resume from sleep). The loaded model is kept."""
    global _down_until, _slow
    _down_until = 0.0
    _slow = 0


def _threads() -> int:
    try:
        return max(1, int(os.getenv("JARVIS_POCKET_THREADS", "4")))
    except ValueError:
        return 4


def _load():
    """The model, loaded once. pocket_tts sets torch's CPU threads to 1 for the whole process when imported; the
    process's own setting is put back (Jarvis's other CPU models keep theirs) and generation runs with
    JARVIS_POCKET_THREADS (4) inside the lock."""
    global _model, _load_error
    if _model is None:
        import torch

        before = torch.get_num_threads()
        try:
            from pocket_tts import TTSModel

            model = TTSModel.load_model()
        finally:
            torch.set_num_threads(before)
        if int(getattr(model, "sample_rate", SAMPLE_RATE)) != SAMPLE_RATE:
            raise RuntimeError(f"Pocket TTS makes {model.sample_rate} Hz audio, not {SAMPLE_RATE}")
        _model = model
        _load_error = ""
    return _model


def _state(name: str):
    if name not in _states:
        _states[name] = _load().get_state_for_audio_prompt(name)
    return _states[name]


_MONEY = re.compile(r"\$(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{2}))?(?!\d)")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")


def _money(m: re.Match) -> str:
    dollars, cents = m.group(1).replace(",", ""), m.group(2)
    said = f"{dollars} dollar" + ("" if dollars == "1" else "s")
    if cents and cents != "00":
        said += f" and {int(cents)} cent" + ("" if cents == "01" else "s")
    return said


def lm_spoken(text: str) -> str:
    """The few spoken forms this model needs on top of core.speech.spoken (which ran already)."""
    text = _MONEY.sub(_money, text)
    text = _PERCENT.sub(lambda m: f"{m.group(1)} percent", text)
    return re.sub(r"\s&\s", " and ", text)


def _to_pcm(chunk, gain: float) -> np.ndarray:
    a = chunk.detach().float().cpu().numpy() if hasattr(chunk, "detach") else np.asarray(chunk, dtype=np.float32)
    return (np.clip(a * gain, -1.0, 1.0) * 32767).astype(np.int16)


def voice_gain(sample: np.ndarray) -> float:
    """The gain that brings a voice to Kokoro's loudness, from its voiced samples (float, -1..1): 1.0 when unknown."""
    a = np.asarray(sample, dtype=np.float32)
    voiced = a[np.abs(a) > 0.01]
    if voiced.size < SAMPLE_RATE // 4:
        return 1.0
    rms = float(np.sqrt(np.mean(np.square(voiced))))
    return float(min(4.0, max(0.5, TARGET_RMS / rms)))


def stream(text: str, voice: str, alive=lambda: True, first_timeout: float = FIRST_CHUNK_TIMEOUT):
    """Yield int16 chunks of `text` (already through core.speech.spoken) as they are made. Raises before the first chunk
    when the voice cannot speak now (not installed, cooling down, failed to load, no first sound within
    FIRST_CHUNK_TIMEOUT); the caller then uses Kokoro. `alive()` going False (stop() was called) ends it early."""
    global _slow, _load_error, _worker
    if not usable():
        raise RuntimeError("pocket voice not available now")
    said = lm_spoken(text).strip()
    if not said:
        return
    name = voice_name(voice)
    if not _lock.acquire(timeout=FIRST_CHUNK_TIMEOUT):
        raise TimeoutError("pocket voice busy")
    if _worker is not None and _worker.is_alive():     # a stopped sentence is still finishing its step in the model
        _lock.release()
        raise TimeoutError("pocket voice still finishing the last sentence")
    stop = threading.Event()
    workers: list = []
    out: queue.Queue = queue.Queue()
    try:
        try:
            state = _state(name)
            model = _load()
        except Exception as error:
            _load_error = str(error)[:120]
            cool_down(f"failed to load ({_load_error})")
            raise
        began = time.monotonic()                        # a cold load is not counted as slowness

        def run():
            import torch

            before = torch.get_num_threads()
            torch.set_num_threads(_threads())
            try:
                with torch.no_grad():
                    for piece in model.generate_audio_stream(state, said, stop=stop):
                        out.put(("chunk", piece))
                out.put(("done", None))
            except Exception as error:      # noqa: BLE001 - handed to the reader
                out.put(("error", error))
            finally:
                torch.set_num_threads(before)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        workers.append(worker)
        samples, first = 0, True
        while True:
            try:
                kind, piece = out.get(timeout=first_timeout if first else 10.0)
            except queue.Empty:
                stop.set()
                cool_down("fell behind (no audio in time)")
                if first:
                    raise TimeoutError("pocket voice gave no first sound in time") from None
                return
            if kind == "error":
                cool_down(f"failed ({str(piece)[:80]})")
                if first:
                    raise piece
                return
            if kind == "done":
                break
            if not alive():
                stop.set()
                return
            first = False
            pcm = _to_pcm(piece, _gain.get(name, 1.0))
            samples += len(pcm)
            yield pcm
        took = time.monotonic() - began
        if samples and took > SLOW_RTF * samples / SAMPLE_RATE:
            _slow += 1
            if _slow >= SLOW_STRIKES:
                cool_down(f"too slow ({took:.1f} s for {samples / SAMPLE_RATE:.1f} s of speech)")
        else:
            _slow = 0
    finally:
        stop.set()
        for worker in workers:    # the model is not thread-safe: the next sentence must not enter it while this runs
            worker.join(timeout=0.3)                    # (not long: Kokoro is waiting to say this sentence instead)
            _worker = worker if worker.is_alive() else None
        _lock.release()


def say(text: str, voice: str, first_timeout: float = FIRST_CHUNK_TIMEOUT) -> np.ndarray:
    """The whole of `text` as int16 at SAMPLE_RATE (the phone's MP3, a lesson's English): an error when nothing came."""
    parts = list(stream(text, voice, first_timeout=first_timeout))
    if not parts:
        raise RuntimeError("pocket voice made no audio")
    return np.concatenate(parts)


def warm_up(voice: str) -> None:
    """Load the model and the voice at start, so the first reply does not pay for it (about 3 s on this PC), and
    measure the voice's loudness once (its gain)."""
    name = voice_name(voice)
    try:
        _gain.pop(name, None)
        raw = say(WARM_LINE, voice, first_timeout=15.0)  # at gain 1.0; a cold first run may take a few seconds
        _gain[name] = voice_gain(raw.astype(np.float32) / 32767)
    except Exception as error:      # noqa: BLE001 - Kokoro answers instead
        print(f"[tts] Pocket voice unavailable ({str(error)[:80]}); Kokoro will speak")
