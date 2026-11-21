"""The Creole reply policy (core/kreyol_bank.settle): what the check accepts and rejects, the one guided retry, and the
honest last resort. Fakes stand in for the model; nothing here calls a model, a speaker or the clock."""
import re

import pytest

from core import kreyol_bank
from tests.kreyol_samples import BAD, GOOD

SAID = "Kisa m ta dwe manje aswè a?"


# ---- the detector: accept and reject tables ------------------------------------------------------------------------
@pytest.mark.parametrize("sentence", GOOD)
def test_good_creole_is_not_sent_for_a_retry(sentence):
    assert not kreyol_bank.bad(kreyol_bank.check(sentence)), kreyol_bank.check(sentence)


@pytest.mark.parametrize("sentence", BAD)
def test_french_english_spanish_and_french_in_creole_clothes_are_never_accepted(sentence):
    assert kreyol_bank.bad(kreyol_bank.check(sentence)), kreyol_bank.check(sentence)


@pytest.mark.parametrize("word", ["prezantasyon", "edikasyon", "enfòmasyon", "operasyon"])
def test_words_spelled_the_creole_way_by_their_ending_are_known(word):
    assert not kreyol_bank.flagged(kreyol_bank.check(f"Mwen vle yon {word}."))


@pytest.mark.parametrize("word", ["presentation", "éducation", "information", "attention"])
def test_the_french_ending_is_still_foreign(word):
    assert word in kreyol_bank.check(f"Mwen vle yon {word}.")["foreign"]


def test_two_unknown_words_pass_in_a_long_reply_but_not_in_a_short_one():
    long_reply = ("Ou ka fè yon bon plan pou semèn nan, epi ou ka kòmanse ak sa ki pi fasil. Zagribay ak pouti se pa "
                  "bagay ki bon pou w, men pran tan w.")
    assert len(kreyol_bank.check(long_reply)["unknown"]) == 2 and not kreyol_bank.bad(kreyol_bank.check(long_reply))
    assert kreyol_bank.bad(kreyol_bank.check("Zagribay ak pouti sa a."))
    assert kreyol_bank.bad(kreyol_bank.check(long_reply + " Pwasonnè a bon."))      # three: always a retry


def test_the_added_words_are_standard_creole_spelling_and_in_no_other_language_list():
    from audio import language
    other = language._SETS["fr"] | language._SETS["es"]
    for word in kreyol_bank.MORE_WORDS.split():
        assert not kreyol_bank._NOT_CREOLE.search(word), word
        assert word not in other or word in language._SETS["ht"], word


def test_every_example_exchange_and_every_last_resort_line_is_clean_creole():
    for question, answer in kreyol_bank.EXCHANGES:
        assert not kreyol_bank.flagged(kreyol_bank.check(question + " " + answer)), (question, answer)
    for line in kreyol_bank.HONEST:
        assert not kreyol_bank.flagged(kreyol_bank.check(line)) and len(line.split()) <= 20


# ---- the retry and the last resort ---------------------------------------------------------------------------------
class Model:
    """A stand-in for the one more try: returns what it is told, records the notes it was given."""

    def __init__(self, *replies):
        self.replies, self.notes, self.audits = list(replies), [], []

    def __call__(self, note):
        self.notes.append(note)
        return self.replies.pop(0) if self.replies else ""

    def audit(self, what):
        self.audits.append(what)


def test_a_clean_draft_is_said_as_written_and_the_model_is_not_asked_again():
    model = Model("never used")
    assert kreyol_bank.settle("Ou ka manje diri ak pwa.", SAID, model, audit=model.audit) == "Ou ka manje diri ak pwa."
    assert model.notes == [] and model.audits == []


def test_a_french_draft_is_retried_once_with_the_creole_word_and_the_question_in_the_note():
    model = Model("Manje diri ak pwa, se bon.")
    out = kreyol_bank.settle("Vous devez manger du riz, c'est très bon.", SAID, model, audit=model.audit)
    assert out == "Manje diri ak pwa, se bon."
    assert len(model.notes) == 1 and model.audits == ["kreyol_again"]
    note = model.notes[0]
    assert "'vous' (say 'ou')" in note and "'très' (say 'anpil')" in note and SAID in note
    assert "not good Haitian Creole" in note and "one or two short sentences" in note


def test_a_retry_that_is_still_french_is_never_said_as_creole_and_ends_honest():
    model = Model("Vous pouvez manger du riz avec des haricots.")
    out = kreyol_bank.settle("Je ne sais pas, vous devriez manger quelque chose.", SAID, model, audit=model.audit)
    assert out in kreyol_bank.HONEST
    assert len(model.notes) == 1 and model.audits == ["kreyol_again", "kreyol_fallback", "kreyol_honest"]
    assert not kreyol_bank.flagged(kreyol_bank.check(out))


def test_a_failed_model_call_ends_honest_after_one_try():
    model = Model()                                           # the stand-in answers '' like a LocalFailure that was caught
    assert kreyol_bank.settle("C'est une bonne question.", SAID, model, audit=model.audit) in kreyol_bank.HONEST
    assert len(model.notes) == 1


def test_the_clean_sentences_of_a_bad_draft_are_kept_when_the_retry_fails():
    model = Model("Je ne sais pas.")
    out = kreyol_bank.settle("Ou ka manje yon ti soup cho. Il fait froid aujourd'hui.", SAID, model, audit=model.audit)
    assert out == "Ou ka manje yon ti soup cho."
    assert "kreyol_honest" not in model.audits


def test_a_sentence_that_only_says_it_heard_the_question_is_not_an_answer():
    assert kreyol_bank.salvage("Mwen tande w mande sou manje a. Vous pouvez manger du riz.") == ""
    model = Model("Voilà ce que je pense.")
    out = kreyol_bank.settle("Mwen tande w mande sou manje a. Vous pouvez manger du riz.", SAID, model)
    assert out in kreyol_bank.HONEST


def test_the_last_resort_for_a_greeting_or_thanks_is_a_plain_sentence_not_the_honest_line():
    assert kreyol_bank.settle("Merci beaucoup, madame.", "Mèsi anpil.", Model("Je vous en prie.")) == "Pa gen pwoblèm."
    assert kreyol_bank.settle("Bonne nuit.", "Bonswa Jarvis.", Model("")) == "Bonswa. Mwen la, mwen byen."
    assert kreyol_bank.fallback("Mwen fatige.", "", "") == "Dakò, mwen tande ou."


@pytest.mark.parametrize("draft", BAD)
def test_no_french_english_or_spanish_draft_ever_comes_out_as_the_reply(draft):
    for retry in (draft, ""):
        out = kreyol_bank.settle(draft, SAID, Model(retry))
        assert out != draft and not kreyol_bank.bad(kreyol_bank.check(out)), out
        assert not re.search(r"\b(?:je|vous|est|the|you|con|que)\b", out, re.I)


def test_the_two_honest_lines_alternate_by_the_line_and_are_stable():
    assert kreyol_bank.honest("a") != kreyol_bank.honest("b")
    assert kreyol_bank.honest("Poukisa lapli tonbe?") == kreyol_bank.honest("Poukisa lapli tonbe?")


def test_the_language_note_carries_exchanges_and_the_direct_answer_rule():
    note = kreyol_bank.grounding("Kijan lapli fòme?")
    assert "Q: Kijan lapli fòme? A: Solèy la chofe dlo a" in note and "to the point" in note
    assert note.count("Q: ") == 3 and len(note) < 2600                  # compact: it rides on every Creole request


# ---- sampling ------------------------------------------------------------------------------------------------------
def test_a_creole_turn_is_sampled_with_a_lighter_presence_penalty_and_the_rest_is_unchanged():
    from audio import language
    from core import intelligence
    language.reset()
    language.observe("k", SAID)
    note = language.reply_note("k", SAID, "owner")
    language.reset()
    assert kreyol_bank.is_note(note)
    creole = [{"role": "user", "content": [{"type": "text", "text": SAID}, {"type": "text", "text": note}]}]
    plain = [{"role": "user", "content": "How are you today?"}]
    for profile in intelligence.PROFILES:
        if profile == "think":
            continue
        got = intelligence.profile_sampling(profile, creole)
        assert got["presence_penalty"] == intelligence.CREOLE_PRESENCE < intelligence.PROFILES[profile]["presence_penalty"]
        assert {k: v for k, v in got.items() if k != "presence_penalty"} == \
            {k: v for k, v in intelligence.PROFILES[profile].items() if k != "presence_penalty"}
        assert intelligence.profile_sampling(profile, plain) == intelligence.PROFILES[profile]
    assert intelligence.profile_sampling("chat", [{"role": "user", "content": note + " text form"}])["presence_penalty"] \
        == intelligence.CREOLE_PRESENCE
    assert intelligence.profile_sampling("chat", [])["presence_penalty"] == intelligence.PROFILES["chat"]["presence_penalty"]
