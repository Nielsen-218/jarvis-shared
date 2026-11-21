"""core.heard: recognition slips put right before routing. Synthetic lines; the app catalogue is faked."""
import pytest

from core import gateway, heard, pchybrid, pcsemantic

INSTALLED = ["calculator", "chatgpt", "claude", "blender", "notepad", "creative cloud", "steam"]


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: list(INSTALLED))     # the real matchers over a known catalogue
    monkeypatch.setattr(pcsemantic, "_is_game", lambda name: False)


@pytest.mark.parametrize("said,meant", [
    ("Closed calculator.", "Close calculator."),
    ("Opened Blender", "Open Blender"),
    ("Jarvis, minimized notepad.", "Jarvis, Minimize notepad."),
    ("[Spoken into the PC] Closed ChatGPT.", "[Spoken into the PC] Close ChatGPT."),
    ("closed the calculator app", "Close the calculator app"),
])
def test_a_bare_past_tense_command_naming_an_app_is_the_command(said, meant):
    assert heard.normalise(said) == meant
    assert heard.normalise(meant) == meant          # idempotent


@pytest.mark.parametrize("said", [
    "Closed the deal.", "Closed.", "I closed calculator.", "Closed calculator?", "He opened the door and left",
    "We opened Blender yesterday and it crashed", "Opened it.", "closed calculator because it was in the way of everything else",
])
def test_talk_in_the_past_tense_is_left_alone(said):
    assert heard.normalise(said) == said


@pytest.mark.parametrize("said,meant", [
    ("Jarrah, close calculator", "Jarvis, close calculator"),
    ("Hey Jervis open notepad", "Hey Jarvis open notepad"),
])
def test_his_name_at_the_start_is_put_right(said, meant):
    assert heard.normalise(said) == meant


@pytest.mark.parametrize("said", ["Travis said he would call", "I told Jervis about it", "My friend Jarrah is here"])
def test_a_name_that_is_not_an_address_is_left_alone(said):
    assert heard.normalise(said) == said


@pytest.mark.parametrize("said,meant", [
    ("Close chat TVT.", "Close ChatGPT."),
    ("open chat g b t", "open ChatGPT"),
    ("Minimize clawed.", "Minimize Claude."),
    ("close note pad", "close Notepad"),
    ("Closed chat gtp.", "Close ChatGPT."),               # both slips at once
])
def test_an_app_name_slip_after_a_command_verb_is_corrected(said, meant):
    assert heard.normalise(said) == meant


@pytest.mark.parametrize("said", [
    "close cloud",                      # the words as heard name an installed app (Creative Cloud): not rewritten to Claude
    "the cloud is down again", "I clawed my way back", "what is a blunder in chess", "did you see that chat g b t thing",
    "tell me about the note pad on my desk",
])
def test_app_slips_are_only_corrected_in_command_position(said):
    assert heard.normalise(said) == said


def test_non_text_and_long_text_pass_through():
    assert heard.normalise("") == "" and heard.normalise(None) is None
    long = "closed calculator " * 40
    assert heard.normalise(long) == long


def test_gateway_decides_and_executes_on_the_normalised_sentence(monkeypatch):
    """'Closed calculator.' reaches the exact lane as 'Close calculator.', and execution authorizes against the same words
    (core.authz.said_to_do needs the close word), so it neither becomes a 'short fragment' nor asks 'Should I close...?'."""
    seen = {}

    def fake_decide(text, history=None, cancelled=None, ask=None):
        seen["decide"] = text
        return None
    monkeypatch.setattr(gateway, "_decide", fake_decide)
    gateway.decide("Closed calculator.")
    assert seen["decide"] == "Close calculator."

    def fake_run(task, cancelled=None, *, plan=None, route=None, **k):
        seen["run"] = task
        return {"state": "completed", "detail": "Closed Calculator."}
    monkeypatch.setattr(pchybrid, "run", fake_run)
    close = gateway.Intent("actions", "close calculator", steps=[{"tool": "close_app", "arguments": {"app": "calculator"}}])
    assert gateway.execute(gateway.Decision([close], "exact"), "Closed calculator.") == "Closed Calculator."
    assert seen["run"] == "Close calculator."


@pytest.mark.parametrize("said,meant", [
    ("Minimize Cloud.", "Minimize Claude."),
    ("Minimize Clot.", "Minimize Claude."),
    ("Hey Jarvis. Minimize Quad.", "Hey Jarvis. Minimize Claude."),
    ("Open Cloud.", "Open Claude."),
    ("focus clot", "focus Claude"),
])
def test_his_claude_mishears_after_a_command_verb(monkeypatch, said, meant):
    """His real lines (2026-11-08..13): Whisper wrote Claude as Cloud, Clot or Quad and nothing (or the wrong app) happened."""
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: [n for n in INSTALLED if n != "creative cloud"])
    assert heard.normalise(said) == meant
    assert heard.normalise(meant) == meant


@pytest.mark.parametrize("said", ["the cloud is slow today", "Open the cloud storage settings", "minimize quad core usage"])
def test_cloud_and_quad_as_words_stay(monkeypatch, said):
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: [n for n in INSTALLED if n != "creative cloud"])
    assert heard.normalise(said) == said
