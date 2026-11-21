"""Polish of the explanation ladder after it went live: no stage direction aloud, the caveat only for a comparison that was
said (and with full stops), "Pre-K" spelt right, every rung names its topic, and stepping UP says again what he heard."""
import json
from datetime import datetime

import pytest

from core import study_check as check
from core import study_engine as engine
from core import study_ladder as ladder
from tools import registry, study
from tests.test_study_ladder_live import CANNED, Canned, quiet, run  # noqa: F401  (quiet is the autouse fixture)


@pytest.mark.parametrize("rung, text, want", [
    ("one", "RAM is a toy box! (point)", "RAM is a toy box!"),
    ("one", "House number! (point to the door)", "House number!"),
    ("prek", "RAM is a box (clap twice) for toys.", "RAM is a box for toys."),
    ("elementary", "Leo has RAM *smiles* in his box. [waves]", "Leo has RAM in his box."),
    ("college", "RAM holds data (point to the diagram) for programs.", "RAM holds data for programs."),
    ("college", "Random access memory (RAM) is volatile.", "Random access memory (RAM) is volatile."),
])
def test_no_stage_direction_survives_on_any_rung(rung, text, want):
    assert check.spoken(rung, text) == want
    assert "(" not in check.finalize("one", "Toy box! (point)") and check.finalize("one", "Toy box! (point)") == "Toy box!"


def test_the_spoken_reply_has_no_parenthesis_but_the_citation(monkeypatch):
    said = study._say_explained({"text": "RAM is a toy box! (point)", "level": "one", "source": "model", "limit": "", "check": "",
                                 "cite": "Concept outline, Hardware, RAM", "asked": "one"})
    assert said == "One-year-old level. RAM is a toy box!"
    said = study._say_explained({"text": "RAM is working memory.", "level": "college", "source": "model", "limit": "", "check": "",
                                 "cite": "Concept outline, Hardware, RAM", "asked": ""})
    assert said.endswith("(Concept outline, Hardware, RAM)")


BASE = {"text": "RAM is a desk where you keep the books you read now", "level": "middle", "source": "model", "check": "What is lost?",
        "cite": "c", "asked": "", "topic": "RAM", "limit": "a desk is only so big"}


def test_the_caveat_is_said_only_for_a_comparison_that_is_in_the_spoken_text():
    said = study._say_explained(dict(BASE, analogy="a desk"))
    assert "Where the comparison stops working: a desk is only so big. Check yourself: What is lost?" in said
    assert "read now. Where" in said                                             # the sentence before it got its full stop
    for analogy, text in (("", "RAM keeps programs while they run."), ("a pizza", "Subnetting cuts a network into parts.")):
        assert "stops working" not in study._say_explained(dict(BASE, analogy=analogy, text=text))


def test_pre_k_is_spelt_with_a_capital_k_in_speech_and_on_the_board():
    out = {"text": "RAM is a box.", "level": "prek", "source": "model", "limit": "", "check": "", "cite": "c", "asked": "prek"}
    assert study._say_explained(out).startswith("Pre-K level. ")
    assert ladder.LABELS["prek"] == "Pre-K"


def test_every_hand_written_rung_names_its_topic():
    for term in ladder.HAND:
        for rung in ("prek", "one"):
            passage = {"id": "x", "concept": term, "chapter": "Networking", "text": "x " + term, "material": "m"}
            got = engine.explain(term, [passage], rung, None)
            assert check.names(got["text"], term) and "(" not in got["text"], (term, rung, got["text"])


class Scripted:
    def __init__(self, *texts):
        self.texts, self.prompts = list(texts), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        text = self.texts.pop(0) if self.texts else self.prompts and "x"
        return json.dumps({"explanation": text, "analogy": "a toy box", "limit": "", "check": ""})


def test_a_rung_that_never_names_its_topic_is_asked_again_once_and_then_the_draft_that_names_it_is_kept():
    passage = {"id": "g", "chapter": "", "section": "", "page": None, "material": "General knowledge", "text": "", "general": True}
    story = "Leo keeps his toys in a box like a toy box. When it is full he puts old toys in the closet."
    model = Scripted(story, "RAM is a toy box. Leo keeps the toys he plays with in it.")
    got = engine.explain("RAM", [passage], "prek", model)
    assert len(model.prompts) >= 2 and "Name RAM" in model.prompts[1] and got["text"].startswith("RAM is a toy box")
    assert got["refused"][0] == "no_topic"
    model = Scripted(story, story)
    assert engine.explain("RAM", [passage], "prek", model)["text"] == ""         # nothing that names it: the caller says so


def test_a_cut_that_loses_the_only_sentence_naming_the_topic_keeps_that_sentence():
    text = "Leo has a box. He puts toys in it. He plays all day. Then he naps. RAM is the same kind of box. The end."
    assert check.names(check.lead_with_topic("prek", text, "RAM"), "RAM")


def test_stepping_up_says_again_what_he_already_heard_in_this_lesson(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    run("study_explain", topic="photosynthesis")
    for _ in range(5):
        run("study_explain_again", level="simpler")
    heard = study._explained[study._k()]["rungs"]["prek"]["text"]
    run("study_explain_again", level="simpler")                                   # down to the one-year-old
    calls = len(study.MODEL.prompts)
    said = run("study_explain_again", level="deeper")
    assert len(study.MODEL.prompts) == calls                                       # no new draft, no template
    assert said.startswith("Pre-K level. ") and heard in said and study._explained[study._k()]["level"] == "prek"


# ---- review fixes: definitions and acronyms survive, markdown/indices/ellipses survive, other word forms count -----------

@pytest.mark.parametrize("text", [
    "OSI (Open Systems Interconnection) has seven layers.",
    "A page (open source code) is used.",
    "Use a mask (a /26 mask) for the subnet.",
    "The CPU (central processing unit) runs code.",
    "A word (use case) here.",
    "A file (close to the root) here.",
])
@pytest.mark.parametrize("rung", ["college", "high", "middle"])
def test_a_definition_in_brackets_on_an_upper_rung_is_not_a_stage_direction(rung, text):
    assert check.spoken(rung, text) == text


@pytest.mark.parametrize("text, want", [
    ("Stand up (point to the door) now.", "Stand up now."),
    ("Say hi (wave). Then go.", "Say hi. Then go."),
    ("Say hi (clap twice) and go.", "Say hi and go."),
    ("Look here (show your hands) please.", "Look here please."),
    ("Wait (pause). Then go.", "Wait. Then go."),
])
def test_a_real_gesture_is_still_taken_out_on_an_upper_rung(text, want):
    assert check.spoken("college", text) == want


def test_markdown_bold_arithmetic_indices_and_ellipses_survive_on_the_upper_rungs():
    assert check.spoken("college", "The **RAM** holds data. Compute 2 * 3 * 4 now.") == "The RAM holds data. Compute 2 * 3 * 4 now."
    assert check.spoken("college", "See [1] and a[i] and wait... ok") == "See [1] and a[i] and wait... ok"
    assert check.spoken("high", "Say it *smiles* now [waves].") == "Say it now."
    assert check.spoken("prek", "The **RAM** is a box [1].") == "The RAM is a box."


def test_the_topic_counts_as_named_in_another_word_form_or_as_an_expansion_of_its_acronym():
    assert check.names("Subnets are small networks inside a bigger one.", "subnetting")
    assert check.names("The central processing unit runs code.", "CPU")
    assert check.names("Random access memory holds what runs now.", "RAM")
    assert not check.names("Leo keeps toys in a box.", "RAM")
    assert not check.names("The processor runs code", "CPU")                  # a bare synonym is not a name; see the keep rule below


def test_a_good_related_answer_that_never_uses_the_exact_name_is_kept_after_one_retry_not_thrown_away():
    source = "ATP is the energy molecule. The Krebs cycle makes ATP from acetyl groups in the mitochondria matrix."
    passage = {"id": "k", "concept": "Krebs cycle", "chapter": "Cells", "section": "", "page": None, "material": "m", "text": source}
    good = ("It makes ATP from acetyl groups in the mitochondria matrix. Each turn releases carbon dioxide and energy carriers. "
            "Those carriers feed the energy molecule production later, step by step through the cell.")
    model = Scripted(good, good)
    got = engine.explain("Krebs cycle", [passage], "college", model)
    assert got["source"] == "model" and got["text"].startswith("It makes ATP")


def test_a_held_rung_from_another_course_is_not_replayed(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    run("study_explain", topic="photosynthesis")
    run("study_explain_again", level="simpler")                                   # college is held, he is on high or middle
    last = study._explained[study._k()]
    assert last["rungs"]
    other = [dict(p, course="course-b", chapter="Other chapter", text="photosynthesis told differently in another course")
             for p in last["passages"]]
    study._explained[study._k()] = dict(last, passages=other)
    calls = len(study.MODEL.prompts)
    run("study_explain_again", level="deeper")
    assert len(study.MODEL.prompts) > calls                                       # a new draft, not course A's text replayed


def test_a_held_rung_of_the_same_material_is_still_replayed(monkeypatch):
    monkeypatch.setattr(study, "MODEL", Canned())
    run("study_explain", topic="photosynthesis")
    run("study_explain_again", level="simpler")
    calls = len(study.MODEL.prompts)
    run("study_explain_again", level="deeper")
    assert len(study.MODEL.prompts) == calls
