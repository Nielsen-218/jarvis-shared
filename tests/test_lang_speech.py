"""Speech during a language lesson: marked phrases go to the language's voice (slowly for a beginner), the English
around them to Jarvis's own voice, brackets are not read, and the recogniser asks Whisper during a foreign lesson.
No voice is synthesised and no recogniser is loaded: every engine call is a recording stand-in."""
import numpy as np
import pytest

from audio import language, stt, tts


@pytest.fixture(autouse=True)
def clean():
    language.clear_marks()
    yield
    language.clear_marks()


@pytest.fixture
def speaker(monkeypatch):
    calls = []
    sp = tts.Speaker.__new__(tts.Speaker)
    sp.voice, sp.rate = "kokoro:bm_george", "+0%"
    sp.kreyol_voice, sp.french_voice, sp.other_voice = "fr-FR-RemyMultilingualNeural", "fr-FR-RemyMultilingualNeural", "en-US-AndrewMultilingualNeural"

    def say_with(text, voice, rate=None):
        calls.append((voice, text, rate or sp.rate))
        return np.ones(3, dtype=np.int16)
    monkeypatch.setattr(sp, "_say_with", say_with)
    monkeypatch.setattr(tts, "kokoro_say", lambda text, voice, rate="+0%": calls.append((voice, text, rate)) or np.ones(3, dtype=np.int16))
    monkeypatch.setattr(tts, "local_voice_only", lambda: False)
    monkeypatch.setattr(tts, "_kokoro_cached", lambda voice: False)
    sp.calls = calls
    return sp


def test_japanese_and_chinese_are_no_longer_english():
    assert language.script("こんにちは、元気ですか？") == "ja" and language.script("你好，你今天好吗？") == "zh" and language.script("Bonjou") is None
    assert language.detect("こんにちは、元気ですか？") == "other" and language.detect("你好，你今天好吗？") == "other"
    assert language.detect("I'll try again.") == "en" and language.detect("Mwen bezwen ale nan mache a jodi.") == "ht"


def test_a_line_is_cut_at_its_marked_phrases():
    assert language.segments("Nothing marked here.") is None
    language.mark("你好", "zh", True)
    language.mark("(nǐ hǎo)", "", False)
    language.mark_line("New: \"hello\" is 你好 (nǐ hǎo). Say it after me.")
    assert language.segments("New: \"hello\" is 你好 (nǐ hǎo). Say it after me.") == [
        (None, "New: \"hello\" is", False), ("zh", "你好", True), (None, "Say it after me.", False)]
    assert language.segments("Something else entirely.") is None
    language.mark("Buenas noches", "es")
    language.mark("Buenas", "es")
    language.mark_line("Buenas noches means good night.")
    assert language.segments("Buenas noches means good night.")[0] == ("es", "Buenas noches", False)       # the longest phrase wins
    for n in range(language.MAX_MARKS + 50):
        language.mark(f"phrase {n}", "es")
    assert len(language._marks) == language.MAX_MARKS


def test_a_lesson_line_is_spoken_in_two_voices_and_the_phrase_slowly(speaker):
    language.mark("Hola", "es", True)
    language.mark_line("New: \"hello\" is Hola. Say it after me.")
    audio = speaker._synthesize("New: \"hello\" is Hola. Say it after me.")
    assert len(audio) == 9
    assert [(v, t, r) for v, t, r in speaker.calls] == [
        ("kokoro:bm_george", "New: \"hello\" is", "+0%"), ("es-MX-JorgeNeural", "Hola", tts.LESSON_SLOW), ("kokoro:bm_george", "Say it after me.", "+0%")]


def test_brackets_with_pinyin_are_not_read_and_chinese_gets_a_chinese_voice(speaker):
    language.mark("你好", "zh", False)
    language.mark("(nǐ hǎo)", "", False)
    language.mark_line("你好 (nǐ hǎo).")
    speaker._synthesize("你好 (nǐ hǎo).")
    assert speaker.calls == [("zh-CN-YunxiNeural", "你好", "+0%")]


def test_unmarked_japanese_still_gets_the_japanese_voice(speaker):
    speaker._synthesize("こんにちは。")
    assert speaker.calls == [("ja-JP-KeitaNeural", "こんにちは。", "+0%")]


def test_voice_order_per_language(monkeypatch):
    monkeypatch.setattr(tts, "_kokoro_cached", lambda voice: False)
    assert tts.lesson_voices("fr", "", "fr-FR-RemyMultilingualNeural") == ["kokoro:ff_siwis", "fr-FR-RemyMultilingualNeural"]      # local first
    assert tts.lesson_voices("es") == ["es-MX-JorgeNeural"]
    monkeypatch.setattr(tts, "_kokoro_cached", lambda voice: True)
    assert tts.lesson_voices("es") == ["kokoro:ef_dora", "es-MX-JorgeNeural"]    # only when the pack is already here
    assert tts.lesson_voices("ht", "mms") == ["mms"] and tts.lesson_voices("ht", "fr-FR-RemyMultilingualNeural") == ["fr-FR-RemyMultilingualNeural"]
    assert tts.lesson_voices("ja") == ["ja-JP-KeitaNeural"] and tts.lesson_voices("zh") == ["zh-CN-YunxiNeural"]


def test_a_failed_lesson_voice_falls_to_the_next_one(speaker, monkeypatch):
    def flaky(text, voice, rate=None):
        speaker.calls.append((voice, text, rate))
        if voice.startswith("kokoro:"):
            raise RuntimeError("gpu fault")
        return np.ones(2, dtype=np.int16)
    monkeypatch.setattr(speaker, "_say_with", flaky)
    assert len(speaker._lesson_say("Bonjour", "fr", True)) == 2
    assert [c[0] for c in speaker.calls] == ["kokoro:ff_siwis", "fr-FR-RemyMultilingualNeural"]


def test_creole_is_said_by_the_configured_voice_and_the_page_says_which(speaker, monkeypatch):
    language.mark("Mèsi", "ht", True)
    speaker._synthesize("Mèsi.")
    assert speaker.calls == [("fr-FR-RemyMultilingualNeural", "Mèsi", tts.LESSON_SLOW)]
    from core import config
    from types import SimpleNamespace
    monkeypatch.setattr(config, "Config", lambda: SimpleNamespace(kreyol_voice="fr-FR-RemyMultilingualNeural", french_voice=""))
    note = tts.lesson_voice_note("ht")
    assert note["local"] is False and "French" in note["note"]
    monkeypatch.setattr(config, "Config", lambda: SimpleNamespace(kreyol_voice="mms", french_voice=""))
    assert tts.lesson_voice_note("ht")["local"] is True


class FakeParakeet:
    def __init__(self):
        self.calls = 0

    def transcribe(self, samples):
        self.calls += 1
        return "come on a star"


def _transcriber(monkeypatch, extra):
    t = stt.Transcriber.__new__(stt.Transcriber)
    t.multilingual, t.forced_language, t.extra_language, t.last_language, t.fast = True, None, extra, "en", FakeParakeet()
    asked = []
    monkeypatch.setattr(t, "_probabilities", lambda samples: {"en": 0.2, "es": 0.7, "fr": 0.05, "ht": 0.05})
    monkeypatch.setattr(t, "_run", lambda samples, lang: asked.append(lang) or "¿Cómo estás?")
    return t, asked


def test_a_spanish_lesson_skips_the_english_french_first_pass(monkeypatch):
    t, asked = _transcriber(monkeypatch, "es")
    assert t._transcribe(np.zeros(1600, dtype=np.int16)) == "¿Cómo estás?"
    assert asked == ["es"] and t.fast.calls == 0 and t.last_language == "es"


def test_outside_a_lesson_the_first_pass_is_unchanged(monkeypatch):
    t, asked = _transcriber(monkeypatch, None)
    assert t._transcribe(np.zeros(1600, dtype=np.int16)) == "come on a star"
    assert asked == [] and t.fast.calls == 1


def test_a_marked_word_never_cuts_an_ordinary_reply():
    """Review finding: short course words ("de", "un", "en", "six") were found as plain substrings of any later reply
    ("Opening", "decision", "understand") and voiced in the lesson's language. Marks reach only the lesson's own lines
    (or the phrase alone, as the Languages page asks for it), and inside those only whole words."""
    language.mark("de", "ht")
    language.mark("un", "fr")
    language.mark("six", "fr")
    language.mark_line("New: \"two\" is de. Say it after me. The decision is yours: un or de.")
    for line in ("Opening Chrome and the decision is made.", "I understand, the news is in.", "You have six new emails.", "Pick un or de."):
        assert language.segments(line) is None, line                                # not a lesson line: spoken as always
    assert language.segments("New: \"two\" is de.") == [(None, "New: \"two\" is", False), ("ht", "de", False)]
    assert language.segments("The decision is yours: un or de.") == [                # a lesson sentence: whole words only
        (None, "The decision is yours:", False), ("fr", "un", False), (None, "or", False), ("ht", "de", False)]
    assert language.segments("de") == [("ht", "de", False)] and language.segments("six.") == [("fr", "six", False)]      # the page's Listen
    language.mark("你好", "zh")
    language.mark_line("\"hello\" is你好。")
    assert language.segments("\"hello\" is你好。")[1] == ("zh", "你好", False)        # characters need no space around them
    language.clear_marks()
    assert language.segments("New: \"two\" is de.") is None
