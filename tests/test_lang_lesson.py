"""The language teacher's skills through tools.registry, the intent router, and the Brain's language hook (no model
unless a fake is set, pinned clock, tmp data, no speaker and no recogniser)."""
from datetime import datetime, timedelta

import pytest

from audio import language
from core import lang_engine as engine
from core import langintent
from tools import langlesson, languages, registry

NOW = datetime(2026, 3, 2, 10, 0, 0)


class FakeSTT:
    extra_language = None
    forced_language = None


class FakeSpeaker:
    other_voice = languages.DEFAULT_OTHER_VOICE


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    def down(prompt):
        raise RuntimeError("no model in tests")
    state = {"now": NOW}
    monkeypatch.setattr(langlesson, "MODEL", down)
    monkeypatch.setattr(engine, "clock", lambda: state["now"])
    monkeypatch.setattr(languages, "transcriber", FakeSTT())
    monkeypatch.setattr(languages, "speaker", FakeSpeaker())
    monkeypatch.setattr(languages, "interpreter", None)
    monkeypatch.setattr(language, "lesson_hint", None)
    for box in (langlesson._sessions, langlesson._last_lang):
        box.clear()
    language.clear_marks()
    yield state
    for box in (langlesson._sessions, langlesson._last_lang):
        box.clear()
    language.clear_marks()


def run(name, **args):
    out, is_error = registry.run(name, args)
    assert not is_error, out
    return out


def current():
    s = langlesson.session()
    st = engine.step(s)
    return s, st, s["items"][st["item"]]


def test_every_language_skill_is_registered_and_the_tutor_skills_still_are():
    for name in ("language_lesson", "language_answer", "language_repeat", "language_skip", "language_stop", "language_say",
                 "language_meaning", "language_progress", "tutor_status", "tutor_review", "tutor_grade", "tutor_add_words", "tutor_log_lesson"):
        assert name in registry.names(), name


# ---- the intent router --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said,lang,minutes,zero", [
    ("let's practice Spanish for 20 minutes", "Spanish", 20, False),
    ("Let's practise Spanish for twenty minutes.", "Spanish", 20, False),
    ("Teach me Japanese like I've never studied it before", "Japanese", 10, True),
    ("teach me Japanese from zero", "Japanese", 10, True),
    ("Jarvis, can you teach me Haitian Creole?", "Haitian Creole", 10, False),
    ("I want to learn Kreyol", "Haitian Creole", 10, False),
    ("let's do a French lesson", "French", 10, False),
    ("give me a 15 minute Mandarin lesson", "Mandarin Chinese", 15, False),
    ("continue my Chinese lesson", "Mandarin Chinese", 10, False),
    ("let's study some French for half an hour", "French", 30, False),
    ("[x] start a Spanish lesson please".replace("[x] ", ""), "Spanish", 10, False),
    ("teach me Spanish as a complete beginner", "Spanish", 10, True),
])
def test_lesson_requests(said, lang, minutes, zero):
    assert langintent.call_for(said) == ("language_lesson", {"language": lang, "minutes": minutes, "from_zero": zero})


@pytest.mark.parametrize("said", [
    "is Spanish hard to learn?", "play some Spanish music", "teach me Spanish cooking", "teach me Japanese history",
    "translate this into French", "only speak Kreyol with me", "what's the weather in French Guiana", "open the Japanese restaurant menu",
    "I studied Spanish in school", "my wife speaks French", "what's the capital of Japan", "what does it mean", "what does that error mean",
    "start interpreting between English and Spanish", "let's practice", "practice Spanish guitar chords", "what is life in Spanish Harlem like",
    "how do you say that again", "repeat that", "slower", "skip", "gracias", "next",
])
def test_ordinary_talk_is_not_a_language_request(said):
    assert langintent.call_for(said) is None, said


def test_questions_about_a_language_with_no_lesson_open():
    assert langintent.call_for("how do you say water in Spanish?") == ("language_say", {"phrase": "water", "language": "Spanish"})
    assert langintent.call_for("what's the Japanese word for friend") == ("language_say", {"phrase": "friend", "language": "Japanese"})
    assert langintent.call_for("what does mèsi mean in Creole") == ("language_meaning", {"phrase": "mèsi", "language": "Haitian Creole"})
    assert langintent.call_for("what's my progress in Spanish") == ("language_progress", {"language": "Spanish"})
    assert langintent.call_for("how is my Japanese going?") == ("language_progress", {"language": "Japanese"})
    assert langintent.call_for("how do you say water") is None                  # no lesson, no language: not ours


def lesson_of(kind="say", lang="es", key="thanks"):
    from core import lang_curriculum as course
    item = course.items(lang)[f"{lang}:{key}"]
    check = (lambda said: engine.check_meaning(item, said)) if kind == "meaning" else (lambda said: engine.check_target(item, said))
    return {"lang": lang, "kind": kind, "check": None if kind == "talk" else check, "fresh": True, "prompted": True,
            "guess": lambda said: engine.is_meaning(lang, said)}


@pytest.mark.parametrize("said,call", [
    ("repeat that", ("language_repeat", {"slower": False})), ("say it again please", ("language_repeat", {"slower": False})),
    ("slower", ("language_repeat", {"slower": True})), ("can you say that more slowly", ("language_repeat", {"slower": True})),
    ("too fast", ("language_repeat", {"slower": True})), ("skip", ("language_skip", {})), ("next one", ("language_skip", {})),
    ("end the lesson", ("language_stop", {})), ("let's stop here", ("language_stop", {})), ("that's enough for today", ("language_stop", {})),
    ("I'm done", ("language_stop", {})), ("stop the Spanish lesson", ("language_stop", {})),
    ("how do you say water", ("language_say", {"phrase": "water", "language": ""})),
    ("what does buenas noches mean?", ("language_meaning", {"phrase": "buenas noches", "language": ""})),
    ("my progress", ("language_progress", {"language": ""})),
    ("I don't know", ("language_answer", {"answer": "I don't know"})),
    ("gracias", ("language_answer", {"answer": "gracias"})), ("grasias", ("language_answer", {"answer": "grasias"})),
    ("no se", ("language_answer", {"answer": "no se"})),                       # a wrong try in the language is still his answer
    ("muchas cosas bonitas", ("language_answer", {"answer": "muchas cosas bonitas"})),
    ("teach me Japanese from zero", ("language_lesson", {"language": "Japanese", "minutes": 10, "from_zero": True})),
])
def test_lesson_talk_and_answers_while_a_lesson_is_open(said, call):
    assert langintent.call_for(said, lesson_of()) == call


@pytest.mark.parametrize("said", ["skip", "next", "next one", "pass", "skip it", "move on", "that's all", "I'm done", "let's stop", "that's enough"])
def test_bare_skip_and_stop_are_lesson_talk_only_right_after_a_lesson_line(said):
    """Review finding: with a lesson open, "next" meant for the music skipped a lesson step and "that's all" after
    any other command ended the lesson. Words that do not name the lesson are taken only as the reply to its line."""
    away = lesson_of() | {"prompted": False}
    assert langintent.call_for(said, away, lambda line: True) is None, said
    lesson = lesson_of()
    del lesson["prompted"]                                                         # unsure is no
    assert langintent.call_for(said, lesson, lambda line: True) is None, said
    assert langintent.call_for(said, lesson_of(), lambda line: True)[0] in ("language_skip", "language_stop")


@pytest.mark.parametrize("said,name", [
    ("skip this word", "language_skip"), ("next question", "language_skip"), ("skip that phrase please", "language_skip"),
    ("end the lesson", "language_stop"), ("stop the Spanish lesson", "language_stop"), ("I'm done with the lesson", "language_stop"),
    ("that's enough practice for today", "language_stop"), ("stop teaching me", "language_stop"),
])
def test_skip_and_stop_that_name_the_lesson_are_always_taken(said, name):
    assert langintent.call_for(said, lesson_of() | {"prompted": False}, lambda line: True) == (name, {})


@pytest.mark.parametrize("said", [
    "what time is it", "turn the volume down", "pause the music", "what's the weather tomorrow", "remind me to call the dentist at five",
    "ok", "yes", "no", "I think the router is acting up again", "stop", "is it going to rain today", "open my email",
])
def test_an_open_lesson_leaves_ordinary_talk_alone(said):
    def orders(line):
        return line in ("stop", "ok", "yes")
    for kind in ("say", "meaning", "talk"):
        assert langintent.call_for(said, lesson_of(kind, key="please"), orders) is None, (kind, said)
    for line in ("hello", "thanks", "good night"):                                 # greetings stay greetings unless a meaning was asked
        assert langintent.call_for(line, lesson_of("say"), orders) is None and langintent.call_for(line, lesson_of("talk"), orders) is None


def test_a_meaning_question_takes_guesses_from_the_course_even_when_they_read_like_greetings():
    table = lesson_of("meaning")
    assert langintent.call_for("thank you", table) == ("language_answer", {"answer": "thank you"})
    assert langintent.call_for("good night", table) == ("language_answer", {"answer": "good night"})       # wrong, but a guess
    assert langintent.call_for("something about food", table) == ("language_answer", {"answer": "something about food"})
    assert langintent.call_for("こんにちは", lesson_of("say", "ja", "hello")) == ("language_answer", {"answer": "こんにちは"})


# ---- the skills ---------------------------------------------------------------------------------------------------
def test_a_spoken_lesson_from_start_to_recap(quiet):
    said = run("language_lesson", language="Spanish", minutes=5)
    assert "Spanish from the very beginning, 5 minutes" in said and "Hola." in said and "Say it after me" in said
    for _ in range(40):
        s, st, item = current()
        said = run("language_answer", answer=item["english"] if st["kind"] == "meaning" else item["text"].replace("...", "Sam"))
        if langlesson.session() is None:
            break
    assert "That's the lesson" in said and "of" in said and langlesson.session() is None
    assert "Spanish: beginner, unit 1 of 8" in run("language_progress", language="Spanish")
    assert "No lesson is running" in run("language_answer", answer="hola")


def test_repeat_slower_skip_and_stop():
    run("language_lesson", language="Japanese", minutes=10, from_zero=True)
    again = run("language_repeat", slower=True)
    assert "こんにちは (konnichiwa)" in again
    assert language._marks["こんにちは"] == ("ja", True) and language._marks["(konnichiwa)"] == ("", False)
    assert "Skipped." in run("language_skip")
    out = run("language_stop")
    assert "That's the lesson" in out and langlesson.session() is None
    assert run("language_stop") == "No lesson is running."


def test_how_do_you_say_and_what_does_it_mean():
    assert run("language_say", phrase="thank you", language="Haitian Creole") == "\"thank you\" in Haitian Creole is Mèsi."
    assert "你好吗？ (nǐ hǎo ma)" in run("language_say", phrase="how are you?", language="Mandarin")
    assert run("language_meaning", phrase="buenas noches", language="Spanish") == "Buenas noches means \"good night\"."
    assert "did not give me a clean answer" in run("language_say", phrase="shoe", language="Spanish")       # model down: said honestly
    assert "Which language?" in run("language_say", phrase="shoe", language="Klingon")


def test_a_phrase_outside_the_course_comes_from_the_local_model_and_is_kept(monkeypatch):
    monkeypatch.setattr(langlesson, "MODEL", lambda prompt: '{"text": "el zapato", "english": "the shoe"}')
    out = run("language_say", phrase="shoe", language="Spanish")
    assert "el zapato" in out and "not the checked course" in out
    assert [w["text"] for w in engine.profile("es")["words"].values()] == ["el zapato"]
    assert language._marks["el zapato"] == ("es", True)


def test_progress_before_and_after():
    assert "not started French yet" in run("language_progress", language="French")
    run("language_lesson", language="French", minutes=10)
    run("language_answer", answer="zzz zzz")
    run("language_answer", answer="zzz zzz")
    run("language_stop")
    out = run("language_progress")                                               # no language named: his last one
    assert out.startswith("French: beginner, unit 1 of 8") and "To practise: Bonjour (hello)" in out


def test_one_lesson_per_person_and_the_page_has_its_own():
    run("language_lesson", language="Spanish")
    with langlesson.on_page("abc"):
        assert langlesson.session() is None and langlesson.on_the_table() is None
        langlesson.lesson_start("French", 5)
        assert langlesson.session()["lang"] == "fr" and langlesson.on_the_table() is None      # a page lesson is never "on the table" for voice
    assert langlesson.session()["lang"] == "es"
    assert langlesson.overview()["lesson"]["lang"] == "es"


def test_a_lesson_he_walked_away_from_ends_and_still_counts(monkeypatch):
    run("language_lesson", language="Spanish")
    run("language_answer", answer="hola")
    langlesson._sessions[("owner", "talk")]["at"] -= langlesson.IDLE + 1
    assert langlesson.on_the_table() is None
    assert engine.profile("es")["lessons"] == 1 and languages.transcriber.extra_language is None


# ---- speech set-up ------------------------------------------------------------------------------------------------
def test_a_voice_lesson_tells_the_recogniser_and_the_speaker_the_language_and_hands_them_back():
    run("language_lesson", language="Spanish")
    assert languages.transcriber.extra_language == "es" and language.lesson_hint == "es"
    assert languages.speaker.other_voice == languages.NATIVE_VOICES["es"]
    assert language._marks["Hola"] == ("es", True)                               # a beginner hears it slowly
    run("language_stop")
    assert languages.transcriber.extra_language is None and language.lesson_hint is None and not language._marks
    assert languages.speaker.other_voice == languages.DEFAULT_OTHER_VOICE


def test_a_lesson_never_touches_an_interpreter_session():
    languages.start_interpreter("English", "Spanish")
    run("language_lesson", language="Japanese")
    assert languages.transcriber.extra_language == "es"
    run("language_stop")
    assert languages.transcriber.extra_language == "es" and languages.interpreter is not None


def test_a_page_lesson_leaves_speech_alone():
    with langlesson.on_page("p1"):
        langlesson.lesson_start("Japanese", 5)
        langlesson.lesson_stop()
    assert languages.transcriber.extra_language is None


def test_the_old_tutor_keeps_one_record_per_language_and_spares_the_interpreter():
    from tools import tutor
    tutor.tutor_set_profile("Kreyol", "beginner", "family")
    assert "level beginner" in tutor.tutor_status("Haitian Creole") and "level beginner" in tutor.tutor_status("creole")
    languages.start_interpreter("English", "Spanish")
    tutor.tutor_log_lesson("French", "greetings")
    assert languages.transcriber.extra_language == "es"


# ---- the Brain ----------------------------------------------------------------------------------------------------
@pytest.fixture
def brain():
    from brain import llm

    b = llm.Brain.__new__(llm.Brain)
    b.uid, b.messages = "owner", []
    return b


def test_the_brain_runs_a_lesson_in_code_and_keeps_listening_to_everything_else(brain):
    said = []
    assert brain._language_request("[Sent from their phone] let's practice Spanish for 20 minutes", said.append) is True
    assert any("Hola." in line for line in said) and langlesson.session()["minutes"] == 20
    assert brain.messages[-1]["role"] == "assistant" and "Hola" in brain.messages[-1]["content"]
    assert brain._language_request("hola", said.append) is True and langlesson.session()["i"] == 1
    at = langlesson.session()["i"]
    for line in ("what time is it", "turn the lights off", "thanks", "pause the music", "what's on my calendar tomorrow"):
        assert brain._language_request(line, said.append) is False, line
    assert langlesson.session()["i"] == at and engine.profile("es")["items"].keys() == {"es:hello"}       # nothing recorded
    assert brain._language_request("next", said.append) is False and langlesson.session()["i"] == at      # after other talk "next" is not the lesson's
    assert brain._language_request("that's all", said.append) is False and langlesson.session() is not None
    assert brain._language_request("slower", said.append) is True
    at = langlesson.session()["i"]
    assert brain._language_request("next", said.append) is True and langlesson.session()["i"] == at + 1    # right after its own line it is
    assert brain._language_request("end the lesson", said.append) is True and langlesson.session() is None
    assert brain._language_request("hola", said.append) is False                 # no lesson: not ours
    assert brain._language_request("what's the weather", said.append) is False


def test_a_page_lesson_does_not_make_voice_deaf(brain):
    with langlesson.on_page("p2"):
        langlesson.lesson_start("Spanish", 5)
    for line in ("hola", "gracias", "skip", "repeat that"):
        assert brain._language_request(line, [].append) is False, line
