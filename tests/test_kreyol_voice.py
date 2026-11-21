"""He speaks Haitian Creole with a real Creole voice, and the conversation follows the language its owner speaks.

Nothing real runs here: the Creole voice model is a stand-in (conftest turns the real one off), the recogniser's
probabilities are given, the local model is a recording transport, and the clock is pinned."""
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from audio import kreyol, language, stt, tts
from core import chatturn, intelligence, quick, warmth
from tools import registry

AFTERNOON = datetime(2026, 11, 12, 15, 20)        # a Thursday


# -- Creole text for the voice ---------------------------------------------------------------------------------------
def test_numbers_in_creole_words():
    said = {0: "zewo", 1: "en", 21: "venteyen", 22: "vennde", 28: "ventuit", 45: "karannsenk", 70: "swasanndis",
            75: "swasannkenz", 80: "katreven", 99: "katrevendiznèf", 100: "san", 125: "san vennsenk", 2026: "de mil vennsis"}
    assert {n: kreyol.number(n) for n in said} == said


def test_clock_and_date_in_creole():
    assert kreyol.clock(15, 20) == "twazè ven nan apremidi" and kreyol.clock(12, 0) == "midi"
    assert kreyol.clock(9, 30) == "nevè edmi nan maten" and kreyol.date(AFTERNOON) == "jedi douz novanm"


def test_the_voice_is_given_letters_only():
    """Its alphabet has no digits or signs: they were silently dropped ("3 minit" was said "minit")."""
    assert kreyol.prepare("Mwen gen 25% batri.") == "mwen gen vennsenk pousan batri"
    assert kreyol.prepare("Sa koute $12, mesye!") == "sa koute douz dola mesye"
    assert kreyol.prepare("Li 3:30.") == "li twazè edmi"
    assert kreyol.prepare("**Dakò** [isit](https://example.com)") == "dakò isit"
    assert [pause > 0 for _, pause in kreyol.pieces("Mwen byen, mèsi anpil. E ou menm?")] == [True, True, True]


# -- which language is a line in? ------------------------------------------------------------------------------------
@pytest.mark.parametrize("line, code", [
    ("Kijan ou ye?", "ht"), ("Mwen bezwen ale nan mache a jodi a.", "ht"), ("Mete yon revèy pou 5 minit", "ht"),
    ("Bonjour, comment allez-vous aujourd'hui ?", "fr"), ("Je veux tout savoir, ou presque.", "fr"),
    ("Hola, ¿cómo estás?", "es"), ("Qué hora es", "es"), ("What time is it?", "en"), ("Close Discord please", "en")])
def test_a_whole_sentence_is_clear(line, code):
    assert language.identify(line) == (code, 0.9)


@pytest.mark.parametrize("line", ["Wi.", "non", "ok", "Blender.", "Ou la?", "gracias", "Thanks"])
def test_a_short_line_is_never_clear(line):
    assert language.identify(line)[1] < language.CLEAR


def test_one_unclear_line_never_flips_the_conversation_and_two_do():
    assert language.observe("a", "gracias") == "en"                  # one telling word: pending, not flipped
    assert language.observe("a", "Blender.") == "en"                 # says nothing: the pending one is kept
    assert language.observe("a", "Buenos dias") == "es"              # the second line pointing the same way
    assert language.observe("a", "ok") == "es" and language.observe("a", "What time is it?") == "en"


def test_one_clear_sentence_flips_at_once_and_each_conversation_has_its_own():
    assert language.observe("voice", "Kijan ou ye jodi a?") == "ht" and language.current("typed") == "en"
    assert language.observe("voice", "Wi.") == "ht" and language.observe("voice", "Blender.") == "ht"
    assert language.observe("voice", "Can you open the calendar for me?") == "en"


def test_the_ear_decides_a_line_the_words_cannot():
    language.heard("Ou la?", "ht", 0.92)
    assert language.observe("voice", "Ou la?") == "ht" and language.spoken_language() == "ht"
    language.heard("Blender", "fr", 0.45)                            # a short line's coin toss: unclear
    assert language.observe("voice", "Blender.") == "ht"
    language.heard("something else entirely", "es", 0.95)            # evidence for another line is not used
    assert language.observe("voice", "Dakò.") == "ht"


def test_a_clear_sentence_outweighs_the_ears_guess_and_an_unused_language_is_ignored():
    language.heard("What time is it", "fr", 0.9)
    assert language.observe("voice", "What time is it?") == "en"
    language.heard("obrigado", "pt", 0.99)
    assert language.observe("voice", "obrigado") == "en"


def test_the_reply_note_says_the_language_and_an_english_conversation_has_none():
    assert language.reply_note("k") == ""
    language.observe("k", "Kijan ou ye jodi a?")
    note = language.reply_note("k")
    assert "Haitian Creole" in note and "Kijan ou ye?" in note and note.startswith("(") and note.endswith(")")
    language.observe("k", "Bonjour, comment allez-vous aujourd'hui ?")
    assert "French" in language.reply_note("k") and "Kreyòl" not in language.reply_note("k")
    language.observe("k", "Okay, what is on my calendar today?")
    assert "English again" in language.reply_note("k")


def test_a_short_sentence_is_said_in_the_voice_of_the_reply():
    assert language.voice_language("Hola, ¿cómo estás hoy?") == "es" and language.voice_language("Nothing looks wrong.") == "en"
    language.speaking = "ht"
    assert language.voice_language("Dakò.") == "ht" and language.voice_language("I will open the calendar now.") == "en"


# -- the Creole voice ------------------------------------------------------------------------------------------------
class FakeCreole:
    def __init__(self, fail=False):
        self.said, self.fail = [], fail

    def synthesize(self, text, rate="+0%"):
        self.said.append((text, rate))
        if self.fail:
            raise RuntimeError("no model")
        return np.full(5, 7, dtype=np.int16)


@pytest.fixture
def speaker(monkeypatch):
    calls = []
    sp = tts.Speaker.__new__(tts.Speaker)
    sp.voice, sp.rate = "kokoro:bm_george", "+0%"
    sp.kreyol_voice, sp.french_voice, sp.other_voice = "fr-FR-RemyMultilingualNeural", "fr-FR-RemyMultilingualNeural", "en-US-AndrewMultilingualNeural"
    monkeypatch.setattr(sp, "_say_with", lambda text, voice, rate=None: calls.append((voice, text)) or np.ones(3, dtype=np.int16))
    monkeypatch.setattr(tts, "kokoro_say", lambda text, voice, rate="+0%": calls.append((voice, text)) or np.ones(3, dtype=np.int16))
    monkeypatch.setattr(tts, "local_voice_only", lambda: False)
    monkeypatch.setattr(tts, "_kokoro_cached", lambda voice: False)
    sp.calls = calls
    return sp


def test_creole_is_spoken_by_the_local_creole_voice_when_the_model_is_here(speaker, monkeypatch):
    fake = FakeCreole()
    monkeypatch.setattr(tts, "_creole_ready", True)
    monkeypatch.setattr(tts, "_creole_voice", lambda download=False: fake)
    audio = speaker._synthesize("Mwen byen, mèsi anpil.")
    assert list(audio) == [7] * 5 and fake.said == [("Mwen byen, mèsi anpil.", "+0%")] and speaker.calls == []
    assert tts.lesson_voice_note("ht")["local"] is True


def test_without_the_model_creole_keeps_the_voice_it_had(speaker, monkeypatch):
    monkeypatch.setattr(tts, "_creole_ready", False)
    monkeypatch.setattr(tts, "_creole_voice", lambda download=False: pytest.fail("the model is not on this machine"))
    assert len(speaker._synthesize("Mwen byen, mèsi anpil.")) == 3
    assert speaker.calls == [("fr-FR-RemyMultilingualNeural", "Mwen byen, mèsi anpil.")]


def test_a_creole_voice_that_fails_falls_back_cleanly(speaker, monkeypatch):
    monkeypatch.setattr(tts, "_creole_ready", True)
    monkeypatch.setattr(tts, "_creole_voice", lambda download=False: FakeCreole(fail=True))
    assert len(speaker._synthesize("Mwen byen, mèsi anpil.")) == 3
    assert speaker.calls == [("fr-FR-RemyMultilingualNeural", "Mwen byen, mèsi anpil.")]


def test_a_model_that_would_not_load_is_not_retried_for_every_sentence(monkeypatch):
    loads = []

    def broken(download=False):
        loads.append(download)
        raise OSError("not in the cache")
    monkeypatch.setattr(tts, "_CreoleVoice", broken)
    monkeypatch.setattr(tts, "_creole", None)
    monkeypatch.setattr(tts, "_creole_down_until", 0.0)
    monkeypatch.setattr(tts, "_creole_ready", True)
    monkeypatch.setattr(tts.time, "monotonic", lambda: 1000.0)
    assert tts.creole_say("Bonjou") is None and tts.creole_say("Bonswa") is None and loads == [False]


def test_the_tests_never_see_the_real_model():
    assert tts.creole_ready() is False and tts.creole_say("Bonjou") is None


def test_spanish_and_french_sentences_get_their_own_voices(speaker):
    speaker._synthesize("Hola, ¿cómo estás hoy?")
    speaker._synthesize("Bonjour, comment allez-vous aujourd'hui ?")
    assert [voice for voice, _ in speaker.calls] == ["es-MX-JorgeNeural", "fr-FR-RemyMultilingualNeural"]


# -- the recogniser --------------------------------------------------------------------------------------------------
def _transcriber(monkeypatch, probs, wrote):
    t = stt.Transcriber.__new__(stt.Transcriber)
    t.multilingual, t.forced_language, t.extra_language, t.last_language, t.fast = True, None, None, "en", None
    asked = []
    monkeypatch.setattr(t, "_probabilities", lambda samples: probs)
    monkeypatch.setattr(t, "_run", lambda samples, lang: asked.append(lang) or wrote.get(lang, ""))
    return t, asked


LONG = np.zeros(3 * 16000, dtype=np.float32)


def test_whispers_guess_is_kept_to_the_languages_he_uses(monkeypatch):
    """Creole went to Portuguese or Malay now and then: only en, ht, fr and es are candidates."""
    t, asked = _transcriber(monkeypatch, {"pt": 0.5, "ms": 0.2, "ht": 0.2, "fr": 0.05, "en": 0.05},
                            {"ht": "Mwen bezwen ale nan mache a", "fr": "Moi besoin aller"})
    assert t._transcribe(LONG) == "Mwen bezwen ale nan mache a" and "pt" not in asked and t.last_language == "ht"
    assert language.observe("voice", "Mwen bezwen ale nan mache a") == "ht"


def test_spanish_is_heard_outside_a_lesson(monkeypatch):
    t, asked = _transcriber(monkeypatch, {"es": 0.8, "en": 0.1, "fr": 0.05, "ht": 0.05}, {"es": "Qué hora es"})
    assert t._transcribe(LONG) == "Qué hora es" and asked == ["es"]


def test_the_next_turn_leans_to_the_conversations_language(monkeypatch):
    probs = {"en": 0.45, "ht": 0.25, "fr": 0.2, "es": 0.1}
    t, asked = _transcriber(monkeypatch, probs, {"en": "we", "ht": "Wi", "fr": "Oui"})
    assert t._transcribe(LONG) == "we"                                # an English conversation: English
    language.heard("Kijan ou ye jodi a", "ht", 0.9)
    language.observe("voice", "Kijan ou ye jodi a?")
    t2, asked2 = _transcriber(monkeypatch, probs, {"en": "we", "ht": "Wi", "fr": "Oui"})
    assert t2._transcribe(LONG) == "Wi" and "en" not in asked2       # the same sound in a Creole conversation


def test_a_very_short_line_is_only_weak_evidence(monkeypatch):
    t, _ = _transcriber(monkeypatch, {"es": 0.9, "en": 0.1}, {"es": "si"})
    t._transcribe(np.zeros(8000, dtype=np.float32))
    assert t.last_confidence <= 0.5 and language.observe("voice", "si") == "en"


# -- quick answers in his language -----------------------------------------------------------------------------------
@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(quick, "datetime", SimpleNamespace(now=lambda: AFTERNOON))


@pytest.mark.parametrize("asked, answer", [
    ("Ki lè li ye?", "Li twazè ven nan apremidi."), ("Eske ki lè li ye kounye a?", "Li twazè ven nan apremidi."),
    ("Quelle heure est-il ?", "Il est 15 h 20."), ("¿Qué hora es?", "Son las 3:20 de la tarde."),
    ("Ki dat nou ye jodi a?", "Jodi a se jedi douz novanm."), ("Quel jour sommes-nous ?", "Nous sommes le jeudi 12 novembre."),
    ("¿Qué día es hoy?", "Hoy es jueves 12 de noviembre.")])
def test_time_and_date_are_answered_in_the_language_asked(clock, asked, answer):
    assert quick.handle(asked) == answer


def test_english_time_is_unchanged(clock):
    assert quick.handle("What time is it?") == "It's 3:20 PM."


@pytest.mark.parametrize("asked, seconds, answer", [
    ("Mete yon revèy pou 5 minit", 300, "Dakò, revèy la mete pou 5 minit. M ap di ou lè li fini."),
    ("Mets un minuteur de dix minutes", 600, "C'est noté : minuteur de 10 minutes. Je vous préviens à la fin."),
    ("Pon un temporizador de 1 minuto", 60, "Listo: temporizador de 1 minuto. Le aviso cuando termine.")])
def test_a_timer_is_set_and_confirmed_in_his_language(monkeypatch, asked, seconds, answer):
    from core import pcbridge
    from tools import system
    made = []
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(system, "set_timer", lambda s, label: made.append(s) or "ok")
    assert quick.handle(asked) == answer and made == [seconds]


# -- the model is told which language to answer in -------------------------------------------------------------------
@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_LOCAL_VISION_MODEL",
                "JARVIS_CHAT_FIRST", "JARVIS_CHAT_THINK", "JARVIS_PC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    monkeypatch.setattr(chatturn, "_now", lambda: AFTERNOON)
    sent = []

    def complete_local(body, cancelled=lambda: False, on_text=None):
        sent.append(body)
        return {"model": "qwen3.5:9b", "choices": [{"finish_reason": "stop", "message": {"content": "Mwen byen, mèsi."}}]}
    monkeypatch.setattr(intelligence, "complete_local", complete_local)
    return sent


def _text(message):
    content = message["content"]
    return content if isinstance(content, str) else " ".join(p.get("text", "") for p in content)


def test_the_language_note_rides_on_his_newest_message_and_the_prefix_is_the_same_bytes(local):
    history = [{"role": "user", "content": "Kijan ou ye jodi a?"}]
    language.observe("k", "Kijan ou ye jodi a?")
    intelligence.request("fast", "MANUAL " * 50, history, registry.schemas(), chat_system="PERSONA")
    plain = local[-1]["messages"]
    intelligence.request("fast", "MANUAL " * 50, history, registry.schemas(), chat_system="PERSONA",
                         language=language.reply_note("k"))
    noted = local[-1]["messages"]
    assert "Answer in Haitian Creole only" in _text(noted[-1]) and "Haitian Creole" not in _text(plain[-1])
    assert noted[:-1] == plain[:-1] and _text(noted[-1]).startswith(_text(plain[-1]))     # only a tail was added
    assert history == [{"role": "user", "content": "Kijan ou ye jodi a?"}]              # never in the history


def test_the_brain_follows_the_language_he_speaks():
    from brain import llm
    brain = llm.Brain.__new__(llm.Brain)
    brain.uid, brain.live, brain._turn_channel = "owner", True, SimpleNamespace()
    brain._hear_language("Kijan ou ye jodi a?")
    assert brain._tongue == "ht" and "Haitian Creole" in brain._language_note and language.speaking == "ht"
    brain._hear_language("Wi.")
    assert brain._tongue == "ht"
    brain._hear_language("[Spoken into the microphone] Thanks, what is on my calendar today?")
    assert brain._tongue == "en" and "English again" in brain._language_note and language.speaking is None
    typed = llm.Brain.__new__(llm.Brain)
    typed.uid, typed.live, typed._turn_channel = "owner", False, SimpleNamespace()
    typed._hear_language("Hello there, how are you?")
    assert typed._tongue == "en" and typed._language_note == ""           # an all-English conversation: nothing added


def test_a_lesson_keeps_its_own_rules(monkeypatch):
    from brain import llm
    monkeypatch.setattr(language, "lesson_hint", "ht")
    brain = llm.Brain.__new__(llm.Brain)
    brain.uid, brain.live, brain._turn_channel = "owner", True, SimpleNamespace()
    brain._hear_language("Mwen byen, mèsi anpil.")
    assert brain._language_note == "" and language.current("owner:voice") == "en"


# -- review 2026-11-12 -----------------------------------------------------------------------------------------------
def test_the_answer_round_after_a_tool_is_told_the_language_too(local):
    asked = {"role": "user", "content": "Kijan move tan an ye jodi a?"}
    language.observe("k", asked["content"])
    note = language.reply_note("k")
    intelligence.request("fast", "MANUAL " * 50, [asked], registry.schemas(), chat_system="PERSONA", language=note)
    first = local[-1]["messages"]
    history = [asked,
               {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_weather", "input": {}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                             "content": "Mwen byen, mèsi."}]}]     # the fake model's words, so the reply is about it
    kept = [dict(turn) for turn in history]
    intelligence.request("fast", "MANUAL " * 50, history, registry.schemas(), chat_system="PERSONA", language=note)
    answer = local[-1]["messages"]
    assert answer[-1]["role"] == "tool"                                    # the round that words the tool's result
    his = [m for m in answer if m["role"] == "user"]
    assert len(his) == 1 and "Answer in Haitian Creole only" in _text(his[0])
    assert _text(his[0]).endswith(note) and _text(first[-1]).endswith(note)   # worded the same in both rounds
    assert history == kept                                                 # never in the history


def test_an_older_message_of_his_is_never_given_the_note(local):
    history = [{"role": "user", "content": "Kijan ou ye jodi a?"},
               {"role": "assistant", "content": "Mwen byen, mèsi."}]       # nothing of his is waiting for an answer
    language.observe("k", history[0]["content"])
    intelligence.request("fast", "MANUAL " * 50, history, registry.schemas(), chat_system="PERSONA",
                         language=language.reply_note("k"))
    assert not any("Haitian Creole" in _text(m) for m in local[-1]["messages"] if m["role"] != "system")


@pytest.mark.parametrize("said, seconds", [
    ("mete yon revèy pou trant senk minit", 2100),
    ("pon un temporizador de treinta y cinco minutos", 2100),
    ("mets un minuteur de vingt-cinq minutes", 1500),
    ("mete yon revèy pou yon èdtan edmi", 5400),
    ("mets un minuteur d'une heure et demie", 5400),
    ("pon un temporizador de una hora y media", 5400),
    ("mete yon revèy pou yon èdtan trant minit", 5400),
    ("pon un temporizador en cinco minutos", 300),
    ("mete yon revèy pou 90 segonn", 90)])
def test_a_compound_number_is_read_whole(said, seconds):
    assert quick._duration_in(said) == seconds


@pytest.mark.parametrize("said", [
    "mete yon revèy pou san ven minit",                # 120: "san" is not a number he knows, so 20 would be a guess
    "pon un temporizador de ciento veinte minutos",
    "mets un minuteur de cent vingt minutes",
    "mete yon revèy pou trannsenk minit",              # a spelling outside the table
    "pon un temporizador de veinticinco minutos",
    "mete yon revèy pou yon èdtan ak kèk minit",       # one part understood, one not: no timer at all
    "mete yon revèy pou de mil minit"])
def test_a_number_he_is_not_sure_of_sets_no_timer(said):
    assert quick._duration_in(said) == 0
