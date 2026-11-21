"""Speech-to-text with faster-whisper (runs locally, audio never leaves the PC).

Understands English, French and Haitian Creole (plus whatever language a
lesson is currently teaching). Whisper's language detector often hears Kreyòl
as French, so when it says "French or Kreyòl" both transcriptions are made and
the words decide which one it really was.
"""
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from audio.language import scores


def _add_cuda_dlls() -> None:
    """faster-whisper's GPU backend needs cuBLAS/cuDNN; PyTorch ships them."""
    if sys.platform != "win32":
        return
    try:
        import importlib.util

        spec = importlib.util.find_spec("torch")
        if spec and spec.origin:
            lib = Path(spec.origin).parent / "lib"
            if lib.exists():
                os.add_dll_directory(str(lib))
                os.environ["PATH"] = str(lib) + os.pathsep + os.environ["PATH"]
    except Exception:
        pass


# Names Whisper mishears because they are not ordinary English words (live: "Cloud" for Claude, "chat TVT" / "cheap UT"
# for ChatGPT, "Travis" / "Acharya's" for Jarvis). faster-whisper's `hotwords` puts them in the decoder prompt so it
# prefers these spellings when it hears something close; it does not add words that were not spoken (measured with
# scripts/stt_hotwords_bench.py, noise-only clips included). JARVIS_HOTWORDS adds more, comma-separated.
# Jarvis's own windows were not in the list: "Command Center" came out as "Comment Center" / "Community Centre" (his real
# lines, 2026-11-12..15). core/heard.py and core/hud._cc_heard catch the slips that still get through.
CORE_HOTWORDS = ["Jarvis", "ChatGPT", "Claude", "Discord", "Blender", "Unreal Engine", "Shadow World", "Apex Legends",
                 "Steam", "Edge", "GitHub", "YouTube", "Spotify", "Command Center", "World Intelligence"]
HOTWORDS_MAX_CHARS = 300          # the prompt also holds context: keep the list short


def hotwords(include_open: bool = True) -> str:
    """The names for Whisper, period-separated ("Jarvis. ChatGPT. Claude."): the core list, the owner's own (JARVIS_HOTWORDS),
    then the apps open now. Periods, not commas: with a comma list Whisper sometimes glued the command to the name
    ("CloseGithub", "OpenShadowWorld"); periods measured 85/85 names right and nothing glued (stt_hotwords_bench)."""
    own = os.getenv("JARVIS_HOTWORDS", "")
    words = list(CORE_HOTWORDS) + ([] if own.strip().casefold() == "off" else [w.strip() for w in own.split(",") if w.strip()])
    if include_open:
        try:
            from core import targets
            words += [targets.display(r["app"]) for r in targets.open_apps()]
        except Exception:  # noqa: BLE001  (no desktop to read: the fixed names still help)
            pass
    seen, out = set(), []
    for word in words:
        key = word.casefold()
        if key not in seen and len(". ".join(out + [word])) + 1 <= HOTWORDS_MAX_CHARS:
            seen.add(key)
            out.append(word)
    return (". ".join(out) + ".") if out else ""


_GLUED = __import__("re").compile(r"^((?i:open|close|minimi[sz]e|launch|start|quit|exit|show|hide))((?:[A-Z][a-z0-9]*)+)[.!?]?$")


# Lines Whisper is known to invent from silence or noise (its training subtitles): live 2026-11-08 "Sous-titrage Société
# Radio-Canada" arrived as if the owner had said it. A transcript that is only one of these is dropped.
_PHANTOMS = __import__("re").compile(
    r"^\W*(?:sous-titrage soci[ée]t[ée] radio-canada|sous-titres r[ée]alis[ée]s par la communaut[ée] d'amara\.org|"
    r"merci d'avoir regard[ée]|thanks? (?:you )?for watching|thank you for watching|please subscribe|"
    r"subtitles by the amara\.org community)\W*$", __import__("re").I)
_CLOSED = __import__("re").compile(r"^closed\b(?=\s+\S)", __import__("re").I)


def clean_heard(text: str) -> str:
    """The transcript as JARVIS should read it: a phantom subtitle line -> '' (nothing was said); a short command heard as
    "Closed ChatGPT." -> "Close ChatGPT." (Whisper hears the imperative as past tense; live 2026-11-08 he answered
    "Very good, sir." and closed nothing); a glued command split (unglue)."""
    t = (text or "").strip()
    if _PHANTOMS.match(t):
        return ""
    if _CLOSED.match(t) and len(t.split()) <= 6:
        t = "Close" + t[len("closed"):]
    return unglue(t)


def unglue(text: str) -> str:
    """'OpenShadowWorld' -> 'Open Shadow World': a command run together with its app name (a rare Whisper slip with a
    name prompt). Only a whole transcript of one capitalized verb + CamelCase name is split; anything else is unchanged."""
    import re
    t = (text or "").strip()
    m = _GLUED.match(t) if " " not in t else None
    if not m:
        return text
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", m.group(2))
    return f"{m.group(1)} {name}"


LEAN = 0.15     # added to the probability of the language the conversation is in


def _gpu_fault(error: BaseException) -> bool:
    text = str(error).lower()
    return "cuda" in text or "cudnn" in text or "device-side" in text or "out of memory" in text


class Transcriber:
    def __init__(self, model: str = "large-v3-turbo", device: str = "cuda"):
        from faster_whisper import WhisperModel

        if device == "cuda":
            _add_cuda_dlls()
        self.multilingual = not model.endswith(".en")
        compute = "float16" if device == "cuda" else "int8"
        print(f"[stt] loading whisper '{model}' on {device} (first run downloads it)...")
        self.on_gpu = False
        self._wanted = (model, device, compute)          # what to reload after a GPU fault (see _heal)
        self._healed = 0
        try:
            self.model = WhisperModel(model, device=device, compute_type=compute)
            self.on_gpu = device == "cuda"
        except Exception as e:
            if device != "cuda":
                raise
            print(f"[stt] GPU unavailable ({e}); falling back to CPU 'small'")
            self.model = WhisperModel("small", device="cpu", compute_type="int8")
        self.last_language = "en"
        if os.getenv("JARVIS_HOTWORDS", "").strip().casefold() != "off":
            print(f"[stt] app names for Whisper (English): {len(hotwords(include_open=False).split('. '))} fixed + the apps open now")
        self.extra_language: str | None = None  # e.g. "es" while a Spanish lesson is on
        self.forced_language: str | None = None  # set when the user locks the language
        self._lock = threading.Lock()  # dictation and conversation may transcribe at once
        # Fast first-pass: Parakeet TDT. It's quicker AND more accurate than Whisper 'small' on a CPU,
        # but LESS accurate than Whisper large-v3-turbo on a GPU - so on a GPU box it would only make
        # short commands ("pair my phone") worse. Default ("auto") therefore uses it only when Whisper
        # isn't on the GPU (e.g. the laptop). JARVIS_STT=parakeet forces it on, =whisper forces it off.
        self.fast = None
        pref = os.getenv("JARVIS_STT", "auto").lower()
        if self.multilingual and pref != "whisper" and (pref == "parakeet" or not self.on_gpu):
            try:
                from audio import parakeet

                if parakeet.available():
                    self.fast = parakeet.Parakeet()
            except Exception as e:
                print(f"[stt] Parakeet unavailable, using Whisper only: {type(e).__name__}: {e}")

    def warm(self) -> float:
        """Run the recogniser once at start on a second of quiet noise, so his first words do not pay for it. Measured
        2026-11-14 on this PC (large-v3-turbo, CUDA): the first transcription after a load took 2.2 s, the next ones 0.1 s,
        and the first spoken turn after each of 10 restarts took 7.2 s (later turns 2.9 s; speech-to-text 1.3 s vs 0.3 s).
        The decoder must really run, so no voice filter here (it would drop the noise and skip the model). Nothing is
        heard, kept or reported: no language is set, and a failure only leaves the first turn as slow as it was.
        Returns the seconds it took (0.0 when it did not run)."""
        started = time.monotonic()
        try:
            rng = np.random.default_rng(7)
            samples = (rng.standard_normal(24000) * 0.003).astype(np.float32)       # 1.5 s, far below speech level
            with self._lock:
                if self.multilingual and hasattr(self.model, "detect_language"):
                    try:
                        self.model.detect_language(samples)                          # the pass every utterance starts with
                    except Exception:  # noqa: BLE001
                        pass
                # temperature 0: noise must never set off the fallback's re-decodes while the lock is held
                segments, _ = self.model.transcribe(samples, language="en", beam_size=5 if self.on_gpu else 1,
                                                    vad_filter=False, temperature=0)
                for _segment in segments:                                            # a generator: decoding happens here
                    pass
                # ...and once the way a real line goes in: the voice filter's own small model loads on first use too
                segments, _ = self.model.transcribe(samples, language="en", beam_size=1, vad_filter=True, temperature=0)
                for _segment in segments:
                    pass
        except Exception:  # noqa: BLE001
            return 0.0
        return max(time.monotonic() - started, 0.001)       # (the clock's step is 15 ms on Windows: a run is never 0.0)

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: int16 mono at 16 kHz. Sets self.last_language ('en', 'fr', 'ht' or a lesson language)."""
        with self._lock:
            return self._transcribe(audio)

    def _transcribe(self, audio: np.ndarray) -> str:
        samples = audio.astype(np.float32) / 32768.0
        if not self.multilingual:
            self.last_language = "en"
            return self._run(samples, "en")
        if self.forced_language:  # "speak only Kreyol" / "only French"
            self.last_language = self.forced_language
            if self.fast and self.forced_language in ("en", "fr"):
                text = self.fast.transcribe(samples)
                if text:
                    return self._heard(text, self.forced_language, 1.0)
            return self._heard(self._run(samples, self.forced_language), self.forced_language, 1.0)

        # Parakeet first pass (fast, accurate for en/fr). Not during a Spanish, Japanese or Chinese lesson: it only
        # writes English and French, and returned his Spanish as English words before Whisper was ever asked. Nor
        # while the conversation is in Creole or Spanish (audio.language's sticky language): same reason.
        from audio import language
        lean = language.spoken_language()
        lesson = self.extra_language and self.extra_language not in ("en", "fr", "ht")
        if self.fast and not lesson and lean not in ("ht", "es"):
            text = self.fast.transcribe(samples)
            if text:
                sc = scores(text)
                lang = max(("en", "fr", "ht"), key=lambda ll: sc.get(ll, 0.0))
                if lang == "ht":  # Parakeet can't do Kreyol: let Whisper confirm it
                    ht = self._run(samples, "ht")
                    self.last_language = "ht"
                    return self._heard(ht or text, "ht", 0.5)
                self.last_language = lang if lang in ("en", "fr") else "en"
                return text                       # no ear evidence: the words decide (audio.language.identify)
            # empty (silence, or something Parakeet couldn't place): fall through to Whisper

        # Whisper's own language probabilities, kept to the languages he really uses with Jarvis (en, ht, fr, es, and
        # a lesson's language): its guess over all 99 sent Creole to Portuguese or Malay now and then. The language
        # the conversation is in gets a lean, so a short unclear line stays in it.
        probs = self._probabilities(samples)
        allowed = list(language.USED) + ([self.extra_language] if lesson else [])
        total = sum(probs.get(code, 0) for code in allowed) or 1.0
        kept = {code: probs.get(code, 0) / total for code in allowed}
        leaned = dict(kept)
        if (lesson and self.extra_language or lean) in leaned:
            leaned[lesson and self.extra_language or lean] += LEAN
        candidates = {code: p for code, p in leaned.items() if code not in ("fr", "ht")}
        candidates["romance"] = leaned["fr"] + leaned["ht"]
        best = max(candidates, key=candidates.get)
        short = len(samples) < 1.2 * 16000        # under about a second Whisper's language guess is a coin toss

        if best != "romance":
            self.last_language = best
            sure = min(kept[best], 0.5) if short else kept[best]
            return self._heard(self._run(samples, best), best, sure)
        fr_text = self._run(samples, "fr")
        ht_text = self._run(samples, "ht")
        # Words in each transcript + Whisper's own guess + a lean toward Kreyòl (the
        # user's language) and toward whichever of the two they spoke last.
        ht_score = scores(ht_text)["ht"] + 0.4 * probs.get("ht", 0) + 0.08
        fr_score = scores(fr_text)["fr"] + 0.4 * probs.get("fr", 0)
        last = lean if lean in ("ht", "fr") else self.last_language
        if last in ("ht", "fr"):
            ht_score += 0.1 if last == "ht" else 0
            fr_score += 0.1 if last == "fr" else 0
        sure = (kept["fr"] + kept["ht"]) * (1.0 if abs(ht_score - fr_score) >= 0.15 else 0.6)
        sure = min(sure, 0.5) if short else sure
        if ht_score >= fr_score:
            self.last_language = "ht"
            return self._heard(ht_text, "ht", sure)
        self.last_language = "fr"
        return self._heard(fr_text, "fr", sure)

    def _heard(self, text: str, code: str, sure: float) -> str:
        """Hand the ear's evidence for this line to the conversation's language (audio.language.observe reads it when
        the same words arrive as his turn)."""
        from audio import language
        self.last_confidence = sure
        language.heard(text, code, sure)
        return text

    def _run(self, samples: np.ndarray, lang: str) -> str:
        # Beam search is noticeably more accurate on short commands and costs ~0.1 s on the GPU;
        # greedy stays on the CPU where it would be slow.
        beam = 5 if getattr(self, "on_gpu", False) else 1
        # English only: the names are English spellings, and the French/Kreyol comparison (above) must hear both
        # languages the same way. JARVIS_HOTWORDS=off turns the names off.
        extra = {}
        if lang == "en" and os.getenv("JARVIS_HOTWORDS", "").strip().casefold() != "off":
            names = hotwords()
            if names:
                extra["hotwords"] = names
        for attempt in range(3):                      # as loaded; reloaded on the GPU; the CPU 'small' model
            try:
                segments, _ = self.model.transcribe(samples, language=lang, beam_size=beam, vad_filter=True, **extra)
                return clean_heard(" ".join(s.text.strip() for s in segments).strip())
            except Exception as error:
                if not _gpu_fault(error) or attempt == 2 or not self._heal(error, cpu=attempt == 1):
                    raise
                beam = 5 if self.on_gpu else 1
        return ""

    def _heal(self, error: BaseException, cpu: bool = False) -> bool:
        """A CUDA fault mid-transcription (seen in the GPU voice after sleep/wake; Whisper shares the card): load the
        model again once; if the GPU faults again, carry on deaf-proof on the CPU 'small' model instead of raising
        on every utterance. Returns True when there is a model to retry with."""
        from faster_whisper import WhisperModel
        model, device, compute = self._wanted
        self._healed += 1
        if cpu or device != "cuda":
            print(f"[stt] GPU still failing ({str(error)[:60]}); listening with the CPU 'small' model")
            model, device, compute = "small", "cpu", "int8"
        else:
            print(f"[stt] GPU fault ({str(error)[:60]}); reloading whisper '{model}' on {device}")
        try:
            self.model = WhisperModel(model, device=device, compute_type=compute)
            self.on_gpu = device == "cuda"
            return True
        except Exception as again:
            print(f"[stt] reload failed ({str(again)[:60]})")
            return False

    def _probabilities(self, samples: np.ndarray) -> dict[str, float]:
        try:
            _, _, probs = self.model.detect_language(samples)
            return dict(probs)
        except Exception:
            return {self.last_language: 1.0}
