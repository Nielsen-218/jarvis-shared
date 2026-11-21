r"""Hardware/model checks (GPU, speech models, internet voices). Not part of the
fast test suite. Run:  .venv\Scripts\python.exe tests\hw_checks.py"""
import asyncio
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import storage  # noqa: E402

storage.DATA = Path(tempfile.mkdtemp(prefix="jarvis-hw-"))
from audio.tts import Speaker  # noqa: E402

results = []
sp = Speaker("en-GB-RyanNeural", muted=True)


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def say16k(text: str, voice: str) -> np.ndarray:
    a = asyncio.run(sp._edge_tts(text, voice)).astype(np.float32)
    return np.interp(np.arange(0, a.size, 1.5), np.arange(a.size), a).astype(np.int16)


def voice_id():
    from audio.voiceid import VoiceID
    from tools import voice

    vid = VoiceID("cuda")
    owner = "en-US-GuyNeural"
    talk = np.concatenate([say16k(s, owner) for s in [
        "So today I went to the store and picked up a few things for the week.",
        "Then I came back home, cooked some rice and beans, and called my sister in Miami.",
        "Later I want to try on a few outfits and maybe practice some Spanish.",
        "Tomorrow I have church in the morning and then the gym in the afternoon."]])

    class FakeListener:
        def record_speech(self, seconds):
            return talk

    said = []
    voice.voiceid, voice.listener, voice.speaker = vid, FakeListener(), None
    import audio.tts
    audio.tts.beep = lambda *a, **k: None
    voice.pending_enrollment = "owner"
    voice.run_enrollment(said.append)
    check("enrollment", vid.enrolled and "saved" in said[0], f"({said[0]})")
    quiet = np.concatenate([say16k("Hello there.", owner)])
    check("too little speech is refused", "at least 15" in vid.enroll(quiet))
    for label, v, text, want in [
        ("owner, new sentence", owner, "Hey Jarvis, can you open Brave for me?", True),
        ("owner, short command", owner, "Next song.", True),
        ("owner, Kreyol", owner, "Mwen vle manje diri ak pwa jodi a.", True),
        ("another man", "en-US-AndrewNeural", "Hey Jarvis, can you open Brave for me?", False),
        ("another man (UK)", "en-GB-RyanNeural", "Hey Jarvis, can you open Brave for me?", False),
        ("a woman", "en-US-AriaNeural", "Hey Jarvis, can you open Brave for me?", False),
        ("TV-style announcer", "en-US-ChristopherNeural", "Coming up next, the weather and sports.", False),
    ]:
        ok, score = vid.check(say16k(text, v))
        check(f"voice ID: {label}", ok == want, f"(match {score:.2f}, {'accepted' if ok else 'ignored'})")


def two_people():
    """Husband and wife both enrolled: Jarvis must tell them apart."""
    from audio.voiceid import VoiceID

    vid = VoiceID("cuda")
    vid.prints.clear()
    him, her = "en-US-GuyNeural", "en-US-AriaNeural"
    lines = ["So today I went to the store and picked up a few things for the week.",
             "Then I came back home, cooked some rice and beans, and called my sister in Miami.",
             "Later I want to try on a few outfits and maybe practice some Spanish.",
             "Tomorrow I have church in the morning and then the gym in the afternoon."]
    vid.enroll(np.concatenate([say16k(s, him) for s in lines]), "owner")
    vid.enroll(np.concatenate([say16k(s, her) for s in lines]), "wife")
    for label, v, want in [("husband speaking", him, "owner"), ("wife speaking", her, "wife"),
                           ("a visitor", "en-US-AndrewNeural", None), ("another woman", "en-US-JennyNeural", None)]:
        uid, score = vid.identify(say16k("Hey Jarvis, what's on the calendar for tomorrow?", v))
        check(f"who's talking: {label}", uid == want, f"(-> {uid}, match {score:.2f})")


def phone_voice_note():
    """A Telegram voice note (compressed audio file) is decoded and transcribed."""
    import tempfile as tf

    from audio.stt import Transcriber
    from core.phone import PhoneBot

    mp3 = bytearray()

    async def grab():
        import edge_tts

        async for c in edge_tts.Communicate("Remind me to call my mother at six tonight.", "en-US-GuyNeural").stream():
            if c["type"] == "audio":
                mp3.extend(c["data"])
    asyncio.run(grab())
    bot = PhoneBot.__new__(PhoneBot)
    bot.stt = Transcriber("large-v3-turbo", "cuda")
    heard = bot._transcribe(bytes(mp3))
    check("phone voice note transcribed", "mother" in heard.lower() and "six" in heard.lower() or "6" in heard, f"({heard})")


if __name__ == "__main__":
    voice_id()
    two_people()
    phone_voice_note()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
