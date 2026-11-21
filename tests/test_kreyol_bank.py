"""The Creole phrase bank (core/kreyol_bank): retrieval into the language note, the reply check with one more try and
a plain fallback, and the owner's corrections kept and used. A fake transport stands in for the model."""
import pytest

from audio import language
from core import chatturn, independence, intelligence, kreyol_bank, warmth
from tools import registry

PERSONA = "PERSONA-CORE You are J.A.R.V.I.S., talking with them out loud."
MANUAL = "MANUAL-OPERATIONS " + "Follow the operating rules. " * 40


@pytest.fixture(autouse=True)
def fresh():
    language.reset()
    yield
    language.reset()


@pytest.fixture
def model(monkeypatch):
    """replies: what the model answers, in order (the last one repeats). Records every body sent and every audit."""
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setenv("JARVIS_LOCAL_FAST_PROVIDER", "ollama")
    monkeypatch.setenv("JARVIS_LOCAL_URL", "http://127.0.0.1:11434/v1")
    for var in ("JARVIS_LOCAL_FAST_MODEL", "JARVIS_CHAT_FIRST", "JARVIS_CHAT_THINK", "JARVIS_PC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(warmth._state, "game", False)
    monkeypatch.setattr(intelligence, "select_tools", lambda history, tools: [])
    state = {"replies": [], "sent": [], "audits": [], "streamed": []}
    monkeypatch.setattr(intelligence, "audit", lambda role, what, *a, **k: state["audits"].append(what))

    def complete_local(body, cancelled=lambda: False, on_text=None):
        state["sent"].append(body)
        text = state["replies"][min(len(state["sent"]) - 1, len(state["replies"]) - 1)]
        if on_text:
            on_text(text + " ")
        return {"model": "qwen3.5:9b", "choices": [{"finish_reason": "stop", "message": {"content": text}}]}
    monkeypatch.setattr(intelligence, "complete_local", complete_local)

    def ask(said, *replies, earlier=()):
        state["replies"], state["sent"], state["audits"], state["streamed"] = list(replies), [], [], []
        language.observe("k", said)
        out = intelligence.request("fast", MANUAL, list(earlier) + [{"role": "user", "content": said}], registry.schemas(),
                                   chat_system=PERSONA, on_text=state["streamed"].append,
                                   language=language.reply_note("k", said, "owner"))
        return " ".join(b.text for b in out.content)
    state["ask"] = ask
    return state


def _last(body) -> str:
    content = body["messages"][-1]["content"]
    return content if isinstance(content, str) else " ".join(p.get("text", "") for p in content)


# ---- the bank -----------------------------------------------------------------------------------------------------
def test_the_bank_has_a_few_hundred_sentences_and_each_passes_its_own_check():
    rows = kreyol_bank.builtin()
    assert len(rows) >= 300 and sum(bool(r["en"]) for r in rows) >= 250
    assert {r["source"] for r in rows} == {"bank", "course", "script"}
    for row in rows:
        assert not kreyol_bank.flagged(kreyol_bank.check(row["ht"])), row["ht"]
    assert "NEEDS THE OWNER'S CORRECTION" in kreyol_bank.__doc__


def test_the_sentences_nearest_his_line_are_picked():
    near = [r["ht"] for r in kreyol_bank.relevant("Mwen grangou, kisa mwen ka kwit ak diri epi poul?")]
    assert "Ou ka kwit diri ak poul." in near and len(near) == kreyol_bank.EXAMPLES
    assert "Syèl la ble paske limyè solèy la gaye nan lè a." in [r["ht"] for r in kreyol_bank.relevant("Poukisa syèl la ble?")]
    assert len(kreyol_bank.relevant("zzz")) >= 3                       # nothing matches: a few plain sentences


def test_the_creole_note_carries_the_examples_and_asks_for_short_simple_sentences():
    language.observe("k", "Mwen grangou, kisa mwen ka kwit aswè a?")
    note = language.reply_note("k", "Mwen grangou, kisa mwen ka kwit aswè a?")
    assert kreyol_bank.is_note(note) and language.KREYOL_GUIDE in note
    assert "one or two short, simple sentences" in note and "Mwen grangou." in note
    assert language.reply_note("english-only", "I am hungry") == ""    # an English turn is what it was
    assert not kreyol_bank.is_note("(Language: they are speaking French. Answer in French only.)")


# ---- the check ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reply, words", [
    ("Je suis fatigué aujourd'hui.", {"suis", "fatigué", "aujourd'hui"}),
    ("Souvan c'èst yon detay.", {"c'èst"}),
    ("Mwen bezwen la información ahora.", {"información", "ahora"}),
    ("Mwen ap attention pou ou.", {"attention"}),
    ("Li se yon question difisil.", {"question"}),
])
def test_french_spanish_and_english_forms_are_foreign(reply, words):
    found = kreyol_bank.check(reply)
    assert words <= set(found["foreign"]) and kreyol_bank.bad(found)


def test_invented_words_are_unknown_and_one_alone_is_let_through():
    found = kreyol_bank.check("Ou ka santi pwasonnè a lou, men chak ti pouti w mete rive.")
    assert {"pwasonnè", "pouti"} <= set(found["unknown"]) and not found["foreign"] and kreyol_bank.bad(found)
    one = kreyol_bank.check("Mwen renmen zagribay sa a.")
    assert one["unknown"] == ["zagribay"] and not kreyol_bank.bad(one)


@pytest.mark.parametrize("reply", ["Bonjou, mesye. Mwen byen, mèsi.", "M ap travay kounye a, men mwen ka ede ou.",
                                   "Kapital Ayiti se Pòtoprens.", "Mwen pa t dòmi byen, men m ap kenbe.",
                                   "Zanmi mwen Remy ap vini ak Dana demen.", "Li fè 3 èdtan."])
def test_good_creole_names_and_numbers_pass(reply):
    assert not kreyol_bank.flagged(kreyol_bank.check(reply))


def test_the_fallback_is_never_empty_and_fits_his_line():
    assert kreyol_bank.fallback("Mèsi anpil.", "Je vous en prie.") == "Pa gen pwoblèm."
    assert kreyol_bank.fallback("Bonswa Jarvis.", "") == "Bonswa. Mwen la, mwen byen."
    assert kreyol_bank.fallback("Poukisa lapli tonbe?", "La pluie tombe parce que.") in kreyol_bank.HONEST
    assert kreyol_bank.fallback("Mwen fatige.", "", "") == "Dakò, mwen tande ou."
    kept = kreyol_bank.fallback("Mwen fatige.", "Ou bezwen repoze. Le sommeil est très important.")
    assert kept == "Ou bezwen repoze."                                 # the clean sentence of the draft is kept
    for line in ("", "Wi.", "Kisa?", "Orevwa."):
        assert not kreyol_bank.flagged(kreyol_bank.check(kreyol_bank.fallback(line, "xx qq")))


# ---- the chat path ------------------------------------------------------------------------------------------------
def test_a_clean_creole_reply_is_said_as_written_with_one_call(model):
    assert model["ask"]("Bonjou Jarvis, kijan ou ye jodi a?", "Bonjou, mesye. Mwen byen, mèsi.") == "Bonjou, mesye. Mwen byen, mèsi."
    assert len(model["sent"]) == 1 and "kreyol_again" not in model["audits"]


def test_a_reply_with_words_that_are_not_creole_is_written_once_more_with_them_named(model):
    said = model["ask"]("Mwen fatige anpil, kisa mwen ta dwe fè?", "Vous devez dormir, c'est important.",
                        "Ou bezwen repoze. Al dòmi bonè aswè a.")
    assert said == "Ou bezwen repoze. Al dòmi bonè aswè a."
    assert len(model["sent"]) == 2 and "kreyol_again" in model["audits"] and "kreyol_fallback" not in model["audits"]
    assert "'vous'" in _last(model["sent"][1]) and "not good Haitian Creole" in _last(model["sent"][1])
    assert model["sent"][0]["messages"][0] == model["sent"][1]["messages"][0]      # the cached prefix is the same
    assert not any("vous" in piece.lower() for piece in model["streamed"])         # nothing bad was said meanwhile


def test_still_bad_after_one_more_try_falls_back_to_plain_creole_never_silence(model):
    said = model["ask"]("Poukisa lapli tonbe?", "La pluie tombe du ciel.", "C'est à cause des nuages.")
    assert said == kreyol_bank.fallback("Poukisa lapli tonbe?", "") and said.strip()
    assert len(model["sent"]) == 2 and "kreyol_fallback" in model["audits"]         # one more try, never a third
    assert not kreyol_bank.flagged(kreyol_bank.check(said))


def test_a_deep_question_in_creole_gets_the_short_chat_profile(model):
    model["ask"]("Poukisa syèl la ble? Esplike m sa an detay.", "Syèl la ble paske limyè solèy la gaye nan lè a.")
    assert model["sent"][0].get("profile") == chatturn.CHAT
    assert chatturn.DEPTH_NOTE not in _last(model["sent"][0])


def test_an_english_conversation_is_not_checked_and_streams_as_before(model):
    language.reset()
    model["replies"], model["sent"] = ["The sky is blue because of scattering, sir."], []
    out = intelligence.request("fast", MANUAL, [{"role": "user", "content": "Tell me something nice."}], registry.schemas(),
                               chat_system=PERSONA, on_text=model["streamed"].append, language="")
    assert " ".join(b.text for b in out.content).startswith("The sky is blue") and len(model["sent"]) == 1
    assert model["streamed"]


# ---- corrections --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("line, before, creole, want", [
    ("Yo pa di sa, yo di mwen grangou.", "Mwen gen grangou.", True, ("mwen grangou", "")),
    ("Nou pa di repose, nou di repoze.", "Ou bezwen repose.", True, ("repoze", "repose")),
    ("Pa di 'resip', di 'resèt'.", "Mwen ka chèche resip la.", True, ("resèt", "resip")),
    ("You should say mwen byen, not mwen bon.", "", False, ("mwen byen", "mwen bon")),
    ("An kreyòl yo di kijan ou ye", "", True, ("kijan ou ye", "")),
    ("In Creole we say 'pa gen pwoblèm'.", "", False, ("pa gen pwoblèm", "")),
])
def test_a_correction_is_recognised(line, before, creole, want):
    assert kreyol_bank.correction(line, creole=creole, before=before) == want


@pytest.mark.parametrize("line, before", [
    ("you should say sorry", ""), ("You should say that again.", ""), ("You should say no", ""),
    ("Ou dwe di m sa ou vle.", "Dakò."), ("Yo pa di anyen, yo di y ap vini demen.", "Mwen tande ou."),
    ("Yo pa di sa, yo di mwen grangou.", ""), ("Don't say anything, say hello to her for me", "Very well."),
    ("You should say it in French: je suis fatigué", ""), ("Kisa yo di?", "Dakò."), ("", ""),
])
def test_narration_and_english_are_not_corrections(line, before):
    assert kreyol_bank.correction(line, creole=True, before=before) is None
    assert kreyol_bank.correction(line, creole=False, before=before) is None


def test_a_kept_correction_is_used_from_then_on():
    assert kreyol_bank.count("owner") == 0
    assert "zagribay" in kreyol_bank.check("Mwen renmen zagribay.", "owner")["unknown"]
    kreyol_bank.learn("resèt", "resip", about="Mwen ka chèche resip la.", uid="owner", at=1.0)
    kreyol_bank.learn("zagribay dous", uid="owner", at=2.0)
    kreyol_bank.learn("Resèt", "resip", uid="owner", at=3.0)                       # the same phrase again: one row
    assert kreyol_bank.count("owner") == 2 and kreyol_bank.count("someone-else") == 0
    assert kreyol_bank.apply("Mwen ka chèche resip la pou ou.", "owner") == "Mwen ka chèche Resèt la pou ou."
    assert not kreyol_bank.check("Mwen renmen zagribay.", "owner")["unknown"]      # his word is Creole now
    first = kreyol_bank.relevant("Ou gen yon resèt pou diri?", "owner")[0]
    assert first["source"] == "owner" and first["ht"].lower() == "resèt"
    note = kreyol_bank.grounding("Ou gen yon resèt pou diri?", "owner")
    assert "they taught you this; never 'resip'" in note


def test_a_corrected_wording_is_replaced_in_the_reply_before_it_is_said(model):
    kreyol_bank.learn("resèt", "resip", uid="owner")
    assert model["ask"]("Kisa mwen ka kwit aswè a?", "Mwen ka ba ou yon bon resip.") == "Mwen ka ba ou yon bon resèt."
    assert len(model["sent"]) == 1


def test_corrections_are_counted_in_the_independence_report():
    assert independence.report(now=1.0)["language"]["creole_corrections"] == 0
    assert "Creole" not in independence.spoken(independence.report(now=1.0))
    kreyol_bank.learn("mwen grangou", uid="owner")
    data = independence.report(now=1.0)
    assert data["language"]["creole_corrections"] == 1
    assert "You corrected my Creole 1 time, and I keep every one" in independence.spoken(data)


# ---- review 2026-11-12 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reply, said", [
    ("Mwen ap travay sou kòd la. Python se yon lang.", ""),
    ("Windows se yon sistèm. Li mache byen.", ""),
    ("Wi, mwen konnen software sa a.", "Ou konnen software sa a?"),          # his own word, said back to him
    ("Excel bon pou sa.", "Kisa ki bon pou chif yo?"),
])
def test_a_name_that_opens_a_sentence_and_his_own_words_are_not_judged(reply, said):
    found = kreyol_bank.check(reply, said=said)
    assert not found["foreign"] and not kreyol_bank.bad(found), found


@pytest.mark.parametrize("reply", ["Vous devez dormir.", "Je suis fatigué.", "The answer is simple.", "Question sa a difisil."])
def test_a_foreign_word_that_opens_a_sentence_is_still_foreign(reply):
    assert kreyol_bank.bad(kreyol_bank.check(reply))


def test_a_creole_answer_about_software_is_said_as_written_with_one_call(model):
    reply = "Python se yon lang pou ekri pwogram. Li senp."
    assert model["ask"]("Kisa Python ye? Mwen vle konnen plis.", reply) == reply
    assert len(model["sent"]) == 1 and "kreyol_again" not in model["audits"]


def test_a_correction_that_fits_one_moment_does_not_rewrite_every_reply():
    """'Pa di bonjou, di bonswa' in the evening: bonjou is good Creole, so it is kept as a lesson and never swapped."""
    kreyol_bank.learn("bonswa", "bonjou", about="Bonjou, mesye.", uid="owner", at=1.0)
    assert kreyol_bank.apply("Bonjou, mesye. Kijan ou ye?", "owner") == "Bonjou, mesye. Kijan ou ye?"
    note = kreyol_bank.grounding("Bonswa Jarvis", "owner")
    assert "'bonswa' (they taught you this, where you had said 'bonjou')" in note and "never 'bonjou'" not in note
    kreyol_bank.learn("resèt", "resip", uid="owner", at=2.0)                # a word that is no Creole is still replaced
    assert kreyol_bank.apply("Bonjou. Men yon resip.", "owner") == "Bonjou. Men yon resèt."
