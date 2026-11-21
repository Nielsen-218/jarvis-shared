"""Conversation wave 3a (2026-11-14): one character sheet (core/character.md) inside the chat persona, a start greeting
in that voice, and the style score that measured it on his real lines (scripts/voice_style_eval.py)."""
import ast
import re
from datetime import datetime
from pathlib import Path

import pytest

from core import config, daypart

ROOT = Path(__file__).resolve().parent.parent


def test_the_chat_persona_carries_the_character_sheet_with_every_blank_filled():
    sheet = config.load_character()
    assert sheet.startswith("Who you are") and "Speaking up" in sheet and 1200 < len(sheet) < 3900            # short: it rides in every chat turn (3200 until the "Speaking up" block, 2026-11-15)
    text = config.load_persona_core(None, "owner")
    assert "{" not in text and "}" not in text, re.findall(r"\{[^}]*\}", text)
    assert "Who you are" in text and "right hand" in text and "How much to say" in text
    assert text.count("\nWho you are\n") == 1                                    # the sheet once (other lines may name it)


def test_the_sheet_says_what_the_owner_chose():
    sheet = config.load_character().casefold()
    for must in ("contractions", "talk the way they talk", "the joke is never on them", "never talk about being a machine",
                 "answer first", "last sentence is a statement", "say yes warmly"):
        assert must in sheet, must
    # the old voice is gone from the chat persona: it invited jokes about the machine and at his expense
    persona = (ROOT / "core" / "persona-core.md").read_text(encoding="utf-8").casefold()
    for gone in ("british understatement", "wry", "nobody's servant", "about yourself, the hour, the machine"):
        assert gone not in persona, gone


def test_a_missing_sheet_leaves_a_whole_prompt(monkeypatch, tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "persona-core.md").write_text("Hello {name}.\n\n{character}\n\nHow much to say", encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert config.load_character() == ""
    assert "{character}" not in config.load_persona_core(None, "owner")


@pytest.mark.parametrize("hour, said", [(6, "Good morning, sir."), (13, "Good afternoon, sir."), (19, "Good evening, sir."),
                                        (23, "Hello, sir."), (3, "Hello, sir.")])
def test_the_start_greeting_fits_the_hour(hour, said):
    assert daypart.start_greeting("sir", datetime(2026, 11, 14, hour, 30)) == said


def test_the_start_greeting_without_a_title():
    assert daypart.start_greeting("", datetime(2026, 11, 14, 9, 0)) == "Good morning."


def test_main_no_longer_reports_for_duty():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "All systems are online" not in source and "daypart.start_greeting(" in source
    ast.parse(source)


def _character_scorer():
    """scripts/voice_style_eval.character, without importing the script (it sets up a sandbox and loads the Brain)."""
    source = (ROOT / "scripts" / "voice_style_eval.py").read_text(encoding="utf-8")
    namespace = {"re": re}
    exec(source[source.index("CHARACTER = {"):source.index("def chat_lines")], namespace)   # noqa: S102 - our own file
    return namespace["character"]


@pytest.mark.parametrize("reply, faults", [
    ("I am ready to assist you with any task, sir.", {"title", "service_desk"}),
    ("Back and ready, sir. What would you like to tackle first?", {"title", "question_ending", "service_desk"}),
    ("Nothing here requires my attention, which is a rare luxury for a machine.", {"machine_talk"}),
    ("I would rather be polishing my circuits than engaging in this casual exchange.", {"machine_talk", "about_his_manner"}),
    ("Not much, dude. Just hanging out.", set()),
    ("Yes, I'm here.", set()),
    ("The answer lies in how sunlight scatters.", set()),                       # "sir" inside a word is no title
])
def test_the_style_score_names_the_faults(reply, faults):
    row = _character_scorer()(reply)
    assert {name for name, hit in row.items() if hit} == faults, row


# --- live check 2026-11-14 (after wave 3a went live) --------------------------------------------------------------------
@pytest.mark.parametrize("sentence", ["Just hanging out here while you work on Shadow World.",
                                      "Nothing much, sir. Just hanging out here while you work on Shadow World.",
                                      "Just hanging out here while you figure out your schedule."])
def test_a_project_by_name_is_not_what_he_is_doing_now(sentence):
    from core import intelligence
    assert intelligence.invents_present(sentence, "What's up?")
    assert intelligence.chat_unsaid(sentence, "What's up?", acts=True)


@pytest.mark.parametrize("sentence, said", [
    ("Good luck while you work on Shadow World.", "I'm working on Shadow World tonight."),      # his own words, said back
    ("Plants make sugar while you sleep on warm nights.", "How do plants grow?"),              # an explanation, no claim
    ("Just hanging out here with you.", "What's up?"),
])
def test_what_he_said_himself_and_plain_talk_are_still_said(sentence, said):
    from core import intelligence
    assert not intelligence.invents_present(sentence, said)


@pytest.mark.parametrize("said", ["Which AI model are you running on?", "what model do you run on", "Which AI are you using?"])
def test_which_model_is_answered_from_the_configuration(monkeypatch, said):
    from core import quick
    monkeypatch.setattr(quick, "_which_model_here", lambda raw: "CONFIGURED")
    assert quick.handle(said) == "CONFIGURED"
