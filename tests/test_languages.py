"""Offline tests for language detection, interpreter/lock controls and the tutor."""
import json

import pytest


@pytest.mark.parametrize("text, lang", [
    ("Bonjou mesye, kijan ou ye jodi a?", "ht"), ("Kle a sou tab la.", "ht"), ("Mèsi anpil.", "ht"),
    ("Bonjour monsieur, comment allez-vous aujourd'hui ?", "fr"), ("Je voudrais acheter un t-shirt.", "fr"),
    ("Non merci.", "fr"), ("Good evening, sir.", "en"), ("I bought a car.", "en"), ("Open Brave.", "en"),
    ("Hola, ¿cómo estás? Me llamo Juan.", "other"),
])
def test_detect(text, lang):
    from audio.language import detect

    assert detect(text) == lang


@pytest.mark.parametrize("sentence, said", [
    ("The capital of France is Paris, monsieur.", "The capital of France is Paris, sir."),   # Gemini, in English
    ("Good evening, mesye. How can I help?", "Good evening, sir. How can I help?"),
    ("De rien, mesye.", "De rien, monsieur."),                                                # the 8B, in French
    ("Bonsoir, sir.", "Bonsoir, monsieur."),
    ("Monsieur, it's five o'clock.", "Sir, it's five o'clock."),
    ("Mwen byen wi, mesye!", "Mwen byen wi, mesye!"),                                         # Kreyòl stays
    ("Je vais bien, merci, monsieur.", "Je vais bien, merci, monsieur."),
    ("Sir Elton John plays tonight.", "Sir Elton John plays tonight."),                       # not calling them
    ("I’m fine, mesye.", "I’m fine, sir."),                                              # a curly apostrophe (live 2026-11-15, the laptop)
    ("I’m doing well, mesye. How can I help?", "I’m doing well, sir. How can I help?"),
    ("Paris, the capital of France.", "Paris, the capital of France."),
    ("Paris, mesye.", "Paris, mesye."),                                                       # can't tell: as is
    ("Bonsoir, monsieur.", "Bonsoir, monsieur."),
])
def test_a_title_is_the_one_of_its_sentences_language(sentence, said):
    from core import intelligence

    assert intelligence.in_their_language(sentence) == said


def test_a_whole_reply_gets_its_titles_fixed_sentence_by_sentence():
    from core import intelligence

    reply = {"choices": [{"finish_reason": "stop", "message": {
        "content": "Good evening, mesye. Bonsoir, sir.\nI'm well, monsieur."}}]}
    out = intelligence.parse_reply(reply, set(), said="Good evening Jarvis")
    assert out.content[0].text == "Good evening, sir. Bonsoir, monsieur.\nI'm well, sir."


class FakeSTT:
    forced_language = None
    extra_language = None


def test_language_lock_and_interpreter(monkeypatch):
    from tools import languages

    stt = FakeSTT()
    monkeypatch.setattr(languages, "transcriber", stt)
    languages.set_conversation_language("Haitian Creole")
    assert stt.forced_language == "ht"
    languages.set_conversation_language("auto")
    assert stt.forced_language is None
    assert "Unknown" in languages.set_conversation_language("Klingon")

    languages.start_interpreter("English", "Spanish")
    assert languages.interpreter["a"] == "en" and languages.interpreter["b"] == "es"
    assert stt.extra_language == "es"
    languages.stop_interpreter()
    assert languages.interpreter is None and stt.extra_language is None


def test_tutor_spaced_repetition(monkeypatch):
    from datetime import date, timedelta

    from tools import tutor

    tutor.tutor_set_profile("Spanish", "beginner (A1)", "travel")
    assert "beginner" in tutor.tutor_status("Spanish")
    tutor.tutor_add_words("Spanish", [{"word": "hola", "translation": "hello", "example": "Hola, Juan."},
                                      {"word": "gracias", "translation": "thanks", "example": "Muchas gracias."}])
    assert "Nothing due" in tutor.tutor_review("Spanish", 5)  # new words come back tomorrow

    # Pretend two days passed.
    real_date = date

    class Later(date):
        @classmethod
        def today(cls):
            return real_date.today() + timedelta(days=2)

    monkeypatch.setattr(tutor, "date", Later)
    due = json.loads(tutor.tutor_review("Spanish", 5))
    assert {w["word"] for w in due} == {"hola", "gracias"}
    assert "next review" in tutor.tutor_grade("Spanish", "hola", True)
    assert "next review" in tutor.tutor_grade("Spanish", "gracias", False)
    # gracias went back to box 0 (due in 1 day), hola moved up (due in 3): nothing due now.
    assert "Nothing due" in tutor.tutor_review("Spanish", 5)


def test_streak():
    from tools import tutor

    assert "Streak: 1" in tutor.tutor_log_lesson("French", "greetings")
    assert "Streak: 1" in tutor.tutor_log_lesson("French", "numbers")  # same day doesn't double count


def test_lesson_page_renders(monkeypatch, tmp_data):
    import webbrowser

    from tools import tutor

    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: None)
    out = tutor.show_lesson_page("Photosynthesis", "Ages 12+", [
        {"heading": "The idea", "body": "Plants make food from light.\n\nThey release oxygen.",
         "terms": [{"term": "chlorophyll", "meaning": "green pigment", "example": "Leaves are green."}],
         "diagram": "graph LR; Light-->Leaf; Leaf-->Sugar"}],
        [{"question": "What gas do plants release?", "answer": "Oxygen"}])
    assert "Opened" in out
    page = next((tmp_data / "users" / "owner" / "lessons").glob("*.html")).read_text(encoding="utf-8")
    assert "mermaid" in page and "chlorophyll" in page and "<details" in page
