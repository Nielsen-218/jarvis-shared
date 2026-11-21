"""Review 2026-11-12 of the command-friction branch: narration must not become an action. These run the REAL matchers
(core.pcsemantic.mentioned_apps, core.authz scoring, core.pchelp.unwrap) over a catalogue with siblings, the way the
installed catalogue really looks; the earlier exact-match fake hid every case here. Nothing touches the real PC."""
import pytest

from core import authz, heard, pchelp, pchybrid, pcsemantic

CATALOGUE = ["calculator", "steam", "steamvr", "microsoft store", "discord", "google chrome", "chrome remote desktop",
             "microsoft edge", "microsoft edge webview2 runtime", "chatgpt", "claude", "notepad", "blender", "camera",
             "visual studio code", "visual studio 2022", "visual studio installer", "python 3.12", "python launcher",
             "git gui", "skype recording manager", "epic games launcher", "creative cloud", "file explorer"]


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: list(CATALOGUE))
    monkeypatch.setattr(pcsemantic, "_is_game", lambda name: False)


# ------------------------------------------------------------------------------------------------ 1. pchelp.next_plan
def _fuzzy_resolve(monkeypatch):
    """tools.apps.resolve_app is fuzzy on the real machine: these are the wrong apps the reviewer's probe got back."""
    from tools import apps
    wrong = {"the car": "camera", "car": "camera", "fresh": "file explorer", "over": "app recovery", "it up": "git gui",
             "recording": "skype recording manager", "notepad": "notepad", "the notepad": "notepad", "calculator": "calculator"}
    monkeypatch.setattr(apps, "resolve_app", lambda q: wrong.get(" ".join(str(q).casefold().split())))


@pytest.mark.parametrize("said", [
    "I need to start the car", "Let's start fresh", "I want to start over", "let's open it up", "can you start recording",
    "could you open the car for me",
])
def test_the_fallback_never_turns_talk_into_an_open(monkeypatch, said):
    _fuzzy_resolve(monkeypatch)
    assert pchelp.next_plan(said) is None


def test_the_fallback_still_opens_an_app_named_exactly(monkeypatch):
    _fuzzy_resolve(monkeypatch)
    task, plan = pchelp.next_plan("Could you open notepad for me, please?")
    assert task == "open notepad" and plan == {"steps": [{"tool": "open_app", "arguments": {"app": "notepad"}}]}


@pytest.mark.parametrize("said", ["I need to open notepad", "I want to open notepad", "let's open notepad"])
def test_first_person_wrappers_are_not_unwrapped(monkeypatch, said):
    _fuzzy_resolve(monkeypatch)
    assert pchelp.next_plan(said) is None


def test_the_fallback_fails_closed_without_a_catalogue(monkeypatch):
    _fuzzy_resolve(monkeypatch)
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: [])
    assert pchelp.next_plan("Could you open notepad for me, please?") is None


# ----------------------------------------------------------------------------------------------------- 2. heard._tense
@pytest.mark.parametrize("said", [
    "Closed calculator already.", "Closed Steam when the game crashed", "Closed the store.", "Opened Discord and it crashed",
    "Closed chrome and edge", "Closed chrome.", "Opened the camera roll", "Closed visual studio.",
])
def test_a_past_tense_sentence_that_merely_contains_an_app_is_talk(said):
    assert heard.normalise(said) == said


@pytest.mark.parametrize("said,meant", [
    ("Closed calculator.", "Close calculator."), ("Opened Discord", "Open Discord"), ("Closed the steam app.", "Close the steam app."),
    ("Jarvis, minimized my notepad window", "Jarvis, Minimize my notepad window"), ("Closed Microsoft Edge.", "Close Microsoft Edge."),
])
def test_a_past_tense_command_whose_object_is_exactly_one_app_is_the_command(said, meant):
    assert heard.normalise(said) == meant


def test_tense_fails_closed_without_a_catalogue(monkeypatch):
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: [])
    assert heard.normalise("Closed calculator.") == "Closed calculator."


# ----------------------------------------------------------------------------------------------------- 3. authz siblings
def asks(app, said, tool="close_app"):
    return authz.decide([{"tool": tool, "arguments": {"app": app}}], said, autonomous=True, owner=True).ask


@pytest.mark.parametrize("app,said", [
    ("visual studio 2022", "close visual studio code"), ("visual studio code", "close visual studio"),
    ("visual studio installer", "close visual studio"), ("chrome remote desktop", "close chrome"), ("steamvr", "close steam"),
    ("microsoft edge webview2 runtime", "close edge"), ("python 3.12", "close python"), ("python launcher", "close python"),
])
def test_a_sibling_app_is_not_named_by_words_another_app_fits_as_well_or_better(app, said):
    assert asks(app, said)
    assert asks(app, said.replace("close", "minimize"), tool="minimize_app")


@pytest.mark.parametrize("app,said", [
    ("google chrome", "close chrome"), ("steam", "close steam"), ("microsoft edge", "close edge"),
    ("visual studio code", "close visual studio code"), ("visual studio code", "close vs code"),
    ("epic games launcher", "close epic"), ("calculator", "close the calc"),
])
def test_the_best_fitting_app_still_runs_without_asking(app, said):
    assert not asks(app, said)


def test_short_names_fail_closed_without_a_catalogue(monkeypatch):
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: [])
    assert asks("microsoft edge", "close edge")                 # as before this branch: asked
    assert not asks("calculator", "close calculator")           # the full name never needed the short-name rule


# ------------------------------------------------------------------------------------------------- 4. heard._name
@pytest.mark.parametrize("said", ["Hey Travis, what time is it", "Travis, stop.", "Travis, close calculator", "Jarvis's turn",
                                  "Jarviss is a funny spelling"])
def test_a_real_first_name_and_a_possessive_are_left_alone(said):
    assert heard.normalise(said) == said


# ------------------------------------------------------------------------------------------------- 5. heard._APP_SLIPS
@pytest.mark.parametrize("said", [
    "close cloud", "open cloud", "close clod", "close claud", "close this chord", "open blunder", "Jarvis, open blunder",
    "show me chat g b t", "go to note pad", "start chat tvt", "show clawed",
])
def test_real_words_and_non_command_verbs_are_not_rewritten(said):
    assert heard.normalise(said) == said


@pytest.mark.parametrize("said,meant", [
    ("Close chat TVT.", "Close ChatGPT."), ("open chat g b t", "open ChatGPT"), ("Minimize clawed.", "Minimize Claude."),
    ("close note pad", "close Notepad"), ("Closed chat gtp.", "Close ChatGPT."),
])
def test_slips_after_open_close_minimize_become_an_installed_app(said, meant):
    assert heard.normalise(said) == meant


def test_a_slip_is_not_corrected_to_an_app_that_is_not_installed(monkeypatch):
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: ["calculator", "notepad"])
    assert heard.normalise("Close chat TVT.") == "Close chat TVT."
    assert heard.normalise("Minimize clawed.") == "Minimize clawed."


def test_slips_fail_closed_when_the_catalogue_lookup_raises(monkeypatch):
    def boom():
        raise OSError("catalogue offline")
    monkeypatch.setattr(pcsemantic, "_catalogue", boom)
    for said in ("Close chat TVT.", "close note pad", "Closed calculator."):
        assert heard.normalise(said) == said
