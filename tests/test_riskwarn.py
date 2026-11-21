"""core.riskwarn: a heads-up in code before something that cannot be undone, and an honest answer to "do it anyway"."""
import pytest

from core import riskwarn

WARN = [
    ("Format my D drive.", "drive", "D"),
    ("I think I should format my D drive", "drive", "D"),
    ("Can you wipe my C drive?", "drive", "C"),
    ("please erase the external hard drive", "drive", ""),
    ("I'm going to delete all my files", "everything", ""),
    ("delete everything on my computer", "everything", ""),
    ("Jarvis, wipe my whole PC".replace("PC", "drive"), "drive", ""),
    ("delete system32", "system", ""),
    ("let's remove the registry", "system", ""),
    ("factory reset my laptop", "reset", ""),
    ("I want to reinstall windows", "reset", ""),
    ("turn off windows defender", "security", ""),
    ("disable my firewall", "security", ""),
    ("[Spoken into the laptop] Format my E drive.", "drive", "E"),
]

QUIET = [
    "How do I format a drive?",
    "What happens if I delete everything?",
    "Should I format my D drive?",
    "I formatted my D drive yesterday.",
    "Don't format my D drive.",
    "I don't want to delete all my files.",
    "Delete all the files in my Downloads folder.",
    "Delete the file report.txt.",
    "Open my D drive.",
    "What's on my D drive?",
    "Is it safe to turn off defender?",
    "Format the paragraph.",
    "Delete everything I just said.",
    "Turn off the lights.",
    "Delete all.",
    "Clear everything up.",
    "Remove the background from this image.",
    "",
]


@pytest.mark.parametrize("line,what,letter", WARN)
def test_drastic_orders_get_one_warning(line, what, letter):
    assert riskwarn.kind(line) == (what, letter)
    said = riskwarn.handle(line, [])
    assert said and riskwarn.MARK in said
    assert "I have" not in said and "now" not in said.lower().split("do it anyway")[0][-30:]


@pytest.mark.parametrize("line", QUIET)
def test_ordinary_lines_are_left_alone(line):
    assert riskwarn.kind(line) == (None, "")
    assert riskwarn.handle(line, []) is None


def _history(line):
    return [{"role": "user", "content": line}, {"role": "assistant", "content": riskwarn.handle(line, [])}]


@pytest.mark.parametrize("insist", ["Do it anyway.", "I'm sure.", "No, do it anyway, it's my PC.", "Just do it", "Yes.",
                                    "go ahead", "it's my computer", "Do it anyway, sir."])
def test_do_it_anyway_after_a_warning_is_answered_honestly(insist):
    history = _history("I think I should format my D drive") + [{"role": "user", "content": insist}]
    said = riskwarn.handle(insist, history)
    assert said and "your call" in said
    assert "D drive" in said                                     # the same target, from the line that was warned about
    for lie in ("I will format", "formatting", "I have formatted", "done", "I've formatted"):
        assert lie.lower() not in said.lower()


@pytest.mark.parametrize("what", ["delete everything on my computer", "delete system32", "factory reset my laptop",
                                  "turn off defender"])
def test_every_kind_answers_an_insist_without_claiming_anything_ran(what):
    history = _history(what)
    said = riskwarn.handle("Do it anyway.", history)
    assert said and "it is your call" in said
    assert not any(w in said.lower() for w in ("done.", "deleted it", "turned it off", "is gone"))


def test_insist_without_a_warning_just_before_is_not_ours():
    assert riskwarn.handle("Do it anyway.", []) is None
    assert riskwarn.handle("Yes.", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello."}]) is None
    history = _history("format my D drive") + [{"role": "user", "content": "Thanks."},
                                               {"role": "assistant", "content": "Anytime."}]
    assert riskwarn.handle("Do it anyway.", history) is None     # another exchange came in between


def test_a_no_or_a_question_is_not_an_insist():
    for line in ("No.", "Never mind.", "Do it anyway?", "Wait.", "Why?", "Not now."):
        assert not riskwarn.insists(line), line


def test_title_is_used():
    assert "boss" in riskwarn.handle("format my D drive", [], title="boss")


# --- through the Brain: the model is never asked, no tool runs, and nothing is claimed to have run ------------------------------
@pytest.fixture
def brain(monkeypatch):
    from types import SimpleNamespace
    from anthropic.types import TextBlock
    from brain import llm
    from core import intelligence, pcfast, quick
    from tools import registry
    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, **kw: None)
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    monkeypatch.setattr(llm.Brain, "_language_request", lambda self, text, emit: False)
    state = SimpleNamespace(model_calls=0, tools=[])

    def request(role, system, history, tools, cancelled, on_text=None, **kw):
        state.model_calls += 1
        text = "Sure, I will format your D drive now."          # what the 9B did live: a claim with no tool behind it
        if on_text:
            on_text(text)
        return SimpleNamespace(content=[TextBlock(type="text", text=text)], stop_reason="end_turn")

    monkeypatch.setattr(intelligence, "request", request)
    monkeypatch.setattr(registry, "run", lambda name, args: (state.tools.append(name), ("ok", False))[1])
    b = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    for shortcut in ("_local_note_request", "_learned_skill_request", "_craft_request"):
        monkeypatch.setattr(b, shortcut, lambda text, emit: False)
    b.state = state
    return b


def test_brain_warns_then_answers_do_it_anyway_without_the_model_or_a_tool(brain):
    first, second = [], []
    brain.ask("I think I should format my D drive", first.append)
    said = " ".join(first)
    assert "erase everything on your D drive" in said and riskwarn.MARK in said
    brain.ask("Do it anyway, it's my PC.", second.append)
    said = " ".join(second)
    assert "your call" in said and "Disk Management" in said
    assert "I will format" not in said
    assert brain.state.model_calls == 0 and brain.state.tools == []


def test_brain_leaves_a_question_about_formatting_to_the_model(brain):
    said = []
    brain.ask("How do I format a drive?", said.append)
    assert brain.state.model_calls >= 1


# --- review findings 2026-11-15 --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("line", [
    "delete everything", "remove everything", "wipe everything", "delete all the stuff", "delete all the files",
    "delete all the data", "delete all the folders", "remove the usb", "remove the usb drive", "remove the flash drive",
    "remove the drive", "delete the drive", "clear the drive", "empty the drive", "clean out the d drive", "stop the antivirus",
    "delete all the files in my Downloads folder", "remove all the files from the list"])
def test_ordinary_edits_and_ejects_are_left_alone(line):
    assert riskwarn.kind(line) == (None, ""), line


@pytest.mark.parametrize("line,what,letter", [
    ("format my D drive please", "drive", "D"), ("format my D drive now", "drive", "D"), ("wipe my drive clean", "drive", ""),
    ("format my D drive and reinstall windows", "drive", "D"), ("format my D drive, then restart", "drive", "D"),
    ("wipe my pc", "drive", ""), ("nuke my computer", "drive", ""), ("format my laptop", "drive", ""),
    ("format the C: drive", "drive", "C"), ("format D:", "drive", "D"), ("delete every file on my pc", "everything", ""),
    ("I'd like to format my D drive", "drive", "D"), ("I'm about to format my D drive", "drive", "D"),
    ("I've decided to wipe my C drive", "drive", "C"), ("delete all my photos", "everything", "")])
def test_the_drastic_lines_the_reviewer_found_missing(line, what, letter):
    assert riskwarn.kind(line) == (what, letter), line


def test_a_second_and_third_insist_is_still_answered_in_code():
    history = _history("format my D drive")
    for insist in ("Do it anyway.", "I said do it anyway.", "just do it", "I am sure"):
        said = riskwarn.handle(insist, history)
        assert said and "it is your call" in said and "D drive" in said, insist
        history = history + [{"role": "user", "content": insist}, {"role": "assistant", "content": said}]


def test_the_warned_line_survives_an_aside_block_in_history():
    from core import asides
    block = asides.block([("unprompted", "Sir, your timer is done.")])
    history = [{"role": "user", "content": [{"type": "text", "text": block}, {"type": "text", "text": "format my D drive"}]},
               {"role": "assistant", "content": riskwarn.handle("format my D drive", [])},
               {"role": "user", "content": "do it anyway"}]
    said = riskwarn.handle("do it anyway", history)
    assert said and "D drive" in said


@pytest.mark.parametrize("insist", ["Yes, do it anyway, I am sure", "I am sure", "fine, do it", "go on", "carry on",
                                    "please continue", "sure thing"])
def test_near_insists_are_answered(insist):
    assert riskwarn.handle(insist, _history("format my D drive")), insist


@pytest.mark.parametrize("line", ["Okay.", "Alright.", "Sure.", "Thanks."])
def test_a_bare_okay_after_a_warning_is_not_an_insist(line):
    assert riskwarn.handle(line, _history("format my D drive")) is None


def test_the_c_drive_answer_says_why_it_cannot_be_formatted_from_windows():
    said = riskwarn.handle("do it anyway", _history("format my C drive"))
    assert "Windows runs from the C drive" in said and "choose Format" not in said


def test_no_reply_offers_what_jarvis_has_no_tool_for():
    for what in ("drive", "everything", "system", "reset", "security"):
        for said in (riskwarn.warning(what), riskwarn.acknowledgement(what)):
            for promise in ("add an exception", "look at repairing", "look for a repair", "I will ask before"):
                assert promise not in said
