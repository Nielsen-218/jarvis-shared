"""Text-to-speech: streams sentences in, plays audio out without gaps.

Two background threads form a pipeline so the next sentence is synthesized
while the current one is playing:  speak() -> [synth thread] -> [play thread].
English uses the configured voice (normally Kokoro, local on the GPU; an edge-tts
voice name also works) after core.speech.spoken() turns screen text into spoken
forms ("Dec. 20" -> "December 20"), and falls back to Kokoro, then the offline
Windows voice (pyttsx3). Haitian Creole sentences are spoken with Meta's MMS
Kreyòl voice, which runs locally (when its model is on this machine; otherwise the
configured voice). French and Spanish sentences get a French and a Spanish voice.
"""
import asyncio
from collections import deque
import os
import queue
import re
import threading
import time

import numpy as np

from audio import pocketvoice   # light: the model itself is only imported when JARVIS_VOICE=pocket:<voice>

SAMPLE_RATE = 24000


# Edge sometimes returns a persistent 403 for a voice/session.  Retrying that
# remote failure for every sentence held up the local Kokoro fallback for many
# seconds.  Keep the breaker per voice so a bad multilingual voice never
# disables an otherwise working configured English voice.
_edge_down_until: dict[str, float] = {}
EDGE_FAILURE_COOLDOWN = 300.0


def edge_reset() -> None:
    """Allow Edge voices to be tried again (used after a network/session reset)."""
    _edge_down_until.clear()


def _edge_auth_or_service_failure(error: BaseException) -> bool:
    text = str(error).lower()
    return "403" in text or "401" in text or "invalid response status" in text


# -- Kokoro: a natural-sounding voice model that runs locally on the GPU (no internet, no cost) ----
# Voices are named like "kokoro:bm_george" (b = British, a = American, f = French; m/f = male/female).
# A blend is two packs averaged by Kokoro itself: "kokoro:bm_lewis,bm_fable". Measured 2026-11-21 (TTS -> Whisper + the
# UTMOSv2 naturalness predictor, ECC/voice-samples-20261121): that blend scored 3.6-3.8 against 3.3-3.4 for bm_lewis
# alone at the same +22%, every key fact heard (67/67), same speed and memory; the rate made little difference.
_kokoro_lock = threading.Lock()
_kokoro_pipes: dict = {}
KOKORO_FRENCH = "kokoro:ff_siwis"
KOKORO_ENGLISH_FALLBACK = "kokoro:bm_lewis"


def kokoro_voice(voice: str) -> str:
    """Select a locally installed conversational voice for any Edge setting.

    ``JARVIS_VOICE`` normally names a Microsoft Edge voice.  Passing that name
    directly to Kokoro caused it to try downloading a nonexistent Kokoro pack
    (for example ``en-GB-RyanNeural.pt``) instead of using the approved local
    fallback.  This mapper is intentionally fixed and local-only.
    """
    return voice if isinstance(voice, str) and voice.startswith("kokoro:") else KOKORO_ENGLISH_FALLBACK


def local_voice_only() -> bool:
    """Whether the established offline mode must avoid an Edge attempt.

    Local-only already forbids cloud intelligence.  Letting English TTS try
    Edge first in that mode made the first audible reply wait for a needless
    network timeout before falling back to the already-warmed Kokoro voice.
    This changes no normal online configuration and keeps Kokoro's existing
    Windows-voice fallback if the local model is unavailable.
    """
    return os.getenv("JARVIS_LOCAL_ONLY", "0") == "1"


# ---- language lessons: which voice speaks each language ------------------------------------------------------------
LESSON_SLOW = "-25%"                 # a beginner hears every phrase in the language at three quarters speed
KOKORO_SPANISH = "kokoro:ef_dora"
_EDGE_NATIVE = {"fr": "fr-FR-DeniseNeural", "es": "es-MX-JorgeNeural", "ja": "ja-JP-KeitaNeural", "zh": "zh-CN-YunxiNeural"}


def _kokoro_cached(voice: str) -> bool:
    """Is this Kokoro voice pack already on this machine? (A lesson never downloads one.)"""
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache("hexgrad/Kokoro-82M", f"voices/{voice.split(':', 1)[1]}.pt"), str)
    except Exception:
        return False


def lesson_voices(code: str, kreyol_voice: str = "", french_voice: str = "") -> list[str]:
    """The voices to try, in order, for a phrase in a lesson language. Local first where a local voice exists:
    French has Kokoro's ff_siwis; Spanish has Kokoro's ef_dora only when its pack is already downloaded; Japanese and
    Chinese need Kokoro's Japanese and Chinese text front ends, which are not installed, so they use Microsoft's
    free online voices; Haitian Creole uses whatever JARVIS_KREYOL_VOICE names ("mms" = Meta's local Creole model),
    which by default is a French online voice reading Creole spelling."""
    if code == "fr":
        return [KOKORO_FRENCH] + [v for v in (french_voice, _EDGE_NATIVE["fr"]) if v][:1]
    if code == "es":
        return ([KOKORO_SPANISH] if _kokoro_cached(KOKORO_SPANISH) else []) + [_EDGE_NATIVE["es"]]
    if code == "ht":
        return [kreyol_voice or "fr-FR-RemyMultilingualNeural"]
    return [_EDGE_NATIVE[code]] if code in _EDGE_NATIVE else []


def lesson_voice_note(code: str) -> dict:
    """What speaks a lesson language on this machine, for the Languages page: {"voice", "local": bool, "note"}."""
    from core.config import Config
    cfg = Config()
    first = (lesson_voices(code, cfg.kreyol_voice, cfg.french_voice) or [""])[0]
    if code == "ht":
        if first == "mms" or creole_ready():
            return {"voice": "Meta MMS Haitian Creole", "local": True, "note": "A Creole voice that runs on this machine."}
        return {"voice": first, "local": False, "note": "A French online voice reading Creole spelling: it sounds French. A real Creole voice is not switched on."}
    if first.startswith("kokoro:"):
        return {"voice": "Kokoro " + first.split(":", 1)[1], "local": True, "note": "Runs on this machine."}
    return {"voice": first, "local": False, "note": "A free online voice: it needs the internet."}


def _kokoro_pipe(lang_code: str):
    with _kokoro_lock:
        if lang_code not in _kokoro_pipes:
            import warnings

            warnings.filterwarnings("ignore", module="torch")
            from kokoro import KPipeline

            _kokoro_pipes[lang_code] = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M")
        return _kokoro_pipes[lang_code]


def _speed(rate: str) -> float:
    """edge-style "+8%" -> 1.08 for Kokoro."""
    try:
        return max(0.7, min(1.4, 1 + float(str(rate).strip().rstrip("%")) / 100))
    except ValueError:
        return 1.0


# Names Kokoro's English voices say wrong, as phonemes in misaki's inline [word](/phonemes/) syntax. Measured with
# Whisper: plain "Aldi" was heard as "oldie" (the British /ˈɔːldi/), "Lidl" as "little".
_SAY_AS = {"Aldi": "ˈɑːldi", "ALDI": "ˈɑːldi", "Lidl": "lˈiːdᵊl", "LIDL": "lˈiːdᵊl"}
_SAY_AS_RE = re.compile(r"(?<![\w\[])(" + "|".join(_SAY_AS) + r")(?![\w\]])")


_kokoro_down_until = 0.0        # circuit breaker: after a GPU failure that a reload didn't cure, skip Kokoro for a while
KOKORO_COOLDOWN = 120.0


def kokoro_reset() -> None:
    """Forget the loaded voice models so the next sentence loads them afresh (after sleep/wake, or a CUDA fault)."""
    global _kokoro_down_until
    with _kokoro_lock:
        _kokoro_pipes.clear()
        _kokoro_down_until = 0.0


def _gpu_fault(error: BaseException) -> bool:
    return "cuda" in str(error).lower() or "device-side" in str(error).lower()


def kokoro_say(text: str, voice: str, rate: str = "+0%") -> np.ndarray:
    """Text -> int16 samples at SAMPLE_RATE (Kokoro's native rate is 24 kHz, same as ours).

    Live log, three sentences in a row: "[tts] voice failed (CUDA error: unknown error)" then "local voice failed
    (CUDA error...)" then Windows' voice — the broken CUDA context was kept and retried on every sentence (slowly).
    A GPU fault now drops the loaded models and loads them once more; if that fails too, Kokoro is skipped for
    KOKORO_COOLDOWN seconds (the fallbacks answer at once) and tried again after."""
    global _kokoro_down_until
    if time.monotonic() < _kokoro_down_until:
        raise RuntimeError("kokoro cooling down after a GPU fault")
    name = kokoro_voice(voice).split(":", 1)[1]
    if name[0] in "ab":  # American / British English
        text = _SAY_AS_RE.sub(lambda m: f"[{m.group(1)}](/{_SAY_AS[m.group(1)]}/)", text)
    try:
        parts = _kokoro_parts(text, name, rate)
    except Exception as error:
        if not _gpu_fault(error):
            raise
        print(f"[tts] GPU voice fault ({str(error)[:60]}); reloading the voice model once")
        with _kokoro_lock:
            _kokoro_pipes.pop(name[0], None)
        try:
            parts = _kokoro_parts(text, name, rate)
        except Exception as again:
            _kokoro_down_until = time.monotonic() + KOKORO_COOLDOWN
            print(f"[tts] GPU voice still failing ({str(again)[:60]}); other voices for {KOKORO_COOLDOWN:.0f} s")
            raise
    if not parts:
        return np.zeros(0, dtype=np.int16)
    audio = np.concatenate(parts)
    return _trim((np.clip(audio, -1, 1) * 32767).astype(np.int16))


def _kokoro_parts(text: str, name: str, rate: str) -> list:
    pipe = _kokoro_pipe(name[0])
    with _kokoro_lock:  # one synthesis at a time on the GPU
        return [a.numpy() if hasattr(a, "numpy") else np.asarray(a) for _, _, a in pipe(text, voice=name, speed=_speed(rate))]


def _trim(pcm: np.ndarray, lead: float = 0.06, tail: float = 0.12, threshold: int = 300) -> np.ndarray:
    """Kokoro pads each sentence with ~0.4 s of silence on both ends, which sounds sluggish between
    sentences and delays the first word; keep only a natural sliver."""
    loud = np.nonzero(np.abs(pcm.astype(np.int32)) > threshold)[0]
    if not len(loud):
        return pcm
    start = max(0, loud[0] - int(lead * SAMPLE_RATE))
    end = min(len(pcm), loud[-1] + int(tail * SAMPLE_RATE))
    return pcm[start:end]


def warm_up_kokoro(voice: str) -> None:
    """Warm the configured voice without delaying it behind an unused language.

    Kokoro serializes all model construction and synthesis on one GPU lock.  The
    old startup worker warmed English and then French unconditionally: once
    English was ready, a first English reply could still wait behind the full
    French model load.  The active voice is the only one that can help the next
    turn; a different language remains safely lazy-loaded when it is actually
    requested.
    """
    try:
        kokoro_say("Ready.", kokoro_voice(voice))
    except Exception as e:
        print(f"[tts] Kokoro voice unavailable ({e}); using the online voice")


class LiveClip:
    """A sentence's audio that is still being made (a streaming voice: audio.pocketvoice). The play thread starts on
    the first chunk instead of waiting for the whole sentence, and reads on as chunks arrive. `len()` is what exists so
    far; `estimate()` the expected total (for the heard-ledger while it is still growing)."""

    def __init__(self, text: str = ""):
        self._buf = np.zeros(SAMPLE_RATE * 4, dtype=np.int16)
        self._n = 0
        self.done = False
        self._cond = threading.Condition()
        words = len(text.split())
        self._expected = int(words / 3.3 * SAMPLE_RATE)       # about 200 words a minute

    def add(self, pcm: np.ndarray) -> None:
        with self._cond:
            need = self._n + len(pcm)
            if need > len(self._buf):
                grown = np.zeros(max(need, 2 * len(self._buf)), dtype=np.int16)
                grown[:self._n] = self._buf[:self._n]
                self._buf = grown
            self._buf[self._n:need] = pcm
            self._n = need
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self.done = True
            self._cond.notify_all()

    def wait_for(self, end: int, timeout: float = 0.1) -> int:
        """Samples available, waiting up to `timeout` for `end` of them (fewer when it finished or the wait ran out)."""
        with self._cond:
            if self._n < end and not self.done:
                self._cond.wait(timeout)
            return self._n

    def view(self) -> np.ndarray:
        with self._cond:
            return self._buf[:self._n]

    def estimate(self) -> int:
        with self._cond:
            return self._n if self.done else max(self._n + SAMPLE_RATE // 2, self._expected)

    def __len__(self) -> int:
        return self._n


class Speaker:
    def __init__(self, voice: str, rate: str = "+0%", muted: bool = False,
                 kreyol_voice: str = "fr-FR-RemyMultilingualNeural",
                 french_voice: str = "fr-FR-RemyMultilingualNeural",
                 other_voice: str = "en-US-AndrewMultilingualNeural"):
        self.voice = voice  # English
        self.kreyol_voice = kreyol_voice  # an edge-tts voice, or "mms" for Meta's Kreyol model
        self.french_voice = french_voice
        self.other_voice = other_voice  # multilingual voice for any other language (lessons)
        self.rate = rate
        self.muted = muted
        self._text_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue()
        self.lifecycle = deque(maxlen=128)  # timestamps only; no sentences or audio
        self._generation = 0  # bumped by stop() so stale audio is dropped
        self.spoken: list[str] = []  # sentences that actually started playing (the echo check: audio.echo)
        # [started, ended or None, text] of the last sentences played, kept across replies (reset_turn clears `spoken`):
        # the mic can still carry "Yes, sir?" said just before a new reply began (data/jarvis.log 2026-11-16: "Yes, sir."
        # cut in on the reply to the next question, voice match 0.18, with nothing of that reply played yet).
        self._played: deque = deque(maxlen=16)
        # The heard-ledger (audio/bargein): a sentence joins `heard` once its LAST chunk was played; stop() adds the part
        # of the cut sentence that played (with "…") and puts that sentence whole, plus everything still queued, in
        # `unsaid` ("go on" replays it from its start, never from mid-sentence).
        self.heard: list[str] = []
        self.unsaid: list[str] = []
        # How each sentence left the queue, kept across replies (reset_turn does not clear it): (monotonic, text, "heard" |
        # "cut"). core/notice counts an unprompted line as announced only once it reads "heard" here.
        self._outcomes: deque = deque(maxlen=64)
        self._ledger = threading.Lock()
        self._clip: list | None = None        # [generation, text, samples written, total samples] of the sentence playing
        self._synthesizing: tuple[int, str] | None = None
        self._gain = 1.0                      # duck(): his voice drops while an interruption is being heard out
        # duck() notes where his voice was when someone cut in: (len(heard), the clip playing, samples of it written).
        # A stop() before restore() was a real interruption, so its cut point is this mark, not where playback ended.
        self._duck_mark: tuple[int, list | None, int] | None = None
        self.last_play_end = 0.0  # monotonic time the last sentence stopped playing (audio.echo window)
        self.first_audio_at: float | None = None
        self._playing: tuple[float, np.ndarray] | None = None  # (start time, audio)
        if not muted:
            threading.Thread(target=self._synth_loop, daemon=True).start()
            threading.Thread(target=self._play_loop, daemon=True).start()
            if kreyol_voice == "mms":  # load it now so the first Kreyol reply isn't delayed
                threading.Thread(target=lambda: creole_say("wi", forced=True), daemon=True).start()
            # Warm the local fallback even when Edge is configured: Edge can
            # reject a session, and the first reply must not then pay Kokoro's
            # multi-second cold model load.
            threading.Thread(target=warm_up_kokoro, args=(kokoro_voice(voice),), daemon=True).start()
            if pocketvoice.is_pocket(voice):   # the human voice (CPU) loads alongside Kokoro (its fallback, GPU)
                threading.Thread(target=pocketvoice.warm_up, args=(voice,), daemon=True).start()

    def speak(self, text: str) -> None:
        text = text.strip()
        if text and not self.muted:
            self._trace("queued")
            self._text_q.put((self._generation, text))

    def _trace(self, event: str) -> None:
        self.lifecycle.append({"event": event, "at_ms": int(time.monotonic() * 1000),
                               "generation": self._generation})

    def wait(self) -> None:
        """Block until everything queued so far has been spoken."""
        if not self.muted:
            self._text_q.join()
            self._audio_q.join()

    def busy(self) -> bool:
        """True while anything is queued, being synthesized, or playing."""
        return self._text_q.unfinished_tasks > 0 or self._audio_q.unfinished_tasks > 0

    def recently_played(self, seconds: float = 10.0) -> list[str]:
        """Sentences playing now or that stopped playing within `seconds`, oldest first, across replies: what the mic may
        still be hearing of him (audio.echo), whatever turn it belonged to."""
        now = time.monotonic()
        return [text for _, ended, text in list(self._played) if ended is None or now - ended <= seconds]

    def output_level(self, window: float = 0.4) -> float:
        """Loudness of what was played over the last `window` seconds. The mic hears
        this as echo, so the listener uses it to tell Jarvis's voice from the user's."""
        playing = self._playing
        if playing is None:
            return 0.0
        started, audio = playing
        end = int((time.monotonic() - started) * SAMPLE_RATE)
        seg = audio[max(0, end - int(window * SAMPLE_RATE)):max(0, end)]
        return float(np.sqrt(np.mean(seg.astype(np.float32) ** 2))) * self._gain if seg.size else 0.0

    def is_playing(self) -> bool:
        return self._playing is not None

    def duck(self, gain: float = 0.3) -> None:
        """Lower his voice at once (the play thread applies it to the next 50 ms chunk) while what cut in is heard out.
        Where his voice was is noted: if it was a real interruption (stop() before restore()), what played on, ducked,
        while he was talking was spoken over, not heard."""
        with self._ledger:
            if self._duck_mark is None:
                clip = self._clip
                self._duck_mark = (len(self.heard), clip, clip[2] if clip is not None else 0)
        self._gain = max(0.0, min(1.0, gain))
        self._trace("duck")

    def restore(self) -> None:
        """Not an interruption ("mhm", a cough, his own echo): full volume, and what played ducked counts as heard."""
        with self._ledger:
            self._duck_mark = None
        self._gain = 1.0
        self._trace("restore")

    def reset_turn(self) -> None:
        """A new reply: the last one's spoken / heard / unsaid sentences are forgotten."""
        with self._ledger:
            self.spoken.clear()
            self.heard.clear()
            self.unsaid = []
            self._duck_mark = None
        self.first_audio_at = None
        self._gain = 1.0

    def heard_text(self) -> str:
        """What he actually heard of this reply (a cut sentence ends in '…')."""
        with self._ledger:
            return " ".join(self.heard)

    def unsaid_text(self) -> str:
        with self._ledger:
            return " ".join(self.unsaid)

    def outcome(self, text: str, since: float = 0.0) -> str | None:
        """'heard' once `text` (queued at or after monotonic `since`) played to its last chunk, 'cut' when stop() took it
        (whole or part of it); None while it is still waiting, or when it never reached the speakers."""
        text = (text or "").strip()
        with self._ledger:
            for at, said, how in reversed(self._outcomes):
                if at < since:
                    break
                if said == text:
                    return how
        return None

    def stop(self) -> None:
        """Cut off speech immediately. Safe from any thread: it only raises a flag;
        the playback thread itself stops its audio stream (PortAudio crashes when a
        stream is stopped from one thread while another is writing to it).
        The heard-ledger keeps the part of the current sentence that played and moves that sentence, with everything
        still queued, to `unsaid`. Stopped while ducked (a real interruption), the cut point is where he began talking
        (duck()), unless the reply had run out by then: what he talked over at its very end ("Shall I?" "Yes.") was heard."""
        if self.muted:
            return
        from audio.bargein import heard_prefix

        with self._ledger:
            old = self._generation
            self._generation += 1
            self._gain = 1.0
            clip, self._clip = self._clip, None
            mark, self._duck_mark = self._duck_mark, None
            playing = clip if clip is not None and clip[0] == old else None
            later: list[str] = [playing[1].strip()] if playing is not None else []    # never heard in full
            with self._audio_q.mutex:   # synthesized and waiting to play
                later += [item[1] for item in self._audio_q.queue if item[0] == old]
            if self._synthesizing is not None and self._synthesizing[0] == old:
                later.append(self._synthesizing[1])
            with self._text_q.mutex:    # not synthesized yet
                later += [item[1] for item in self._text_q.queue if item[0] == old]
            cut, written = playing, playing[2] if playing is not None else 0
            if mark is not None and later:
                # The sentences that finished while he was talking go back to unsaid; the one playing when he began is
                # the cut one (it is now later[0], whether it finished since or is still playing).
                heard_at, at, written = mark
                later = self.heard[heard_at:] + later
                del self.heard[heard_at:]
                cut = at if at is not None and at[0] == old else None
            if cut is not None:
                text = cut[1].strip()
                part = heard_prefix(text, written / cut[3] if cut[3] else 0.0)
                if part == text:
                    self.heard.append(text)     # its last chunk was already out
                    self._outcomes.append((time.monotonic(), cut[1], "heard"))
                    later = later[1:]
                elif part:
                    self.heard.append(part + "…")
            now = time.monotonic()
            self._outcomes.extend((now, sentence, "cut") for sentence in later)
            if clip is not None or later:
                self.unsaid = later
        self._trace("cancel")
        self._playing = None

    # -- pipeline ---------------------------------------------------------
    def _synth_loop(self) -> None:
        while True:
            gen, text = self._text_q.get()
            try:
                if gen == self._generation:
                    with self._ledger:
                        self._synthesizing = (gen, text)
                    self._trace("synth_start")
                    if not self._stream_english(gen, text):     # a streaming voice queued it chunk by chunk itself
                        audio = self._synthesize(text)
                        with self._ledger:   # in exactly one place for stop(): being made, or queued to play
                            if audio is not None:
                                self._audio_q.put((gen, text, audio))
                            self._synthesizing = None
                    self._trace("synth_end")
            finally:
                with self._ledger:
                    self._synthesizing = None
                self._text_q.task_done()

    def _open_output(self):
        """The speakers, as a started int16 mono output stream (None if they can't be opened)."""
        import sounddevice as sd

        try:
            s = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
            s.start()
            return s
        except Exception as e:  # e.g. the speaker device was removed; retried on the next sentence
            print(f"[tts] can't open the speakers ({e}); restart Jarvis if the sound device changed")
            return None

    def _play_loop(self) -> None:
        """Plays sentences through one output stream owned by this thread, in small
        chunks, checking between chunks whether stop() was called."""
        open_stream = self._open_output
        chunk = SAMPLE_RATE // 20  # 50 ms
        stream = open_stream()
        while True:
            gen, text, audio = self._audio_q.get()
            played = None
            try:
                if gen != self._generation:
                    continue
                if stream is None and (stream := open_stream()) is None:
                    continue  # drop the sentence rather than hang the conversation
                self._trace("play_start")
                self.spoken.append(text)
                played = [time.monotonic(), None, text]
                self._played.append(played)
                live = isinstance(audio, LiveClip)      # still being made: play what exists, wait for the rest
                with self._ledger:
                    if gen == self._generation:
                        self._clip = [gen, text, 0, audio.estimate() if live else len(audio)]
                started = time.monotonic()
                self._playing = (started, audio.view() if live else audio)
                if self.first_audio_at is None:
                    self.first_audio_at = time.monotonic()  # for the "[timing]" line in the log
                i = 0
                while True:
                    if gen != self._generation:  # interrupted: drop the rest
                        break
                    if live:
                        have = audio.wait_for(i + chunk)
                        while have < i + chunk and not audio.done and gen == self._generation:
                            have = audio.wait_for(i + chunk)
                        if have <= i or gen != self._generation:   # finished, or stop() came while it waited
                            break
                        piece = audio.view()[i:i + chunk]
                        self._playing = (started, audio.view())
                        with self._ledger:
                            if self._clip is not None and self._clip[0] == gen:
                                self._clip[3] = audio.estimate()
                    else:
                        if i >= len(audio):
                            break
                        piece = audio[i:i + chunk]
                    i += len(piece)
                    gain = self._gain
                    if gain != 1.0:
                        piece = (piece.astype(np.float32) * gain).astype(np.int16)
                    stream.write(np.ascontiguousarray(piece, dtype=np.int16).reshape(-1, 1))
                    with self._ledger:
                        if self._clip is not None and self._clip[0] == gen:
                            self._clip[2] = i
                with self._ledger:   # played to the end (a cut one was taken by stop())
                    if self._clip is not None and self._clip[0] == gen and gen == self._generation:
                        self.heard.append(text)
                        self._outcomes.append((time.monotonic(), text, "heard"))
                        self._clip = None
            except Exception as e:
                print(f"[tts] playback problem ({e}); reopening the audio output")
                try:
                    stream.abort()
                    stream.close()
                except Exception:
                    pass
                stream = open_stream()
            finally:
                with self._ledger:   # a sentence the device failed on is neither heard nor waiting to be cut
                    if self._clip is not None and self._clip[0] == gen and self._clip[1] is text:
                        self._clip = None
                self._playing = None
                self.last_play_end = time.monotonic()
                if played is not None:
                    played[1] = self.last_play_end
                self._trace("play_end")
                self._audio_q.task_done()

    def _stream_english(self, gen: int, text: str) -> bool:
        """With a streaming voice (JARVIS_VOICE=pocket:<voice>), an ordinary English sentence starts playing on its first
        chunk (about 0.05 s) instead of after the whole sentence was made: a LiveClip goes into the play queue and
        fills while it plays. False when this sentence is not for it (another language, a lesson phrase, the voice
        cooling down or giving no first sound in time): the caller makes it the usual way, Kokoro included."""
        if not pocketvoice.is_pocket(self.voice) or not pocketvoice.usable():
            return False
        from audio.language import script, segments, voice_language
        from core.speech import spoken

        if segments(text) or script(text) or voice_language(text) != "en":
            return False
        said = spoken(text)
        if not said:
            return False
        chunks = pocketvoice.stream(said, self.voice, alive=lambda: gen == self._generation)
        try:
            try:
                first = next(chunks)
            except StopIteration:
                return False
            except Exception as e:      # noqa: BLE001 - Kokoro says this sentence
                print(f"[tts] Pocket voice did not start ({str(e)[:80]}); Kokoro says it")
                return False
            clip = LiveClip(said)
            clip.add(first)
            with self._ledger:   # queued to play: no longer "being made" for stop()
                if gen == self._generation:
                    self._audio_q.put((gen, text, clip))
                self._synthesizing = None
            try:
                for piece in chunks:
                    if gen != self._generation:
                        break
                    clip.add(piece)
            except Exception as e:      # noqa: BLE001 - what was made plays; the voice is cooling down
                print(f"[tts] Pocket voice stopped mid-sentence ({str(e)[:80]})")
            finally:
                clip.finish()
            return True
        finally:
            chunks.close()

    def _synthesize(self, text: str):
        from audio.language import script, segments, voice_language

        cut = segments(text)
        if cut:      # a language lesson's line: its phrases in the language's voice, the English around them in his own
            pieces = [a for code, piece, slow in cut
                      if (a := self._lesson_say(piece, code, slow) if code else self._synthesize(piece)) is not None and len(a)]
            return np.concatenate(pieces) if pieces else None
        if script(text):                                    # Japanese or Chinese outside a marked phrase
            return self._lesson_say(text, script(text), False)
        lang = voice_language(text)
        if lang == "ht":       # the local Creole voice when this machine has it; else the configured voice below
            audio = creole_say(text, self.rate, forced=self.kreyol_voice == "mms")
            if audio is not None:
                return audio
        if lang == "es":       # Spanish in ordinary conversation: the same voices a Spanish lesson uses
            audio = self._lesson_say(text, "es", False)
            if audio is not None:
                return audio
        voice = {"ht": CREOLE_FALLBACK if self.kreyol_voice == "mms" else self.kreyol_voice,
                 "fr": self.french_voice, "other": self.other_voice, "es": self.other_voice}.get(lang)
        if voice:
            try:
                return self._say_with(text, voice)
            except Exception as e:
                print(f"[tts] {lang} voice failed ({str(e)[:80]}); trying a local voice")
                try:  # French (and Creole with no Creole voice here): Kokoro's French voice
                    if lang in ("fr", "ht"):
                        return kokoro_say(text, KOKORO_FRENCH, self.rate)
                except Exception as e2:
                    print(f"[tts] local {lang} voice failed ({e2})")
        if lang == "en":  # "Dec. 20" -> "December 20", "62°F" -> "62 degrees", markdown and links out
            from core.speech import spoken

            text = spoken(text)
            if not text:
                return None
            if local_voice_only() and not pocketvoice.is_pocket(self.voice):   # the Pocket voice is local too
                return kokoro_say(text, kokoro_voice(self.voice), self.rate)
        try:
            return self._say_with(text, self.voice)
        except Exception as e:
            print(f"[tts] voice failed ({str(e)[:80]}); trying the local voice")
        try:
            return kokoro_say(text, "kokoro:bm_lewis", self.rate)
        except Exception as e:  # last resort: Windows' built-in voice
            if self.muted:
                # A muted Speaker only makes audio for somewhere else (mp3_for: a phone, the companion's window). Windows'
                # voice plays out of THIS machine's speakers, so it is never the fallback for that: a phone reply would
                # be read aloud in the room where the PC is (review 2026-11-19). Nothing is voiced; the caller decides.
                print(f"[tts] local voice failed ({e}); nothing voiced")
                return None
            print(f"[tts] local voice failed ({e}); using Windows' voice")
            self._offline_say(text)
            return None

    def _lesson_say(self, text: str, code: str, slow: bool):
        """A phrase in a lesson language: the first voice for that language that answers, slowly when asked."""
        rate = LESSON_SLOW if slow else self.rate
        for voice in lesson_voices(code, self.kreyol_voice, self.french_voice):
            try:
                if code == "ht":
                    audio = creole_say(text, rate, forced=voice == "mms")
                    if audio is not None:
                        return audio
                return self._say_with(text, CREOLE_FALLBACK if voice == "mms" else voice, rate)
            except Exception as e:
                print(f"[tts] lesson voice {voice} failed ({str(e)[:80]})")
        try:
            return self._say_with(text, self.other_voice, rate)
        except Exception as e:
            print(f"[tts] no voice for {code} ({str(e)[:80]})")
            return None

    def _say_with(self, text: str, voice: str, rate: str | None = None) -> np.ndarray:
        if voice.startswith("kokoro:"):
            return kokoro_say(text, voice, rate or self.rate)
        if pocketvoice.is_pocket(voice):
            # Kokoro at once while the Pocket voice cools down, and for a phone reply too long for the PC's voice
            # budget (core.pcbridge gives it 2 s + 1 s per 400 characters; Pocket makes about 5 s of speech a second).
            if not pocketvoice.usable() or (self.muted and len(text) > pocketvoice.PHONE_CHARS):
                return kokoro_say(text, kokoro_voice(voice), rate or self.rate)
            return pocketvoice.say(text, voice)
        if time.monotonic() < _edge_down_until.get(voice, 0.0):
            raise RuntimeError("edge voice cooling down after a recent service failure")
        try:
            return asyncio.run(self._edge_tts(text, voice, rate) if rate else self._edge_tts(text, voice))
        except Exception as error:
            if _edge_auth_or_service_failure(error):
                _edge_down_until[voice] = time.monotonic() + EDGE_FAILURE_COOLDOWN
                print(f"[tts] Edge voice {voice} unavailable; using local voice for {EDGE_FAILURE_COOLDOWN:.0f} s")
            raise

    async def _edge_tts(self, text: str, voice: str | None = None, rate: str | None = None) -> np.ndarray:
        import edge_tts
        import miniaudio

        mp3 = bytearray()
        async for chunk in edge_tts.Communicate(text, voice or self.voice, rate=rate or self.rate).stream():
            if chunk["type"] == "audio":
                mp3.extend(chunk["data"])
        decoded = miniaudio.decode(
            bytes(mp3),
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=SAMPLE_RATE,
        )
        return np.frombuffer(decoded.samples, dtype=np.int16)

    @staticmethod
    def _offline_say(text: str) -> None:
        import pyttsx3

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()


# -- Haitian Creole: Meta's MMS voice (facebook/mms-tts-hat), local ------------------------------------------------
# A small VITS model (36M parameters, about 145 MB) that reads Creole as Creole; the French online voice read Creole
# spelling with French sounds. Licence: CC-BY-NC 4.0 (non-commercial: personal use in this household is fine).
# It is used whenever the model is already on this machine (the PC has it; a machine without it keeps the configured
# voice: nothing is downloaded while he waits for a reply). JARVIS_KREYOL_MMS=off turns it off;
# JARVIS_KREYOL_VOICE=mms keeps the old meaning (use it, downloading it on first use).
# When the owner has trained it on his own reading (scripts/kreyol_finetune.py writes models/kreyol-tuned), that tuned
# model is loaded first, then the stock one, then the voice he had before. JARVIS_KREYOL_TUNED=off switches back to
# the stock model (audio.kreyol.tuned_model).
CREOLE_MODEL = "facebook/mms-tts-hat"
CREOLE_FALLBACK = "fr-FR-RemyMultilingualNeural"
CREOLE_GPU_FREE = 1.0e9        # bytes of free GPU memory needed to put it on the card (it needs about 0.3 GB)
_creole_ready: bool | None = None


def creole_ready() -> bool:
    """Is the Creole model on this machine (and the libraries that run it)? Checked once, without the network."""
    global _creole_ready
    if _creole_ready is None:
        try:
            import importlib.util

            from huggingface_hub import try_to_load_from_cache
            from audio import kreyol
            _creole_ready = (os.getenv("JARVIS_KREYOL_MMS", "").strip().lower() != "off"
                             and importlib.util.find_spec("transformers") is not None
                             and importlib.util.find_spec("torch") is not None
                             and (kreyol.tuned_model() is not None
                                  or isinstance(try_to_load_from_cache(CREOLE_MODEL, "model.safetensors"), str)))
        except Exception:
            _creole_ready = False
    return _creole_ready


def _creole_device() -> str:
    """'cuda' when the card is there and has room (a game or the brain may have filled it), else 'cpu'."""
    try:
        import torch

        if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] >= CREOLE_GPU_FREE:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _creole_load(source: str, local_only: bool = True):
    """(model, tokenizer) of a Creole VITS voice: a local folder (the tuned one) or the stock model's name."""
    from transformers import AutoTokenizer, VitsModel

    return (VitsModel.from_pretrained(source, local_files_only=local_only).eval(),
            AutoTokenizer.from_pretrained(source, local_files_only=local_only))


class _CreoleVoice:
    """facebook/mms-tts-hat: a small (36M parameter) Haitian Creole voice; the owner's tuned copy when it exists."""

    def __init__(self, download: bool = False):
        from audio import kreyol

        tuned = kreyol.tuned_model()
        self.source = CREOLE_MODEL
        loaded = None
        if tuned is not None:
            try:
                print("[tts] loading the Haitian Creole voice trained on the owner's reading...")
                loaded = _creole_load(str(tuned))
                self.source = str(tuned)
            except Exception as e:           # a half-copied or broken folder never costs him the Creole voice
                print(f"[tts] the tuned Creole voice did not load ({str(e)[:80]}); using the stock one")
        if loaded is None:
            print("[tts] loading the Haitian Creole voice (Meta MMS, local)...")
            loaded = _creole_load(CREOLE_MODEL, not download)
        self.model, self.tokenizer = loaded
        self.device = "cpu"
        self._to(_creole_device())
        self.lock = threading.Lock()

    def _to(self, device: str) -> None:
        try:
            self.model.to(device)
            self.device = device
        except Exception as e:
            print(f"[tts] Creole voice stays on the CPU ({str(e)[:60]})")
            self.model.to("cpu")
            self.device = "cpu"

    def _wave(self, said: str, speed: float) -> np.ndarray:
        import torch

        inputs = self.tokenizer(said, return_tensors="pt")
        if not inputs["input_ids"].shape[-1]:
            return np.zeros(0, dtype=np.float32)
        self.model.speaking_rate = speed
        torch.manual_seed(7)             # VITS draws noise for its prosody: the same sentence always sounds the same
        with torch.no_grad():
            out = self.model(**{k: v.to(self.device) for k, v in inputs.items()})
        return out.waveform[0].detach().float().cpu().numpy()

    def synthesize(self, text: str, rate: str = "+0%") -> np.ndarray:
        """Creole text -> int16 samples at SAMPLE_RATE: prepared (audio.kreyol: numbers and signs in words), said
        clause by clause with a breath where the punctuation was, resampled from the model's 16 kHz."""
        from audio import kreyol

        speed = _speed(rate)
        parts: list[np.ndarray] = []
        with self.lock:
            for said, pause in kreyol.pieces(text):
                try:
                    wave = self._wave(said, speed)
                except Exception as error:
                    if self.device == "cpu" or not _gpu_fault(error) and "out of memory" not in str(error).lower():
                        raise
                    print(f"[tts] Creole voice: GPU fault ({str(error)[:60]}); carrying on with the CPU")
                    self._to("cpu")
                    wave = self._wave(said, speed)
                if not wave.size:
                    continue
                native = self.model.config.sampling_rate
                wave = np.interp(np.arange(0, wave.size, native / SAMPLE_RATE), np.arange(wave.size), wave)
                peak = max(float(np.abs(wave).max()), 1e-6)
                parts.append(_trim((wave / peak * 0.9 * 32767).astype(np.int16), tail=0.05))
                if pause:
                    parts.append(np.zeros(int(pause * SAMPLE_RATE), dtype=np.int16))
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


_creole = None
_creole_lock = threading.Lock()
_creole_down_until = 0.0        # a load that failed is not retried on every sentence
CREOLE_COOLDOWN = 300.0


def _creole_voice(download: bool = False) -> _CreoleVoice:
    global _creole, _creole_down_until
    with _creole_lock:
        if _creole is None:
            if time.monotonic() < _creole_down_until:
                raise RuntimeError("the Creole voice failed to load a moment ago")
            try:
                _creole = _CreoleVoice(download)
            except Exception:
                _creole_down_until = time.monotonic() + CREOLE_COOLDOWN
                raise
        return _creole


def creole_say(text: str, rate: str = "+0%", forced: bool = False):
    """Creole text in the local Creole voice, or None when this machine has no Creole voice (or it failed): the
    caller then uses the voice it had before."""
    if not (forced or creole_ready()):
        return None
    try:
        audio = _creole_voice(download=forced).synthesize(text, rate)
        return audio if len(audio) else None
    except Exception as e:
        print(f"[tts] Creole voice failed ({str(e)[:80]}); using the other voice")
        return None


def warm_creole() -> None:
    """Load the Creole voice in the background (the conversation just turned to Creole)."""
    if creole_ready() and _creole is None:
        threading.Thread(target=lambda: creole_say("wi"), daemon=True).start()


def beep(freq: float = 880, duration: float = 0.12, wait: bool = True) -> None:
    """Short 'I'm listening' chime."""
    import sounddevice as sd

    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), False)
    tone = 0.25 * np.sin(2 * np.pi * freq * t) * np.hanning(t.size)
    sd.play(tone.astype(np.float32), SAMPLE_RATE)
    if wait:
        sd.wait()


def mp3_for(text: str) -> bytes:
    """Whole reply as one MP3 (for the phone app): each run of sentences in the voice for its
    language, the same voices (and the same local fallbacks) Jarvis uses out loud. When no voice could say it, a tenth
    of a second of silence, as always; a caller that has another voice to fall back to uses mp3_voiced."""
    return _mp3(text)[0]


def mp3_voiced(text: str) -> bytes:
    """mp3_for, but an error when nothing was voiced (every voice failed, or the text has nothing to say). For callers
    that must know: the PC making a phone reply's audio (the laptop then makes it itself) and the audio kept from the
    start-up warm-up. Silence returned as if it were a voice would be played to him as the reply (review 2026-11-19)."""
    data, voiced = _mp3(text)
    if not voiced:
        raise RuntimeError("nothing was voiced")
    return data


def _mp3(text: str) -> tuple[bytes, bool]:
    """(the MP3, whether any voice produced sound for it)."""
    import re

    import lameenc

    from audio.language import voice_language as detect
    from core.config import Config

    cfg = Config()
    other = cfg.other_voice
    try:
        from tools import languages

        if languages.speaker is not None:
            other = languages.speaker.other_voice  # native voice during a lesson
    except Exception:
        pass
    voice = Speaker(cfg.voice, cfg.voice_rate, muted=True, kreyol_voice=cfg.kreyol_voice,
                    french_voice=cfg.french_voice, other_voice=other)
    groups: list[list] = []  # consecutive sentences in the same language are spoken together
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if not sentence:
            continue
        lang = detect(sentence)
        if groups and groups[-1][0] == lang:
            groups[-1][1] += " " + sentence
        else:
            groups.append([lang, sentence])
    pieces = [a for _, chunk in groups if (a := voice._synthesize(chunk)) is not None and len(a)]
    pcm = np.concatenate(pieces) if pieces else np.zeros(SAMPLE_RATE // 10, dtype=np.int16)
    enc = lameenc.Encoder()
    enc.set_bit_rate(64)
    enc.set_in_sample_rate(SAMPLE_RATE)
    enc.set_channels(1)
    enc.set_quality(2)
    return bytes(enc.encode(pcm.astype(np.int16).tobytes()) + enc.flush()), bool(pieces)
