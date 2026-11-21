"""The owner's Haitian Creole voice recordings: the store behind app/voice-record.html and scripts/kreyol_finetune.

He reads the script (core/kreyol_script) one sentence a take. Everything lives in his own data folder,
data/users/<uid>/voice/kreyol/, which Syncthing carries between the laptop (which serves the phone) and the PC (which
trains). So that the two machines never write the same file, every fact is its own small file:

    takes/<take>.json   what was recorded: sentence id, the uploaded file's name, when, on which machine
    raw/<take>.<ext>    the browser's upload, untouched (webm, ogg, mp4 or wav): always kept
    wav/<take>.wav      mono 16-bit 16 kHz with the silence trimmed: what the voice is trained on
    wav/<take>.json     the verdict of the machine that decoded it: kept (with seconds) or rejected (with the reason)
    edits/<sid>.json    his corrected wording of one sentence

A machine with no decoder (the laptop may have neither PyAV nor ffmpeg) stores the upload and leaves it "pending";
whichever machine can decode converts it later (convert_pending). A Syncthing ".sync-conflict" copy is never read.
A take remembers the wording it was read from: when the sentence is changed afterwards the take asks for a redo and is
not trained on (putting the wording back makes it good again). A redo only replaces the older take once it is KEPT.
"""
import json
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
import wave
from pathlib import Path

import numpy as np

from core import kreyol_script, storage

RATE = 16000                 # facebook/mms-tts-hat is a 16 kHz model (config.sampling_rate)
MAX_BYTES = 20 * 1024 * 1024
MIN_SECONDS, MAX_SECONDS = 0.8, 20.0
QUIET_PEAK = 0.05            # loudest sample below about -26 dBFS: too far from the microphone
CLIP_LEVEL, CLIP_SHARE = 0.985, 0.002
PAD = 0.12                   # seconds of room left around the speech after trimming
MAX_TEXT = 200
MAX_DECODE = 30.0            # seconds: nothing longer is ever decoded (a take is one sentence)
FFMPEG_TIMEOUT = 15          # seconds for one conversion
FFMPEG_FORMATS = {".webm": "matroska", ".ogg": "ogg", ".mp4": "mov", ".wav": "wav", ".mp3": "mp3"}
STALE = "The sentence was changed after this take. Please record it again."
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
EXTENSIONS = {"webm": "webm", "ogg": "ogg", "oga": "ogg", "opus": "ogg", "mp4": "mp4", "m4a": "mp4", "aac": "mp4",
              "wav": "wav", "wave": "wav", "x-wav": "wav", "mpeg": "mp3", "mp3": "mp3"}
clock = time.time            # a test pins it


class RecordError(Exception):
    """Something he can fix; the message is shown to him as it is."""


class NoDecoder(Exception):
    """This machine cannot read the upload's format: the take waits for a machine that can."""


def folder(uid: str | None = None) -> Path:
    return storage.user_dir(uid) / "voice" / "kreyol"


def _files(path: Path, pattern: str) -> list[Path]:
    return sorted(p for p in path.glob(pattern) if ".sync-conflict" not in p.name) if path.is_dir() else []


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")       # two writers never share a temp file
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


# -- the script, with his corrections --------------------------------------------------------------------------------
def clean_text(text: str) -> str:
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(text or "")).split())[:MAX_TEXT]


def sentences(uid: str | None = None) -> list[dict]:
    """The script in order: {"id", "text" (his wording when he changed it), "original", "edited"}."""
    edits = {p.stem: clean_text(_load(p).get("text")) for p in _files(folder(uid) / "edits", "*.json")}
    out = []
    for s in kreyol_script.SENTENCES:
        text = edits.get(s["id"]) or s["text"]
        out.append({"id": s["id"], "text": text, "original": s["text"], "edited": text != s["text"]})
    return out


def save_sentence(sid: str, text: str, uid: str | None = None) -> dict:
    """His wording for one sentence; the script's own wording (or nothing) takes the correction away."""
    original = next((s["text"] for s in kreyol_script.SENTENCES if s["id"] == sid), None)
    if original is None:
        raise RecordError("That sentence is not in the script.")
    text = clean_text(text)
    path = folder(uid) / "edits" / f"{sid}.json"
    if not text or text == original:
        path.unlink(missing_ok=True)
        text = original
    else:
        if not re.search(r"[a-zA-Zèòà]", text):
            raise RecordError("A sentence needs some words.")
        _save(path, {"id": sid, "text": text, "at": int(clock())})
    return {"id": sid, "text": text, "original": original, "edited": text != original}


# -- audio -------------------------------------------------------------------------------------------------------------
def _resample(samples: np.ndarray, rate: int) -> np.ndarray:
    if rate == RATE or not samples.size:
        return samples.astype(np.float32)
    at = np.arange(0, samples.size, rate / RATE)
    return np.interp(at, np.arange(samples.size), samples).astype(np.float32)


def _decode_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getcomptype() != "NONE":
            raise NoDecoder("not 16-bit PCM")
        frames = min(w.getnframes(), int(MAX_DECODE * w.getframerate()) + 1)
        data = np.frombuffer(w.readframes(frames), dtype="<i2").astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
        return _resample(data, w.getframerate())


def _decode_av(path: Path) -> np.ndarray:
    import av

    with av.open(str(path)) as box:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        parts, total, limit = [], 0, int(MAX_DECODE * RATE)
        for frame in box.decode(audio=0):
            for out in resampler.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
                total += parts[-1].size
            if total >= limit:                       # a long or endless file is never decoded whole
                break
        else:
            for out in resampler.resample(None):
                parts.append(out.to_ndarray().reshape(-1))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32) / 32768.0


def _decode_ffmpeg(path: Path, exe: str) -> np.ndarray:
    # Only this local file is read (no network or playlist protocols), as the format its extension names, and never
    # more than MAX_DECODE seconds of it.
    shape = FFMPEG_FORMATS.get(path.suffix.lower())
    done = subprocess.run([exe, "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe", *(["-f", shape] if shape else []),
                           "-t", str(int(MAX_DECODE)), "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                          capture_output=True, timeout=FFMPEG_TIMEOUT)
    if done.returncode != 0:
        raise ValueError(done.stderr.decode("utf-8", "replace")[:200] or "ffmpeg could not read it")
    return np.frombuffer(done.stdout, dtype="<i2").astype(np.float32) / 32768.0


def decoders() -> list[str]:
    """What this machine can decode a browser recording with, best first."""
    import importlib.util

    found = ["av"] if importlib.util.find_spec("av") else []
    if shutil.which("ffmpeg"):
        found.append("ffmpeg")
    return found


def decode(path: Path) -> np.ndarray:
    """An upload as mono float samples at RATE. NoDecoder when this machine cannot read the format; ValueError when
    the file itself is broken."""
    if path.suffix.lower() == ".wav":
        try:
            return _decode_wav(path)
        except (NoDecoder, wave.Error, EOFError):
            pass                                    # an odd WAV (float, 24-bit): the general decoders read those
    have = decoders()
    if not have:
        raise NoDecoder("no audio decoder on this machine")
    error: Exception | None = None
    for name in have:
        try:
            return _decode_av(path) if name == "av" else _decode_ffmpeg(path, shutil.which("ffmpeg"))
        except Exception as e:                      # try the next decoder before giving up on the file
            error = e
    raise ValueError(str(error)[:200])


def trim(samples: np.ndarray) -> np.ndarray:
    """The speech with PAD seconds around it: leading and trailing silence gone."""
    frame = RATE // 50
    count = samples.size // frame
    if not count:
        return samples[:0]
    level = np.sqrt((samples[:count * frame].reshape(count, frame) ** 2).mean(axis=1))
    loud = np.nonzero(level > max(0.003, 0.08 * float(level.max())))[0]
    if not loud.size:
        return samples[:0]
    pad = int(PAD * RATE)
    return samples[max(0, loud[0] * frame - pad):min(samples.size, (loud[-1] + 1) * frame + pad)]


def check(samples: np.ndarray) -> tuple[np.ndarray, str]:
    """(the trimmed take, "") when it is good for training, else (anything, the reason in words he can act on)."""
    if not samples.size:
        return samples, "I heard nothing in that take. Check that the microphone is allowed, then try again."
    if samples.size >= int(MAX_DECODE * RATE):
        return samples[:0], "That take is too long. One sentence a take, please."
    peak = float(np.abs(samples).max())
    if peak < QUIET_PEAK:
        return samples, "That take is too quiet. Hold the phone a little closer, about a hand's width from your mouth."
    if float((np.abs(samples) >= CLIP_LEVEL).mean()) > CLIP_SHARE:
        return samples, "That take is too loud and distorts. Move the phone a little further away and try again."
    cut = trim(samples)
    seconds = cut.size / RATE
    if seconds < MIN_SECONDS:
        return cut, "That take is too short. Read the whole sentence, then stop."
    if seconds > MAX_SECONDS:
        return cut, "That take is too long. One sentence a take, please."
    return cut, ""


def write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _convert(base: Path, take: str, raw: Path) -> dict:
    """Decode one upload, judge it and write its WAV and verdict. NoDecoder passes through (the take stays pending)."""
    try:
        samples, reason = check(decode(raw))
    except NoDecoder:
        raise
    except Exception:
        samples, reason = np.zeros(0, dtype=np.float32), "I could not read that recording. Please record it again."
    if reason:
        verdict = {"id": take, "state": "rejected", "reason": reason}
    else:
        peak = float(np.abs(samples).max())
        write_wav(base / "wav" / f"{take}.wav", samples * (0.9 / peak))        # one loudness for every clip
        verdict = {"id": take, "state": "kept", "file": f"wav/{take}.wav", "seconds": round(samples.size / RATE, 2)}
    verdict["by"] = socket.gethostname()
    _save(base / "wav" / f"{take}.json", verdict)
    return verdict


# -- takes -------------------------------------------------------------------------------------------------------------
def _extension(filename: str, content_type: str) -> str:
    for hint in (Path(filename or "").suffix.lstrip(".").lower(),
                 (content_type or "").split(";")[0].split("/")[-1].strip().lower()):
        if hint in EXTENSIONS:
            return EXTENSIONS[hint]
    return "webm"


def takes(uid: str | None = None) -> list[dict]:
    """Every take with its state: {"id", "sentence", "state" (kept, pending or rejected), "seconds", "reason",
    "created", "file" (the WAV, when kept), "raw"}; oldest first."""
    base = folder(uid)
    wording = {s["id"]: s["text"] for s in sentences(uid)}
    out = []
    for path in _files(base / "takes", "*.json"):
        meta = _load(path)
        if not meta.get("sentence"):
            continue
        take = path.stem
        verdict = _load(base / "wav" / f"{take}.json")
        state = verdict.get("state") if verdict.get("state") in ("kept", "rejected") else "pending"
        if state == "kept" and not (base / "wav" / f"{take}.wav").is_file():
            state = "pending"                      # the verdict arrived before its WAV (sync in progress)
        try:
            hint = max(0.0, min(float(meta.get("seconds") or 0), MAX_SECONDS))
        except (TypeError, ValueError):
            hint = 0.0
        reason = verdict.get("reason", "") if state == "rejected" else ""
        if state != "rejected" and meta.get("text") and meta["text"] != wording.get(meta["sentence"], meta["text"]):
            state, reason = "rejected", STALE       # read from a wording he has since changed: never trained on
        out.append({"id": take, "sentence": meta["sentence"], "state": state, "raw": meta.get("raw", ""),
                    "created": meta.get("created", 0), "reason": reason,
                    "seconds": float(verdict.get("seconds") or 0) if state == "kept" else hint,
                    "file": f"wav/{take}.wav" if state == "kept" else ""})
    return sorted(out, key=lambda t: (t["created"], t["id"]))


def current_takes(uid: str | None = None) -> dict[str, dict]:
    """The take of each sentence the page shows: the newest one that is kept or waiting, else the newest refused one
    (a redo that failed its check never hides the good take it was meant to replace)."""
    best: dict[str, dict] = {}
    for t in takes(uid):
        if t["state"] != "rejected" or best.get(t["sentence"], t)["state"] == "rejected":
            best[t["sentence"]] = t
    return best


def _supersede(base: Path, uid: str | None, take: str) -> None:
    """A take that is now KEPT replaces every other take of its sentence."""
    mine = takes(uid)
    sid = next((t["sentence"] for t in mine if t["id"] == take), None)
    for other in mine:
        if other["sentence"] == sid and other["id"] != take:
            _remove(base, other["id"])


def _lock(take: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(take, threading.Lock())


def _remove(base: Path, take: str) -> bool:
    found = False
    for sub in ("takes", "raw", "wav"):
        for path in (base / sub).glob(f"{take}.*") if (base / sub).is_dir() else ():
            path.unlink(missing_ok=True)
            found = True
    return found


def delete_take(take: str, uid: str | None = None) -> bool:
    if not re.fullmatch(r"s\d{3}-[0-9a-f]{8,32}", take or ""):
        return False
    return _remove(folder(uid), take)


def add_take(sid: str, data: bytes, filename: str = "", content_type: str = "", seconds: float = 0.0,
             uid: str | None = None) -> dict:
    """Keep one take of a sentence. The upload is always stored; when this machine can decode it, it is judged now
    (a bad take is refused with the reason and nothing is kept), otherwise it waits as "pending". A new take replaces
    the older takes of that sentence only once it is kept: a redo still waiting for its check leaves them alone."""
    wording = next((s["text"] for s in sentences(uid) if s["id"] == sid), None)
    if wording is None:
        raise RecordError("That sentence is not in the script.")
    if not data:
        raise RecordError("That recording is empty. Try again.")
    if len(data) > MAX_BYTES:
        raise RecordError("That recording is too large. One sentence a take, please.")
    base = folder(uid)
    take = f"{sid}-{uuid.uuid4().hex[:12]}"
    raw = base / "raw" / f"{take}.{_extension(filename, content_type)}"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(data)
    try:
        verdict = _convert(base, take, raw)
    except NoDecoder:
        verdict = {"state": "pending"}
    if verdict["state"] == "rejected":
        _remove(base, take)
        raise RecordError(verdict["reason"])
    try:
        hint = round(max(0.0, min(float(seconds or 0), MAX_SECONDS)), 2)
    except (TypeError, ValueError):
        hint = 0.0
    _save(base / "takes" / f"{take}.json", {"id": take, "sentence": sid, "text": wording, "raw": f"raw/{raw.name}",
                                            "seconds": hint, "created": int(clock()), "machine": socket.gethostname()})
    if verdict["state"] == "kept":
        _supersede(base, uid, take)
    return next(t for t in takes(uid) if t["id"] == take)


def convert_pending(uid: str | None = None, limit: int = 0) -> dict:
    """Decode the takes that were uploaded through a machine with no decoder. {"converted", "rejected", "waiting"}.
    One take is converted by one caller at a time (a second page load skips it), a take that already has a verdict
    is left alone, and a conversion that cannot be done now (no decoder, a file still syncing, a full disk) leaves
    the take waiting instead of failing the caller."""
    base = folder(uid)
    done = {"converted": 0, "rejected": 0, "waiting": 0}
    for take in takes(uid):
        if take["state"] != "pending":
            continue
        raw = base / take["raw"] if take["raw"] else None
        lock = _lock(take["id"])
        if (raw is None or not raw.is_file() or (limit and done["converted"] + done["rejected"] >= limit)
                or not lock.acquire(blocking=False)):
            done["waiting"] += 1
            continue
        try:
            if (base / "wav" / f"{take['id']}.json").is_file():        # someone decided while we waited
                continue
            verdict = _convert(base, take["id"], raw)
            if verdict["state"] == "kept":
                _supersede(base, uid, take["id"])
            done["converted" if verdict["state"] == "kept" else "rejected"] += 1
        except (NoDecoder, OSError):
            done["waiting"] += 1
        finally:
            lock.release()
    return done


def audio_path(take: str, uid: str | None = None) -> Path | None:
    """The file to play a take back from: its WAV, else the upload."""
    if not re.fullmatch(r"s\d{3}-[0-9a-f]{8,32}", take or ""):
        return None
    base = folder(uid)
    for path in [base / "wav" / f"{take}.wav", *_files(base / "raw", f"{take}.*")]:
        if path.is_file():
            return path
    return None


def overview(uid: str | None = None) -> dict:
    """What the page shows: the script with each sentence's take, and how far he is."""
    mine = current_takes(uid)
    rows = []
    for s in sentences(uid):
        t = mine.get(s["id"])
        rows.append(dict(s, take=t and {k: t[k] for k in ("id", "state", "seconds", "reason")}))
    good = [t for t in mine.values() if t["state"] != "rejected"]
    return {"sentences": rows, "kept": len(good), "seconds": round(sum(t["seconds"] for t in good), 1),
            "pending": sum(t["state"] == "pending" for t in good), "target_seconds": kreyol_script.TARGET_SECONDS,
            "total": len(rows), "decoder": bool(decoders())}


def kept(uid: str | None = None) -> list[dict]:
    """The takes ready for training, in script order: {"id", "sentence", "text", "file" (absolute Path), "seconds"}.
    The newest KEPT take of each sentence, read from the wording the sentence has now."""
    base = folder(uid)
    mine = {t["sentence"]: t for t in takes(uid) if t["state"] == "kept"}
    return [{"id": mine[s["id"]]["id"], "sentence": s["id"], "text": s["text"],
             "file": base / mine[s["id"]]["file"], "seconds": mine[s["id"]]["seconds"]}
            for s in sentences(uid) if s["id"] in mine]
